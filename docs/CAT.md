# CAT (Computer Aided Transceiver) Mode

Serial-port control of amateur radio transceivers. Lives under the **Signals** group on the main mode bar and is reachable at `/cat`.

> Status: PR #1 (driver + supervisor + diagnostics + main-view terminal). Panadapter, front-panel view, and the other vendor drivers are deferred to follow-up PRs.

---

## 1. What's in this PR

### Reference driver — Kenwood TS-850S
Full implementation in `utils/cat/kenwood_ts850.py`:

- Auto-Info (`AI1;`) live frame streaming
- Periodic poll: `IF;` + `SM;` every 0.5 s (or `SM;` every 1.5 s when AI is on) — **togglable** at runtime via `/cat/polling`
- Frame parsers: `IF`, `FA`, `FB`, `MD`, `SM`, `FR`, `FT`, `RT`, `XT`, `GT`, `FL`, `MC`, `NB`, `RA`, `AG`, `RG`, `SQ`, `KS`, `PC`
- Modes: `LSB`, `USB`, `CW`, `CW-R`, `AM`, `FM`, `FSK`, `FSK-R`
- Default serial framing: **4800 8N2**, RTS/DTR de-asserted (TS-850 quirk). All five parameters (baud, data bits, stop bits, parity, RTS/DTR) are per-rig defaults sourced from the registry and can be overridden per connection.

### Vendor catalog (stubs only, no driver code yet)
`utils/cat/registry.py` lists these so the UI can advertise upcoming support. Selecting one returns HTTP 400 `driver_unavailable` until a real driver lands. Each descriptor carries its own serial-framing defaults (`default_baud`, `data_bits`, `stop_bits`, `parity`) so the UI can preset the right values per rig.

| Vendor | Models |
|---|---|
| Kenwood | TS-590S, TS-2000 |
| Yaesu   | FT-991A, FT-DX10, FTX-1 |
| Icom    | IC-7300, IC-7610, IC-705 |
| Xiegu   | G90, X6100 |

### Supervisor — server-side safety
Enforced in `routes/cat.py` *before* any command reaches the rig. Persisted via `get_setting`/`set_setting` under key `cat.supervisor` (no schema migration).

| Setting | Default | Effect |
|---|---|---|
| `tx_locked`   | **on**       | `/cat/ptt` → HTTP 403 `tx_locked`. `/cat/raw` refuses **only commands that physically key the rig** (`TX`, `KY`, `KS` prefixes); query / mode / VFO / AI commands still pass. |
| `band_guard`  | **on**       | `/cat/vfo` outside configured ham bands → HTTP 403 `band_guard` |
| `max_power_w` | `0` (no cap) | When >0, `/cat/power` clamps requested watts and reports both `requested` and applied `watts` |
| `bands`       | HF + 6 m / 2 m / 70 cm | Editable list of `(lo_hz, hi_hz)` ranges |

---

## 2. API

### Status / catalog (GET)
| Endpoint | Description |
|---|---|
| `/cat/rigs` | All rig descriptors with capability tags, serial-framing defaults (`default_baud`, `data_bits`, `stop_bits`, `parity`) and `implemented` flag |
| `/cat/ports` | Available serial ports (`pyserial.tools.list_ports`) |
| `/cat/status` | Current driver + state + supervisor snapshot + `polling_enabled` |
| `/cat/supervisor` | Current supervisor settings |
| `/cat/supervisor/check_freq?hz=…` | Returns `{allowed: bool}` |
| `/cat/polling` | Returns `{enabled: bool}` for the running driver |

### Lifecycle (POST)
| Endpoint | Body |
|---|---|
| `/cat/select` | `{ "rig_id": "kenwood_ts850" }` |
| `/cat/connect` | `{ "port": "/dev/ttyUSB0", "baud": 4800, "data_bits": 8, "stop_bits": 2, "parity": "N", "use_auto_info": true, "assert_rts": false, "assert_dtr": false }` — framing fields default to the selected rig descriptor when omitted |
| `/cat/disconnect` | `{}` |
| `/cat/refresh` | `{}` — re-poll `IF;` |
| `/cat/polling` | `{ "enabled": bool }` — turn the safety-net poll on/off without reconnecting |
| `/cat/probe` | `{ "port": "…", "baud": 4800, "data_bits": 8, "stop_bits": 2, "parity": "N", "assert_rts": false, "assert_dtr": false, "timeout": 1.0 }` — stand-alone cable diagnostic, refuses while a driver is connected (HTTP 409 `busy`). Sends `AI0; ID; IF; FA;` and returns per-query ASCII + hex + byte counts plus a verdict string. Modeled on `port/source/plugins/panadapter/diag.py::radio_probe()`. |

