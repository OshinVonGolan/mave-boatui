// ── Battery detail + charts ────────────────────────────────────────────────

const HIST_MAX = 10000;
const histData = [];  // [{ts, soc, voltage, current}, ...]

// domain: feste Achsen-Grenzen [min,max] oder null = automatisch aus Daten
const SERIES_DEF = {
  soc:      { color: '#22c55e', unit: '%',  label: 'SOC',       fmt: v => Math.round(v) + ' %',         domain: [0, 100] },
  voltage:  { color: '#06b6d4', unit: 'V',  label: 'Spannung',  fmt: v => v.toFixed(2) + ' V',          minSpan: 0.4 },
  current:  { color: '#f97316', unit: 'A',  label: 'Strom',     fmt: v => v.toFixed(1) + ' A',          minSpan: 2.0, zero: true },
  solar:    { color: '#eab308', unit: 'W',  label: 'Solar',     fmt: v => Math.round(v) + ' W',         minSpan: 20,  zero: true },
  zelldiff: { color: '#a78bfa', unit: 'mV', label: 'Zelldiff.', fmt: v => Math.round(v * 1000) + ' mV', minSpan: 0.01, zero: true },
};

const CH_NAMES = ['Küche', 'Kartentisch', 'Salon', 'Achtkabine stbd'];

let chartSeries   = { soc: true, voltage: true, current: true, solar: true, zelldiff: false };
let chartRangeSec = 1800;
let chartHoverPos = null; // null=live, 0.0–1.0=scrub fraction
let _lastSolarW   = null;

function recordHistory(b) {
  const entry = { ts: Date.now() / 1000 };
  if (b.soc      != null) entry.soc      = b.soc;
  if (b.voltage  != null) entry.voltage  = b.voltage;
  if (b.current  != null) entry.current  = b.current;
  if (_lastSolarW   != null) entry.solar    = _lastSolarW;
  if (_lastZelldiff != null) entry.zelldiff = _lastZelldiff;
  histData.push(entry);
  if (histData.length > HIST_MAX) histData.shift();
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
  chartSeries[key] = !chartSeries[key];
  $(`tog-${key}`).classList.toggle('active', chartSeries[key]);
  renderCharts();
}

function setChartRange(btn, secs) {
  chartRangeSec = secs;
  chartHoverPos = null;
  document.querySelectorAll('.chart-range').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderCharts();
}

