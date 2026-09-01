// ── Wetter (Lübeck + Lübecker Bucht, 3-Tage) ────────────────────────────────

let _wxData = null;

// Wetter-Symbol als Inline-SVG (Icon-System, keine Emojis).
// Ohne Wettercode bleibt es beim schlichten Punkt — sonst würde eine
// fehlende Angabe wie "bewölkt" aussehen.
function _wxIconHtml(code, storm, size) {
  if (storm) return icon('thunder', { size });
  if (code == null) return '<span style="opacity:.5">·</span>';
  return icon(weatherIcon(code), { size });
}

function _wxDayName(date, i) {
  if (i === 0) return 'Heute';
  try { return new Date(date).toLocaleDateString('de-DE', { weekday: 'short' }); }
  catch (_) { return ''; }
}

function _renderWeather() {
  const d = _wxData;
  if (!d) return;
  const land = d.land?.days || [];
  const sea  = d.sea?.days  || [];

  // Zeilen mit Icon: Icon und Wert mittig nebeneinander
  const rowStyle = 'display:flex;align-items:center;justify-content:center;gap:4px';

  // Normal: 3-Tage-Spalten (Land: Icon/Temp/Regen · See: Wind/Welle)
  const grid = $('wxGrid');
  if (grid) {
    grid.innerHTML = land.map((ld, i) => {
      const sd = sea[i] || {};
      const storm = ld.storm || sd.storm;
      return `<div class="wx-day">
        <div class="wx-day-name">${_wxDayName(ld.date, i)}</div>
        <div class="wx-icon">${_wxIconHtml(ld.wmo, storm, 30)}</div>
        <div class="wx-temp"><b>${ld.tmax != null ? Math.round(ld.tmax) : '--'}°</b> <span>${ld.tmin != null ? Math.round(ld.tmin) : '--'}°</span></div>
        <div class="wx-row" style="${rowStyle}">${icon('droplet', { size: 13 })} ${ld.pop != null ? ld.pop : '--'}%</div>
        <div class="wx-row" style="${rowStyle}">${icon('wind', { size: 13 })} ${sd.wind != null ? Math.round(sd.wind) : '--'} kn</div>
        <div class="wx-row" style="${rowStyle}">${icon('waves', { size: 13 })} ${sd.wave != null ? sd.wave.toFixed(1) : '--'} m</div>
      </div>`;
    }).join('');
  }

  // Half: heute kompakt
  const ld = land[0] || {}, sd = sea[0] || {};
  const storm = ld.storm || sd.storm;
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  const iconEl = $('wxHalfIcon');
  if (iconEl) iconEl.innerHTML = _wxIconHtml(ld.wmo, storm, 40);
  set('wxHalfTemp', (ld.tmax != null ? Math.round(ld.tmax) : '--') + '°');
  set('wxHalfWind', (sd.wind != null ? Math.round(sd.wind) : '--') + ' kn');
  set('wxHalfPop',  (ld.pop  != null ? ld.pop  : '--') + '%');
}

function fetchWeather() {
  // gibt das Promise zurueck: der gemeinsame Start wartet darauf
  return fetch('/api/weather')
    .then(r => r.ok ? r.json() : null)
    .then(d => { if (d) { _wxData = d; _renderWeather(); } })
    .catch(() => {});
}

// Alle 30 Minuten — pausiert automatisch, solange die Seite versteckt ist.
const _wxPoller = createPoller(fetchWeather, 30 * 60 * 1000);
_wxPoller.start();
