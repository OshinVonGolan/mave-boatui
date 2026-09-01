// ── SOC-Gauge ──────────────────────────────────────────────────────────────

const GAUGE_CX = 80, GAUGE_CY = 68, GAUGE_R = 58;
const GAUGE_START = 225, GAUGE_SWEEP = 270;

function polarXY(cx, cy, r, deg) {
  const rad = (deg - 90) * Math.PI / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}
function arcPath(cx, cy, r, start, sweep) {
  const [x1,y1] = polarXY(cx,cy,r,start);
  const [x2,y2] = polarXY(cx,cy,r,start+sweep);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${sweep>180?1:0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}
$('gaugeTrack').setAttribute('d', arcPath(GAUGE_CX,GAUGE_CY,GAUGE_R,GAUGE_START,GAUGE_SWEEP));

function updateGauge(soc) {
  const pct   = soc == null ? 0 : Math.max(0, Math.min(100, soc));
  const sweep = GAUGE_SWEEP * pct / 100;
  const color = pct >= 50 ? 'var(--green)' : pct >= 20 ? 'var(--yellow)' : 'var(--red)';
  $('gaugeValue').setAttribute('d', sweep < 2 ? '' : arcPath(GAUGE_CX,GAUGE_CY,GAUGE_R,GAUGE_START,sweep));
  $('gaugeValue').style.stroke = color;
  $('gaugePct').textContent = soc == null ? '--' : Math.round(soc) + '%';
  $('gaugePct').style.fill  = color;
}
updateGauge(null);

// ── Charge arrow (inside gauge) ────────────────────────────────────────────

function updateChargeStatus(b) {
  const arrow = $('gaugeArrow');
  const dir   = $('dChargeDir');
  const i = b.current;
  if (i == null || Math.abs(i) <= 0.3) {
    if (arrow) { arrow.textContent = ''; }
    if (dir) dir.textContent = '';
  } else if (i > 0.3) {
    if (arrow) { arrow.textContent = '▲'; arrow.setAttribute('fill', '#22c55e'); }
    if (dir) { dir.textContent = '▲ Lädt'; dir.style.color = 'var(--green)'; }
  } else {
    if (arrow) { arrow.textContent = '▼'; arrow.setAttribute('fill', '#f97316'); }
    if (dir) { dir.textContent = '▼ Entlädt'; dir.style.color = 'var(--orange)'; }
  }
}

// ── Starter battery min/max ────────────────────────────────────────────────

let _starterMin = null, _starterMax = null;
let _serviceMin = null, _serviceMax = null;
let _lastZelldiff = null;
let _lastBattery = null;

// ── Tageswerte: heute entnommen / geladen ──────────────────────────────────
//
// Frueher zaehlte allein der Browser mit: nach jedem Seitenaufruf stand wieder
// "-- Ah" in der Kachel, obwohl der Pi den Tag lueckenlos mitschreibt. Quelle
// ist deshalb /api/daily-stats (daily_stats.py integriert im CAN-Thread, mit
// 5-min-Deckel und Zeitsprung-Erkennung). Zwischen zwei Abfragen zaehlt der
// Browser nur noch die Differenz mit, damit die Kachel live mitlaeuft.
//
// WICHTIG: fehlende Tage liefern null, NICHT 0.0.
//   null  = keine Daten (Pi war aus)  -> Anzeige "--"
//   0.0   = echte Null (nichts entnommen) -> Anzeige "0.0"
// Beides zu vermengen waere auf einem Batteriemonitor gefaehrlich: "nichts
// verbraucht" und "wir wissen es nicht" sind verschiedene Aussagen.

const DAILY_POLL_MS  = 120000;   // Tageswerte alle 2 min frisch holen
const AH_MAX_STEP_S  = 300;      // Integrations-Deckel, wie _MAX_STEP_S in daily_stats.py

let _dailyHeute = null;          // {date, charged_ah, discharged_ah, avg_soc} vom Server
let _dailyStand = 0;             // nowTs() der letzten Serverantwort
// Lokale Zwischensumme seit _dailyStand (Shunt), wird bei jeder Antwort genullt:
let _lokalAhEntnommen = 0, _lokalWhEntnommen = 0, _lokalAhGeladen = 0;
// Rueckfallebene aus dem geladenen Verlauf; null = Verlauf deckt heute nicht ab
let _verlaufAhEntnommen = null, _verlaufWhEntnommen = null;

let _lastShuntTs  = null;
let _battEnergyUnit = 'ah';

/** Lokale Mitternacht als Unix-Sekunden. */
function _mitternachtTs() { return new Date().setHours(0, 0, 0, 0) / 1000; }

/** Heutiges Datum als YYYY-MM-DD (gleiche Schreibweise wie daily_stats.py). */
function _heuteDatum() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Holt die Tageswerte vom Pi. Fehler werden gemeldet, nicht verschluckt. */
function fetchDailyStats() {
  return fetch('/api/daily-stats?days=1', { cache: 'no-store' })
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(rows => {
      const heute = Array.isArray(rows) && rows.length ? rows[rows.length - 1] : null;
      if (!heute || typeof heute !== 'object') return;
      _dailyHeute = heute;
      _dailyStand = nowTs();
      // Serverwert enthaelt den Tag bis eben — lokale Zwischensumme neu beginnen.
      _lokalAhEntnommen = _lokalWhEntnommen = _lokalAhGeladen = 0;
      _renderTodayTile();
    })
    .catch(err => console.warn('Tageswerte konnten nicht geladen werden:', err));
}

// Sichtbarkeitsgesteuert: im Hintergrund fragt niemand den Pi ab, beim
// Zurueckkehren wird sofort einmal aktualisiert (createPoller in core.js).
const _dailyPoller = createPoller(fetchDailyStats, DAILY_POLL_MS);
if (document.readyState === 'loading')
  document.addEventListener('DOMContentLoaded', () => _dailyPoller.start());
else
  _dailyPoller.start();

function toggleBattUnit(e) {
  e?.stopPropagation();
  _battEnergyUnit = _battEnergyUnit === 'ah' ? 'wh' : 'ah';
  _updateBattToggle();
  _renderBattGrid();
}

function _updateBattToggle() {
  $('battToggleAh')?.classList.toggle('btu-active', _battEnergyUnit === 'ah');
  $('battToggleWh')?.classList.toggle('btu-active', _battEnergyUnit === 'wh');
}

function _renderBattGrid() {
  const b   = _lastBattery;
  if (!b) return;
  const ah  = _battEnergyUnit === 'ah';

  // STROM / LEISTUNG
  const mainEl   = $('battIMain'), mainUnit = $('battIMainUnit');
  const subEl    = $('battISub'),  subUnit  = $('battISubUnit');
  const lblEl    = $('battStromLabel');
  const subParent = subEl?.parentElement;
  if (ah) {
    if (mainEl)   { mainEl.textContent = fmt(b.current); mainEl.className = b.current == null ? '' : b.current >= 0 ? 'val-green' : 'val-orange'; }
    if (mainUnit)   mainUnit.textContent = 'A';
    if (subParent)  subParent.style.display = 'none';
    if (lblEl)      lblEl.textContent   = 'Strom';
  } else {
    if (mainEl)   { mainEl.textContent = b.power != null ? fmt(b.power, 0) : '--'; mainEl.className = b.power == null ? '' : b.power >= 0 ? 'val-green' : 'val-orange'; }
    if (mainUnit)   mainUnit.textContent = 'W';
    if (subParent)  subParent.style.display = 'none';
    if (lblEl)      lblEl.textContent   = 'Leistung';
  }

  // RESTKAPAZITÄT
  const remEl   = $('battRemVal'), remUnit = $('battRemUnit');
  const capAh   = batteriesConfig.capacity_ah ?? null;
  if (remEl && capAh != null && b.consumed_ah != null) {
    const remainAh = Math.max(0, capAh + b.consumed_ah);
    if (ah) {
      remEl.textContent  = remainAh.toFixed(1);
      if (remUnit) remUnit.textContent = 'Ah';
    } else {
      const wh = remainAh * (b.voltage ?? 13.0);
      const { val, unit } = _fmtWh(wh);
      remEl.textContent  = val;
      if (remUnit) remUnit.textContent = unit;
    }
  }

  _renderTodayTile();
}

// Speist die kompakten (halbe Höhe) und erweiterten (doppelte Breite) Varianten.
function _renderBattHalfWide(b) {
  const st = (id, v) => { const e = $(id); if (e) e.textContent = v; };
  const soc = b.soc;
  st('battHalfSoc', soc == null ? '--%' : Math.round(soc) + '%');
  const hs = $('battHalfSoc');
  if (hs) hs.style.color = soc == null ? 'var(--text)' : soc >= 50 ? 'var(--green)' : soc >= 20 ? 'var(--yellow)' : 'var(--red)';
  st('battHalfV', fmtV(b.voltage));
  st('battHalfA', b.current == null ? '--' : fmt(b.current));
  const capAh = batteriesConfig.capacity_ah ?? null;
  const rem = (capAh != null && b.consumed_ah != null) ? Math.max(0, capAh + b.consumed_ah).toFixed(0) : '--';
  st('battHalfRem', rem);
  // Wide-Variante: SOC-Verlauf-Graph (Min/Max werden dort gesetzt)
  _renderBattWideChart();
}

// SOC-Verlauf der letzten 6 h für die Wide-Kachel (nur sichtbar im wide-Modus).
//
// ENTSCHEIDUNG: die Zeitachse ist FEST auf 6 h (jetzt-6h … jetzt); vorhandene
// Daten fuellen nur den Teil der Breite, der ihnen zusteht. Vorher wurde die
// Achse auf den Datenbereich gedehnt: lagen zehn Minuten Verlauf vor, fuellten
// die zehn Minuten die ganze Kachel und die Ueberschrift behauptete trotzdem
// "6 h" — ein zehnminuetiger Knick sah aus wie ein Halbtagestrend. Gedehnt
// waere die Kurve zwar huebscher, aber sie luegt ueber die Zeitbasis; auf einem
// Batteriemonitor wiegt Ehrlichkeit schwerer. Deckt der Verlauf das Fenster
// nicht ab, sagt die Ueberschrift zusaetzlich, wie viel wirklich drinsteckt.
const WIDE_FENSTER_S = 6 * 3600;

/** Schreibt die Ueberschrift der Wide-Kachel (ehrlich ueber die Datenlage). */
function _setWideHeader(abgedecktS) {
  const hd = document.querySelector('#battWideSrc .batt-wide-hd');
  if (!hd) return;
  hd.textContent = abgedecktS == null
    ? 'SOC-Verlauf · 6 h (keine Daten)'
    : abgedecktS >= WIDE_FENSTER_S * 0.9
      ? 'SOC-Verlauf · 6 h'
      : `SOC-Verlauf · 6 h (Daten: ${timeSince(abgedecktS)})`;
}

function _renderBattWideChart() {
  const canvas = $('battWideChart');
  if (!canvas) return;
  const W = canvas.offsetWidth, H = canvas.offsetHeight;
  if (!W || !H) return;                         // nicht sichtbar -> überspringen
  const src = (typeof histData !== 'undefined') ? histData : [];
  const t1  = nowTs();
  const t0  = t1 - WIDE_FENSTER_S;
  // Nur echte Zahlen: ein NaN-SOC wuerde als Luecke durch die Linie laufen und
  // Min/Max unbrauchbar machen — `!= null` allein faengt NaN nicht ab.
  const pts = src.filter(d => d.ts >= t0 && d.ts <= t1 &&
                              typeof d.soc === 'number' && isFinite(d.soc));
  const dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr); ctx.clearRect(0, 0, W, H);
  const setT = (id, v) => { const e = $(id); if (e) e.textContent = v; };
  if (pts.length < 2) {
    setT('battWideMin', '--'); setT('battWideMax', '--');
    _setWideHeader(null);
    return;
  }
  _setWideHeader(pts[pts.length - 1].ts - pts[0].ts);

  // Min/Max als Schleife statt Math.min(...socs): der Spread schiebt jeden Punkt
  // als eigenes Argument auf den Stack und legt ihn bei langen Verlaeufen um
  // (RangeError, die Kachel bliebe leer) — sechs Stunden sind mehrere tausend
  // Punkte.
  let lo = pts[0].soc, hi = pts[0].soc;
  for (const p of pts) {
    if (p.soc < lo) lo = p.soc;
    if (p.soc > hi) hi = p.soc;
  }
  setT('battWideMin', Math.round(lo));
  setT('battWideMax', Math.round(hi));
  if (hi - lo < 5) { const m = (hi + lo) / 2; lo = m - 2.5; hi = m + 2.5; }
  lo = Math.max(0, lo - 2); hi = Math.min(100, hi + 2);
  const pad = 6;
  // Feste Achse: t0 links, t1 (=jetzt) rechts — unabhaengig davon, wie weit
  // die vorhandenen Punkte reichen.
  const xOf = t => pad + (t - t0) / WIDE_FENSTER_S * (W - 2 * pad);
  const yOf = v => pad + (1 - (v - lo) / ((hi - lo) || 1)) * (H - 2 * pad);

  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, 'rgba(34,197,94,0.30)');
  grad.addColorStop(1, 'rgba(34,197,94,0.00)');
  ctx.beginPath();
  pts.forEach((p, i) => i === 0 ? ctx.moveTo(xOf(p.ts), yOf(p.soc)) : ctx.lineTo(xOf(p.ts), yOf(p.soc)));
  ctx.lineTo(xOf(pts[pts.length - 1].ts), H); ctx.lineTo(xOf(pts[0].ts), H); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();

  ctx.beginPath();
  ctx.strokeStyle = '#22c55e'; ctx.lineWidth = 2; ctx.lineJoin = 'round';
  pts.forEach((p, i) => i === 0 ? ctx.moveTo(xOf(p.ts), yOf(p.soc)) : ctx.lineTo(xOf(p.ts), yOf(p.soc)));
  ctx.stroke();
}

