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

// Markiert den Topbar-Button der gerade offenen Seite mit blauem Rahmen.
// btnId=null entfernt die Markierung überall.
function _navActive(btnId) {
  document.querySelectorAll('.icon-btn.nav-active').forEach(b => b.classList.remove('nav-active'));
  if (btnId) $(btnId)?.classList.add('nav-active');
}

// ── Config (geladen aus /api/presets) ─────────────────────────────────────

let tanksConfig    = { tank1: { name: 'Tank 1', capacity_l: 200 }, tank2: { name: 'Tank 2', capacity_l: 120 } };
let devicesConfig  = {};
let batteriesConfig = { service_instance: 0, starter_instance: 1, primary_source: 'shunt' };
let wartungConfig  = { due_soon_days: 7 };
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

// ── Burger-Menü (Mobile) ───────────────────────────────────────────────────

function toggleBurger(e) {
  e?.stopPropagation();
  $('burgerMenu')?.classList.toggle('hidden');
}
function closeBurger() {
  $('burgerMenu')?.classList.add('hidden');
}
// Außerhalb klicken schließt das Menü
document.addEventListener('click', e => {
  const wrap = $('burgerWrap');
  if (wrap && !wrap.contains(e.target)) closeBurger();
});
