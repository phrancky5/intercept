"""rigctld-compatible TCP relay (optional, used by the CAT bridge).

Exposes a minimal subset of Hamlib's ``rigctld`` text protocol on TCP so
external ham-radio applications (WSJT-X, Fldigi, JS8Call, N1MM, Gpredict,
CQRLOG ...) can drive whatever rig the bridge is connected to, without
shipping Hamlib.

This is a vendor-neutral port of the cat-ftx1 ``rigctld-relay.mjs``: it
drives the rig through intercept's high-level
:class:`~utils.cat.base.RigDriver` / :class:`~utils.cat.base.RigState`
API instead of raw vendor frames, so it works for any implemented driver.

Protocol notes
--------------
* Line-based ASCII. One request line; one or more response lines.
* "Single-letter mode" (the legacy default) is what common clients use.
  The "+extended" response mode is not supported.
* GET commands: response is the value(s), one per line, with NO trailing
  ``RPRT 0`` (this matches Hamlib's actual basic-mode behaviour).
* SET commands: ``RPRT 0`` on success, ``RPRT -N`` on error (N = Hamlib
  errno).
* ``dump_state`` returns a capabilities dump that WSJT-X parses once.
* Unknown commands -> ``RPRT -11`` (RIG_ENAVAIL).

This module is part of the *optional* bridge feature; the main intercept
app never imports it.
"""

from __future__ import annotations

import logging
import socket
import socketserver
import threading
from typing import Callable, Optional

logger = logging.getLogger('intercept.cat.rigctld')


# --- Hamlib errno subset (positive here; negated on the wire) ---------------
class ERR:
    OK = 0
    EINVAL = 1       # invalid parameter
    ETIMEOUT = 5     # communication timeout
    EIO = 6          # I/O error (e.g. radio not connected)
    EINTERNAL = 7
    ERJCTED = 9      # rig rejected the command (used for TX-lock)
    ENAVAIL = 11     # function not available -> unknown commands
    EVFO = 16        # VFO not targetable


# --- mode name maps: rigctld <-> intercept RigState.mode --------------------
# RigState.mode strings come from the drivers (e.g. yaesu_ftx1 MODE_CODES:
# LSB/USB/CW/FM/AM/FSK/CW-R/DATA-L/FSK-R/DATA-FM/FM-N/DATA-U/AM-N).
RIGCTLD_FROM_RIGSTATE = {
    'LSB': 'LSB', 'USB': 'USB',
    'CW': 'CW', 'CW-U': 'CW', 'CW-R': 'CWR', 'CW-L': 'CWR',
    'AM': 'AM', 'AM-N': 'AM', 'AMN': 'AM',
    'FM': 'FM', 'FM-N': 'FM', 'FMN': 'FM',
    'FSK': 'RTTY', 'RTTY': 'RTTY', 'RTTY-L': 'RTTY',
    'FSK-R': 'RTTYR', 'RTTY-U': 'RTTYR',
    'DATA-L': 'PKTLSB', 'DATA-U': 'PKTUSB', 'DATA-FM': 'PKTFM',
    'PKTLSB': 'PKTLSB', 'PKTUSB': 'PKTUSB', 'PKTFM': 'PKTFM',
}

RIGSTATE_FROM_RIGCTLD = {
    'LSB': 'LSB', 'USB': 'USB',
    'CW': 'CW', 'CWU': 'CW', 'CWR': 'CW-R', 'CWL': 'CW-R',
    'AM': 'AM',
    'FM': 'FM',
    'RTTY': 'FSK', 'RTTYR': 'FSK-R',
    'PKTLSB': 'DATA-L', 'PKTUSB': 'DATA-U', 'PKTFM': 'DATA-FM',
}

# Modes bitmap (Hamlib rig_mode_t subset) used in dump_state:
#   USB|LSB|CW|CWR|AM|FM|RTTY|RTTYR|PKTLSB|PKTUSB|PKTFM = 0x3bbf
_MODES_MASK = '0x3bbf'