/**
 * Heute entnommene Energie.
 *
 * Rueckgabe {ah, wh} oder null, wenn es fuer heute KEINE Daten gibt. Null ist
 * ausdruecklich etwas anderes als der Wert 0 — siehe Kommentar oben.
 */
function _heuteEntnommen() {
  // Zwischensumme aus einem abgelaufenen Tag darf nicht in den neuen wandern.
  if (_dailyStand && _dailyStand < _mitternachtTs())
    _lokalAhEntnommen = _lokalWhEntnommen = _lokalAhGeladen = 0;

  if (_dailyHeute && _dailyHeute.date === _heuteDatum() && _dailyHeute.discharged_ah != null) {
    const ah = _dailyHeute.discharged_ah + _lokalAhEntnommen;
    // Der Pi fuehrt pro Tag nur Ah. Wh wird daraus mit der Packspannung
    // hochgerechnet (Anzeigewert, keine gemessene Energie) — die lokale
    // Zwischensumme ist dagegen echt integriert.
    const v = _lastBattery?.voltage ?? 13.0;
    return { ah, wh: _dailyHeute.discharged_ah * v + _lokalWhEntnommen };
  }
  // Rueckfallebene: aus dem geladenen Verlauf gerechnet (z. B. wenn
  // /api/daily-stats nicht antwortet). Deckt der Verlauf heute nicht ab,
  // bleibt es ehrlich bei "keine Daten".
  if (_verlaufAhEntnommen != null)
    return { ah: _verlaufAhEntnommen + _lokalAhEntnommen,
             wh: (_verlaufWhEntnommen ?? 0) + _lokalWhEntnommen };
  return null;
}

