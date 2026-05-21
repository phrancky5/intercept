# Pager & 433 Sensor Display Revamp — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plain card feed for Pager and 433 Sensor modes with a source-directory split-view and a per-device station dashboard, each with a toggle back to the classic feed.

**Architecture:** `#pagerDirectoryView` (left panel) + `#output` (right feed) share a flex wrapper for the pager split; `#sensorDashboardView` replaces `#output` in sensor dashboard mode while `#output` continues receiving cards silently. Two new IIFE components (`PagerDirectory`, `SensorDashboard`) are notified via one-line hooks in the existing `addMessage()` and `addSensorReading()` functions. No backend changes.

**Tech Stack:** Vanilla JS (IIFE pattern), CSS custom properties from `variables.css`, SVG sparklines, Flask/Jinja2 templates, pytest for template structure tests.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `static/css/components/pager-directory.css` | Flex wrap layout, directory panel, entry rows, highlight, toggle buttons |
| Create | `static/css/components/sensor-dashboard.css` | Station card grid, flash animations, sparkline, state-only devices |
| Create | `static/js/components/pager-directory.js` | `PagerDirectory` IIFE — address tracking, highlight, show/hide, reset |
| Create | `static/js/components/sensor-dashboard.js` | `SensorDashboard` IIFE — station cards, sparklines, flash, show/hide, reset |
| Modify | `templates/index.html` | Wrap `#output`, add view containers, toggle buttons, `<link>`/`<script>` tags, two hook calls, two `applyViewState` calls, two reset calls |
| Modify | `tests/test_app.py` | Template structure tests for new elements |

---

## Task 1: HTML scaffolding + template tests

**Files:**
- Modify: `tests/test_app.py`
- Modify: `templates/index.html`

- [ ] **Step 1: Write failing template tests**

Add to `tests/test_app.py`:

```python
def test_pager_directory_elements_present(client):
    response = client.get('/')
    assert b'id="signalViewWrap"' in response.data
    assert b'id="pagerDirectoryView"' in response.data
    assert b'id="pagerDirEntries"' in response.data
    assert b'id="pagerFeedHeader"' in response.data
    assert b'id="pagerToggleDir"' in response.data
    assert b'pager-directory.css' in response.data
    assert b'pager-directory.js' in response.data


def test_sensor_dashboard_elements_present(client):
    response = client.get('/')
    assert b'id="sensorDashboardView"' in response.data
    assert b'id="sensorDashboardGrid"' in response.data
    assert b'id="sensorToggleDash"' in response.data
    assert b'sensor-dashboard.css' in response.data
    assert b'sensor-dashboard.js' in response.data
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_app.py::test_pager_directory_elements_present tests/test_app.py::test_sensor_dashboard_elements_present -v
```

Expected: 2 FAILED (elements not yet in template).

- [ ] **Step 3: Add CSS `<link>` tags**

In `templates/index.html`, find this line (near line 58):
```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/components/signal-cards.css') }}">
```
Add immediately after:
```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/components/pager-directory.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/components/sensor-dashboard.css') }}">
```

- [ ] **Step 4: Add toggle buttons to `#pagerStats`**

In `templates/index.html`, find this exact closing tag (after the FLEX count div, around line 812):
```html
                        </div>
                        <div class="stats" id="sensorStats">
```
Change it to:
```html
                            <div class="view-toggle-group">
                                <button class="view-toggle-btn view-toggle-btn--active" id="pagerToggleDir" onclick="PagerDirectory.show()">Directory</button>
                                <button class="view-toggle-btn" id="pagerToggleFeed" onclick="PagerDirectory.hide()">Feed</button>
                            </div>
                        </div>
                        <div class="stats" id="sensorStats">
```

- [ ] **Step 5: Add toggle buttons to `#sensorStats`**

Find the closing `</div>` of `#sensorStats` (after the device count div, around line 816):
```html
                        </div>
                        <div class="stats" id="wifiStats">
```
Change it to:
```html
                            <div class="view-toggle-group">
                                <button class="view-toggle-btn view-toggle-btn--active" id="sensorToggleDash" onclick="SensorDashboard.show()">Dashboard</button>
                                <button class="view-toggle-btn" id="sensorToggleFeed" onclick="SensorDashboard.hide()">Feed</button>
                            </div>
                        </div>
                        <div class="stats" id="wifiStats">
```

- [ ] **Step 6: Wrap `#output` and add new view containers**

