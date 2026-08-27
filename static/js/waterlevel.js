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
  if (data.forecast_alarm) {
    chip.classList.add('wl-alarm');
  } else {
    chip.classList.remove('wl-alarm');
  }
}

// Eine einzelne Messlücke (v: null) darf die Grafik nicht kippen: null wird in
// JavaScript zu 0 gerechnet, Math.min/Math.max lieferten dadurch eine unsinnige
// Skala und der Kurvenzug verschwand. Deshalb bleiben nur endliche Werte übrig;
// ihre Position auf der Zeitachse (Index im Originalfeld) bleibt erhalten,
// sodass Lücken übersprungen und nicht zusammengeschoben werden.
function _wlNum(v) {
  if (v == null || v === '') return NaN;
  const n = Number(v);
  return Number.isFinite(n) ? n : NaN;
}

function _wlPoints(measurements) {
  if (!Array.isArray(measurements) || !measurements.length) return [];
  const span = Math.max(1, measurements.length - 1);
  const pts  = [];
  measurements.forEach((m, i) => {
    const v = _wlNum(m?.v);
    if (Number.isFinite(v)) pts.push({ f: i / span, v });   // f = 0…1 auf der Zeitachse
  });
  return pts;
}

function _wlRange(pts) {
  let min = Infinity, max = -Infinity;
  for (const p of pts) { if (p.v < min) min = p.v; if (p.v > max) max = p.v; }
  return { min, max };
}

function _renderWlChart(measurements) {
  const canvas = $('wlCanvas');
  if (!canvas) return;
  const pts = _wlPoints(measurements);
  if (pts.length < 2) return;
  const dpr = window.devicePixelRatio || 1;
  const W   = canvas.offsetWidth  || 600;
  const H   = canvas.offsetHeight || 180;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const span = _wlRange(pts);
  const min  = span.min - 5;
  const max  = span.max + 5;
  const pad  = { t: 10, r: 10, b: 24, l: 44 };
  const cW   = W - pad.l - pad.r;
  const cH   = H - pad.t - pad.b;

  const xOf = f => pad.l + f * cW;
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
  pts.forEach((p, i) => {
    i === 0 ? ctx.moveTo(xOf(p.f), yOf(p.v)) : ctx.lineTo(xOf(p.f), yOf(p.v));
  });
  ctx.lineTo(xOf(pts[pts.length - 1].f), pad.t + cH);
  ctx.lineTo(xOf(pts[0].f), pad.t + cH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // line
  ctx.beginPath();
  ctx.strokeStyle = '#3b82f6';
  ctx.lineWidth   = 2;
  ctx.lineJoin    = 'round';
  pts.forEach((p, i) => {
    i === 0 ? ctx.moveTo(xOf(p.f), yOf(p.v)) : ctx.lineTo(xOf(p.f), yOf(p.v));
  });
  ctx.stroke();

  // x-axis labels (every ~4h)
  ctx.fillStyle = 'rgba(128,128,128,0.7)';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(measurements.length / 6));
  const zeitSpanne = Math.max(1, measurements.length - 1);
  for (let i = 0; i < measurements.length; i += step) {
    const dt = new Date(measurements[i]?.ts);
    if (isNaN(dt.getTime())) continue;
    const lbl = dt.getHours().toString().padStart(2, '0') + ':' + dt.getMinutes().toString().padStart(2, '0');
    ctx.fillText(lbl, xOf(i / zeitSpanne), H - 6);
  }
}

function _updateWlOverlay(data) {
  const val   = $('wlDetailValue');
  const trend = $('wlDetailTrend');
  const delta = $('wlDetailDelta');
  const img   = $('wlForecastImg');
  const fcmin = $('wlForecastMin');
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
  if (fcmin) {
    if (data.forecast_min_nhn_cm != null) {
      const s = data.forecast_min_nhn_cm >= 0 ? '+' : '';
      fcmin.textContent = `Prognose Min: ${s}${data.forecast_min_nhn_cm} cm NHN`;
      fcmin.style.color = data.forecast_alarm ? 'var(--red)' : 'var(--text2)';
      fcmin.style.display = '';
    } else {
      fcmin.style.display = 'none';
    }
  }
  if (img && data.forecast_img) {
    img.src = data.forecast_img + '?t=' + Math.floor(Date.now() / 300000);
  }
  _renderWlChart(data.measurements);
}

