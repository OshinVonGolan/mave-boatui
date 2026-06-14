// ── Wasserstand Travemünde ──────────────────────────────────────────────────

let _wlData = null;

function _trendArrow(trend) {
  if (trend === 'rising')  return '↑';
  if (trend === 'falling') return '↓';
  return '→';
}

function _trendColor(trend) {
  if (trend === 'rising')  return 'var(--blue)';
  if (trend === 'falling') return 'var(--yellow)';
  return 'var(--text3)';
}

function _nhnLabel(data) {
  if (data.current_nhn_cm == null) return null;
  const cm = data.current_nhn_cm;
  const sign = cm >= 0 ? '+' : '';
  return `${sign}${cm} cm NHN`;
}

function _updateTopbarChip(data) {
  const chip  = $('wlChip');
  const val   = $('wlValue');
  const trend = $('wlTrend');
  if (!chip || !data || data.current_cm == null) return;
  chip.style.display = '';
  val.textContent = _nhnLabel(data) ?? (Math.round(data.current_cm) + ' cm');
  trend.textContent = _trendArrow(data.trend);
  trend.style.color = _trendColor(data.trend);
}

function _renderWlChart(measurements) {
  const canvas = $('wlCanvas');
  if (!canvas || !measurements || !measurements.length) return;
  const dpr = window.devicePixelRatio || 1;
  const W   = canvas.offsetWidth  || 600;
  const H   = canvas.offsetHeight || 180;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const vals = measurements.map(m => m.v);
  const min  = Math.min(...vals) - 5;
  const max  = Math.max(...vals) + 5;
  const pad  = { t: 10, r: 10, b: 24, l: 44 };
  const cW   = W - pad.l - pad.r;
  const cH   = H - pad.t - pad.b;

  const xOf = i => pad.l + (i / (measurements.length - 1)) * cW;
  const yOf = v => pad.t + (1 - (v - min) / (max - min)) * cH;

  // grid lines
  ctx.strokeStyle = 'rgba(128,128,128,0.15)';
  ctx.lineWidth   = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (i / 4) * cH;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + cW, y); ctx.stroke();
    const label = Math.round(max - i * (max - min) / 4);
    ctx.fillStyle = 'rgba(128,128,128,0.7)';
    ctx.font = `${10}px sans-serif`;
    ctx.textAlign = 'right';
    ctx.fillText(label, pad.l - 4, y + 4);
  }

  // fill
  const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + cH);
  grad.addColorStop(0, 'rgba(59,130,246,0.3)');
  grad.addColorStop(1, 'rgba(59,130,246,0.02)');
  ctx.beginPath();
  measurements.forEach((m, i) => {
    i === 0 ? ctx.moveTo(xOf(i), yOf(m.v)) : ctx.lineTo(xOf(i), yOf(m.v));
  });
  ctx.lineTo(xOf(measurements.length - 1), pad.t + cH);
  ctx.lineTo(xOf(0), pad.t + cH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // line
  ctx.beginPath();
  ctx.strokeStyle = '#3b82f6';
  ctx.lineWidth   = 2;
  ctx.lineJoin    = 'round';
  measurements.forEach((m, i) => {
    i === 0 ? ctx.moveTo(xOf(i), yOf(m.v)) : ctx.lineTo(xOf(i), yOf(m.v));
  });
  ctx.stroke();

  // x-axis labels (every ~4h)
  ctx.fillStyle = 'rgba(128,128,128,0.7)';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(measurements.length / 6));
  for (let i = 0; i < measurements.length; i += step) {
    const dt  = new Date(measurements[i].ts);
    const lbl = dt.getHours().toString().padStart(2, '0') + ':' + dt.getMinutes().toString().padStart(2, '0');
    ctx.fillText(lbl, xOf(i), H - 6);
  }
}

function _updateWlOverlay(data) {
  const val   = $('wlDetailValue');
  const trend = $('wlDetailTrend');
  const delta = $('wlDetailDelta');
  const img   = $('wlForecastImg');
  if (!data) return;
  if (val) {
    const nhn = _nhnLabel(data);
    val.textContent = nhn ?? (data.current_cm != null ? Math.round(data.current_cm) + ' cm' : '-- cm');
  }
  if (trend) {
    trend.textContent = _trendArrow(data.trend);
    trend.style.color = _trendColor(data.trend);
  }
  if (delta && data.delta_cm != null) {
    const sign = data.delta_cm >= 0 ? '+' : '';
    delta.textContent = `${sign}${data.delta_cm} cm · Pegel ${Math.round(data.current_cm)} cm (30 min)`;
  }
  if (img && data.forecast_img) {
    img.src = data.forecast_img + '?t=' + Math.floor(Date.now() / 300000);
  }
  _renderWlChart(data.measurements);
}

function fetchWaterLevel() {
  fetch('/api/waterlevel')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      _wlData = d;
      _updateTopbarChip(d);
    })
    .catch(() => {});
}

function openWaterLevel() {
  $('wlOverlay').classList.remove('hidden');
  _updateWlOverlay(_wlData);
  if (!_wlData) fetch('/api/waterlevel').then(r => r.ok ? r.json() : null).then(d => { if (d) { _wlData = d; _updateWlOverlay(d); _updateTopbarChip(d); } });
}

function closeWaterLevel() {
  $('wlOverlay').classList.add('hidden');
}
