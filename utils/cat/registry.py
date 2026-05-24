"""Catalog of supported transceivers.

A :class:`RigDescriptor` carries the user-facing metadata (vendor, model,
display name) plus the default serial parameters and a reference to the
concrete :class:`utils.cat.base.RigDriver` subclass. Rigs whose drivers
have not yet been written carry ``driver_class=None`` — the UI still
lists them with a "driver coming soon" hint so users can see what is on
the roadmap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Type

from utils.cat.base import RigDriver
from utils.cat.kenwood_ts850 import KenwoodTS850Driver
from utils.cat.yaesu_ftx1 import YaesuFTX1Driver


@dataclass(frozen=True)
class RigDescriptor:
    """Static metadata for a transceiver model."""

    rig_id: str
    vendor: str
    model: str
    display_name: str
    default_baud: int
    supported_bauds: tuple[int, ...]
    driver_class: Type[RigDriver] | None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    notes: str = ''
    # Serial framing defaults. Most modern rigs are 8N1, but a number of
    # older Kenwoods (notably the TS-850) require 2 stop bits or the rig
    # will silently ignore commands. Surface these so the UI can preset
    # the right values and users can override per cable / level shifter.
    data_bits: int = 8
    stop_bits: int = 1
    parity: str = 'N'  # 'N', 'E', 'O', 'M', 'S'

    @property
    def implemented(self) -> bool:
        return self.driver_class is not None

    def to_dict(self) -> dict:
        return {
            'rig_id': self.rig_id,
            'vendor': self.vendor,
            'model': self.model,
            'display_name': self.display_name,
            'default_baud': int(self.default_baud),
            'supported_bauds': list(self.supported_bauds),
            'capabilities': sorted(self.capabilities),
            'implemented': self.implemented,
            'notes': self.notes,
            'data_bits': int(self.data_bits),
            'stop_bits': int(self.stop_bits),
            'parity': self.parity,
        }


# Capability tags consumed by both the UI (to grey out unsupported
# controls) and the REST layer (to reject 400 early before hitting the
# wire). Drivers are free to advertise more or fewer caps as they mature.
CAP_VFO = 'vfo'
CAP_MODE = 'mode'
CAP_SPLIT = 'split'
CAP_RIT = 'rit'
CAP_PTT = 'ptt'
CAP_AGC = 'agc'
CAP_FILTER = 'filter'
CAP_NB = 'noise_blanker'
CAP_ATT = 'attenuator'
CAP_AF = 'af_gain'
CAP_RF = 'rf_gain'
CAP_SQUELCH = 'squelch'
CAP_KEYER = 'keyer'
CAP_POWER = 'power'
CAP_STEP = 'step'
CAP_MEMORY = 'memory'
CAP_RAW = 'raw'

# TS-850 capability set is deliberately narrow: only commands that the
# 1991-era firmware actually implements per the External Control manual.
# AGC/NB/ATT/AF/RF/SQUELCH/KEYER/POWER are CAT-controllable on later
# Kenwoods (TS-590S, TS-2000) but were never wired up on the TS-850 —
# exposing them here just gave the user "?" replies on every connect.
TS850_CAPS = frozenset({
    CAP_VFO, CAP_MODE, CAP_SPLIT, CAP_RIT, CAP_PTT,
    CAP_FILTER, CAP_STEP, CAP_MEMORY, CAP_RAW,
})

FTX1_CAPS = frozenset({
    CAP_VFO, CAP_MODE, CAP_SPLIT, CAP_RIT, CAP_PTT,
    CAP_AGC, CAP_NB, CAP_POWER, CAP_RAW,
})


RIG_REGISTRY: dict[str, RigDescriptor] = {
    # --- Kenwood --------------------------------------------------------
    'kenwood_ts850': RigDescriptor(
        rig_id='kenwood_ts850',
        vendor='Kenwood',
        model='TS-850S',
        display_name='Kenwood TS-850S',
        default_baud=4800,
        supported_bauds=(1200, 2400, 4800, 9600),
        driver_class=KenwoodTS850Driver,
        capabilities=TS850_CAPS,
        notes='ASCII CAT, 4800 8N2 (1 start, 8 data, 2 stop, no parity) per Kenwood TS-850S manual.',
        data_bits=8,
        stop_bits=2,
        parity='N',
    ),
    'kenwood_ts590s': RigDescriptor(
        rig_id='kenwood_ts590s',
        vendor='Kenwood',
        model='TS-590S',
        display_name='Kenwood TS-590S',
        default_baud=9600,
        supported_bauds=(4800, 9600, 19200, 38400, 57600),
        driver_class=None,
        capabilities=frozenset(),
        notes='Kenwood ASCII (largely TS-850 compatible). Driver pending.',
    ),
    'kenwood_ts2000': RigDescriptor(
        rig_id='kenwood_ts2000',
        vendor='Kenwood',
        model='TS-2000',
        display_name='Kenwood TS-2000',
        default_baud=9600,
        supported_bauds=(4800, 9600, 19200, 38400, 57600),
        driver_class=None,
        capabilities=frozenset(),
        notes='Kenwood ASCII (largely TS-850 compatible). Driver pending.',
    ),

    # --- Yaesu ----------------------------------------------------------
    'yaesu_ft991a': RigDescriptor(
        rig_id='yaesu_ft991a',
        vendor='Yaesu',
        model='FT-991A',
        display_name='Yaesu FT-991A',
        default_baud=38400,
        supported_bauds=(4800, 9600, 19200, 38400),
        driver_class=None,
        capabilities=frozenset(),
        notes='Yaesu CAT (ASCII). Driver pending.',
    ),
    'yaesu_ftdx10': RigDescriptor(
        rig_id='yaesu_ftdx10',
        vendor='Yaesu',
        model='FT-DX10',
        display_name='Yaesu FT-DX10',
        default_baud=38400,
        supported_bauds=(4800, 9600, 19200, 38400),
        driver_class=None,
        capabilities=frozenset(),
        notes='Yaesu CAT (ASCII). Driver pending.',
    ),
    'yaesu_ftx1': RigDescriptor(
        rig_id='yaesu_ftx1',
        vendor='Yaesu',
        model='FTX-1',
        display_name='Yaesu FTX-1',
        default_baud=38400,
        supported_bauds=(4800, 9600, 19200, 38400, 115200),
        driver_class=YaesuFTX1Driver,
        capabilities=FTX1_CAPS,
        notes='Yaesu FTX-1 (Field / Optima). Yaesu CAT v2, 38400 8N1.',
        data_bits=8,
        stop_bits=1,
        parity='N',
    ),

    # --- Icom -----------------------------------------------------------
    'icom_ic7300': RigDescriptor(
        rig_id='icom_ic7300',
        vendor='Icom',
        model='IC-7300',
        display_name='Icom IC-7300',
        default_baud=19200,
        supported_bauds=(4800, 9600, 19200, 38400, 57600, 115200),
        driver_class=None,
        capabilities=frozenset(),
        notes='Icom CI-V (binary). Driver pending.',
    ),
    'icom_ic7610': RigDescriptor(
        rig_id='icom_ic7610',
        vendor='Icom',
        model='IC-7610',
        display_name='Icom IC-7610',
        default_baud=19200,
        supported_bauds=(4800, 9600, 19200, 38400, 57600, 115200),
        driver_class=None,
        capabilities=frozenset(),
        notes='Icom CI-V (binary). Driver pending.',
    ),
    'icom_ic705': RigDescriptor(
        rig_id='icom_ic705',
        vendor='Icom',
        model='IC-705',
        display_name='Icom IC-705',
        default_baud=115200,
        supported_bauds=(4800, 9600, 19200, 38400, 57600, 115200),
        driver_class=None,
        capabilities=frozenset(),
        notes='Icom CI-V (binary). Driver pending.',
    ),

    # --- Xiegu ----------------------------------------------------------
    'xiegu_g90': RigDescriptor(
        rig_id='xiegu_g90',
        vendor='Xiegu',
        model='G90',
        display_name='Xiegu G90',
        default_baud=19200,
        supported_bauds=(9600, 19200, 38400),
        driver_class=None,
        capabilities=frozenset(),
        notes='Icom CI-V compatible. Driver pending.',
    ),
    'xiegu_x6100': RigDescriptor(
        rig_id='xiegu_x6100',
        vendor='Xiegu',
        model='X6100',
        display_name='Xiegu X6100',
        default_baud=19200,
        supported_bauds=(9600, 19200, 38400),
        driver_class=None,
        capabilities=frozenset(),
        notes='Icom CI-V compatible. Driver pending.',
    ),
}


def get_descriptor(rig_id: str) -> RigDescriptor | None:
    """Look up a descriptor by its stable ``rig_id``."""
    return RIG_REGISTRY.get(rig_id)


def list_descriptors() -> list[RigDescriptor]:
    """Return all registered descriptors, sorted vendor + model."""
    return sorted(
        RIG_REGISTRY.values(),
        key=lambda d: (d.vendor.lower(), d.model.lower()),
    )