function _renderTodayTile() {
  const el   = $('battTodayWh');
  const unit = $('battTodayUnit');
  if (!el) return;
  const heute = _heuteEntnommen();
  if (_battEnergyUnit === 'ah') {
    // null -> "--" (keine Daten), 0 -> "0.0" (echte Null)
    el.textContent = heute ? heute.ah.toFixed(1) : '--';
    if (unit) unit.textContent = 'Ah';
  } else {
    const { val, unit: u } = _fmtWh(heute ? heute.wh : null);
    el.textContent = val;
    if (unit) unit.textContent = u;
  }
}

/**
 * Zaehlt die Zwischensumme seit der letzten Serverantwort mit.
 *
 * dt-Deckel: zwischen zwei WebSocket-Nachrichten liegen nach Handy-Standby,
 * gesperrtem Bildschirm oder Funkloch Minuten bis Stunden. Ohne Obergrenze
 * wurde diese ganze Pause mit dem GERADE anliegenden Strom multipliziert — der
 * Zaehler sprang um Dutzende Ah. Groessere Schritte werden deshalb verworfen;
 * die Luecke holt der naechste /api/daily-stats-Abruf zurueck, denn der Pi hat
 * waehrenddessen durchgezaehlt (gleicher Deckel dort: _MAX_STEP_S).
 */
function _accumTodayWh(power, current) {
  const now = nowTs();
  const vor = _lastShuntTs;
  _lastShuntTs = now;
  if (vor === null) return;
  const dt = now - vor;
  if (!(dt > 0) || dt > AH_MAX_STEP_S) return;   // Ruecklauf und Luecken verwerfen
  if (vor < _mitternachtTs()) return;            // Schritt ueber Mitternacht: Servertag zaehlt
  const dtH = dt / 3600;
  if (current != null) {
    if (current < 0) _lokalAhEntnommen += Math.abs(current) * dtH;
    else             _lokalAhGeladen   += current * dtH;
  }
  if (power != null && power < 0) _lokalWhEntnommen += Math.abs(power) * dtH;
}

function _fmtWh(wh) {
  if (wh == null) return { val: '--', unit: 'Wh' };
  if (wh >= 1000) return { val: (wh / 1000).toFixed(2), unit: 'kWh' };
  return { val: Math.round(wh).toString(), unit: 'Wh' };
}

// ── Dual-source tile update ────────────────────────────────────────────────

function updateDualTiles() {
  const b   = _lastBattery;
  const bms = _lastBms;
  if (!b) return;
  const primary  = batteriesConfig.primary_source ?? 'shunt';
  const secLabel = primary === 'shunt' ? 'BMS' : 'Shunt';

  const socP = primary === 'shunt' ? b.soc          : bms?.soc;
  const socS = primary === 'shunt' ? bms?.soc        : b.soc;
  const vP   = primary === 'shunt' ? b.voltage       : bms?.voltage;
  const vS   = primary === 'shunt' ? bms?.voltage    : b.voltage;
  const iP   = primary === 'shunt' ? b.current       : bms?.current_total;
  const iS   = primary === 'shunt' ? bms?.current_total : b.current;

  updateGauge(socP);
  updateTopbarBatt(socP);

  const dSocEl = $('dSoc');
  if (dSocEl) dSocEl.textContent = socP != null ? Math.round(socP) : '--';
  const _pct = socP != null ? Math.max(0, Math.min(100, socP)) : 0;
  const socBar = $('dSocBar');
  if (socBar) {
    socBar.style.width = _pct + '%';
    socBar.style.background = _pct >= 50 ? 'var(--green)' : _pct >= 20 ? 'var(--yellow)' : 'var(--red)';
  }
  const socSec = $('dSocSec');
  if (socSec) {
    if (socS != null) { socSec.textContent = `${secLabel}: ${Math.round(socS)} %`; socSec.style.display = ''; }
    else socSec.style.display = 'none';
  }

  const dVEl = $('dV');
  if (dVEl) dVEl.textContent = fmtV(vP);
  const vSec = $('dVSec');
  if (vSec) {
    if (vS != null) { vSec.textContent = `${secLabel}: ${fmtV(vS)} V`; vSec.style.display = ''; }
    else vSec.style.display = 'none';
  }

  const dIEl = $('dI');
  if (dIEl) dIEl.textContent = fmt(iP);
  const iSec = $('dISec');
  if (iSec) {
    if (iS != null) { iSec.textContent = `${secLabel}: ${fmt(iS)} A`; iSec.style.display = ''; }
    else iSec.style.display = 'none';
  }
}

// ── Daily Ah accumulation ──────────────────────────────────────────────────

let _todayChargeAh = 0, _todayDischargeAh = 0;
let _lastBmsAhTs = null;

// BMS-Lade-/Entladestrom mitzaehlen. Gleicher dt-Deckel wie bei _accumTodayWh:
// eine Standby-Pause darf nicht als Stunden bei aktuellem Strom verbucht werden.
// (Diese beiden Werte kommen vom BMS und werden vom Pi nicht pro Tag gefuehrt —
// sie werden beim Verlaufsabruf aus der History nachgerechnet.)
function accumBmsAh(chargeA, dischargeA) {
  const now = nowTs();
  const vor = _lastBmsAhTs;
  _lastBmsAhTs = now;
  if (vor === null) return;
  if (vor < _mitternachtTs()) { _todayChargeAh = 0; _todayDischargeAh = 0; return; }
  const dt = now - vor;
  if (!(dt > 0) || dt > AH_MAX_STEP_S) return;
  const dtH = dt / 3600;
  _todayChargeAh    += (chargeA    ?? 0) * dtH;
  _todayDischargeAh += (dischargeA ?? 0) * dtH;
}

