// ── Battery detail + charts ────────────────────────────────────────────────

const HIST_MAX = 10000;
const histData = [];  // [{ts, soc, voltage, current}, ...]

// domain: feste Achsen-Grenzen [min,max] oder null = automatisch aus Daten
const SERIES_DEF = {
  soc:      { color: '#22c55e', unit: '%',  label: 'SOC',       fmt: v => Math.round(v) + ' %',         domain: [0, 100] },
  voltage:  { color: '#06b6d4', unit: 'V',  label: 'Spannung',  fmt: v => v.toFixed(2) + ' V',          minSpan: 0.4, smooth: 0.04 },
  current:  { color: '#f97316', unit: 'A',  label: 'Strom',     fmt: v => v.toFixed(1) + ' A',          minSpan: 2.0, zero: true, smooth: 0.04 },
  solar:    { color: '#eab308', unit: 'W',  label: 'Solar',     fmt: v => Math.round(v) + ' W',         minSpan: 20,  zero: true, smooth: 0.12 },
  zelldiff: { color: '#a78bfa', unit: 'mV', label: 'Zelldiff.', fmt: v => Math.round(v * 1000) + ' mV', minSpan: 0.03, zero: true, smooth: 0.02 },
};

const CH_NAMES = ['Küche', 'Kartentisch', 'Salon', 'Achtkabine stbd'];

let chartSecondary = 'current';       // aktive Sekundär-Serie (rechte Achse) oder null
let chartRangeSec  = 1800;
let chartHoverPos  = null; // null=live, 0.0–1.0=scrub fraction
let _lastSolarW   = null;

function recordHistory(b) {
  const entry = { ts: Date.now() / 1000 };
  if (b.soc      != null) entry.soc      = b.soc;
  if (b.voltage  != null) entry.voltage  = b.voltage;
  if (b.current  != null) entry.current  = b.current;
  if (_lastSolarW   != null) entry.solar    = _lastSolarW;
  if (_lastZelldiff != null) entry.zelldiff = _lastZelldiff;
  if (Object.keys(entry).length < 2) return; // kein Datenwert — nicht pushen
  histData.push(entry);
  if (histData.length > HIST_MAX) histData.shift();
}

// Exponential Moving Average — glättet stark springende Werte (z.B. Strom)
function _ema(pts, key, alpha = 0.12) {
  let s = null;
  return pts.map(d => {
    const v = d[key];
    if (v == null) { s = null; return d; }
    s = (s === null) ? v : alpha * v + (1 - alpha) * s;
    const r = Object.assign({}, d); r[key] = s; return r;
  });
}

