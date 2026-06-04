// ── WebSocket ──────────────────────────────────────────────────────────────

let ws = null, reconnectTimer = null;

function setConnState(state) {
  const dot = $('connDot'), lbl = $('connLabel');
  if (state==='ok')   { dot.className='conn-dot ok';   lbl.textContent='Verbunden'; }
  else if (state==='warn') { dot.className='conn-dot warn'; lbl.textContent='Verbinde…'; }
  else                { dot.className='conn-dot';       lbl.textContent='Getrennt'; }
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
  const cutoff    = Date.now() / 1000 - cutoffSec;
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

const _ALARM_CARD_MAP = {
  bat_soc_warn: 'battCard', bat_soc_crit: 'battCard',
  bat_voltage: 'battCard', bat_temp_high: 'battCard',
  starter_voltage: 'battCard',
  bms_comm_err: 'battCard', bms_min_v: 'battCard', bms_max_v: 'battCard',
  bms_min_t: 'battCard', bms_max_t: 'battCard',
};

function _applyAlarmBorders(alarms) {
  const activeKeys = new Set(
    alarms.filter(a => !a.resolved).map(a => a.key)
  );
  // Batterie-Kachel
  $('battCard')?.classList.toggle('card--alarm',
    [...activeKeys].some(k => _ALARM_CARD_MAP[k] === 'battCard'));
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

  const _altPower   = data.alternator?.power   ?? data.orion?.output_power;
  const _altCurrent = data.alternator?.current ?? data.orion?.dc_current;
  if (data.solar?.power != null) showSource('srcSolar', 'solarW', data.solar.power);
  if (_altPower != null)         showSource('srcAlt',   'altW',   _altPower);

  $('srcRow').classList.toggle('hidden', !anyVisible);

  // Progress-Bars rechts neben dem Gauge
  _updateSrcBar('srcBarCharger', 'srcBarChargerFill', 'srcBarChargerTxt',
    data.charger?.dc_current, 50, 'Charger');
  _updateSrcBar('srcBarSolar',   'srcBarSolarFill',   'srcBarSolarTxt',
    data.solar?.current,      30, 'Solar');
  _updateSrcBar('srcBarAlt',     'srcBarAltFill',     'srcBarAltTxt',
    _altCurrent, 80, 'Alternator');

  // History aufzeichnen
  const entry = { ts: Date.now() / 1000 };
  CHARGE_SOURCES.forEach(s => { entry[s.key] = s.get(data) ?? 0; });
  chargeHist.push(entry);
  if (chargeHist.length > CHARGE_HIST_MAX) chargeHist.shift();

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

  // Pill-Toggle
  $('invSlideTrack')?.classList.toggle('on', isActive);
}

function handleData(data) {
  if (data.ping) return;
  _lastData = data;
  if (data.version) $('versionBadge').textContent = 'v' + data.version;
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
  _syncWartungHeight();
  if (data.alarms != null) {
    updateAlarmBadge(data.unack_alarms ?? 0);
    _applyAlarmBorders(data.alarms);
    if (!$('alarmOverlay').classList.contains('hidden') &&
        $('tabAktiv').classList.contains('active')) {
      renderAlarms(data.alarms);
    }
  }
}

function _syncWartungHeight() {
  const inv  = document.getElementById('inverterCard');
  const wart = document.getElementById('wartungCard');
  if (!inv || !wart) return;
  if (window.innerWidth >= 1024) {
    wart.style.maxHeight = inv.offsetHeight + 'px';
  } else {
    wart.style.maxHeight = '';
  }
}
window.addEventListener('resize', _syncWartungHeight);

function connect() {
  setConnState('warn');
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen    = () => {
    setConnState('ok'); clearTimeout(reconnectTimer);
    if (histData.length === 0) {
      fetch('/api/history').then(r => r.json()).then(hist => {
        hist.forEach(e => {
          histData.push(e);
          const ce = { ts: e.ts };
          ['solar1','solar2','solar3','charger','alternator','wind'].forEach(k => {
            if (e[k] != null) ce[k] = e[k];
          });
          if (Object.keys(ce).length > 1) chargeHist.push(ce);
        });
        histData.sort((a, b) => a.ts - b.ts);
        recomputeDailyAhFromHist();
        if (!$('battOverlay').classList.contains('hidden')) renderCharts();
        reRenderChargePie();
      }).catch(() => {});
    }
  };
  ws.onmessage = ev => { try { handleData(JSON.parse(ev.data)); } catch(_) {} };
  ws.onclose = ws.onerror = () => {
    setConnState('off');
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 4000);
  };
}