### Control (POST)
| Endpoint | Body | Supervisor gate |
|---|---|---|
| `/cat/vfo`        | `{ "which": "A"\|"B", "hz": int }` | band-guard |
| `/cat/mode`       | `{ "mode": "USB" }` | — |
| `/cat/split`      | `{ "on": bool }` | — |
| `/cat/rit`        | `{ "hz": int, "on": bool }` | — |
| `/cat/rit/clear`  | `{}` | — |
| `/cat/ptt`        | `{ "tx": bool }` | **tx-lock** |
| `/cat/raw`        | `{ "cmd": "IF;" }` | **tx-lock** (only commands beginning with `TX` / `KY` / `KS` are refused while locked) |
| `/cat/agc`        | `{ "agc": 0..2 }` | — |
| `/cat/filter`     | `{ "slot": int }` | — |
| `/cat/nb`         | `{ "on": bool }` | — |
| `/cat/attenuator` | `{ "level": int }` | — |
| `/cat/af_gain`    | `{ "value": 0..255 }` | — |
| `/cat/rf_gain`    | `{ "value": 0..255 }` | — |
| `/cat/squelch`    | `{ "value": int }` | — |
| `/cat/keyer`      | `{ "wpm": int }` | — |
| `/cat/power`      | `{ "watts": int }` | **power-cap** |
| `/cat/step`       | `{ "hz": int }` | — |
| `/cat/supervisor` | partial settings dict | — |

### Live stream
`GET /cat/stream` — Server-Sent Events. Multi-tab safe via `sse_stream_fanout`.

Event types: `state`, `supervisor`, `io`, `lifecycle` (`lifecycle.event` is one of `connected` / `disconnected` / `polling`).

---

## 3. Frontend

