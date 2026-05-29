// ── Energiefluss (Victron-Stil Flussdiagramm) ──────────────────────────────

// dir: 'in' = Quelle (Fluss zum Bus wenn W>0), 'out' = Verbraucher (Fluss vom Bus),
//      'batt' = Batterie (W>0 laden → vom Bus, W<0 entladen → zum Bus), 'none' = nur Wert
const FLOW_NODES = [
  { id: 'solar1',  dir: 'in',   color: '#eab308', get: d => d.solar?.power,      sub: d => d.solar?.voltage != null ? d.solar.voltage.toFixed(1) + ' V' : '' },
  { id: 'solar2',  dir: 'in',   color: '#f59e0b', get: d => d.solar2?.power },
  { id: 'solar3',  dir: 'in',   color: '#fb923c', get: d => d.solar3?.power },
  { id: 'alt',     dir: 'in',   color: '#06b6d4', get: d => d.alternator?.power },
  { id: 'shore',   dir: 'in',   color: '#3b82f6', get: d => d.shore?.power },
  { id: 'inv',     dir: 'out',  color: '#a78bfa', get: d => d.inverter?.power },
  { id: 'service', dir: 'batt', color: '#22c55e', get: d => d.battery?.power,     sub: d => d.battery?.soc != null ? d.battery.soc + ' %' : '' },
  { id: 'starter', dir: 'none', color: '#94a3b8', get: d => null,                 sub: d => d.battery?.starter_voltage != null ? d.battery.starter_voltage.toFixed(1) + ' V' : '' },
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
  return Math.abs(w) >= 1000 ? (w / 1000).toFixed(2) + ' kW' : Math.round(w) + ' W';
}

function updateFlow(data) {
  if ($('flowOverlay').classList.contains('hidden')) return;
  let hubIn = 0, hubOut = 0;

  FLOW_NODES.forEach(n => {
    const w      = n.get(data);
    const valEl  = $('fv-' + n.id), subEl = $('fs-' + n.id);
    const nodeEl = $('fn-' + n.id), lineEl = $('fl-' + n.id);
    const sub    = n.sub ? n.sub(data) : '';
    if (subEl) subEl.textContent = sub || '';

    const hasData = w != null || (n.dir === 'none' && sub);
    nodeEl.classList.toggle('dim', !hasData);

    if (w == null) {
      valEl.textContent = n.dir === 'none' ? '—' : '--';
      valEl.style.color = '';
      lineEl.classList.remove('flow-active', 'flow-rev');
      lineEl.style.stroke = '';
      return;
    }

    valEl.textContent = _flowFmtW(w);
    valEl.style.color = n.color;

    const active = Math.abs(w) > 1;
    lineEl.classList.toggle('flow-active', active);
    lineEl.style.stroke = active ? n.color : '';

    // Flussrichtung: Linien sind als "Knoten → Hub" gezeichnet.
    let towardHub = true;
    if (n.dir === 'in')   towardHub = w > 0;
    if (n.dir === 'out')  towardHub = false;
    if (n.dir === 'batt') towardHub = w < 0;   // entladen speist den Bus
    lineEl.classList.toggle('flow-rev', !towardHub);

    if (active) {
      if (n.dir === 'in'  && w > 0) hubIn  += w;
      if (n.dir === 'out' && w > 0) hubOut += w;
      if (n.dir === 'batt') { if (w < 0) hubIn += -w; else hubOut += w; }
    }
  });

  $('flowHubVal').textContent = _flowFmtW(Math.max(hubIn, hubOut));
}
