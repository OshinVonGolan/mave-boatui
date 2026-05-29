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

const CHART_PAD_L = 46, CHART_PAD_R = 16;

function renderCharts() {
  const canvas = $('chartMain');
  if (!canvas) return;

  const now    = Date.now() / 1000;
  const cutoff = now - chartRangeSec;
  const pts    = histData.filter(d => d.ts >= cutoff);

  const dpr  = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;

  const FONT = '10px -apple-system,BlinkMacSystemFont,sans-serif';
  const PAD_L = CHART_PAD_L, PAD_R = CHART_PAD_R, PAD_B = 24, PAD_T = 10;
  const CW = W - PAD_L - PAD_R, CH = H - PAD_B - PAD_T;

  ctx.fillStyle = '#1e293b';
  ctx.fillRect(0, 0, W, H);

  const tMin0 = now - chartRangeSec;
  const xOf = ts => PAD_L + Math.max(0, Math.min(CW, ((ts - tMin0) / chartRangeSec) * CW));
  const scrubTs = chartHoverPos !== null ? tMin0 + chartHoverPos * chartRangeSec : null;

  renderChartLegend(pts, scrubTs);

  // Aktive Serien berechnen
  const active = [];
  Object.entries(SERIES_DEF).forEach(([key, def]) => {
    if (!chartSeries[key]) return;
    const vals = pts.map(d => d[key]).filter(v => v != null);
    if (vals.length < 2) return;
    const [lo, hi] = _seriesDomain(vals, def);
    const span = (hi - lo) || 1;
    const yOf = v => PAD_T + CH - ((Math.max(lo, Math.min(hi, v)) - lo) / span) * CH;
    active.push({ key, def, yOf, lo, hi });
  });

  // X-Achse Zeitbeschriftung
  ctx.fillStyle = '#64748b'; ctx.font = FONT; ctx.textBaseline = 'alphabetic';
  for (let i = 0; i <= 4; i++) {
    const ts = tMin0 + chartRangeSec * i / 4;
    const x  = PAD_L + CW * i / 4;
    ctx.textAlign = i === 0 ? 'left' : i === 4 ? 'right' : 'center';
    ctx.fillText(fmtAxisTime(ts, now), x, H - 4);
  }

  // Y-Achse: linke Seite für die erste aktive Serie (farbig beschriftet)
  if (active.length > 0) {
    const { def, lo, hi } = active[0];
    ctx.font = FONT; ctx.fillStyle = def.color + 'bb'; ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * (1 - i / 4);
      const y = PAD_T + CH * i / 4;
      ctx.textBaseline = i === 0 ? 'top' : i === 4 ? 'bottom' : 'middle';
      ctx.fillText(fmtYVal(v, active[0].key), PAD_L - 4, y);
    }
  }

  // Horizontale Hilfslinien
  ctx.strokeStyle = '#2a3a4f'; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = PAD_T + Math.round(CH * i / 4) + 0.5;
    ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(PAD_L + CW, y); ctx.stroke();
  }

  // Serien zeichnen — immer Area-Fill (halbtransparent), dann Linie drüber
  ctx.save();
  ctx.beginPath(); ctx.rect(PAD_L, PAD_T, CW, CH); ctx.clip();

  active.forEach(({ key, def, yOf }) => {
    // Fläche unter der Kurve (immer, aber bei mehreren Serien etwas transparenter)
    const alphaHex = active.length === 1 ? '3a' : '20';
    const grad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + CH);
    grad.addColorStop(0, def.color + alphaHex);
    grad.addColorStop(1, def.color + '00');
    let first = true, lastX = 0;
    ctx.beginPath();
    pts.forEach(d => {
      const v = d[key]; if (v == null) { first = true; return; }
      const x = xOf(d.ts), y = yOf(v);
      if (first) { ctx.moveTo(x, PAD_T + CH); ctx.lineTo(x, y); first = false; }
      else ctx.lineTo(x, y);
      lastX = x;
    });
    if (!first) { ctx.lineTo(lastX, PAD_T + CH); ctx.closePath(); ctx.fillStyle = grad; ctx.fill(); }

    // Linie
    first = true; let lastY = 0;
    ctx.beginPath();
    pts.forEach(d => {
      const v = d[key]; if (v == null) { first = true; return; }
      const x = xOf(d.ts), y = yOf(v);
      if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
      lastX = x; lastY = y;
    });
    ctx.strokeStyle = def.color; ctx.lineWidth = 2;
    ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();

    // Letzter Punkt
    if (!first && scrubTs === null) {
      ctx.beginPath(); ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = def.color; ctx.fill();
    }
  });

  ctx.restore();

  // für Scrubber unten verfügbar machen
  const seriesYOf = Object.fromEntries(active.map(a => [a.key, a]));

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

  // Neue 2x2 Zellanzeige in der Batterie-Detail-Seite
  const hasBms = bms.voltage != null;
  const cellWrap = $('bdCellMini'), cellAbsent = $('bdCellMiniAbsent');
  if (cellWrap) cellWrap.style.display = hasBms ? '' : 'none';
  if (cellAbsent) cellAbsent.style.display = hasBms ? 'none' : '';
  if (hasBms) {
    const cells2 = bms.cells ?? [];
    const n2 = cells2.length || (bms.cell_count ?? 0);
    const grid2 = $('bdCellMiniGrid');
    if (grid2 && n2) {
      const order = n2 === 4 ? [1, 2, 0, 3] : Array.from({length: n2}, (_, i) => i);
      grid2.innerHTML = order.map(i => {
        const c = cells2[i];
        const isLo = i === (bms.lowest_cell_nr - 1), isHi = i === (bms.highest_cell_nr - 1);
        const col = c ? cellColor(c.voltage, isLo, isHi, bms.alarm_min_volt, bms.alarm_max_volt) : 'var(--border)';
        const vStr = c?.voltage != null ? c.voltage.toFixed(3) : '--';
        const tStr = c?.temp    != null ? c.temp.toFixed(1) + ' °C' : '';
        return `<div class="bd-cell-mini-item" style="border-color:${col}">
          <div class="bd-cell-mini-nr">Zelle ${i + 1}</div>
          <div class="bd-cell-mini-v" style="color:${col}">${vStr} <span style="font-size:10px;color:var(--text3)">V</span></div>
          ${tStr ? `<div class="bd-cell-mini-t">${tStr}</div>` : ''}
        </div>`;
      }).join('');
    }
  }
}

// Solar-Leistung für den Verlaufs-Graph merken (Summe aller Solar-Quellen)
function updateSolarCard(data) {
  const total = (data.solar?.power ?? 0) + (data.solar2?.power ?? 0) + (data.solar3?.power ?? 0);
  _lastSolarW = (data.solar?.power != null || data.solar2?.power != null || data.solar3?.power != null)
    ? total : null;
}
