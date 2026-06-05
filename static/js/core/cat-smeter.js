/**
 * Analog multimeter widget for the CAT virtual front panel.
 *
 * Pure-SVG (no bitmap assets) replica of a classic transceiver multimeter
 * face with several stacked scales sharing one needle:
 *
 *   S    : S 1..9 + +20/+40/+60 dB (red over-scale)   -- RX signal
 *   PO   : 0..250 W forward power                       -- TX
 *   SWR  : 1 / 1.5 / 2 / 3 / inf                         -- TX tune
 *   Id   : 0..15 A drain current                        -- informational
 *   COMP : 0/10/20/30 dB compression                    -- PROC mode
 *
 * Vendor-neutral: any rig skin can mount it. Driven by the front panel
 * controller from RigState (s_meter for RX, power_w for TX).
 *
 * Usage:
 *   const m = window.CatSMeter.create(hostEl);
 *   m.setRX(sUnits);     // 0..15  (CAT S-meter raw)
 *   m.setPWR(watts);     // 0..250
 *   m.setMode('rx'|'pwr'|'swr'|'id'|'comp');
 *   m.destroy();
 */
(function () {
  'use strict';

  const W = 200, H = 100;
  const PIVOT_X = 100, PIVOT_Y = 110;
  const NEEDLE_LEN = 92;
  const SWEEP_DEG = 55;
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function _angleFor(v) {
    const clamped = Math.max(0, Math.min(1, v));
    const deg = -SWEEP_DEG + clamped * (2 * SWEEP_DEG);
    return deg * Math.PI / 180;
  }
  function _tickX(a, r) { return PIVOT_X + r * Math.sin(a); }
  function _tickY(a, r) { return PIVOT_Y - r * Math.cos(a); }

  function _tick(frag, v, label, opts) {
    const a = _angleFor(v);
    const t = document.createElementNS(SVG_NS, 'line');
    t.setAttribute('x1', _tickX(a, opts.rIn).toFixed(2));
    t.setAttribute('y1', _tickY(a, opts.rIn).toFixed(2));
    t.setAttribute('x2', _tickX(a, opts.rOut).toFixed(2));
    t.setAttribute('y2', _tickY(a, opts.rOut).toFixed(2));
    t.setAttribute('stroke', opts.color || '#9fb2c2');
    t.setAttribute('stroke-width', opts.thick ? '1.4' : '0.8');
    frag.appendChild(t);
    if (label != null) {
      const tx = document.createElementNS(SVG_NS, 'text');
      tx.setAttribute('x', _tickX(a, opts.rLbl).toFixed(2));
      tx.setAttribute('y', _tickY(a, opts.rLbl).toFixed(2));
      tx.setAttribute('text-anchor', 'middle');
      tx.setAttribute('dominant-baseline', 'middle');
      tx.setAttribute('font-size', opts.fs || '5');
      tx.setAttribute('font-family', '"Courier New", monospace');
      tx.setAttribute('fill', opts.color || '#9fb2c2');
      tx.textContent = label;
      frag.appendChild(tx);
    }
  }

  function _scale(frag, def) {
    def.stops.forEach(stop => {
      _tick(frag, stop.v, stop.label, {
        rOut: def.rOut, rIn: def.rIn, rLbl: def.rLbl,
        thick: !!stop.thick,
        color: stop.color || def.baseColor || '#9fb2c2',
        fs: def.fs || '5',
      });
    });
    if (def.name) {
      const a = _angleFor(0);
      const tx = document.createElementNS(SVG_NS, 'text');
      tx.setAttribute('x', (_tickX(a, def.rIn) - 6).toFixed(2));
      tx.setAttribute('y', _tickY(a, def.rIn).toFixed(2));
      tx.setAttribute('text-anchor', 'end');
      tx.setAttribute('dominant-baseline', 'middle');
      tx.setAttribute('font-size', '5');
      tx.setAttribute('font-family', '"Courier New", monospace');
      tx.setAttribute('fill', def.nameColor || '#c8d6e2');
      tx.setAttribute('font-weight', '700');
      tx.textContent = def.name;
      frag.appendChild(tx);
    }
  }

  function create(host) {
    if (!host) throw new Error('CatSMeter.create: host required');
    host.innerHTML = '';

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', `0 8 ${W} ${H - 8}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('aria-label', 'Analog multimeter');
    svg.classList.add('cat-fp-meter-svg');

    const bezel = document.createElementNS(SVG_NS, 'rect');
    bezel.setAttribute('x', '1'); bezel.setAttribute('y', '9');
    bezel.setAttribute('width', W - 2); bezel.setAttribute('height', H - 10);
    bezel.setAttribute('fill', 'none');
    bezel.setAttribute('stroke', 'rgba(0,0,0,0.25)');
    bezel.setAttribute('stroke-width', '0.4');
    svg.appendChild(bezel);

    // S-meter (RX) top row
    const sStops = [];
    for (let s = 1; s <= 9; s++) {
      sStops.push({ v: (s - 1) / 14, label: String(s), thick: s % 2 === 1 });
    }
    sStops.push({ v: (9.0 + 20 / 6 - 1) / 14, label: '+20', color: '#ff5050', thick: true });
    sStops.push({ v: (9.0 + 40 / 6 - 1) / 14, label: '+40', color: '#ff5050', thick: true });
    sStops.push({ v: 1.0, label: '+60', color: '#ff5050', thick: true });
    _scale(svg, { name: 'S', stops: sStops, rOut: 86, rIn: 82, rLbl: 76, fs: '5.5' });

    // PO (TX forward power)
    _scale(svg, {
      name: 'PO', rOut: 70, rIn: 66, rLbl: 60, fs: '5',
      stops: [
        { v: 0, label: '0', thick: true },
        { v: 25 / 250, label: '25' },
        { v: 50 / 250, label: '50', thick: true },
        { v: 100 / 250, label: '100' },
        { v: 150 / 250, label: '150', thick: true },
        { v: 200 / 250, label: '200' },
        { v: 1, label: '250W', thick: true },
      ],
    });

    // SWR (TX tune)
    _scale(svg, {
      name: 'SWR', rOut: 54, rIn: 50, rLbl: 44, fs: '5',
      stops: [
        { v: 0, label: '1', thick: true },
        { v: 0.35, label: '1.5', thick: true },
        { v: 0.55, label: '2', thick: true },
        { v: 0.78, label: '3', thick: true },
        { v: 1, label: '\u221e', thick: true, color: '#ff5050' },
      ],
    });

    // Id (drain current)
    _scale(svg, {
      name: 'Id', rOut: 38, rIn: 34, rLbl: 28, fs: '4.5',
      stops: [
        { v: 0, label: '0', thick: true },
        { v: 5 / 15, label: '5' },
        { v: 10 / 15, label: '10', thick: true },
        { v: 1, label: '15A', thick: true },
      ],
    });

    // COMP (compression)
    _scale(svg, {
      name: 'COMP', rOut: 24, rIn: 20, rLbl: 14, fs: '4.5',
      stops: [
        { v: 0, label: '0', thick: true },
        { v: 10 / 30, label: '10' },
        { v: 20 / 30, label: '20', thick: true },
        { v: 1, label: '30dB', thick: true },
      ],
    });

    const needle = document.createElementNS(SVG_NS, 'line');
    needle.setAttribute('x1', PIVOT_X); needle.setAttribute('y1', PIVOT_Y);
    needle.setAttribute('x2', PIVOT_X); needle.setAttribute('y2', PIVOT_Y - NEEDLE_LEN);
    needle.setAttribute('stroke', '#e8b84d');
    needle.setAttribute('stroke-width', '1.1');
    needle.setAttribute('stroke-linecap', 'round');
    needle.style.transformOrigin = `${PIVOT_X}px ${PIVOT_Y}px`;
    needle.style.transition = 'transform 220ms cubic-bezier(.25,.8,.3,1)';
    svg.appendChild(needle);

    const pivot = document.createElementNS(SVG_NS, 'circle');
    pivot.setAttribute('cx', PIVOT_X); pivot.setAttribute('cy', PIVOT_Y);
    pivot.setAttribute('r', '3');
    pivot.setAttribute('fill', '#c8d6e2');
    svg.appendChild(pivot);

    host.appendChild(svg);

    let curMode = 'rx';
    const lastVals = { rx: 0, pwr: 0, swr: 0, id: 0, comp: 0 };

    function _renderNeedle() {
      const v = lastVals[curMode] || 0;
      const deg = -SWEEP_DEG + v * (2 * SWEEP_DEG);
      needle.style.transform = `rotate(${deg.toFixed(2)}deg)`;
    }

    return {
      setRX(sRaw) {
        lastVals.rx = Math.max(0, Math.min(1, (Number(sRaw) || 0) / 14));
        if (curMode === 'rx') _renderNeedle();
      },
      setPWR(watts) {
        lastVals.pwr = Math.max(0, Math.min(1, (Number(watts) || 0) / 250));
        if (curMode === 'pwr') _renderNeedle();
      },
      setSWR(swr) {
        const x = Math.max(1, Number(swr) || 1);
        const v = x <= 1 ? 0
          : x <= 1.5 ? (x - 1) / 1.5 * 0.35
            : x <= 2 ? 0.35 + (x - 1.5) / 0.5 * 0.20
              : x <= 3 ? 0.55 + (x - 2) / 1.0 * 0.23
                : x <= 10 ? 0.78 + (x - 3) / 7.0 * 0.22
                  : 1;
        lastVals.swr = Math.min(1, v);
        if (curMode === 'swr') _renderNeedle();
      },
      setMode(mode) {
        if (['rx', 'pwr', 'swr', 'id', 'comp'].indexOf(mode) < 0) return;
        curMode = mode;
        _renderNeedle();
      },
      get mode() { return curMode; },
      destroy() { host.innerHTML = ''; },
      el: svg,
    };
  }

  window.CatSMeter = { create };
})();