// Rechnet die Tageswerte aus dem geladenen Verlauf nach. Fuer die BMS-Stroeme
// ist das die einzige Quelle; fuer den Shunt nur die Rueckfallebene, falls
// /api/daily-stats nicht antwortet (dort steht der lueckenlose Tageswert).
function recomputeDailyAhFromHist() {
  const midnightTs = _mitternachtTs();
  // Gleicher Deckel wie im Live-Pfad: eine Verlaufsluecke (Pi war aus, Funk weg)
  // darf nicht mit dem letzten Strom aufgefuellt werden.
  const schritt = (a, b) => {
    const dt = b.ts - a.ts;
    return (dt > 0 && dt <= AH_MAX_STEP_S) ? dt / 3600 : 0;
  };

  // BMS-Ah (current_charge/current_discharge)
  _todayChargeAh = 0; _todayDischargeAh = 0;
  const todayBms = histData.filter(e => e.ts >= midnightTs && (e.current_charge != null || e.current_discharge != null));
  for (let i = 1; i < todayBms.length; i++) {
    const dtH = schritt(todayBms[i-1], todayBms[i]);
    _todayChargeAh    += (todayBms[i-1].current_charge    ?? 0) * dtH;
    _todayDischargeAh += (todayBms[i-1].current_discharge ?? 0) * dtH;
  }

  // Shunt-Ah/Wh aus history.current + history.voltage
  const todayShunt = histData.filter(e => e.ts >= midnightTs && e.current != null);
  if (todayShunt.length >= 2) {
    let ah = 0, wh = 0;
    for (let i = 1; i < todayShunt.length; i++) {
      const dtH = schritt(todayShunt[i-1], todayShunt[i]);
      const cur = todayShunt[i-1].current;
      if (cur < 0) {
        ah += Math.abs(cur) * dtH;
        wh += Math.abs(cur) * (todayShunt[i-1].voltage ?? 13.0) * dtH;
      }
    }
    _verlaufAhEntnommen = ah;
    _verlaufWhEntnommen = wh;
  } else {
    // Verlauf deckt heute nicht ab -> keine Aussage (nicht "0")
    _verlaufAhEntnommen = null;
    _verlaufWhEntnommen = null;
  }

  // _lastShuntTs auf jetzt setzen: der naechste Live-Schritt soll die Zeit seit
  // JETZT integrieren, nicht die Luecke bis zum letzten Verlaufspunkt.
  _lastShuntTs = nowTs();

  // Sofort alle Batterie-Kacheln rendern, nicht auf nächste WS-Nachricht warten.
  _renderTodayTile();
  if (_lastBattery) _renderBattGrid();
}

// ── Battery update ─────────────────────────────────────────────────────────

function updateBattery(b) {
  const hasData = b.voltage != null || b.soc != null;
  $('battCard').style.display = hasData ? '' : 'none';
  if (!hasData) return;
  _lastBattery = b;
  updateChargeStatus(b);
  const vEl = $('battV'); vEl.textContent = fmtV(b.voltage); vEl.className = colorClass(b.voltage, 12.6, 12.0);
  $('battStarter').textContent = fmtV(b.starter_voltage);
  $('battCycles').textContent  = b.cycles ?? '--';
  $('battFull').textContent    = timeSince(b.time_since_full);

  _accumTodayWh(b.power, b.current);
  _renderBattGrid();
  _renderBattHalfWide(b);

  // Detail-Overlay-Felder (non-dual)
  $('dP').textContent   = fmt(b.power, 0);
  $('dAh').textContent  = fmt(b.consumed_ah);
  $('dStarter').textContent = fmtV(b.starter_voltage);
  // Starter Min/Max: bevorzugt Shunt-Werte (H15/H16), Fallback JS-Session
  if (b.starter_min_voltage != null) _starterMin = b.starter_min_voltage;
  else if (b.starter_voltage != null && (_starterMin === null || b.starter_voltage < _starterMin)) _starterMin = b.starter_voltage;
  if (b.starter_max_voltage != null) _starterMax = b.starter_max_voltage;
  else if (b.starter_voltage != null && (_starterMax === null || b.starter_voltage > _starterMax)) _starterMax = b.starter_voltage;
  $('dStarterMin').textContent = fmtV(_starterMin);
  $('dStarterMax').textContent = fmtV(_starterMax);
  $('dCycles').textContent  = b.cycles   ?? '--';
  // min/max: prefer shunt PGN-130900 values; fall back to JS-tracked session extremes
  if (b.voltage != null) {
    if (_serviceMin === null || b.voltage < _serviceMin) _serviceMin = b.voltage;
    if (_serviceMax === null || b.voltage > _serviceMax) _serviceMax = b.voltage;
  }
  $('dMinV').textContent = fmtV(b.min_voltage ?? _serviceMin);
  $('dMaxV').textContent = fmtV(b.max_voltage ?? _serviceMax);
  $('dFull').textContent = timeSince(b.time_since_full);

  updateDualTiles();
  recordHistory(b);
  if (!$('battOverlay').classList.contains('hidden')) renderCharts();
}

function updateTopbarBatt(soc) {
  if (typeof _kioskUpdateBatt === 'function') _kioskUpdateBatt(soc);  // Kiosk Slide-down
  const fill = $('topbarBattFill');
  const pct  = $('topbarBattPct');
  const wrap = $('topbarBatt');
  if (!fill) return;
  if (soc == null) {
    fill.setAttribute('width', '0');
    if (pct)  pct.textContent = '--%';
    if (wrap) wrap.style.color = 'var(--text3)';
    return;
  }
  const v = Math.max(0, Math.min(100, soc));
  fill.setAttribute('width', String((v / 100 * 16).toFixed(1)));
  const color = v >= 50 ? 'var(--green)' : v >= 20 ? 'var(--yellow)' : 'var(--red)';
  if (wrap) wrap.style.color = color;
  if (pct)  pct.textContent = Math.round(v) + '%';
}

/** Kennzahl der Servicebatterie-Karte. */
function _sbStat(label, wert, einheit, farbe) {
  const leer = '<span style="color:var(--text3)">--</span>';
  return `<div class="st"><span class="st-l">${label}</span>
    <span class="st-v"${farbe ? ` style="color:${farbe}"` : ''}>${
      wert == null ? leer : wert + (einheit ? `<small>${einheit}</small>` : '')
    }</span></div>`;
}

// ── Batterie-Detailseite ──────────────────────────────────────────────────
// Die Servicebatterie ist EINE Karte. Vorher war die Bank auf zwei Kacheln
// zerlegt — Shunt-Werte in der einen, BMS-Werte in der anderen — und die Zellen
// standen neben der Starterbatterie. Shunt und BMS messen aber DIESELBE
// Batterie; die Starterbatterie hat damit nichts zu tun. Deshalb hier alles
// zusammen, Starter separat.

/** Quellen, die ueberhaupt einen Wert melden. */
function _baQuellen(data) {
  return [
    ['Landstrom', data.charger?.power,    'var(--accent)'],
    ['Solar',     data.solar?.power,      'var(--yellow)'],
    ['DC-DC',     data.orion?.power,      '#a78bfa'],
    // Lichtmaschine: Vorbereitung, erscheint sobald das Geraet Daten liefert.
    ['Lichtmaschine', data.alternator?.power, '#38bdf8'],
  ].filter(q => q[1] != null).map(q => ({ name: q[0], w: Math.max(0, q[1]), farbe: q[2] }));
}

// _W ist in flow.js belegt — alle JS-Dateien landen in EINEM Bundle-Scope.
const _baW = v => v == null ? '--'
  : Math.abs(v) >= 1000 ? (v / 1000).toFixed(2) + ' kW' : Math.round(v) + ' W';