// Berechnet [lo,hi] Achsen-Domäne einer Serie aus den Werten + Definition
function _seriesDomain(vals, def) {
  if (def.domain) return def.domain.slice();
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (def.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  let span = hi - lo;
  if (def.minSpan && span < def.minSpan) {       // flache Linie nicht aufblasen
    const mid = (hi + lo) / 2;
    lo = mid - def.minSpan / 2; hi = mid + def.minSpan / 2;
  }
  const pad = (hi - lo) * 0.08 || 0.5;
  return [lo - pad, hi + pad];
}

function toggleSeries(key) {
  const prev = chartSecondary;
  chartSecondary = (chartSecondary === key) ? null : key;
  if (prev) $(`tog-${prev}`).classList.remove('active');
  if (chartSecondary) $(`tog-${chartSecondary}`).classList.add('active');
  renderCharts();
}

function setChartRange(btn, secs) {
  chartRangeSec = secs;
  chartHoverPos = null;
  document.querySelectorAll('.chart-range').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderCharts();
}

function renderChartLegend(pts, secPts, scrubTs) {
  const leg = $('chartLegend');
  if (!leg) return;
  const entries = [{ key: 'soc', src: pts }];
  if (chartSecondary) entries.push({ key: chartSecondary, src: secPts });
  leg.innerHTML = entries.map(({ key, src }) => {
    const def = SERIES_DEF[key];
    const ptsK = src.filter(d => d[key] != null);
    let displayVal = null;
    if (scrubTs != null && ptsK.length) {
      let cl = ptsK[0], md = Math.abs(ptsK[0].ts - scrubTs);
      ptsK.forEach(d => { const dist = Math.abs(d.ts - scrubTs); if (dist < md) { md = dist; cl = d; } });
      displayVal = cl[key];
    } else {
      displayVal = ptsK.at(-1)?.[key] ?? null;
    }
    return `<div class="chart-legend-item">
      <div class="chart-legend-dot" style="background:${def.color}"></div>
      <span>${def.label}</span>
      <span class="chart-legend-val" style="color:${def.color}">${displayVal != null ? def.fmt(displayVal) : '--'}</span>
    </div>`;
  }).join('');
}

function fmtAxisTime(ts, nowTs) {
  const d = nowTs - ts;
  if (d < 30) return 'jetzt';
  const date = new Date(ts * 1000);
  if (d < 86400)
    return date.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  return date.toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit' });
}

function fmtYVal(v, key) {
  if (key === 'soc')      return Math.round(v) + '%';
  if (key === 'voltage')  return v.toFixed(1) + 'V';
  if (key === 'current')  return v.toFixed(0) + 'A';
  if (key === 'zelldiff') return Math.round(v * 1000) + 'mV';
  return String(Math.round(v));
}

const CHART_PAD_L = 38, CHART_PAD_R = 40;

// Berechnet schöne gerundete Y-Achsen-Ticks
function _niceTicks(lo, hi, nTarget) {
  if (hi === lo) return [lo];
  const span = hi - lo;
  const rough = span / nTarget;
  const exp   = Math.pow(10, Math.floor(Math.log10(rough)));
  const frac  = rough / exp;
  const step  = frac < 1.5 ? exp : frac < 3.5 ? 2 * exp : frac < 7.5 ? 5 * exp : 10 * exp;
  const start = Math.ceil(lo / step - 1e-9) * step;
  const ticks = [];
  for (let v = start; v <= hi + step * 1e-9; v += step)
    ticks.push(parseFloat(v.toFixed(10)));
  return ticks;
}

// Zeichnet eine Linie mit lineTo — schnell, korrekt, kein Overshoot
// maxGapSec: Zeitlücken größer als dieser Wert trennen Segmente (verhindert lange Diagonalen)
function _buildSegs(pts, key, xOf, yOf, maxGapSec = 60) {
  let seg = [], segs = [], last = null, prevTs = null;
  pts.forEach(d => {
    const v = d[key];
    if (v == null) {
      if (seg.length) { segs.push(seg); seg = []; }
      prevTs = null;
      return;
    }
    if (prevTs !== null && (d.ts - prevTs) > maxGapSec) {
      if (seg.length) { segs.push(seg); seg = []; }
    }
    const p = { x: xOf(d.ts), y: yOf(v) };
    seg.push(p); last = p; prevTs = d.ts;
  });
  if (seg.length) segs.push(seg);
  return { segs, last };
}

function _smoothSeg(ctx, s) {
  if (!s.length) return;
  ctx.moveTo(s[0].x, s[0].y);
  for (let i = 1; i < s.length; i++) ctx.lineTo(s[i].x, s[i].y);
}

function _decimate(pts, maxPts) {
  if (pts.length <= maxPts) return pts;
  const out = [], step = pts.length / maxPts;
  for (let i = 0; i < maxPts; i++) {
    const lo = Math.floor(i * step), hi = Math.min(pts.length, Math.floor((i+1)*step));
    const bucket = pts.slice(lo, hi);
    const merged = { ts: bucket[Math.floor(bucket.length/2)].ts };
    for (const k of Object.keys(bucket[0])) {
      if (k === 'ts') continue;
      const vals = bucket.map(d => d[k]).filter(v => v != null);
      if (vals.length) merged[k] = vals.reduce((a,b) => a+b, 0) / vals.length;
    }
    out.push(merged);
  }
  return out;
}

function renderCharts() {
  const canvas = $('chartMain');
  if (!canvas) return;

  const now    = Date.now() / 1000;
  const cutoff = now - chartRangeSec;
  const pts    = _decimate(histData.filter(d => d.ts >= cutoff), 2000);

  const dpr = window.devicePixelRatio || 1;
  // offsetWidth ist immer ein Integer → kein Sub-Pixel-Wachstum beim Hover
  const W = canvas.offsetWidth;
  const H = canvas.offsetHeight;
  if (!W || !H) return;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const PAD_L = CHART_PAD_L, PAD_R = CHART_PAD_R, PAD_B = 20, PAD_T = 8;
  const CW = W - PAD_L - PAD_R, CH = H - PAD_B - PAD_T;

  ctx.fillStyle = '#1e293b';
  ctx.fillRect(0, 0, W, H);

  const tMin0  = now - chartRangeSec;
  const xOf    = ts => PAD_L + Math.max(0, Math.min(CW, ((ts - tMin0) / chartRangeSec) * CW));
  const scrubTs = chartHoverPos !== null ? tMin0 + chartHoverPos * chartRangeSec : null;

  // Sekundär-Punkte ggf. glätten (EMA): alpha kommt direkt aus SERIES_DEF
  const secDef   = chartSecondary ? SERIES_DEF[chartSecondary] : null;
  const secAlpha = typeof secDef?.smooth === 'number' ? secDef.smooth : 0.12;
  const secPts   = chartSecondary && secDef?.smooth
    ? _ema(pts, chartSecondary, secAlpha)
    : pts;

  renderChartLegend(pts, secPts, scrubTs);

  // Aktive Serien aufbauen: SOC fest links (0–100%), Sekundär-Serie rechts
  const active = [];

  {
    const vals = pts.map(d => d.soc).filter(v => v != null);
    if (vals.length >= 2) {
      const def = SERIES_DEF.soc;
      const yOf = v => PAD_T + CH * (1 - Math.max(0, Math.min(100, v)) / 100);
      active.push({ key: 'soc', def, lo: 0, hi: 100, yOf, sPts: pts });
    }
  }

  if (chartSecondary) {
    const key = chartSecondary, def = SERIES_DEF[key];
    const vals = secPts.map(d => d[key]).filter(v => v != null);
    if (vals.length >= 2) {
      const [lo, hi] = _seriesDomain(vals, def);
      const span = (hi - lo) || 1;
      const yOf = v => PAD_T + CH - ((Math.max(lo, Math.min(hi, v)) - lo) / span) * CH;
      active.push({ key, def, lo, hi, yOf, sPts: secPts });
    }
  }

  const yLeft  = active.find(a => a.key === 'soc') ?? active[0] ?? null;
  const yRight = active.find(a => a.key !== 'soc') ?? null;

  // Ticks der linken Achse → horizontale Gridlinien
  const yTicks = yLeft ? _niceTicks(yLeft.lo, yLeft.hi, 4) : [];
  ctx.strokeStyle = '#2a3a4f'; ctx.lineWidth = 1;
  if (yTicks.length) {
    yTicks.forEach(v => {
      const y = Math.round(yLeft.yOf(v)) + 0.5;
      if (y < PAD_T || y > PAD_T + CH) return;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(PAD_L + CW, y); ctx.stroke();
    });
  } else {
    for (let i = 0; i <= 4; i++) {
      const y = PAD_T + Math.round(CH * i / 4) + 0.5;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(PAD_L + CW, y); ctx.stroke();
    }
  }

  // Vertikale Gitternetzlinien (an den gleichen Positionen wie die X-Achsen-Labels)
  ctx.strokeStyle = '#243040'; ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) {
    const x = Math.round(PAD_L + CW * i / 4) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, PAD_T + CH); ctx.stroke();
  }

  ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textBaseline = 'middle';

  // Linke Y-Achse
  if (yLeft) {
    ctx.textAlign = 'right';
    const col = yLeft.def.color;
    yTicks.forEach(v => {
      const y = yLeft.yOf(v);
      if (y < PAD_T - 2 || y > PAD_T + CH + 2) return;
      ctx.fillStyle = col + 'cc';
      ctx.fillText(fmtYVal(v, yLeft.key), PAD_L - 4, y);
    });
    ctx.fillStyle = col + '88';
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText(yLeft.def.unit, 2, PAD_T);
  }

  // Rechte Y-Achse (zweite Serie — eigene Ticks, keine neuen Gridlinien)
  if (yRight) {
    const rTicks = _niceTicks(yRight.lo, yRight.hi, 4);
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    const col = yRight.def.color;
    rTicks.forEach(v => {
      const y = yRight.yOf(v);
      if (y < PAD_T - 2 || y > PAD_T + CH + 2) return;
      ctx.fillStyle = col + 'cc';
      // kleiner Strich als Tick
      ctx.beginPath(); ctx.moveTo(PAD_L + CW, y); ctx.lineTo(PAD_L + CW + 3, y);
      ctx.strokeStyle = col + '55'; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillText(fmtYVal(v, yRight.key), PAD_L + CW + 5, y);
    });
    ctx.fillStyle = col + '88';
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText(yRight.def.unit, W - PAD_R + 4, PAD_T);
  }

  // X-Achse Zeitlabels
  ctx.fillStyle = '#64748b'; ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textBaseline = 'alphabetic';
  for (let i = 0; i <= 4; i++) {
    const ts = tMin0 + chartRangeSec * i / 4;
    const x  = PAD_L + CW * i / 4;
    ctx.textAlign = i === 0 ? 'left' : i === 4 ? 'right' : 'center';
    ctx.fillText(fmtAxisTime(ts, now), x, H - 4);
  }

  // Serien zeichnen: Fill + glatte Linie
  ctx.save();
  ctx.beginPath(); ctx.rect(PAD_L, PAD_T, CW, CH); ctx.clip();

  // Adaptiver Gap-Schwellwert: 1/30 des Zeitfensters (z.B. 60s bei 30min, 12min bei 6h)
  const maxGapSec = Math.max(30, chartRangeSec / 30);

  const fillKey = yLeft?.key ?? null;

  active.forEach(({ key, def, yOf, sPts }) => {
    const { segs, last } = _buildSegs(sPts, key, xOf, yOf, maxGapSec);
    if (!segs.length) return;

    // Fill: nur für primäre Serie, nach unten schließen
    if (key === fillKey) {
      segs.forEach(s => {
        if (s.length < 2) return;
        const grad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + CH);
        grad.addColorStop(0, def.color + '18');
        grad.addColorStop(1, def.color + '00');
        ctx.beginPath();
        _smoothSeg(ctx, s);
        ctx.lineTo(s[s.length - 1].x, PAD_T + CH);
        ctx.lineTo(s[0].x, PAD_T + CH);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();
      });
    }

    // Linie
    ctx.beginPath();
    segs.forEach(s => _smoothSeg(ctx, s));
    ctx.strokeStyle = def.color; ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();

    // Livepoint
    if (last && scrubTs === null) {
      ctx.beginPath(); ctx.arc(last.x, last.y, 3, 0, Math.PI * 2);
      ctx.fillStyle = def.color; ctx.fill();
      ctx.strokeStyle = '#1e293b'; ctx.lineWidth = 1; ctx.stroke();
    }
  });

  ctx.restore();

  // Chart-Rahmen
  ctx.strokeStyle = '#334155'; ctx.lineWidth = 1;
  ctx.strokeRect(PAD_L + 0.5, PAD_T + 0.5, CW, CH);

  // Crosshair + dots
  const seriesYOf = Object.fromEntries(active.map(a => [a.key, a]));
  if (scrubTs !== null) {
    const x = xOf(scrubTs);
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.3)'; ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, PAD_T + CH); ctx.stroke();
    ctx.setLineDash([]); ctx.restore();
    Object.entries(seriesYOf).forEach(([key, { yOf, def, sPts }]) => {
      const ptsK = sPts.filter(d => d[key] != null);
      if (!ptsK.length) return;
      let cl = ptsK[0], md = Math.abs(ptsK[0].ts - scrubTs);
      ptsK.forEach(d => { const dist = Math.abs(d.ts - scrubTs); if (dist < md) { md = dist; cl = d; } });
      const y = yOf(cl[key]);
      ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = def.color; ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
    });
  }
}