Find this exact block in `templates/index.html` (around line 3600):
```html
                <div class="output-content signal-feed" id="output">
```
Replace with:
```html
                <div id="signalViewWrap">
                <div id="pagerDirectoryView" class="pdir-panel" style="display:none;">
                    <div class="pdir-header">Sources — <span id="pagerDirCount">0</span> active</div>
                    <div id="pagerDirEntries" class="pdir-entries"></div>
                </div>
                <div id="sensorDashboardView" class="sdb-view" style="display:none;">
                    <div id="sensorDashboardGrid" class="sdb-grid"></div>
                </div>
                <div class="pdir-feed-col">
                <div class="pdir-feed-header" id="pagerFeedHeader" style="display:none;">
                    <span id="pagerFeedLabel">All messages</span>
                    <button id="pagerClearHighlight" class="pdir-clear-btn" onclick="PagerDirectory.clearHighlight()" style="display:none;">clear highlight</button>
                </div>
                <div class="output-content signal-feed" id="output">
```

Then find the `</div>` that closes `#output` followed by `<div class="status-bar">` (around line 3607-3609):
```html
                </div>

                <div class="status-bar">
```
Replace with:
```html
                </div>
                </div><!-- .pdir-feed-col -->
                </div><!-- #signalViewWrap -->

                <div class="status-bar">
```

- [ ] **Step 7: Add `<script>` tags**

Find this line (around line 3641):
```html
    <script src="{{ url_for('static', filename='js/components/signal-cards.js') }}"></script>
```
Add immediately after:
```html
    <script src="{{ url_for('static', filename='js/components/pager-directory.js') }}"></script>
    <script src="{{ url_for('static', filename='js/components/sensor-dashboard.js') }}"></script>
```

- [ ] **Step 8: Run tests to confirm they pass**

```
pytest tests/test_app.py::test_pager_directory_elements_present tests/test_app.py::test_sensor_dashboard_elements_present -v
```

Expected: 2 PASSED.

- [ ] **Step 9: Smoke-test the page loads without JS errors**

```
python intercept.py
```