/** Zeitspanne menschenlesbar. */
function _baDauer(h) {
  if (h == null || !isFinite(h) || h <= 0) return null;
  // Unter zehn Tagen mit einer Nachkommastelle. Mit ganzen Tagen sprang die
  // Ueberschrift am Geraet zwischen "6 Tage" und "7 Tage", sobald der
  // Verbrauch um zwei Watt schwankte — die Rundungsgrenze lag mitten im
  // normalen Rauschen.
  if (h >= 240) return Math.round(h / 24) + ' Tage';
  if (h >= 48)  return (h / 24).toFixed(1).replace('.', ',') + ' Tage';
  if (h >= 1)   return Math.floor(h) + ' h ' + Math.round((h % 1) * 60) + ' min';
  return Math.round(h * 60) + ' min';
}

/**
 * Geglaetteter Verbrauch fuer die Reichweiten-Schaetzung.
 *
 * Der Momentanwert schwankt um einige Watt (Kuehlschrank, Lader-Regelung).
 * Die Ueberschrift wird bei jedem WebSocket-Frame neu gerechnet und zappelte
 * deshalb sichtbar. Median statt Mittelwert: ein einzelner Ausreisser — etwa
 * der Anlaufstrom einer Pumpe — soll die Schaetzung nicht mitreissen.
 *
 * Die Reihe haengt an der Zeit, nicht an der Anzahl Frames: bei rund 2,4
 * Frames je Sekunde deckt sie etwa 25 Sekunden ab.
 */
const _BA_VERBRAUCH_FENSTER_S = 25;
let _baVerbrauchReihe = [];

function _baVerbrauchGeglaettet(w) {
  if (w == null || !isFinite(w)) return null;
  const jetzt = nowTs();
  _baVerbrauchReihe.push({ ts: jetzt, w });
  const grenze = jetzt - _BA_VERBRAUCH_FENSTER_S;
  // Nach einem Sprung der Serverzeit (der Pi hat keine Echtzeituhr) koennen
  // alte Stempel in der Zukunft liegen — dann lieber neu anfangen.
  if (_baVerbrauchReihe.some(e => e.ts > jetzt + 5)) _baVerbrauchReihe = [{ ts: jetzt, w }];
  else _baVerbrauchReihe = _baVerbrauchReihe.filter(e => e.ts >= grenze);
  const werte = _baVerbrauchReihe.map(e => e.w).sort((a, b) => a - b);
  const m = werte.length >> 1;
  return werte.length % 2 ? werte[m] : (werte[m - 1] + werte[m]) / 2;
}

/** SOC als Ring — Prozent ist radial richtig aufgehoben. */
function _baRing(soc, farbe) {
  const r = 62, C = 2 * Math.PI * r;
  return `<svg class="ba-ring" viewBox="0 0 160 160" role="img"
      aria-label="Ladezustand ${soc != null ? soc : 'unbekannt'} Prozent">
    <circle cx="80" cy="80" r="${r}" fill="none" stroke="var(--surface2)" stroke-width="13"/>
    ${soc != null ? `<circle cx="80" cy="80" r="${r}" fill="none" stroke="${farbe}"
      stroke-width="13" stroke-linecap="round"
      stroke-dasharray="${(C * soc / 100).toFixed(1)} ${C.toFixed(1)}"
      transform="rotate(-90 80 80)" style="transition:stroke-dasharray .5s ease"/>` : ''}
    <text x="80" y="76" text-anchor="middle" class="ba-ring-num"
      fill="${farbe}">${soc != null ? soc : '--'}</text>
    <text x="80" y="96" text-anchor="middle" class="ba-ring-lbl">% Ladung</text>
  </svg>`;
}

/**
 * Karte 1 — die Antwort.
 *
 * Die Seite beantwortete bisher nirgends die Frage, mit der man sie oeffnet:
 * reicht der Strom? Der Shunt liefert kein ttg (live immer null), aus
 * Restenergie und Verbrauch laesst sich die Autonomie aber rechnen.
 *
 * Gerechnet wird bewusst gegen den VERBRAUCH, nicht gegen den Nettofluss: am
 * Steg ist netto nahe null und ergaebe absurde Zahlen ("reicht 91 Tage").
 * Die nuetzliche Frage ist "wie lange komme ich hin, wenn der Landstrom
 * wegfaellt".
 */
