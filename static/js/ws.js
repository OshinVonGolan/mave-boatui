// ── WebSocket ──────────────────────────────────────────────────────────────

let ws = null, reconnectTimer = null;

// Der Schriftzug neben dem Punkt ist entfallen; der Zustand steckt jetzt in
// der Farbe des Punktes und im title-Attribut. lbl bleibt optional, damit ein
// spaeteres Wiedereinsetzen des Labels ohne Aenderung hier funktioniert.
const _CONN_TEXT = { ok: 'Verbunden', warn: 'Verbinde…', off: 'Getrennt' };

function setConnState(state) {
  const dot = $('connDot'), lbl = $('connLabel');
  const art = state === 'ok' ? 'ok' : state === 'warn' ? 'warn' : 'off';
  if (dot) {
    dot.className = art === 'off' ? 'conn-dot' : 'conn-dot ' + art;
    const badge = dot.parentElement;
    if (badge) badge.title = _CONN_TEXT[art];
  }
  if (lbl) lbl.textContent = _CONN_TEXT[art];
}

// ── Ladeströme ─────────────────────────────────────────────────────────────

const CHARGE_SOURCES = [
  { key: 'charger',    label: 'Ladegerät',     color: '#3b82f6', get: d => d.charger?.power },
  { key: 'solar1',     label: 'Solar 1',       color: '#eab308', get: d => d.solar?.power },
  { key: 'solar2',     label: 'Solar 2',       color: '#f97316', get: d => d.solar2?.power },
  { key: 'solar3',     label: 'Solar 3',       color: '#f59e0b', get: d => d.solar3?.power },
  { key: 'alternator', label: 'Lichtmaschine', color: '#06b6d4', get: d => d.alternator?.power ?? d.orion?.output_power },
  { key: 'wind',       label: 'Wind / Hydro',  color: '#22c55e', get: d => d.wind?.power },
];

const chargeHist = [];   // [{ts, charger, solar1, solar2, solar3, alternator, wind}, ...]
const CHARGE_HIST_MAX = 25000;
const CHARGE_HIST_MAX_AGE_S = 25 * 3600;   // zeitbasiert kappen, siehe trimHist()
let chargeRange    = 'live';
let _lastChargeActive = [];

function setChargeRange(range) {
  chargeRange = range;
  ['Live','24h','7d'].forEach(r => {
    const key = r === 'Live' ? 'live' : r === '24h' ? '24h' : '7d';
    $(`cpBtn${r}`)?.classList.toggle('active', key === range);
  });
  reRenderChargePie();
}

function reRenderChargePie() {
  if (chargeRange === 'live') {
    renderChargePie(_lastChargeActive, false);
    return;
  }
  const cutoffSec = chargeRange === '24h' ? 86400 : 604800;
  const cutoff    = nowTs() - cutoffSec;
  const window    = chargeHist.filter(e => e.ts >= cutoff);

  if (!window.length) { renderChargePie([], false); return; }

  // Durchschnitt nur über Einträge mit Wert > 0 je Quelle
  const sums = {}, counts = {};
  window.forEach(e => {
    CHARGE_SOURCES.forEach(s => {
      if (e[s.key] > 0) {
        sums[s.key]   = (sums[s.key]   || 0) + e[s.key];
        counts[s.key] = (counts[s.key] || 0) + 1;
      }
    });
  });
  const active = CHARGE_SOURCES
    .map(s => ({ label: s.label, color: s.color, watts: counts[s.key] ? sums[s.key] / counts[s.key] : 0 }))
    .filter(s => s.watts > 0);
  renderChargePie(active, true);
}

