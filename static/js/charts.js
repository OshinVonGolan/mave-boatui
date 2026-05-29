// ── Battery detail + charts ────────────────────────────────────────────────

const HIST_MAX = 10000;
const histData = [];  // [{ts, soc, voltage, current}, ...]

const SERIES_DEF = {
  soc:      { color: '#22c55e', unit: '%',  label: 'SOC',       fmt: v => Math.round(v) + ' %',         minRange: 10 },
  voltage:  { color: '#06b6d4', unit: 'V',  label: 'Spannung',  fmt: v => v.toFixed(2) + ' V',          minRange: 2.0 },
  current:  { color: '#f97316', unit: 'A',  label: 'Strom',     fmt: v => v.toFixed(1) + ' A',          minRange: 3.0 },
  zelldiff: { color: '#a78bfa', unit: 'mV', label: 'Zelldiff.', fmt: v => Math.round(v * 1000) + ' mV', minRange: 0.015 },
};

const CH_NAMES = ['Küche', 'Kartentisch', 'Salon', 'Achtkabine stbd'];

let chartSeries   = { soc: true, voltage: true, current: false, zelldiff: false };
let chartRangeSec = 1800;
let chartHoverPos = null; // null=live, 0.0–1.0=scrub fraction

function recordHistory(b) {
  const entry = { ts: Date.now() / 1000 };
  if (b.soc      != null) entry.soc      = b.soc;
  if (b.voltage  != null) entry.voltage  = b.voltage;
  if (b.current  != null) entry.current  = b.current;
  if (_lastZelldiff != null) entry.zelldiff = _lastZelldiff;
  histData.push(entry);
  if (histData.length > HIST_MAX) histData.shift();
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

const CHART_PAD_L = 36, CHART_PAD_R = 54;

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

  const tMin0 = now - chartRangeSec;
  const xOf = ts => PAD_L + Math.max(0, Math.min(CW, ((ts - tMin0) / chartRangeSec) * CW));
  const scrubTs = chartHoverPos !== null ? tMin0 + chartHoverPos * chartRangeSec : null;

  renderChartLegend(pts, scrubTs);

  // X-axis time labels
  ctx.fillStyle = '#64748b';
  ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textBaseline = 'alphabetic';
  for (let i = 0; i <= 4; i++) {
    const ts = tMin0 + chartRangeSec * i / 4;
    const x  = PAD_L + CW * i / 4;
    ctx.textAlign = i === 0 ? 'left' : i === 4 ? 'right' : 'center';
    ctx.fillText(fmtAxisTime(ts, now), x, H - 4);
  }

  if (pts.length < 2) return;

  // Compute Y scale per active series (percentile-based to ignore outlier spikes)
  const seriesYOf = {};
  Object.entries(SERIES_DEF).forEach(([key, def]) => {
    if (!chartSeries[key]) return;
    const vals = pts.map(d => d[key]).filter(v => v != null);
    if (vals.length < 2) return;
    const sorted = [...vals].sort((a, b) => a - b);
    const n   = sorted.length;
    let lo = sorted[Math.max(0, Math.floor(n * 0.02))];
    let hi = sorted[Math.min(n - 1, Math.ceil(n * 0.98) - 1)];
    if (def.minRange && (hi - lo) < def.minRange) {
      const mid = (hi + lo) / 2;
      lo = mid - def.minRange / 2;
      hi = mid + def.minRange / 2;
    }
    const pad = (hi - lo) * 0.12 || 0.5;
    lo -= pad; hi += pad;
    const yRange = hi - lo;
    const yOf = v => PAD_T + CH - ((v - lo) / yRange) * CH * 0.9 - CH * 0.05;
    seriesYOf[key] = { yOf, def, lo, hi };
  });

  // Grid lines
  ctx.strokeStyle = '#334155'; ctx.lineWidth = 1;
  for (let i = 1; i <= 3; i++) {
    const y = PAD_T + Math.round(CH * i / 4) + 0.5;
    ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(PAD_L + CW, y); ctx.stroke();
  }

  // Y-axis: left = primary series, right = secondary series
  ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
  const yAxisEntries = Object.entries(seriesYOf);
  [[yAxisEntries[0], 'right', PAD_L - 4], [yAxisEntries[1], 'left', PAD_L + CW + 4]].forEach(([entry, align, x]) => {
    if (!entry) return;
    const [key, { lo, hi, def: eDef }] = entry;
    ctx.fillStyle = eDef.color + 'bb';
    ctx.textAlign = align;
    for (let i = 1; i <= 3; i++) {
      const v = lo + (0.95 - i / 4) / 0.9 * (hi - lo);
      ctx.textBaseline = 'middle';
      ctx.fillText(fmtYVal(v, key), x, PAD_T + CH * i / 4);
    }
    ctx.textBaseline = 'top';
    ctx.fillText(fmtYVal(lo + (0.95 / 0.9) * (hi - lo), key), x, PAD_T);
  });

  // Draw each series — clipped to chart area so outliers don't render outside
  ctx.save();
  ctx.beginPath();
  ctx.rect(PAD_L, PAD_T, CW, CH);
  ctx.clip();

  Object.entries(seriesYOf).forEach(([key, { yOf, def }]) => {
    const grad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + CH);
    grad.addColorStop(0, def.color + '44');
    grad.addColorStop(1, def.color + '00');

    let first = true, lastX = 0, lastY = 0, lastVal = null;
    ctx.beginPath();
    pts.forEach(d => {
      const v = d[key]; if (v == null) { first = true; return; }
      const x = xOf(d.ts), y = yOf(v);
      if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
      lastX = x; lastY = y; lastVal = v;
    });
    if (!first) {
      ctx.lineTo(lastX, PAD_T + CH); ctx.lineTo(PAD_L, PAD_T + CH);
      ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
    }

    first = true;
    ctx.beginPath();
    pts.forEach(d => {
      const v = d[key]; if (v == null) { first = true; return; }
      const x = xOf(d.ts), y = yOf(v);
      if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = def.color; ctx.lineWidth = 2;
    ctx.lineJoin = 'round'; ctx.stroke();

    if (!first && lastVal != null && scrubTs === null) {
      ctx.beginPath(); ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = def.color; ctx.fill();
    }
  });

  ctx.restore();

  // Crosshair + dots at hover position
  if (scrubTs !== null) {
    const x = xOf(scrubTs);
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.3)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, PAD_T + CH); ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    Object.entries(seriesYOf).forEach(([key, { yOf, def }]) => {
      const ptsWithKey = pts.filter(d => d[key] != null);
      if (!ptsWithKey.length) return;
      let closest = ptsWithKey[0], minDist = Math.abs(ptsWithKey[0].ts - scrubTs);
      ptsWithKey.forEach(d => { const dist = Math.abs(d.ts - scrubTs); if (dist < minDist) { minDist = dist; closest = d; } });
      const y = yOf(closest[key]);
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
  _updateCellMini(bms);
  const remEl = $('dRemKwh');
  if (remEl && bms.remaining_kwh != null) remEl.textContent = bms.remaining_kwh.toFixed(2);
}

function _updateCellMini(bms) {
  const wrap = $('bdCellMini');
  const grid = $('bdCellMiniGrid');
  if (!wrap || !grid) return;
  const cells = bms.cells ?? [];
  const n = cells.length || (bms.cell_count ?? 0);
  if (!n) { wrap.style.display = 'none'; return; }
  wrap.style.display = '';
  const renderOrder = n === 4 ? [1, 2, 0, 3] : Array.from({length: n}, (_, i) => i);
  grid.innerHTML = renderOrder.map(i => {
    const c     = cells[i];
    const isLo  = i === (bms.lowest_cell_nr  - 1);
    const isHi  = i === (bms.highest_cell_nr - 1);
    const color = c ? cellColor(c.voltage, isLo, isHi, bms.alarm_min_volt, bms.alarm_max_volt) : 'var(--border)';
    const vStr  = c?.voltage != null ? c.voltage.toFixed(3) : '--';
    const tStr  = c?.temp    != null ? c.temp.toFixed(1) + ' °C' : '';
    return `<div class="bd-cell-mini-item" style="border-color:${color}">
      <div class="bd-cell-mini-nr">Zelle ${i + 1}</div>
      <div class="bd-cell-mini-v" style="color:${color}">${vStr} <span style="font-size:11px;font-weight:400;color:var(--text3)">V</span></div>
      ${tStr ? `<div class="bd-cell-mini-t">${tStr}</div>` : ''}
    </div>`;
  }).join('');
}

function updateSolarCard(data) {
  function setPower(wId, w) {
    const el = $(wId);
    if (!el) return;
    el.textContent = w != null ? Math.round(w) + ' W' : '-- W';
    el.style.color = w != null && w > 0 ? 'var(--yellow)' : 'var(--text3)';
  }
  const s1 = data.solar?.power;
  const s2 = data.solar2?.power;
  const s3 = data.solar3?.power;
  setPower('bdS1W', s1);
  setPower('bdS2W', s2);
  setPower('bdS3W', s3);
  // Solar 1 panel voltage (available from shunt PGN)
  const v1El = $('bdS1V');
  if (v1El) {
    const v1 = data.solar?.voltage;
    v1El.textContent = v1 != null ? v1.toFixed(1) + ' V' : '';
  }
  const total = (s1 ?? 0) + (s2 ?? 0) + (s3 ?? 0);
  const totalEl = $('bdSolarTotal');
  if (totalEl) {
    totalEl.textContent = Math.round(total) + ' W';
    totalEl.style.color = total > 0 ? 'var(--yellow)' : 'var(--text3)';
  }
  // Tagesertrag from chargeHist (solar sources summed, today only)
  const dayStart = new Date(); dayStart.setHours(0,0,0,0);
  const dayStartSec = dayStart.getTime() / 1000;
  const todayEntries = chargeHist.filter(e => e.ts >= dayStartSec);
  if (todayEntries.length > 1) {
    let wh = 0;
    for (let i = 1; i < todayEntries.length; i++) {
      const dt = todayEntries[i].ts - todayEntries[i-1].ts;
      const avgW = ((todayEntries[i].solar1 ?? 0) + (todayEntries[i].solar2 ?? 0) + (todayEntries[i].solar3 ?? 0) +
                   (todayEntries[i-1].solar1 ?? 0) + (todayEntries[i-1].solar2 ?? 0) + (todayEntries[i-1].solar3 ?? 0)) / 2;
      wh += avgW * dt / 3600;
    }
    const ydEl = $('bdYieldDay');
    if (ydEl) ydEl.textContent = Math.round(wh);
  }
}
