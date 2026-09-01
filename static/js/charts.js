// ── Battery detail + charts ────────────────────────────────────────────────

// Der Client-Puffer muss den groessten Zeitknopf abdecken, sonst nuetzt der
// serverseitige Wochenverlauf nichts: gemergte Wochenpunkte wurden vorher
// sofort wieder weggeschnitten (25 h Kappung), und "7 Tage" blieb zu 85 % leer.
// Er enthaelt beides — 5-Sekunden-Werte der letzten Stunden und Minutenmittel
// der Woche —, deshalb die groessere Stueckzahl.
const HIST_MAX = 20000;
const HIST_MAX_AGE_S = 7 * 86400 + 3600;   // Verlauf zeitbasiert kappen, nicht stueckbasiert
const HIST_MIN_GAP_S = 5;           // gleiche Kadenz wie der Server (main.py)
const histData = [];  // [{ts, soc, voltage, current}, ...]

// ── Zeitbasis ───────────────────────────────────────────────────────────────
// Der Pi Zero W hat KEINE Echtzeituhr. Nach einem Stromausfall laeuft seine Uhr
// falsch, bis NTP greift. Filtern wir Zeitfenster gegen Date.now() des Telefons,
// schneidet ein Uhrenversatz unter Umstaenden ALLE Punkte weg — der Graph waere
// leer, obwohl saubere Daten vorliegen. Deshalb rechnen wir mit der Server-Zeit.
let _clockOffset = 0;   // server_now - Telefonzeit, in Sekunden

function setClockOffset(serverNow) {
  const versatz = serverNow - Date.now() / 1000;
  // Unter einer Sekunde ist Messrauschen, darueber ist es echt.
  _clockOffset = Math.abs(versatz) < 1 ? 0 : versatz;
  if (_clockOffset) console.info('Uhrenversatz Pi/Geraet: %.1f s', _clockOffset);
}

/** Aktuelle Zeit auf der Zeitachse des Servers (Unix-Sekunden). */
function nowTs() { return Date.now() / 1000 + _clockOffset; }

/** Kappt einen Verlauf zeitbasiert und als Notbremse stueckbasiert. */
function trimHist(arr, maxAgeS, maxLen) {
  const grenze = nowTs() - maxAgeS;
  let weg = 0;
  while (weg < arr.length && arr[weg].ts < grenze) weg++;
  if (weg) arr.splice(0, weg);
  if (arr.length > maxLen) arr.splice(0, arr.length - maxLen);
}

// domain: feste Achsen-Grenzen [min,max] oder null = automatisch aus Daten
const SERIES_DEF = {
  soc:      { color: '#22c55e', unit: '%',  label: 'SOC',       fmt: v => Math.round(v) + ' %',         domain: [0, 100] },
  voltage:  { color: '#06b6d4', unit: 'V',  label: 'Spannung',  fmt: v => v.toFixed(2) + ' V',          minSpan: 0.4, tau: 125 },
  current:  { color: '#f97316', unit: 'A',  label: 'Strom',     fmt: v => v.toFixed(1) + ' A',          minSpan: 2.0, zero: true, tau: 125 },
  solar:    { color: '#eab308', unit: 'W',  label: 'Solar',     fmt: v => Math.round(v) + ' W',         minSpan: 20,  zero: true, tau: 42 },
  zelldiff: { color: '#a78bfa', unit: 'mV', label: 'Zelldiff.', fmt: v => Math.round(v * 1000) + ' mV', minSpan: 0.03, zero: true, tau: 250 },
};

const CH_NAMES = ['Küche', 'Kartentisch', 'Salon', 'Achtkabine stbd'];

let chartSecondary = 'current';       // aktive Sekundär-Serie (rechte Achse) oder null
let chartRangeSec  = 1800;
// chartHoverPos ist der Bruchteil des Zeitfensters unter dem Finger (0…1),
// gesetzt von den Touch-/Maus-Handlern in lightdetail.js. Gerechnet wird aber
// mit _scrubTs — siehe Kommentar in _zeichneCharts().
let chartHoverPos  = null; // null=live, 0.0–1.0=Bruchteil des Zeitfensters
let _scrubTs       = null; // an einen ZEITPUNKT gebundene Scrub-Position
let _scrubPosBasis = null; // Bruchteil, aus dem _scrubTs berechnet wurde
let _lastSolarW   = null;

/**
 * Traegt einen Messpunkt in den Client-Verlauf ein.
 *
 * Wurde vorher bei JEDER WebSocket-Nachricht aufgerufen und ungetaktet
 * gepusht, waehrend HIST_MAX nach ANZAHL statt nach Zeit begrenzte. Damit
 * deckte der Puffer nur Minuten ab — die Zeitknoepfe 6 h / 12 h / 24 h
 * konnten konstruktionsbedingt nie gefuellt werden.
 *
 * Jetzt: gleiche 5-Sekunden-Kadenz wie der Server, und zeitbasiert gekappt.
 */
function recordHistory(b) {
  const ts = nowTs();
  const letzter = histData.length ? histData[histData.length - 1] : null;
  if (letzter && (ts - letzter.ts) < HIST_MIN_GAP_S) return;

  // Zelldiff hier selbst nachziehen, statt auf die Reihenfolge in handleData
  // zu vertrauen: dort laeuft updateBattery (und damit diese Funktion) VOR
  // updateBms, das _lastZelldiff setzt. Der erste lokal aufgezeichnete Eintrag
  // nach jedem Seitenaufbau hatte deshalb gar keinen Zelldiff-Wert — die
  // Glaettung setzte dort zurueck und das Segment riss auf. Nebenbei ist der
  // Wert damit auch nicht mehr einen WebSocket-Frame alt.
  const _bmsJetzt = _lastData?.bms;
  if (_bmsJetzt?.highest_cell_v != null && _bmsJetzt?.lowest_cell_v != null)
    _lastZelldiff = _bmsJetzt.highest_cell_v - _bmsJetzt.lowest_cell_v;

  const entry = { ts };
  if (b.soc      != null) entry.soc      = b.soc;
  if (b.voltage  != null) entry.voltage  = b.voltage;
  if (b.current  != null) entry.current  = b.current;
  if (_lastSolarW   != null) entry.solar    = _lastSolarW;
  if (_lastZelldiff != null) entry.zelldiff = _lastZelldiff;
  if (Object.keys(entry).length < 2) return; // kein Datenwert — nicht pushen
  histData.push(entry);
  trimHist(histData, HIST_MAX_AGE_S, HIST_MAX);
}

/**
 * Glaettung mit ZEITKONSTANTE statt festem Faktor je Punkt.
 *
 * Vorher rechnete _ema  s = alpha*v + (1-alpha)*s  pro PUNKT, ohne den
 * zeitlichen Abstand. Der Puffer besteht aber aus zwei Abschnitten mit sehr
 * verschiedener Dichte: links die vom Server ausgeduennten History-Punkte
 * (~36 s auseinander), rechts die lokal aufgezeichneten (5 s). Die wirksame
 * Zeitkonstante war damit Abstand/alpha — im Serverteil 1800 s, im lokalen
 * Teil 250 s, also Faktor 7 genau an der Nahtstelle. Dort knickte die Kurve
 * sichtbar ab; bei Zelldiff am staerksten, weil die Serie das kleinste alpha
 * hatte und ihre Achse auf wenige Millivolt zoomt.
 *
 * Jetzt ist die Zeitkonstante tau (in Sekunden) definiert und der Faktor wird
 * aus dem tatsaechlichen Abstand gerechnet: w = 1 - e^(-dt/tau). In beiden
 * Abschnitten glaettet es damit gleich stark. Kein Messwert wird veraendert,
 * und ein echter Zellausreisser schlaegt mit derselben, jetzt definierten
 * Zeitkonstante durch.
 */
function _emaZeit(pts, key, tauS, maxGapS) {
  let s = null, tVor = null;
  return pts.map(d => {
    const v = d[key];
    if (v == null) { s = null; tVor = null; return d; }
    const dt = tVor === null ? null : d.ts - tVor;
    if (dt === null || dt <= 0 || dt > maxGapS) s = v;   // Neustart nach Luecke
    else { const w = 1 - Math.exp(-dt / tauS); s = w * v + (1 - w) * s; }
    tVor = d.ts;
    const r = Object.assign({}, d); r[key] = s; return r;
  });
}

