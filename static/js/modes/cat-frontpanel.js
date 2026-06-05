/**
 * Generic virtual front panel controller for CAT mode.
 *
 * Rig skins are plain HTML partials served from /cat/frontpanel/<rig_id>.
 * A skin declares all of its interactivity through data-attributes so this
 * controller never needs rig-specific code -- adding a new transceiver is a
 * matter of authoring an HTML skin + CSS, with no JS changes here.
 *
 * Interaction contract (data-attributes on skin elements)
 * -------------------------------------------------------
 *   data-act="power"                          connect / disconnect
 *   data-act="ptt"                            toggle PTT (TX)
 *   data-act="vfo-dial"                       wheel = step, click = enter freq
 *   data-act="step"  data-dir="up|down"       nudge active VFO
 *   data-act="mode"  data-mode-a data-mode-b  toggle two modes (dual-label key)
 *   data-act="mode-set" data-mode="CW"        set one specific mode
 *   data-act="select-vfo" data-vfo="A|B"      select RX VFO
 *   data-act="split-toggle"                   toggle split TX/RX
 *   data-act="rit-toggle"                     toggle RIT
 *   data-act="filter-cycle"                   cycle filter slot 0->1->2
 *   data-act="nb-toggle"                      toggle noise blanker
 *   data-knob="squelch|power|af|rf|agc|atten|keyer"
 *                                             wheel = adjust, click = enter value
 *
 * State rendering contract (read from a RigState dict)
 * ----------------------------------------------------
 *   [data-role="freq"]            main VFO frequency (formatted MHz.kHz.hHz)
 *   [data-role="freq-unit"]       static unit label (left as-is)
 *   .fp-mode-chip[data-mode]      lit when state.mode matches
 *   [data-role="split"|"rit"|"xit"|"memch"|"offset"]   indicator chips
 *   [data-led="onair"]            lit on PTT
 *   [data-role="smeter-host"]     analog meter mount point
 *   [data-role="smeter-val"]      numeric S reading
 *   [data-role="smeter-dbm"]      dBm reading
 *   [data-knob-val="<knob>"]      numeric knob readouts
 *
 * Capability awareness
 * --------------------
 * Controls whose CAT capability is not advertised by the rig (see the CAT
 * registry) are visually disabled (.fp-disabled) and not wired, so e.g. the
 * TS-850's AF/RF/SQL knobs render but stay inert because that 1991 firmware
 * never exposed them over CAT.
 */