The UI is split between a thin sidebar (rig controls that don't need real estate) and the **main view** (`#catVisuals`) which renders a CAT terminal as the primary work surface. The main view is visible on every viewport — there are no mobile-only controls hidden in the sidebar.

### Sidebar — `templates/partials/modes/cat.html`
- **VFO & Mode** — VFO A/B inputs with `Set` and `RX A`/`RX B` buttons, mode dropdown, Split / RIT toggles.
- **Supervisor** — TX-lock, band-guard, power-cap controls.

### Main view — `#catVisuals` in `templates/index.html`
- **Connection panel** (collapsible `<details>`) — transceiver, serial port, baud, data / parity / stop, RTS/DTR, Connect / Disconnect. Auto-collapses on successful connect and re-opens on disconnect. Framing fields auto-fill from the selected rig descriptor (TS-850 → `8N2`, modern rigs → `8N1`).
- **Toolbar** — RIG name badge, connection-state dot, `autoscroll` checkbox, `poll` checkbox (toggles `/cat/polling` at runtime), `clear`, **Probe** (runs `/cat/probe` diagnostic and echoes results to the terminal), `Poll IF;` (one-shot refresh).
- **CAT terminal** — colour-coded log of every TX / RX frame, system messages, and probe verdicts. Capped at ~500 lines.
- **Raw input** — type a command and press Enter or click **Send**. Trailing `;` is added automatically. While TX-lock is on, only `TX` / `KY` / `KS` commands are refused; queries and mode/VFO/AI commands work freely.
- **Live state** pane — human-readable summary of `RigState` (VFO A/B, mode, split, RIT, PTT, S-meter, AGC, AF/RF/SQL, power, keyer).

### Controller — `static/js/modes/cat.js`
IIFE `CATMode` matching the pattern of every other mode module. Uses `EventSource('/cat/stream')` and won't overwrite an input while the user is typing in it. Public surface: `init`, `destroy`, `connect`, `disconnect`, `refreshPorts`, `setVfo`, `selectVfo`, `setMode`, `setSplit`, `setRit`, `clearRit`, `sendRaw`, `updateSupervisor`, `probe`, `clearTerminal`, `refreshStatus`, `togglePolling`.

### Styles
`static/css/modes/cat.css` — scoped, uses existing CSS tokens (`--accent`, `--accent-cyan`, `--accent-green`, `--accent-red`, `--bg-card`, `--border-dim`, `--font-mono`, `--text-dim`).

### Wiring in `templates/index.html`
CSS map, JS map, mode card under Signals, partial include, `modeCatalog` entry, destroy map, switch-mode branch, plus a `catVisuals` entry in the `modesWithVisuals` list and a `display: flex / none` toggle in the mode-switch handler.

---

## 4. Testing in Docker

The CAT mode talks to a transceiver over a USB↔Serial adapter (FTDI / Prolific / CH340). The container must be able to see that `/dev/tty*` device.

### 4.1 Linux host

1. Plug the adapter in and find its path:
   ```bash
   ls /dev/ttyUSB* /dev/ttyACM*
   dmesg | tail   # confirms FTDI/CH340/etc.
   ```
2. Edit [docker-compose.yml](../docker-compose.yml) and uncomment the matching device line under the `intercept` service:
   ```yaml
   devices:
     - /dev/bus/usb:/dev/bus/usb
     - /dev/ttyUSB0:/dev/ttyUSB0     # ← uncomment / adjust
   ```
   (The device must exist on the host **before** `docker compose up`; otherwise compose fails with "no such file".)
3. The container already runs `privileged: true`, so it can open the device once it's mapped. If you've dropped privileged in your own deploy, also set:
   ```yaml
   group_add:
     - "20"          # `getent group dialout | cut -d: -f3` on the host
   ```
4. Start:
   ```bash
   docker compose --profile basic up -d --build
   ```
5. Open `http://localhost:5050/`, log in, switch to **CAT** under the Signals group.
6. In the main view, open the **Connection** drawer (top of `#catVisuals`):
   - **Rig**: `Kenwood TS-850S` — picking the rig auto-fills the framing fields below.
   - **Port**: should list `/dev/ttyUSB0` (hit *Rescan* if not)
   - **Baud / Data / Parity / Stop**: `4800 / 8 / N / 2` (preset from the TS-850 descriptor; override if your interface differs)
   - **RTS / DTR**: both **off** (TS-850 quirk)
   - Click **Connect**. The drawer collapses, the RIG badge lights up, and the CAT terminal starts logging frames.
7. If nothing comes back, click **Probe** before fiddling with cables. It runs `/cat/probe` against the same port/framing with the driver detached, sends `ID; IF; FA;`, and prints ASCII + hex + a verdict so you can tell "no bytes at all" from "wrong baud / framing".
8. Try read-only commands (everything except PTT / explicit TX-keying raw commands is gated only by band-guard):
   - Type a VFO A frequency inside a ham band (e.g. `14250000` Hz) → **Set A**.
   - Watch the terminal for `→ FA00014250000;` and the rig response.
9. **TX is locked by default.** Status queries, mode changes, and Auto-Info commands all still work. To actually key the rig (`/cat/ptt`, `TX;`, `KY…;`), uncheck **TX locked** in the Supervisor sidebar first. Then test with the rig's antenna disconnected or into a dummy load.
10. The toolbar **poll** checkbox toggles the safety-net `IF; SM;` poll loop at runtime. With Kenwood Auto-Info on it's mostly redundant; turn it off if you want a strictly event-driven trace.

### 4.2 Windows / WSL2 host

USB↔Serial passthrough into WSL2 needs `usbipd-win` (already documented in `docker-compose.override.yml` for SDR dongles — same procedure applies):

```powershell
winget install dorssel.usbipd-win        # one time
usbipd list                              # find BUSID of the serial adapter
usbipd bind --busid <BUSID>              # one time per device (elevated)
usbipd attach --wsl --busid <BUSID>      # each session
```

Then in WSL the adapter appears as `/dev/ttyUSB0`. Proceed with step 2 above.

### 4.3 Without hardware (sanity check only)

You can still verify the wiring without a rig:

```bash
docker compose --profile basic up -d --build
curl -s http://localhost:5050/cat/rigs       | jq '.rigs[].rig_id'
curl -s http://localhost:5050/cat/ports      | jq
curl -s http://localhost:5050/cat/status     | jq
curl -s http://localhost:5050/cat/supervisor | jq
```

(Adjust for your auth — log in via the UI first and reuse the session cookie, or set `INTERCEPT_DISABLE_AUTH=true` in compose for local testing only.)

---

## 5. Tests

```bash
pytest tests/test_cat_registry.py tests/test_cat_driver.py tests/test_cat_routes.py -v
```

- `test_cat_registry.py` — 5 tests: descriptor catalog, sort order, `to_dict` round-trip.
- `test_cat_driver.py` — 13 tests: parser for every supported frame type plus command formatting and input validation. **No serial port required** — the driver is instantiated via `__new__` and frames are fed directly into `_parse()`.
- `test_cat_routes.py` — 15 tests: REST endpoints, supervisor enforcement (band-guard, tx-lock keying-only narrowing, power-cap), 409 when no driver, unimplemented-rig rejection. Driver is mocked.

Tests run on Linux / WSL. On native Windows pytest's conftest fails earlier on an unrelated `termios` import from another route module — run from inside the container or WSL.

---

## 6. File map

| Path | Purpose |
|---|---|
| `utils/cat/__init__.py` | Package façade, `list_serial_ports()` |
| `utils/cat/base.py` | `RigDriver` ABC + `RigState` dataclass |
| `utils/cat/supervisor.py` | `Supervisor` dataclass, `load_supervisor` / `save_supervisor` |
| `utils/cat/registry.py` | `RigDescriptor`, capability constants, `RIG_REGISTRY`, lookups |
| `utils/cat/kenwood_ts850.py` | TS-850S driver implementation |
| `routes/cat.py` | Blueprint `cat_bp`, REST + SSE endpoints |
| `templates/partials/modes/cat.html` | UI partial |
| `static/js/modes/cat.js` | `CATMode` IIFE controller |
| `static/css/modes/cat.css` | Scoped styles |
| `tests/test_cat_registry.py` | Registry tests |
| `tests/test_cat_driver.py` | Driver / parser tests |
| `tests/test_cat_routes.py` | API + supervisor tests |

Integration touchpoints:

- `app.py` — globals `cat_driver`, `cat_queue`, `cat_lock`
- `routes/__init__.py` — `cat_bp` imported & registered
- `templates/index.html` — CSS map, JS map, Signals mode card, partial include, `modeCatalog`, destroy map, switch-mode branch (8 edits total)

---

## 7. Deferred to follow-up PRs

- Panadapter / waterfall surface bound to VFO A
- Front-panel virtual-rig view
- Real driver code for the 10 stub vendor entries (Kenwood TS-590S/TS-2000, Yaesu FT-991A/FT-DX10/FTX-1, Icom IC-7300/IC-7610/IC-705, Xiegu G90/X6100)
- Memory-channel browser UI
- Per-rig user presets (default band/mode pairs)

---

## 8. Command catalog & Macro Builder

A SQLite catalog of per-rig CAT commands plus a UI for assembling them
into named macros. Backing store: `instance/cat.db` (separate from
`interc_settings.db` so the feature can be enabled/disabled without
touching the main settings file).

**Tables** (`utils/cat/macros_db.py::_SCHEMA`):

- `interc_cat_commands(id, rig_id, category, name, raw_template,
  param_label, param_type, param_default, description, is_builtin)`
- `interc_cat_macros(id, rig_id, name, description, created_at)`
- `interc_cat_macro_steps(id, macro_id, position, command_id,
  param_value, delay_ms, note)` — `ON DELETE CASCADE` from macros.

**Built-ins** (`utils/cat/seed_commands.py`):

- `kenwood_ts850` — 44 commands across `vfo`, `mode`, `split`, `rit`,
  `gain`, `aux`.
- `yaesu_ftx1` — ~30 commands (catalog only, no live driver yet — see
  the [FTX-1 reference](cat/FTX1_REFERENCE.md)).

Seeds run once at startup from `app.py::_init_app()` via
`utils.cat.init_command_catalog()`; re-running is a no-op
(`has_seed(rig_id)` guard).

**REST endpoints** (added to `routes/cat.py`):

| Method | Path                          | Notes                                |
|--------|-------------------------------|--------------------------------------|
| GET    | `/cat/commands`               | Query: `rig_id`, `q`, `category`     |
| POST   | `/cat/commands`               | Add user-defined command             |
| DELETE | `/cat/commands/<id>`          | Refuses built-ins                    |
| GET    | `/cat/macros`                 | List macros for a rig                |
| GET    | `/cat/macros/<id>`            | Full macro with steps                |
| POST   | `/cat/macros`                 | Upsert by `(rig_id, name)`           |
| DELETE | `/cat/macros/<id>`            | Cascades to steps                    |
| POST   | `/cat/macros/<id>/run`        | Pre-flight TX-lock gate, then run    |

`/cat/macros/<id>/run` walks the step list and calls `driver.send_raw()`
for each rendered frame, honoring per-step `delay_ms`. If TX is locked
and *any* step's `raw_command` matches `_command_keys_tx()`, the whole
macro is refused before sending a single byte (HTTP 403 `tx_locked`).
A `macro` event is also echoed onto the CAT SSE stream so the terminal
shows the run summary.

**UI**: collapsible `<details id="catMacroPanel">` between the
Connection panel and the terminal. Two columns — left = command
catalog (filter + category select), right = saved macros / step
editor. All JS lives in `static/js/modes/cat.js::CATMode::Macros`.
Re-fetches automatically when the rig picker changes.

---

## 9. Persistent connection preferences

Per-rig serial settings are remembered between sessions so the operator
doesn't have to re-pick the COM port, baud and RTS/DTR flags on every
visit. Storage is **client-side `localStorage`** under the key
`intercept.cat.prefs.v1` with shape:

```jsonc
{
  "kenwood_ts850": {
    "port": "COM3", "baud": "4800",
    "data_bits": "8", "stop_bits": "2", "parity": "N",
    "assert_rts": true, "assert_dtr": false
  },
  "yaesu_ftx1": { /* … */ }
}
```

Saved automatically on every change of the connection fields and again
on a successful `Connect`. Restored on rig-picker change and after
`/cat/ports` resolves (so a `COM3` value that doesn't exist on the
current host is silently ignored rather than corrupting the form).

`localStorage` was chosen over a server-side `interc_settings` row
because these are per-workstation operator preferences — two operators
sharing one Intercept instance keep their own COM-port choice without
clobbering each other.

## 10. UI affordances

* **Connection summary** in the collapsed `catConnectPanel` reflects
  live state: `"Kenwood TS-850S · connected · COM3 @ 4800"` while
  connected, `"… · choose port"` otherwise. Re-renders on connect,
  disconnect, every form change, and every SSE state push.
* **RECON panel and the bottom status-bar are hidden in CAT mode** —
  neither belongs to a rig-control conversation. Configured via the
  `hideRecon` / `hideStatusBar` mode-lists in
  `templates/index.html`'s `switchMode()` block.
* **Terminal resize handle** uses an explicit `height: 360px` (with
  `resize: vertical` and `max-height: 80vh`) instead of a flex-grown
  size, because `resize` requires a definite height on the element
  itself.

## 11. Timing & cooldowns

The TS-850 over an FTDI USB-serial cable is sensitive to back-to-back
writes — frames sent <30 ms apart can be silently merged or dropped by
the radio's UART. The driver mitigates this at three points:

* **`POST_OPEN_SETTLE_S = 0.25`** in
  `utils/cat/kenwood_ts850.py::start()` — quiet window after the port
  is opened (and RTS/DTR set) before the first `AI1;`/`IF;` leaves the
  host. Eliminates the "first connect doesn't respond" race.
* **`INTER_COMMAND_DELAY_S = 0.05`** in the TX-drain loop in `_run()` —
  inserted only when another frame is already queued, so single
  commands keep their original latency.
* **Flush after every write** so the FTDI bridge cannot coalesce two
  CAT frames into one USB packet.

At the route layer, `_RECONNECT_SETTLE_S = 1.2` enforces a minimum gap
between connect/disconnect operations. The stamp is now updated on
*both* paths, and `/cat/disconnect` returns `cooldown_ms` so the JS
greys out the Connect button (`Wait 1.2s`) for the duration. Defends
against the operator double-clicking Connect/Disconnect before the COM
port has fully released.
