// ── Hilfsfunktionen ────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);
const fmt  = (v, d=1) => v == null ? '--' : Number(v).toFixed(d);
const fmtV = v => v == null ? '--' : Number(v).toFixed(2);

function colorClass(v, greenMin, yellowMin) {
  if (v == null) return '';
  return v >= greenMin ? 'val-green' : v >= yellowMin ? 'val-yellow' : 'val-red';
}

function timeSince(s) {
  if (s == null) return '--';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h > 48) return `${Math.floor(h/24)} T`;
  if (h > 0)  return `${h} h`;
  return `${m} min`;
}

// ── Config (geladen aus /api/presets) ─────────────────────────────────────

let tanksConfig    = { tank1: { name: 'Tank 1', capacity_l: 200 }, tank2: { name: 'Tank 2', capacity_l: 120 } };
let devicesConfig  = {};
let batteriesConfig = { service_instance: 0, starter_instance: 1, primary_source: 'shunt' };
let state = { lights: { channels: Array(9).fill(0) } };

// ── Clock ──────────────────────────────────────────────────────────────────

function updateClock() {
  const el = $('topbarClock');
  if (!el) return;
  const d = new Date();
  el.textContent = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}
updateClock();
setInterval(updateClock, 10000);
