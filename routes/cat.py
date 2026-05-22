"""CAT (Computer Aided Transceiver) control routes.

Drives a single hardware transceiver per server over serial. The driver
is owned globally (on ``app_module``) so SSE clients and REST callers
share one connection.

PR #1 scope (driver only): rig selection, serial connect/disconnect,
VFO / mode / split / RIT / PTT control, raw CAT passthrough, status
SSE, and the Supervisor (TX lock + band guard + power cap). Panadapter
and front-panel views ship in later PRs.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.cat import (
    RIG_REGISTRY,
    Supervisor,
    get_descriptor,
    list_descriptors,
    list_serial_ports,
    load_supervisor,
    save_supervisor,
)
from utils.cat.base import RigDriver, RigState
from utils.responses import api_error
from utils.sse import sse_stream_fanout

logger = logging.getLogger('intercept.cat')

cat_bp = Blueprint('cat', __name__)


# --- Module state ------------------------------------------------------------
# The CAT driver is held on ``app_module`` (see ``app.py`` near the other
# mode queues) so other modes / shutdown handlers can introspect it. We
# also keep a per-process supervisor singleton here.

_supervisor: Supervisor = load_supervisor()
_supervisor_lock = threading.Lock()
_selected_rig_id: str = 'kenwood_ts850'

# Minimum gap between consecutive connect/disconnect operations. USB-serial
# adapters and the radio's CAT engine need a moment to settle.  Stamped
# on every connect *and* disconnect so a quick disconnect→reconnect
# sequence still honours the cooldown.  Surfaced to the UI via the
# ``cooldown_ms`` field returned by /cat/disconnect so the Connect
# button can grey out for the duration.
_RECONNECT_SETTLE_S = 1.2
_last_reconnect_ts = 0.0
_reconnect_lock = threading.Lock()


# --- Helpers -----------------------------------------------------------------
def _driver() -> RigDriver | None:
    return getattr(app_module, 'cat_driver', None)


def _publish(event: dict[str, Any]) -> None:
    """Push an event onto the CAT SSE queue. Drops on full queue."""
    q = getattr(app_module, 'cat_queue', None)
    if q is None:
        return
    try:
        q.put_nowait(event)
    except Exception:
        pass


def _on_status(state: RigState) -> None:
    _publish({'type': 'state', 'state': state.to_dict()})


def _on_io(direction: str, payload: str) -> None:
    _publish({
        'type': 'io',
        'direction': direction,
        'payload': payload,
        'ts': time.time(),
    })


def _validate_hz(value: Any, *, max_hz: int = 1_300_000_000) -> int:
    try:
        hz = int(value)
    except (TypeError, ValueError):
        raise ValueError('frequency must be an integer (Hz)')
    if hz < 0 or hz > max_hz:
        raise ValueError(f'frequency out of range: {hz}')
    return hz


def _validate_baud(rig_id: str, value: Any) -> int:
    desc = get_descriptor(rig_id)
    if desc is None:
        raise ValueError(f'unknown rig {rig_id!r}')
    try:
        baud = int(value) if value is not None else desc.default_baud
    except (TypeError, ValueError):
        raise ValueError('baud must be an integer')
    if baud not in desc.supported_bauds:
        raise ValueError(
            f'baud {baud} not in supported {list(desc.supported_bauds)}'
        )
    return baud


_VALID_DATA_BITS = (5, 6, 7, 8)
_VALID_STOP_BITS = (1, 2)  # 1.5 deliberately omitted — almost no rig uses it
_VALID_PARITY = ('N', 'E', 'O', 'M', 'S')


def _validate_framing(
    data_bits: Any, stop_bits: Any, parity: Any, desc: Any,
) -> tuple[int, int, str]:
    """Coerce + validate serial framing, falling back to rig defaults."""
    db_default = getattr(desc, 'data_bits', 8)
    sb_default = getattr(desc, 'stop_bits', 1)
    py_default = getattr(desc, 'parity', 'N')
    try:
        db = int(data_bits) if data_bits is not None else int(db_default)
        sb = int(stop_bits) if stop_bits is not None else int(sb_default)
    except (TypeError, ValueError):
        raise ValueError('data_bits/stop_bits must be integers')
    py = str(parity or py_default).upper()
    if db not in _VALID_DATA_BITS:
        raise ValueError(f'data_bits must be one of {list(_VALID_DATA_BITS)}')
    if sb not in _VALID_STOP_BITS:
        raise ValueError(f'stop_bits must be one of {list(_VALID_STOP_BITS)}')
    if py not in _VALID_PARITY:
        raise ValueError(f'parity must be one of {list(_VALID_PARITY)}')
    return db, sb, py


def _require_driver() -> RigDriver | None:
    drv = _driver()
    if drv is None or not drv.is_running():
        return None
    return drv


def _supervisor_snapshot() -> dict[str, Any]:
    with _supervisor_lock:
        return _supervisor.to_dict()


# --- Discovery ---------------------------------------------------------------
@cat_bp.route('/cat/rigs', methods=['GET'])
def cat_rigs():
    return jsonify({
        'rigs': [d.to_dict() for d in list_descriptors()],
        'selected': _selected_rig_id,
    })


@cat_bp.route('/cat/ports', methods=['GET'])
def cat_ports():
    return jsonify({'ports': list_serial_ports()})


@cat_bp.route('/cat/select', methods=['POST'])
def cat_select():
    global _selected_rig_id
    payload = request.get_json(silent=True) or {}
    rig_id = str(payload.get('rig_id') or '').strip()
    if rig_id not in RIG_REGISTRY:
        return api_error(f'unknown rig {rig_id!r}', 400, 'invalid_rig')
    desc = RIG_REGISTRY[rig_id]
    if not desc.implemented:
        return api_error(
            f'{desc.display_name} driver is not implemented yet',
            400,
            'driver_unavailable',
        )
    _selected_rig_id = rig_id
    return jsonify({'selected': rig_id})


# --- Lifecycle ---------------------------------------------------------------
@cat_bp.route('/cat/connect', methods=['POST'])
def cat_connect():
    global _last_reconnect_ts
    payload = request.get_json(silent=True) or {}
    rig_id = str(payload.get('rig_id') or _selected_rig_id).strip()
    desc = get_descriptor(rig_id)
    if desc is None:
        return api_error(f'unknown rig {rig_id!r}', 400, 'invalid_rig')
    if not desc.implemented:
        return api_error(
            f'{desc.display_name} driver is not implemented yet',
            400,
            'driver_unavailable',
        )
    port = str(payload.get('port') or '').strip()
    if not port:
        return api_error('serial port required', 400, 'missing_port')
    try:
        baud = _validate_baud(rig_id, payload.get('baud'))
    except ValueError as exc:
        return api_error(str(exc), 400, 'invalid_baud')
    assert_rts = bool(payload.get('assert_rts', False))
    assert_dtr = bool(payload.get('assert_dtr', False))
    try:
        data_bits, stop_bits, parity = _validate_framing(
            payload.get('data_bits'),
            payload.get('stop_bits'),
            payload.get('parity'),
            desc,
        )
    except ValueError as exc:
        return api_error(str(exc), 400, 'invalid_framing')

    with _reconnect_lock:
        # Drop any existing driver first.
        existing = _driver()
        if existing is not None:
            try:
                existing.stop()
            except Exception as exc:
                logger.debug('Prior driver stop raised: %s', exc)
            app_module.cat_driver = None

        # Honour the settle window.
        elapsed = time.time() - _last_reconnect_ts
        if elapsed < _RECONNECT_SETTLE_S:
            time.sleep(_RECONNECT_SETTLE_S - elapsed)

        try:
            driver = desc.driver_class(  # type: ignore[misc]
                port=port,
                baud=baud,
                on_status=_on_status,
                on_io=_on_io,
                assert_rts=assert_rts,
                assert_dtr=assert_dtr,
                data_bits=data_bits,
                stop_bits=stop_bits,
                parity=parity,
            )
            driver.start()
        except Exception as exc:
            logger.warning('CAT connect failed: %s', exc)
            _last_reconnect_ts = time.time()
            return api_error(f'connect failed: {exc}', 502, 'connect_failed')

        app_module.cat_driver = driver
        _last_reconnect_ts = time.time()

    _publish({'type': 'lifecycle', 'event': 'connected', 'rig_id': rig_id,
              'port': port, 'baud': baud})
    return jsonify({'connected': True, 'rig_id': rig_id, 'port': port,
                    'baud': baud, 'data_bits': data_bits,
                    'stop_bits': stop_bits, 'parity': parity})


@cat_bp.route('/cat/disconnect', methods=['POST'])
def cat_disconnect():
    global _last_reconnect_ts
    with _reconnect_lock:
        driver = _driver()
        if driver is None:
            return jsonify({'connected': False, 'cooldown_ms': 0})
        try:
            driver.stop()
        except Exception as exc:
            logger.debug('Driver stop raised: %s', exc)
        app_module.cat_driver = None
        # Start the reconnect cooldown clock from the moment the port
        # was actually closed.  The Connect button greys itself out for
        # this long client-side.
        _last_reconnect_ts = time.time()
    _publish({'type': 'lifecycle', 'event': 'disconnected'})
    return jsonify({
        'connected': False,
        'cooldown_ms': int(_RECONNECT_SETTLE_S * 1000),
    })


@cat_bp.route('/cat/status', methods=['GET'])
def cat_status():
    driver = _driver()
    if driver is None:
        return jsonify({
            'connected': False,
            'rig_id': _selected_rig_id,
            'supervisor': _supervisor_snapshot(),
        })
    state = driver.state()
    return jsonify({
        'connected': bool(state.connected),
        'rig_id': state.rig_id or _selected_rig_id,
        'state': state.to_dict(),
        'supervisor': _supervisor_snapshot(),
        'polling_enabled': bool(getattr(driver, 'polling_enabled', True)),
    })


@cat_bp.route('/cat/polling', methods=['GET', 'POST'])
def cat_polling():
    """Toggle the driver's safety-net poll.

    With Kenwood Auto-Info enabled, polling is mostly redundant — the
    rig pushes IF; on every dial / control change. Users who want a
    truly silent CAT bus (e.g. while debugging a flaky cable) can turn
    polling off and rely on AI pushes plus on-demand /cat/refresh.
    """
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get('enabled', True))
        try:
            driver.set_polling(enabled)
        except Exception as exc:
            return api_error(str(exc), 400, 'driver_error')
        _publish({'type': 'lifecycle', 'event': 'polling',
                  'enabled': enabled})
    return jsonify({'enabled': bool(getattr(driver, 'polling_enabled', True))})


@cat_bp.route('/cat/refresh', methods=['POST'])
def cat_refresh():
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    driver.request_status()
    return jsonify({'ok': True})


# --- Control: VFO/mode/split/RIT --------------------------------------------
@cat_bp.route('/cat/vfo', methods=['POST'])
def cat_vfo():
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    payload = request.get_json(silent=True) or {}
    which = str(payload.get('which') or 'A').upper()
    if which not in ('A', 'B'):
        return api_error('which must be A or B', 400, 'invalid_vfo')

    if 'select' in payload and payload['select']:
        try:
            driver.select_vfo(which)
        except Exception as exc:
            return api_error(str(exc), 400, 'driver_error')
        return jsonify({'ok': True, 'selected': which})

    if 'hz' not in payload:
        return api_error('hz required', 400, 'missing_hz')
    try:
        hz = _validate_hz(payload['hz'])
    except ValueError as exc:
        return api_error(str(exc), 400, 'invalid_hz')

    with _supervisor_lock:
        if not _supervisor.freq_allowed(hz):
            return api_error(
                'frequency blocked by band guard',
                403,
                'band_guard',
            )

    try:
        driver.set_vfo(which, hz)
    except Exception as exc:
        return api_error(str(exc), 400, 'driver_error')
    return jsonify({'ok': True, 'which': which, 'hz': hz})


@cat_bp.route('/cat/mode', methods=['POST'])
def cat_mode():
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get('mode') or '').strip()
    if not mode:
        return api_error('mode required', 400, 'missing_mode')
    try:
        driver.set_mode(mode)
    except Exception as exc:
        return api_error(str(exc), 400, 'driver_error')
    return jsonify({'ok': True, 'mode': mode.upper()})


@cat_bp.route('/cat/split', methods=['POST'])
def cat_split():
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    payload = request.get_json(silent=True) or {}
    on = bool(payload.get('on', False))
    try:
        driver.set_split(on)
    except Exception as exc:
        return api_error(str(exc), 400, 'driver_error')
    return jsonify({'ok': True, 'split': on})


@cat_bp.route('/cat/rit', methods=['POST'])
def cat_rit():
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    payload = request.get_json(silent=True) or {}
    on = bool(payload.get('on', False))
    try:
        driver.set_rit(on)
    except Exception as exc:
        return api_error(str(exc), 400, 'driver_error')
    return jsonify({'ok': True, 'rit_on': on})


@cat_bp.route('/cat/rit/clear', methods=['POST'])
def cat_rit_clear():
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    try:
        driver.clear_rit()
    except Exception as exc:
        return api_error(str(exc), 400, 'driver_error')
    return jsonify({'ok': True})


# --- Control: TX (Supervisor-gated) -----------------------------------------
@cat_bp.route('/cat/ptt', methods=['POST'])
def cat_ptt():
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    payload = request.get_json(silent=True) or {}
    tx = bool(payload.get('tx', False))

    if tx:
        with _supervisor_lock:
            if _supervisor.tx_locked:
                return api_error(
                    'TX is locked by supervisor',
                    403,
                    'tx_locked',
                )
    try:
        driver.ptt(tx)
    except Exception as exc:
        return api_error(str(exc), 400, 'driver_error')
    return jsonify({'ok': True, 'tx': tx})


@cat_bp.route('/cat/raw', methods=['POST'])
def cat_raw():
    """Send a raw vendor-specific CAT command.

    While ``tx_locked`` is true we only refuse commands that can actually
    key the rig (``TX;``, ``KY``, ``RX``-after-TX semantics on some
    Kenwoods). Query / mode / VFO / AI commands stay available — the
    lock is about transmission, not introspection.
    """
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    payload = request.get_json(silent=True) or {}
    cmd = str(payload.get('cmd') or '').strip()
    if not cmd:
        return api_error('cmd required', 400, 'missing_cmd')
    with _supervisor_lock:
        locked = _supervisor.tx_locked
    if locked and _command_keys_tx(cmd):
        return api_error(
            f'{cmd!r} can key the radio and is blocked while TX is locked',
            403,
            'tx_locked',
        )
    try:
        resp = driver.send_raw(cmd)
    except NotImplementedError:
        return api_error('driver has no raw passthrough', 400, 'unsupported')
    except Exception as exc:
        return api_error(str(exc), 400, 'driver_error')
    return jsonify({'ok': True, 'cmd': cmd, 'response': resp})


# Vendor-agnostic prefix list of commands that physically key the rig.
# Conservative: any cmd starting with one of these (case-insensitive) is
# refused while ``tx_locked`` is set. Anything else — IF;, AI1;, FA;,
# MD2;, etc. — passes through.
_TX_KEYING_PREFIXES = (
    'TX',   # Kenwood / Yaesu: start transmit
    'KY',   # Kenwood: keyer / CW message send (transmits)
    'KS',   # Yaesu: keyer send
)


def _command_keys_tx(cmd: str) -> bool:
    head = cmd.strip().upper().lstrip()
    # Strip leading non-alpha noise so e.g. ' tx;' still matches.
    for prefix in _TX_KEYING_PREFIXES:
        if head.startswith(prefix):
            return True
    return False



# --- Cable / radio diagnostics ----------------------------------------------
# Stand-alone probe that does not need the long-running driver thread.
# Refuses to run while a driver is connected so it can't fight for the port.
# Ported from `port/source/plugins/panadapter/diag.py::radio_probe`.
_PROBE_QUERIES = (b'ID;', b'IF;', b'FA;')


@cat_bp.route('/cat/probe', methods=['POST'])
def cat_probe():
    """Send a few low-level Kenwood queries and report what came back.

    Body: ``{port, baud, assert_rts, assert_dtr, timeout?}``. Designed
    for low-baud rigs (TS-850 default 4800) — uses inter-command delays
    and a per-query receive window.
    """
    if _driver() is not None:
        return api_error(
            'disconnect first — probe needs exclusive port access',
            409,
            'busy',
        )
    payload = request.get_json(silent=True) or {}
    port = str(payload.get('port') or '').strip()
    baud = int(payload.get('baud') or 4800)
    timeout = float(payload.get('timeout') or 1.0)
    assert_rts = bool(payload.get('assert_rts', False))
    assert_dtr = bool(payload.get('assert_dtr', False))
    # Framing defaults to whatever the currently-selected rig descriptor
    # specifies (e.g. 8N2 for the TS-850). Callers may override.
    desc = get_descriptor(_selected_rig_id)
    try:
        data_bits, stop_bits, parity = _validate_framing(
            payload.get('data_bits'),
            payload.get('stop_bits'),
            payload.get('parity'),
            desc,
        )
    except ValueError as exc:
        return api_error(str(exc), 400, 'invalid_framing')
    if not port:
        return api_error('port required', 400, 'missing_port')

    try:
        import serial  # type: ignore
    except ImportError:
        return api_error('pyserial not installed', 500, 'no_pyserial')

    result: dict[str, Any] = {
        'ok': True, 'port': port, 'baud': baud,
        'rts': assert_rts, 'dtr': assert_dtr,
        'data_bits': data_bits, 'stop_bits': stop_bits, 'parity': parity,
        'queries': [], 'total_rx_bytes': 0, 'valid_frames': 0,
        'verdict': '',
    }
    try:
        ser = serial.Serial(
            port, baudrate=baud, bytesize=data_bits, parity=parity,
            stopbits=stop_bits, timeout=timeout, rtscts=False, dsrdtr=False,
        )
        try:
            ser.rts = assert_rts
            ser.dtr = assert_dtr
        except Exception:
            pass
    except Exception as exc:
        return api_error(f'open failed: {exc}', 400, 'open_failed')

    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        # Quiet any auto-info flood before probing.
        ser.write(b'AI0;')
        ser.flush()
        time.sleep(0.1)
        ser.reset_input_buffer()

        for q in _PROBE_QUERIES:
            ser.write(q)
            ser.flush()
            t0 = time.time()
            buf = b''
            while time.time() - t0 < timeout:
                chunk = ser.read(64)
                if chunk:
                    buf += chunk
                    if buf.endswith(b';'):
                        break
            ascii_view = buf.decode('ascii', 'replace')
            looks_valid = (
                ascii_view.endswith(';')
                and ascii_view.startswith(q[:2].decode('ascii'))
            )
            if looks_valid:
                result['valid_frames'] += 1
            result['total_rx_bytes'] += len(buf)
            result['queries'].append({
                'sent': q.decode('ascii'),
                'received_ascii': ascii_view,
                'received_hex': buf.hex(),
                'received_bytes': len(buf),
                'looks_valid': looks_valid,
            })
            # Brief pause between commands — TS-850 needs settle time at 4800.
            time.sleep(0.05)
    finally:
        try:
            ser.close()
        except Exception:
            pass

    n = len(_PROBE_QUERIES)
    valid = result['valid_frames']
    total = result['total_rx_bytes']
    if valid == n:
        result['verdict'] = 'Radio replied to all queries. CAT link is fully working.'
    elif valid > 0:
        result['verdict'] = (
            f'Got {valid}/{n} valid replies. Link works but is unreliable — '
            'check baud, 2 stop bits, or cable strain.'
        )
    elif total > 0:
        result['verdict'] = (
            f'Received {total} bytes but none parsed as a Kenwood reply. '
            'Wrong baud / stop bits / rig protocol — check CAT menu on the rig.'
        )
    else:
        result['verdict'] = (
            'No bytes received. Either CAT mode is off, RX/TX swapped on the '
            'cable, the level shifter has no power, or the wrong port is selected.'
        )
    return jsonify(result)


# --- Control: extended (all optional per driver capabilities) ---------------
def _dispatch_simple(method_name: str, value: Any) -> tuple[bool, Any]:
    """Invoke ``driver.method_name(value)`` if available, else 400."""
    driver = _require_driver()
    if driver is None:
        return False, api_error('not connected', 409, 'not_connected')
    method = getattr(driver, method_name, None)
    if method is None:
        return False, api_error('unsupported', 400, 'unsupported')
    try:
        method(value)
    except NotImplementedError:
        return False, api_error('unsupported by driver', 400, 'unsupported')
    except (ValueError, TypeError) as exc:
        return False, api_error(str(exc), 400, 'invalid_value')
    except Exception as exc:
        return False, api_error(str(exc), 400, 'driver_error')
    return True, None


@cat_bp.route('/cat/agc', methods=['POST'])
def cat_agc():
    payload = request.get_json(silent=True) or {}
    ok, resp = _dispatch_simple('set_agc', payload.get('value', 0))
    return jsonify({'ok': True}) if ok else resp


@cat_bp.route('/cat/filter', methods=['POST'])
def cat_filter():
    payload = request.get_json(silent=True) or {}
    ok, resp = _dispatch_simple('set_filter', payload.get('slot', 0))
    return jsonify({'ok': True}) if ok else resp


@cat_bp.route('/cat/nb', methods=['POST'])
def cat_nb():
    payload = request.get_json(silent=True) or {}
    ok, resp = _dispatch_simple('set_noise_blanker', bool(payload.get('on', False)))
    return jsonify({'ok': True}) if ok else resp


@cat_bp.route('/cat/attenuator', methods=['POST'])
def cat_attenuator():
    payload = request.get_json(silent=True) or {}
    ok, resp = _dispatch_simple('set_attenuator', payload.get('step', 0))
    return jsonify({'ok': True}) if ok else resp


@cat_bp.route('/cat/af_gain', methods=['POST'])
def cat_af_gain():
    payload = request.get_json(silent=True) or {}
    ok, resp = _dispatch_simple('set_af_gain', payload.get('value', 0))
    return jsonify({'ok': True}) if ok else resp


@cat_bp.route('/cat/rf_gain', methods=['POST'])
def cat_rf_gain():
    payload = request.get_json(silent=True) or {}
    ok, resp = _dispatch_simple('set_rf_gain', payload.get('value', 0))
    return jsonify({'ok': True}) if ok else resp


@cat_bp.route('/cat/squelch', methods=['POST'])
def cat_squelch():
    payload = request.get_json(silent=True) or {}
    ok, resp = _dispatch_simple('set_squelch', payload.get('value', 0))
    return jsonify({'ok': True}) if ok else resp


@cat_bp.route('/cat/keyer', methods=['POST'])
def cat_keyer():
    payload = request.get_json(silent=True) or {}
    ok, resp = _dispatch_simple('set_keyer_speed', payload.get('wpm', 20))
    return jsonify({'ok': True}) if ok else resp


@cat_bp.route('/cat/power', methods=['POST'])
def cat_power():
    """Set TX power. Clamped by Supervisor's ``max_power_w``."""
    payload = request.get_json(silent=True) or {}
    try:
        watts = max(0, int(payload.get('watts', 0)))
    except (TypeError, ValueError):
        return api_error('watts must be int', 400, 'invalid_value')
    with _supervisor_lock:
        capped = _supervisor.cap_power(watts)
    ok, resp = _dispatch_simple('set_power', capped)
    return jsonify({'ok': True, 'watts': capped, 'requested': watts}) if ok else resp