window.CATFrontPanel = (function () {
  'use strict';

  // ── Skin registry ────────────────────────────────────────────────────
  // rig_id -> { name, css: [stylesheet urls in load order] }.
  // The base stylesheet is always loaded first; skin-specific CSS layers on top.
  const BASE_CSS = '/static/css/modes/cat-frontpanel.css';
  const SKINS = {
    kenwood_ts850: { name: 'Kenwood TS-850S', css: ['/static/css/skins/ts850.css'] },
    // Future rigs register here, e.g.:
    // yaesu_ftx1:  { name: 'Yaesu FTX-1',  css: ['/static/css/skins/ftx1.css'] },
  };

  // Action / knob -> CAT capability tag required to wire it. 'power' (connect)
  // is always available and intentionally absent from this map.
  const ACT_CAP = {
    'ptt': 'ptt',
    'vfo-dial': 'vfo',
    'step': 'step',
    'mode': 'mode',
    'mode-set': 'mode',
    'select-vfo': 'vfo',
    'split-toggle': 'split',
    'rit-toggle': 'rit',
    'filter-cycle': 'filter',
    'nb-toggle': 'noise_blanker',
  };
  const KNOB_CFG = {
    squelch: { cap: 'squelch', path: '/cat/squelch', key: 'value', min: 0, max: 255, step: 5 },
    power: { cap: 'power', path: '/cat/power', key: 'watts', min: 0, max: 100, step: 5, suffix: ' W' },
    af: { cap: 'af_gain', path: '/cat/af_gain', key: 'value', min: 0, max: 255, step: 5 },
    rf: { cap: 'rf_gain', path: '/cat/rf_gain', key: 'value', min: 0, max: 255, step: 5 },
    agc: { cap: 'agc', path: '/cat/agc', key: 'value', min: 0, max: 2, step: 1 },
    atten: { cap: 'attenuator', path: '/cat/attenuator', key: 'step', min: 0, max: 3, step: 1 },
    keyer: { cap: 'keyer', path: '/cat/keyer', key: 'wpm', min: 5, max: 60, step: 1 },
  };

  // ── Module state ─────────────────────────────────────────────────────
  let container = null;     // host element the skin is injected into
  let ctx = null;           // host hooks { post, power, getState, toast }
  let caps = [];            // rig capability tags
  let rigId = null;
  let meter = null;         // CatSMeter instance
  let lastState = null;
  const knobLocal = {};     // knob name -> last numeric value (for relative wheel)
  let filterSlot = 0;
  let nbOn = false;

  function $(sel) { return container ? container.querySelector(sel) : null; }
  function $$(sel) { return container ? Array.from(container.querySelectorAll(sel)) : []; }

  function isSupported(id) { return !!SKINS[id]; }

  // ── Asset loading ────────────────────────────────────────────────────
  function ensureStyles(id) {
    const urls = [BASE_CSS].concat((SKINS[id] && SKINS[id].css) || []);
    urls.forEach(href => {
      if (document.querySelector(`link[data-fp-style="${href}"]`)) return;
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      link.dataset.fpStyle = href;
      document.head.appendChild(link);
    });
  }

  function ensureMeterScript() {
    if (window.CatSMeter) return Promise.resolve();
    const SRC = '/static/js/core/cat-smeter.js';
    if (document.querySelector(`script[data-fp-script="${SRC}"]`)) {
      // Already injected; poll briefly for it to finish parsing.
      return new Promise(resolve => {
        let n = 0;
        const t = setInterval(() => {
          if (window.CatSMeter || n++ > 50) { clearInterval(t); resolve(); }
        }, 40);
      });
    }
    return new Promise(resolve => {
      const s = document.createElement('script');
      s.src = SRC;
      s.dataset.fpScript = SRC;
      s.onload = () => resolve();
      s.onerror = () => resolve();
      document.body.appendChild(s);
    });
  }

  // ── Lightweight themed prompt (freq / value entry) ───────────────────
  function fpPrompt(title, opts) {
    opts = opts || {};
    return new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.className = 'cat-fp-prompt-overlay';
      overlay.innerHTML =
        '<div class="cat-fp-prompt-box">' +
        `<div class="cat-fp-prompt-title">${escapeHtml(title)}</div>` +
        (opts.desc ? `<div class="cat-fp-prompt-desc">${escapeHtml(opts.desc)}</div>` : '') +
        `<input class="cat-fp-prompt-input" type="text" value="${escapeHtml(String(opts.value ?? ''))}" ` +
        `placeholder="${escapeHtml(opts.placeholder || '')}">` +
        '<div class="cat-fp-prompt-actions">' +
        '<button type="button" class="cat-fp-prompt-cancel">Cancel</button>' +
        '<button type="button" class="cat-fp-prompt-ok">Apply</button>' +
        '</div></div>';
      document.body.appendChild(overlay);
      const input = overlay.querySelector('.cat-fp-prompt-input');
      const done = (val) => {
        overlay.remove();
        resolve(val);
      };
      overlay.querySelector('.cat-fp-prompt-ok').addEventListener('click', () => done(input.value));
      overlay.querySelector('.cat-fp-prompt-cancel').addEventListener('click', () => done(null));
      overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) done(null); });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); done(input.value); }
        if (e.key === 'Escape') { e.preventDefault(); done(null); }
      });
      requestAnimationFrame(() => { input.focus(); input.select(); });
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  // ── Helpers ──────────────────────────────────────────────────────────
  function connected() {
    return !!(lastState && lastState.connected);
  }
  function activeVfo() {
    return (lastState && lastState.active_vfo === 'B') ? 'B' : 'A';
  }
  function hasCap(cap) {
    return !cap || caps.indexOf(cap) >= 0;
  }
  function toast(msg, isErr) {
    if (ctx && typeof ctx.toast === 'function') ctx.toast(msg, isErr);
  }
  async function post(path, body) {
    try {
      return await ctx.post(path, body);
    } catch (err) {
      toast(`${path}: ${err.message}`, true);
      throw err;
    }
  }

  // Format Hz -> "MHz.kHz.hHz" (e.g. 27_805_000 -> "27.805.00").
  function fmtFreq(hz) {
    hz = Math.max(0, Math.round(Number(hz) || 0));
    const mhz = Math.floor(hz / 1e6);
    const khz = Math.floor((hz % 1e6) / 1e3).toString().padStart(3, '0');
    const sub = Math.floor((hz % 1e3) / 10).toString().padStart(2, '0');
    return `${mhz}.${khz}.${sub}`;
  }

  // ── Event binding (generic, capability-aware) ────────────────────────
  function bindActions() {
    $$('[data-act]').forEach(el => {
      const act = el.dataset.act;
      const cap = ACT_CAP[act];
      if (act !== 'power' && !hasCap(cap)) {
        el.classList.add('fp-disabled');
        el.title = (el.title ? el.title + '\n' : '') + 'Not CAT-controllable on this rig';
        return;
      }
      switch (act) {
        case 'power':
          el.addEventListener('click', () => ctx.power());
          el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ctx.power(); }
          });
          break;
        case 'ptt':
          el.addEventListener('click', () => post('/cat/ptt', { tx: !(lastState && lastState.ptt) }));
          break;
        case 'step':
          el.addEventListener('click', () => post('/cat/step', { direction: el.dataset.dir === 'down' ? 'down' : 'up' }));
          break;
        case 'mode':
          el.addEventListener('click', () => {
            const cur = (lastState && lastState.mode || '').toUpperCase();
            const a = (el.dataset.modeA || '').toUpperCase();
            const b = (el.dataset.modeB || '').toUpperCase();
            const next = cur === a ? (el.dataset.modeB || a) : (el.dataset.modeA || a);
            post('/cat/mode', { mode: next });
          });
          break;
        case 'mode-set':
          el.addEventListener('click', () => post('/cat/mode', { mode: el.dataset.mode }));
          break;
        case 'select-vfo':
          el.addEventListener('click', () => post('/cat/vfo', { which: el.dataset.vfo === 'B' ? 'B' : 'A', select: true }));
          break;
        case 'split-toggle':
          el.addEventListener('click', () => post('/cat/split', { on: !(lastState && lastState.split) }));
          break;
        case 'rit-toggle':
          el.addEventListener('click', () => post('/cat/rit', { on: !(lastState && lastState.rit_on) }));
          break;
        case 'filter-cycle':
          el.addEventListener('click', () => {
            filterSlot = (filterSlot + 1) % 3;
            post('/cat/filter', { slot: filterSlot });
          });
          break;
        case 'nb-toggle':
          el.addEventListener('click', () => {
            nbOn = !nbOn;
            el.classList.toggle('fp-on', nbOn);
            post('/cat/nb', { on: nbOn });
          });
          break;
        case 'vfo-dial':
          bindVfoDial(el);
          break;
        default:
          break;
      }
    });
  }

  function bindVfoDial(el) {
    el.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (!connected()) return;
      post('/cat/step', { direction: e.deltaY < 0 ? 'up' : 'down' });
    }, { passive: false });
    el.addEventListener('click', async () => {
      if (!connected()) { toast('Connect first (POWER ON)', true); return; }
      const which = activeVfo();
      const cur = which === 'B' ? (lastState.vfo_b_hz || 0) : (lastState.vfo_a_hz || 0);
      const v = await fpPrompt(`VFO ${which} Frequency`, {
        desc: 'Enter frequency in Hz',
        value: cur || 14250000,
        placeholder: '14250000',
      });
      if (v == null) return;
      const hz = parseInt(String(v).replace(/\D/g, ''), 10);
      if (Number.isFinite(hz)) post('/cat/vfo', { which, hz });
    });
  }

  function bindKnobs() {
    $$('[data-knob]').forEach(el => {
      const name = el.dataset.knob;
      const cfg = KNOB_CFG[name];
      if (!cfg) return;
      if (!hasCap(cfg.cap)) {
        el.classList.add('fp-disabled');
        el.title = (el.title ? el.title + '\n' : '') + 'Not CAT-controllable on this rig';
        return;
      }
      if (knobLocal[name] == null) knobLocal[name] = cfg.min;
      const readout = $(`[data-knob-val="${name}"]`);
      const paint = () => {
        if (readout) readout.textContent = knobLocal[name] + (cfg.suffix || '');
      };
      paint();
      el.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (!connected()) return;
        const delta = (e.deltaY < 0 ? 1 : -1) * cfg.step;
        knobLocal[name] = Math.max(cfg.min, Math.min(cfg.max, knobLocal[name] + delta));
        post(cfg.path, { [cfg.key]: knobLocal[name] });
        paint();
      }, { passive: false });
      el.addEventListener('click', async () => {
        if (!connected()) { toast('Connect first (POWER ON)', true); return; }
        const v = await fpPrompt(name.toUpperCase(), {
          desc: `Range ${cfg.min}\u2013${cfg.max}`,
          value: knobLocal[name],
        });
        if (v == null) return;
        const n = parseInt(v, 10);
        if (!Number.isFinite(n)) return;
        knobLocal[name] = Math.max(cfg.min, Math.min(cfg.max, n));
        post(cfg.path, { [cfg.key]: knobLocal[name] });
        paint();
      });
    });
  }

  // ── State rendering ──────────────────────────────────────────────────
  function setChip(role, on) {
    const el = $(`[data-role="${role}"]`);
    if (!el) return;
    if (on) el.removeAttribute('data-off');
    else el.setAttribute('data-off', '');
  }

  function applyState(state) {
    lastState = state;
    if (!container) return;

    const conn = !!(state && state.connected);
    container.dataset.connected = conn ? '1' : '0';

    // Power rocker visual
    $$('[data-act="power"]').forEach(el => el.classList.toggle('on', conn));

    if (!state || !conn) {
      const freqEl = $('[data-role="freq"]');
      if (freqEl) freqEl.textContent = '--.---.--';
      $$('.fp-mode-chip').forEach(c => c.setAttribute('data-off', ''));
      ['split', 'rit', 'xit', 'memch', 'offset'].forEach(r => setChip(r, false));
      $$('[data-led="onair"]').forEach(l => l.classList.remove('on'));
      $$('[data-act="ptt"]').forEach(b => b.classList.remove('tx'));
      const sv = $('[data-role="smeter-val"]'); if (sv) sv.textContent = '0';
      const sd = $('[data-role="smeter-dbm"]'); if (sd) sd.textContent = '--- dBm';
      if (meter) { meter.setRX(0); meter.setMode('rx'); }
      return;
    }

    // Frequency (active VFO)
    const fActive = state.active_vfo === 'B' ? state.vfo_b_hz : state.vfo_a_hz;
    const freqEl = $('[data-role="freq"]');
    if (freqEl) freqEl.textContent = fmtFreq(fActive);

    // Memory channel digit
    const memEl = $('[data-role="memch"]');
    if (memEl) {
      if (state.in_memory && typeof state.memory_ch === 'number' && state.memory_ch >= 0) {
        memEl.textContent = String(state.memory_ch).padStart(2, '0');
        memEl.removeAttribute('data-off');
      } else {
        memEl.textContent = '--';
        memEl.setAttribute('data-off', '');
      }
    }

    // RIT/XIT offset
    const offEl = $('[data-role="offset"]');
    if (offEl) {
      if ((state.rit_on || state.xit_on) && typeof state.rit_hz === 'number') {
        const sign = state.rit_hz >= 0 ? '+' : '-';
        offEl.textContent = `${sign}${Math.abs(state.rit_hz).toString().padStart(4, '0')}`;
        offEl.removeAttribute('data-off');
      } else {
        offEl.textContent = '----';
        offEl.setAttribute('data-off', '');
      }
    }

    // Indicator chips
    setChip('split', !!state.split);
    setChip('rit', !!state.rit_on);
    setChip('xit', !!state.xit_on);

    // Mode legend chips
    const mode = (state.mode || '').toUpperCase();
    $$('.fp-mode-chip').forEach(el => {
      const m = (el.dataset.mode || '').toUpperCase();
      if (m === mode) el.removeAttribute('data-off');
      else el.setAttribute('data-off', '');
    });
    // Dual-mode buttons highlight
    $$('[data-act="mode"]').forEach(b => {
      const a = (b.dataset.modeA || '').toUpperCase();
      const bb = (b.dataset.modeB || '').toUpperCase();
      b.classList.toggle('mode-a-active', mode === a);
      b.classList.toggle('mode-b-active', mode === bb);
    });

    // Active-VFO indicator
    $$('[data-role="vfo-active"]').forEach(el => {
      el.textContent = state.active_vfo === 'B' ? 'B' : 'A';
    });

    // S-meter
    const sRaw = Math.max(0, Math.min(15, Number(state.s_meter) || 0));
    const dBm = sRaw <= 9 ? -127 + 6 * sRaw : -73 + (sRaw - 9) * 10;
    if (meter) {
      if (state.ptt && typeof state.power_w === 'number' && state.power_w >= 0) {
        meter.setMode('pwr');
        meter.setPWR(state.power_w);
      } else {
        meter.setMode('rx');
        meter.setRX(sRaw);
      }
    }
    const sv = $('[data-role="smeter-val"]'); if (sv) sv.textContent = String(sRaw);
    const sd = $('[data-role="smeter-dbm"]');
    if (sd) sd.textContent = sRaw === 0 ? '\u2014' : `${dBm.toFixed(0)} dBm`;

    // PTT / ON AIR
    $$('[data-led="onair"]').forEach(l => l.classList.toggle('on', !!state.ptt));
    $$('[data-act="ptt"]').forEach(b => b.classList.toggle('tx', !!state.ptt));

    // Sync knob readouts from readback where present
    syncKnob('squelch', state.squelch);
    syncKnob('power', state.power_w);
    syncKnob('af', state.af_gain);
    syncKnob('rf', state.rf_gain);
    syncKnob('agc', state.agc);
    syncKnob('atten', state.attenuator);
    syncKnob('keyer', state.keyer_wpm);
    if (typeof state.filter_slot === 'number' && state.filter_slot >= 0) filterSlot = state.filter_slot;
    if (typeof state.nb === 'boolean') {
      nbOn = state.nb;
      $$('[data-act="nb-toggle"]').forEach(b => b.classList.toggle('fp-on', nbOn));
    }
  }

  function syncKnob(name, value) {
    if (typeof value !== 'number' || value < 0) return;
    const cfg = KNOB_CFG[name];
    if (!cfg) return;
    knobLocal[name] = value;
    const readout = $(`[data-knob-val="${name}"]`);
    if (readout) readout.textContent = value + (cfg.suffix || '');
  }

  // ── Spectrum stub ────────────────────────────────────────────────────
  function bindSpectrumStub() {
    const start = $('[data-act="spec-start"]');
    const stop = $('[data-act="spec-stop"]');
    const status = $('[data-role="spec-status"]');
    if (start) {
      start.addEventListener('click', () => {
        if (status) { status.textContent = 'panadapter integration coming soon'; status.classList.add('fp-spec-soon'); }
      });
    }
    if (stop) {
      stop.addEventListener('click', () => {
        if (status) { status.textContent = 'off'; status.classList.remove('fp-spec-soon'); }
      });
    }
  }

  // ── Lifecycle ────────────────────────────────────────────────────────
  async function mount(opts) {
    opts = opts || {};
    rigId = opts.rigId;
    container = opts.container;
    ctx = opts.ctx || {};
    caps = opts.caps || [];

    if (!container) throw new Error('CATFrontPanel.mount: container required');
    if (!isSupported(rigId)) {
      container.innerHTML =
        '<div class="cat-fp-unsupported">No virtual front panel skin for this rig yet.' +
        ' Use the Terminal view to control it.</div>';
      return false;
    }

    ensureStyles(rigId);
    await ensureMeterScript();

    // Fetch the skin HTML partial.
    let html = '';
    try {
      const r = await fetch(`/cat/frontpanel/${encodeURIComponent(rigId)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      html = await r.text();
    } catch (err) {
      container.innerHTML = `<div class="cat-fp-unsupported">Failed to load front panel: ${escapeHtml(err.message)}</div>`;
      return false;
    }
    container.innerHTML = html;

    // Mount the analog meter into its host, if the skin provides one.
    const meterHost = $('[data-role="smeter-host"]');
    if (meterHost && window.CatSMeter) {
      try { meter = window.CatSMeter.create(meterHost); }
      catch (e) { meter = null; }
    }

    bindActions();
    bindKnobs();
    bindSpectrumStub();

    // Paint with whatever state we already have.
    applyState(ctx.getState ? ctx.getState() : lastState);
    return true;
  }

  function unmount() {
    if (meter) { try { meter.destroy(); } catch (_) {} meter = null; }
    if (container) container.innerHTML = '';
    // Keep lastState so a remount repaints instantly.
  }

  return { isSupported, mount, unmount, applyState };
})();