// Scroll-Lock: position:fixed verhindert Background-Scroll ohne touch-events
// zu blockieren (overflow:hidden auf body killt Touch auf Mobile).
// Boolean-Flag _scrollLocked ist Source of Truth — nicht CSS-State abfragen,
// da body.style.position in Edge Cases inkonsistent sein kann.
let _scrollLockY = 0;
let _scrollLocked = false;
function _scrollLock(lock) {
  if (lock && !_scrollLocked) {
    _scrollLockY = window.scrollY;
    _scrollLocked = true;
    document.body.style.position = 'fixed';
    document.body.style.top      = `-${_scrollLockY}px`;
    document.body.style.width    = '100%';
  } else if (!lock && _scrollLocked) {
    _scrollLocked = false;
    document.body.style.position = '';
    document.body.style.top      = '';
    document.body.style.width    = '';
    window.scrollTo(0, _scrollLockY);
  }
}
// Sicherheits-Reset beim Seitenstart — stellt sicher dass kein Altstand vorliegt.
document.addEventListener('DOMContentLoaded', () => { _scrollLocked = false; });

// Beobachte alle .overlay-Elemente auf class-Änderungen
new MutationObserver(() => {
  const anyOpen = [...document.querySelectorAll('.overlay')]
    .some(el => !el.classList.contains('hidden'));
  _scrollLock(anyOpen);
}).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'] });