@cat_bp.route('/cat/step', methods=['POST'])
def cat_step():
    payload = request.get_json(silent=True) or {}
    direction = str(payload.get('direction') or 'up').lower()
    if direction not in ('up', 'down'):
        return api_error('direction must be up or down', 400, 'invalid_value')
    ok, resp = _dispatch_simple('vfo_step', direction)
    return jsonify({'ok': True, 'direction': direction}) if ok else resp


# --- Supervisor --------------------------------------------------------------
@cat_bp.route('/cat/supervisor', methods=['GET', 'POST'])
def cat_supervisor():
    global _supervisor
    if request.method == 'GET':
        return jsonify(_supervisor_snapshot())

    payload = request.get_json(silent=True) or {}
    with _supervisor_lock:
        _supervisor.apply_update(payload)
        save_supervisor(_supervisor)
        snap = _supervisor.to_dict()
    _publish({'type': 'supervisor', 'supervisor': snap})
    return jsonify(snap)


@cat_bp.route('/cat/supervisor/check_freq', methods=['GET'])
def cat_supervisor_check_freq():
    try:
        hz = _validate_hz(request.args.get('hz'))
    except ValueError as exc:
        return api_error(str(exc), 400, 'invalid_hz')
    with _supervisor_lock:
        allowed = _supervisor.freq_allowed(hz)
    return jsonify({'hz': hz, 'allowed': allowed})


