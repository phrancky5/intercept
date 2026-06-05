"""Client glue for the optional host-side CAT bridge.

When ``CAT_BRIDGE_URL`` is set, intercept's ``/cat/*`` routes drive the
radio through a remote :mod:`cat_bridge` process instead of opening a
local serial port. :class:`RemoteDriverProxy` mimics the
:class:`~utils.cat.base.RigDriver` surface the routes already call, so the
existing handlers (and the Supervisor) keep working unchanged.

This module is imported lazily by :mod:`routes.cat` and only used when the
bridge is configured; with ``CAT_BRIDGE_URL`` unset the base app behaves
exactly as before.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Callable, Optional

import requests

from utils.cat.base import RigState

logger = logging.getLogger('intercept.cat.bridge')

_HTTP_TIMEOUT = 6  # seconds for normal request/response calls


def bridge_url() -> Optional[str]:
    """Return the configured bridge base URL (no trailing slash), or None."""
    raw = (os.environ.get('CAT_BRIDGE_URL') or '').strip()
    return raw.rstrip('/') if raw else None


def bridge_token() -> str:
    return (os.environ.get('CAT_BRIDGE_TOKEN') or '').strip()


def bridge_enabled() -> bool:
    return bridge_url() is not None


def _auth_headers() -> dict[str, str]:
    tok = bridge_token()
    return {'Authorization': f'Bearer {tok}'} if tok else {}


def fetch_ports() -> dict[str, Any]:
    """Proxy ``GET /ports`` to the bridge. Raises on transport failure."""
    base = bridge_url()
    if not base:
        raise RuntimeError('CAT bridge not configured')
    resp = requests.get(f'{base}/ports', headers=_auth_headers(),
                        timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


class RemoteDriverProxy:
    """Driver-shaped facade over a remote :mod:`cat_bridge` process.

    Construction performs the remote ``POST /connect`` and starts a
    background thread that mirrors the bridge's SSE feed onto the local
    CAT SSE queue via ``on_event``.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        connect_payload: dict[str, Any],
        *,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._base = base_url.rstrip('/')
        self._token = token
        self._on_event = on_event
        self.polling_enabled = True
        self.rig_id = str(connect_payload.get('rig_id') or '')
        self._alive = False
        self._sse_stop = threading.Event()
        self._sse_thread: Optional[threading.Thread] = None

        # Perform the remote connect synchronously so failures surface to
        # the caller (mirrors a local driver.start() raising).
        resp = requests.post(
            f'{self._base}/connect',
            headers=self._headers(),
            json=connect_payload,
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code >= 400:
            detail = self._error_detail(resp)
            raise RuntimeError(detail)
        self._alive = True
        self._start_sse()

    # -- helpers -------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self._token}'} if self._token else {}

    @staticmethod
    def _error_detail(resp: 'requests.Response') -> str:
        try:
            return resp.json().get('error') or f'HTTP {resp.status_code}'
        except Exception:
            return f'HTTP {resp.status_code}'

    def _rpc(self, method: str, args: list) -> Any:
        resp = requests.post(
            f'{self._base}/rpc',
            headers=self._headers(),
            json={'method': method, 'args': args},
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise RuntimeError(self._error_detail(resp))
        data = resp.json()
        return data.get('result')

    # -- RigDriver-shaped surface -------------------------------------------
    def is_running(self) -> bool:
        return self._alive

    def state(self) -> RigState:
        try:
            resp = requests.get(f'{self._base}/status', headers=self._headers(),
                                timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            st = data.get('state') or {}
            # RigState.to_dict() (asdict) round-trips back through the ctor.
            return RigState(**st)
        except Exception as exc:
            logger.debug('bridge state() failed: %s', exc)
            return RigState(rig_id=self.rig_id, connected=False)

    def stop(self) -> None:
        self._alive = False
        self._sse_stop.set()
        try:
            requests.post(f'{self._base}/disconnect', headers=self._headers(),
                         timeout=_HTTP_TIMEOUT)
        except Exception as exc:
            logger.debug('bridge disconnect failed: %s', exc)

    def set_polling(self, enabled: bool) -> None:
        self.polling_enabled = bool(enabled)
        try:
            self._rpc('set_polling', [bool(enabled)])
        except Exception as exc:
            logger.debug('bridge set_polling failed: %s', exc)

    def request_status(self) -> None:
        self._rpc('request_status', [])

    # Control verbs — each forwards to the bridge's generic /rpc.
    def set_vfo(self, which: str, hz: int) -> None:
        self._rpc('set_vfo', [which, hz])

    def set_mode(self, mode: str) -> None:
        self._rpc('set_mode', [mode])

    def select_vfo(self, which: str) -> None:
        self._rpc('select_vfo', [which])

    def set_split(self, on: bool) -> None:
        self._rpc('set_split', [bool(on)])

    def set_rit(self, on: bool) -> None:
        self._rpc('set_rit', [bool(on)])

    def clear_rit(self) -> None:
        self._rpc('clear_rit', [])

    def ptt(self, tx: bool) -> None:
        self._rpc('ptt', [bool(tx)])

    def set_agc(self, value: int) -> None:
        self._rpc('set_agc', [value])

    def set_filter(self, slot: int) -> None:
        self._rpc('set_filter', [slot])

    def set_noise_blanker(self, on: bool) -> None:
        self._rpc('set_noise_blanker', [bool(on)])

    def set_attenuator(self, step: int) -> None:
        self._rpc('set_attenuator', [step])

    def set_af_gain(self, value: int) -> None:
        self._rpc('set_af_gain', [value])

    def set_rf_gain(self, value: int) -> None:
        self._rpc('set_rf_gain', [value])

    def set_squelch(self, value: int) -> None:
        self._rpc('set_squelch', [value])

    def set_keyer_speed(self, wpm: int) -> None:
        self._rpc('set_keyer_speed', [wpm])

    def set_power(self, watts: int) -> None:
        self._rpc('set_power', [watts])

    def vfo_step(self, direction: str) -> None:
        self._rpc('vfo_step', [direction])

    def send_raw(self, cmd: str) -> Optional[str]:
        return self._rpc('send_raw', [cmd])

    # -- SSE mirror ----------------------------------------------------------
    def _start_sse(self) -> None:
        self._sse_thread = threading.Thread(
            target=self._sse_loop, name='cat-bridge-sse', daemon=True,
        )
        self._sse_thread.start()

    def _sse_loop(self) -> None:
        url = f'{self._base}/stream'
        params = {'token': self._token} if self._token else None
        while not self._sse_stop.is_set():
            try:
                with requests.get(url, headers=self._headers(), params=params,
                                  stream=True, timeout=(5, None)) as resp:
                    if resp.status_code >= 400:
                        logger.warning('bridge /stream HTTP %s', resp.status_code)
                        break
                    for raw in resp.iter_lines(decode_unicode=True):
                        if self._sse_stop.is_set():
                            break
                        if not raw or not raw.startswith('data:'):
                            continue
                        payload = raw[len('data:'):].strip()
                        if not payload:
                            continue
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        self._handle_event(event)
            except Exception as exc:
                if self._sse_stop.is_set():
                    break
                logger.debug('bridge SSE reconnect after error: %s', exc)
                self._sse_stop.wait(2.0)

    def _handle_event(self, event: dict) -> None:
        etype = event.get('type')
        # Initial snapshot frame from /stream has no 'type' but carries
        # {connected, state}; surface it as a state event.
        if etype is None and 'state' in event:
            event = {'type': 'state', 'state': event['state']}
            etype = 'state'
        if etype == 'lifecycle' and event.get('event') == 'disconnected':
            self._alive = False
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass
