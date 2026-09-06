// ── Wasserstand ─────────────────────────────────────────────────────────────
//
// Bis zu fünf Pegel, durchgeschaltet mit einem Tipp auf den Namen — dieselbe
// Bediengrammatik wie bei der Wetterkachel. Der Heimatpegel beantwortet nicht,
// ob man im Zielhafen abends noch über die Schwelle kommt.
//
// Anders als beim Wetter ist ein Pegel keine Koordinate, sondern eine
// Messstelle mit eigener Kennung: die Zahl allein sähe an der Nachbarmole
// schon anders aus, weil jeder Pegel seinen eigenen Nullpunkt hat. Deshalb
// rechnet der Pi sie in cm über NHN um, mit dem Nullpunkt DIESES Pegels.

let _wlData = null;
let _wlStationen = [{ name: 'Travemünde', uuid: '' }];
// Ob die Liste GEPFLEGT ist oder nur die Vorgabe des Servers. Der Unterschied
// zaehlt in den Einstellungen: dort soll "noch nichts eingetragen" stehen und
// nicht ein Eintrag, den niemand angelegt hat.
let _wlGepflegt = false;
let _wlIndex = 0;

const _WL_SPEICHER = 'mave.pegel.station';
// Beim Durchschalten soll die Kachel nicht bei jedem Tipp leer werden.
const _wlCache = new Map();

function _wlEsc(t) {
  return String(t == null ? '' : t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _wlStation() {
  if (!(_wlIndex >= 0 && _wlIndex < _wlStationen.length)) _wlIndex = 0;
  return _wlStationen[_wlIndex] || { name: '', uuid: '' };
}

/** Ein Tipp auf den Pegelnamen: einen weiter. */
function wlStationWeiter(richtung = 1) {
  const n = _wlStationen.length;
  if (n < 2) return;
  _wlIndex = ((_wlIndex + richtung) % n + n) % n;
  try { localStorage.setItem(_WL_SPEICHER, String(_wlIndex)); } catch (_) {}
  _wlNamenSetzen();
  fetchWaterLevel();
}

function _wlNamenSetzen() {
  const st = _wlStation();
  const feld = $('wlStationName');
  if (feld) feld.textContent = st.name;
  // Bei nur einem Pegel ist der Pfeil eine Lüge — dann bleibt der Name Text.
  const pfeil = $('wlStationKnopf') && $('wlStationKnopf').querySelector('svg');
  if (pfeil) pfeil.style.display = _wlStationen.length > 1 ? '' : 'none';
  const titel = $('wlDetailTitel');
  if (titel) titel.textContent = st.name ? `Wasserstand ${st.name}` : 'Wasserstand';
  const leiste = $('wlStationLeiste');
  if (leiste) {
    leiste.hidden = _wlStationen.length < 2;
    leiste.innerHTML = _wlStationen.map((p, i) =>
      `<button class="wx-chip${i === _wlIndex ? ' aktiv' : ''}" data-index="${i}">${_wlEsc(p.name)}</button>`).join('');
  }
}

/** Die gepflegten Pegel — einmal beim Start und nach dem Speichern. */
function fetchPegelOrte() {
  return fetch('/api/pegel/orte')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d || !Array.isArray(d.stationen) || !d.stationen.length) return;
      _wlStationen = d.stationen;
      _wlGepflegt  = !!d.gepflegt;
      try {
        const i = parseInt(localStorage.getItem(_WL_SPEICHER), 10);
        if (!isNaN(i)) _wlIndex = i;
      } catch (_) {}
      _wlNamenSetzen();
    })
    .catch(() => {});
}

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

// _updateTopbarChip() stand hier: sie fuellte den Pegel-Chip in der
// Kopfzeile. Der Chip ist entfernt (doppelt zur Statusleiste), die
// Funktion damit gegenstandslos. Alles Uebrige der Wasserstandsseite
// bleibt unveraendert.

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

/**
 * Der Verlauf auf der Detailseite — in cm über NHN, wie die große Zahl darüber.
 *
 * Vorher stand hier der rohe Pegelstand: die Überschrift sagte „+12 cm NHN",
 * die Achse daneben „515". Zwei Zahlen für denselben Wasserstand, und keine
 * davon falsch — aber zusammen unlesbar. Seit der Pegelnullpunkt vom Pegel
 * selbst kommt, lässt sich die Achse mitrechnen.
 */
