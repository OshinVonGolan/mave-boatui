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
  const pts = src.filter(d => d.ts >= t0 && d.ts <= t1 && d.soc != null);
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

  const socs = pts.map(p => p.soc);
  let lo = Math.min(...socs);
  let hi = Math.max(...socs);
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

// ── Device Tiles (Battery Detail Overlay) ─────────────────────────────────

const _MPPT_MODE_LABEL = ['Aus', 'Begrenzt', 'Aktiv'];
const _ORION_OR = {
  0x0001: 'Kein Eingang', 0x0002: 'Schalter aus', 0x0004: 'Remote',
  0x0008: 'Schutz aktiv', 0x0020: 'Payload', 0x0040: 'BMS', 0x0080: 'Motor-Absch.',
};

function _orLabel(or_val) {
  if (or_val == null) return null;
  const bits = Object.entries(_ORION_OR).filter(([bit]) => or_val & Number(bit)).map(([,lbl]) => lbl);
  return bits.length ? bits.join(', ') : 'OK';
}

function _kv(label, val, unit='', cls='') {
  if (val == null || val === '' || val === '--') return '';
  const v = unit ? `${val}<span class="dt-unit"> ${unit}</span>` : val;
  return `<div class="dt-kv${cls ? ' ' + cls : ''}"><span class="dt-lbl">${label}</span><span class="dt-val">${v}</span></div>`;
}

// status: 'ok' | 'idle' | 'warn' | 'err'
const _DOT_COLOR = { ok:'var(--green)', idle:'var(--border)', warn:'var(--yellow)', err:'var(--red)' };

// iconHtml: fertiges SVG aus icon(name) — KEIN Emoji (Projektregel).
// Parameter bewusst nicht "icon" genannt, sonst verdeckt er die Funktion icon().
function _tile(iconHtml, title, statusTxt, bodyHtml, status='idle') {
  const dotColor = _DOT_COLOR[status] ?? 'var(--border)';
  return `<div class="dt-card dt-card-${status}">
    <div class="dt-head">
      <span class="dt-icon" style="display:inline-flex;align-items:center;color:var(--text2)">${iconHtml}</span>
      <span class="dt-title">${title}</span>
      <span class="dt-dot" style="background:${dotColor}"></span>
      ${statusTxt ? `<span class="dt-status">${statusTxt}</span>` : ''}
    </div>
    <div class="dt-body">${bodyHtml}</div>
  </div>`;
}

function _chgCsState(cs) {
  if (cs == null) return 'idle';
  if (cs === 2)  return 'err';   // Fault
  if (cs === 0)  return 'idle';  // Off
  return 'ok';                    // Bulk/Absorption/Float/etc.
}

function _tileBattBoard(b, bms) {
  if (!b) return '';
  const socPct = b.soc != null ? Math.round(b.soc) : null;
  const socBar = socPct != null
    ? `<div class="dt-soc-wrap"><div class="dt-soc-fill" style="width:${socPct}%;background:${socPct>=50?'var(--green)':socPct>=20?'var(--yellow)':'var(--red)'}"></div></div>`
    : '';
  const socTxt = socPct != null ? `<span class="dt-soc-num">${socPct}%</span>` : '';
  // "Verbraucht" ist direkt der Shunt-Zaehler (negativ = entnommen, laeuft beim
  // Laden wieder Richtung 0). Frueher stand hier das Sitzungs-Minimum: die
  // Anzeige war eine Ratsche, lief nur nach unten und behauptete nach dem Laden
  // weiter, die Batterie sei leer.
  const body = `
    <div class="dt-soc-row">${socTxt}${socBar}</div>
    <div class="dt-kvgrid">
      ${_kv('Spannung',    b.voltage    != null ? b.voltage.toFixed(2) : null, 'V')}
      ${_kv('Strom',       b.current    != null ? b.current.toFixed(1) : null, 'A', b.current < 0 ? 'val-orange' : 'val-green')}
      ${_kv('Leistung',    b.power      != null ? Math.round(b.power)  : null, 'W', b.power < 0 ? 'val-orange' : 'val-green')}
      ${_kv('Verbraucht',  b.consumed_ah != null ? b.consumed_ah.toFixed(1) : null, 'Ah')}
      ${_kv('Min',         b.min_voltage != null ? b.min_voltage.toFixed(3) : null, 'V')}
      ${_kv('Max',         b.max_voltage != null ? b.max_voltage.toFixed(3) : null, 'V')}
      ${_kv('Zyklen',      b.cycles)}
      ${_kv('Voll vor',    b.time_since_full != null ? timeSince(b.time_since_full) : null)}
      ${_kv('Starter',     b.starter_voltage != null ? b.starter_voltage.toFixed(2) : null, 'V')}
      ${_kv('Temp.',       b.temperature != null ? b.temperature.toFixed(1) : null, '°C')}
    </div>`;
  const shuntStatus = socPct != null && socPct < 20 ? 'warn' : 'ok';
  return _tile(icon('battery', {size:14}), 'Servicebatterie / Shunt', socPct != null ? `${socPct}%` : '', body, shuntStatus);
}

