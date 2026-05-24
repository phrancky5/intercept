"""SQLite-backed CAT command catalog + macro builder storage.

Isolated database at ``instance/cat.db`` — kept separate from the main
INTERCEPT settings DB until the upstream merge is approved. This avoids
schema collisions with ``interc_settings`` while we iterate on the macro
data model.

Schema (three tables, mirrors ``port/source/sql/database-schema-cat-macros.sql``
but with SQLite-flavoured types):

    interc_cat_commands     — per-rig CAT command catalog (seeded + user-added)
    interc_cat_macros       — named macros (per rig)
    interc_cat_macro_steps  — ordered steps composing a macro

Concurrency notes:
    SQLite connections are short-lived per operation (open → use → close)
    and we enable WAL so SSE writers / reads don't block each other. All
    write helpers wrap their statement in a single transaction.

Reference implementation:
    port/source/plugins/panadapter/macros.py (Postgres-flavoured original).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger('intercept.cat.macros_db')

# Default location — created on first init. Relative path is fine here:
# Flask's CWD is the project root inside the container (/app).
DEFAULT_DB_PATH = Path('instance') / 'cat.db'

_db_path: Path = DEFAULT_DB_PATH
_init_lock = threading.Lock()
_initialised = False


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------
@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Yield a short-lived SQLite connection with row-dict access."""
    conn = sqlite3.connect(str(_db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS interc_cat_commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rig_id          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    category        TEXT    NOT NULL DEFAULT 'misc',
    raw_template    TEXT    NOT NULL,
    param_label     TEXT,
    param_type      TEXT    DEFAULT 'none',
    param_default   TEXT,
    expects_response INTEGER DEFAULT 0,
    description     TEXT,
    is_builtin      INTEGER DEFAULT 1,
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (rig_id, name)
);

CREATE INDEX IF NOT EXISTS idx_cat_commands_rig
    ON interc_cat_commands(rig_id);
CREATE INDEX IF NOT EXISTS idx_cat_commands_category
    ON interc_cat_commands(rig_id, category);

CREATE TABLE IF NOT EXISTS interc_cat_macros (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rig_id      TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT,
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (rig_id, name)
);

CREATE INDEX IF NOT EXISTS idx_cat_macros_rig
    ON interc_cat_macros(rig_id);

CREATE TABLE IF NOT EXISTS interc_cat_macro_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    macro_id    INTEGER NOT NULL REFERENCES interc_cat_macros(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    command_id  INTEGER REFERENCES interc_cat_commands(id) ON DELETE SET NULL,
    raw_command TEXT    NOT NULL,
    param_value TEXT,
    delay_ms    INTEGER NOT NULL DEFAULT 100,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_macro_steps_macro
    ON interc_cat_macro_steps(macro_id, position);
"""


def init(db_path: Path | str | None = None) -> None:
    """Create the cat.db schema if missing. Idempotent."""
    global _db_path, _initialised
    with _init_lock:
        if db_path is not None:
            _db_path = Path(db_path)
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.execute('PRAGMA journal_mode = WAL')
            conn.executescript(_SCHEMA)
        _initialised = True
        logger.info('cat.db initialised at %s', _db_path)


def is_initialised() -> bool:
    return _initialised


def get_db_path() -> Path:
    return _db_path


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def has_seed(rig_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS n FROM interc_cat_commands WHERE rig_id = ?',
            (rig_id,),
        ).fetchone()
    return bool(row and int(row['n']) > 0)


def seed_catalog(rig_id: str, commands: Iterable[dict]) -> int:
    """Insert built-ins for ``rig_id`` if the catalog has no rows yet."""
    if has_seed(rig_id):
        return 0
    return _insert_builtins(rig_id, commands)


def reseed_builtins(rig_id: str, commands: Iterable[dict]) -> int:
    """Refresh the built-in catalog rows for ``rig_id``.

    Deletes existing rows with ``is_builtin = 1`` and reinserts the
    provided list. User-added commands (``is_builtin = 0``) are left
    alone. Macro steps that referenced a deleted built-in row keep
    their ``raw_command`` (already wire-ready); only the FK is reset to
    ``NULL`` by the schema's ``ON DELETE SET NULL``.

    Use this from :func:`init_command_catalog` so corrections to the
    built-in catalog (e.g. dropping commands the rig never supported)
    propagate to existing ``cat.db`` files on startup without
    requiring manual DB surgery.
    """
    with _connect() as conn:
        conn.execute(
            'DELETE FROM interc_cat_commands '
            'WHERE rig_id = ? AND is_builtin = 1',
            (rig_id,),
        )
    return _insert_builtins(rig_id, commands)


def _insert_builtins(rig_id: str, commands: Iterable[dict]) -> int:
    inserted = 0
    with _connect() as conn:
        for cmd in commands:
            try:
                conn.execute(
                    """
                    INSERT INTO interc_cat_commands
                      (rig_id, name, category, raw_template, param_label,
                       param_type, param_default, expects_response,
                       description, is_builtin)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        rig_id,
                        cmd['name'],
                        cmd.get('category', 'misc'),
                        cmd['raw_template'],
                        cmd.get('param_label'),
                        cmd.get('param_type', 'none'),
                        cmd.get('param_default'),
                        1 if cmd.get('expects_response') else 0,
                        cmd.get('description'),
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError as exc:
                logger.debug('seed insert skipped %s/%s: %s',
                             rig_id, cmd.get('name'), exc)
    logger.info('Seeded %d CAT commands for %s', inserted, rig_id)
    return inserted


# ---------------------------------------------------------------------------
# Command catalog read / write
# ---------------------------------------------------------------------------
def list_commands(rig_id: str, category: str | None = None,
                  search: str | None = None) -> list[dict]:
    sql = ('SELECT id, rig_id, name, category, raw_template, param_label, '
           'param_type, param_default, expects_response, description, is_builtin '
           'FROM interc_cat_commands WHERE rig_id = ?')
    params: list[Any] = [rig_id]
    if category:
        sql += ' AND category = ?'
        params.append(category)
    if search:
        sql += ' AND (name LIKE ? OR raw_template LIKE ? OR description LIKE ?)'
        like = f'%{search}%'
        params.extend([like, like, like])
    sql += ' ORDER BY category, name'
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_categories(rig_id: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            'SELECT DISTINCT category FROM interc_cat_commands '
            'WHERE rig_id = ? ORDER BY category',
            (rig_id,),
        ).fetchall()
    return [r['category'] for r in rows if r['category']]


def add_custom_command(rig_id: str, name: str, raw_template: str,
                       category: str = 'custom',
                       param_label: str | None = None,
                       param_type: str = 'none',
                       param_default: str | None = None,
                       description: str | None = None) -> dict:
    if not raw_template.endswith(';'):
        raw_template = raw_template + ';'
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO interc_cat_commands
              (rig_id, name, category, raw_template, param_label,
               param_type, param_default, description, is_builtin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (rig_id, name, category, raw_template, param_label,
             param_type, param_default, description),
        )
        row = conn.execute(
            'SELECT * FROM interc_cat_commands WHERE rig_id = ? AND name = ?',
            (rig_id, name),
        ).fetchone()
    return _row_to_dict(row) if row else {}


def delete_command(command_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            'DELETE FROM interc_cat_commands WHERE id = ? AND is_builtin = 0',
            (command_id,),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Param resolution (str.format-style placeholders, e.g. FA{hz:011d};)
# ---------------------------------------------------------------------------
import re as _re
_PARAM_RE = _re.compile(r'\{(\w+)(?::[^}]+)?\}')


def resolve_command(template: str, param_value: str | None) -> str:
    if '{' not in template:
        return template
    m = _PARAM_RE.search(template)
    if not m:
        return template
    kw = m.group(1)
    val = param_value if param_value not in (None, '') else '0'
    try:
        return template.format(**{kw: int(val)})
    except (ValueError, TypeError):
        try:
            return template.format(**{kw: val})
        except Exception:
            return template


# ---------------------------------------------------------------------------
# Macro CRUD
# ---------------------------------------------------------------------------
def list_macros(rig_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            'SELECT id, rig_id, name, description, created_at, updated_at '
            'FROM interc_cat_macros WHERE rig_id = ? ORDER BY name',
            (rig_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_macro(macro_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            'SELECT id, rig_id, name, description FROM interc_cat_macros WHERE id = ?',
            (macro_id,),
        ).fetchone()
        if not row:
            return None
        macro = _row_to_dict(row)
        steps = conn.execute(
            'SELECT id, position, command_id, raw_command, param_value, delay_ms, note '
            'FROM interc_cat_macro_steps WHERE macro_id = ? ORDER BY position',
            (macro_id,),
        ).fetchall()
    macro['steps'] = [_row_to_dict(s) for s in steps]
    return macro


def upsert_macro(rig_id: str, name: str, description: str | None,
                 steps: list[dict]) -> dict:
    """Create or replace a macro and its steps.

    Each step dict accepts:
        command_id?: int — reference to interc_cat_commands.id
        raw_template?: str — used when no command_id (e.g. ad-hoc raw)
        param_value?: str — substituted into the template
        delay_ms?: int — pause after this step
        note?: str
    """
    # Build template lookup for command_id refs.
    cmd_rows = list_commands(rig_id)
    cmd_by_id = {c['id']: c for c in cmd_rows}

    with _connect() as conn:
        existing = conn.execute(
            'SELECT id FROM interc_cat_macros WHERE rig_id = ? AND name = ?',
            (rig_id, name),
        ).fetchone()
        if existing:
            macro_id = existing['id']
            conn.execute(
                'UPDATE interc_cat_macros SET description = ?, '
                'updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (description, macro_id),
            )
            conn.execute(
                'DELETE FROM interc_cat_macro_steps WHERE macro_id = ?',
                (macro_id,),
            )
        else:
            cur = conn.execute(
                'INSERT INTO interc_cat_macros (rig_id, name, description) '
                'VALUES (?, ?, ?)',
                (rig_id, name, description),
            )
            macro_id = cur.lastrowid

        for idx, step in enumerate(steps or []):
            cmd_id = step.get('command_id')
            template = step.get('raw_template')
            if cmd_id and cmd_id in cmd_by_id:
                template = cmd_by_id[cmd_id]['raw_template']
            if not template:
                continue
            param_value = step.get('param_value')
            resolved = resolve_command(template, param_value)
            conn.execute(
                """
                INSERT INTO interc_cat_macro_steps
                  (macro_id, position, command_id, raw_command, param_value,
                   delay_ms, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (macro_id, idx,
                 cmd_id if cmd_id and cmd_id > 0 else None,
                 resolved, param_value,
                 int(step.get('delay_ms') or 100),
                 step.get('note')),
            )
    return get_macro(macro_id) or {}


def delete_macro(macro_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            'DELETE FROM interc_cat_macros WHERE id = ?',
            (macro_id,),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def run_macro(macro_id: int, cat_driver) -> dict:
    """Walk the macro's steps and send each ``raw_command`` to ``cat_driver``.

    The driver must expose ``send_raw(cmd: str) -> Optional[str]`` and
    ``is_running() -> bool`` (matches :class:`utils.cat.base.RigDriver`).
    Execution is synchronous on the caller's thread.
    """
    macro = get_macro(macro_id)
    if not macro:
        return {'ok': False, 'error': 'macro not found'}
    if not cat_driver or not getattr(cat_driver, 'is_running', lambda: False)():
        return {'ok': False, 'error': 'CAT not connected'}

    results: list[dict] = []
    started = time.time()
    for step in macro['steps']:
        cmd = step['raw_command']
        try:
            resp = cat_driver.send_raw(cmd)
            results.append({
                'position': step['position'], 'cmd': cmd,
                'response': resp, 'ok': True,
            })
        except Exception as exc:  # noqa: BLE001 — report and continue
            results.append({
                'position': step['position'], 'cmd': cmd,
                'error': str(exc), 'ok': False,
            })
        delay = int(step.get('delay_ms') or 0)
        if delay > 0:
            time.sleep(delay / 1000.0)
    return {
        'ok': True,
        'macro_id': macro_id,
        'macro_name': macro['name'],
        'elapsed_s': round(time.time() - started, 3),
        'steps': results,
    }