Open `http://localhost:5000` in a browser. Check the DevTools console — there should be no JS errors (the new script tags reference files that don't exist yet, but that causes a network 404, not a console error that breaks the page).

- [ ] **Step 10: Commit**

```bash
git add templates/index.html tests/test_app.py
git commit -m "feat: add HTML scaffolding for pager directory and sensor dashboard views"
```

---

## Task 2: Pager directory CSS

**Files:**
- Create: `static/css/components/pager-directory.css`

- [ ] **Step 1: Create the CSS file**

Create `static/css/components/pager-directory.css` with this exact content:

```css
/* ============================================================
   Signal View Wrap — flex container for split-panel layouts
   ============================================================ */
#signalViewWrap {
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

/* Feed column — wraps feed header + #output, fills remaining space */
.pdir-feed-col {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

/* Feed header strip — shown in directory mode above the message list */
.pdir-feed-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 10px;
    background: var(--bg-card);
    border-bottom: 1px solid var(--border-color);
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--text-secondary);
    flex-shrink: 0;
}

.pdir-clear-btn {
    background: none;
    border: none;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    cursor: pointer;
    padding: 2px 6px;
    border-radius: var(--radius-sm);
    transition: color var(--transition-fast);
}
.pdir-clear-btn:hover { color: var(--text-dim); }

/* ---- Directory panel (left side of split) ---- */
.pdir-panel {
    width: 200px;
    flex-shrink: 0;
    border-right: 1px solid var(--border-color);
    flex-direction: column;
    overflow: hidden;
    background: var(--bg-secondary);
    font-family: var(--font-mono);
}

.pdir-header {
    padding: 6px 10px;
    font-size: var(--text-xs);
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1px;
    border-bottom: 1px solid var(--border-color);
    background: var(--bg-card);
    flex-shrink: 0;
}

.pdir-entries {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
}

/* ---- Individual address entry ---- */
.pdir-entry {
    padding: 7px 10px;
    border-bottom: 1px solid rgba(var(--accent-cyan-rgb), 0.04);
    cursor: pointer;
    position: relative;
    transition: background var(--transition-fast);
}
.pdir-entry:hover { background: var(--bg-tertiary); }
.pdir-entry--active {
    background: rgba(var(--accent-cyan-rgb), 0.06);
    border-left: 2px solid var(--accent-cyan);
    padding-left: 8px;
}

.pdir-entry-top {
    display: flex;
    align-items: center;
    gap: 5px;
    margin-bottom: 3px;
}

.pdir-proto {
    font-size: 8px;
    padding: 1px 4px;
    border-radius: var(--radius-sm);
    font-weight: var(--font-bold);
    flex-shrink: 0;
}
.pdir-proto--p { background: rgba(var(--accent-cyan-rgb), 0.15); color: var(--accent-cyan); }
.pdir-proto--f { background: rgba(143, 123, 214, 0.15); color: var(--accent-purple); }

.pdir-addr {
    font-size: var(--text-xs);
    color: var(--text-secondary);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.pdir-new-dot {
    display: inline-block;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--accent-green);
    flex-shrink: 0;
    opacity: 0;
}
.pdir-new-dot--active {
    animation: pdir-dot-fade 3s ease-out forwards;
}
@keyframes pdir-dot-fade {
    0%   { opacity: 1; }
    85%  { opacity: 1; }
    100% { opacity: 0; }
}

.pdir-count { font-size: 9px; color: var(--text-muted); flex-shrink: 0; }

.pdir-bar-wrap { height: 2px; background: var(--bg-tertiary); border-radius: 1px; margin-bottom: 2px; }
.pdir-bar { height: 2px; background: var(--accent-cyan); border-radius: 1px; transition: width var(--transition-slow); }
.pdir-bar--flex { background: var(--accent-purple); }

.pdir-age { font-size: 8px; color: var(--text-muted); }

/* ---- Highlight applied to signal-cards in #output ---- */
.signal-card.pdir-hl {
    border-left: 2px solid var(--accent-cyan) !important;
    background: rgba(var(--accent-cyan-rgb), 0.04) !important;
}

/* ---- View toggle button group (inside .stats) ---- */
.stats .view-toggle-group { display: none; }
.stats.active .view-toggle-group { display: flex; }

.view-toggle-group {
    gap: 2px;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    padding: 2px;
    margin-left: 6px;
}

.view-toggle-btn {
    padding: 2px 8px;
    font-size: 9px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--text-muted);
    cursor: pointer;
    transition: background var(--transition-fast), color var(--transition-fast);
}
.view-toggle-btn:hover { color: var(--text-dim); }
.view-toggle-btn--active {
    background: var(--accent-cyan-dim);
    color: var(--accent-cyan);
}
```

- [ ] **Step 2: Verify styles load without errors**

With `python intercept.py` running, open `http://localhost:5000`, DevTools → Network tab. Confirm `pager-directory.css` returns HTTP 200.

- [ ] **Step 3: Commit**

```bash
git add static/css/components/pager-directory.css
git commit -m "feat: add pager directory view CSS"
```

---

## Task 3: PagerDirectory JS component

**Files:**
- Create: `static/js/components/pager-directory.js`

- [ ] **Step 1: Create the component file**

Create `static/js/components/pager-directory.js` with this exact content:

```javascript
const PagerDirectory = (function () {
    'use strict';

    const STORAGE_KEY = 'pagerView';

    // Map<address, { count, protocol, lastSeen }>
    const addresses = new Map();
    let highlighted = null;

    // ---- Helpers ----

    function esc(str) {
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function formatAge(ts) {
        const s = Math.floor((Date.now() - ts) / 1000);
        if (s < 10) return 'just now';
        if (s < 60) return `${s}s ago`;
        return `${Math.floor(s / 60)}m ago`;
    }

    // ---- Directory rendering ----

    function renderDirectory() {
        const entriesEl = document.getElementById('pagerDirEntries');
        const countEl   = document.getElementById('pagerDirCount');
        if (!entriesEl) return;

        const sorted = [...addresses.entries()].sort((a, b) => b[1].count - a[1].count);
        const maxCount = sorted.length > 0 ? sorted[0][1].count : 1;

        if (countEl) countEl.textContent = sorted.length;

        sorted.forEach(([addr, data]) => {
            let el = entriesEl.querySelector(`[data-pdir-addr="${CSS.escape(addr)}"]`);
            const isActive = addr === highlighted;
            const pct = Math.round((data.count / maxCount) * 100);
            const isPocsag = data.protocol !== 'flex';
            const protoClass = isPocsag ? 'pdir-proto--p' : 'pdir-proto--f';
            const barClass   = isPocsag ? '' : 'pdir-bar--flex';
            const html = `
                <div class="pdir-entry-top">
                    <span class="pdir-proto ${protoClass}">${isPocsag ? 'P' : 'F'}</span>
                    <span class="pdir-addr">${esc(addr)}</span>
                    <span class="pdir-new-dot"></span>
                    <span class="pdir-count">×${data.count}</span>
                </div>
                <div class="pdir-bar-wrap"><div class="pdir-bar ${barClass}" style="width:${pct}%"></div></div>
                <div class="pdir-age">${formatAge(data.lastSeen)}</div>`;

            if (!el) {
                el = document.createElement('div');
                el.className = 'pdir-entry';
                el.dataset.pdirAddr = addr;
                el.addEventListener('click', () => toggleHighlight(addr));
                entriesEl.appendChild(el);
            }
            el.classList.toggle('pdir-entry--active', isActive);
            el.innerHTML = html;
        });

        // Re-order DOM to match sort
        sorted.forEach(([addr]) => {
            const el = entriesEl.querySelector(`[data-pdir-addr="${CSS.escape(addr)}"]`);
            if (el) entriesEl.appendChild(el);
        });
    }

    function flashNewDot(addr) {
        // Find the dot inside this entry after the current render frame
        setTimeout(() => {
            const entriesEl = document.getElementById('pagerDirEntries');
            const entry = entriesEl?.querySelector(`[data-pdir-addr="${CSS.escape(addr)}"]`);
            const dot = entry?.querySelector('.pdir-new-dot');
            if (!dot) return;
            dot.classList.remove('pdir-new-dot--active');
            void dot.offsetWidth; // force reflow to restart animation
            dot.classList.add('pdir-new-dot--active');
        }, 0);
    }

    // ---- Highlight ----

    function toggleHighlight(addr) {
        if (highlighted === addr) clearHighlight();
        else highlight(addr);
    }

    function highlight(addr) {
        highlighted = addr;
        renderDirectory();

        const feedLabel   = document.getElementById('pagerFeedLabel');
        const clearBtn    = document.getElementById('pagerClearHighlight');
        if (feedLabel) feedLabel.textContent = `${addr} highlighted`;
        if (clearBtn)  clearBtn.style.display = 'inline';

        const output = document.getElementById('output');
        if (!output) return;

        output.querySelectorAll('.signal-card').forEach(card => {
            card.classList.toggle('pdir-hl', card.dataset.address === addr);
        });

        const first = output.querySelector(`.signal-card[data-address="${CSS.escape(addr)}"]`);
        if (first) first.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function clearHighlight() {
        highlighted = null;
        renderDirectory();

        const feedLabel = document.getElementById('pagerFeedLabel');
        const clearBtn  = document.getElementById('pagerClearHighlight');
        if (feedLabel) feedLabel.textContent = 'All messages';
        if (clearBtn)  clearBtn.style.display = 'none';

        document.getElementById('output')
            ?.querySelectorAll('.pdir-hl')
            .forEach(c => c.classList.remove('pdir-hl'));
    }

    // ---- Public: message hook ----

    function addMessage(msg) {
        const addr  = msg.address;
        if (!addr) return;
        const proto = (msg.protocol || '').includes('FLEX') ? 'flex' : 'pocsag';
        const entry = addresses.get(addr);
        if (entry) {
            entry.count++;
            entry.lastSeen = Date.now();
            entry.protocol = proto;
        } else {
            addresses.set(addr, { count: 1, protocol: proto, lastSeen: Date.now() });
        }
        renderDirectory();
        flashNewDot(addr);
        // Re-apply highlight class to the newly inserted card (caller inserts it after this hook)
        if (highlighted === addr) {
            setTimeout(() => {
                const output = document.getElementById('output');
                output?.querySelectorAll(`.signal-card[data-address="${CSS.escape(addr)}"]`)
                    .forEach(c => c.classList.add('pdir-hl'));
            }, 0);
        }
    }

    // ---- Show / hide / reset ----

    function applyViewState(mode) {
        const dirPanel   = document.getElementById('pagerDirectoryView');
        const feedHeader = document.getElementById('pagerFeedHeader');

        if (mode === 'pager') {
            const saved = localStorage.getItem(STORAGE_KEY) || 'directory';
            const isDir = saved === 'directory';
            if (dirPanel)   dirPanel.style.display   = isDir ? 'flex' : 'none';
            if (feedHeader) feedHeader.style.display  = isDir ? 'flex' : 'none';
            _updateToggle(isDir);
        } else {
            if (dirPanel)   dirPanel.style.display   = 'none';
            if (feedHeader) feedHeader.style.display = 'none';
            clearHighlight();
        }
    }

    function show() {
        localStorage.setItem(STORAGE_KEY, 'directory');
        applyViewState('pager');
    }

    function hide() {
        localStorage.setItem(STORAGE_KEY, 'feed');
        applyViewState('pager');
    }

    function _updateToggle(isDir) {
        document.getElementById('pagerToggleDir')?.classList.toggle('view-toggle-btn--active', isDir);
        document.getElementById('pagerToggleFeed')?.classList.toggle('view-toggle-btn--active', !isDir);
    }

    function reset() {
        addresses.clear();
        highlighted = null;
        const entriesEl = document.getElementById('pagerDirEntries');
        const countEl   = document.getElementById('pagerDirCount');
        if (entriesEl) entriesEl.innerHTML = '';
        if (countEl)   countEl.textContent = '0';
        clearHighlight();
    }

    return { addMessage, highlight, clearHighlight, show, hide, reset, applyViewState };
})();
```

- [ ] **Step 2: Verify the script loads without errors**

With the dev server running, open `http://localhost:5000`, switch to pager mode in the UI. Check DevTools console — no errors. Open DevTools console and run `PagerDirectory` — should return the object (not `undefined`).

- [ ] **Step 3: Commit**

```bash
git add static/js/components/pager-directory.js
git commit -m "feat: add PagerDirectory JS component"
```

---

## Task 4: Sensor dashboard CSS

**Files:**
- Create: `static/css/components/sensor-dashboard.css`

- [ ] **Step 1: Create the CSS file**

Create `static/css/components/sensor-dashboard.css` with this exact content:

```css
/* ============================================================
   Sensor Dashboard View
   ============================================================ */
.sdb-view {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
    background: var(--bg-primary);
}

.sdb-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 8px;
    padding: 10px;
}

/* ---- Station card ---- */
.sdb-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: 10px;
    font-family: var(--font-mono);
    overflow: hidden;
}

.sdb-card--new {
    border-color: rgba(56, 193, 128, 0.3);
    animation: sdb-slide-in 0.4s ease-out;
}
@keyframes sdb-slide-in {
    from { opacity: 0; transform: translateY(-6px); }
    to   { opacity: 1; transform: none; }
}

.sdb-card--flash-blue {
    animation: sdb-flash-blue 0.8s ease-out;
}
@keyframes sdb-flash-blue {
    0%   { background: rgba(var(--accent-cyan-rgb), 0.10); border-color: rgba(var(--accent-cyan-rgb), 0.30); }
    100% { background: var(--bg-card); border-color: var(--border-color); }
}

.sdb-card--flash-purple {
    animation: sdb-flash-purple 0.8s ease-out;
}
@keyframes sdb-flash-purple {
    0%   { background: rgba(143, 123, 214, 0.10); border-color: rgba(143, 123, 214, 0.30); }
    100% { background: var(--bg-card); border-color: var(--border-color); }
}

/* ---- Card header ---- */
.sdb-card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 8px;
}
.sdb-name {
    font-size: var(--text-xs);
    color: var(--accent-cyan);
    font-weight: var(--font-semibold);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 120px;
}
.sdb-id     { font-size: 8px; color: var(--text-muted); margin-top: 1px; }
.sdb-age    { font-size: 8px; color: var(--text-muted); white-space: nowrap; }
.sdb-age--fresh { color: var(--accent-green); }

/* ---- Readings grid ---- */
.sdb-readings {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 8px;
    min-height: 36px;
    align-items: flex-end;
}
.sdb-reading     { text-align: center; min-width: 34px; }
.sdb-reading-val { font-size: 15px; font-weight: var(--font-bold); line-height: 1; }
.sdb-reading-unit  { font-size: 8px; color: var(--text-muted); }
.sdb-reading-label { font-size: 8px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 1px; }
.sdb-no-readings   { font-size: 9px; color: var(--text-muted); align-self: center; }

/* ---- State-only device ---- */
.sdb-state { display: flex; align-items: center; gap: 6px; min-height: 36px; }
.sdb-state-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.sdb-state-dot--on  { background: var(--accent-green); box-shadow: 0 0 5px var(--accent-green); }
.sdb-state-dot--off { background: var(--text-muted); }
.sdb-state-label    { font-size: 9px; color: var(--text-secondary); }

/* ---- Sparkline ---- */
.sdb-spark { margin-bottom: 6px; }
.sdb-spark svg { width: 100%; height: 22px; display: block; }
.sdb-spark-placeholder {
    height: 22px;
    background: var(--bg-secondary);
    border-radius: var(--radius-sm);
    display: flex;
    align-items: center;
    padding: 0 6px;
    font-size: 8px;
    color: var(--text-muted);
}

/* ---- Card footer ---- */
.sdb-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 8px;
}
.sdb-bat--ok  { color: var(--accent-green); }
.sdb-bat--low { color: var(--accent-red); }
.sdb-snr      { color: var(--text-muted); }
.sdb-freq {
    padding: 1px 4px;
    border-radius: var(--radius-sm);
    background: var(--bg-secondary);
    color: var(--text-muted);
}
```

- [ ] **Step 2: Verify styles load without errors**

With dev server running, DevTools → Network — confirm `sensor-dashboard.css` returns HTTP 200.

- [ ] **Step 3: Commit**

```bash
git add static/css/components/sensor-dashboard.css
git commit -m "feat: add sensor dashboard view CSS"
```

---

## Task 5: SensorDashboard JS component

**Files:**
- Create: `static/js/components/sensor-dashboard.js`

- [ ] **Step 1: Create the component file**

Create `static/js/components/sensor-dashboard.js` with this exact content:

```javascript
const SensorDashboard = (function () {
    'use strict';

    const STORAGE_KEY   = 'sensorView';
    const MAX_SPARK_PTS = 30;

    // Map<deviceKey, { card: HTMLElement, history: number[], primaryColor: string }>
    const devices = new Map();

    // ---- Helpers ----

    function esc(str) {
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function formatAge(timestamp) {
        if (!timestamp) return '';
        const ts = typeof timestamp === 'string' ? new Date(timestamp).getTime() : Number(timestamp);
        const s = Math.floor((Date.now() - ts) / 1000);
        if (s < 10) return 'just now';
        if (s < 60) return `${s}s ago`;
        return `${Math.floor(s / 60)}m ago`;
    }

    function isRecent(timestamp) {
        if (!timestamp) return false;
        const ts = typeof timestamp === 'string' ? new Date(timestamp).getTime() : Number(timestamp);
        return (Date.now() - ts) < 10000;
    }

    // ---- Primary value for sparkline ----

    function getPrimary(msg) {
        if (msg.temperature !== undefined)
            return { value: msg.temperature, color: '#f59e0b' };
        if (msg.pressure !== undefined)
            return { value: msg.pressure, color: '#a78bfa' };
        if (msg.wind_speed !== undefined)
            return { value: msg.wind_speed, color: '#4aa3ff' };
        return null;
    }

    function getFlashClass(msg) {
        return msg.temperature !== undefined ? 'sdb-card--flash-blue' : 'sdb-card--flash-purple';
    }

    // ---- HTML builders ----

    function buildReadingsHTML(msg) {
        // State-only device (no continuous numeric field)
        if (msg.state !== undefined && msg.temperature === undefined
                && msg.pressure === undefined && msg.wind_speed === undefined) {
            const raw = String(msg.state);
            const isOn = raw === '1' || raw === 'true' || raw === 'on' || raw === 'active';
            return `<div class="sdb-state">
                <span class="sdb-state-dot ${isOn ? 'sdb-state-dot--on' : 'sdb-state-dot--off'}"></span>
                <span class="sdb-state-label">${esc(raw.toUpperCase())}</span>
            </div>`;
        }

        const parts = [];
        if (msg.temperature !== undefined)
            parts.push({ val: msg.temperature, unit: `°${msg.temperature_unit || 'C'}`, label: 'Temp',   color: '#f59e0b' });
        if (msg.humidity !== undefined)
            parts.push({ val: msg.humidity,    unit: '%',                                label: 'Humid',  color: '#38bdf8' });
        if (msg.pressure !== undefined)
            parts.push({ val: msg.pressure,    unit: msg.pressure_unit || 'hPa',         label: 'Press',  color: '#a78bfa' });
        if (msg.wind_speed !== undefined)
            parts.push({ val: msg.wind_speed,  unit: msg.wind_unit || 'km/h',            label: 'Wind',   color: '#4aa3ff' });
        if (msg.rain !== undefined)
            parts.push({ val: msg.rain,        unit: msg.rain_unit || 'mm',              label: 'Rain',   color: '#38bdf8' });

        if (parts.length === 0)
            return `<div class="sdb-no-readings">No numeric data</div>`;

        return parts.map(p => `
            <div class="sdb-reading">
                <div class="sdb-reading-val" style="color:${p.color}">${p.val}</div>
                <div class="sdb-reading-unit">${esc(p.unit)}</div>
                <div class="sdb-reading-label">${p.label}</div>
            </div>`).join('');
    }

    function buildSparklineHTML(history, color) {
        if (history.length < 2)
            return `<div class="sdb-spark-placeholder">Collecting data…</div>`;

        const W = 120, H = 22, PAD = 2;
        const min = Math.min(...history);
        const max = Math.max(...history);
        const range = max - min || 1;
        const pts = history.map((v, i) => {
            const x = (i / (history.length - 1)) * (W - PAD * 2) + PAD;
            const y = H - PAD - ((v - min) / range) * (H - PAD * 2);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        const last = pts.split(' ').pop().split(',');
        return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <rect fill="var(--bg-secondary)" width="${W}" height="${H}"/>
            <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" opacity="0.85"/>
            <circle cx="${last[0]}" cy="${last[1]}" r="2" fill="${color}"/>
        </svg>`;
    }

    function buildCardHTML(msg, history, primaryColor) {
        const age     = formatAge(msg.timestamp);
        const fresh   = isRecent(msg.timestamp);
        const batOk   = msg.battery === 'OK';
        const batLow  = msg.battery === 'LOW';
        const sparkHTML = history.length > 0
            ? buildSparklineHTML(history, primaryColor || '#4aa3ff')
            : `<div class="sdb-spark-placeholder">Waiting for data…</div>`;

        return `
            <div class="sdb-card-header">
                <div>
                    <div class="sdb-name">${esc(msg.model || 'Unknown')}</div>
                    <div class="sdb-id">ID ${esc(String(msg.id || 'N/A'))}${msg.channel ? ` · Ch ${msg.channel}` : ''}</div>
                </div>
                <div class="sdb-age${fresh ? ' sdb-age--fresh' : ''}">${age}</div>
            </div>
            <div class="sdb-readings">${buildReadingsHTML(msg)}</div>
            <div class="sdb-spark">${sparkHTML}</div>
            <div class="sdb-footer">
                ${msg.battery ? `<span class="sdb-bat ${batLow ? 'sdb-bat--low' : 'sdb-bat--ok'}">● BAT ${esc(msg.battery)}</span>` : '<span></span>'}
                ${msg.snr !== undefined ? `<span class="sdb-snr">SNR ${msg.snr} dB</span>` : '<span></span>'}
                ${msg.frequency ? `<span class="sdb-freq">${esc(String(msg.frequency))}</span>` : '<span></span>'}
            </div>`;
    }

    // ---- Public: reading hook ----

    function addReading(msg) {
        const key = `${msg.model || 'Unknown'}_${msg.id || msg.channel || '0'}`;
        const primary = getPrimary(msg);

        if (devices.has(key)) {
            const dev = devices.get(key);
            if (primary) {
                dev.history.push(primary.value);
                if (dev.history.length > MAX_SPARK_PTS) dev.history.shift();
                dev.primaryColor = primary.color;
            }
            dev.card.innerHTML = buildCardHTML(msg, dev.history, dev.primaryColor);
            const cls = getFlashClass(msg);
            dev.card.classList.add(cls);
            setTimeout(() => dev.card.classList.remove(cls), 820);
        } else {
            const history = primary ? [primary.value] : [];
            const grid = document.getElementById('sensorDashboardGrid');
            if (!grid) return;
            const card = document.createElement('div');
            card.className = 'sdb-card sdb-card--new';
            card.innerHTML = buildCardHTML(msg, history, primary ? primary.color : '#4aa3ff');
            grid.insertBefore(card, grid.firstChild);
            setTimeout(() => card.classList.remove('sdb-card--new'), 2000);
            devices.set(key, { card, history, primaryColor: primary ? primary.color : '#4aa3ff' });
        }
    }

    // ---- Show / hide / reset ----

    function applyViewState(mode) {
        const view   = document.getElementById('sensorDashboardView');
        const output = document.getElementById('output');

        if (mode === 'sensor') {
            const saved = localStorage.getItem(STORAGE_KEY) || 'dashboard';
            const isDash = saved === 'dashboard';
            if (view)   view.style.display   = isDash ? 'block' : 'none';
            if (output) output.style.display = isDash ? 'none'  : '';
            _updateToggle(isDash);
        } else {
            if (view)   view.style.display   = 'none';
            if (output) output.style.display = '';
        }
    }

    function show() {
        localStorage.setItem(STORAGE_KEY, 'dashboard');
        applyViewState('sensor');
    }

    function hide() {
        localStorage.setItem(STORAGE_KEY, 'feed');
        applyViewState('sensor');
    }

    function _updateToggle(isDash) {
        document.getElementById('sensorToggleDash')?.classList.toggle('view-toggle-btn--active', isDash);
        document.getElementById('sensorToggleFeed')?.classList.toggle('view-toggle-btn--active', !isDash);
    }

    function reset() {
        devices.clear();
        const grid = document.getElementById('sensorDashboardGrid');
        if (grid) grid.innerHTML = '';
    }

    return { addReading, show, hide, reset, applyViewState };
})();
```

- [ ] **Step 2: Verify the script loads**

Open `http://localhost:5000` in a browser, switch to 433 mode, open DevTools console, run `SensorDashboard` — should return the object.

