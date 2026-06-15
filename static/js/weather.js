// ── Wetter (Lübeck + Lübecker Bucht, 3-Tage) ────────────────────────────────

let _wxData = null;

function _wxIcon(c) {
  if (c == null) return '·';
  if (c === 0) return '☀️';
  if (c <= 2)  return '🌤️';
  if (c === 3) return '☁️';
  if (c === 45 || c === 48) return '🌫️';
  if (c <= 57) return '🌦️';
  if (c <= 67) return '🌧️';
  if (c <= 77) return '🌨️';
  if (c <= 82) return '🌧️';
  if (c <= 86) return '🌨️';
  if (c >= 95) return '⛈️';
  return '·';
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

  // Normal: 3-Tage-Spalten (Land: Icon/Temp/Regen · See: Wind/Welle)
  const grid = $('wxGrid');
  if (grid) {
    grid.innerHTML = land.map((ld, i) => {
      const sd = sea[i] || {};
      const storm = ld.storm || sd.storm;
      return `<div class="wx-day">
        <div class="wx-day-name">${_wxDayName(ld.date, i)}</div>
        <div class="wx-icon">${storm ? '⛈️' : _wxIcon(ld.wmo)}</div>
        <div class="wx-temp"><b>${ld.tmax != null ? Math.round(ld.tmax) : '--'}°</b> <span>${ld.tmin != null ? Math.round(ld.tmin) : '--'}°</span></div>
        <div class="wx-row">💧 ${ld.pop != null ? ld.pop : '--'}%</div>
        <div class="wx-row">🌬️ ${sd.wind != null ? Math.round(sd.wind) : '--'} kn</div>
        <div class="wx-row">🌊 ${sd.wave != null ? sd.wave.toFixed(1) : '--'} m</div>
      </div>`;
    }).join('');
  }

  // Half: heute kompakt
  const ld = land[0] || {}, sd = sea[0] || {};
  const storm = ld.storm || sd.storm;
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  set('wxHalfIcon', storm ? '⛈️' : _wxIcon(ld.wmo));
  set('wxHalfTemp', (ld.tmax != null ? Math.round(ld.tmax) : '--') + '°');
  set('wxHalfWind', (sd.wind != null ? Math.round(sd.wind) : '--') + ' kn');
  set('wxHalfPop',  (ld.pop  != null ? ld.pop  : '--') + '%');
}

function fetchWeather() {
  fetch('/api/weather')
    .then(r => r.ok ? r.json() : null)
    .then(d => { if (d) { _wxData = d; _renderWeather(); } })
    .catch(() => {});
}

fetchWeather();
setInterval(fetchWeather, 30 * 60 * 1000);
