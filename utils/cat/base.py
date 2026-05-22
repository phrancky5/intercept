"""Vendor-neutral CAT driver abstraction.

A :class:`RigDriver` owns one transceiver over serial. It serialises all IO
on a daemon thread, publishes a :class:`RigState` snapshot through the
``on_status`` callback, and exposes a high-level vendor-agnostic command
API.

Concrete drivers (e.g. :class:`utils.cat.kenwood_ts850.KenwoodTS850Driver`)
translate this API into the wire protocol of a specific manufacturer.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class RigState:
    """Snapshot of a transceiver's state.

    Fields are deliberately a superset across vendors; drivers populate
    whichever subset they support and leave the rest at sentinel values
    (``-1`` for unknown integers, defaults for booleans).
    """

    connected: bool = False
    rig_id: str = ''
    vfo_a_hz: int = 0
    vfo_b_hz: int = 0
    active_vfo: str = 'A'         # 'A' or 'B'
    tx_vfo: str = 'A'
    split: bool = False
    mode: str = 'USB'
    rit_hz: int = 0
    rit_on: bool = False
    xit_on: bool = False
    s_meter: int = 0
    ptt: bool = False
    agc: int = -1
    filter_slot: int = -1
    memory_ch: int = -1
    in_memory: bool = False
    nb: bool = False
    attenuator: int = 0
    af_gain: int = -1
    rf_gain: int = -1
    squelch: int = -1
    keyer_wpm: int = -1
    power_w: int = -1
    last_update: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RigDriver(ABC):
    """Abstract base class for transceiver drivers."""

    #: Stable identifier matching the rig's :class:`RigDescriptor`.
    rig_id: str = ''

    def __init__(
        self,
        port: str,
        baud: int,
        *,
        on_status: Callable[[RigState], None] | None = None,
        on_io: Callable[[str, str], None] | None = None,
        assert_rts: bool = False,
        assert_dtr: bool = False,
        data_bits: int = 8,
        stop_bits: int = 1,
        parity: str = 'N',
    ) -> None:
        self.port = port
        self.baud = baud
        self.on_status = on_status
        self.on_io = on_io
        self.assert_rts = assert_rts
        self.assert_dtr = assert_dtr
        self.data_bits = int(data_bits)
        self.stop_bits = int(stop_bits)
        self.parity = (parity or 'N').upper()
        self.polling_enabled = True

    def set_polling(self, enabled: bool) -> None:
        """Toggle the driver's periodic safety-net poll.

        Drivers that rely solely on push events (e.g. Kenwood Auto-Info)
        can stay healthy with polling off; turning it back on resumes
        the per-driver default cadence.
        """
        self.polling_enabled = bool(enabled)

    # --- Lifecycle ----------------------------------------------------------
    @abstractmethod
    def start(self) -> None:
        """Open the serial port and begin the worker thread."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the worker thread and close the serial port."""

    @abstractmethod
    def is_running(self) -> bool:
        """Return ``True`` while the worker thread is alive."""

    @abstractmethod
    def state(self) -> RigState:
        """Return a copy of the latest :class:`RigState`."""

    # --- Core CAT commands --------------------------------------------------
    @abstractmethod
    def set_vfo(self, which: str, hz: int) -> None:
        """Set VFO A or B to a frequency in Hz."""

    @abstractmethod
    def set_mode(self, mode: str) -> None:
        """Set operating mode (LSB/USB/CW/AM/FM/...)."""

    @abstractmethod
    def select_vfo(self, which: str) -> None:
        """Select VFO A or B as the active receive VFO."""

    @abstractmethod
    def set_split(self, on: bool) -> None:
        """Enable or disable split TX/RX."""

    @abstractmethod
    def set_rit(self, on: bool) -> None:
        """Enable or disable RIT."""

    @abstractmethod
    def clear_rit(self) -> None:
        """Clear RIT offset."""

    @abstractmethod
    def ptt(self, tx: bool) -> None:
        """Key (``True``) or unkey (``False``) the transmitter."""

    # --- Optional extended controls ----------------------------------------
    # Drivers raise :class:`NotImplementedError` if a capability is not
    # supported. The blueprint guards calls using
    # :attr:`RigDescriptor.capabilities` so unsupported requests return 400
    # without hitting the wire.

    def set_agc(self, value: int) -> None:
        raise NotImplementedError

    def set_filter(self, slot: int) -> None:
        raise NotImplementedError

    def set_noise_blanker(self, on: bool) -> None:
        raise NotImplementedError

    def set_attenuator(self, step: int) -> None:
        raise NotImplementedError

    def set_af_gain(self, value: int) -> None:
        raise NotImplementedError

    def set_rf_gain(self, value: int) -> None:
        raise NotImplementedError

    def set_squelch(self, value: int) -> None:
        raise NotImplementedError

    def set_keyer_speed(self, wpm: int) -> None:
        raise NotImplementedError

    def set_power(self, watts: int) -> None:
        raise NotImplementedError

    def vfo_step(self, direction: str) -> None:
        raise NotImplementedError

    def set_memory_channel(self, ch: int) -> None:
        raise NotImplementedError

    def select_memory(self) -> None:
        raise NotImplementedError

    def request_status(self) -> None:
        """Force a fresh poll of all state. Drivers should override."""

    def send_raw(self, cmd: str) -> str | None:
        """Send a raw vendor-specific command. Returns response if any."""
        raise NotImplementedError