function renderChargePie(active, isAvg) {
  const noData  = $('chargeSourceNoData');
  const hasData = $('chargeSourceHasData');
  if (!hasData) return;
  if (!active.length) {
    if (noData)  noData.style.display  = '';
    hasData.style.display = 'none';
    return;
  }
  if (noData)  noData.style.display  = 'none';
  hasData.style.display = '';

  const canvas = $('chargeSourceChart');
  const dpr = window.devicePixelRatio || 1;
  const S = 88;
  canvas.width  = S * dpr;
  canvas.height = S * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const total = active.reduce((s, x) => s + x.watts, 0);
  const cx = S / 2, cy = S / 2, R = 38, r = 22;
  let angle = -Math.PI / 2;

  ctx.clearRect(0, 0, S, S);

  active.forEach(src => {
    const sweep = (src.watts / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, R, angle, angle + sweep);
    ctx.closePath();
    ctx.fillStyle = src.color;
    ctx.fill();
    angle += sweep;
  });

  // Loch
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = '#263348';
  ctx.fill();

  // Mitte: Gesamtleistung (mit Ø-Zeichen bei Durchschnitt)
  const totalStr = total >= 1000 ? (total / 1000).toFixed(1) + ' kW' : Math.round(total) + ' W';
  ctx.fillStyle = '#f1f5f9';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  if (isAvg) {
    ctx.font = '700 9px -apple-system,sans-serif';
    ctx.fillText('Ø', cx, cy - 7);
    ctx.font = `700 ${total >= 1000 ? 9 : 11}px -apple-system,sans-serif`;
    ctx.fillText(totalStr, cx, cy + 5);
  } else {
    ctx.font = `700 ${total >= 1000 ? 10 : 12}px -apple-system,sans-serif`;
    ctx.fillText(totalStr, cx, cy);
  }

  // Legende
  const legEl = $('chargeSourceLegend');
  if (!legEl) return;
  legEl.innerHTML = active.map(s => {
    const pct = Math.round(s.watts / total * 100);
    return `<div class="charge-src-row">
      <div class="charge-src-dot" style="background:${s.color}"></div>
      <span class="charge-src-label">${s.label}</span>
      <span class="charge-src-val">${isAvg ? 'Ø ' : ''}${Math.round(s.watts)} W</span>
      <span class="charge-src-pct">${pct}%</span>
    </div>`;
  }).join('');
}

// Welche Alarmregel faerbt welche Kachel rot. Bewusst eine Zuordnung statt
// einer Namenskonvention: nicht jeder Alarm gehoert sichtbar auf eine Kachel,
// und derselbe Alarm kann kuenftig auf mehreren stehen.
const _ALARM_CARD_MAP = {
  bat_soc_warn: 'battCard', bat_soc_crit: 'battCard',
  bat_voltage: 'battCard', bat_temp_high: 'battCard',
  starter_voltage: 'battCard',
  bms_comm_err: 'battCard', bms_min_v: 'battCard', bms_max_v: 'battCard',
  bms_min_t: 'battCard', bms_max_t: 'battCard',
  hz_offline: 'heizungCard', hz_fehlercode: 'heizungCard',
  hz_frost: 'heizungCard', hz_frost_warn: 'heizungCard',
  hz_kein_raum: 'heizungCard',
};

// Jede Kachel, die ueberhaupt eine Umrandung bekommen kann — auch die ohne
// aktuell aktiven Alarm muss zurueckgesetzt werden, sonst bleibt die Umrandung
// nach dem Aufloesen stehen.
const _ALARM_CARDS = [...new Set(Object.values(_ALARM_CARD_MAP))];

function _applyAlarmBorders(alarms) {
  const activeKeys = new Set(
    alarms.filter(a => !a.resolved).map(a => a.key)
  );
  // Kacheln mit aktivem Alarm einsammeln und ALLE bekannten Kacheln setzen
  // bzw. zuruecksetzen. Vorher war hier nur die Batterie fest verdrahtet.
  const betroffen = new Set();
  activeKeys.forEach(k => { const c = _ALARM_CARD_MAP[k]; if (c) betroffen.add(c); });
  _ALARM_CARDS.forEach(id => $(id)?.classList.toggle('card--alarm', betroffen.has(id)));
  // Tank-Balken
  [1, 2].forEach(i => {
    const wrap = $(`tank${i}Fill`)?.parentElement;
    const alarmKey = `tank${i}_low`;
    wrap?.classList.toggle('tank-bar-wrap--alarm', activeKeys.has(alarmKey));
  });
}

function _setSourceDot(dotId, active) {
  const el = $(dotId);
  if (!el) return;
  el.style.background  = active ? 'var(--green)' : 'var(--border)';
  el.style.boxShadow   = active ? '0 0 4px var(--green)' : 'none';
}