function _closeAllOverlays() {
  try {
    document.querySelectorAll('.overlay').forEach(el => el.classList.add('hidden'));
    clearInterval(netTimer);           netTimer = null;
    clearInterval(_settingsNetTimer);  _settingsNetTimer = null;
    clearInterval(_connOverlayTimer);  _connOverlayTimer = null;
    lightDetailOpen = false;
    closePresetSave();
    _navActive(null);
  } catch(e) { console.warn('_closeAllOverlays:', e); }
  _scrollLock(false);
}

let _weekData = null;

function openBattDetail() {
  _closeAllOverlays();
  history.pushState({ overlay: 'battery' }, '', '#battery');
  $('battOverlay').classList.remove('hidden');
  if (_lastBms) updateBms(_lastBms);
  if (_lastData) renderDeviceTiles(_lastData);
  _renderMobileCells();
  // Graph erst nach zwei Frames — Canvas hat dann korrekte clientWidth/clientHeight
  requestAnimationFrame(() => requestAnimationFrame(() => {
    renderCharts();
    _loadAndRenderWeekChart();
  }));
}

function _loadAndRenderWeekChart() {
  fetch('/api/daily-stats?days=7', { cache: 'no-store' })
    .then(r => r.json())
    .then(data => { _weekData = data; _renderWeekChart(data); })
    .catch(() => {});
}

