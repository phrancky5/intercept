/**
 * CAT (Computer Aided Transceiver) mode controller.
 *
 * Owns the /cat REST + SSE conversation. Subscribes once on init() and
 * unsubscribes on destroy(). All state is server-authoritative: the UI
 * sends commands and renders whatever the server pushes back on the SSE
 * channel.
 */
const CATMode = (function() {
    'use strict';

    let evtSource = null;
    let rigs = [];
    let supervisor = { tx_locked: true, band_guard: true, max_power_w: 0, bands: [] };
    let lastState = null;
    let initialized = false;

    function $(id) { return document.getElementById(id); }

    /* Persisted connection preferences (per rig_id).
     *
     * Stored in localStorage under "intercept.cat.prefs.v1" as a
     * `{rig_id: {port, baud, data_bits, stop_bits, parity, assert_rts,
     * assert_dtr}}` map. Restored when the rig picker changes and saved
     * on every successful Connect. localStorage is acceptable here —
     * these are UI-only operator preferences, not security-sensitive
     * state, and they belong on the workstation that runs the browser
     * (not the server) so two operators sharing one Intercept instance
     * keep their own COM-port choice.
     */
    const PREFS_KEY = 'intercept.cat.prefs.v1';
    function _readPrefs() {
        try { return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') || {}; }
        catch (_) { return {}; }
    }
    function _writePrefs(map) {
        try { localStorage.setItem(PREFS_KEY, JSON.stringify(map)); } catch (_) { /* quota / private mode */ }
    }
    function loadPrefs(rigId) {
        return _readPrefs()[rigId] || null;
    }
    function savePrefs(rigId) {
        if (!rigId) return;
        const all = _readPrefs();
        all[rigId] = {
            port: $('catPortSelect')?.value || '',
            baud: $('catBaudSelect')?.value || '',
            data_bits: $('catDataBits')?.value || '',
            stop_bits: $('catStopBits')?.value || '',
            parity: $('catParity')?.value || '',
            assert_rts: !!$('catAssertRts')?.checked,
            assert_dtr: !!$('catAssertDtr')?.checked,
        };
        _writePrefs(all);
    }
    function applyPrefs(rigId) {
        const p = loadPrefs(rigId);
        if (!p) return;
        const setIfPresent = (id, value) => {
            const el = $(id);
            if (!el || value === undefined || value === '') return;
            // For <select>, only assign if the option exists; otherwise leave default.
            if (el.tagName === 'SELECT') {
                if ([...el.options].some(o => o.value === String(value))) el.value = String(value);
            } else if (el.type === 'checkbox') {
                el.checked = !!value;
            } else {
                el.value = String(value);
            }
        };
        setIfPresent('catPortSelect', p.port);
        setIfPresent('catBaudSelect', p.baud);
        setIfPresent('catDataBits',  p.data_bits);
        setIfPresent('catStopBits',  p.stop_bits);
        setIfPresent('catParity',    p.parity);
        setIfPresent('catAssertRts', p.assert_rts);
        setIfPresent('catAssertDtr', p.assert_dtr);
    }

    function setStatus(text, cls) {
        const el = $('catConnStatus');
        if (el) {
            el.textContent = text;
            el.classList.remove('connected', 'disconnected', 'tx');
            if (cls) el.classList.add(cls);
        }
        const wrap = document.querySelector('.cat-vis-status');
        const lbl = $('catStatusLabel');
        if (wrap) {
            wrap.classList.remove('connected', 'tx');
            if (cls === 'connected') wrap.classList.add('connected');
            if (cls === 'tx') wrap.classList.add('tx');
        }
        if (lbl) lbl.textContent = text;
    }

    function setRigBadge() {
        const badge = $('catRigBadge');
        if (!badge) return;
        const rig = selectedRig();
        badge.textContent = rig ? rig.display_name.toUpperCase() : '—';
        const sum = $('catConnectSummary');
        if (sum) {
            if (!rig) { sum.textContent = 'choose rig & port'; return; }
            const connected = !!(lastState && lastState.connected);
            const port = $('catPortSelect')?.value || (connected ? '' : 'no port');
            const baud = $('catBaudSelect')?.value || '';
            const tail = port
                ? `${port}${baud ? ' @ ' + baud : ''}`
                : (baud ? `@ ${baud}` : '');
            sum.textContent = connected
                ? `${rig.display_name} · connected${tail ? ' · ' + tail : ''}`
                : `${rig.display_name} · ${tail || 'choose port'}`;
        }
    }

    function termAppend(kind, text) {
        const term = $('catTerminal');
        if (!term) return;
        const stamp = new Date().toTimeString().slice(0, 8);
        const prefix = kind === 'tx' ? '→' : kind === 'rx' ? '←' : kind === 'err' ? '!' : '·';
        const span = document.createElement('span');
        span.className = kind;
        span.textContent = `[${stamp}] ${prefix} ${text}\n`;
        term.appendChild(span);
        // Cap to ~500 lines.
        while (term.childNodes.length > 500) term.removeChild(term.firstChild);
        const auto = $('catAutoscroll');
        if (!auto || auto.checked) term.scrollTop = term.scrollHeight;
    }

    function appendIo(direction, payload) {
        termAppend(direction === 'tx' ? 'tx' : 'rx', payload);
    }

    function clearTerminal() {
        const term = $('catTerminal');
        if (term) term.textContent = '';
    }

    function renderState(state) {
        lastState = state;
        const box = $('catStateBox');
        if (!box) return;
        if (!state || !state.connected) {
            box.textContent = '(disconnected)';
            setRigBadge();
            return;
        }
        const fmt = (hz) => Number(hz || 0).toLocaleString('en-US');
        const lines = [
            `VFO A: ${fmt(state.vfo_a_hz)} Hz`,
            `VFO B: ${fmt(state.vfo_b_hz)} Hz`,
            `Active VFO: ${state.active_vfo}    Mode: ${state.mode}`,
            `Split: ${state.split ? 'on' : 'off'}    RIT: ${state.rit_on ? 'on' : 'off'} (${state.rit_hz} Hz)`,
            `PTT: ${state.ptt ? 'TX' : 'RX'}    S: ${state.s_meter}`,
        ];
        if (state.agc >= 0) lines.push(`AGC: ${state.agc}`);
        if (state.af_gain >= 0) lines.push(`AF: ${state.af_gain}    RF: ${state.rf_gain}    SQL: ${state.squelch}`);
        if (state.power_w >= 0) lines.push(`Power: ${state.power_w} W`);
        if (state.keyer_wpm >= 0) lines.push(`Keyer: ${state.keyer_wpm} wpm`);
        box.textContent = lines.join('\n');

        // Reflect into form controls (but don't fight typing).
        if (document.activeElement?.id !== 'catVfoAHz') $('catVfoAHz').value = state.vfo_a_hz;
        if (document.activeElement?.id !== 'catVfoBHz') $('catVfoBHz').value = state.vfo_b_hz;
        const modeSel = $('catModeSelect');
        if (modeSel && document.activeElement !== modeSel) modeSel.value = state.mode;
        $('catSplit').checked = !!state.split;
        $('catRit').checked = !!state.rit_on;

        setStatus(
            state.ptt ? 'Connected — TX' : 'Connected',
            state.ptt ? 'tx' : 'connected'
        );
        setRigBadge();
    }

    function renderSupervisor(sv) {
        supervisor = sv || supervisor;
        $('catSupTxLocked').checked = !!supervisor.tx_locked;
        $('catSupBandGuard').checked = !!supervisor.band_guard;
        $('catSupMaxPower').value = supervisor.max_power_w || 0;
    }

    async function api(path, opts) {
        const init = Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {});
        const r = await fetch(path, init);
        let data = null;
        try { data = await r.json(); } catch (_) { data = null; }
        if (!r.ok) {
            const msg = (data && (data.message || data.error)) || `HTTP ${r.status}`;
            throw new Error(msg);
        }
        return data;
    }

    async function refreshRigs() {
        const data = await api('/cat/rigs');
        rigs = data.rigs || [];
        const sel = $('catRigSelect');
        if (!sel) return;
        sel.innerHTML = '';
        rigs.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r.rig_id;
            opt.textContent = r.implemented ? r.display_name : `${r.display_name} (driver coming soon)`;
            if (!r.implemented) opt.disabled = true;
            sel.appendChild(opt);
        });
        sel.value = data.selected;
        sel.onchange = () => onRigChange();
        onRigChange();
        setRigBadge();
    }

    function selectedRig() {
        const id = $('catRigSelect')?.value;
        return rigs.find(r => r.rig_id === id);
    }

    function onRigChange() {
        const rig = selectedRig();
        if (!rig) return;
        $('catRigNotes').textContent = rig.notes || '';
        const baudSel = $('catBaudSelect');
        baudSel.innerHTML = '';
        (rig.supported_bauds || []).forEach(b => {
            const opt = document.createElement('option');
            opt.value = b; opt.textContent = String(b);
            if (b === rig.default_baud) opt.selected = true;
            baudSel.appendChild(opt);
        });
        // Preset framing from the descriptor (most rigs are 8N1; TS-850 is 8N2).
        if (rig.data_bits) $('catDataBits').value = String(rig.data_bits);
        if (rig.stop_bits) $('catStopBits').value = String(rig.stop_bits);
        if (rig.parity)    $('catParity').value   = String(rig.parity);
        api('/cat/select', { method: 'POST', body: JSON.stringify({ rig_id: rig.rig_id }) })
            .catch(err => console.warn('[CAT] select failed:', err));
        // Restore this rig's saved serial prefs (port/baud/RTS/DTR/framing).
        // Must run *after* the baud options have been populated above so
        // the <select> can actually accept the persisted value.
        applyPrefs(rig.rig_id);
        setRigBadge();
        if (initialized) Macros.refresh().catch(() => {});
    }

    async function refreshPorts() {
        const data = await api('/cat/ports');
        const sel = $('catPortSelect');
        if (!sel) return;
        const previous = sel.value;
        sel.innerHTML = '';
        (data.ports || []).forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.device;
            opt.textContent = p.description ? `${p.device} — ${p.description}` : p.device;
            sel.appendChild(opt);
        });
        if (previous) sel.value = previous;
        // After the port list has been populated, re-apply this rig's
        // persisted port choice (the initial onRigChange may have run
        // before /cat/ports resolved and could not set a value that
        // didn't exist yet).
        const rig = selectedRig();
        if (rig) {
            const p = loadPrefs(rig.rig_id);
            if (p && p.port && [...sel.options].some(o => o.value === p.port)) {
                sel.value = p.port;
            }
            setRigBadge();
        }
    }

    async function refreshSupervisor() {
        const sv = await api('/cat/supervisor');
        renderSupervisor(sv);
    }

    async function refreshStatus() {
        const data = await api('/cat/status');
        if (data.supervisor) renderSupervisor(data.supervisor);
        if (data.state) renderState(data.state);
        else setStatus('Disconnected.', 'disconnected');
        if (typeof data.polling_enabled === 'boolean') {
            const cb = $('catPollingToggle');
            if (cb) cb.checked = data.polling_enabled;
        }
    }

    function openStream() {
        closeStream();
        evtSource = new EventSource('/cat/stream');
        evtSource.onmessage = (ev) => {
            let msg;
            try { msg = JSON.parse(ev.data); } catch (_) { return; }
            if (msg.type === 'state') renderState(msg.state);
            else if (msg.type === 'supervisor') renderSupervisor(msg.supervisor);
            else if (msg.type === 'io') appendIo(msg.direction, msg.payload);
            else if (msg.type === 'lifecycle') {
                if (msg.event === 'disconnected') {
                    renderState(null);
                    setStatus('Disconnected.', 'disconnected');
                }
            }
        };
        evtSource.onerror = () => { /* browser will auto-retry */ };
    }

    function closeStream() {
        if (evtSource) { try { evtSource.close(); } catch (_) {} evtSource = null; }
    }

    // --- public actions ---
    async function connect() {
        const rig = selectedRig();
        if (!rig) return;
        const port = $('catPortSelect').value;
        if (!port) { setStatus('Pick a serial port first.', 'disconnected'); return; }
        const baud = parseInt($('catBaudSelect').value, 10);
        try {
            await api('/cat/connect', {
                method: 'POST',
                body: JSON.stringify({
                    rig_id: rig.rig_id, port, baud,
                    assert_rts: $('catAssertRts').checked,
                    assert_dtr: $('catAssertDtr').checked,
                    data_bits: parseInt($('catDataBits').value, 10),
                    stop_bits: parseInt($('catStopBits').value, 10),
                    parity: $('catParity').value,
                }),
            });
            setStatus('Connected.', 'connected');
            // Persist successful connection prefs so the next open of CAT
            // mode comes up with the same port/baud/RTS/DTR already set.
            savePrefs(rig.rig_id);
            const panel = $('catConnectPanel');
            if (panel) panel.open = false;
            refreshStatus();
            setRigBadge();
        } catch (err) {
            setStatus(`Connect failed: ${err.message}`, 'disconnected');
        }
    }

    async function disconnect() {
        let cooldown = 0;
        try {
            const data = await api('/cat/disconnect', { method: 'POST' });
            cooldown = (data && data.cooldown_ms) || 0;
        } catch (_) {}
        setStatus('Disconnected.', 'disconnected');
        renderState(null);
        const panel = $('catConnectPanel');
        if (panel) panel.open = true;
        setRigBadge();
        if (cooldown > 0) startConnectCooldown(cooldown);
    }

    let _cooldownTimer = null;
    function startConnectCooldown(ms) {
        const btn = $('catConnectBtn');
        if (!btn) return;
        if (_cooldownTimer) { clearInterval(_cooldownTimer); _cooldownTimer = null; }
        const originalLabel = btn.dataset.origLabel || btn.textContent;
        btn.dataset.origLabel = originalLabel;
        btn.disabled = true;
        const endsAt = Date.now() + ms;
        const tick = () => {
            const remaining = Math.max(0, endsAt - Date.now());
            if (remaining <= 0) {
                btn.disabled = false;
                btn.textContent = originalLabel;
                clearInterval(_cooldownTimer);
                _cooldownTimer = null;
                return;
            }
            btn.textContent = `Wait ${(remaining / 1000).toFixed(1)}s`;
        };
        tick();
        _cooldownTimer = setInterval(tick, 100);
    }

    async function togglePolling(enabled) {
        try {
            const data = await api('/cat/polling', { method: 'POST', body: JSON.stringify({ enabled: !!enabled }) });
            termAppend('sys', `polling ${data.enabled ? 'enabled' : 'disabled'}`);
        } catch (err) {
            termAppend('err', `polling toggle failed: ${err.message}`);
            // Revert checkbox on failure.
            const cb = $('catPollingToggle');
            if (cb) cb.checked = !enabled;
        }
    }

    async function setVfo(which) {
        const hz = parseInt($(`catVfo${which}Hz`).value, 10);
        if (!Number.isFinite(hz)) return;
        try { await api('/cat/vfo', { method: 'POST', body: JSON.stringify({ which, hz }) }); }
        catch (err) { alert(`VFO ${which}: ${err.message}`); }
    }

    async function selectVfo(which) {
        try { await api('/cat/vfo', { method: 'POST', body: JSON.stringify({ which, select: true }) }); }
        catch (err) { console.warn('[CAT] select VFO:', err); }
    }

    async function setMode(mode) {
        try { await api('/cat/mode', { method: 'POST', body: JSON.stringify({ mode }) }); }
        catch (err) { console.warn('[CAT] setMode:', err); }
    }

    async function setSplit(on) {
        try { await api('/cat/split', { method: 'POST', body: JSON.stringify({ on }) }); }
        catch (err) { console.warn('[CAT] setSplit:', err); }
    }

    async function setRit(on) {
        try { await api('/cat/rit', { method: 'POST', body: JSON.stringify({ on }) }); }
        catch (err) { console.warn('[CAT] setRit:', err); }
    }

    async function clearRit() {
        try { await api('/cat/rit/clear', { method: 'POST' }); }
        catch (err) { console.warn('[CAT] clearRit:', err); }
    }

    async function sendRaw() {
        const input = $('catRawCmd');
        let cmd = (input?.value || '').trim();
        if (!cmd) return;
        if (!cmd.endsWith(';')) cmd += ';';
        termAppend('tx', cmd);
        try {
            const data = await api('/cat/raw', { method: 'POST', body: JSON.stringify({ cmd }) });
            if (data.response) termAppend('rx', data.response);
            if (input) input.value = '';
        } catch (err) {
            termAppend('err', `raw failed: ${err.message}`);
        }
    }

    async function probe() {
        const port = $('catPortSelect')?.value;
        if (!port) { termAppend('err', 'Pick a serial port first.'); return; }
        const baud = parseInt($('catBaudSelect')?.value, 10) || 4800;
        const body = {
            port, baud,
            assert_rts: $('catAssertRts')?.checked || false,
            assert_dtr: $('catAssertDtr')?.checked || false,
            data_bits: parseInt($('catDataBits')?.value, 10) || 8,
            stop_bits: parseInt($('catStopBits')?.value, 10) || 1,
            parity: $('catParity')?.value || 'N',
        };
        termAppend('sys', `probe → ${port} @ ${baud} ${body.data_bits}${body.parity}${body.stop_bits} (AI0; ID; IF; FA;)`);
        const btn = $('catProbeBtn');
        if (btn) btn.disabled = true;
        try {
            const data = await api('/cat/probe', { method: 'POST', body: JSON.stringify(body) });
            (data.queries || []).forEach(q => {
                termAppend('tx', q.sent);
                const tag = q.looks_valid ? 'rx' : 'err';
                const rx = q.received_ascii || `(0 bytes)`;
                termAppend(tag, `${rx}   [${q.received_bytes}B hex=${q.received_hex || '—'}]`);
            });
            termAppend('sys', `verdict: ${data.verdict}`);
            termAppend('sys', `total ${data.total_rx_bytes} bytes, ${data.valid_frames}/${(data.queries||[]).length} valid frames`);
        } catch (err) {
            termAppend('err', `probe failed: ${err.message}`);
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function updateSupervisor() {
        const payload = {
            tx_locked: $('catSupTxLocked').checked,
            band_guard: $('catSupBandGuard').checked,
            max_power_w: parseInt($('catSupMaxPower').value, 10) || 0,
        };
        try {
            const sv = await api('/cat/supervisor', { method: 'POST', body: JSON.stringify(payload) });
            renderSupervisor(sv);
        } catch (err) {
            alert(`Supervisor: ${err.message}`);
        }
    }

    function init() {
        if (initialized) {
            // Re-entering the mode — just refresh.
            refreshStatus().catch(() => {});
            setRigBadge();
            Macros.refresh().catch(() => {});
            return;
        }
        initialized = true;
        Promise.all([refreshRigs(), refreshPorts(), refreshSupervisor(), refreshStatus()])
            .catch(err => console.warn('[CAT] init:', err));
        openStream();
        const raw = $('catRawCmd');
        if (raw && !raw.dataset.bound) {
            raw.dataset.bound = '1';
            raw.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter') { ev.preventDefault(); sendRaw(); }
            });
        }
        // Live-save serial prefs whenever the operator changes a field —
        // not just on Connect — so the next session starts with the same
        // settings even if the user never clicked Connect.
        const liveSave = () => {
            const rig = selectedRig();
            if (rig) savePrefs(rig.rig_id);
            setRigBadge();
        };
        ['catPortSelect', 'catBaudSelect', 'catDataBits', 'catStopBits',
         'catParity', 'catAssertRts', 'catAssertDtr'].forEach(id => {
            const el = $(id);
            if (el && !el.dataset.prefBound) {
                el.dataset.prefBound = '1';
                el.addEventListener('change', liveSave);
            }
        });
        Macros.init();
    }

    function destroy() {
        closeStream();
    }

    function isActive() { return !!evtSource; }

    /* --------------------------------------------------------------------
     *  Macro Builder
     *  Talks to /cat/commands and /cat/macros. State is kept in-module so
     *  re-entering the CAT mode doesn't lose an in-progress edit.
     * ------------------------------------------------------------------ */
    const Macros = (function () {
        const state = {
            commands: [],
            categories: [],
            macros: [],
            steps: [],
            currentMacroId: null,
            bound: false,
            loaded: false,
        };

        function rigId() {
            return ($('catRigSelect')?.value) || 'kenwood_ts850';
        }

        function setMsg(text, ok = true) {
            const edit = $('catMacEditPanel');
            const inEdit = edit && !edit.hidden;
            const el = inEdit ? $('catMacMsgEdit') : $('catMacMsg');
            if (!el) return;
            el.textContent = text || '';
            el.style.color = ok ? '' : 'var(--accent-red, #ff7070)';
        }

        function esc(s) {
            return String(s == null ? '' : s)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }

        async function api(method, url, body) {
            const opts = { method, headers: { 'Content-Type': 'application/json' } };
            if (body !== undefined) opts.body = JSON.stringify(body);
            const r = await fetch(url, opts);
            const data = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(data.message || data.error || `HTTP ${r.status}`);
            return data;
        }

        async function refresh() {
            const rig = rigId();
            try {
                const [cmds, macs] = await Promise.all([
                    api('GET', `/cat/commands?rig_id=${encodeURIComponent(rig)}`),
                    api('GET', `/cat/macros?rig_id=${encodeURIComponent(rig)}`),
                ]);
                state.commands = cmds.commands || [];
                state.categories = cmds.categories || [];
                state.macros = macs.macros || [];
                state.loaded = true;
                renderCategorySelect();
                renderCommands();
                renderMacroList();
            } catch (err) {
                setMsg(`Load failed: ${err.message}`, false);
            }
        }

        function renderCategorySelect() {
            const sel = $('catMacCategory');
            if (!sel) return;
            const current = sel.value;
            sel.innerHTML = '<option value="">all categories</option>';
            for (const c of state.categories) {
                const o = document.createElement('option');
                o.value = c; o.textContent = c;
                sel.appendChild(o);
            }
            if (current) sel.value = current;
        }

        function filteredCommands() {
            const q = ($('catMacFilter')?.value || '').toLowerCase().trim();
            const cat = $('catMacCategory')?.value || '';
            return state.commands.filter(c => {
                if (cat && c.category !== cat) return false;
                if (!q) return true;
                return (c.name || '').toLowerCase().includes(q)
                    || (c.raw_template || '').toLowerCase().includes(q)
                    || (c.description || '').toLowerCase().includes(q);
            });
        }

        function renderCommands() {
            const list = $('catMacCmdList');
            if (!list) return;
            const rows = filteredCommands();
            if (!rows.length) {
                list.innerHTML = '<div class="cat-vis-macro-empty">No commands. Built-ins are seeded on first startup.</div>';
                return;
            }
            list.innerHTML = '';
            for (const c of rows) {
                const row = document.createElement('div');
                row.className = 'cat-vis-cmd-row';
                row.title = c.description || '';
                row.innerHTML =
                    `<span class="cat-vis-cmd-cat">${esc(c.category)}</span>` +
                    `<span class="cat-vis-cmd-name">${esc(c.name)}</span>` +
                    `<span class="cat-vis-cmd-template">${esc(c.raw_template)}</span>`;
                row.addEventListener('click', () => {
                    if ($('catMacEditPanel')?.hidden) enterEditMode();
                    addStepFromCommand(c);
                });
                list.appendChild(row);
            }
        }

        function renderMacroList() {
            const list = $('catMacList');
            if (!list) return;
            if (!state.macros.length) {
                list.innerHTML = '<div class="cat-vis-macro-empty">No macros saved yet. Click + New Macro to create one.</div>';
                return;
            }
            list.innerHTML = '';
            for (const m of state.macros) {
                const row = document.createElement('div');
                row.className = 'cat-vis-macro-row';
                row.innerHTML =
                    `<span class="cat-vis-macro-row-name">${esc(m.name)}</span>` +
                    `<button type="button" class="cat-vis-macro-btn cat-vis-macro-btn--run" title="Run">▶</button>` +
                    `<button type="button" class="cat-vis-macro-btn" title="Edit">✎</button>` +
                    `<button type="button" class="cat-vis-macro-btn cat-vis-macro-btn--del" title="Delete">✕</button>`;
                const [btnRun, btnEdit, btnDel] = row.querySelectorAll('button');
                btnRun.addEventListener('click', () => runSaved(m.id, m.name));
                btnEdit.addEventListener('click', () => loadForEdit(m.id));
                btnDel.addEventListener('click', () => removeMacro(m.id, m.name));
                list.appendChild(row);
            }
        }

        function renderSteps() {
            const list = $('catMacStepList');
            if (!list) return;
            list.innerHTML = '';
            if (!state.steps.length) {
                list.innerHTML = '<div class="cat-vis-macro-empty">Click a command on the left to add it to the sequence.</div>';
                return;
            }
            state.steps.forEach((s, idx) => {
                const row = document.createElement('div');
                row.className = 'cat-vis-step-row';
                const paramHtml = s.param_label
                    ? `<input type="text" class="cat-vis-step-param" value="${esc(s.param_value || '')}" placeholder="${esc(s.param_label)}">`
                    : '';
                row.innerHTML =
                    `<span class="cat-vis-step-idx">${idx + 1}.</span>` +
                    `<span class="cat-vis-step-name">${esc(s.name)}</span>` +
                    `<span class="cat-vis-step-tpl">${esc(s.raw_template)}</span>` +
                    paramHtml +
                    `<input type="number" class="cat-vis-step-delay" value="${s.delay_ms}" min="0" max="10000" step="50" title="delay after this step (ms)">` +
                    `<button type="button" class="cat-vis-macro-btn" title="up">↑</button>` +
                    `<button type="button" class="cat-vis-macro-btn" title="down">↓</button>` +
                    `<button type="button" class="cat-vis-macro-btn cat-vis-macro-btn--del" title="remove">✕</button>`;
                const inputs = row.querySelectorAll('input');
                const buttons = row.querySelectorAll('button');
                let i = 0;
                if (paramHtml) {
                    inputs[i].addEventListener('input', (e) => { s.param_value = e.target.value; });
                    i++;
                }
                inputs[i].addEventListener('input', (e) => { s.delay_ms = parseInt(e.target.value, 10) || 0; });
                buttons[0].addEventListener('click', () => { if (idx > 0) { swap(idx, idx - 1); renderSteps(); } });
                buttons[1].addEventListener('click', () => { if (idx < state.steps.length - 1) { swap(idx, idx + 1); renderSteps(); } });
                buttons[2].addEventListener('click', () => { state.steps.splice(idx, 1); renderSteps(); });
                list.appendChild(row);
            });
        }

        function swap(a, b) {
            const t = state.steps[a]; state.steps[a] = state.steps[b]; state.steps[b] = t;
        }

        function addStepFromCommand(c) {
            state.steps.push({
                command_id: c.id,
                name: c.name,
                raw_template: c.raw_template,
                param_label: c.param_label || null,
                param_value: c.param_default || '',
                delay_ms: 100,
            });
            renderSteps();
        }

        function enterEditMode(macroId = null) {
            $('catMacBrowsePanel').hidden = true;
            $('catMacEditPanel').hidden = false;
            state.currentMacroId = macroId;
            if (!macroId) {
                $('catMacName').value = '';
                state.steps = [];
            }
            setMsg('');
            renderSteps();
        }

        function enterBrowseMode() {
            $('catMacEditPanel').hidden = true;
            $('catMacBrowsePanel').hidden = false;
            state.currentMacroId = null;
            state.steps = [];
        }

        async function loadForEdit(macroId) {
            try {
                const data = await api('GET', `/cat/macros/${macroId}`);
                const m = data.macro;
                $('catMacName').value = m.name;
                state.steps = (m.steps || []).map(s => {
                    const c = state.commands.find(x => x.id === s.command_id);
                    return {
                        command_id: s.command_id,
                        name: c ? c.name : '(custom)',
                        raw_template: c ? c.raw_template : s.raw_command,
                        param_label: c ? c.param_label : null,
                        param_value: s.param_value || '',
                        delay_ms: s.delay_ms || 0,
                    };
                });
                enterEditMode(macroId);
            } catch (err) {
                setMsg(`Load failed: ${err.message}`, false);
            }
        }

        async function save() {
            const name = ($('catMacName')?.value || '').trim();
            if (!name) { setMsg('Macro needs a name', false); return; }
            if (!state.steps.length) { setMsg('Add at least one step', false); return; }
            try {
                await api('POST', '/cat/macros', {
                    rig_id: rigId(),
                    name,
                    steps: state.steps.map(s => ({
                        command_id: s.command_id,
                        param_value: s.param_value,
                        delay_ms: s.delay_ms,
                    })),
                });
                setMsg('Saved');
                await refresh();
                enterBrowseMode();
            } catch (err) {
                setMsg(`Save failed: ${err.message}`, false);
            }
        }

        async function runEditing() {
            // Save then run in one shot if there's no current macro.
            if (!state.currentMacroId) {
                await save();
                if (!state.currentMacroId) {
                    // After save, find the just-created macro by name.
                    const name = ($('catMacName')?.value || '').trim();
                    const m = state.macros.find(x => x.name === name);
                    if (!m) return;
                    state.currentMacroId = m.id;
                }
            }
            await runSaved(state.currentMacroId, $('catMacName')?.value);
        }

        async function runSaved(macroId, name) {
            try {
                const r = await api('POST', `/cat/macros/${macroId}/run`);
                if (r.ok) {
                    termAppend('sys', `macro "${name || macroId}" ran in ${r.elapsed_s}s (${r.steps.length} steps)`);
                    setMsg(`Ran in ${r.elapsed_s}s`);
                } else {
                    termAppend('err', `macro failed: ${r.error || 'unknown'}`);
                    setMsg(`Failed: ${r.error}`, false);
                }
            } catch (err) {
                termAppend('err', `macro error: ${err.message}`);
                setMsg(`Failed: ${err.message}`, false);
            }
        }

        async function removeMacro(macroId, name) {
            if (!confirm(`Delete macro "${name}"?`)) return;
            try {
                await api('DELETE', `/cat/macros/${macroId}`);
                await refresh();
            } catch (err) {
                setMsg(`Delete failed: ${err.message}`, false);
            }
        }

        function bindOnce() {
            if (state.bound) return;
            state.bound = true;
            $('catMacFilter')?.addEventListener('input', renderCommands);
            $('catMacCategory')?.addEventListener('change', renderCommands);
            $('catMacNewBtn')?.addEventListener('click', () => enterEditMode(null));
            $('catMacCancelBtn')?.addEventListener('click', enterBrowseMode);
            $('catMacSaveBtn')?.addEventListener('click', save);
            $('catMacRunBtn')?.addEventListener('click', runEditing);
            $('catMacClearBtn')?.addEventListener('click', () => { state.steps = []; renderSteps(); });
            // Lazy-load when panel is first opened.
            $('catMacroPanel')?.addEventListener('toggle', (ev) => {
                if (ev.target.open && !state.loaded) refresh().catch(() => {});
            });
        }

        function init() {
            bindOnce();
            if ($('catMacroPanel')?.open) refresh().catch(() => {});
        }

        return { init, refresh };
    })();

    return { init, destroy, isActive, connect, disconnect, refreshPorts,
             setVfo, selectVfo, setMode, setSplit, setRit, clearRit,
             sendRaw, updateSupervisor, probe, clearTerminal, refreshStatus,
             togglePolling };
})();