// maxA: Maximalstrom für die Bar (Lader/Orion=50A, Solar=30A, Alt.=80A)
function _updateSrcBar(barId, fillId, txtId, currentA, maxA, label) {
  const bar = $(barId), fill = $(fillId), txt = $(txtId);
  if (!fill || !txt || !bar) return;
  const a   = currentA ?? 0;
  const active = a > 0.2;
  const pct = active ? Math.min(100, (a / maxA) * 100) : 0;
  fill.style.width      = pct + '%';
  fill.style.background = active ? 'var(--green)' : 'transparent';
  // Heller Text: sichtbar rechts vom Fill (auf dunklem Hintergrund)
  txt.textContent  = label;
  txt.style.clipPath = pct > 0 ? `inset(0 0 0 ${pct}%)` : '';
  // Dunkler Text: sichtbar links vom Fill (auf grünem Hintergrund)
  const dark = bar.querySelector('.src-bar-text-dark');
  if (dark) { dark.textContent = label; dark.style.clipPath = `inset(0 ${100 - pct}% 0 0)`; }
}

function updatePowerSources(data) {
  let anyVisible = false;

  function showSource(itemId, valId, watts) {
    const item = $(itemId);
    if (watts != null) {
      $(valId).textContent = Math.round(watts);
      item.classList.add('visible');
      anyVisible = true;
    }
  }

  const _altCurrent = data.alternator?.current ?? data.orion?.dc_current;
  if (data.solar?.power != null)      showSource('srcSolar', 'solarW', data.solar.power);
  if (data.alternator?.power != null) showSource('srcAlt',   'altW',   data.alternator.power);

  $('srcRow').classList.toggle('hidden', !anyVisible);

  // Progress-Bars rechts neben dem Gauge
  _updateSrcBar('srcBarCharger', 'srcBarChargerFill', 'srcBarChargerTxt',
    data.charger?.dc_current, 50, 'Charger');
  _updateSrcBar('srcBarSolar',   'srcBarSolarFill',   'srcBarSolarTxt',
    data.solar?.current,      30, 'Solar');
  _updateSrcBar('srcBarAlt',     'srcBarAltFill',     'srcBarAltTxt',
    _altCurrent, 50, 'Alternator');

  // Batterie-Kachel: Breite-Variante Einspeiser-Werte
  const _w = (id, v) => { const el = $(id); if (el) el.textContent = v != null ? Math.round(v) : '--'; };
  _w('battWideSrcCharger', data.charger?.power);
  _w('battWideSrcSolar', (data.solar?.power ?? 0) + (data.solar2?.power ?? 0) + (data.solar3?.power ?? 0) || null);
  _w('battWideSrcAlt',   data.alternator?.power ?? data.orion?.output_power);

  // History aufzeichnen
  const entry = { ts: nowTs() };
  CHARGE_SOURCES.forEach(s => { entry[s.key] = s.get(data) ?? 0; });
  // Getaktet wie histData: sonst deckten 25.000 Eintraege bei jedem WS-Tick
  // nur ~20 Minuten ab, und die Knoepfe "24 h" / "7 T" mittelten in Wahrheit
  // ueber diese 20 Minuten.
  // WICHTIG: nur das Wegschreiben ist getaktet. Ein frühes return hier würde
  // auch die Live-Torte darunter ausbremsen — die soll bei jedem Zustand
  // aktualisieren, nicht nur alle 5 s.
  const letzterC = chargeHist.length ? chargeHist[chargeHist.length - 1] : null;
  if (!letzterC || (entry.ts - letzterC.ts) >= HIST_MIN_GAP_S) {
    chargeHist.push(entry);
    trimHist(chargeHist, CHARGE_HIST_MAX_AGE_S, CHARGE_HIST_MAX);
  }

  _lastChargeActive = CHARGE_SOURCES
    .map(s => ({ label: s.label, color: s.color, watts: s.get(data) ?? 0 }))
    .filter(s => s.watts > 0);

  reRenderChargePie();
}

let _lastData = null;

async function setInverterMode(mode) {
  try {
    const r = await fetch('/api/inverter/mode', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    });
    if (!r.ok) {
      // API-Fehler: optimistischen Zustand rückgängig machen
      const e = await r.json().catch(() => {});
      alert('Fehler: ' + (e?.detail || r.status));
      _invCurrentState = mode === 2 ? 'Aus' : 'Aktiv';
      _invPending = false;
      _invLockUntil = 0;
      if (_lastData?.inverter) updateInverterCard(_lastData.inverter, _lastData?.charger);
    }
  } catch(e) {
    alert('Verbindungsfehler');
    _invPending = false; _invLockUntil = 0;
  }
}