// Berechnet [lo,hi] Achsen-Domäne einer Serie aus den Werten + Definition.
// Gibt null zurueck, wenn kein brauchbarer Wert dabei ist — der Aufrufer
// zeichnet die Serie dann gar nicht.
//
// Vorher stand hier Math.min(...vals): der Spread legt bei grossen Arrays den
// Argument-Stack um (RangeError, Graph bleibt schwarz), und bei leerem vals
// liefert er +Infinity/-Infinity. Daraus wurde ueber die Padding-Rechnung NaN,
// und mit NaN-Grenzen wurde nichts mehr gezeichnet — ohne jede Fehlermeldung.
function _seriesDomain(vals, def) {
  if (def.domain) return def.domain.slice();
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i];
    if (typeof v !== 'number' || !isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!isFinite(lo) || !isFinite(hi)) return null;
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
  renderCharts(true);
}

/**
 * Zeitbereich der Batterie-Detailseite — EINER fuer alle Diagramme.
 *
 * Feinverlauf und Stromverlauf hatten getrennte Knopfreihen mit
 * unterschiedlichen Stufen (30 min/6/12/24 h gegen 2/6/12/16 h). Beim
 * Vergleichen musste man beide von Hand nachfuehren und sah trotzdem leicht
 * verschiedene Fenster. Jetzt schaltet jede Reihe BEIDE Diagramme, und beide
 * Reihen zeigen denselben Stand.
 */
const ZEITBEREICHE = [
  { label: '30 min',  secs: 1800 },
  { label: '6 Std',   secs: 6 * 3600 },
  { label: '24 Std',  secs: 24 * 3600 },
  { label: '7 Tage',  secs: 7 * 86400 },
];

function setZeitbereich(secs) {
  chartRangeSec = secs;
  chartHoverPos = null;
  // Beide Knopfreihen nachfuehren, egal welche geklickt wurde.
  document.querySelectorAll('.chart-range').forEach(b =>
    b.classList.toggle('active', Number(b.dataset.secs) === secs));
  if (typeof _vlBereich !== 'undefined') {
    _vlBereich = secs;
    if (typeof _vlBereichKnoepfe === 'function') _vlBereichKnoepfe();
  }
  renderCharts(true);
  if (typeof ladeVerlauf === 'function') ladeVerlauf(true);
  // Fuer kurze Fenster den Verlauf feiner nachladen: der grosse Abruf liefert
  // fuer 24 h nur alle ~36 s einen Punkt, was am linken Rand eines
  // 30-Minuten-Fensters einen leeren Streifen stehen laesst.
  fetchHistoryFenster(secs);
}

// Alter Name bleibt: die Knoepfe in index.html rufen ihn auf.
function setChartRange(btn, secs) { setZeitbereich(secs); }

/**
 * Legende unter dem Graphen.
 *
 * rohPts     — ungefilterte Messpunkte des Zeitfensters (weder zusammengefasst
 *              noch geglaettet)
 * gezeichnet — {Serie: Punkte}, genau die Reihen, die als Linie im Bild stehen
 *
 * Im LIVE-Zustand zeigt die Legende den letzten echten Messwert aus rohPts.
 * Vorher stand dort der letzte Punkt der gezeichneten Reihe — und der ist ein
 * EMA-geglaetteter Mittelwert ueber einen zusammengefassten Zeit-Bucket. Bei
 * springendem Strom widersprach die Legende damit der Kachel daneben (Kachel
 * "-12,4 A", Legende "-6,1 A"), was auf einem Batteriemonitor unbrauchbar ist.
 * Beim Scrubben bleibt die gezeichnete Reihe die Quelle: der Wert muss zu dem
 * Punkt passen, auf dem das Fadenkreuz sitzt.
 */