- [ ] **Step 3: Commit**

```bash
git add static/js/components/sensor-dashboard.js
git commit -m "feat: add SensorDashboard JS component"
```

---

## Task 6: Wire up hooks, mode integration, and reset

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add hook in `addMessage()`**

In `templates/index.html`, find this line inside `addMessage()` (around line 7247):
```javascript
            const msgEl = SignalCards.createPagerCard(msg);

            output.insertBefore(msgEl, output.firstChild);
```
Add the hook call immediately before `output.insertBefore`:
```javascript
            const msgEl = SignalCards.createPagerCard(msg);

            if (typeof PagerDirectory !== 'undefined') PagerDirectory.addMessage(msg);
            output.insertBefore(msgEl, output.firstChild);
```

- [ ] **Step 2: Add hook in `addSensorReading()`**

Find this line inside `addSensorReading()` (around line 5771):
```javascript
            const card = SignalCards.createSensorCard(msg);
            output.insertBefore(card, output.firstChild);
```
Replace with (the two `if` lines copy `snr`/`rssi` from raw `data` into `msg` so the dashboard footer can display them):
```javascript
            if (data.snr  !== undefined) msg.snr  = data.snr;
            if (data.rssi !== undefined) msg.rssi = data.rssi;
            const card = SignalCards.createSensorCard(msg);
            if (typeof SensorDashboard !== 'undefined') SensorDashboard.addReading(msg);
            output.insertBefore(card, output.firstChild);
```