def _build_dump_state() -> str:
    """Build a conservative ``dump_state`` capabilities dump.

    Generic HF + 6 m coverage. Clients (WSJT-X especially) parse this to
    learn frequency ranges and supported modes; the values are
    deliberately broad rather than a faithful per-rig clone.
    """
    lines: list[str] = []
    lines.append('0')        # protocol version 0 (basic)
    lines.append('2')        # rig model number (2 = NETRIGCTL/generic-ish)
    lines.append('2')        # ITU region 2

    # RX range: {start end modes low_pwr high_pwr vfo_mask ant_mask}
    lines.append(f'30000 56000000 {_MODES_MASK} -1 -1 0x3 0x1')
    lines.append('0 0 0 0 0 0 0')

    # TX ranges (IARU HF + 6 m ham bands); power range in milliwatts.
    tx_bands = [
        '1800000 2000000', '3500000 4000000', '5300000 5410000',
        '7000000 7300000', '10100000 10150000', '14000000 14350000',
        '18068000 18168000', '21000000 21450000', '24890000 24990000',
        '28000000 29700000', '50000000 54000000',
    ]
    for band in tx_bands:
        lines.append(f'{band} {_MODES_MASK} 1000 100000 0x3 0x1')
    lines.append('0 0 0 0 0 0 0')

    # Tuning steps {modes hz}
    for step in ('1', '10', '100', '1000'):
        lines.append(f'{_MODES_MASK} {step}')
    lines.append('0 0')

    # IF filters {modes hz}
    lines.append('0x2 500')      # CW 500 Hz
    lines.append('0xc 2400')     # SSB 2400 Hz
    lines.append('0x1 6000')     # AM 6 kHz
    lines.append('0x20 12000')   # FM 12 kHz
    lines.append('0 0')

    lines.append('0')   # max RIT
    lines.append('0')   # max XIT
    lines.append('0')   # max IF shift
    lines.append('0')   # announce
    lines.append('0')   # preamp list (0-terminated)
    lines.append('0')   # attenuator list (0-terminated)
    lines.append('0')   # has_get_func
    lines.append('0')   # has_set_func
    lines.append('0')   # has_get_level
    lines.append('0')   # has_set_level
    lines.append('0')   # has_get_parm
    lines.append('0')   # has_set_parm
    lines.append('done')
    return '\n'.join(lines) + '\n'


_DUMP_STATE = _build_dump_state()


