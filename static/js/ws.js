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
  { key: 'alternator', label: 'Lichtmaschine', color: '#06b6d4', get: d => d.alternator?.power },
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
  $('chargeSourceLegend').innerHTML = active.map(s => {
    const pct = Math.round(s.watts / total * 100);
    return `<div class="charge-src-row">
      <div class="charge-src-dot" style="background:${s.color}"></div>
      <span class="charge-src-label">${s.label}</span>
      <span class="charge-src-val">${isAvg ? 'Ø ' : ''}${Math.round(s.watts)} W</span>
      <span class="charge-src-pct">${pct}%</span>
    </div>`;
  }).join('');
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

  if (data.solar?.power != null)      showSource('srcSolar', 'solarW', data.solar.power);
  if (data.alternator?.power != null) showSource('srcAlt',   'altW',   data.alternator.power);

  $('srcRow').classList.toggle('hidden', !anyVisible);

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
  if (data.bms) updateBms(data.bms);
  if (data.alarms != null) {
    updateAlarmBadge(data.unack_alarms ?? 0);
    if (!$('alarmOverlay').classList.contains('hidden') &&
        $('tabAktiv').classList.contains('active')) {
      renderAlarms(data.alarms);
    }
  }
}

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