function _renderWeekChart(data) {
  const canvas = $('weekChart');
  if (!canvas || !data || !data.length) return;

  const dpr = window.devicePixelRatio || 1;
  const W   = canvas.offsetWidth;
  const H   = canvas.offsetHeight;
  if (!W || !H) return;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const PAD = { t: 8, r: 12, b: 28, l: 36 };
  const CW = W - PAD.l - PAD.r;
  const CH = H - PAD.t - PAD.b;

  // Maximalwert für Ah-Achse
  const maxAh = Math.max(
    ...data.map(d => Math.max(d.charged_ah || 0, d.discharged_ah || 0)), 1
  );
  const yAh  = v  => PAD.t + CH * (1 - v / maxAh);
  const ySoc = v  => PAD.t + CH * (1 - v / 100);

  // Hintergrund
  ctx.fillStyle = '#1e293b';
  ctx.fillRect(0, 0, W, H);

  // Y-Achsen-Ticks (Ah links)
  ctx.fillStyle = '#475569'; ctx.font = `${9 * dpr / dpr}px sans-serif`; ctx.textAlign = 'right';
  const ahTicks = [0, maxAh * 0.5, maxAh];
  ahTicks.forEach(v => {
    const y = yAh(v);
    ctx.fillStyle = '#334155';
    ctx.fillRect(PAD.l, y, CW, 0.5);
    ctx.fillStyle = '#64748b';
    ctx.fillText(Math.round(v) + ' Ah', PAD.l - 3, y + 3);
  });

  const barW   = CW / data.length;
  const bW     = Math.max(3, barW * 0.22);  // Breite je Balken
  const radius = 2;

  function roundedBar(x, y, w, h) {
    if (h < 1) return;
    const r = Math.min(radius, h / 2, w / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h);
    ctx.lineTo(x, y + h);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
    ctx.fill();
  }

  data.forEach((d, i) => {
    const cx = PAD.l + i * barW + barW / 2;

    // Geladene Ah (grün)
    if (d.charged_ah > 0) {
      const bh = Math.max(1, yAh(0) - yAh(d.charged_ah));
      ctx.fillStyle = '#22c55e';
      roundedBar(cx - bW * 1.5, yAh(d.charged_ah), bW, bh);
    }
    // Entladene Ah (orange)
    if (d.discharged_ah > 0) {
      const bh = Math.max(1, yAh(0) - yAh(d.discharged_ah));
      ctx.fillStyle = '#f97316';
      roundedBar(cx - bW * 0.15, yAh(d.discharged_ah), bW, bh);
    }
    // Ø SOC (blau, halbtransparent)
    if (d.avg_soc != null) {
      const bh = Math.max(1, ySoc(0) - ySoc(d.avg_soc));
      ctx.fillStyle = 'rgba(59,130,246,0.4)';
      roundedBar(cx + bW * 1.2, ySoc(d.avg_soc), bW, bh);
    }

    // Tag-Beschriftung
    const label = new Date(d.date + 'T12:00:00').toLocaleDateString('de-DE', { weekday: 'short' });
    ctx.fillStyle = '#64748b'; ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(label, cx, PAD.t + CH + 14);
  });
}

