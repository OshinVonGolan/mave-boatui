// ── Energiefluss (Schema im Victron-Stil) ──────────────────────────────────
// Verbindungslinien sind als Quelle→Ziel gezeichnet. Fließt Leistung in
// Pfadrichtung, animieren die Punkte vorwärts; bei Gegenrichtung rückwärts.
// Punkt-Geschwindigkeit ∝ Leistung (mehr Watt = schnellere Punkte).

// Knoten: Wert-Anzeige + ob aktiv (Daten vorhanden)
const FLOW_NODES = [
  { id: 'grid',    get: d => null },                         // Landstrom (noch keine Messung)
  { id: 'bord',    get: d => null },                         // 230V Bordnetz
  { id: 'inv',     get: d => d.inverter?.power,   unit: 'W' },
  { id: 'charger', get: d => d.charger?.power,    unit: 'W' },
  { id: 'solar1',  get: d => d.solar?.power,      unit: 'W' },
  { id: 'solar2',  get: d => d.solar2?.power,     unit: 'W' },
  { id: 'solar3',  get: d => d.solar3?.power,     unit: 'W' },
  { id: 'orion',   get: d => d.orion?.power,      unit: 'W' },
  { id: 'dcgrid',  get: d => d.dc_grid?.power,    unit: 'W' },
  { id: 'alt',     get: d => d.alternator?.power, unit: 'W' },
  { id: 'starter', get: d => d.battery?.starter_voltage, unit: 'V' },
];

// Kanten: Leistungsquelle + Farbe. Positiv = Fluss in Pfadrichtung.
const FLOW_EDGES = [
  { id: 'fe-solar1',       get: d => d.solar?.power,      color: '#eab308' },
  { id: 'fe-solar2',       get: d => d.solar2?.power,     color: '#f59e0b' },
  { id: 'fe-solar3',       get: d => d.solar3?.power,     color: '#fb923c' },
  { id: 'fe-charger',      get: d => d.charger?.power,    color: '#3b82f6' },
  { id: 'fe-gridcharger',  get: d => d.charger?.power,    color: '#3b82f6' },
  { id: 'fe-gridbord',     get: d => d.shore?.power,      color: '#3b82f6' },
  { id: 'fe-inv',          get: d => d.inverter?.power,   color: '#a78bfa' },
  { id: 'fe-invbord',      get: d => d.inverter?.power,   color: '#a78bfa' },
  { id: 'fe-altstarter',   get: d => d.alternator?.power, color: '#06b6d4' },
  { id: 'fe-starterorion', get: d => d.orion?.power,      color: '#22d3ee' },
  { id: 'fe-orion',        get: d => d.orion?.power,      color: '#22d3ee' },
  { id: 'fe-dcgrid',       get: d => d.dc_grid?.power,    color: '#f87171' },
];

function openFlow() {
  _closeAllOverlays();
  history.pushState({ overlay: 'flow' }, '', '#flow');
  $('flowOverlay').classList.remove('hidden');
  if (_lastData) updateFlow(_lastData);
}

function closeFlow() {
  $('flowOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

function _flowFmtW(w) {
  if (w == null) return '--';
  return Math.abs(w) >= 1000 ? (w / 1000).toFixed(2) + ' kW' : Math.round(w) + ' W';
}

// Punkt-Dauer aus Leistung: viel Leistung → kurze Dauer → schnelle Punkte
function _flowDur(w) {
  const a = Math.abs(w);
  return Math.max(0.35, Math.min(2.4, 400 / Math.max(20, a))).toFixed(2);
}

function updateFlow(data) {
  if ($('flowOverlay').classList.contains('hidden')) return;

  // Knoten
  FLOW_NODES.forEach(n => {
    const valEl = $('fv-' + n.id), g = $('fg-' + n.id);
    const w = n.get(data);
    if (n.id === 'batt') return;                       // Batterie separat
    const active = w != null;
    if (g) g.classList.toggle('dim', !active);
    if (!valEl) return;
    if (!active) { valEl.textContent = (n.id === 'grid' || n.id === 'bord') ? '—' : '--'; return; }
    valEl.textContent = n.unit === 'V' ? w.toFixed(1) + ' V' : _flowFmtW(w);
  });

  // Batterie (immer aktiv): Netto-Leistung + SOC/Spannung
  const bp = data.battery?.power;
  const bv = $('fv-batt'), bs = $('fs-batt'), bg = $('fg-batt');
  if (bv) {
    bv.textContent = bp != null ? _flowFmtW(bp) : '--';
    bv.style.fill  = bp == null ? '' : bp > 0 ? 'var(--green)' : bp < 0 ? 'var(--orange)' : '';
  }
  if (bs) {
    const soc = data.battery?.soc, volt = data.battery?.voltage;
    bs.textContent = [soc != null ? soc + ' %' : null, volt != null ? volt.toFixed(2) + ' V' : null]
      .filter(Boolean).join('  ·  ');
  }
  if (bg) bg.classList.remove('dim');

  // DC-Verbraucher aus Energiebilanz herleiten:
  //   Quellen in den Bus − Inverter − Batterie-Laden = DC-Last
  const num = v => (v == null ? 0 : v);
  const sourcesIn = num(data.solar?.power) + num(data.solar2?.power) + num(data.solar3?.power)
                  + num(data.charger?.power) + num(data.orion?.power);
  const invW = num(data.inverter?.power);
  let dcLoad = null;
  if (bp != null) dcLoad = Math.max(0, sourcesIn - bp - invW);
  const dcValEl = $('fv-dcgrid'), dcG = $('fg-dcgrid');
  if (dcValEl) dcValEl.textContent = dcLoad != null ? _flowFmtW(dcLoad) : '--';
  if (dcG) dcG.classList.toggle('dim', dcLoad == null);

  // Kanten
  FLOW_EDGES.forEach(e => {
    const el = $(e.id);
    if (!el) return;
    const w = e.id === 'fe-dcgrid' ? dcLoad : e.get(data);
    const active = w != null && Math.abs(w) > 1;
    el.classList.toggle('on', active);
    if (!active) { el.style.stroke = ''; el.style.animationDuration = ''; el.classList.remove('flow-rev'); return; }
    el.style.stroke = e.color;
    el.style.animationDuration = _flowDur(w) + 's';
    el.classList.toggle('flow-rev', w < 0);
  });
}
