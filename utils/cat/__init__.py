"""CAT (Computer Aided Transceiver) control package.

Provides a vendor-neutral driver abstraction for radio transceivers over
serial. Each concrete driver implements the :class:`RigDriver` ABC and is
registered in :mod:`utils.cat.registry`.

The package is consumed by the Flask blueprint in :mod:`routes.cat`.
"""

from __future__ import annotations

from utils.cat.base import RigDriver, RigState
from utils.cat.registry import RIG_REGISTRY, RigDescriptor, get_descriptor, list_descriptors
from utils.cat.supervisor import Supervisor, load_supervisor, save_supervisor

__all__ = [
    'RIG_REGISTRY',
    'RigDescriptor',
    'RigDriver',
    'RigState',
    'Supervisor',
    'get_descriptor',
    'init_command_catalog',
    'list_descriptors',
    'list_serial_ports',
    'load_supervisor',
    'save_supervisor',
]


def init_command_catalog(db_path=None) -> None:
    """Initialise the SQLite-backed CAT command catalog + macro tables.

    Idempotent. Creates ``instance/cat.db`` (or the provided path) and
    seeds the built-in command catalogs from :mod:`utils.cat.seed_commands`
    for every rig that doesn't yet have rows. Called once at app startup
    from :func:`app._init_app`.
    """
    from utils.cat import macros_db, seed_commands

    macros_db.init(db_path)
    seed_commands.seed_all(macros_db.seed_catalog)


def list_serial_ports() -> list[dict]:
    """Enumerate available serial ports for the UI to pick from."""
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError:
        return []
    try:
        return [
            {'device': p.device, 'description': p.description or '', 'hwid': p.hwid or ''}
            for p in list_ports.comports()
        ]
    except Exception:
        return []