function _updateWlTile(data) {
  if (!data) return;
  const nhn  = data.current_nhn_cm;
  const sign = nhn != null && nhn >= 0 ? '+' : '';
  const val  = nhn != null ? `${sign}${nhn}` : (data.current_cm != null ? Math.round(data.current_cm) : '--');
  const arrow = _trendArrow(data.trend);
  const color = _trendColor(data.trend);

  const _s = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  const _c = (id, c) => { const el = $(id); if (el) el.style.color  = c; };
  _s('wlTileVal',    val);   _s('wlTileTrend',  arrow);  _c('wlTileTrend',  color);
  _s('wlTileValH',   val);   _s('wlTileTrendH', arrow);  _c('wlTileTrendH', color);

  const card = $('wlCard');
  if (card) card.style.borderColor = data.forecast_alarm ? 'var(--red)' : '';

  _renderWlSpark(data.measurements);
}

// Mini-Verlaufsgrafik in der Home-Kachel (füllt das Quadrat, ohne Achsen).
function _renderWlSpark(measurements) {
  const canvas = $('wlTileSpark');
  if (!canvas) return;
  const pts = _wlPoints(measurements);
  if (pts.length < 2) return;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth, H = canvas.offsetHeight;
  if (!W || !H) return;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const { min, max } = _wlRange(pts);
  const rng = (max - min) || 1;
  const pad = 6;
  const xOf = f => f * W;
  const yOf = v => pad + (1 - (v - min) / rng) * (H - 2 * pad);

  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, 'rgba(59,130,246,0.30)');
  grad.addColorStop(1, 'rgba(59,130,246,0.00)');
  ctx.beginPath();
  pts.forEach((p, i) => i === 0 ? ctx.moveTo(xOf(p.f), yOf(p.v)) : ctx.lineTo(xOf(p.f), yOf(p.v)));
  ctx.lineTo(xOf(pts[pts.length - 1].f), H); ctx.lineTo(xOf(pts[0].f), H); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();

  ctx.beginPath();
  ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 2; ctx.lineJoin = 'round';
  pts.forEach((p, i) => i === 0 ? ctx.moveTo(xOf(p.f), yOf(p.v)) : ctx.lineTo(xOf(p.f), yOf(p.v)));
  ctx.stroke();
}

function fetchWaterLevel() {
  fetch('/api/waterlevel')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      _wlData = d;
      _updateTopbarChip(d);
      _updateWlTile(d);
    })
    .catch(() => {});
}

function openWaterLevel() {
  // Gleiches Muster wie alle anderen Overlays: erst aufräumen, dann einen
  // History-Eintrag setzen. Ohne den Eintrag verließ die Zurück-Geste die App,
  // statt nur dieses Overlay zu schließen.
  _closeAllOverlays();
  history.pushState({ overlay: 'waterlevel' }, '', '#waterlevel');
  _wlOverlayAnzeigen();
}

// Overlay sichtbar machen und fuellen — ohne History anzufassen. Wird auch von
// der Zurueck-/Vorwaerts-Geste benutzt (popstate-Karte in lightdetail.js), die
// den History-Eintrag schon hat. Liegen noch keine Daten vor (erster Abruf
// fehlgeschlagen, naechster Poll erst in bis zu 10 min), wird einmal
// nachgeholt, sonst bliebe die Ansicht leer.
function _wlOverlayAnzeigen() {
  $('wlOverlay').classList.remove('hidden');
  _updateWlOverlay(_wlData);
  if (!_wlData) fetch('/api/waterlevel').then(r => r.ok ? r.json() : null).then(d => { if (d) { _wlData = d; _updateWlOverlay(d); _updateTopbarChip(d); } }).catch(() => {});
}

function closeWaterLevel() {
  $('wlOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}