- [ ] **Step 3: Add `applyViewState` calls in `switchMode()`**

Find these two lines in `switchMode()` (around line 4787):
```javascript
            document.getElementById('pagerStats')?.classList.toggle('active', mode === 'pager');
            document.getElementById('sensorStats')?.classList.toggle('active', mode === 'sensor');
```
Add immediately after:
```javascript
            if (typeof PagerDirectory  !== 'undefined') PagerDirectory.applyViewState(mode);
            if (typeof SensorDashboard !== 'undefined') SensorDashboard.applyViewState(mode);
```

- [ ] **Step 4: Add reset calls in `clearOutput()`**

Find these lines in the clear output function (around line 7346):
```javascript
            msgCount = 0;
            pocsagCount = 0;
            flexCount = 0;
            sensorCount = 0;
```
Add immediately before:
```javascript
            if (typeof PagerDirectory  !== 'undefined') PagerDirectory.reset();
            if (typeof SensorDashboard !== 'undefined') SensorDashboard.reset();
            msgCount = 0;
```

- [ ] **Step 5: Verify pager directory end-to-end**

Start `python intercept.py`, open `http://localhost:5000`, switch to Pager mode.

- The output header should show **Directory | Feed** toggle buttons.
- Click **Start Decoding** with a valid SDR connected (or wait for agent replay if available).
- On first message: an address entry should appear in the left panel with a green dot.
- Click that address entry: cards from that address get a blue left border; the feed header shows `"<addr> highlighted"`; clicking the same address again clears it.
- Click **Feed**: left panel disappears, classic cards fill the full width. Click **Directory**: panel returns.
- Refresh the page: the view preference is restored from localStorage.

