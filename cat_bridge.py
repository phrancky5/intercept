#!/usr/bin/env python3
"""Standalone host-side CAT serial bridge (OPTIONAL feature).

Runs on the machine the radio's USB-serial cable is physically plugged
into (recommended: natively on the Windows host, where COM ports
enumerate reliably with no usbipd/Docker passthrough). It owns the serial
port via intercept's existing :mod:`utils.cat` drivers and exposes it:

* over HTTP (+ Server-Sent Events) so a dockerised intercept instance can
  drive the rig through ``CAT_BRIDGE_URL`` instead of fighting the
  Docker/WSL device-passthrough boundary, and
* over a rigctld-compatible TCP relay so apps like WSJT-X / Fldigi /
  JS8Call / Gpredict can control the same radio.

This file is entirely opt-in: the main intercept app never imports it and
runs unchanged if you never start the bridge. It adds no new
dependencies (Flask + pyserial are already required by intercept).

Run (Windows host example)::

    set CAT_BRIDGE_TOKEN=changeme
    set ALLOWED_IPS=127.0.0.1/8,::1,172.16.0.0/12
    python cat_bridge.py

Then point the container at it (docker-compose env)::

    CAT_BRIDGE_URL=http://host.docker.internal:5060
    CAT_BRIDGE_TOKEN=changeme

Environment variables
---------------------
CAT_BRIDGE_HOST    Bind address for the HTTP API (default 0.0.0.0)
CAT_BRIDGE_PORT    HTTP API port (default 5060)
CAT_BRIDGE_TOKEN   Shared bearer token. If unset, one is generated and
                   printed at startup (and written to a token file).
ALLOWED_IPS        Comma-separated IP/CIDR allowlist for HTTP + rigctld
                   (default loopback only).
RIGCTLD_ENABLE     "1" (default) / "0" to disable the rigctld relay.
RIGCTLD_HOST       rigctld bind address (default 0.0.0.0).
RIGCTLD_PORT       rigctld TCP port (default 4532).
BRIDGE_TX_LOCK     "1" (default) / "0". When on, rigctld PTT is refused
                   so external apps cannot key the rig until unlocked.
RIGCTLD_DEBUG      "1" to log every rigctld line.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import sys
import threading
import time

# Make ``utils.cat`` importable no matter the working directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from flask import Flask, Response, jsonify, request  # noqa: E402

from utils.cat import (  # noqa: E402
    RIG_REGISTRY,
    get_descriptor,
    list_serial_ports,
)
from utils.cat.base import RigState  # noqa: E402
from utils.cat.ip_allowlist import get_allowlist  # noqa: E402
from utils.cat.rigctld_relay import RigctldRelay  # noqa: E402

# --- config -----------------------------------------------------------------
HTTP_HOST = os.environ.get('CAT_BRIDGE_HOST', '0.0.0.0')
HTTP_PORT = int(os.environ.get('CAT_BRIDGE_PORT', '5060'))
RIGCTLD_ENABLE = os.environ.get('RIGCTLD_ENABLE', '1') != '0'
RIGCTLD_HOST = os.environ.get('RIGCTLD_HOST', '0.0.0.0')
RIGCTLD_PORT = int(os.environ.get('RIGCTLD_PORT', '4532'))
BRIDGE_TX_LOCK = os.environ.get('BRIDGE_TX_LOCK', '1') != '0'
RIGCTLD_DEBUG = os.environ.get('RIGCTLD_DEBUG', '0') == '1'
BRIDGE_DEBUG = os.environ.get('CAT_BRIDGE_DEBUG', '0') == '1'
_TOKEN_FILE = os.path.join(_HERE, 'instance', 'cat_bridge_token')

# --- connection tracking for debugging --------------------------------------
_connections: dict[str, dict] = {}  # remote_ip -> {first_seen, last_seen, requests, type}
_connections_lock = threading.Lock()


def _track_connection(remote_ip: str, conn_type: str = 'http') -> None:
    """Track incoming connections for debugging."""
    now = time.time()
    with _connections_lock:
        if remote_ip not in _connections:
            _connections[remote_ip] = {
                'first_seen': now,
                'last_seen': now,
                'requests': 1,
                'type': conn_type,
            }
            if BRIDGE_DEBUG:
                print(f'[cat-bridge] NEW {conn_type.upper()} connection from {remote_ip}')
        else:
            _connections[remote_ip]['last_seen'] = now
            _connections[remote_ip]['requests'] += 1
            _connections[remote_ip]['type'] = conn_type


def _get_connection_stats() -> list[dict]:
    """Get list of tracked connections."""
    with _connections_lock:
        return [
            {'ip': ip, **info}
            for ip, info in sorted(_connections.items(), key=lambda x: x[1]['last_seen'], reverse=True)
        ]

# Methods the container may invoke via POST /rpc. Restricting the set
# keeps the bridge from exposing arbitrary attribute access.
_RPC_METHODS = frozenset({
    'set_vfo', 'set_mode', 'select_vfo', 'set_split', 'set_rit', 'clear_rit',
    'ptt', 'set_agc', 'set_filter', 'set_noise_blanker', 'set_attenuator',
    'set_af_gain', 'set_rf_gain', 'set_squelch', 'set_keyer_speed',
    'set_power', 'vfo_step', 'set_memory_channel', 'select_memory',
    'request_status', 'send_raw', 'set_polling',
})


def _ensure_token() -> str:
    tok = (os.environ.get('CAT_BRIDGE_TOKEN') or '').strip()
    if tok:
        return tok
    # Generate, persist (best-effort) and surface so the operator can copy
    # it into the container's CAT_BRIDGE_TOKEN.
    tok = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(_TOKEN_FILE), exist_ok=True)
        with open(_TOKEN_FILE, 'w', encoding='utf-8') as fh:
            fh.write(tok)
    except OSError:
        pass
    print('=' * 70)
    print('[cat-bridge] CAT_BRIDGE_TOKEN was not set — generated a token:')
    print(f'             {tok}')
    print('[cat-bridge] Set this as CAT_BRIDGE_TOKEN on the intercept container.')
    print('=' * 70)
    return tok


TOKEN = _ensure_token()
ALLOWLIST = get_allowlist()


# --- driver state -----------------------------------------------------------
class BridgeManager:
    """Owns at most one connected :class:`RigDriver` plus SSE fan-out."""

    def __init__(self) -> None:
        self._driver = None
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._sub_lock = threading.Lock()

    # -- subscribers -----------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._sub_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            self._subscribers.discard(q)

    def broadcast(self, event: dict) -> None:
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    # -- driver access ---------------------------------------------------
    def driver(self):
        with self._lock:
            return self._driver

    def running_driver(self):
        """Return the driver only if it is actively running (for rigctld)."""
        with self._lock:
            drv = self._driver
        if drv is not None and drv.is_running():
            return drv
        return None

    def status(self) -> dict:
        drv = self.driver()
        if drv is None:
            return {'connected': False, 'state': RigState().to_dict()}
        try:
            st = drv.state()
            return {'connected': bool(st.connected), 'state': st.to_dict()}
        except Exception as exc:
            return {'connected': False, 'error': str(exc), 'state': RigState().to_dict()}

    # -- lifecycle -------------------------------------------------------
    def connect(self, payload: dict) -> dict:
        rig_id = str(payload.get('rig_id') or '').strip()
        desc = get_descriptor(rig_id)
        if desc is None or not desc.implemented:
            raise ValueError(f'unknown or unimplemented rig {rig_id!r}')
        port = str(payload.get('port') or '').strip()
        if not port:
            raise ValueError('port is required')
        try:
            baud = int(payload.get('baud') or desc.default_baud)
        except (TypeError, ValueError):
            raise ValueError('baud must be an integer')
        if baud not in desc.supported_bauds:
            raise ValueError(f'baud {baud} not in {list(desc.supported_bauds)}')

        def _coerce_int(value, default):
            try:
                return int(value) if value is not None else int(default)
            except (TypeError, ValueError):
                raise ValueError('data_bits/stop_bits must be integers')

        data_bits = _coerce_int(payload.get('data_bits'), desc.data_bits)
        stop_bits = _coerce_int(payload.get('stop_bits'), desc.stop_bits)
        parity = str(payload.get('parity') or desc.parity).upper()

        with self._lock:
            if self._driver is not None:
                try:
                    self._driver.stop()
                except Exception:
                    pass
                self._driver = None

            driver = desc.driver_class(  # type: ignore[misc]
                port=port,
                baud=baud,
                on_status=self._on_status,
                on_io=self._on_io,
                assert_rts=bool(payload.get('assert_rts', False)),
                assert_dtr=bool(payload.get('assert_dtr', False)),
                data_bits=data_bits,
                stop_bits=stop_bits,
                parity=parity,
            )
            driver.start()
            self._driver = driver

        self.broadcast({'type': 'lifecycle', 'event': 'connected',
                        'rig_id': rig_id, 'port': port, 'baud': baud})
        return {'connected': True, 'rig_id': rig_id, 'port': port, 'baud': baud,
                'data_bits': data_bits, 'stop_bits': stop_bits, 'parity': parity}

    def disconnect(self) -> dict:
        with self._lock:
            drv = self._driver
            self._driver = None
        if drv is not None:
            try:
                drv.stop()
            except Exception:
                pass
        self.broadcast({'type': 'lifecycle', 'event': 'disconnected'})
        return {'connected': False}

    def rpc(self, method: str, args: list) -> dict:
        if method not in _RPC_METHODS:
            raise ValueError(f'method {method!r} not allowed')
        drv = self.driver()
        if drv is None or not drv.is_running():
            raise RuntimeError('not connected')
        fn = getattr(drv, method, None)
        if fn is None:
            raise ValueError(f'driver has no method {method!r}')
        result = fn(*(args or []))
        return {'ok': True, 'result': result}

    # -- driver callbacks ------------------------------------------------
    def _on_status(self, state: RigState) -> None:
        self.broadcast({'type': 'state', 'state': state.to_dict()})

    def _on_io(self, direction: str, payload: str) -> None:
        self.broadcast({'type': 'io', 'direction': direction,
                        'payload': payload, 'ts': time.time()})


manager = BridgeManager()


# --- Flask app --------------------------------------------------------------
app = Flask(__name__)


def _gate():
    """Return a Flask error response if the request is not authorised.

    Combines the IP allowlist (matches the rigctld relay) with the shared
    bearer token. SSE clients pass the token via ?token= because
    EventSource cannot set headers.
    """
    remote = request.remote_addr or ''
    endpoint = request.path
    
    # Check IP allowlist first
    if not ALLOWLIST.is_allowed(remote):
        if BRIDGE_DEBUG:
            print(f'[cat-bridge] DENIED {remote} -> {endpoint} (IP not in allowlist)')
        return jsonify({'error': 'forbidden', 'detail': f'IP {remote} not in ALLOWED_IPS'}), 403
    
    # Check token
    auth = request.headers.get('Authorization', '')
    tok = auth[7:].strip() if auth.startswith('Bearer ') else ''
    if not tok:
        tok = (request.args.get('token') or '').strip()
    if not tok or not secrets.compare_digest(tok, TOKEN):
        if BRIDGE_DEBUG:
            print(f'[cat-bridge] DENIED {remote} -> {endpoint} (invalid or missing token)')
        return jsonify({'error': 'unauthorized', 'detail': 'Invalid or missing CAT_BRIDGE_TOKEN'}), 401
    
    # Track successful connection
    _track_connection(remote, 'http')
    if BRIDGE_DEBUG:
        print(f'[cat-bridge] OK {remote} -> {endpoint}')
    
    return None


@app.get('/health')
def health():
    # Unauthenticated liveness probe (no rig data leaked).
    return jsonify({'ok': True, 'service': 'cat-bridge'})


@app.get('/info')
def info():
    """Bridge configuration and connection info (requires auth)."""
    err = _gate()
    if err:
        return err
    
    drv = manager.driver()
    rig_info = None
    if drv is not None:
        try:
            st = drv.state()
            rig_info = {
                'connected': bool(st.connected),
                'rig_id': st.rig_id,
                'port': getattr(drv, 'port', None),
                'baud': getattr(drv, 'baud', None),
            }
        except Exception as exc:
            rig_info = {'error': str(exc)}
    
    return jsonify({
        'bridge': {
            'version': '1.0',
            'http_host': HTTP_HOST,
            'http_port': HTTP_PORT,
            'rigctld_enabled': RIGCTLD_ENABLE,
            'rigctld_host': RIGCTLD_HOST,
            'rigctld_port': RIGCTLD_PORT,
            'tx_lock': BRIDGE_TX_LOCK,
            'debug': BRIDGE_DEBUG,
        },
        'allowlist': {
            'spec': ALLOWLIST.spec,
            'source': ALLOWLIST.source,
        },
        'rig': rig_info,
        'connections': _get_connection_stats(),
        'available_ports': list_serial_ports(),
    })


@app.get('/ports')
def ports():
    err = _gate()
    if err:
        return err
    return jsonify({'ports': list_serial_ports()})


@app.get('/status')
def status():
    err = _gate()
    if err:
        return err
    return jsonify(manager.status())


@app.post('/connect')
def connect():
    err = _gate()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    remote = request.remote_addr or 'unknown'
    rig_id = payload.get('rig_id', 'unknown')
    port = payload.get('port', 'unknown')
    
    print(f'[cat-bridge] RIG CONNECT request from {remote}: rig={rig_id} port={port}')
    
    try:
        result = manager.connect(payload)
        print(f'[cat-bridge] RIG CONNECTED: {rig_id} on {port} @ {result.get("baud")} baud')
        return jsonify(result)
    except ValueError as exc:
        print(f'[cat-bridge] RIG CONNECT FAILED (validation): {exc}')
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        print(f'[cat-bridge] RIG CONNECT FAILED (error): {exc}')
        return jsonify({'error': f'connect failed: {exc}'}), 502


@app.post('/disconnect')
def disconnect():
    err = _gate()
    if err:
        return err
    return jsonify(manager.disconnect())


@app.post('/rpc')
def rpc():
    err = _gate()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    method = str(payload.get('method') or '').strip()
    args = payload.get('args') or []
    if not isinstance(args, list):
        return jsonify({'error': 'args must be a list'}), 400
    try:
        return jsonify(manager.rpc(method, args))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({'error': str(exc)}), 409
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


@app.get('/stream')
def stream():
    err = _gate()
    if err:
        return err

    q = manager.subscribe()

    def gen():
        # Prime the client with the current snapshot.
        snap = manager.status()
        yield f'data: {json.dumps(snap)}\n\n'
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    yield f'data: {json.dumps(event)}\n\n'
                except queue.Empty:
                    yield ': keepalive\n\n'
        finally:
            manager.unsubscribe(q)

    resp = Response(gen(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Connection'] = 'keep-alive'
    return resp


# --- rigctld relay ----------------------------------------------------------
_relay = None


def _start_rigctld():
    global _relay
    if not RIGCTLD_ENABLE:
        print('[cat-bridge] rigctld relay disabled via RIGCTLD_ENABLE=0')
        return
    _relay = RigctldRelay(
        manager.running_driver,
        host=RIGCTLD_HOST,
        port=RIGCTLD_PORT,
        is_allowed=ALLOWLIST.is_allowed,
        tx_locked=BRIDGE_TX_LOCK,
        on_event=lambda name, payload: manager.broadcast(
            {'type': 'rigctld', 'event': name, **payload}
        ),
        debug=RIGCTLD_DEBUG,
    )
    _relay.start()


def main() -> None:
    import socket
    
    # Get local IP addresses for display
    hostname = socket.gethostname()
    try:
        local_ips = socket.gethostbyname_ex(hostname)[2]
    except Exception:
        local_ips = ['unknown']
    
    print('')
    print('=' * 70)
    print('  CAT BRIDGE - Host-side serial bridge for Intercept')
    print('=' * 70)
    print('')
    print('CONFIGURATION:')
    print(f'  Host machine:     {hostname}')
    print(f'  Local IPs:        {", ".join(local_ips)}')
    print(f'  HTTP API:         http://{HTTP_HOST}:{HTTP_PORT}')
    print(f'  Token:            {TOKEN[:8]}...')
    print(f'  Debug mode:       {"ON" if BRIDGE_DEBUG else "OFF"}')
    print('')
    print('ALLOWLIST:')
    print(f'  Source:           {ALLOWLIST.source}')
    print(f'  Spec:             {ALLOWLIST.spec}')
    print('')
    print('RIGCTLD RELAY:')
    if RIGCTLD_ENABLE:
        print(f'  Status:           ENABLED on {RIGCTLD_HOST}:{RIGCTLD_PORT}')
        print(f'  TX Lock:          {"ON (PTT blocked)" if BRIDGE_TX_LOCK else "OFF (PTT allowed)"}')
        print(f'  Debug:            {"ON" if RIGCTLD_DEBUG else "OFF"}')
    else:
        print('  Status:           DISABLED')
    print('')
    print('SERIAL PORTS DETECTED:')
    ports = list_serial_ports()
    if ports:
        for p in ports:
            print(f'  - {p["device"]}: {p["description"]}')
    else:
        print('  (none)')
    print('')
    print('DOCKER CONTAINER SHOULD USE:')
    print(f'  CAT_BRIDGE_URL=http://host.docker.internal:{HTTP_PORT}')
    print(f'  CAT_BRIDGE_TOKEN={TOKEN}')
    print('')
    print('=' * 70)
    print('Waiting for connections...')
    print('=' * 70)
    print('')
    
    _start_rigctld()
    # threaded=True so SSE streams + concurrent requests + rigctld coexist.
    app.run(host=HTTP_HOST, port=HTTP_PORT, threaded=True, use_reloader=False)


if __name__ == '__main__':
    main()