function renderChartLegend(rohPts, gezeichnet, scrubTs) {
  const leg = $('chartLegend');
  if (!leg) return;
  const entries = [{ key: 'soc' }];
  if (chartSecondary) entries.push({ key: chartSecondary });
  leg.innerHTML = entries.map(({ key }) => {
    const def = SERIES_DEF[key];
    const quelle = scrubTs != null ? (gezeichnet[key] ?? rohPts) : rohPts;
    const ptsK = quelle.filter(d => d[key] != null);
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

/**
 * Achsenbeschriftung.
 *
 * Die Nachkommastellen richten sich nach dem TICK-ABSTAND, nicht nach der
 * Groesse. Vorher stand fest `toFixed(0)` fuer Strom: liegt der Strom nahe
 * null, waehlt _niceTicks Schritte von 0,5 A — und die Achse las sich am
 * Geraet als "1A / 0A / -1A / -1A", zwei gleiche Zahlen auf verschiedenen
 * Hoehen. Bei Spannung dieselbe Falle (13,30 gegen 13,31 V).
 */
function _achsenDez(step) {
  if (!isFinite(step) || step <= 0) return 0;
  if (step >= 1)   return 0;
  if (step >= 0.1) return 1;
  if (step >= 0.01) return 2;
  return 3;
}

function fmtYVal(v, key, step) {
  const d = _achsenDez(step);
  if (key === 'soc')      return v.toFixed(Math.min(d, 1)) + '%';
  if (key === 'voltage')  return v.toFixed(Math.max(1, d)) + 'V';
  if (key === 'current')  return v.toFixed(d) + 'A';
  if (key === 'zelldiff') return Math.round(v * 1000) + 'mV';
  return v.toFixed(d);
}

/** Abstand zweier Ticks — Grundlage fuer die Nachkommastellen. */
function _tickStep(ticks) {
  return (Array.isArray(ticks) && ticks.length > 1)
    ? Math.abs(ticks[1] - ticks[0]) : 1;
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

/**
 * Fasst den Verlauf auf hoechstens maxPts Punkte zusammen (Mittelwert je Bucket).
 *
 * Die Schluessel werden ueber ALLE Elemente des Buckets gesammelt. Vorher wurden
 * nur die Schluessel des ERSTEN Bucket-Elements uebernommen: fehlte dort z. B.
 * `solar` (Solar meldet seltener als der Shunt), verschwand die Serie fuer den
 * ganzen Bucket — die Linie riss immer wieder ab, obwohl Daten vorlagen.
 */
function _decimate(pts, maxPts) {
  if (pts.length <= maxPts) return pts;
  const out = [], step = pts.length / maxPts;
  for (let i = 0; i < maxPts; i++) {
    const lo = Math.floor(i * step), hi = Math.min(pts.length, Math.floor((i+1)*step));
    if (hi <= lo) continue;                       // leerer Bucket (Rundung)
    const summe = {}, anzahl = {};
    for (let j = lo; j < hi; j++) {
      const d = pts[j];
      for (const k in d) {
        if (k === 'ts') continue;
        const v = d[k];
        if (typeof v !== 'number' || !isFinite(v)) continue;
        summe[k]  = (summe[k]  ?? 0) + v;
        anzahl[k] = (anzahl[k] ?? 0) + 1;
      }
    }
    const merged = { ts: pts[lo + ((hi - lo) >> 1)].ts };
    for (const k in summe) merged[k] = summe[k] / anzahl[k];
    out.push(merged);
  }
  return out;
}

// ── Zeichentakt ─────────────────────────────────────────────────────────────
// renderCharts() wird bei JEDER WebSocket-Nachricht gerufen (am Geraet gemessen
// 2,42/s) und zeichnet jedes Mal das komplette Canvas neu — inklusive Filtern,
// Zusammenfassen und Glaetten des Verlaufs. Auf dem Pi Zero W (ein Kern) ist
// das verschenkte Rechenzeit, mehr als ein Bild pro Sekunde sieht ohnehin
// niemand. Deshalb: hoechstens 1 Hz, und gezeichnet wird im
// requestAnimationFrame (im Hintergrund-Tab laeuft der gar nicht erst).
//
// Bedienung geht vor: Scrubben, Zeitfenster- und Serienwechsel zeichnen sofort
// (sofort=true bzw. veraenderte Fingerposition), werden aber ueber denselben
// rAF zu einem Bild pro Frame zusammengefasst.
const CHART_MIN_INTERVAL_MS = 1000;
let _chartRaf = null, _chartTimer = null, _chartLetzteZeichnung = 0, _chartHoverGesehen = null;

function renderCharts(sofort = false) {
  if (chartHoverPos !== _chartHoverGesehen) {   // Finger bewegt/abgehoben = Bedienung
    _chartHoverGesehen = chartHoverPos;
    sofort = true;
  }
  if (_chartRaf !== null) return;               // Bild ist schon angemeldet
  const seit = Date.now() - _chartLetzteZeichnung;
  if (!sofort && seit < CHART_MIN_INTERVAL_MS) {
    // Den letzten Stand nachziehen, sobald der Takt es erlaubt — genau ein Timer.
    if (_chartTimer === null)
      _chartTimer = setTimeout(() => { _chartTimer = null; renderCharts(true); },
                               CHART_MIN_INTERVAL_MS - seit);
    return;
  }
  if (_chartTimer !== null) { clearTimeout(_chartTimer); _chartTimer = null; }
  _chartRaf = requestAnimationFrame(() => {
    _chartRaf = null;
    _chartLetzteZeichnung = Date.now();
    _zeichneCharts();
  });
}

function _zeichneCharts() {
  const canvas = $('chartMain');
  if (!canvas) return;

  const now    = nowTs();
  const cutoff = now - chartRangeSec;
  // rohPts: unveraenderte Messwerte — Quelle fuer die Legende im Live-Zustand.
  const rohPts = histData.filter(d => d.ts >= cutoff);
  const pts    = _decimate(rohPts, 2000);

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

  // Der Scrub haengt an einem ZEITPUNKT, nicht am Fensteranteil. Das Fenster
  // wandert mit jedem Tick nach rechts; wuerde bei jedem Bild erneut
  // tMin0 + Anteil * Fenster gerechnet, zeigte das Fadenkreuz bei ruhendem
  // Finger jede Sekunde einen anderen Messwert — der Wert wanderte unter dem
  // Finger weg. Umgerechnet wird deshalb nur, wenn die Bedienung den Anteil
  // aendert; danach bleibt der Zeitpunkt stehen.
  if (chartHoverPos === null) {
    _scrubTs = null; _scrubPosBasis = null;
  } else if (chartHoverPos !== _scrubPosBasis) {
    _scrubPosBasis = chartHoverPos;
    _scrubTs = tMin0 + chartHoverPos * chartRangeSec;
  } else if (_scrubTs !== null) {
    // Haelt der Finger lange still, laeuft der Zeitpunkt irgendwann aus dem
    // Fenster — dann am Rand festhalten statt ins Leere zu zeigen.
    _scrubTs = Math.max(tMin0, Math.min(now, _scrubTs));
  }
  const scrubTs = _scrubTs;

  // Groesster Abstand, ueber den hinweg noch verbunden (und geglaettet) wird.
  // Stand frueher weiter unten; die Glaettung braucht ihn jetzt auch.
  const maxGapSec = Math.max(30, chartRangeSec / 30);

  // Sekundaer-Punkte glaetten: die Zeitkonstante kommt aus SERIES_DEF.
  const secDef = chartSecondary ? SERIES_DEF[chartSecondary] : null;
  const secPts = (chartSecondary && typeof secDef?.tau === 'number')
    ? _emaZeit(pts, chartSecondary, secDef.tau, maxGapSec)
    : pts;

  const gezeichnet = { soc: pts };
  if (chartSecondary) gezeichnet[chartSecondary] = secPts;
  renderChartLegend(rohPts, gezeichnet, scrubTs);

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
    const dom  = vals.length >= 2 ? _seriesDomain(vals, def) : null;
    if (dom) {
      const [lo, hi] = dom;
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
      ctx.fillText(fmtYVal(v, yLeft.key, _tickStep(yTicks)), PAD_L - 4, y);
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
      ctx.fillText(fmtYVal(v, yRight.key, _tickStep(rTicks)), PAD_L + CW + 5, y);
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
  // maxGapSec steht weiter oben — die Glaettung braucht denselben Wert.

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

/**
 * Jede Detailseite oeffnet oben.
 *
 * Gescrollt wird nicht das Fenster, sondern .ov-body im Overlay (style.css:
 * .overlay ist position:fixed, .ov-body hat flex:1 und overflow-y:auto).
 * Beim Schliessen bekommt das Overlay nur .hidden (display:none) — das
 * Element bleibt bestehen, und der Browser stellt den gemerkten Scrollstand
 * des Containers wieder her, sobald es erneut sichtbar wird. Zurueckgesetzt
 * hat das bisher niemand: keiner der 23 Aufrufe von classList.remove('hidden')
 * fasst scrollTop an. Die Batterieseite fiel nur am staerksten auf, weil sie
 * die laengste ist — betroffen waren alle.
 *
 * Deshalb hier zentral statt an 23 Stellen: der Beobachter laeuft ohnehin.
 * Zurueckgesetzt wird NUR beim Uebergang versteckt -> sichtbar; ein Schreiben
 * auf scrollTop erzwingt Layout, das soll nicht bei jeder Klassenaenderung
 * irgendwo im Dokument passieren.
 */
const _ovWarSichtbar = new WeakSet();

function _ovNachOben(ov) {
  // Das Overlay selbst mitnehmen: #wlOverlay hat als einziges keine .ov-body.
  if (ov.scrollTop) ov.scrollTop = 0;
  ov.querySelectorAll('.ov-body').forEach(b => { if (b.scrollTop) b.scrollTop = 0; });
}

// Beobachte alle .overlay-Elemente auf class-Änderungen
new MutationObserver(() => {
  let anyOpen = false;
  document.querySelectorAll('.overlay').forEach(el => {
    const offen = !el.classList.contains('hidden');
    if (offen) anyOpen = true;
    if (offen && !_ovWarSichtbar.has(el)) {
      _ovWarSichtbar.add(el);
      // Jetzt ist .hidden gerade weg, das Element hat wieder ein Layoutobjekt —
      // vorher (oder in den close-Funktionen) waere der Schreibzugriff
      // wirkungslos, weil display:none kein scrollTop kennt.
      _ovNachOben(el);
      // Einmal nachfassen: manche Browser stellen die gemerkte Position erst
      // im naechsten Frame wieder her.
      requestAnimationFrame(() => _ovNachOben(el));
    } else if (!offen) {
      _ovWarSichtbar.delete(el);
    }
  });
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

/**
 * Die Batterie-Detailseite anzeigen — der EINE Weg fuer alle Aufrufer.
 *
 * Es gibt drei: den Knopf auf der Startseite, den Kiosk-Tab "Energie" und die
 * Zurueck-/Vorwaerts-Geste (lightdetail.js). Der dritte hatte frueher einen
 * eigenen, schwaecheren Pfad mit setTimeout(renderCharts, 50). Seit auf der
 * Seite DREI Canvas liegen, ist das die Stelle, an der Diagramme leer bleiben:
 * ein verstecktes Canvas misst 0 und alle drei Zeichenfunktionen steigen dann
 * still aus. Deshalb hier gebuendelt, mit zwei Frames Vorlauf.
 */
function _battDetailAnzeigen() {
  $('battOverlay').classList.remove('hidden');
  if (_lastBms) updateBms(_lastBms);
  if (_lastData) renderDeviceTiles(_lastData);
  _vlBereichKnoepfe();
  // Erst nach zwei Frames: dann ist das Overlay sichtbar und die Canvas haben
  // ihre echte Breite.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    renderCharts(true);
    ladeVerlauf();
    _loadAndRenderWeekChart();
  }));
  // Der Feinverlauf steht beim Oeffnen auf 30 Minuten — dafuer ist der grosse
  // 24-Stunden-Abruf zu grob (dort liegen die Punkte ~36 s auseinander).
  // Einmal fein nachfassen, sonst bleibt links ein leerer Streifen stehen.
  fetchHistoryFenster(chartRangeSec);
}

function openBattDetail() {
  _closeAllOverlays();
  history.pushState({ overlay: 'battery' }, '', '#battery');
  _battDetailAnzeigen();
}

function _loadAndRenderWeekChart() {
  fetch('/api/daily-stats?days=7', { cache: 'no-store' })
    .then(r => r.json())
    .then(data => { _weekData = data; _renderWeekChart(data); })
    .catch(() => {});
}

function _renderWeekChart(data) {
  const canvas = $('weekChart');
  if (!canvas) return;
  const hinweis = $('weekHinweis');

  // Leerzustand.
  //
  // daily_stats liefert fuer Tage ohne Messwerte KEINE leere Liste, sondern
  // sieben Eintraege mit charged_ah/discharged_ah/avg_soc = null. Die alte
  // Pruefung (!data.length) griff dabei nie: gezeichnet wurde ein vollstaendiges
  // Diagramm mit der Achse "0,0 / 0,5 / 1,0 Ah" — eine erfundene Skala, die wie
  // ein Messergebnis aussieht. Jetzt wird bei fehlenden Daten gar nichts
  // gezeichnet und stattdessen gesagt, warum.
  const hatDaten = Array.isArray(data) && data.some(d =>
    (d.charged_ah ?? 0) > 0 || (d.discharged_ah ?? 0) > 0 || d.avg_soc != null);

  // Sichtbarkeit VOR jeder Messung setzen: ein display:none-Canvas hat
  // offsetWidth 0, und der Zeichencode weiter unten steigt dann still aus.
  canvas.style.display = hatDaten ? 'block' : 'none';
  if (hinweis) {
    hinweis.className   = hatDaten ? 'vl-hinweis' : 'vl-leer';
    hinweis.textContent = hatDaten
      ? 'Geladene und entladene Amperestunden je Tag (linke Achse), '
        + 'dazu der Tagesmittelwert des Ladezustands (rechte Achse).'
      : 'Für die letzten sieben Tage liegen noch keine Tageswerte vor. '
        + 'Der Trend füllt sich, sobald der Monitor einen Tag durchgelaufen ist.';
  }
  if (!hatDaten) return;

  const dpr = window.devicePixelRatio || 1;
  const W   = canvas.offsetWidth;
  const H   = canvas.offsetHeight;
  if (!W || !H) return;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  // Beide Achsen brauchen Platz fuer ihre Beschriftung: links steht sie
  // rechtsbuendig bei PAD.l - 3, rechts linksbuendig bei PAD.l + CW + 5. Mit
  // r:12 lag "100%" ausserhalb der Zeichenflaeche — die zweite Achse war zwar
  // gerechnet, aber schlicht nicht zu sehen.
  // Die Raender sind gegen die BREITESTE Schrift gemessen, die "9px sans-serif"
  // treffen kann: auf dem Pi ist das DejaVu Sans (Raspberry Pi OS). Dort ist
  // "100%" 25,7 px und "15.1 Ah" 34,8 px breit — mit r:30 / l:38 waere beides
  // um Haaresbreite angeschnitten worden. Auf schmaleren Schriften bleibt nur
  // etwas Luft stehen, das kostet keinen sichtbaren Balken.
  const PAD = { t: 8, r: 36, b: 28, l: 42 };
  const CW = W - PAD.l - PAD.r;
  const CH = H - PAD.t - PAD.b;

  // ZWEI Achsen: Ah links (Balken geladen/entladen), Prozent rechts (Ø SOC).
  // Der SOC-Balken wurde vorher zwar schon mit ySoc gerechnet, aber die einzige
  // beschriftete Achse war die Ah-Achse — ein 80-%-Balken las sich damit als
  // "80 Ah". Die rechte Achse macht den zweiten Massstab sichtbar.
  const maxAh = Math.max(
    ...data.map(d => Math.max(d.charged_ah || 0, d.discharged_ah || 0)), 1
  );
  const yAh  = v  => PAD.t + CH * (1 - v / maxAh);
  const ySoc = v  => PAD.t + CH * (1 - Math.max(0, Math.min(100, v)) / 100);

  // Ah-Beschriftung mit so vielen Nachkommastellen, wie der Achsenschritt
  // braucht. Mit Math.round() stand an einem ruhigen Tag "0 Ah / 1 Ah / 1 Ah"
  // an der Achse: drei Zahlen, von denen zwei gleich aussahen und keine den
  // gezeichneten Balken entsprach.
  const _ahSchritt = maxAh / 2;
  const _ahDez     = _ahSchritt >= 10 ? 0 : _ahSchritt >= 0.5 ? 1 : 2;
  const fmtAh      = v => v.toFixed(_ahDez) + ' Ah';

  // Hintergrund
  ctx.fillStyle = '#1e293b';
  ctx.fillRect(0, 0, W, H);

  // Y-Achse links: Ah (mit den Gitterlinien)
  ctx.font = '9px sans-serif'; ctx.textAlign = 'right';
  [0, maxAh * 0.5, maxAh].forEach(v => {
    const y = yAh(v);
    ctx.fillStyle = '#334155';
    ctx.fillRect(PAD.l, y, CW, 0.5);
    ctx.fillStyle = '#64748b';
    ctx.fillText(fmtAh(v), PAD.l - 3, y + 3);
  });

  // Y-Achse rechts: Prozent (Ø SOC) — in der Farbe des SOC-Balkens, damit
  // erkennbar ist, welcher Balken auf welchem Massstab steht.
  ctx.textAlign = 'left';
  ctx.fillStyle = 'rgba(59,130,246,0.85)';
  [0, 50, 100].forEach(v => {
    const y = ySoc(v);
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(59,130,246,0.35)'; ctx.lineWidth = 1;
    ctx.moveTo(PAD.l + CW, y); ctx.lineTo(PAD.l + CW + 3, y); ctx.stroke();
    ctx.fillText(v + '%', PAD.l + CW + 5, y + 3);
  });

  const barW   = CW / data.length;
  ctx.textAlign = 'center';
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

    // Tag-Beschriftung. Tage ohne jede Angabe (der Server liefert dort null,
    // nicht 0) werden gedimmt — ein leerer Tag ist "keine Daten", nicht "nichts
    // verbraucht".
    const ohneDaten = d.charged_ah == null && d.discharged_ah == null && d.avg_soc == null;
    const label = new Date(d.date + 'T12:00:00').toLocaleDateString('de-DE', { weekday: 'short' });
    ctx.fillStyle = ohneDaten ? '#3f4d61' : '#64748b';
    ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(label, cx, PAD.t + CH + 14);
  });
}

// Groessenwechsel: Canvas-Bitmaps haengen an der Pixelbreite, gezeichnet wird
// aber nur, wenn neue Messwerte kommen. Dreht das Telefon sich bei offenem
// Batterie-Detail, bliebe die alte Bitmap stehen und wuerde verzerrt
// hochskaliert. Deshalb beide Graphen neu zeichnen — entprellt ueber
// requestAnimationFrame, damit der eine Kern des Pi waehrend des Drehens nicht
// dutzendfach rechnet, und nur solange das Overlay ueberhaupt sichtbar ist.
let _chartResizeRaf = null;
function _chartsBeiGroessenwechsel() {
  if (_chartResizeRaf !== null) cancelAnimationFrame(_chartResizeRaf);
  _chartResizeRaf = requestAnimationFrame(() => {
    _chartResizeRaf = null;
    // Alle drei Diagramme der Batterieseite haengen an derselben Sichtbarkeit
    // und werden gemeinsam neu gezeichnet. Der Stromverlauf zeichnet aus
    // _vlDaten, ohne neuen Abruf — Drehen kostet also keinen Serverzugriff.
    const ov = $('battOverlay');
    if (!ov || ov.classList.contains('hidden')) return;
    renderCharts(true);
    if (typeof zeichneVerlauf === 'function') zeichneVerlauf();
    if (_weekData) _renderWeekChart(_weekData);
  });
}
window.addEventListener('resize', _chartsBeiGroessenwechsel);
window.addEventListener('orientationchange', _chartsBeiGroessenwechsel);

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
  // Restkapazität kommt ausschliesslich aus updateBattery() (konfigurierte
  // Bankkapazitaet + consumed_ah vom Shunt).
  //
  // Hier stand ein BMS-Rueckfall: bms.capacity_ah * bms.soc / 100, ausgegeben
  // in Ah. Der ist unbrauchbar, weil das Feld hoechstwahrscheinlich gar keine
  // Ah enthaelt: live meldet das BMS capacity_ah = 11,5 bei remaining_kwh =
  // 10,878 und soc = 95 — und 10,878 / 11,5 = 0,946, also exakt der SOC. Das
  // Feld verhaelt sich wie kWh. Als "11,5 Ah" waere es fuer eine Bank mit
  // 10,9 kWh Restenergie um Groessenordnungen daneben. Bis die Einheit am
  // BMS-Display geprueft ist, lieber "--" als eine falsche Zahl.
  // (Parser: nmea2000.py, parse_bms_pack, Offset 17.)
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
      // bms.cells sind Objekte ({voltage, temp}) — Math.min(...cells) lieferte
      // NaN, jeder Vergleich damit war false und der Punkt blieb IMMER gruen:
      // eine ausfallende Zelle wurde nicht angezeigt. Deshalb erst die
      // Spannungen herausziehen, dann Min/Max bilden.
      const zellV = (bms.cells ?? [])
        .map(c => c?.voltage)
        .filter(v => typeof v === 'number' && isFinite(v));
      const minV  = zellV.length ? Math.min(...zellV) : bms.lowest_cell_v;
      const maxV  = zellV.length ? Math.max(...zellV) : bms.highest_cell_v;
      const diff  = (minV != null && maxV != null) ? maxV - minV : null;
      // Fehlende Temperatur ist KEINE Temperatur von 0 °C: mit "?? 0" meldete
      // der Punkt bei einem BMS ohne Temperaturfuehler dauerhaft Unterkuehlung.
      const tempHigh = bms.highest_temp != null && bms.highest_temp > 40;
      const tempLow  = bms.lowest_temp  != null && bms.lowest_temp  < 5;
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