function renderChartLegend(pts, scrubTs) {
  const leg = $('chartLegend');
  if (!leg) return;
  leg.innerHTML = Object.entries(SERIES_DEF).filter(([k]) => chartSeries[k]).map(([key, def]) => {
    const ptsWithKey = pts.filter(d => d[key] != null);
    let displayVal = null;
    if (scrubTs != null && ptsWithKey.length) {
      let closest = ptsWithKey[0], minDist = Math.abs(ptsWithKey[0].ts - scrubTs);
      ptsWithKey.forEach(d => { const dist = Math.abs(d.ts - scrubTs); if (dist < minDist) { minDist = dist; closest = d; } });
      displayVal = closest[key];
    } else {
      displayVal = ptsWithKey.at(-1)?.[key] ?? null;
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

// Catmull-Rom → glatte Bézierkurve (tension 0 = scharf, 1 = sehr weich)
const _SMOOTH_T = 0.4;
function _smoothSeg(ctx, s) {
  if (s.length === 0) return;
  ctx.moveTo(s[0].x, s[0].y);
  if (s.length < 3) { if (s.length === 2) ctx.lineTo(s[1].x, s[1].y); return; }
  for (let i = 0; i < s.length - 1; i++) {
    const p0 = s[Math.max(0, i - 1)];
    const p1 = s[i], p2 = s[i + 1];
    const p3 = s[Math.min(s.length - 1, i + 2)];
    const t = _SMOOTH_T;
    ctx.bezierCurveTo(
      p1.x + t * (p2.x - p0.x) / 2, p1.y + t * (p2.y - p0.y) / 2,
      p2.x - t * (p3.x - p1.x) / 2, p2.y - t * (p3.y - p1.y) / 2,
      p2.x, p2.y
    );
  }
}

// Sammelt Segmente (bei null-Werten unterbrochen) und gibt letzten Punkt zurück
function _buildSegs(pts, key, xOf, yOf) {
  let seg = [], segs = [], last = null;
  // Downsampling bei vielen Punkten
  const step = pts.length > 400 ? Math.ceil(pts.length / 400) : 1;
  pts.forEach((d, i) => {
    if (i % step !== 0 && i !== pts.length - 1) return;
    const v = d[key];
    if (v == null) { if (seg.length) { segs.push(seg); seg = []; } return; }
    const p = { x: xOf(d.ts), y: yOf(v) };
    seg.push(p); last = p;
  });
  if (seg.length) segs.push(seg);
  return { segs, last };
}

function renderCharts() {
  const canvas = $('chartMain');
  if (!canvas) return;

  const now    = Date.now() / 1000;
  const cutoff = now - chartRangeSec;
  const pts    = histData.filter(d => d.ts >= cutoff);

  const dpr  = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;

  const PAD_L = CHART_PAD_L, PAD_R = CHART_PAD_R, PAD_B = 20, PAD_T = 8;
  const CW = W - PAD_L - PAD_R, CH = H - PAD_B - PAD_T;

  ctx.fillStyle = '#1e293b';
  ctx.fillRect(0, 0, W, H);

  const tMin0  = now - chartRangeSec;
  const xOf    = ts => PAD_L + Math.max(0, Math.min(CW, ((ts - tMin0) / chartRangeSec) * CW));
  const scrubTs = chartHoverPos !== null ? tMin0 + chartHoverPos * chartRangeSec : null;

  renderChartLegend(pts, scrubTs);

  // Aktive Serien
  const active = [];
  Object.entries(SERIES_DEF).forEach(([key, def]) => {
    if (!chartSeries[key]) return;
    const vals = pts.map(d => d[key]).filter(v => v != null);
    if (vals.length < 2) return;
    const [lo, hi] = _seriesDomain(vals, def);
    const span = (hi - lo) || 1;
    const yOf  = v => PAD_T + CH - ((Math.max(lo, Math.min(hi, v)) - lo) / span) * CH;
    active.push({ key, def, lo, hi, yOf });
  });

  // Y-Achse links + rechts: je eine Serie, Priorität SOC > current > voltage > solar
  const yAxisOrder = ['soc','current','voltage','solar','zelldiff'];
  const sorted     = yAxisOrder.map(k => active.find(a => a.key === k)).filter(Boolean);
  const yLeft      = sorted[0] || null;
  const yRight     = sorted[1] || null;

  // Ticks der linken Achse → Gridlinien
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

  const fillAlpha = active.length === 1 ? '30' : '1a';

  active.forEach(({ key, def, yOf }) => {
    const { segs, last } = _buildSegs(pts, key, xOf, yOf);
    if (!segs.length) return;

    // Fill: für jedes Segment schließen wir nach unten
    segs.forEach(s => {
      if (s.length < 2) return;
      const grad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + CH);
      grad.addColorStop(0, def.color + fillAlpha);
      grad.addColorStop(1, def.color + '00');
      ctx.beginPath();
      _smoothSeg(ctx, s);
      ctx.lineTo(s[s.length - 1].x, PAD_T + CH);
      ctx.lineTo(s[0].x, PAD_T + CH);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();
    });

    // Linie
    ctx.beginPath();
    segs.forEach(s => _smoothSeg(ctx, s));
    ctx.strokeStyle = def.color; ctx.lineWidth = 2;
    ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();

    // Livepoint
    if (last && scrubTs === null) {
      ctx.beginPath(); ctx.arc(last.x, last.y, 3, 0, Math.PI * 2);
      ctx.fillStyle = def.color; ctx.fill();
    }
  });

  ctx.restore();

  // Crosshair + dots
  const seriesYOf = Object.fromEntries(active.map(a => [a.key, a]));
  if (scrubTs !== null) {
    const x = xOf(scrubTs);
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.3)'; ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, PAD_T + CH); ctx.stroke();
    ctx.setLineDash([]); ctx.restore();
    Object.entries(seriesYOf).forEach(([key, { yOf, def }]) => {
      const ptsK = pts.filter(d => d[key] != null);
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

function _closeAllOverlays() {
  document.querySelectorAll('.overlay').forEach(el => el.classList.add('hidden'));
  clearInterval(netTimer);           netTimer = null;
  clearInterval(_settingsNetTimer);  _settingsNetTimer = null;
  clearInterval(_connOverlayTimer);  _connOverlayTimer = null;
  lightDetailOpen = false;
  closePresetSave();
  _navActive(null);
}

function openBattDetail() {
  _closeAllOverlays();
  history.pushState({ overlay: 'battery' }, '', '#battery');
  $('battOverlay').classList.remove('hidden');
  if (_lastBms) updateBms(_lastBms);
  setTimeout(renderCharts, 50);
}
function closeBattDetail() {
  $('battOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
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
  // Restkapazität auf der Startseite
  const remAhEl = $('battRemAh');
  if (remAhEl) {
    if (bms.capacity_ah != null && bms.soc != null) {
      remAhEl.textContent = (bms.capacity_ah * bms.soc / 100).toFixed(1);
    } else {
      remAhEl.textContent = '--';
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
