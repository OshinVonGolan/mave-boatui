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
  // Der Verlauf spannt sich ueber den GANZEN Tank, von voll bis leer — nicht
  // ueber die Fuellung.
  //
  // Der Unterschied ist der Punkt: liegt er in der Fuellung, wandert die Farbe
  // mit dem Pegel, und derselbe Ton bedeutet einmal 80 % und einmal 20 %.
  // Ueber den Tank gespannt gehoert jede Hoehe fest zu einem Stand — man sieht
  // an der Farbe, wo man ist, nicht nur an der Kante.
  //
  // Gemacht wird das mit background-size: die Fuellung ist nur `--fuell` hoch,
  // ihr Hintergrundbild wird auf die volle Tankhoehe gestreckt und unten
  // verankert. Sichtbar ist dann genau der untere Ausschnitt.
  const farbe = tanksConfig[`tank${idx}`]?.color || 'var(--green)';
  fill.style.background =
    `linear-gradient(to top,
       color-mix(in srgb, ${farbe} 72%, #000) 0%,
       ${farbe} 55%,
       color-mix(in srgb, ${farbe} 78%, #fff) 100%)`;
  fill.style.backgroundRepeat = 'no-repeat';
  fill.style.backgroundPosition = 'bottom';
  // Null waere eine Division durch null; unter einem Prozent sieht ohnehin
  // niemand mehr einen Verlauf.
  fill.style.backgroundSize = `100% ${(100 / Math.max(clamped, 1) * 100).toFixed(2)}%`;

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
