// ── Tanks ──────────────────────────────────────────────────────────────────

let tankShowLiters = false;
const tankLast = { 1: null, 2: null };

function toggleTanks() {
  tankShowLiters = !tankShowLiters;
  $('tankUnitLabel').textContent = tankShowLiters ? 'L · Tippen für Prozent' : '% · Tippen für Liter';
  renderTank(1, tankLast[1]);
  renderTank(2, tankLast[2]);
  // Das Wasserfeld der Statusleiste zeigt dieselbe Einheit — sonst haette ein
  // Tipp darauf die Kachel umgeschaltet und das Feld selbst nicht.
  if (typeof updateStatusBar === 'function' && typeof _lastData !== 'undefined' && _lastData) {
    updateStatusBar(_lastData);
  }
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
  fill.style.background = tanksConfig[`tank${idx}`]?.color || 'var(--green)';

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

// Ohne Messwerte wird die Kachel ausgeblendet. Die Rasterberechnung in
// display.js (_applyGrid) setzt aber bei jedem Neuaufbau — Fenstergroesse
// geaendert, Kachel-Einstellungen gespeichert — style.display JEDER sichtbaren
// Kachel zurueck. Die leere Tank-Kachel blitzte dadurch wieder auf und
// verschwand erst mit dem naechsten WebSocket-Frame. Deshalb merken wir uns die
// gewuenschte Sichtbarkeit und stellen sie sofort wieder her, sobald jemand das
// style-Attribut anfasst. Der Beobachter haengt an genau einem Element und
// meldet sich nur bei echten Attributaenderungen — auf dem Pi Zero kostet das
// nichts.
let _tankCardSichtbar = true;

function _applyTankCardVisibility() {
  const card = $('tankCard');
  if (!card) return;
  const soll = _tankCardSichtbar ? '' : 'none';
  if (card.style.display !== soll) card.style.display = soll;   // kein Endlos-Ping-Pong
}

(function _watchTankCard() {
  const card = $('tankCard');
  if (!card || typeof MutationObserver !== 'function') return;
  new MutationObserver(_applyTankCardVisibility)
    .observe(card, { attributes: true, attributeFilter: ['style'] });
})();

function updateTanks(tanks) {
  _tankCardSichtbar = tanks.tank1 != null || tanks.tank2 != null;
  _applyTankCardVisibility();
  renderTank(1, tanks.tank1);
  renderTank(2, tanks.tank2);
}