function _renderWlChart(measurements, versatzCm = 0) {
  const canvas = $('wlCanvas');
  if (!canvas) return;
  const pts = _wlPoints(measurements).map(p => ({ f: p.f, v: p.v + versatzCm }));
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
    const wert = Math.round(max - i * (max - min) / 4);
    const label = i === 0 ? `${wert > 0 ? '+' : ''}${wert} cm` : String(wert);
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

function _wlOffen() {
  const o = $('wlOverlay');
  return o && !o.classList.contains('hidden');
}

/**
 * Die Detailseite fuellen.
 *
 * Nur wenn sie offen ist, und das ist kein Feinschliff: hier haengt das
 * Vorhersagebild des BSH dran, rund 330 kB. Bei jedem Abruf im Hintergrund
 * (alle zehn Minuten) waere das ein Drittel Megabyte ueber die Mobilfunk-
 * verbindung des Bootes, fuer ein Bild, das niemand ansieht.
 */
function _updateWlOverlay(data) {
  if (!_wlOffen()) return;
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
  // Für die allermeisten Pegel gibt es keine BSH-Kurve — dann fällt der
  // ganze Block weg statt einen leeren Rahmen zu zeigen.
  const block = $('wlPrognoseBlock');
  if (block) block.hidden = !data.forecast_img;
  if (img && data.forecast_img) {
    img.src = data.forecast_img + '?t=' + Math.floor(Date.now() / 300000);
  }
  const quelle = $('wlDetailQuelle');
  if (quelle) {
    const pnp = data.station && data.station.pnp_m;
    quelle.textContent = 'Messung WSV · cm über NHN'
      + (pnp != null ? ` · Pegelnull ${pnp.toFixed(3)} m NHN` : '');
  }
  const pnp = data.station && data.station.pnp_m;
  _renderWlChart(data.measurements, pnp != null ? pnp * 100 : 0);
}

/** Alles auf Strich — solange die Antwort fuer diesen Pegel noch aussteht. */
function _wlLeeren() {
  for (const id of ['wlTileVal', 'wlTileValH', 'wlDetailValue']) {
    const el = $(id);
    if (el) el.textContent = id === 'wlDetailValue' ? '-- cm' : '--';
  }
  for (const id of ['wlTileTrend', 'wlTileTrendH', 'wlDetailTrend', 'wlDetailDelta']) {
    const el = $(id);
    if (el) el.textContent = '';
  }
  for (const id of ['wlTileSpark', 'wlCanvas']) {
    const c = $(id);
    if (c && c.width) c.getContext('2d').clearRect(0, 0, c.width, c.height);
  }
  const block = $('wlPrognoseBlock');
  if (block) block.hidden = true;
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
  const st = _wlStation();
  const bekannt = _wlCache.get(st.uuid);
  // Ohne Zwischenspeicher erst leeren: sonst steht die Zahl des vorherigen
  // Pegels unter dem neuen Namen, und zwar so lange, wie pegelonline braucht.
  _wlData = bekannt || null;
  if (bekannt) { _updateWlTile(bekannt); _updateWlOverlay(bekannt); }
  else _wlLeeren();

  // gibt das Promise zurueck: der gemeinsame Start wartet darauf
  return fetch('/api/waterlevel' + (st.uuid ? '?station=' + encodeURIComponent(st.uuid) : ''))
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      _wlCache.set(st.uuid, d);
      // Die Antwort kann zu einem Pegel gehoeren, von dem laengst
      // weggetippt wurde. Dann in den Speicher, aber nicht auf den Schirm.
      if (_wlStation().uuid !== st.uuid) return;
      _wlData = d;
      _updateWlTile(d);
      _updateWlOverlay(d);
    })
    .catch(() => {});
}

function _wlBinden() {
  wischenBinden($('wlCard'), wlStationWeiter);
  const leiste = $('wlStationLeiste');
  if (leiste) leiste.addEventListener('click', e => {
    const k = e.target.closest('[data-index]');
    if (!k) return;
    _wlIndex = parseInt(k.dataset.index, 10) || 0;
    try { localStorage.setItem(_WL_SPEICHER, String(_wlIndex)); } catch (_) {}
    _wlNamenSetzen();
    fetchWaterLevel();
  });
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
  _wlNamenSetzen();
  _updateWlOverlay(_wlData);
  // Liegen noch keine Daten vor, holt fetchWaterLevel sie fuer den gewaehlten
  // Pegel — der eigene Abruf hier holte immer den ersten.
  if (!_wlData) fetchWaterLevel();
}

function closeWaterLevel() {
  $('wlOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}
