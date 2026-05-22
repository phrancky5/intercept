# CAT macros — staged PR plan

> Recreated 2026-05-22 after the original plan note was lost.
> This document maps the path from the current Intercept fork back to an
> upstream-mergeable contribution.  Each stage is intended to be a single
> reviewable PR.

## Guiding principles

1. **Additive over invasive** — no driver internals are rewritten in this
   thread of work.  New tables live in `instance/cat.db`, isolated from
   `interc_settings.db`, so the fork can be merged or reverted in one
   step.
2. **One PR per stage**, kept small enough that a reviewer can read the
   diff in one sitting.
3. **Hardware-optional** — UI must function and tests must pass without
   a connected rig.  Live execution paths fail closed (HTTP 409
   `not_connected`).
4. **Supervisor-honest** — every new code path that *could* key the rig
   passes through `_command_keys_tx()` / `_supervisor.tx_locked`, same as
   `/cat/raw`.

## Stage map

### PR #1 — CAT driver core + supervisor (✅ merged in fork)

* `utils/cat/base.py` — `RigDriver` ABC, supervisor.
* `utils/cat/kenwood_ts850.py` — first concrete driver.
* `routes/cat.py` — 29 endpoints, SSE fanout, TX-lock gate.
* `templates/index.html` — CAT mode tile, connect panel, terminal.
* `static/{css,js}/modes/cat.*` — mode controller.

### PR #2 — Command catalog + Macro Builder *(this iteration)*

* `utils/cat/macros_db.py` — SQLite DAL at `instance/cat.db`.
* `utils/cat/seed_commands.py` — built-in TS-850 + FTX-1 catalogs.
* `utils/cat/__init__.py` exposes `init_command_catalog()`.
* `app.py::_init_app()` calls it after `init_db()`.
* `routes/cat.py` — 8 new endpoints under `/cat/commands` and
  `/cat/macros`.
* `templates/index.html` — collapsible `<details id="catMacroPanel">`
  panel between Connection and terminal.
* `static/css/modes/cat.css` — `.cat-vis-macro-*` styling, no inline
  styles, mobile-friendly.
* `static/js/modes/cat.js` — `Macros` sub-module inside `CATMode` IIFE,
  re-fetches when the rig picker changes.
* `docs/cat/FTX1_REFERENCE.md` — curated CAT manual extract.
* `docs/CAT.md` — appended section linking to the manual and explaining
  the catalog/macro feature.
* **Migration**: none — new SQLite file, auto-seeds on first boot.
  Existing installs gain the feature transparently.
* **Resize-handle fix** in `cat.css` (terminal `flex: 1` → `height: 360px`)
  ships with this PR — small enough to ride along.

### PR #3 — Front-panel rig views

* Yaesu FTX-1 SVG front-panel mockup (no driver yet) under
  `templates/components/cat_frontpanel_ftx1.html`.
* TS-850 front-panel that mirrors live state from `/cat/stream`.
* `static/js/modes/cat.js` adds `FrontPanel` sub-module bound to the SSE
  feed.
* Toggle between *Terminal*, *Front panel* and *Macros* via the existing
  `<details>` rows.

### PR #4 — Panadapter (optional, port from upstream port/)

* `routes/panadapter.py` — narrow scan endpoint that drives the active
  driver, emits to `cat_queue`.
* `templates/components/cat_panadapter.html` and matching JS.
* Documented as **experimental** — guarded behind a feature flag in
  `config.py`.

### PR #5 — `rigctld` bridge

* `utils/cat/rigctld_bridge.py` — async TCP server that translates a
  subset of Hamlib `rigctld` commands into `RigDriver` calls.
* Allows external apps (`fldigi`, `wsjtx`, `cqrlog`) to share the rig
  through Intercept's supervisor instead of fighting for the COM port.
* Opt-in via `intercept_agent.cfg`.

### PR #6 — More rig drivers

* `utils/cat/yaesu_ftx1.py` — first real Yaesu driver, gated by the
  catalog seeded in PR #2.
* `utils/cat/icom_civ.py` — generic CI-V driver behind a transceive
  address descriptor.
* Each driver: ABC implementation + descriptor entry + a `tests/` smoke
  test using a recorded transcript fixture.

## Acceptance gates per PR

* `pytest -q` clean on the touched modules.
* `docker compose --profile basic up -d --build` succeeds and the CAT
  mode tile loads without console errors on a fresh `instance/`.
* `docs/CAT.md` reflects any new endpoints or UI affordances.
* `CHANGELOG.md` notes the stage.

## Out of scope (deliberately)

* Sharing `cat.db` rows with `interc_settings` — held until an upstream
  maintainer signs off, to keep PR #2 small.
* Multi-rig simultaneous control — one driver instance, one COM port.
* Persisted macro run history (telemetry).  Echoing to the CAT SSE
  stream is enough for the foreseeable future.