function _baAntwort(data) {
  const b = data.battery ?? {}, bms = data.bms ?? {};
  const soc = b.soc != null ? Math.round(b.soc) : null;
  const farbe = soc == null ? 'var(--text3)'
    : soc >= 50 ? 'var(--green)' : soc >= 20 ? 'var(--yellow)' : 'var(--red)';
  // Verfuegbare Energie — bewusst aus der KONFIGURIERTEN Bankkapazitaet.
  //
  // Es gibt zwei Quellen, und sie widersprechen sich um fast eine
  // Groessenordnung. Live gemessen: Einstellungen sagen 100 Ah Bank
  // (= rund 1,3 kWh bei 13,3 V), das BMS meldet remaining_kwh = 10,878.
  // Zusaetzlich meldet dasselbe BMS capacity_ah = 11,5 — und 10,878 / 11,5
  // ergibt exakt den SOC von 95 %, die beiden BMS-Felder stehen also in
  // derselben Einheit, welche auch immer das ist. PROTOCOL.md:238 sagt Ah,
  // die Zahlen verhalten sich wie kWh; belegen laesst sich das nur am
  // BMS-Display selbst.
  //
  // Auf dieser Karte steht die Frage "wie lange reicht der Strom" — die darf
  // nicht auf einem Feld stehen, dessen Einheit ungeklaert ist. Deshalb:
  // Hauptquelle ist dieselbe Rechnung, aus der auch die Batterie-Kachel ihre
  // Restkapazitaet zieht (konfigurierte Kapazitaet + consumed_ah vom Shunt).
  // Das BMS dient als Gegenprobe; weichen beide stark ab, sagen wir das,
  // statt eine der beiden Zahlen stillschweigend zu bevorzugen.
  const capAh    = batteriesConfig.capacity_ah ?? null;
  // consumed_ah ist beim Victron-Shunt NEGATIV (entnommene Ah). Nach oben auf
  // die Bankkapazitaet klemmen: ein Shunt mit falschem Vorzeichen oder ein
  // nicht zurueckgesetzter Zaehler ergaebe sonst "Verfuegbar 2.369 Wh von
  // 1.286 Wh" — mehr verfuegbar als vorhanden.
  const restAh = (capAh != null && b.consumed_ah != null)
    ? Math.min(capAh, Math.max(0, capAh + b.consumed_ah)) : null;
  const restWhSh = (restAh != null && b.voltage != null)
    ? Math.round(restAh * b.voltage) : null;
  const restWhBms = bms.remaining_kwh != null ? Math.round(bms.remaining_kwh * 1000) : null;
  const restWh    = restWhSh ?? restWhBms;
  // Faktor 2 als Schwelle: Messrauschen und ein nicht ganz frisch kalibrierter
  // Shunt erklaeren Abweichungen im Zehnerprozentbereich, keinen Faktor 2.
  const streit = (restWhSh != null && restWhBms != null && restWhSh > 0 && restWhBms > 0)
    && (restWhBms / restWhSh > 2 || restWhSh / restWhBms > 2);

  const q = _baQuellen(data);
  const rein = q.reduce((sum, x) => sum + x.w, 0);
  const p = b.power;
  const verbrauch = p != null ? Math.max(0, rein - p) : null;
  // Fuer die Reichweite den geglaetteten Wert, fuer die Anzeige darunter den
  // aktuellen — "reicht X" soll ruhig stehen, "aktuell Y W" darf zappeln.
  const verbrauchGl = _baVerbrauchGeglaettet(verbrauch);
  const amNetz = q.some(x => x.name !== 'Solar' && x.w > 5);

  let kopf = 'Ladezustand', unter = '';
  if (verbrauch != null && verbrauch > 2 && restWh != null) {
    kopf = (amNetz ? 'ohne Landstrom ' : '') + 'reicht '
         + (_baDauer(restWh / (verbrauchGl || verbrauch)) || '--');
    unter = `bei aktuell ${_baW(verbrauch)} Verbrauch`
      + (p > 1 ? ` · lädt mit ${_baW(p)}` : p < -1 ? ` · entnimmt ${_baW(Math.abs(p))}` : '');
  } else if (p != null && p > 1 && restWh != null && soc) {
    const gesamt = restWh / (soc / 100);
    kopf = soc >= 99 ? 'voll geladen' : 'voll in ' + (_baDauer((gesamt - restWh) / p) || '--');
    unter = `lädt mit ${_baW(p)}`;
  } else {
    unter = 'kein nennenswerter Verbrauch';
  }

  // Energie durchgaengig in Wh.
  //
  // Vorher standen "Verfuegbar 10.878 Wh" und "Verbraucht -0,1 Ah" nebeneinander
  // — dieselbe Groesse in zwei Einheiten in einer Zeile. Die Gesamtkapazitaet
  // kommt aus den BMS-eigenen Zahlen (Restenergie / SOC), NICHT aus dem
  // Shunt-SOC: remaining_kwh ist ein BMS-Wert, und die beiden SOC-Angaben
  // weichen voneinander ab (live: Shunt 100 %, BMS 95 %). Unter 20 % SOC wird
  // die Division zu grob — der SOC kommt als ganze Prozentzahl, bei 10 % sind
  // das schon +-5 % Fehler auf das Ergebnis. Dann nur den Restwert zeigen.
  // Gesamtkapazitaet: bei der Shunt-Rechnung steht sie direkt in den
  // Einstellungen, beim BMS-Rueckfall wird sie aus Restenergie und SOC
  // hochgerechnet. Unter 20 % SOC wird diese Division zu grob — der SOC kommt
  // als ganze Prozentzahl, bei 10 % sind das schon +-5 % Fehler.
  const bmsSoc = bms.soc;
  const gesamtWh = restWhSh != null
    ? (b.voltage != null ? Math.round(capAh * b.voltage) : null)
    : (restWh != null && bmsSoc != null && bmsSoc >= 20
        ? Math.round(restWh / (bmsSoc / 100)) : null);
  // Ab einer Kilowattstunde in kWh: "11.969 Wh von 11.970 Wh" waren zwei
  // praktisch gleiche Zahlen mit vorgetaeuschter Wattstunden-Genauigkeit.
  // BEIDE Zahlen des Satzes teilen sich die Einheit — "784 Wh von 1,3 kWh"
  // waere derselbe Einheitenmix, den diese Karte gerade losgeworden ist.
  const einheitAbKwh = Math.max(restWh ?? 0, gesamtWh ?? 0) >= 1000;
  const wh = n => einheitAbKwh
    ? (n / 1000).toFixed(n / 1000 < 10 ? 2 : 1).replace('.', ',') + ' kWh'
    : Math.round(n) + ' Wh';

  // Zelldiff. steht ab jetzt als "Spreizung" in der Zellen-Karte direkt
  // darunter — hier waere es dieselbe Zahl ein zweites Mal.
  const kette = [
    ['Spannung',  b.voltage != null ? b.voltage.toFixed(2) + ' V' : null],
    ['Strom',     b.current != null ? (b.current > 0 ? '+' : '') + b.current.toFixed(1) + ' A' : null],
    ['Zyklen',    b.cycles],
    ['Temperatur', bms.lowest_temp != null && bms.highest_temp != null
      ? `${bms.lowest_temp.toFixed(0)}–${bms.highest_temp.toFixed(0)} °C` : null],
    ['Voll vor',  b.time_since_full != null ? timeSince(b.time_since_full) : null],
  ].filter(([, v]) => v != null)
   .map(([n, v]) => `<span>${n} <b>${v}</b></span>`).join('');

  return `<div class="sb-card">
    <div class="ba-antwort">
      ${_baRing(soc, farbe)}
      <div>
        <div class="ba-kopf">${kopf}<small>${unter}</small></div>
        <div class="ba-kette">
          ${restWh != null ? `<span>Verfügbar <b>${wh(restWh)}</b>${
            (gesamtWh != null && restWh / gesamtWh < 0.99)
              ? ` von ${wh(gesamtWh)}` : ''}</span>` : ''}
          ${kette}
        </div>
        ${streit ? `<div class="ba-streit">${icon('warning', {size: 13})}
          <span>Bank laut Einstellungen <b>${capAh} Ah</b> (${wh(restWhSh)} verfügbar),
          BMS meldet <b>${wh(restWhBms)}</b> — Faktor
          ${(Math.max(restWhBms, restWhSh) / Math.min(restWhBms, restWhSh)).toFixed(1)}.
          Gerechnet wird mit dem eingestellten Wert. Bitte Bankkapazität in den
          Einstellungen prüfen.</span></div>` : ''}
      </div>
    </div>
  </div>`;
}

/**
 * Karte 2 — gerichtete Bilanz.
 * Quellen links, Batterie mit Vorzeichen in der Mitte, Verbrauch rechts.
 * Ringe zeigten nur Betraege; eine Energiebilanz ist aber gerichtet.
 */
function _baBilanz(data) {
  const q = _baQuellen(data);
  if (!q.length) return '';
  const rein = q.reduce((sum, x) => sum + x.w, 0);
  const p = data.battery?.power;
  const verbrauch = p != null ? Math.max(0, rein - p) : null;
  const max = Math.max(rein, verbrauch ?? 0, 1);

  const zeile = (name, w, farbe) => `<div class="ba-zeile">
      <span class="ba-punkt" style="background:${farbe}"></span>
      <span class="ba-name">${name}</span>
      <span class="ba-watt">${_baW(w)}</span>
      <div class="ba-bahn"><div style="width:${(w / max * 100).toFixed(1)}%;background:${farbe}"></div></div>
    </div>`;

  return `<div class="sb-card">
    <div class="sb-hd">${icon('bolt', {size: 14})} Energiebilanz jetzt</div>
    <div class="ba-fluss">
      <div class="ba-seite">${q.map(x => zeile(x.name, x.w, x.farbe)).join('')}</div>
      <div class="ba-mitte">
        <span class="ba-pfeil">&#8594;</span>
        <b style="color:${p == null ? 'var(--text3)' : p > 0 ? 'var(--green)' : 'var(--orange)'}">${
          p == null ? '--' : (p > 0 ? '+' : '') + Math.round(p) + ' W'}</b>
        <span>Batterie</span>
        <span class="ba-pfeil">&#8594;</span>
      </div>
      <div class="ba-seite">${verbrauch != null
        ? zeile('Verbrauch', verbrauch, 'var(--orange)')
        : '<span class="ba-name">Verbrauch unbekannt</span>'}</div>
    </div>
  </div>`;
}

