"""Server-side safety guardrails for CAT control.

The :class:`Supervisor` enforces three policies that *cannot* be bypassed
from the browser without an explicit administrative change:

* ``tx_locked`` — blocks any PTT / TX-bearing command.
* ``band_guard`` — refuses VFO frequencies outside the configured list.
* ``max_power_w`` — clamps requested output power (0 disables the cap).

Defaults are deliberately strict (TX locked, band guard on) because the
original deployment context was an educational lab where students may not
transmit. Operators who own a licence can relax these via the UI.

State is persisted as a single JSON blob under the upstream ``settings``
key/value table (``cat.supervisor``); no schema migration required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from utils.database import get_setting, set_setting

logger = logging.getLogger('intercept.cat.supervisor')

_SETTING_KEY = 'cat.supervisor'

# Amateur HF + 6 m + 2 m + 70 cm bands as a sensible default. Operators
# trim/extend the list from the UI.
DEFAULT_BANDS_HZ: list[tuple[int, int]] = [
    (1_800_000, 2_000_000),       # 160 m
    (3_500_000, 4_000_000),       # 80 m
    (5_330_500, 5_406_400),       # 60 m (region 2 channels)
    (7_000_000, 7_300_000),       # 40 m
    (10_100_000, 10_150_000),     # 30 m
    (14_000_000, 14_350_000),     # 20 m
    (18_068_000, 18_168_000),     # 17 m
    (21_000_000, 21_450_000),     # 15 m
    (24_890_000, 24_990_000),     # 12 m
    (28_000_000, 29_700_000),     # 10 m
    (50_000_000, 54_000_000),     # 6 m
    (144_000_000, 148_000_000),   # 2 m
    (430_000_000, 440_000_000),   # 70 cm
]


@dataclass
class Supervisor:
    """In-memory policy state. Persist with :func:`save_supervisor`."""

    tx_locked: bool = True
    band_guard: bool = True
    max_power_w: int = 0
    bands: list[dict[str, Any]] = field(default_factory=lambda: [
        {'name': '', 'lo': lo, 'hi': hi} for lo, hi in DEFAULT_BANDS_HZ
    ])

    def to_dict(self) -> dict[str, Any]:
        return {
            'tx_locked': bool(self.tx_locked),
            'band_guard': bool(self.band_guard),
            'max_power_w': int(self.max_power_w),
            'bands': [
                {
                    'name': str(b.get('name', '')),
                    'lo': int(b['lo']),
                    'hi': int(b['hi']),
                }
                for b in self.bands
            ],
        }

    def freq_allowed(self, hz: int) -> bool:
        """Return ``True`` if ``hz`` is inside any enabled band, or if the
        band guard is disabled entirely."""
        if not self.band_guard:
            return True
        return any(int(b['lo']) <= int(hz) <= int(b['hi']) for b in self.bands)

    def cap_power(self, watts: int) -> int:
        """Clamp requested power to the configured maximum.

        ``max_power_w == 0`` is treated as "no cap" and returns ``watts``
        unchanged.
        """
        w = max(0, int(watts))
        if self.max_power_w <= 0:
            return w
        return min(w, int(self.max_power_w))

    def apply_update(self, payload: dict[str, Any]) -> None:
        """Apply a partial update dict from the REST layer.

        Only known keys are honoured; unknown keys are ignored so that
        older UIs do not break newer servers.
        """
        if 'tx_locked' in payload:
            self.tx_locked = bool(payload['tx_locked'])
        if 'band_guard' in payload:
            self.band_guard = bool(payload['band_guard'])
        if 'max_power_w' in payload:
            self.max_power_w = max(0, int(payload['max_power_w']))
        if 'bands' in payload and isinstance(payload['bands'], list):
            cleaned: list[dict[str, Any]] = []
            for b in payload['bands']:
                if not isinstance(b, dict):
                    continue
                try:
                    lo = int(b['lo'])
                    hi = int(b['hi'])
                except (KeyError, TypeError, ValueError):
                    continue
                if hi <= lo:
                    continue
                cleaned.append({
                    'name': str(b.get('name', '') or ''),
                    'lo': lo,
                    'hi': hi,
                })
            if cleaned:
                self.bands = cleaned


def load_supervisor() -> Supervisor:
    """Return a :class:`Supervisor` populated from persistent settings.

    Falls back to defaults if the setting is absent or malformed.
    """
    raw = get_setting(_SETTING_KEY, None)
    sv = Supervisor()
    if not isinstance(raw, dict):
        return sv
    try:
        sv.apply_update(raw)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning('Discarded malformed supervisor setting: %s', exc)
        return Supervisor()
    return sv


def save_supervisor(sv: Supervisor) -> None:
    """Persist the current :class:`Supervisor` state."""
    try:
        set_setting(_SETTING_KEY, sv.to_dict())
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning('Could not persist supervisor: %s', exc)