function _tileBms(bms) {
  if (!bms || bms.voltage == null) return '';
  const flags = [
    bms.allow_charge    === false ? '<span class="dt-flag dt-flag-warn">Laden gesperrt</span>' : '',
    bms.allow_discharge === false ? '<span class="dt-flag dt-flag-warn">Entladen gesperrt</span>' : '',
    bms.comm_error      ? '<span class="dt-flag dt-flag-err">BMS Komm.-Fehler</span>' : '',
    bms.alarm_min_volt  ? '<span class="dt-flag dt-flag-err">Zellspg. zu niedrig</span>' : '',
    bms.alarm_max_volt  ? '<span class="dt-flag dt-flag-err">Zellspg. zu hoch</span>' : '',
    bms.alarm_min_temp  ? '<span class="dt-flag dt-flag-err">Temp. zu niedrig</span>' : '',
    bms.alarm_max_temp  ? '<span class="dt-flag dt-flag-err">Temp. zu hoch</span>' : '',
  ].filter(Boolean).join('');

  // Zellen-Grid: 2×2 (bzw. n×2) mit Spannung + optionaler Temperatur
  const cells = bms.cells ?? [];
  let cellGrid = '';
  if (cells.length > 0) {
    const hasMinAlarm = bms.alarm_min_volt;
    const hasMaxAlarm = bms.alarm_max_volt;
    const lo = bms.lowest_cell_v, hi = bms.highest_cell_v;
    const cellHtml = cells.map((c, i) => {
      const v = c.voltage != null ? c.voltage.toFixed(3) : '--';
      const t = c.temp    != null ? ` · ${c.temp.toFixed(1)}°` : '';
      // Nur Farbe wenn wirklich ein Alarm aktiv ist
      const isAlarmLo = hasMinAlarm && lo != null && c.voltage != null && Math.abs(c.voltage - lo) < 0.001;
      const isAlarmHi = hasMaxAlarm && hi != null && c.voltage != null && Math.abs(c.voltage - hi) < 0.001;
      const cls = isAlarmLo ? ' dt-cell-lo' : isAlarmHi ? ' dt-cell-hi' : '';
      return `<div class="dt-cell${cls}">
        <span class="dt-cell-nr">#${i + 1}</span>
        <span class="dt-cell-v">${v}<span class="dt-unit"> V</span></span>
        ${t ? `<span class="dt-cell-t">${t}</span>` : ''}
      </div>`;
    }).join('');
    cellGrid = `<div class="dt-cell-grid">${cellHtml}</div>`;
  }

  const body = `
    <div class="dt-kvgrid">
      ${_kv('Spannung',    bms.voltage       != null ? bms.voltage.toFixed(2) : null, 'V')}
      ${_kv('Strom',       bms.current_total != null ? bms.current_total.toFixed(2) : null, 'A', bms.current_total < 0 ? 'val-orange' : 'val-green')}
      ${_kv('SOC',         bms.soc != null ? bms.soc + ' %' : null)}
      ${_kv('Kapazität',   bms.capacity_ah   != null ? bms.capacity_ah.toFixed(1) : null, 'Ah')}
      ${_kv('Verbleibend', bms.remaining_kwh != null ? (bms.remaining_kwh * 1000).toFixed(0) : null, 'Wh')}
      ${_kv('Lade-A',      bms.current_charge    != null ? bms.current_charge.toFixed(2) : null, 'A')}
      ${_kv('Entlade-A',   bms.current_discharge != null ? bms.current_discharge.toFixed(2) : null, 'A')}
      ${bms.lowest_temp    != null ? _kv('Temp. min/max', `${bms.lowest_temp.toFixed(1)} / ${(bms.highest_temp??0).toFixed(1)} °C`) : ''}
    </div>
    ${cellGrid}
    ${flags ? `<div class="dt-flags">${flags}</div>` : ''}`;
  const socTxt = bms.soc != null ? `SOC ${bms.soc}%` : '';
  const bmsStatus = flags.length ? (bms.comm_error || bms.alarm_min_volt || bms.alarm_max_volt || bms.alarm_min_temp || bms.alarm_max_temp ? 'err' : 'warn') : 'ok';
  return _tile(icon('gauge', {size:14}), '123SmartBMS', socTxt, body, bmsStatus);
}

