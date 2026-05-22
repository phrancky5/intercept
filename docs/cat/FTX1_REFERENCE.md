# Yaesu FTX-1 — CAT Command Reference (curated)

> Status: **catalog-only / mock-up**.  Intercept ships an FTX-1 *descriptor*
> for the rig picker and seeds a built-in command catalog into `cat.db`,
> but there is no live `YaesuFTX1` driver yet.  The Macro Builder will
> render and save FTX-1 macros, but executing them against hardware
> requires a future driver implementation that subclasses
> `utils.cat.base.RigDriver`.

This document is a curated extract from the FTX-1 *CAT Operation Reference
Manual* (Yaesu, latest revision available at time of writing).  It is
intentionally narrow: it covers the commands Intercept exposes through
the catalog plus the protocol framing needed to add new ones.

---

## 1.  Wire-level framing

| Parameter        | Value                                          |
|------------------|------------------------------------------------|
| Interface        | USB-CDC virtual COM (CAT)                      |
| Baud rates       | 4800, 9600, 19200, 38400, **115200** (default) |
| Data bits        | 8                                              |
| Parity           | None                                           |
| Stop bits        | 1                                              |
| Flow control     | None (DTR/RTS ignored)                         |
| Line terminator  | `;` (semicolon)                                |
| Casing           | UPPER CASE — commands and parameters           |

Every command and every reply is an ASCII string terminated by `;`.
The FTX-1 echoes nothing on success when a *write* completes — it only
replies if you append a `?` style or if you issue a *read* form (e.g.
`FA;` to query VFO-A).  Always frame each command with a trailing `;`
before sending.

---

## 2.  Frequency, VFO, split, RIT

| Command   | Form / example         | Meaning                                |
|-----------|------------------------|----------------------------------------|
| `FA`      | `FA014250000;`         | Set VFO-A frequency (Hz, 9 digits)     |
| `FA;`     | →`FA014250000;`        | Query VFO-A frequency                  |
| `FB`      | `FB014250000;`         | Set VFO-B frequency (Hz, 9 digits)     |
| `FB;`     | →`FB014250000;`        | Query VFO-B frequency                  |
| `FR0;`/`FR1;` |                    | RX VFO = A / B                         |
| `FT0;`/`FT1;` |                    | TX VFO = A / B (drives SPLIT)          |
| `RT0;`/`RT1;` |                    | RIT off / on                           |
| `RC;`     |                        | Clear RIT offset                       |
| `RU0050;`/`RD0050;` |              | RIT up / down 50 Hz                    |

The FTX-1 frequency field is always **9 digits, right-padded with zeros**
to Hz precision.  Intercept's `FA` catalog entry uses the format string
`FA{hz:09d};` — the macro engine substitutes the user-supplied parameter
via `str.format`.

---

## 3.  Mode

`MD0n;` writes the operating mode of the active VFO.  `MD0;` reads it.

| Code | Mode      | Code | Mode        |
|------|-----------|------|-------------|
| 1    | LSB       | 7    | CW-R        |
| 2    | USB       | 8    | LSB-DATA    |
| 3    | CW        | 9    | RTTY-R      |
| 4    | FM        | A    | DATA-FM     |
| 5    | AM        | B    | FM-N        |
| 6    | RTTY-L    | C    | DATA-U      |
|      |           | D    | AM-N        |

Intercept's catalog ships `MD-USB`, `MD-LSB`, `MD-CW`, `MD-AM`, `MD-FM`,
`MD-DATA-U` and `MD-DATA-L` as ready-made entries.

---

## 4.  Bandwidth (`SH`) and IF filters

`SH0nn;` writes the selected DSP bandwidth for the active VFO/mode.  The
exact index range depends on the current mode — Yaesu publishes a table
in §4-2 of the reference manual.  A representative subset:

