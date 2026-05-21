# Pager & 433 Sensor Display Revamp

**Date:** 2026-05-21
**Status:** Approved

## Overview

Replace the plain chronological card feed for the Pager and 433 Sensor modes with purpose-built views that better surface the structure of each signal type. Both new views are opt-out (toggle to classic feed available).

---

## Architecture

The two modes use slightly different DOM strategies suited to each layout.

**Pager:** `#pagerDirectoryView` is the left directory panel only. The output panel parent switches to `display: flex` in directory mode, placing the directory panel and `#output` side by side. `#output` becomes the right feed panel — no duplication, no hidden copy.

**Sensor:** `#sensorDashboardView` is a full-replacement grid that sits alongside `#output`. In dashboard mode `#output` is hidden but continues to receive classic `signal-card` insertions so export and filtering remain intact.

```
[output-panel]  (flex in pager directory mode)
  [#pagerDirectoryView]    ← left dir panel only; shown in pager directory mode
  [#sensorDashboardView]   ← full replacement grid; shown in sensor dashboard mode
  [#output]                ← right feed panel (pager) or hidden (sensor); always updated
```

`addMessage()` gets a hook to `PagerDirectory.addMessage()` for directory panel updates only (the feed is `#output` itself). `addSensorReading()` gets a hook to `SensorDashboard.addReading()` for station card updates. No other existing logic changes.

### New files

| File | Purpose |
|------|---------|
| `static/js/components/pager-directory.js` | PagerDirectory component |
| `static/js/components/sensor-dashboard.js` | SensorDashboard component |
| `static/css/components/pager-directory.css` | Directory view styles |
| `static/css/components/sensor-dashboard.css` | Dashboard view styles |

`templates/index.html` gets:
- Two new sibling containers (`#pagerDirectoryView`, `#sensorDashboardView`)
- Toggle buttons in the output panel header (one per mode, shown when that mode is active)
- Script/link tags for the four new files
- One-line hook calls inside `addMessage()` and `addSensorReading()`

---

## Pager — Source Directory View

### Layout

Split panel, full height of the output area:

- **Left (200 px fixed):** address directory panel
- **Right (flex):** full message feed

### Directory panel (left)

- One row per unique pager address seen this session
- Sorted by message count descending (most active at top)
- Each row shows:
  - Protocol badge (`P` = POCSAG, `F` = FLEX), coloured accordingly
  - Address string
  - Message count (`×24`)
  - Relative-width activity bar (count relative to the highest-count address)
  - Last-seen relative timestamp (`just now`, `2m ago`)
  - Green dot when a new message arrives from that address (fades after 3 s)
  - Blue left-border accent on the currently highlighted address
- Directory state is in-memory for the session only (not persisted)

### Feed panel (right)

- Shows **all messages** at all times (no filtering)
- When an address is highlighted via the directory:
  - Feed scrolls to that address's most recent card
  - All cards from that address get a blue left-border + subtle background tint
  - Sub-header shows `"<address> highlighted"` with a "clear highlight" link
- Clicking "clear highlight" (or clicking the same address again) removes all highlighting and returns to the plain feed
- Cards are otherwise identical to the existing `signal-card` format

### Toggle

- Button group top-right of the output panel header: **Directory** | **Feed**
- Default: **Directory**
- Preference saved to `localStorage` key `pagerView` (`'directory'` | `'feed'`)
- Restored on mode switch

---

## 433 Sensor — Station Dashboard View

### Layout

Responsive CSS grid of station cards (3 columns on typical desktop width, wrapping as needed).

### Station card

One persistent card per unique device, keyed by `model + id`. Cards are created on first reading and updated in place on subsequent readings from the same device.

Each card contains:

- **Header:** device model name (e.g. `Acurite-Tower`), device ID + channel, last-seen relative timestamp (green when < 10 s)
- **Readings:** the primary numeric values for that device (temperature, humidity, pressure, wind speed, rain, etc.) — label + value + unit, displayed as a small inline grid
- **Sparkline:** SVG polyline tracking the primary numeric value across the last 30 readings. Colour matches the reading type (amber for temperature, blue for humidity/wind, purple for pressure). A filled circle marks the latest data point.
- **Footer:** battery status (green `BAT OK` / red `BAT LOW`), SNR value, frequency badge

### State-only devices

Devices that emit only a state (doorbells, PIR sensors, etc.) get a card with a state indicator (coloured dot + label e.g. `MOTION DETECTED`) in place of numeric readings. The sparkline area is replaced with an "event-only device" label. Card still flashes on each event.

### Flash on update

When a new reading arrives for a known device:
- Card receives a CSS animation class that briefly tints the background (blue for temp sensors, purple for other types) and fades back to normal over ~0.8 s
- Values update in place; the sparkline dot advances right

### New device appearance

First time a device is seen: card slides in with a subtle green border accent. The border fades to normal after the first update.

### Toggle

- Button group top-right of output panel header: **Dashboard** | **Feed**
- Default: **Dashboard**
- Preference saved to `localStorage` key `sensorView` (`'dashboard'` | `'feed'`)
- Restored on mode switch

---

## Shared behaviour

- Both toggles are shown only when the relevant mode is active
- Classic `#output` feed always receives cards in the background (export, CSV/JSON, existing filter bar all continue to work)
- No changes to SSE handling, process management, or backend routes
- No new backend endpoints required