/**
 * Karte 3 — Zellen als Abweichung vom Mittel.
 * Absolute 3.335 gegen 3.340 sagt beim Draufschauen nichts; die Abweichung
 * zeigt sofort, welche Zelle ausreisst.
 */
// Zellabweichung: feste Skala und feste Schwellen.
//
// Vorher skalierte die Karte sich selbst (max = Math.max(8, ...groesste
// Abweichung)) und faerbte ab 15 mV rot. Damit sah sie IMMER maximal dramatisch
// aus: die groesste Abweichung fuellte per Definition den Balken, egal ob sie
// 4 mV oder 400 mV betrug, und eine fuer LiFePO4 im Ruhezustand voellig normale
// Spreizung von 25 mV erzeugte einen roten Vollbalken.
//
// Jetzt: feste Skala, damit Balkenlaengen ueber die Zeit UND zwischen den
// Zellen vergleichbar sind. Die Skala endet genau dort, wo die Farbe auf Rot
// springt — ein voller Balken heisst also "rot", nichts anderes.
// Schwellen auf die Abweichung vom Mittel, nicht auf die Spreizung: bei einer
// 4-Zellen-Bank mit einem Ausreisser ist die max. Abweichung 3/4 der Spreizung.
// 40 mV Abweichung entspricht damit rund 53 mV Spreizung (fuer LiFePO4 im
// Auge behalten), 75 mV Abweichung rund 100 mV Spreizung (handlungsbeduerftig).
const _ZELL_SKALA_MV = 75;   // Balkenende = Rot-Schwelle
const _ZELL_GELB_MV  = 40;
const _ZELL_ROT_MV   = 75;

/**
 * Farbe der Zelltemperatur.
 *
 * ABSOLUT, nicht als Abweichung: Temperatur ist eine Sicherheitsgroesse —
 * Laden bei -2 °C schaedigt die Zelle dauerhaft (Lithium-Plating), egal was
 * die Nachbarzellen tun. Eine Abweichungsdarstellung wuerde bei den real
 * gemessenen 19-22 °C denselben Fehler wiederholen, den die Spannungsskala
 * losgeworden ist: aus 1,5 K Streuung wuerde ein Vollausschlag.
 *
 * Die Schwellen sind NICHT neu erfunden, sondern die im Projekt bereits
 * verwendeten: charts.js faerbt den Zellgesundheits-Punkt ab >40 °C warm und
 * unter 5 °C kalt, alarms.json warnt ab 45 °C. Fuer LiFePO4 gilt fachlich:
 * Laden nur zwischen 0 und 45 °C, darunter Plating, darueber beschleunigte
 * Alterung.
 */
const _ZELL_T_KALT = 5, _ZELL_T_FROST = 0, _ZELL_T_WARM = 40, _ZELL_T_HEISS = 45;

function _zellTempFarbe(t) {
  if (t == null) return 'var(--text3)';
  if (t < _ZELL_T_FROST || t > _ZELL_T_HEISS) return 'var(--red)';
  if (t < _ZELL_T_KALT  || t > _ZELL_T_WARM)  return 'var(--yellow)';
  return 'var(--accent)';
}

function _zellFarbe(mv) {
  const a = Math.abs(mv);
  return a > _ZELL_ROT_MV ? 'var(--red)' : a > _ZELL_GELB_MV ? 'var(--yellow)' : 'var(--green)';
}

function _baZellen(data) {
  const bms = data.bms ?? {};
  const c = Array.isArray(bms.cells) ? bms.cells : [];
  if (!c.length) return '';
  const mv = c.map(z => (z && z.voltage != null) ? z.voltage * 1000 : null);
  const gueltig = mv.filter(v => v != null);
  if (!gueltig.length) return '';
  const mittel = gueltig.reduce((a, x) => a + x, 0) / gueltig.length;
  const spreizung = Math.max(...gueltig) - Math.min(...gueltig);

  // Meldet ueberhaupt eine Zelle eine Temperatur? Wenn nein, faellt die Spalte
  // restlos weg statt vier Striche zu zeigen.
  const hatTemps = c.some(z => z && z.temp != null);

  // Zellen OHNE Wert werden als Zeile mit "--" ausgegeben statt stillschweigend
  // uebersprungen: eine fehlende Zelle ist eine Information, kein Nichts.
  const zeilen = c.map((z, i) => {
    const v = mv[i];
    // Temperatur als eigene, zweite Farbe am Zeilenende. Sie kostet keine
    // Zeilenhoehe und zeigt den absoluten Wert — bei vier Zellen und wenigen
    // Kelvin Spanne ist die Zahl aussagekraeftiger als jede Balkenlaenge.
    // Fehlt sie bei einer Zelle, bleibt die Spalte dort leer (aber vorhanden,
    // sonst verrutscht das Raster der ganzen Zeile).
    const t  = (z && z.temp != null) ? z.temp : null;
    const tc = hatTemps
      ? `<span class="ba-zt" style="color:${_zellTempFarbe(t)}">${
          t == null ? '--' : t.toFixed(0) + '°'}</span>`
      : '';
    if (v == null) {
      return `<div class="ba-zeile-z">
        <span class="ba-znr">#${i + 1}</span>
        <span class="ba-zv" style="color:var(--text3)">-- V</span>
        <div class="ba-bahn-z"><div class="ba-null"></div></div>
        <span class="ba-mv" style="color:var(--text3)">--</span>
        ${tc}
      </div>`;
    }
    const a = v - mittel;
    const ueber = Math.abs(a) > _ZELL_SKALA_MV;
    const breite = Math.min(Math.abs(a) / _ZELL_SKALA_MV, 1) * 50;
    const links = a < 0 ? 50 - breite : 50;
    const f = _zellFarbe(a);
    return `<div class="ba-zeile-z">
      <span class="ba-znr">#${i + 1}</span>
      <span class="ba-zv">${(v / 1000).toFixed(3)}<small> V</small></span>
      <div class="ba-bahn-z"><div class="ba-null"></div>
        <div class="ba-bar-z${ueber ? ' ba-bar-ueber' : ''}"
             style="left:${links}%;width:${breite}%;background:${f}"></div></div>
      <span class="ba-mv" style="color:${f}">${a > 0 ? '+' : ''}${a.toFixed(0)} mV</span>
      ${tc}
    </div>`;
  }).join('');

  const flaggen = [
    bms.allow_charge    === false ? '<span class="chip warn">Laden gesperrt</span>' : '',
    bms.allow_discharge === false ? '<span class="chip warn">Entladen gesperrt</span>' : '',
    bms.comm_error                ? '<span class="chip err">Kommunikationsfehler</span>' : '',
  ].filter(Boolean).join('');

  // Die Temperaturen stehen jetzt je Zelle in der Zeile; die Fusszeile nennt
  // nur noch die Spanne, wenn es etwas zu nennen gibt.
  const tw = c.map(z => (z && z.temp != null) ? z.temp : null).filter(x => x != null);
  const temps = tw.length
    ? (Math.min(...tw) === Math.max(...tw)
        ? `alle ${tw[0].toFixed(0)} °C`
        : `${Math.min(...tw).toFixed(0)}–${Math.max(...tw).toFixed(0)} °C`)
    : '';

  return `<div class="sb-card">
    <div class="sb-hd">${icon('gauge', {size: 14})} Zellen
      ${flaggen}
      <span class="chip">Mittel ${(mittel / 1000).toFixed(3)} V</span>
      <span class="chip" style="color:${_zellFarbe(spreizung * 3 / 4)}">Spreizung ${spreizung.toFixed(0)} mV</span>
    </div>
    <div class="ba-zellen">${zeilen}</div>
    <div class="vl-hinweis">Abweichung vom Zellmittel, Skala &plusmn;${_ZELL_SKALA_MV} mV${
      temps ? ' · ' + temps : ''}</div>
  </div>`;
}