class _Handler(socketserver.StreamRequestHandler):
    """Per-connection rigctld protocol handler."""

    # Access to the owning relay is via ``self.server.relay``.
    timeout = None  # blocking; clients hold the connection open

    def handle(self) -> None:  # noqa: C901 - protocol dispatch is naturally branchy
        relay: 'RigctldRelay' = self.server.relay  # type: ignore[attr-defined]
        ip = self.client_address[0]
        if ip.startswith('::ffff:'):
            ip = ip[len('::ffff:'):]
        remote = f'{ip}:{self.client_address[1]}'

        if not relay._is_allowed(ip):
            logger.warning('rigctld: rejected connection from %s (not on ALLOWED_IPS)', remote)
            return

        relay._emit('client-connect', {'remote': remote})
        if relay.debug:
            logger.info('rigctld: connect %s', remote)

        # Per-socket virtual-split bookkeeping.
        ctx = {'remote': remote, 'split': False, 'split_tx_vfo': 'VFOA'}
        try:
            while True:
                raw = self.rfile.readline()
                if not raw:
                    break
                line = raw.decode('ascii', errors='ignore').strip()
                if not line:
                    continue
                relay._emit('line', {'remote': remote, 'dir': 'in', 'text': line})
                try:
                    if self._dispatch(relay, ctx, line) is False:
                        break  # quit requested
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug('rigctld handler error on %r: %s', line, exc)
                    self._rprt(relay, ctx, ERR.EINTERNAL)
        finally:
            relay._emit('client-disconnect', {'remote': remote})
            if relay.debug:
                logger.info('rigctld: disconnect %s', remote)

    # -- dispatch ------------------------------------------------------------
    def _dispatch(self, relay, ctx, line):
        rest = line
        if rest.startswith('\\'):
            sp = rest.find(' ')
            key = rest if sp == -1 else rest[:sp]
            arg = '' if sp == -1 else rest[sp + 1:].strip()
        else:
            key = rest[0]
            arg = rest[1:].strip()

        if key in ('f', '\\get_freq'):
            return self._get_freq(relay, ctx)
        if key in ('F', '\\set_freq'):
            return self._set_freq(relay, ctx, arg)
        if key in ('i', '\\get_split_freq'):
            return self._get_split_freq(relay, ctx)
        if key in ('I', '\\set_split_freq'):
            return self._set_split_freq(relay, ctx, arg)
        if key in ('m', '\\get_mode'):
            return self._get_mode(relay, ctx)
        if key in ('M', '\\set_mode'):
            return self._set_mode(relay, ctx, arg)
        if key in ('x', '\\get_split_mode'):
            return self._get_mode(relay, ctx)
        if key in ('X', '\\set_split_mode'):
            return self._set_mode(relay, ctx, arg)
        if key in ('t', '\\get_ptt'):
            return self._get_ptt(relay, ctx)
        if key in ('T', '\\set_ptt'):
            return self._set_ptt(relay, ctx, arg)
        if key in ('v', '\\get_vfo'):
            return self._get_vfo(relay, ctx)
        if key in ('V', '\\set_vfo'):
            return self._set_vfo(relay, ctx, arg)
        if key in ('s', '\\get_split_vfo'):
            return self._get_split(relay, ctx)
        if key in ('S', '\\set_split_vfo'):
            return self._set_split(relay, ctx, arg)
        if key in ('1', '\\dump_state'):
            return self._raw(relay, ctx, _DUMP_STATE)
        if key == '\\chk_vfo':
            return self._raw(relay, ctx, 'CHKVFO 0\n')
        if key in ('q', 'Q', '\\quit'):
            return False

        return self._rprt(relay, ctx, ERR.ENAVAIL)

    # -- frequency -----------------------------------------------------------
    def _get_freq(self, relay, ctx):
        st = relay._state()
        if st is None or not st.connected:
            return self._rprt(relay, ctx, ERR.EIO)
        return self._raw(relay, ctx, f'{int(st.vfo_a_hz)}\n')

    def _set_freq(self, relay, ctx, arg):
        try:
            hz = int(arg)
        except ValueError:
            return self._rprt(relay, ctx, ERR.EINVAL)
        return self._call(relay, ctx, 'set_vfo', 'A', hz)

    def _get_split_freq(self, relay, ctx):
        st = relay._state()
        if st is None or not st.connected:
            return self._rprt(relay, ctx, ERR.EIO)
        return self._raw(relay, ctx, f'{int(st.vfo_b_hz)}\n')

    def _set_split_freq(self, relay, ctx, arg):
        try:
            hz = int(arg)
        except ValueError:
            return self._rprt(relay, ctx, ERR.EINVAL)
        return self._call(relay, ctx, 'set_vfo', 'B', hz)

    # -- mode ----------------------------------------------------------------
    def _get_mode(self, relay, ctx):
        st = relay._state()
        if st is None or not st.connected:
            return self._rprt(relay, ctx, ERR.EIO)
        name = RIGCTLD_FROM_RIGSTATE.get(st.mode, st.mode or 'USB')
        # Bandwidth 0 = "use rig's current width".
        return self._raw(relay, ctx, f'{name}\n0\n')

    def _set_mode(self, relay, ctx, arg):
        parts = arg.split()
        if not parts:
            return self._rprt(relay, ctx, ERR.EINVAL)
        name = parts[0].upper()
        mode = RIGSTATE_FROM_RIGCTLD.get(name)
        if mode is None:
            return self._rprt(relay, ctx, ERR.EINVAL)
        return self._call(relay, ctx, 'set_mode', mode)

    # -- PTT -----------------------------------------------------------------
    def _get_ptt(self, relay, ctx):
        st = relay._state()
        if st is None or not st.connected:
            return self._rprt(relay, ctx, ERR.EIO)
        return self._raw(relay, ctx, f'{1 if st.ptt else 0}\n')

    def _set_ptt(self, relay, ctx, arg):
        v = arg.strip()
        if v not in ('0', '1'):
            return self._rprt(relay, ctx, ERR.EINVAL)
        if v == '1' and relay.tx_locked:
            logger.warning('rigctld: PTT refused (BRIDGE_TX_LOCK on) from %s', ctx['remote'])
            return self._rprt(relay, ctx, ERR.ERJCTED)
        return self._call(relay, ctx, 'ptt', v == '1')

    # -- VFO -----------------------------------------------------------------
    def _get_vfo(self, relay, ctx):
        st = relay._state()
        if st is None or not st.connected:
            return self._rprt(relay, ctx, ERR.EIO)
        return self._raw(relay, ctx, f"{'VFOB' if st.active_vfo == 'B' else 'VFOA'}\n")

    def _set_vfo(self, relay, ctx, arg):
        v = arg.strip().upper()
        if v in ('VFOA', 'MAIN'):
            which = 'A'
        elif v in ('VFOB', 'SUB'):
            which = 'B'
        else:
            return self._rprt(relay, ctx, ERR.EVFO)
        return self._call(relay, ctx, 'select_vfo', which, optional=True)

    # -- split ---------------------------------------------------------------
    def _get_split(self, relay, ctx):
        st = relay._state()
        if st is None or not st.connected:
            return self._rprt(relay, ctx, ERR.EIO)
        on = 1 if (st.split or ctx['split']) else 0
        return self._raw(relay, ctx, f"{on}\n{ctx['split_tx_vfo']}\n")

    def _set_split(self, relay, ctx, arg):
        parts = arg.split()
        on = parts[0] == '1' if parts else False
        tx_vfo = (parts[1] if len(parts) > 1 else ('VFOB' if on else 'VFOA')).upper()
        if tx_vfo not in ('VFOA', 'VFOB', 'MAIN', 'SUB'):
            return self._rprt(relay, ctx, ERR.EVFO)
        ctx['split'] = on
        ctx['split_tx_vfo'] = 'VFOA' if tx_vfo in ('VFOA', 'MAIN') else 'VFOB'
        return self._call(relay, ctx, 'set_split', on, optional=True)

    # -- driver call helper --------------------------------------------------
    def _call(self, relay, ctx, method, *args, optional: bool = False):
        drv = relay._driver()
        if drv is None:
            return self._rprt(relay, ctx, ERR.EIO)
        fn = getattr(drv, method, None)
        if fn is None:
            return self._rprt(relay, ctx, ERR.OK if optional else ERR.ENAVAIL)
        try:
            fn(*args)
            return self._rprt(relay, ctx, ERR.OK)
        except NotImplementedError:
            return self._rprt(relay, ctx, ERR.OK if optional else ERR.ENAVAIL)
        except (ValueError, TypeError):
            return self._rprt(relay, ctx, ERR.EINVAL)
        except Exception as exc:
            logger.debug('rigctld: driver.%s raised %s', method, exc)
            return self._rprt(relay, ctx, ERR.EIO)

    # -- wire helpers --------------------------------------------------------
    def _rprt(self, relay, ctx, code):
        wire = 0 if code == 0 else -abs(code)
        return self._raw(relay, ctx, f'RPRT {wire}\n')

    def _raw(self, relay, ctx, payload: str):
        for ln in payload.split('\n'):
            if ln == '':
                continue
            relay._emit('line', {'remote': ctx['remote'], 'dir': 'out', 'text': ln})
        try:
            self.wfile.write(payload.encode('ascii'))
            self.wfile.flush()
        except OSError:
            return False
        return True


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class RigctldRelay:
    """Threaded rigctld-compatible TCP server bound to a live driver.

    Parameters
    ----------
    driver_provider:
        Zero-arg callable returning the currently connected
        :class:`RigDriver` (or ``None`` when disconnected).
    host, port:
        Bind address/port. Default ``0.0.0.0:4532`` (the Hamlib default).
    is_allowed:
        Predicate ``(ip:str) -> bool`` gating each incoming connection.
        Defaults to allow-all (callers should pass the real allowlist).
    tx_locked:
        When ``True`` (default), rigctld ``T 1`` (PTT on) is refused, so
        external clients cannot key the rig until explicitly unlocked.
    on_event:
        Optional callback ``(name:str, payload:dict)`` for connect /
        disconnect / line traffic (used to mirror traffic to the bridge
        SSE feed). Errors in the callback are swallowed.
    """

    def __init__(
        self,
        driver_provider: Callable[[], object],
        *,
        host: str = '0.0.0.0',
        port: int = 4532,
        is_allowed: Optional[Callable[[str], bool]] = None,
        tx_locked: bool = True,
        on_event: Optional[Callable[[str, dict], None]] = None,
        debug: bool = False,
    ) -> None:
        self._driver = driver_provider
        self.host = host
        self.port = port
        self._is_allowed = is_allowed if callable(is_allowed) else (lambda _ip: True)
        self.tx_locked = bool(tx_locked)
        self._on_event = on_event
        self.debug = bool(debug)
        self._server: Optional[_ThreadingTCPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _state(self):
        drv = self._driver()
        if drv is None:
            return None
        try:
            return drv.state()
        except Exception:
            return None

    def _emit(self, name: str, payload: dict) -> None:
        if not self._on_event:
            return
        try:
            self._on_event(name, payload)
        except Exception:
            pass

    def start(self) -> None:
        """Begin accepting connections. Idempotent."""
        if self._server is not None:
            return
        self._server = _ThreadingTCPServer((self.host, self.port), _Handler)
        self._server.relay = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name='rigctld-relay', daemon=True,
        )
        self._thread.start()
        logger.info('rigctld relay listening on %s:%d (rigctld-compatible)', self.host, self.port)

    def stop(self) -> None:
        """Stop accepting connections and shut the server down. Idempotent."""
        srv = self._server
        if srv is None:
            return
        self._server = None
        try:
            srv.shutdown()
            srv.server_close()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug('rigctld stop error: %s', exc)
        logger.info('rigctld relay stopped')