// ── Inverter / 230V Kachel ─────────────────────────────────────────────────

const INV_RATED_W = 2000;  // Nennleistung (= 100%)
const INV_MAX_W   = 2500;  // Gauge-Maximum (125% für Spitzen sichtbar)
const _INV_CX = 80, _INV_CY = 68, _INV_R = 58;
const _INV_START = 225, _INV_SWEEP = 270;
const _SHORE_STATES = new Set(['Bulk','Absorption','Float','Storage','Equalise','Starting','Auto-Equalise','Const VI','Überladen','Kein Float','Ext. Control','External Control']);

function _invArc(cx, cy, r, start, sweep) {
  const rad = a => (a - 90) * Math.PI / 180;
  const x1 = cx + r * Math.cos(rad(start)), y1 = cy + r * Math.sin(rad(start));
  const x2 = cx + r * Math.cos(rad(start + sweep)), y2 = cy + r * Math.sin(rad(start + sweep));
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${sweep > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}
$('invGaugeTrack')?.setAttribute('d', _invArc(_INV_CX, _INV_CY, _INV_R, _INV_START, _INV_SWEEP));
// 100%-Markierung bei INV_RATED_W/INV_MAX_W des Bogens
(function() {
  const mark = $('invGaugeMark');
  if (!mark) return;
  const markPct  = INV_RATED_W / INV_MAX_W;          // 0.8 = 80% des Gauge
  const markDeg  = _INV_START + _INV_SWEEP * markPct;
  const rad      = a => (a - 90) * Math.PI / 180;
  const r1 = _INV_R - 8, r2 = _INV_R + 8;
  const x1 = _INV_CX + r1 * Math.cos(rad(markDeg));
  const y1 = _INV_CY + r1 * Math.sin(rad(markDeg));
  const x2 = _INV_CX + r2 * Math.cos(rad(markDeg));
  const y2 = _INV_CY + r2 * Math.sin(rad(markDeg));
  mark.setAttribute('d', `M ${x1.toFixed(1)} ${y1.toFixed(1)} L ${x2.toFixed(1)} ${y2.toFixed(1)}`);
})();

let _invCurrentState = 'Aus';
let _invLockUntil   = 0;
let _invPending     = false;   // true: Befehl gesendet, warte auf Bus-Bestätigung

function toggleInverter() {
  const isOn = _invCurrentState === 'Aktiv' || _invCurrentState === 'Eco';
  const newMode = isOn ? 4 : 2;

  // Optimistic: sofort umschalten, kein Warten auf API-Roundtrip oder Bus-Frame
  _invCurrentState = isOn ? 'Aus' : 'Aktiv';
  _invLockUntil    = Date.now() + 20000;   // 20 s Lock — Inverter braucht Zeit zum Starten
  _invPending      = !isOn;                // "Startet…" nur beim Einschalten
  updateInverterCard({ state: _invCurrentState });

  setInverterMode(newMode);
}

function updateInverterCard(inv, charger) {
  if (!inv) return;
  const fromBus = charger !== undefined;

  // Bus-Frame während Lock: optimistischen Zustand beibehalten
  if (fromBus && Date.now() < _invLockUntil) inv = { ...inv, state: _invCurrentState };

  // Bus bestätigt Zustand → Pending aufheben
  if (fromBus && _invPending && (inv.state === 'Aktiv' || inv.state === 'Eco')) _invPending = false;

  _invCurrentState = inv.state || 'Aus';

  // Landstrom: aktiv wenn Ladegerät-PGN in den letzten 30s empfangen wurde
  const shoreActive = charger?.active === true
                   || (charger?.active == null && charger?.state != null && _SHORE_STATES.has(charger.state));
  const dot = $('shoreIndicator');
  const lbl = $('shoreLabel');
  if (dot) dot.className = 'shore-dot' + (shoreActive ? ' on' : '');
  if (lbl) { lbl.textContent = 'Landstrom'; lbl.style.color = shoreActive ? 'var(--green)' : 'var(--text3)'; }

  const isActive = inv.state === 'Aktiv' || inv.state === 'Eco';

  const _INV_AR = {
    0x0001:'Niedrige Batterie', 0x0002:'Überhitzung', 0x0008:'Überlast',
    0x0010:'Batt. zu niedrig',  0x0020:'Zu heiß',     0x0040:'Overload',
    0x0100:'AC abgeschaltet',
  };

  const indicatorRow = $('invIndicatorRow');
  if (indicatorRow) indicatorRow.style.display = 'flex';

  const invDot = $('invStatusDot'), invLbl = $('invStatusLabel');
  if (invDot && invLbl) {
    const ar   = inv.err  || 0;
    const warn = inv.warn || 0;
    if (ar) {
      const msgs = Object.entries(_INV_AR).filter(([k]) => ar & +k).map(([,v]) => v);
      invDot.style.background = 'var(--red)';
      invDot.style.boxShadow  = '0 0 4px var(--red)';
      invLbl.textContent = msgs.length ? msgs[0] : `Alarm 0x${ar.toString(16)}`;
      invLbl.style.color = 'var(--red)';
    } else if (warn) {
      invDot.style.background = 'var(--yellow)';
      invDot.style.boxShadow  = '0 0 4px var(--yellow)';
      invLbl.textContent = 'Warnung';
      invLbl.style.color = 'var(--yellow)';
    } else if (_invPending) {
      invDot.style.background = 'var(--yellow)';
      invDot.style.boxShadow  = '0 0 4px var(--yellow)';
      invLbl.textContent = 'Startet…';
      invLbl.style.color = 'var(--yellow)';
    } else if (isActive) {
      invDot.style.background = 'var(--green)';
      invDot.style.boxShadow  = '0 0 4px var(--green)';
      invLbl.textContent = 'Inverter';
      invLbl.style.color = 'var(--green)';
    } else {
      invDot.style.background = 'var(--border)';
      invDot.style.boxShadow  = 'none';
      invLbl.textContent = 'Inverter';
      invLbl.style.color = 'var(--text3)';
    }
  }

  // Gauge
  const power = inv.power;
  const gaugePct  = (power != null && isActive) ? Math.max(0, Math.min(100, power / INV_MAX_W * 100)) : 0;
  const displayPct = (power != null && isActive) ? Math.round(power / INV_RATED_W * 100) : 0;
  const color = gaugePct >= 85 ? 'var(--red)' : gaugePct >= 65 ? 'var(--yellow)' : 'var(--green)';
  const gaugeEl = $('invGaugeVal');
  if (gaugeEl) {
    const sweep = _INV_SWEEP * gaugePct / 100;
    gaugeEl.setAttribute('d', sweep < 2 ? '' : _invArc(_INV_CX, _INV_CY, _INV_R, _INV_START, sweep));
    gaugeEl.style.stroke = color;
  }
  const loadEl = $('invLoadPct');
  if (loadEl) loadEl.textContent = isActive ? displayPct + '%' : '--%';

  // Tiles
  const vEl = $('invAcV');
  if (vEl) vEl.textContent = (isActive && inv.ac_voltage != null) ? inv.ac_voltage.toFixed(0) : '--';
  const pwEl = $('invPowerVal');
  if (pwEl) pwEl.textContent = isActive && power != null ? Math.round(power) : (shoreActive ? '--' : '--');
  // 2×2-Grid: Landstrom + Inverter-Status (Home-Kachel)
  const shEl = $('invShoreVal');
  if (shEl) { shEl.textContent = shoreActive ? 'Aktiv' : 'Inaktiv'; shEl.style.color = shoreActive ? 'var(--green)' : 'var(--text3)'; }
  const stEl = $('invStateVal');
  if (stEl) { stEl.textContent = inv.state ?? '--'; stEl.style.color = isActive ? 'var(--green)' : 'var(--text3)'; }

  // Halbe-Höhe-Zusammenfassung
  const _si = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  _si('invHalfPct', isActive ? displayPct + '%' : '--%');
  _si('invHalfW',   isActive && power != null ? Math.round(power) : '--');

  // Breite-Variante: Status-Detail
  const _v = $('invWideAcV');
  if (_v) _v.textContent = isActive && inv.ac_voltage != null ? inv.ac_voltage.toFixed(0) : '--';
  const _sh = $('invWideShore');
  if (_sh) { _sh.textContent = shoreActive ? 'Aktiv' : 'Inaktiv'; _sh.style.color = shoreActive ? 'var(--green)' : 'var(--text3)'; }
  const _st = $('invWideState');
  if (_st) { _st.textContent = inv.state ?? '--'; _st.style.color = isActive ? 'var(--green)' : 'var(--text3)'; }

  // Pill-Toggle
  $('invSlideTrack')?.classList.toggle('on', isActive);
}

function handleData(data) {
  if (data.ping) return;
  _lastData = data;
  // Jeder Frame verraet, ob er vom Pi kam oder aus der Server-Kopie.
  if (typeof quelleAusDaten === 'function') quelleAusDaten(data);
  if (data.version) {
    // NUR wenn sich der Text wirklich aendert.
    //
    // Gemessen: _kopfLogoPruefen kostete die Haelfte des gesamten Frames
    // (3,8 von 7,7 ms auf einem gedrosselten Kern). Es setzt eine Klasse und
    // liest danach scrollWidth — das erzwingt jedes Mal ein vollstaendiges
    // Neuberechnen des Layouts. Bei bis zu 20 Datensaetzen je Sekunde wurde
    // also zwanzigmal in der Sekunde nachgemessen, ob derselbe unveraenderte
    // Text noch neben den Schriftzug passt.
    //
    // Der Grund, warum es ueberhaupt hier steht, gilt weiterhin: der Text
    // aendert die Breite des Logos, aber nicht zwingend seine Rahmenmasse —
    // der ResizeObserver feuert dann nicht. Er gilt nur eben genau dann, wenn
    // der Text sich AENDERT.
    const neuerStand = 'v' + data.version;
    const feld = $('versionBadge');
    if (feld && feld.textContent !== neuerStand) {
      feld.textContent = neuerStand;
      if (typeof _kopfLogoPruefen === 'function') _kopfLogoPruefen();
    }
  }
  if (data.battery) updateBattery(data.battery);
  if (data.tanks)   updateTanks(data.tanks);
  if (data.lights) {
    state.lights = data.lights;
    updateChannels(data.lights.channels ?? []);
    syncLightOverlay(data.lights.channels ?? []);
  }
  updatePowerSources(data);
  updateSolarCard(data);
  updateFlow(data);
  if (data.inverter) updateInverterCard(data.inverter, data.charger);
  if (data.bms) updateBms(data.bms);
  if (!$('battOverlay').classList.contains('hidden')) renderDeviceTiles(data);
  updateStatusBar(data);   // dichte Kernwert-Zeile ueber den Kacheln
  if (data.alarms != null) {
    updateAlarmBadge(data.unack_alarms ?? 0);
    _applyAlarmBorders(data.alarms);
    // Und Krach machen, wenn einer neu ist. Was "neu" heisst und ob dieses
    // Geraet ueberhaupt Ton geben soll, entscheidet alarmton.js.
    if (typeof alarmTonPruefen === 'function') alarmTonPruefen(data.alarms);
    if (!$('alarmOverlay').classList.contains('hidden') &&
        $('tabAktiv').classList.contains('active')) {
      renderAlarms(data.alarms);
    }
  }
}

/**
 * Die Wartungskachel neu zeichnen — beim Groessenwechsel, nicht bei jedem Wert.
 *
 * Sie stand frueher mitten im Frame-Pfad und wurde damit bis zu zwanzigmal je
 * Sekunde neu gebaut. Das war Arbeit ohne Anlass: der Wartungsplan kommt aus
 * /api/wartung, nicht aus der Live-Verbindung — zwanzig Neubauten je Sekunde
 * erzeugten zwanzigmal exakt dasselbe HTML. Gemessen 1,15 von 7,7 ms je Frame.
 *
 * Was sich mit der Zeit doch aendert, ist das DATUM: "in 3 Tagen faellig" wird
 * irgendwann "ueberfaellig". Dafuer laeuft unten ein langsamer Takt — einmal je
 * Minute genuegt fuer etwas, das sich um Mitternacht aendert.
 */
function _syncWartungHeight() {
  if (typeof updateWartungHomeTile === 'function') updateWartungHomeTile();
}
window.addEventListener('resize', _syncWartungHeight);

// Der Tageswechsel, mehr nicht. Ueber createPoller und nicht ueber
// setInterval, damit er stehen bleibt, solange die Seite im Hintergrund liegt.
const _wartungTagPoller = (typeof createPoller === 'function')
  ? createPoller(_syncWartungHeight, 60000) : null;

let _histFetchPending = false;
let _histFetchOk      = 0;      // Zeitpunkt des letzten ERFOLGREICHEN Abrufs

// Wie weit zurück der Server gefragt wird — deckt den größten Bereich ab,
// den die Zeitknöpfe im Chart anbieten (24 h).
const HIST_FETCH_RANGE_S = 24 * 3600;
const HIST_REFETCH_AFTER_S = 300;   // nach 5 min darf erneut geholt werden

/**
 * Feinaufloesung fuer ein kurzes Zeitfenster nachladen.
 *
 * Der grosse Abruf holt 24 h mit 1500 Punkten. Der Server duennt dafuer
 * indexweise aus (main.py, _decimate_history): der Ringpuffer fasst 10800
 * Eintraege, macht 7,2 je Bucket, also rund 36 s zwischen zwei Punkten.
 * Fuer das 30-Minuten-Fenster ist das zu grob — der erste zeichenbare Punkt
 * liegt bis zu 41 s hinter dem linken Rand, und weil die Achse fest an der Uhr
 * haengt (tMin0 = jetzt - Fenster), bleibt dort ein leerer Streifen stehen.
 * Bei 1800 s sind 41 s gut 2 % der Breite und damit sichtbar; bei 6 Stunden
 * sind dieselben 41 s 0,19 % und fallen nicht auf. Genau das beschriebene
 * Muster.
 *
 * Deshalb fuer kurze Fenster gezielt nachfassen: dasselbe Fenster mit so
 * vielen Punkten, dass die 5-Sekunden-Kadenz der Aufzeichnung erhalten bleibt.
 * 30 Minuten sind damit 360 Punkte — ein kleiner Abruf, der unter der
 * 400-Eintraege-Schwelle bleibt, ab der die Serialisierung in den Executor
 * wandert.
 */
const _histFeinGeholt = {};            // je Fenster: Zeitpunkt des letzten Abrufs

function fetchHistoryFenster(secs) {
  if (!secs) return;
  const jetzt = Date.now() / 1000;
  if (_histFeinGeholt[secs] && (jetzt - _histFeinGeholt[secs]) < 60) return;
  _histFeinGeholt[secs] = jetzt;
  // Wie viele Punkte fuer dieses Fenster? Kurze Fenster in der 5-Sekunden-
  // Kadenz der Aufzeichnung, lange Fenster in der Minutenkadenz des groben
  // Verlaufs — mehr anzufordern braechte nichts, weil der Server dort ohnehin
  // nur Minutenmittel hat. 900 bleibt die Obergrenze: darueber wird die
  // Serialisierung auf dem Pi Zero spuerbar.
  const kadenz = secs > 6 * 3600 ? 60 : HIST_MIN_GAP_S;
  const punkte = Math.min(900, Math.ceil(secs / kadenz) + 10);
  fetch(`/api/history?range=${secs}&max_points=${punkte}`)
    .then(r => r.ok ? r.json() : null)
    .then(res => {
      if (!res) return;
      if (typeof res.server_now === 'number') setClockOffset(res.server_now);
      const eintraege = Array.isArray(res) ? res : (res.entries || []);
      if (!eintraege.length) return;
      // Gleiche Abbildung wie beim grossen Abruf: der Server fuehrt die
      // Solarleistung als 'solar1', die Chart-Serie liest 'solar'.
      eintraege.forEach(e => { if (e.solar == null && e.solar1 != null) e.solar = e.solar1; });
      _mergeHist(histData, eintraege);
      trimHist(histData, HIST_MAX_AGE_S, HIST_MAX);
      if (!$('battOverlay').classList.contains('hidden')) renderCharts(true);
    })
    .catch(err => console.warn('Feinverlauf konnte nicht geladen werden:', err));
}

/**
 * Holt den Verlauf vom Server und führt ihn mit dem zusammen, was der Client
 * inzwischen selbst gesammelt hat.
 *
 * Vorher stand hier zweimal `if (histData.length > 0) return;`. Das sah nach
 * einer Schutzmaßnahme aus, war aber der Grund, warum die Graphen leer blieben:
 * der WebSocket liefert den ersten State binnen Millisekunden, recordHistory
 * trägt ihn ein — und wenn die Antwort auf /api/history Sekunden später
 * eintraf, war histData längst nicht mehr leer und der komplette Server-
 * Verlauf wurde weggeworfen. Jeder Seitenaufbau hat den teuren Abruf also
 * bezahlt und das Ergebnis verworfen.
 *
 * Jetzt wird gemerged statt verworfen, und nach Reconnect darf erneut geholt
 * werden (vorher blockierte der Guard das für immer).
 */
function _fetchHistory(force) {
  if (_histFetchPending) return;
  if (!force && _histFetchOk && (Date.now() / 1000 - _histFetchOk) < HIST_REFETCH_AFTER_S) return;
  _histFetchPending = true;

  fetch(`/api/history?range=${HIST_FETCH_RANGE_S}&max_points=1500`)
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(res => {
      const eintraege = Array.isArray(res) ? res : (res.entries || []);

      // Zeitbasis: der Pi hat KEINE Echtzeituhr. Filtern wir Zeitfenster gegen
      // die Uhr des Telefons, kann eine falsch stehende Pi-Uhr alle Punkte
      // wegschneiden — der Graph wäre leer, obwohl Daten da sind.
      if (typeof res.server_now === 'number') setClockOffset(res.server_now);

      // Der Server fuehrt die Solarleistung als 'solar1' (solar2/solar3 sind
      // Vorbereitung fuer weitere Felder), die Chart-Serie liest aber 'solar'.
      // Ohne diese Abbildung zeigte die Solarkurve nur den seit Seitenaufruf
      // gesammelten Schwanz und die Legende stand dauerhaft auf "Solar --".
      eintraege.forEach(e => { if (e.solar == null && e.solar1 != null) e.solar = e.solar1; });
      _mergeHist(histData, eintraege);

      const CH_KEYS = ['solar1', 'charger', 'alternator'];
      _mergeHist(chargeHist, eintraege.map(e => {
        const ce = { ts: e.ts };
        CH_KEYS.forEach(k => { if (e[k] != null) ce[k] = e[k]; });
        return ce;
      }).filter(ce => Object.keys(ce).length > 1));

      _histFetchOk = Date.now() / 1000;
      recomputeDailyAhFromHist();
      if (!$('battOverlay').classList.contains('hidden')) renderCharts();
      reRenderChargePie();
    })
    .catch(err => {
      // Nicht verschlucken: ohne Meldung sucht man den leeren Graphen ewig.
      console.warn('Verlauf konnte nicht geladen werden:', err);
    })
    .finally(() => { _histFetchPending = false; });
}

/**
 * Führt Server-Einträge in ein bestehendes Array ein, ohne Doppelte.
 * Der Client sammelt zwischen den Abrufen selbst weiter — beide Quellen
 * beschreiben dieselbe Zeitachse, deshalb wird über den Zeitstempel
 * dedupliziert (auf die Sekunde gerundet) und danach sortiert.
 */
function _mergeHist(ziel, neue) {
  if (!neue.length) return;
  const bekannt = new Set(ziel.map(e => Math.round(e.ts)));
  let dazu = 0;
  for (const e of neue) {
    const k = Math.round(e.ts);
    if (bekannt.has(k)) continue;
    bekannt.add(k);
    ziel.push(e);
    dazu++;
  }
  if (dazu) ziel.sort((a, b) => a.ts - b.ts);
}

function connect() {
  setConnState('warn');
  _fetchHistory(); // startet parallel zum WS-Aufbau damit Daten früher da sind
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen    = () => {
    setConnState('ok'); clearTimeout(reconnectTimer);
    // Nach jedem (Wieder-)Verbinden nachladen: Server-Neustart, WLAN-Abriss
    // oder Handy aus dem Standby hinterlassen sonst eine Lücke im Verlauf,
    // die nie wieder gefüllt wurde.
    _fetchHistory(true);
  };
  ws.onmessage = ev => { try { handleData(JSON.parse(ev.data)); } catch(_) {} };
  ws.onclose = ws.onerror = () => {
    setConnState('off');
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 4000);
  };
}