/**
 * Geraete unter dem Verlauf — einheitlich aufgebaut.
 *
 * Vorher waren das die alten Kacheln mit je 2 bis 8 Wertepaaren; dadurch waren
 * sie unterschiedlich hoch und die Reihe lief unten als Treppe aus. Jetzt hat
 * jedes Geraet dieselbe Form: Name, Zustands-Chip, eine grosse Wattzahl, eine
 * Zusatzzeile. Was daran nicht passt, gehoert nicht auf die Batterieseite.
 */
function _baGeraete(data) {
  const zeile = (...t) => t.filter(x => x != null && x !== '').join(' · ');
  const v2 = v => v != null ? v.toFixed(2) + ' V' : null;
  const a1 = v => v != null ? v.toFixed(1) + ' A' : null;

  const c = data.charger, s = data.solar, o = data.orion, i = data.inverter;
  const liste = [];

  if (c && (c.cs != null || c.power != null)) liste.push({
    name: 'Landstrom', icon: 'plug', watt: c.power, chip: c.cs_label ?? c.state,
    an: c.cs != null && c.cs !== 0, sub: zeile(v2(c.dc_voltage), a1(c.dc_current)),
  });
  if (s && (s.cs != null || s.power != null)) liste.push({
    name: 'Solar', icon: 'solar', watt: s.power, chip: s.cs_label ?? s.mppt_mode_label,
    an: s.cs != null && s.cs !== 0,
    sub: zeile(s.ppv != null ? 'Panel ' + Math.round(s.ppv) + ' W' : null,
               s.yield_today_wh != null ? 'heute ' + Math.round(s.yield_today_wh) + ' Wh' : null),
  });
  if (o && (o.cs != null || o.power != null)) liste.push({
    name: 'DC-DC Orion', icon: 'alternator', watt: o.power,
    chip: o.cs === 0 ? (o.off_reason_label ?? 'Aus') : (o.cs_label ?? o.state),
    an: o.cs != null && o.cs !== 0,
    sub: zeile(o.input_voltage != null ? 'Ein ' + o.input_voltage.toFixed(1) + ' V' : null,
               v2(o.dc_voltage)),
  });
  if (i && (i.cs != null || i.state != null)) liste.push({
    name: 'Inverter', icon: 'bolt', watt: i.ac_power ?? i.power,
    chip: i.cs_label ?? i.state, an: i.cs != null && i.cs !== 0, fehler: !!i.err,
    sub: zeile(i.ac_voltage != null ? i.ac_voltage.toFixed(0) + ' V~' : null,
               i.ac_current != null ? i.ac_current.toFixed(1) + ' A~' : null),
  });

  if (!liste.length) return '';
  return liste.map(g => `<div class="bd-src bd-src-${
      g.fehler ? 'err' : g.an ? 'ok' : 'idle'}">
    <div class="bd-src-head">
      <span style="display:inline-flex;color:var(--text2)">${icon(g.icon, {size: 15})}</span>
      <span class="bd-src-name">${g.name}</span>
      <span class="chip ${g.fehler ? 'err' : g.an ? 'on' : ''}">${_esc(g.chip ?? '--')}</span>
    </div>
    <div class="bd-src-main">
      <b>${g.watt != null ? Math.round(g.watt) : '--'}</b><span>W</span>
    </div>
    <div class="bd-src-sub">${_esc(g.sub) || '&nbsp;'}</div>
  </div>`).join('');
}

/**
 * Plausible Starterspannung?
 *
 * Die Min-/Max-Werte sind die Schleppzeiger des Shunts (Victron H15/H16). Sie
 * halten fest, was am Hilfseingang JE gemessen wurde — auch Momente, in denen
 * dort gar keine Batterie hing. Live gemessen kam so ein Minimum von -0,004 V
 * zurueck. Frueher wurde das auf 0 geklemmt und als "0.00 V" angezeigt; das
 * liest sich wie eine tiefentladene Batterie, ist aber ein Aussetzer der
 * Messung. Eine angeschlossene Bleibatterie liegt selbst voellig platt noch
 * ueber 5 V, und ueber 20 V kann am 12-V-Eingang nichts Echtes stehen.
 * Ausserhalb dieses Fensters zeigen wir "--" statt einer erfundenen Zahl.
 */
const _ST_MIN_PLAUSIBEL = 5.0;
const _ST_MAX_PLAUSIBEL = 20.0;
const _stPlausibel = v =>
  (v != null && v >= _ST_MIN_PLAUSIBEL && v <= _ST_MAX_PLAUSIBEL) ? v : null;

/** Starterbatterie: eigene, kleine Karte — gehoert NICHT zur Bank. */
function _starterKarte(b) {
  if (!b || b.starter_voltage == null) return '';
  const v = b.starter_voltage;
  const farbe = v < 11.8 ? 'var(--red)' : v < 12.2 ? 'var(--yellow)' : null;
  const min = _stPlausibel(b.starter_min_voltage);
  const max = _stPlausibel(b.starter_max_voltage);
  return `<div class="starter">
    ${_sbStat('Starterbatterie', v.toFixed(2), 'V', farbe)}
    ${_sbStat('Min', min != null ? min.toFixed(2) : null, 'V')}
    ${_sbStat('Max', max != null ? max.toFixed(2) : null, 'V')}
  </div>`;
}

/**
 * Baut die Batterie-Detailseite.
 * Reihenfolge: Servicebatterie -> Verlauf -> Geraete -> Starterbatterie.
 *
 * Die Geraete kommen aus _baGeraete(); die alten Einzelkacheln (_tileMppt,
 * _tileBms & Co.) sind mit diesem Umbau entfallen. Wer ein NEUES Geraet
 * ergaenzt, haengt es in _baGeraete() an die Liste — nicht mehr an _tile()/_kv().
 */
function renderDeviceTiles(data) {
  // Jede Karte in ihr eigenes Rasterfeld. Vorher landeten Antwort, Bilanz und
  // Zellen zusammen in EINEM Container und konnten deshalb nur untereinander
  // stehen — das war der Grund fuer den langen Stapel.
  const setz = (id, html) => { const e = $(id); if (e) e.innerHTML = html; };
  setz('bdAntwort', _baAntwort(data));
  setz('bdBilanz',  _baBilanz(data));
  setz('bdZellen',  _baZellen(data));
  setz('bdSources', _baGeraete(data));
  setz('bdDiag',    _starterKarte(data.battery ?? null));
}