# --- SSE ---------------------------------------------------------------------
@cat_bp.route('/cat/stream')
def cat_stream() -> Response:
    response = Response(
        sse_stream_fanout(
            source_queue=app_module.cat_queue,
            channel_key='cat',
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


# --- Command catalog & macros ------------------------------------------------
# Backing store: utils.cat.macros_db (SQLite at instance/cat.db).
# Initialised once at app startup via utils.cat.init_command_catalog().
#
# Naming convention follows the rest of /cat/* — verb-only paths, plural
# resource names. All endpoints are rig-scoped via either the URL or
# the JSON body so the picker can switch rigs without re-registering.
from utils.cat import macros_db


def _rig_arg() -> str:
    """Resolve the rig_id from query string or fall back to current selection."""
    return (request.args.get('rig_id') or _selected_rig_id or 'kenwood_ts850').strip()


@cat_bp.route('/cat/commands', methods=['GET'])
def cat_commands_list():
    """List built-in + user-added CAT commands for a rig.

    Query string:
        rig_id   — defaults to the current selection
        q        — case-insensitive substring match on name / template / desc
        category — exact match on category
    Response: ``{commands: [...], categories: [...]}``
    """
    rig_id = _rig_arg()
    q = (request.args.get('q') or '').strip() or None
    category = (request.args.get('category') or '').strip() or None
    try:
        commands = macros_db.list_commands(rig_id, category=category, search=q)
        categories = macros_db.list_categories(rig_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning('cat_commands_list failed: %s', exc)
        return api_error('catalog unavailable', 500, 'catalog_error')
    return jsonify({'rig_id': rig_id, 'commands': commands, 'categories': categories})


@cat_bp.route('/cat/commands', methods=['POST'])
def cat_commands_add():
    """Add a user-defined command (``is_builtin = 0``)."""
    payload = request.get_json(silent=True) or {}
    rig_id = (payload.get('rig_id') or _selected_rig_id or '').strip()
    name = (payload.get('name') or '').strip()
    raw_template = (payload.get('raw_template') or '').strip()
    if not rig_id or not name or not raw_template:
        return api_error('rig_id, name and raw_template are required',
                         400, 'missing_fields')
    try:
        cmd = macros_db.add_custom_command(
            rig_id=rig_id,
            name=name,
            raw_template=raw_template,
            category=(payload.get('category') or 'custom').strip(),
            param_label=payload.get('param_label') or None,
            param_type=(payload.get('param_type') or 'none').strip(),
            param_default=payload.get('param_default') or None,
            description=payload.get('description') or None,
        )
    except Exception as exc:  # noqa: BLE001
        return api_error(str(exc), 400, 'invalid')
    return jsonify({'ok': True, 'command': cmd})


@cat_bp.route('/cat/commands/<int:command_id>', methods=['DELETE'])
def cat_commands_delete(command_id: int):
    """Delete a user-added command. Built-ins are refused."""
    try:
        ok = macros_db.delete_command(command_id)
    except Exception as exc:  # noqa: BLE001
        return api_error(str(exc), 400, 'invalid')
    if not ok:
        return api_error('builtin or not found', 404, 'not_found')
    return jsonify({'ok': True, 'id': command_id})


@cat_bp.route('/cat/macros', methods=['GET'])
def cat_macros_list():
    rig_id = _rig_arg()
    try:
        macros = macros_db.list_macros(rig_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning('cat_macros_list failed: %s', exc)
        return api_error('catalog unavailable', 500, 'catalog_error')
    return jsonify({'rig_id': rig_id, 'macros': macros})


@cat_bp.route('/cat/macros', methods=['POST'])
def cat_macros_save():
    """Create or replace a macro by ``(rig_id, name)``.

    Body: ``{rig_id, name, description?, steps: [{command_id|raw_template,
    param_value?, delay_ms?, note?}]}``
    """
    payload = request.get_json(silent=True) or {}
    rig_id = (payload.get('rig_id') or _selected_rig_id or '').strip()
    name = (payload.get('name') or '').strip()
    if not rig_id or not name:
        return api_error('rig_id and name are required', 400, 'missing_fields')
    steps = payload.get('steps') or []
    if not isinstance(steps, list):
        return api_error('steps must be a list', 400, 'invalid')
    try:
        macro = macros_db.upsert_macro(
            rig_id=rig_id,
            name=name,
            description=payload.get('description') or None,
            steps=steps,
        )
    except Exception as exc:  # noqa: BLE001
        return api_error(str(exc), 400, 'invalid')
    return jsonify({'ok': True, 'macro': macro})


@cat_bp.route('/cat/macros/<int:macro_id>', methods=['GET'])
def cat_macros_get(macro_id: int):
    macro = macros_db.get_macro(macro_id)
    if not macro:
        return api_error('not found', 404, 'not_found')
    return jsonify({'ok': True, 'macro': macro})


@cat_bp.route('/cat/macros/<int:macro_id>', methods=['DELETE'])
def cat_macros_delete(macro_id: int):
    try:
        ok = macros_db.delete_macro(macro_id)
    except Exception as exc:  # noqa: BLE001
        return api_error(str(exc), 400, 'invalid')
    if not ok:
        return api_error('not found', 404, 'not_found')
    return jsonify({'ok': True, 'id': macro_id})


@cat_bp.route('/cat/macros/<int:macro_id>/run', methods=['POST'])
def cat_macros_run(macro_id: int):
    """Execute a macro against the live CAT driver.

    Supervisor gate: any step whose ``raw_command`` would key the rig is
    refused while ``tx_locked`` is set — same rule as ``/cat/raw``. The
    refused step is reported in the result but execution continues
    through the remaining steps.
    """
    driver = _require_driver()
    if driver is None:
        return api_error('not connected', 409, 'not_connected')
    macro = macros_db.get_macro(macro_id)
    if not macro:
        return api_error('not found', 404, 'not_found')
    with _supervisor_lock:
        locked = _supervisor.tx_locked
    if locked:
        blocked = [s for s in macro['steps']
                   if _command_keys_tx(s['raw_command'])]
        if blocked:
            return api_error(
                f"macro contains {len(blocked)} TX-keying step(s) and TX is locked",
                403, 'tx_locked',
            )
    try:
        result = macros_db.run_macro(macro_id, driver)
    except Exception as exc:  # noqa: BLE001
        return api_error(str(exc), 400, 'driver_error')
    # Echo the result onto the CAT SSE stream so the terminal shows it.
    _publish({
        'type': 'macro',
        'macro_id': macro_id,
        'macro_name': result.get('macro_name'),
        'elapsed_s': result.get('elapsed_s'),
        'steps': result.get('steps', []),
    })
    return jsonify(result)
