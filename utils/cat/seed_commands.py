"""Built-in CAT command catalog — seed data for the macros DB.

These dicts are inserted into ``interc_cat_commands`` the first time
``cat.db`` is created (or any time a rig has zero rows). User-added
commands live alongside them with ``is_builtin = 0``.

When you add a rig:
    1. Append a new ``<RIG>_COMMANDS`` list below.
    2. Register it in :data:`SEEDS`.
    3. Make sure a matching ``RigDescriptor`` exists in
       :mod:`utils.cat.registry` so the rig appears in the picker.

Templates use :py:meth:`str.format` placeholders, e.g. ``FA{hz:011d};``.
The DB layer (:mod:`utils.cat.macros_db.resolve_command`) substitutes
the user-supplied parameter at macro-save time, so the
``raw_command`` stored on each step is already wire-ready.

Sources:
    - TS-850: derived from :mod:`utils.cat.kenwood_ts850` and the
      reference plugin (port/source/plugins/panadapter/rigs.py).
    - FTX-1: derived from the Yaesu FTX-1 CAT Operation Reference
      Manual (see docs/cat/FTX1_REFERENCE.md). The FTX-1 has no
      Python driver yet; commands are catalogued so the macro builder
      and command browser work — actual transmit requires a future
      YaesuCAT driver.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Kenwood TS-850S — 4800 8N2, 11-digit frequency.
#
# Scope is intentionally limited to commands documented in the TS-850S
# External Control Operation manual: AI, DN/UP, FA/FB, FL, FR/FT, ID,
# IF, LK, MC, MD, MR, MW, MX, PT, RC, RD/RU, RM, RT, RX/TX, SC, SH/SL,
# SM, TN, VR, XT. CAT for AGC/AF/RF/SQ/NB/RA/KS/PC was added on later
# Kenwoods (TS-590S, TS-2000) and is NOT implemented on the TS-850 —
# putting them in the macro catalog only ever produced "?" replies.
# ---------------------------------------------------------------------------
TS850_COMMANDS: list[dict] = [
    # --- frequency ---
    dict(name='Set VFO A', category='frequency', raw_template='FA{hz:011d};',
         param_label='Frequency (Hz)', param_type='int', param_default='14225000',
         description='Set receive frequency on VFO A'),
    dict(name='Set VFO B', category='frequency', raw_template='FB{hz:011d};',
         param_label='Frequency (Hz)', param_type='int', param_default='14225000',
         description='Set frequency on VFO B'),
    dict(name='Query VFO A', category='frequency', raw_template='FA;',
         expects_response=True, description='Read VFO A frequency'),
    dict(name='Query VFO B', category='frequency', raw_template='FB;',
         expects_response=True, description='Read VFO B frequency'),
    dict(name='Select VFO A (RX)', category='frequency', raw_template='FR0;',
         description='Make VFO A the receive VFO'),
    dict(name='Select VFO B (RX)', category='frequency', raw_template='FR1;',
         description='Make VFO B the receive VFO'),
    dict(name='Select Memory (RX)', category='frequency', raw_template='FR2;',
         description='Receive from memory channel'),
    dict(name='VFO Step Up', category='frequency', raw_template='UP;',
         description='One step up at current tuning rate'),
    dict(name='VFO Step Down', category='frequency', raw_template='DN;',
         description='One step down at current tuning rate'),

    # --- mode ---
    dict(name='Mode LSB',   category='mode', raw_template='MD1;', description='Set LSB'),
    dict(name='Mode USB',   category='mode', raw_template='MD2;', description='Set USB'),
    dict(name='Mode CW',    category='mode', raw_template='MD3;', description='Set CW'),
    dict(name='Mode FM',    category='mode', raw_template='MD4;', description='Set FM'),
    dict(name='Mode AM',    category='mode', raw_template='MD5;', description='Set AM'),
    dict(name='Mode FSK',   category='mode', raw_template='MD6;', description='Set FSK'),
    dict(name='Mode CW-R',  category='mode', raw_template='MD7;', description='Set CW-Reverse'),
    dict(name='Mode FSK-R', category='mode', raw_template='MD9;', description='Set FSK-Reverse'),

    # --- TX source / split ---
    # FTn sets the TX source (0=VFO A, 1=VFO B, 2=Memory). Split exists
    # implicitly when FT != FR; there is no dedicated split toggle on
    # the TS-850.
    dict(name='TX from VFO A',  category='split', raw_template='FT0;',
         description='Set TX source = VFO A (split if RX is on B)'),
    dict(name='TX from VFO B',  category='split', raw_template='FT1;',
         description='Set TX source = VFO B (split if RX is on A)'),
    dict(name='TX from Memory', category='split', raw_template='FT2;',
         description='Set TX source = memory channel'),

    # --- RIT / XIT ---
    dict(name='RIT ON',         category='rit', raw_template='RT1;'),
    dict(name='RIT OFF',        category='rit', raw_template='RT0;'),
    dict(name='Clear RIT',      category='rit', raw_template='RC;'),
    dict(name='RIT Step Up',    category='rit', raw_template='RU;',
         description='Step RIT offset up by one increment'),
    dict(name='RIT Step Down',  category='rit', raw_template='RD;',
         description='Step RIT offset down by one increment'),
    dict(name='XIT ON',         category='rit', raw_template='XT1;'),
    dict(name='XIT OFF',        category='rit', raw_template='XT0;'),

    # --- PTT (Supervisor-gated on the route layer) ---
    dict(name='PTT ON (TX)',  category='ptt', raw_template='TX;',
         description='Key the transmitter — Supervisor TX-lock must be off'),
    dict(name='PTT OFF (RX)', category='ptt', raw_template='RX;'),

    # --- front-end / DSP (only what the TS-850 actually implements) ---
    dict(name='Filter Slot', category='dsp', raw_template='FL{slot:02d};',
         param_label='Slot (0=wide … 20=narrow)', param_type='int', param_default='0'),
    dict(name='AIP ON',  category='dsp', raw_template='MX1;',
         description='Advanced Intercept Point ON (HF front-end protection)'),
    dict(name='AIP OFF', category='dsp', raw_template='MX0;'),
    dict(name='CW Pitch', category='dsp', raw_template='PT{value:02d};',
         param_label='Pitch (0-12)', param_type='int', param_default='6'),

    # --- memory ---
    dict(name='Memory Channel', category='memory', raw_template='MC{ch:02d};',
         param_label='Channel (0-99)', param_type='int', param_default='0',
         description='Select memory channel (2 digits per TS-850 manual)'),
    dict(name='Memory Read', category='memory', raw_template='MR{section};',
         param_label='Section (0 or 1)', param_type='int', param_default='0',
         expects_response=True,
         description='Read current memory channel contents'),

    # --- scan / lock / tones ---
    dict(name='Scan ON',  category='misc', raw_template='SC1;'),
    dict(name='Scan OFF', category='misc', raw_template='SC0;'),
    dict(name='Lock ON',  category='misc', raw_template='LK1;',
         description='Lock front panel'),
    dict(name='Lock OFF', category='misc', raw_template='LK0;'),
    dict(name='CTCSS Tone', category='misc', raw_template='TN{tone:02d};',
         param_label='Tone number (01-38)', param_type='int', param_default='01'),

    # --- status ---
    dict(name='Identify',       category='status', raw_template='ID;',
         expects_response=True, description='Read model ID (TS-850 = ID008)'),
    dict(name='Query IF (bulk status)', category='status', raw_template='IF;',
         expects_response=True),
    dict(name='Query S-meter',  category='status', raw_template='SM;',
         expects_response=True),
    dict(name='Read Meter',     category='status', raw_template='RM;',
         expects_response=True,
         description='Read non-S meter (SWR / ALC / COMP depending on selection)'),
    dict(name='Query Mode',     category='status', raw_template='MD;',
         expects_response=True),
    dict(name='AutoInfo ON',    category='status', raw_template='AI1;',
         description='Radio pushes IF; on every change'),
    dict(name='AutoInfo OFF',   category='status', raw_template='AI0;'),
]


# ---------------------------------------------------------------------------
# Yaesu FTX-1 Optima — 38400 8N1, 9-digit frequency, sub-byte mode codes.
# See docs/cat/FTX1_REFERENCE.md for the full command list.
# ---------------------------------------------------------------------------
FTX1_COMMANDS: list[dict] = [
    # --- frequency ---
    dict(name='Set VFO A', category='frequency', raw_template='FA{hz:09d};',
         param_label='Frequency (Hz)', param_type='int', param_default='14225000',
         description='Set Main VFO frequency'),
    dict(name='Set VFO B', category='frequency', raw_template='FB{hz:09d};',
         param_label='Frequency (Hz)', param_type='int', param_default='14225000',
         description='Set Sub VFO frequency'),
    dict(name='Query VFO A', category='frequency', raw_template='FA;', expects_response=True),
    dict(name='Query VFO B', category='frequency', raw_template='FB;', expects_response=True),

    # --- mode (Main bank, sub-byte M*) ---
    dict(name='Mode LSB',     category='mode', raw_template='MD0M1;', description='Main: LSB'),
    dict(name='Mode USB',     category='mode', raw_template='MD0M2;', description='Main: USB'),
    dict(name='Mode CW-U',    category='mode', raw_template='MD0M3;', description='Main: CW (USB)'),
    dict(name='Mode FM',      category='mode', raw_template='MD0M4;', description='Main: FM'),
    dict(name='Mode AM',      category='mode', raw_template='MD0M5;', description='Main: AM'),
    dict(name='Mode RTTY-L',  category='mode', raw_template='MD0M6;', description='Main: RTTY-LSB'),
    dict(name='Mode CW-L',    category='mode', raw_template='MD0M7;', description='Main: CW (LSB)'),
    dict(name='Mode DATA-L',  category='mode', raw_template='MD0M8;', description='Main: DATA-LSB'),
    dict(name='Mode RTTY-U',  category='mode', raw_template='MD0M9;', description='Main: RTTY-USB'),
    dict(name='Mode DATA-FM', category='mode', raw_template='MD0MB;', description='Main: DATA-FM'),
    dict(name='Mode FM-N',    category='mode', raw_template='MD0MC;', description='Main: FM Narrow'),
    dict(name='Mode DATA-U',  category='mode', raw_template='MD0MD;', description='Main: DATA-USB'),
    dict(name='Mode AM-N',    category='mode', raw_template='MD0ME;', description='Main: AM Narrow'),

    # --- PTT ---
    dict(name='PTT ON (TX)',  category='ptt', raw_template='TX1;',
         description='Key transmitter — Supervisor TX-lock must be off'),
    dict(name='PTT OFF (RX)', category='ptt', raw_template='TX0;'),

    # --- power / meter ---
    dict(name='TX Power', category='power', raw_template='PC{watts:03d};',
         param_label='Watts (5-100)', param_type='int', param_default='10'),
    dict(name='Query S-meter',    category='status', raw_template='SM0;', expects_response=True),
    dict(name='Query IF (info)',  category='status', raw_template='IF;',  expects_response=True),

    # --- DSP / AGC / NB ---
    dict(name='AGC Slow', category='dsp', raw_template='GT00;'),
    dict(name='AGC Mid',  category='dsp', raw_template='GT01;'),
    dict(name='AGC Fast', category='dsp', raw_template='GT02;'),
    dict(name='AGC OFF',  category='dsp', raw_template='GT03;'),
    dict(name='NB ON',    category='dsp', raw_template='NB01;'),
    dict(name='NB OFF',   category='dsp', raw_template='NB00;'),

    # --- VFO swap / split ---
    dict(name='Split ON',  category='split', raw_template='ST1;'),
    dict(name='Split OFF', category='split', raw_template='ST0;'),
    dict(name='Swap A/B',  category='split', raw_template='SV;'),

    # --- system ---
    dict(name='AutoInfo ON',  category='status', raw_template='AI1;'),
    dict(name='AutoInfo OFF', category='status', raw_template='AI0;'),
    dict(name='Identify',     category='status', raw_template='ID;', expects_response=True),
]


# ---------------------------------------------------------------------------
# Registry — maps rig_id to its built-in command list.
# Keys must match the rig descriptor IDs in :mod:`utils.cat.registry`.
# ---------------------------------------------------------------------------
SEEDS: dict[str, list[dict]] = {
    'kenwood_ts850': TS850_COMMANDS,
    'yaesu_ftx1':    FTX1_COMMANDS,
}


def commands_for(rig_id: str) -> list[dict]:
    """Return the seed list for ``rig_id`` (empty list if unknown)."""
    return list(SEEDS.get(rig_id, []))


def seed_all(seed_fn) -> dict[str, int]:
    """Apply every seed list via ``seed_fn(rig_id, commands)`` → int.

    Returns ``{rig_id: rows_inserted}`` for logging.
    """
    return {rig: seed_fn(rig, cmds) for rig, cmds in SEEDS.items()}