function _renderMobileCells() {
  const wrap = $('bdMobileCells');
  if (!wrap) return;
  const bms = _lastBms;
  if (!bms || !bms.cells || !bms.cells.length) { wrap.innerHTML = ''; return; }
  const cells = bms.cells.slice(0, 8);
  const rows = cells.map((c, i) => {
    const v = c.voltage != null ? c.voltage.toFixed(3) : '--';
    const t = c.temp    != null ? ` · ${c.temp.toFixed(1)}°` : '';
    return `<div class="dt-cell">
      <span class="dt-cell-nr">#${i+1}</span>
      <span class="dt-cell-v">${v}<span class="dt-unit"> V</span></span>
      ${t ? `<span class="dt-cell-t">${t}</span>` : ''}
    </div>`;
  }).join('');
  wrap.innerHTML = `<div class="dt-cell-grid" style="padding:0 12px 12px">${rows}</div>`;
}
function closeBattDetail() {
  $('battOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
  // Inverter-Card sofort auf letzten bekannten Zustand setzen (verhindert Flash)
  if (_lastData) updateInverterCard(_lastData.inverter, _lastData.charger);
}

// ── BMS update ─────────────────────────────────────────────────────────────

let _lastBms = null;

function cellColor(v, isLowest, isHighest, alarmMin, alarmMax) {
  if (v == null) return 'var(--border)';
  if ((alarmMin && isLowest) || v < 2.9)  return 'var(--red)';
  if ((alarmMax && isHighest) || v > 3.65) return 'var(--red)';
  if (v < 3.1 || v > 3.6) return 'var(--yellow)';
  return 'var(--green)';
}

function updateBms(bms) {
  if (!bms) return;
  _lastBms = bms;
  // Restkapazität: Shunt (updateBattery) hat Vorrang; BMS als Fallback
  const remAhEl = $('battRemVal');
  if (remAhEl && remAhEl.textContent === '--') {
    if (bms.capacity_ah != null && bms.soc != null)
      remAhEl.textContent = (bms.capacity_ah * bms.soc / 100).toFixed(1);
  }
  // BMS-Relais-Status auf der Batterie-Kachel
  const relayRow = $('bmsRelayRow');
  if (relayRow && bms.allow_charge != null) {
    relayRow.style.display = 'flex';
    const _dot = (id, ok) => {
      const el = $(id);
      if (!el) return;
      el.classList.toggle('on', ok);
      el.style.background  = ok ? 'var(--green)' : 'var(--red)';
      el.style.boxShadow   = ok ? '0 0 4px var(--green)' : '0 0 4px var(--red)';
    };
    _dot('bmsRelayChargeDot',    bms.allow_charge);
    _dot('bmsRelayDischargeDot', bms.allow_discharge);

    // Zellgesundheits-Dot
    const hdot = $('cellHealthDot');
    if (hdot) {
      const anyAlarm = bms.alarm_min_volt || bms.alarm_max_volt ||
                       bms.alarm_min_temp || bms.alarm_max_temp;
      const cells = bms.cells ?? [];
      const minV  = cells.length ? Math.min(...cells) : bms.lowest_cell_v;
      const maxV  = cells.length ? Math.max(...cells) : bms.highest_cell_v;
      const diff  = (minV != null && maxV != null) ? maxV - minV : null;
      const tempHigh = (bms.highest_temp ?? 0) > 40;
      const tempLow  = (bms.lowest_temp  ?? 0) < 5;
      const cellBad  = (minV != null && minV < 3.0) || (maxV != null && maxV > 3.65) || (diff != null && diff > 0.08);
      const cellWarn = (minV != null && minV < 3.1) || (maxV != null && maxV > 3.6) || (diff != null && diff > 0.04);
      let color, shadow;
      if (anyAlarm || cellBad || tempHigh || tempLow) {
        color = 'var(--red)';   shadow = '0 0 4px var(--red)';
      } else if (cellWarn || (diff != null && diff > 0.04)) {
        color = 'var(--yellow)'; shadow = '0 0 4px var(--yellow)';
      } else {
        color = 'var(--green)';  shadow = '0 0 4px var(--green)';
      }
      hdot.style.background = color;
      hdot.style.boxShadow  = shadow;
    }
  }
  const hasBms = bms.voltage != null;
  $('bmsNoSignal').style.display  = hasBms ? 'none' : '';
  $('bmsDataWrap').style.display  = hasBms ? ''     : 'none';
  if (!hasBms) return;

  // BMS daily Ah accumulation
  if (bms.current_charge != null || bms.current_discharge != null) {
    accumBmsAh(bms.current_charge, bms.current_discharge);
    const dsEl = $('bmsDailyStats');
    if (dsEl) dsEl.style.display = '';
    const tcEl = $('bmsTodayCharge');    if (tcEl) tcEl.textContent = _todayChargeAh.toFixed(1) + ' Ah';
    const tdEl = $('bmsTodayDischarge'); if (tdEl) tdEl.textContent = _todayDischargeAh.toFixed(1) + ' Ah';
    const caEl = $('bmsChargeA');    if (caEl) caEl.textContent = (bms.current_charge ?? 0).toFixed(1) + ' A';
    const daEl = $('bmsDischargeA'); if (daEl) daEl.textContent = (bms.current_discharge ?? 0).toFixed(1) + ' A';
  }

  // Pack row
  const packItems = [
    { label: 'SOC',        val: bms.soc != null ? bms.soc + ' %' : '--' },
    { label: 'Spannung',   val: bms.voltage != null ? bms.voltage.toFixed(2) + ' V' : '--' },
    { label: 'Strom',      val: bms.current_total != null ? bms.current_total.toFixed(1) + ' A' : '--' },
    { label: 'Kapazität',  val: bms.capacity_ah != null ? bms.capacity_ah.toFixed(0) + ' Ah' : '--' },
    { label: 'Verbleibend',val: bms.remaining_kwh != null ? bms.remaining_kwh.toFixed(2) + ' kWh' : '--' },
    { label: 'Zellen',     val: bms.cell_count ?? '--' },
  ];
  $('bmsPackRow').innerHTML = packItems.map(it =>
    `<div class="bms-pack-item">
      <div class="bd-label">${it.label}</div>
      <div class="bms-pack-val">${it.val}</div>
    </div>`
  ).join('');

  // Status flags
  const flags = [
    { label: 'Laden OK',   cls: bms.allow_charge    ? 'ok'  : 'err', icon: bms.allow_charge    ? '✓' : '✕' },
    { label: 'Entladen OK',cls: bms.allow_discharge ? 'ok'  : 'err', icon: bms.allow_discharge ? '✓' : '✕' },
    { label: 'Kommunikation', cls: bms.comm_error   ? 'err' : 'ok',  icon: bms.comm_error      ? '!' : '✓' },
    { label: 'Alarm V min',cls: bms.alarm_min_volt  ? 'err' : 'ok',  icon: bms.alarm_min_volt  ? '!' : '✓' },
    { label: 'Alarm V max',cls: bms.alarm_max_volt  ? 'err' : 'ok',  icon: bms.alarm_max_volt  ? '!' : '✓' },
    { label: 'Alarm T min',cls: bms.alarm_min_temp  ? 'warn': 'ok',  icon: bms.alarm_min_temp  ? '!' : '✓' },
    { label: 'Alarm T max',cls: bms.alarm_max_temp  ? 'warn': 'ok',  icon: bms.alarm_max_temp  ? '!' : '✓' },
  ];
  $('bmsFlags').innerHTML = flags.map(f =>
    `<div class="bms-flag ${f.cls}">${f.icon} ${f.label}</div>`
  ).join('');

  // Cell grid (4-cell: 2×2 with bottom-left=1, top-left=2, top-right=3, bottom-right=4)
  const cells = bms.cells ?? [];
  const n     = cells.length || (bms.cell_count ?? 0);
  const renderOrder = n === 4 ? [1, 2, 0, 3] : Array.from({length: n}, (_, i) => i);
  const grid = $('bmsCellGrid');
  grid.style.gridTemplateColumns = n === 4 ? 'repeat(2, 1fr)' : '';
  grid.innerHTML = renderOrder.map(i => {
    const c    = cells[i];
    const isLo = i === (bms.lowest_cell_nr  - 1);
    const isHi = i === (bms.highest_cell_nr - 1);
    const color = c ? cellColor(c.voltage, isLo, isHi, bms.alarm_min_volt, bms.alarm_max_volt) : 'var(--border)';
    const vStr  = c?.voltage != null ? c.voltage.toFixed(3) + ' V' : '-- V';
    const tStr  = c?.temp    != null ? c.temp.toFixed(1)    + ' °C' : '-- °C';
    return `<div class="bms-cell" style="border-color:${color}">
      <div class="cell-num">Zelle ${i + 1}</div>
      <div class="cell-volt" style="color:${color}">${vStr}</div>
      <div class="cell-temp">${tStr}</div>
    </div>`;
  }).join('');

  // Track cell diff for Verlauf chart
  if (bms.highest_cell_v != null && bms.lowest_cell_v != null)
    _lastZelldiff = bms.highest_cell_v - bms.lowest_cell_v;

  updateDualTiles();
  const remEl = $('dRemKwh');
  if (remEl && bms.remaining_kwh != null) remEl.textContent = bms.remaining_kwh.toFixed(2);
}

// Solar-Leistung für den Verlaufs-Graph merken (Summe aller Solar-Quellen)
function updateSolarCard(data) {
  const total = (data.solar?.power ?? 0) + (data.solar2?.power ?? 0) + (data.solar3?.power ?? 0);
  _lastSolarW = (data.solar?.power != null || data.solar2?.power != null || data.solar3?.power != null)
    ? total : null;
}
