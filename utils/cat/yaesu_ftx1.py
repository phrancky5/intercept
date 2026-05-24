"""Yaesu FTX-1 Optima CAT control driver.

Protocol: Yaesu CAT v2. ASCII commands terminated with ';'. Default
serial settings 38400 8N1. Frequencies are zero-padded to 9 digits,
mode codes are two-byte ``MDxMy`` selectors where ``x`` is the channel
(``0`` = Main, ``1`` = Sub) and ``y`` is the mode digit/letter.

Reference: docs/cat/FTX1_REFERENCE.md and the FTX-1 CAT operation
manual. Where the FTX-1 differs from Kenwood ASCII (mode encoding,
PTT verb ``TX1;``/``TX0;``, split toggle ``ST1;``/``ST0;``) the
quirks are noted inline.

Threading mirrors :class:`utils.cat.kenwood_ts850.KenwoodTS850Driver`:
a single daemon thread serialises all serial I/O via a TX queue, and
parsed status frames are pushed through ``on_status``.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import asdict
from typing import Callable, Optional

from utils.cat.base import RigDriver, RigState

logger = logging.getLogger('intercept.cat.yaesu_ftx1')

try:
    import serial  # type: ignore
    _HAS_SERIAL = True
except ImportError:  # pragma: no cover — requirements include pyserial
    serial = None  # type: ignore
    _HAS_SERIAL = False


# FTX-1 mode codes (MD0M<x>;). Drawn from the FTX-1 CAT reference.
MODE_CODES = {
    '1': 'LSB', '2': 'USB', '3': 'CW',  '4': 'FM',
    '5': 'AM',  '6': 'FSK', '7': 'CW-R',
    '8': 'DATA-L', '9': 'FSK-R',
    'B': 'DATA-FM', 'C': 'FM-N', 'D': 'DATA-U', 'E': 'AM-N',
}
# Reverse map prefers the most common encoding for ambiguous labels.
MODE_REVERSE = {
    'LSB': '1', 'USB': '2', 'CW': '3', 'FM': '4', 'AM': '5',
    'FSK': '6', 'CW-R': '7', 'DATA-L': '8', 'FSK-R': '9',
    'DATA-FM': 'B', 'FM-N': 'C', 'DATA-U': 'D', 'AM-N': 'E',
}


class YaesuFTX1Driver(RigDriver):
    """Serial CAT driver for the Yaesu FTX-1 Optima."""

    rig_id = 'yaesu_ftx1'

    DEFAULT_BAUD = 38400
    SUPPORTED_BAUDS = (4800, 9600, 19200, 38400, 115200)

    # The FTX-1 pushes status via AI1 (auto-info) similar to recent
    # Yaesus. With AI on we still safety-poll the S-meter; with AI off
    # we poll IF; and SM0; more aggressively.
    POLL_INTERVAL = 1.5
    POLL_INTERVAL_NO_AI = 0.5
    READ_TIMEOUT = 0.2

    # Modern Yaesus over genuine USB are far happier than the TS-850
    # with back-to-back writes, but cheap CH340/CP2102 cables still
    # benefit from a small inter-command gap.
    INTER_COMMAND_DELAY_S = 0.02
    POST_OPEN_SETTLE_S = 0.15

    # IF; payload: 9-digit freq, then trailing fields. Yaesu IF reply
    # length is 28 chars + ';' on the FTX-1 (vs 38 on the TS-850).
    _IF_RE = re.compile(r'^IF(\d{9})(.*);$')

    def __init__(
        self,
        port: str,
        baud: int = DEFAULT_BAUD,
        *,
        on_status: Optional[Callable[[RigState], None]] = None,
        on_io: Optional[Callable[[str, str], None]] = None,
        use_auto_info: bool = True,
        assert_rts: bool = False,
        assert_dtr: bool = False,
        data_bits: int = 8,
        stop_bits: int = 1,
        parity: str = 'N',
    ) -> None:
        if not _HAS_SERIAL:
            raise RuntimeError('pyserial not installed')
        if baud not in self.SUPPORTED_BAUDS:
            raise ValueError(
                f'Unsupported baud {baud}; choose {self.SUPPORTED_BAUDS}'
            )
        super().__init__(
            port=port,
            baud=baud,
            on_status=on_status,
            on_io=on_io,
            assert_rts=assert_rts,
            assert_dtr=assert_dtr,
            data_bits=data_bits,
            stop_bits=stop_bits,
            parity=parity,
        )
        self.use_auto_info = use_auto_info

        self._ser: Optional['serial.Serial'] = None  # type: ignore[name-defined]
        self._tx_q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = RigState(rig_id=self.rig_id)
        self._state_lock = threading.Lock()
        self._rx_buf = b''
        self._pending: dict[str, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._last_poll = 0.0

    # ---------- lifecycle ----------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ser = serial.Serial(  # type: ignore[union-attr]
            self.port,
            baudrate=self.baud,
            bytesize=self.data_bits,
            parity=self.parity,
            stopbits=self.stop_bits,
            timeout=self.READ_TIMEOUT,
            rtscts=False,
            dsrdtr=False,
        )
        # FTX-1 ignores RTS/DTR for CAT; default both off and let the
        # operator override per cable.
        try:
            self._ser.rts = bool(self.assert_rts)
            self._ser.dtr = bool(self.assert_dtr)
        except Exception as exc:  # pragma: no cover
            logger.debug('RTS/DTR set failed: %s', exc)

        time.sleep(self.POST_OPEN_SETTLE_S)

        self._stop.clear()
        with self._state_lock:
            self._state.connected = True
        self._thread = threading.Thread(
            target=self._run, name='cat-ftx1', daemon=True
        )
        self._thread.start()

        if self.use_auto_info:
            self._tx_q.put(b'AI1;')
        self._tx_q.put(b'IF;')

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ser and self._ser.is_open:
                try:
                    self._ser.write(b'AI0;')
                    self._ser.flush()
                except Exception:
                    pass
                self._ser.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=1.0)
        with self._state_lock:
            self._state.connected = False

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def state(self) -> RigState:
        with self._state_lock:
            return RigState(**asdict(self._state))

    # ---------- core API ----------------------------------------------------
    def set_vfo(self, which: str, hz: int) -> None:
        if which.upper() == 'A':
            self._send(f'FA{int(hz):09d};')
        elif which.upper() == 'B':
            self._send(f'FB{int(hz):09d};')
        else:
            raise ValueError(f'Unknown VFO {which!r}')

    def set_mode(self, mode: str) -> None:
        code = MODE_REVERSE.get(mode.upper())
        if code is None:
            raise ValueError(f'Unknown mode {mode}')
        # Channel 0 = Main receiver.
        self._send(f'MD0M{code};')

    def select_vfo(self, which: str) -> None:
        # FTX-1 uses VS (VFO swap) and explicit FA/FB queries; there is
        # no direct "FR" register like Kenwood. Closest analogue is the
        # band/main toggle. Emulate by issuing a swap if the request
        # disagrees with current state; otherwise no-op.
        with self._state_lock:
            current = self._state.active_vfo
        target = which.upper()
        if target not in ('A', 'B'):
            raise ValueError(f'Unknown VFO {which!r}')
        if current != target:
            self._send('SV;')

    def set_split(self, on: bool) -> None:
        self._send('ST1;' if on else 'ST0;')

    def set_rit(self, on: bool) -> None:
        # FTX-1 RIT: RT0; / RT1; on the main channel.
        self._send('RT1;' if on else 'RT0;')

    def clear_rit(self) -> None:
        # Set RIT offset to 0 — RC clears Clarifier on Yaesu.
        self._send('RC;')

    def ptt(self, tx: bool) -> None:
        self._send('TX1;' if tx else 'TX0;')

    # ---------- extended controls -------------------------------------------
    def set_power(self, watts: int) -> None:
        v = max(5, min(100, int(watts)))
        self._send(f'PC{v:03d};')

    def set_noise_blanker(self, on: bool) -> None:
        # Yaesu NB takes a channel-prefixed argument: NB0<on>; for main.
        self._send('NB01;' if on else 'NB00;')

    def set_agc(self, value: int) -> None:
        # Yaesu AGC: GT0<x>; where x: 0=slow, 1=mid, 2=fast, 3=off.
        v = max(0, min(3, int(value)))
        self._send(f'GT0{v};')

    def request_status(self) -> None:
        for cmd in (b'IF;', b'FA;', b'FB;', b'MD0;', b'SM0;', b'ST;'):
            self._tx_q.put(cmd)

    def send_raw(self, cmd: str) -> Optional[str]:
        if not cmd.endswith(';'):
            cmd += ';'
        data = cmd.encode('ascii')
        # Queries on Yaesu are 3-char (XX;) or 4-char with a channel
        # (XX0;); treat anything <=4 bytes ending in ';' as a query and
        # wait for the prefix's response.
        if len(cmd) <= 4 and cmd[0:2].isalpha():
            return self._send_query(cmd[:2], data, timeout=0.5)
        self._send(cmd)
        return None

    # ---------- internals ---------------------------------------------------
    def _send(self, cmd: str | bytes) -> None:
        data = cmd.encode('ascii') if isinstance(cmd, str) else cmd
        self._tx_q.put(data)

    def _send_query(
        self, prefix: str, data: bytes, timeout: float = 0.5
    ) -> Optional[str]:
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[prefix] = q
        try:
            self._tx_q.put(data)
            return q.get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            with self._pending_lock:
                self._pending.pop(prefix, None)

    # See KenwoodTS850Driver.SERIAL_FAIL_LIMIT — same Docker/usbipd-win
    # situation applies to any USB-serial radio.
    SERIAL_FAIL_LIMIT = 10

    def _run(self) -> None:
        fail_count = 0
        while not self._stop.is_set():
            # 1) drain TX queue with inter-command pacing + flush.
            try:
                while True:
                    data = self._tx_q.get_nowait()
                    if self._ser:
                        try:
                            self._ser.write(data)
                            try:
                                self._ser.flush()
                            except Exception:
                                pass
                            self._emit_io('tx', data)
                            fail_count = 0
                        except Exception as exc:
                            logger.warning('Serial write error: %s', exc)
                            fail_count += 1
                            if fail_count >= self.SERIAL_FAIL_LIMIT:
                                self._tx_q.task_done()
                                self._fail_link(exc)
                                return
                    self._tx_q.task_done()
                    if not self._tx_q.empty():
                        time.sleep(self.INTER_COMMAND_DELAY_S)
            except queue.Empty:
                pass

            ser = self._ser
            if ser is None:
                time.sleep(0.1)
                continue

            # 2) read bytes
            try:
                try:
                    waiting = ser.in_waiting or 0
                except Exception:
                    waiting = 0
                if isinstance(waiting, int) and waiting > 0:
                    self._rx_buf += ser.read(waiting)
                else:
                    chunk = ser.read(64)
                    if chunk:
                        self._rx_buf += chunk
                fail_count = 0
            except Exception as exc:
                logger.warning('Serial read error: %s', exc)
                fail_count += 1
                if fail_count >= self.SERIAL_FAIL_LIMIT:
                    self._fail_link(exc)
                    return
                time.sleep(0.5)
                continue

            # 3) parse ;-terminated frames
            while b';' in self._rx_buf:
                idx = self._rx_buf.index(b';')
                frame = self._rx_buf[:idx + 1].decode('ascii', errors='ignore')
                self._rx_buf = self._rx_buf[idx + 1:]
                self._emit_io('rx', frame)
                self._parse(frame)

            # 4) periodic poll
            if self.polling_enabled:
                now = time.time()
                interval = self.POLL_INTERVAL if self.use_auto_info else self.POLL_INTERVAL_NO_AI
                if now - self._last_poll > interval:
                    self._last_poll = now
                    if not self.use_auto_info:
                        self._tx_q.put(b'IF;')
                    self._tx_q.put(b'SM0;')

    # ---------- parser ------------------------------------------------------
    def _parse(self, frame: str) -> None:
        if len(frame) < 3:
            return
        prefix = frame[:2]

        with self._pending_lock:
            q = self._pending.pop(prefix, None)
        if q is not None:
            try:
                q.put_nowait(frame)
            except queue.Full:
                pass

        try:
            if prefix == 'IF':
                self._parse_if(frame)
            elif prefix == 'FA':
                self._parse_fx(frame, 'A')
            elif prefix == 'FB':
                self._parse_fx(frame, 'B')
            elif prefix == 'MD' and len(frame) >= 6:
                # MD0M<x>; — character at index 4 is the mode code.
                ch = frame[4]
                with self._state_lock:
                    self._state.mode = MODE_CODES.get(ch, self._state.mode)
                    self._state.last_update = time.time()
                self._emit()
            elif prefix == 'SM' and len(frame) >= 7:
                # SM0<nnn>;  (channel 0, 3-digit value 0..255-ish)
                try:
                    smv = int(frame[3:].rstrip(';'))
                    with self._state_lock:
                        self._state.s_meter = smv
                        self._state.last_update = time.time()
                    self._emit()
                except ValueError:
                    pass
            elif prefix == 'ST' and len(frame) >= 4:
                with self._state_lock:
                    self._state.split = (frame[2] == '1')
                self._emit()
            elif prefix == 'RT' and len(frame) >= 4:
                with self._state_lock:
                    self._state.rit_on = (frame[2] == '1')
                self._emit()
            elif prefix == 'TX' and len(frame) >= 4:
                with self._state_lock:
                    self._state.ptt = (frame[2] == '1')
                self._emit()
            elif prefix == 'PC' and len(frame) >= 6:
                try:
                    with self._state_lock:
                        self._state.power_w = int(frame[2:5])
                    self._emit()
                except ValueError:
                    pass
        except (ValueError, IndexError) as exc:
            logger.debug('Parse error on %r: %s', frame, exc)

    def _parse_if(self, frame: str) -> None:
        m = self._IF_RE.match(frame)
        if not m:
            return
        try:
            freq = int(m.group(1))
        except ValueError:
            return
        tail = m.group(2)
        # Yaesu FTX-1 IF; tail (positions, 0-indexed past the 9 freq digits):
        #   tail[0:4]  Clarifier offset (4 digits)
        #   tail[4]    RX clarifier on/off
        #   tail[5]    TX clarifier on/off
        #   tail[6:8]  Mode (1 char) + reserved
        #   tail[8]    VFO memory channel banking flag
        #   tail[9]    Mode (effective)
        #   tail[10]   RX VFO (0=A,1=B)
        #   tail[11]   Scan (ignored)
        #   tail[12]   Split (0/1)
        # The exact layout varies across Yaesu firmware revisions — only
        # consume the fields we are confident in and leave the rest as-is.
        rit_on = False
        xit_on = False
        mode_str: Optional[str] = None
        rx_vfo: Optional[str] = None
        split = False
        try:
            if len(tail) >= 5:
                rit_on = tail[4] == '1'
            if len(tail) >= 6:
                xit_on = tail[5] == '1'
            if len(tail) >= 10:
                mode_str = MODE_CODES.get(tail[9])
            if len(tail) >= 11:
                rx_vfo = 'A' if tail[10] == '0' else 'B'
            if len(tail) >= 13:
                split = tail[12] == '1'
        except (ValueError, IndexError):
            pass

        with self._state_lock:
            if rx_vfo == 'A':
                self._state.vfo_a_hz = freq
                self._state.active_vfo = 'A'
            elif rx_vfo == 'B':
                self._state.vfo_b_hz = freq
                self._state.active_vfo = 'B'
            else:
                if self._state.active_vfo == 'A':
                    self._state.vfo_a_hz = freq
                else:
                    self._state.vfo_b_hz = freq
            self._state.rit_on = rit_on
            self._state.xit_on = xit_on
            self._state.split = split
            if mode_str:
                self._state.mode = mode_str
            self._state.last_update = time.time()
        self._emit()

        if rx_vfo == 'A' and self._state.vfo_b_hz == 0:
            self._tx_q.put(b'FB;')

    def _parse_fx(self, frame: str, which: str) -> None:
        # Yaesu FA/FB; reply is "FA<9-digit>;"
        if len(frame) < 12:
            return
        try:
            hz = int(frame[2:11])
        except ValueError:
            return
        with self._state_lock:
            if which == 'A':
                self._state.vfo_a_hz = hz
            else:
                self._state.vfo_b_hz = hz
            self._state.last_update = time.time()
        self._emit()

    def _emit(self) -> None:
        if not self.on_status:
            return
        try:
            self.on_status(self.state())
        except Exception as exc:  # pragma: no cover
            logger.debug('on_status callback error: %s', exc)

    def _emit_io(self, direction: str, payload) -> None:
        if not self.on_io:
            return
        try:
            if isinstance(payload, (bytes, bytearray)):
                text = bytes(payload).decode('ascii', errors='replace')
            else:
                text = str(payload)
            self.on_io(direction, text)
        except Exception as exc:  # pragma: no cover
            logger.debug('on_io callback error: %s', exc)

    def _fail_link(self, exc: BaseException) -> None:
        """Mark the serial link as lost after repeated I/O failures.

        See ``KenwoodTS850Driver._fail_link`` for rationale — same
        Docker Desktop + usbipd-win pattern applies here.
        """
        logger.warning('CAT serial link declared dead: %s', exc)
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass
        with self._state_lock:
            self._state.connected = False
        try:
            self._emit_io(
                'sys',
                f'serial link lost ({exc}) — disconnect and reconnect '
                'the rig from the Connection panel',
            )
        except Exception:
            pass
        self._emit()
