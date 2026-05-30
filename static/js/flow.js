// ── Energiefluss (Victron-Stil Schema) ────────────────────────────────────
// Kantenzustände:
//   .on              → Leistung bekannt + > 0 → animierte laufende Punkte
//   .static          → Strom fließt sicher, aber Menge unbekannt →
//                       farbige Strich-Punkt-Linie, kein Laufen
//   (weder on noch static) → kein Fluss / unbekannt

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

function _W(v) {
  if (v == null) return '--';
  const a = Math.abs(v);
  return a >= 1000 ? (v / 1000).toFixed(2) + ' kW' : Math.round(v) + ' W';
}
function _A(a) {
  if (a == null) return null;
  return (Math.abs(a) >= 10 ? Math.round(a) : a.toFixed(1)) + ' A';
}
function _dur(w) {
  return Math.max(0.3, Math.min(2.2, 400 / Math.max(20, Math.abs(w)))).toFixed(2);
}

function _setEdge(id, color, w, reverse = false, forceStatic = false) {
  const el = $(id);
  if (!el) return;

  if (w == null && !forceStatic) {
    // Keine Info — Linie unsichtbar
    el.classList.remove('on', 'static', 'flow-rev');
    el.style.stroke = '';
    el.style.animationDuration = '';
    return;
  }

  el.style.stroke = color;

  if (forceStatic || Math.abs(w ?? 0) < 1) {
    // Strom fließt sicher, Menge unbekannt → statische farbige Strichlinie
    el.classList.remove('on', 'flow-rev');
    el.classList.add('static');
    el.style.animationDuration = '';
  } else {
    // Leistung bekannt → animierte laufende Punkte
    el.classList.remove('static');
    el.classList.add('on');
    el.classList.toggle('flow-rev', reverse);
    el.style.animationDuration = _dur(w) + 's';
  }
}

function _setNode(id, text, color, ampText) {
  const el = $('fv-' + id), g = $('fg-' + id), sa = $('fsa-' + id);
  if (el) { el.textContent = text; el.style.fill = color || ''; }
  if (sa) { sa.textContent = ampText || ''; }
  if (g)  g.classList.toggle('dim', text === '--' || text === '—');
}

function updateFlow(data) {
  if ($('flowOverlay')?.classList.contains('hidden')) return;

  const bat = data.battery ?? {};
  const bms = data.bms ?? {};
  const C   = '#4a78c8'; // Einheitliche Linienfarbe

  // Shunt: negativ = Entladen, positiv = Laden (wie in battery.js)
  const shuntI = bat.current;
  const shuntW = bat.power;

  const s1w      = data.solar?.power, s2w = data.solar2?.power, s3w = data.solar3?.power;
  const chargerW = data.charger?.power;
  const orionW   = data.orion?.power;
  const altW     = data.alternator?.power;
  const invW     = data.inverter?.power;

  // DC-Lasten = BMS-Entladestrom × Spannung (plausibel: 0–400 A)
  const bmsDischarge = bms.current_discharge;
  const dcRawA  = (bmsDischarge != null && bmsDischarge >= 0 && bmsDischarge <= 400) ? bmsDischarge : null;
  const dcNetW  = (dcRawA != null && bat.voltage != null) ? dcRawA * bat.voltage : null;

  const hasGrid    = chargerW != null && chargerW > 5;
  const bordActive = invW > 10 || hasGrid;

  // ── Solar ─────────────────────────────────────────────────────────────
  _setNode('solar1', s1w != null ? _W(s1w) : '--', s1w > 0 ? '#eab308' : null, _A(data.solar?.current));
  _setNode('solar2', s2w != null ? _W(s2w) : '--', s2w > 0 ? '#eab308' : null, _A(data.solar2?.current));
  _setNode('solar3', s3w != null ? _W(s3w) : '--', s3w > 0 ? '#eab308' : null, _A(data.solar3?.current));

  // ── Landstrom / Ladegerät ─────────────────────────────────────────────
  _setNode('grid',    hasGrid ? 'aktiv' : '—');
  _setNode('charger', chargerW != null ? _W(chargerW) : '--', chargerW > 5 ? '#3b82f6' : null);

  // ── Inverter + AC-Lasten (= Inverterleistung) ─────────────────────────
  _setNode('inv',  invW != null ? _W(invW) : '--', invW > 10 ? '#a78bfa' : null);
  _setNode('bord', invW != null && invW > 0 ? _W(invW) : (bordActive ? '—' : '—'),
           invW > 10 ? '#a78bfa' : null);

  // ── Orion / Lichtmaschine / Starter ──────────────────────────────────
  _setNode('orion',   orionW  != null ? _W(orionW)  : '--', orionW  > 0 ? '#22d3ee' : null);
  _setNode('alt',     altW    != null ? _W(altW)    : '--');
  _setNode('starter', bat.starter_voltage != null ? bat.starter_voltage.toFixed(1) + ' V' : '--');

  // ── DC-Lasten ─────────────────────────────────────────────────────────
  _setNode('dcgrid', dcNetW != null ? _W(dcNetW) : '--',
           dcNetW > 10 ? '#f87171' : null,
           dcNetW != null ? _A(dcRawA) : null);

  // ── Batterie (Hauptknoten) ────────────────────────────────────────────
  const bv = $('fv-batt'), bs = $('fs-batt'), bg = $('fg-batt');
  if (bv) {
    bv.textContent = shuntW != null ? _W(shuntW) : '--';
    // negativ = Entladen → orange; positiv = Laden → grün
    bv.style.fill = shuntW == null ? '' : shuntW < 0 ? '#f97316' : shuntW > 0 ? '#22c55e' : '#eef4fb';
  }
  if (bs) {
    const parts = [];
    if (bat.soc     != null) parts.push(bat.soc + ' %');
    if (bat.voltage != null) parts.push(bat.voltage.toFixed(2) + ' V');
    if (shuntI      != null) parts.push(_A(shuntI));
    bs.textContent = parts.join('  ·  ');
  }
  if (bg) bg.classList.remove('dim');

  // ── Kanten (alle einheitlich dunkelblau) ─────────────────────────────
  _setEdge('fe-solar1',       C, s1w,      false);
  _setEdge('fe-solar2',       C, s2w,      false);
  _setEdge('fe-solar3',       C, s3w,      false);
  _setEdge('fe-charger',      C, chargerW, false);
  _setEdge('fe-gridcharger',  C, chargerW, false, hasGrid && chargerW == null);
  _setEdge('fe-gridbord',     C, null,     false, hasGrid);
  _setEdge('fe-orion',        C, orionW,   false);
  _setEdge('fe-starterorion', C, orionW,   false, altW > 5 && orionW == null);
  _setEdge('fe-altstarter',   C, altW,     false, altW == null && orionW > 5);
  _setEdge('fe-inv',          C, invW,     false);
  _setEdge('fe-invbord',      C, invW,     false, bordActive && invW == null);
  _setEdge('fe-dcgrid',       C, dcNetW,   false);
}