function _tileMppt(solar) {
  if (!solar || (solar.voltage == null && solar.vpv == null && solar.cs_label == null)) return '';
  const state = solar.cs_label ?? '--';
  const isActive = solar.cs != null && solar.cs !== 0;
  const mpptLbl = solar.mppt_mode != null ? (_MPPT_MODE_LABEL[solar.mppt_mode] ?? `Mode ${solar.mppt_mode}`) : null;
  const body = `
    <div class="dt-kvgrid">
      ${_kv('Zustand',        state)}
      ${_kv('Tracker',        mpptLbl)}
      ${_kv('Batterie-Spg.', solar.voltage  != null ? solar.voltage.toFixed(3)  : null, 'V')}
      ${_kv('Lade-Strom',    solar.current  != null ? solar.current.toFixed(3)  : null, 'A', 'val-green')}
      ${_kv('Ausgangsleistung', solar.power != null ? Math.round(solar.power)   : null, 'W', 'val-green')}
      ${_kv('Panel-Spannung', solar.vpv     != null ? solar.vpv.toFixed(2)      : null, 'V')}
      ${_kv('Panel-Leistung', solar.ppv     != null ? Math.round(solar.ppv)     : null, 'W', 'val-green')}
      ${_kv('Ertrag heute',   solar.yield_today_wh    != null ? solar.yield_today_wh    : null, 'Wh')}
      ${_kv('Max heute',      solar.max_power_today_w != null ? solar.max_power_today_w : null, 'W')}
    </div>`;
  return _tile(icon('solar', {size:14}), 'MPPT 75/15', state, body, _chgCsState(solar.cs));
}