If no SDR is available, manually invoke from the browser console to verify DOM updates:
```javascript
PagerDirectory.addMessage({ address: '1234567', protocol: 'POCSAG-1200', message: 'TEST', timestamp: new Date().toISOString() });
PagerDirectory.addMessage({ address: '9990001', protocol: 'FLEX',        message: 'HELLO', timestamp: new Date().toISOString() });
// Expect: two entries in the directory panel
PagerDirectory.highlight('1234567');
// Expect: first entry highlighted with blue border
```

- [ ] **Step 6: Verify sensor dashboard end-to-end**

Switch to 433 mode.

- The header should show **Dashboard | Feed** toggle buttons.
- Start Listening (or use console):
```javascript
SensorDashboard.addReading({ model: 'Acurite-Tower', id: 42, channel: 'A', temperature: 21.4, temperature_unit: 'C', humidity: 67, battery: 'OK', snr: 14, frequency: '433.92', timestamp: new Date().toISOString() });
SensorDashboard.addReading({ model: 'Acurite-Tower', id: 42, channel: 'A', temperature: 21.6, temperature_unit: 'C', humidity: 68, battery: 'OK', snr: 14, frequency: '433.92', timestamp: new Date().toISOString() });
// Expect: one card, second call updates in place with flash + sparkline with two points
SensorDashboard.addReading({ model: 'Interlogix-PIR', id: '0x44A', state: 'active', battery: 'OK', snr: 18, frequency: '433.92', timestamp: new Date().toISOString() });
// Expect: second card with green state dot
```
- Click **Feed**: dashboard hides, classic cards become visible. Click **Dashboard**: returns.
- Switching to a different mode (e.g. ADS-B) and back: dashboard is restored.

- [ ] **Step 7: Run full test suite**

```
pytest
```

Expected: all existing tests pass (no regressions from template edits).

- [ ] **Step 8: Commit**

```bash
git add templates/index.html
git commit -m "feat: wire PagerDirectory and SensorDashboard into pager and sensor modes"
```