| `nn` | SSB     | CW      | AM       | FM     |
|------|---------|---------|----------|--------|
| 00   | 1.5 kHz | 50 Hz   | 3.0 kHz  | 9 kHz  |
| 10   | 2.4 kHz | 500 Hz  | 6.0 kHz  | 16 kHz |
| 21   | 3.0 kHz | 2.4 kHz | 9.0 kHz  | (n/a)  |

Intercept does **not** seed a bandwidth command into the catalog — it
varies per mode and risks confusing first-time users.  Add the entries
you need through the *Add custom command* button.

---

## 5.  Gain, NB, AGC, attenuator

| Command   | Range            | Meaning                                    |
|-----------|------------------|--------------------------------------------|
| `AG0nnn;` | 000–255          | AF gain (0 = silent, 255 = max)            |
| `RG0nnn;` | 000–255          | RF gain                                    |
| `SQ0nnn;` | 000–100          | Squelch level                              |
| `GT00`–`GT03` |              | AGC speed: off / slow / mid / fast         |
| `NB00`/`NB01` |              | Noise blanker off / on                     |
| `RA00`/`RA01`/`RA02` |       | Attenuator: 0 / 6 / 12 dB                  |
| `PC0nnn;` | 005–100          | TX power (W, **TX-keying** — see §7)       |

---

## 6.  ID, IF poll, S-meter

| Command | Reply form                | Notes                                |
|---------|---------------------------|--------------------------------------|
| `ID;`   | `ID0750;` (example)       | 4-digit model code                   |
| `IF;`   | `IFnnnnnnnnnnn…;`         | Composite state — VFO, mode, etc.    |
| `SM0;`  | `SM0nnn;`                 | S-meter, 0–255 raw                   |

Intercept's supervisor uses `IF;` as the periodic poll when the user
enables the **poll** checkbox.

---

## 7.  Transmit-keying commands (TX-LOCK GATE)

The following commands are flagged as **TX-keying** by Intercept's CAT
supervisor.  When the user has TX-locked the radio (default on first
connect), the `/cat/raw`, `/cat/macros/<id>/run` and `/cat/ptt` endpoints
will refuse to send them and the macro runner reports a
`tx_locked` error before sending the **first** byte:

* `TX0;` / `TX1;` — PTT off / on
* `KP{wpm:03d};` — keyer speed (changes WPM but does **not** key)
  is *not* gated — but a macro that combines `KP` with `TX1;` will be
  refused as a whole.
* `PC{w:03d};` — TX power level write.  Reads (`PC;`) are not gated.

To unlock TX, the operator must explicitly clear the supervisor lock
from the CAT mode toolbar.  This mirrors the same gate already applied
to the TS-850 driver and is intentional — macros must not silently
key the rig.

---

## 8.  Menu / EX commands

`EX{nnn};` reads/writes menu item *nnn* (see §6 of the manual for the
full table).  Intercept does **not** seed any `EX` commands — they are
rig-config-specific and a wrong value can take a service-menu reset to
recover.  Use the *Add custom command* button to register the specific
`EX` entries you rely on.

---

## 9.  Adding commands not in this list

The Macro Builder's *Add custom command* form accepts:

* **Name** — short human label.
* **Template** — the raw frame, with optional `{name:format}` placeholders
  (e.g. `FA{hz:09d};`, `PC{w:03d};`, `EX{n:04d}{v};`).  The macro engine
  uses Python's `str.format` for substitution; integer parameters are
  parsed as `int(param)` first, falling back to a string.
* **Category** — free-form (e.g. `vfo`, `mode`, `aux`).
* **Param label / type / default** — only the *label* is rendered into
  the step editor; the type/default are persisted for future UI hints.
* **Description** — shown as a hover tooltip on the command list.

User-added commands are stored with `is_builtin = 0` in
`interc_cat_commands` and can be deleted; built-ins are protected.

---

## 10.  References

* Yaesu — *FTX-1 CAT Operation Reference Manual* (Rev. F or later).
* Yaesu — *FTX-1F / FTX-1FA Operating Manual*, §10 "Computer Control".
* Intercept — [CAT mode overview](../CAT.md).
