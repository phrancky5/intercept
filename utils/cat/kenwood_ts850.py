"""Kenwood TS-850S CAT control driver.

Protocol: ASCII commands terminated with ';'. Default serial settings 4800 8N2.
The TS-850 is sensitive to stop bits — they must be 2.

Reference: Kenwood TS-850S Operating Manual, "Computer Control" section.

This driver is also a reasonable starting point for protocol-compatible
later Kenwoods (TS-590S, TS-2000, TS-480). Quirks differ — register
distinct driver subclasses if needed rather than overloading this one.

Threading: the driver owns a single daemon thread that serialises all
serial I/O. Public methods enqueue commands and (optionally) wait for a
response. Status updates parsed from the radio are published through the
``on_status`` callback.
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

logger = logging.getLogger('intercept.cat.kenwood_ts850')

try:
    import serial  # type: ignore
    _HAS_SERIAL = True
except ImportError:  # pragma: no cover — requirements include pyserial
    serial = None  # type: ignore
    _HAS_SERIAL = False


# TS-850 numeric mode codes.
MODE_CODES = {
    1: 'LSB', 2: 'USB', 3: 'CW', 4: 'FM',
    5: 'AM', 6: 'FSK', 7: 'CW-R', 9: 'FSK-R',
}
MODE_REVERSE = {v: k for k, v in MODE_CODES.items()}


class KenwoodTS850Driver(RigDriver):
    """Serial CAT driver for the Kenwood TS-850S."""

    rig_id = 'kenwood_ts850'

    DEFAULT_BAUD = 4800
    SUPPORTED_BAUDS = (1200, 2400, 4800, 9600)
    # AI mode pushes IF; on every dial/control change, so the safety-net
    # poll only needs to ping things AI does not push (S-meter). At
    # 4800 baud this keeps line load well below 5 %.
    POLL_INTERVAL = 1.5
    POLL_INTERVAL_NO_AI = 0.5
    READ_TIMEOUT = 0.2

    # The TS-850 over an FTDI cable is famously sensitive to back-to-back
    # writes — frames sent <30 ms apart are silently swallowed or merged
    # by the radio's UART. Insert a short gap between consecutive CAT
    # writes (50 ms is what `hamlib` settled on for the same family).
    INTER_COMMAND_DELAY_S = 0.05
    # After the port is opened (and RTS/DTR are set) the FTDI-USB stack
    # takes a moment to settle; sending AI1;/IF; immediately races the
    # radio's own input loop and misses the first reply. A quarter-second
    # of quiet here makes "first connect" reliable.
    POST_OPEN_SETTLE_S = 0.25

    _IF_RE = re.compile(r'^IF(\d{11})(.*);$')

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
        stop_bits: int = 2,
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
        # RTS/DTR default OFF. Asserting RTS on common FTDI cables blocks
        # CAT comms with the TS-850. The radio's CTS hold needed for TX is
        # supposed to come from a hardware jumper on DIN pins 4-5, not the
        # PC. Callers that need these lines driven can opt-in.
        try:
            self._ser.rts = bool(self.assert_rts)
            self._ser.dtr = bool(self.assert_dtr)
        except Exception as exc:  # pragma: no cover — vendor quirk
            logger.debug('RTS/DTR set failed: %s', exc)

        # Let the FTDI driver settle before the first frame leaves the
        # host — see POST_OPEN_SETTLE_S.
        time.sleep(self.POST_OPEN_SETTLE_S)

        self._stop.clear()
        with self._state_lock:
            self._state.connected = True
        self._thread = threading.Thread(
            target=self._run, name='cat-ts850', daemon=True
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
            self._send(f'FA{int(hz):011d};')
        elif which.upper() == 'B':
            self._send(f'FB{int(hz):011d};')
        else:
            raise ValueError(f'Unknown VFO {which!r}')

    def set_mode(self, mode: str) -> None:
        code = MODE_REVERSE.get(mode.upper())
        if code is None:
            raise ValueError(f'Unknown mode {mode}')
        self._send(f'MD{code};')

    def select_vfo(self, which: str) -> None:
        if which.upper() == 'A':
            self._send('FR0;')
        elif which.upper() == 'B':
            self._send('FR1;')
        else:
            raise ValueError(f'Unknown VFO {which!r}')

    def set_split(self, on: bool) -> None:
        self._send('FT1;' if on else 'FT0;')

    def set_rit(self, on: bool) -> None:
        self._send('RT1;' if on else 'RT0;')

    def clear_rit(self) -> None:
        self._send('RC;')

    def ptt(self, tx: bool) -> None:
        self._send('TX;' if tx else 'RX;')

    # ---------- extended controls -------------------------------------------
    def set_agc(self, value: int) -> None:
        v = max(0, min(2, int(value)))
        self._send(f'GT{v:03d};')

    def set_filter(self, slot: int) -> None:
        v = max(0, min(99, int(slot)))
        self._send(f'FL{v:02d};')

    def set_noise_blanker(self, on: bool) -> None:
        self._send('NB1;' if on else 'NB0;')

    def set_attenuator(self, step: int) -> None:
        v = max(0, min(3, int(step)))
        self._send(f'RA{v:02d};')

    def set_af_gain(self, value: int) -> None:
        v = max(0, min(255, int(value)))
        self._send(f'AG0{v:03d};')

    def set_rf_gain(self, value: int) -> None:
        v = max(0, min(255, int(value)))
        self._send(f'RG{v:03d};')

    def set_squelch(self, value: int) -> None:
        v = max(0, min(255, int(value)))
        self._send(f'SQ0{v:03d};')

    def set_keyer_speed(self, wpm: int) -> None:
        v = max(10, min(60, int(wpm)))
        self._send(f'KS{v:03d};')

    def set_power(self, watts: int) -> None:
        v = max(0, min(100, int(watts)))
        self._send(f'PC{v:03d};')

    def vfo_step(self, direction: str) -> None:
        if direction.lower() == 'up':
            self._send('UP;')
        elif direction.lower() == 'down':
            self._send('DN;')
        else:
            raise ValueError(f'Unknown step direction {direction!r}')

    def set_memory_channel(self, ch: int) -> None:
        v = max(0, min(99, int(ch)))
        self._send(f'MC{v:03d};')

    def select_memory(self) -> None:
        self._send('FR2;')

    def request_status(self) -> None:
        """Force a fresh poll of the most useful state."""
        for cmd in (
            b'IF;', b'FA;', b'FB;', b'FR;', b'FT;', b'MD;',
            b'RT;', b'XT;', b'SM;',
            b'GT;', b'FL;', b'NB;', b'RA;',
            b'AG0;', b'RG;', b'SQ0;',
            b'KS;', b'PC;', b'MC;',
        ):
            self._tx_q.put(cmd)

    def send_raw(self, cmd: str) -> Optional[str]:
        """Send an arbitrary CAT command. If it looks like a query the
        response (up to 0.5 s) is returned."""
        if not cmd.endswith(';'):
            cmd += ';'
        if len(cmd) <= 3 and cmd[0:2].isalpha():
            return self._send_query(cmd[:2], cmd.encode('ascii'), timeout=0.5)
        self._send(cmd)
        return None

    # ---------- internals ----------------------------------------------------
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

    def _run(self) -> None:
        while not self._stop.is_set():
            # 1) drain TX queue. The TS-850 cannot keep up with
            # back-to-back writes, so we space them by
            # INTER_COMMAND_DELAY_S and flush each frame to push it
            # through the USB-serial bridge immediately.
            try:
                while True:
                    data = self._tx_q.get_nowait()
                    if self._ser:
                        self._ser.write(data)
                        try:
                            self._ser.flush()
                        except Exception:
                            pass
                        self._emit_io('tx', data)
                    self._tx_q.task_done()
                    if not self._tx_q.empty():
                        # Only sleep when another frame is pending —
                        # avoids adding latency on lone commands.
                        time.sleep(self.INTER_COMMAND_DELAY_S)
            except queue.Empty:
                pass

            # 2) read bytes
            ser = self._ser
            if ser is None:
                time.sleep(0.1)
                continue
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
            except Exception as exc:
                logger.warning('Serial read error: %s', exc)
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
                    self._tx_q.put(b'SM;')

    # ---------- parser ------------------------------------------------------
    def _parse(self, frame: str) -> None:  # noqa: C901 — clearer as one switch
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
            elif prefix == 'MD' and len(frame) >= 4:
                code = int(frame[2])
                with self._state_lock:
                    self._state.mode = MODE_CODES.get(code, self._state.mode)
                    self._state.last_update = time.time()
                self._emit()
            elif prefix == 'SM' and len(frame) >= 6:
                try:
                    smv = int(frame[2:].rstrip(';'))
                    with self._state_lock:
                        self._state.s_meter = smv
                        self._state.last_update = time.time()
                    self._emit()
                except ValueError:
                    pass
            elif prefix == 'FR' and len(frame) >= 4:
                with self._state_lock:
                    self._state.active_vfo = 'A' if frame[2] == '0' else 'B'
                    self._state.in_memory = frame[2] == '2'
                self._emit()
            elif prefix == 'FT' and len(frame) >= 4:
                txv = 'A' if frame[2] == '0' else 'B'
                with self._state_lock:
                    self._state.tx_vfo = txv
                    self._state.split = (txv != self._state.active_vfo)
                self._emit()
            elif prefix == 'RT' and len(frame) >= 4:
                with self._state_lock:
                    self._state.rit_on = (frame[2] == '1')
                self._emit()
            elif prefix == 'XT' and len(frame) >= 4:
                with self._state_lock:
                    self._state.xit_on = (frame[2] == '1')
                self._emit()
            elif prefix == 'GT' and len(frame) >= 6:
                with self._state_lock:
                    self._state.agc = int(frame[2:5])
                self._emit()
            elif prefix == 'FL' and len(frame) >= 5:
                with self._state_lock:
                    self._state.filter_slot = int(frame[2:4])
                self._emit()
            elif prefix == 'MC' and len(frame) >= 6:
                with self._state_lock:
                    self._state.memory_ch = int(frame[2:5])
                self._emit()
            elif prefix == 'NB' and len(frame) >= 4:
                with self._state_lock:
                    self._state.nb = (frame[2] == '1')
                self._emit()
            elif prefix == 'RA' and len(frame) >= 5:
                with self._state_lock:
                    self._state.attenuator = int(frame[2:4])
                self._emit()
            elif prefix == 'AG' and len(frame) >= 7:
                with self._state_lock:
                    self._state.af_gain = int(frame[3:6])
                self._emit()
            elif prefix == 'RG' and len(frame) >= 6:
                with self._state_lock:
                    self._state.rf_gain = int(frame[2:5])
                self._emit()
            elif prefix == 'SQ' and len(frame) >= 7:
                with self._state_lock:
                    self._state.squelch = int(frame[3:6])
                self._emit()
            elif prefix == 'KS' and len(frame) >= 6:
                with self._state_lock:
                    self._state.keyer_wpm = int(frame[2:5])
                self._emit()
            elif prefix == 'PC' and len(frame) >= 6:
                with self._state_lock:
                    self._state.power_w = int(frame[2:5])
                self._emit()
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

        # IF; tail layout (selected fields):
        #   tail[0:4]  step (unused)
        #   tail[4:9]  RIT offset (signed 5-digit)
        #   tail[9]    RIT on/off
        #   tail[10]   XIT on/off
        #   tail[15]   TX flag
        #   tail[16]   mode (1..9)
        #   tail[17]   RX VFO (0=A, 1=B, 2=mem)
        #   tail[19]   split flag
        rit_hz = 0
        rit_on = False
        xit_on = False
        ptt = False
        mode_str: Optional[str] = None
        rx_vfo: Optional[str] = None
        split = False
        try:
            if len(tail) >= 9:
                rit_hz = int(tail[4:9])
            if len(tail) >= 10:
                rit_on = tail[9] == '1'
            if len(tail) >= 11:
                xit_on = tail[10] == '1'
            if len(tail) >= 16:
                ptt = tail[15] == '1'
            if len(tail) >= 17:
                code = int(tail[16]) if tail[16].isdigit() else 0
                mode_str = MODE_CODES.get(code)
            if len(tail) >= 18:
                rx_vfo = 'A' if tail[17] == '0' else ('B' if tail[17] == '1' else 'M')
            if len(tail) >= 20:
                split = tail[19] == '1'
        except (ValueError, IndexError):
            pass

        with self._state_lock:
            if rx_vfo == 'A':
                self._state.vfo_a_hz = freq
                self._state.active_vfo = 'A'
                self._state.in_memory = False
            elif rx_vfo == 'B':
                self._state.vfo_b_hz = freq
                self._state.active_vfo = 'B'
                self._state.in_memory = False
            else:
                if self._state.active_vfo == 'A':
                    self._state.vfo_a_hz = freq
                else:
                    self._state.vfo_b_hz = freq
                self._state.in_memory = rx_vfo == 'M'
            self._state.rit_hz = rit_hz
            self._state.rit_on = rit_on
            self._state.xit_on = xit_on
            self._state.ptt = ptt
            self._state.split = split
            if mode_str:
                self._state.mode = mode_str
            self._state.last_update = time.time()
        self._emit()

        # If we never saw VFO B yet, ask for it explicitly.
        if rx_vfo == 'A' and self._state.vfo_b_hz == 0:
            self._tx_q.put(b'FB;')

    def _parse_fx(self, frame: str, which: str) -> None:
        if len(frame) < 14:
            return
        try:
            hz = int(frame[2:13])
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
        except Exception as exc:  # pragma: no cover — defensive
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
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug('on_io callback error: %s', exc)
