// ── Tanks ──────────────────────────────────────────────────────────────────

let tankShowLiters = false;
const tankLast = { 1: null, 2: null };

function toggleTanks() {
  tankShowLiters = !tankShowLiters;
  $('tankUnitLabel').textContent = tankShowLiters ? 'L · Tippen für Prozent' : '% · Tippen für Liter';
  renderTank(1, tankLast[1]);
  renderTank(2, tankLast[2]);
}

function renderTank(idx, pct) {
  tankLast[idx] = pct;
  const fill = $(`tank${idx}Fill`);
  const val  = $(`tank${idx}Val`);
  const unit = $(`tank${idx}Unit`);
  const sub  = $(`tank${idx}Sub`);

  if (pct == null) {
    fill.style.height = '0%';
    fill.style.background = 'var(--border)';
    val.textContent  = '--';
    unit.textContent = tankShowLiters ? 'L' : '%';
    sub.textContent  = '';
    return;
  }

  const clamped = Math.max(0, Math.min(100, pct));
  fill.style.height = clamped + '%';
  const customColor = tanksConfig[`tank${idx}`]?.color;
  fill.style.background = clamped < 20 ? 'var(--red)'
    : clamped < 50 ? (customColor || 'var(--yellow)')
    : (customColor || 'var(--green)');

  const cap = tanksConfig[`tank${idx}`]?.capacity_l ?? 100;

  if (tankShowLiters) {
    val.textContent  = Math.round(clamped / 100 * cap);
    unit.textContent = 'L';
    sub.textContent  = `${Math.round(clamped)} %`;
  } else {
    val.textContent  = Math.round(clamped);
    unit.textContent = '%';
    sub.textContent  = `${Math.round(clamped / 100 * cap)} L`;
  }
}

function updateTanks(tanks) {
  const hasAny = tanks.tank1 != null || tanks.tank2 != null;
  $('tankCard').style.display = hasAny ? '' : 'none';
  renderTank(1, tanks.tank1);
  renderTank(2, tanks.tank2);
}
