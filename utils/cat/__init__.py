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
    refreshes the built-in command catalogs from
    :mod:`utils.cat.seed_commands` on every startup. User-added
    commands (``is_builtin = 0``) and saved macros are preserved; only
    rows flagged ``is_builtin = 1`` are replaced so corrections to the
    seed list (e.g. dropping commands the rig never supported)
    propagate to existing deployments. Called once at app startup from
    :func:`app._init_app`.
    """
    from utils.cat import macros_db, seed_commands

    macros_db.init(db_path)
    for rig_id, cmds in seed_commands.SEEDS.items():
        macros_db.reseed_builtins(rig_id, cmds)


def list_serial_ports() -> list[dict]:
    """Enumerate available serial ports for the UI to pick from.

    Primary source is pyserial's ``list_ports.comports()``, which returns
    rich metadata (description, USB VID/PID hwid) when the ``/sys`` tree
    for the device is visible.

    Inside a Docker container the USB-serial adapter is frequently exposed
    as a bare ``/dev/tty*`` character node (via the compose ``devices:``
    bind-mount) WITHOUT the matching ``/sys/class/tty/<name>/device`` sysfs
    entry that ``comports()`` walks to decide a port is "real". When that
    metadata is absent ``comports()`` silently returns an empty list even
    though the node is fully usable — this is the intermittent
    "port not detected" symptom under Docker Desktop / usbipd-win.

    To make detection robust we additionally scan the well-known USB
    serial device-node globs and merge in anything ``comports()`` missed,
    keyed by device path so entries are never duplicated.
    """
    import logging
    import os
    import stat

    logger = logging.getLogger('intercept.cat')
    ports: dict[str, dict] = {}

    # 1. Primary: pyserial enumeration (rich metadata when sysfs is present).
    try:
        from serial.tools import list_ports  # type: ignore

        comports_result = list(list_ports.comports())
        logger.debug('comports() returned %d port(s)', len(comports_result))
        for p in comports_result:
            ports[p.device] = {
                'device': p.device,
                'description': p.description or '',
                'hwid': p.hwid or '',
            }
    except ImportError:
        logger.warning('pyserial not installed — using device-node fallback only')
    except Exception as exc:
        logger.warning('comports() raised %s — falling back to device-node scan', exc)

    # 2. Fallback: scan raw character-device nodes. Covers the Docker
    #    bind-mount case where the node exists but sysfs metadata does not,
    #    so comports() returned nothing (or missed this specific device).
    #    POSIX only — the globs never match on a native Windows host, where
    #    comports() already enumerates COM ports reliably.
    if os.name == 'posix':
        import glob

        # USB-CDC / USB-serial classes + common virtual/pseudo-terminal devices
        # used by some adapters. Legacy /dev/ttyS* is omitted on purpose:
        # containers expose dozens of non-functional ttyS nodes that would
        # flood the picker, and comports() already surfaces the real ones
        # when present.
        patterns = (
            '/dev/ttyUSB*',   # FTDI, Prolific, CH340, etc.
            '/dev/ttyACM*',   # CDC-ACM (Arduino, many modern adapters)
            '/dev/ttyAMA*',   # Raspberry Pi UART
            '/dev/ttyCH*',    # Some CH341 drivers
            '/dev/ttyXRUSB*', # Exar USB-serial
        )
        for pattern in patterns:
            for node in glob.glob(pattern):
                if node in ports:
                    continue
                # Verify it's actually a character device we can stat
                try:
                    mode = os.stat(node).st_mode
                    if not stat.S_ISCHR(mode):
                        continue
                except OSError:
                    continue
                ports[node] = {
                    'device': node,
                    'description': 'USB serial device (bind-mount)',
                    'hwid': '',
                }
                logger.debug('Fallback scan found: %s', node)

        # If still nothing found, log a hint for the operator
        if not ports:
            logger.info(
                'No serial ports detected. If using Docker, ensure the device '
                'is attached via usbipd BEFORE `docker compose up`, or recreate '
                'the container after attaching: `docker compose up -d --force-recreate`'
            )

    result = sorted(ports.values(), key=lambda d: d['device'])
    logger.debug('list_serial_ports returning %d port(s): %s',
                 len(result), [p['device'] for p in result])
    return result