function _tileOrion(orion) {
  if (!orion || (orion.cs == null && orion.output_power == null)) return '';
  const state = orion.state ?? orion.cs_label ?? '--';
  const isActive = orion.cs != null && orion.cs !== 0;
  const orLbl = orion.off_reason_label ?? _orLabel(orion.off_reason);
  const body = `
    <div class="dt-kvgrid">
      ${_kv('Zustand',           state)}
      ${_kv('Aus-Spg.',          orion.dc_voltage    != null ? orion.dc_voltage.toFixed(3)   : null, 'V')}
      ${_kv('Aus-Strom',         orion.dc_current    != null ? orion.dc_current.toFixed(3)   : null, 'A', 'val-green')}
      ${_kv('Ausgangsleistung',  orion.output_power  != null ? Math.round(orion.output_power): null, 'W', 'val-green')}
      ${_kv('Ein-Spannung',      orion.input_voltage != null ? orion.input_voltage.toFixed(3): null, 'V')}
      ${_kv('Ein-Strom',         orion.input_current != null ? orion.input_current.toFixed(3): null, 'A')}
      ${_kv('Eingangsleistung',  orion.input_power   != null ? Math.round(orion.input_power) : null, 'W')}
      ${orLbl ? _kv('Off Reason', orLbl) : ''}
    </div>`;
  return _tile(icon('alternator', {size:14}), 'Orion-XS DC-DC', state, body, _chgCsState(orion.cs));
}

function _tileCharger(charger) {
  if (!charger || charger.state == null) return '';
  const isActive = charger.active !== false && charger.state && charger.state !== 'Aus';
  const body = `
    <div class="dt-kvgrid">
      ${_kv('Zustand',    charger.state)}
      ${_kv('Spannung',   charger.dc_voltage != null ? charger.dc_voltage.toFixed(3) : null, 'V')}
      ${_kv('Strom',      charger.dc_current != null ? charger.dc_current.toFixed(3) : null, 'A', 'val-green')}
      ${_kv('Leistung',   charger.power      != null ? Math.round(charger.power)     : null, 'W', 'val-green')}
    </div>`;
  const chargerCs = charger.cs ?? (charger.state === 'Aus' ? 0 : charger.state ? 1 : null);
  return _tile(icon('plug', {size:14}), 'Smart IP43', charger.state ?? '', body, _chgCsState(chargerCs));
}

function _tileInverter(inv) {
  if (!inv || inv.state == null) return '';
  const isActive = inv.state === 'Aktiv' || inv.state === 'Eco';
  const body = `
    <div class="dt-kvgrid">
      ${_kv('Zustand',     inv.state)}
      ${_kv('AC-Spannung', (isActive && inv.ac_voltage != null) ? inv.ac_voltage.toFixed(1) : null, 'V')}
      ${_kv('AC-Strom',    (isActive && inv.ac_current != null) ? inv.ac_current.toFixed(1) : null, 'A')}
      ${_kv('AC-Leistung', (isActive && inv.power != null) ? Math.round(inv.power) : null, 'W', 'val-orange')}
      ${_kv('DC-Spannung', inv.dc_voltage != null ? inv.dc_voltage.toFixed(3) : null, 'V')}
      ${_kv('DC-Strom',    inv.dc_current != null ? inv.dc_current.toFixed(3) : null, 'A')}
    </div>`;
  const invStatus = inv.state === 'Fehler' ? 'err' : isActive ? 'ok' : 'idle';
  return _tile(icon('bolt', {size:14}), 'Inverter 2000VA', inv.state ?? '', body, invStatus);
}

function _tileStarter(b) {
  if (!b || b.starter_voltage == null) return '';
  const body = `
    <div class="dt-kvgrid">
      ${_kv('Spannung', b.starter_voltage != null ? b.starter_voltage.toFixed(2) : null, 'V')}
      ${_kv('Min',      _starterMin != null ? _starterMin.toFixed(2) : null, 'V')}
      ${_kv('Max',      _starterMax != null ? _starterMax.toFixed(2) : null, 'V')}
    </div>`;
  return _tile(icon('battery', {size:14}), 'Starterbatterie', '', body, 'ok');
}

function renderDeviceTiles(data) {
  const sec = $('deviceTilesSection');
  if (!sec) return;
  const b   = data.battery ?? null;
  const bms = data.bms     ?? null;
  sec.innerHTML =
    _tileBattBoard(b, bms) +
    _tileBms(bms) +
    _tileMppt(data.solar) +
    _tileOrion(data.orion) +
    _tileCharger(data.charger) +
    _tileInverter(data.inverter) +
    _tileStarter(b);
}
