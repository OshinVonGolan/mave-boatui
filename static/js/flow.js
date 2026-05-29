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

  const num = v => (v == null ? 0 : v);
  const bat = data.battery ?? {};
  const bms = data.bms ?? {};

  // ── Shunt-Nettostrom ──────────────────────────────────────────────────
  // positiv = Batterie entladen (Netz zieht aus Batterie)
  // negativ = Batterie lädt (Quellen laden Batterie)
  const shuntI = bat.current;        // A, Shunt-Messung
  const shuntW = bat.power;          // W, direkt vom Shunt

  // Vorzeichen-Konvention (wie in battery.js bestätigt):
  // bat.power / bat.current > 0  →  Laden (grün)
  // bat.power / bat.current < 0  →  Entladen (orange)

  // BMS misst:
  //   current_charge    = Strom INS Batterie rein (A, immer positiv)
  //   current_discharge = Strom AUS Batterie raus ins DC-Netz (A, immer positiv)
  // Erwartung: bmsCharge - bmsDischarge ≈ shuntI (gleiche Vorzeichen-Konvention)
  const bmsCharge    = bms.current_charge;
  const bmsDischarge = bms.current_discharge;
  const bmsHasData   = bmsCharge != null && bmsDischarge != null;

  // Bekannte externe Quellen (Solar, Ladegerät, Orion)
  const s1w = data.solar?.power, s2w = data.solar2?.power, s3w = data.solar3?.power;
  const chargerW = data.charger?.power;
  const orionW   = data.orion?.power;
  const hasSourceData = s1w != null || s2w != null || s3w != null
                     || chargerW != null || orionW != null;
  const sourcesIn = num(s1w) + num(s2w) + num(s3w) + num(chargerW) + num(orionW);
  const invW  = data.inverter?.power;

  let dcNetW = null;

  if (bmsHasData && bat.voltage != null) {
    // BMS-Pfad: battery_discharge ist gemessen → direkt DC-Last berechnen
    // Shunt-Kalibrierung: wenn BMS-Nettostrom > 25 % vom Shunt abweicht → skalieren
    // Scale wird auf [0.5 … 2.0] begrenzt um Sprünge bei bmsNet ≈ 0 zu verhindern
    let discA = bmsDischarge;
    if (shuntI != null && Math.abs(shuntI) > 0.5) {
      const bmsNet = bmsCharge - bmsDischarge;  // BMS-Nettostrom (+ = laden)
      const diff   = Math.abs(bmsNet - num(shuntI));
      if (diff > Math.abs(shuntI) * 0.25 && Math.abs(bmsNet) > 0.2) {
        const rawScale = num(shuntI) / bmsNet;
        const scale    = Math.max(0.5, Math.min(2.0, rawScale)); // KEIN explodieren
        discA = bmsDischarge * Math.abs(scale);
      }
    }
    // DC-Last = Quellen + Batterie-Entladung - Inverter
    dcNetW = Math.max(0, sourcesIn + discA * bat.voltage - num(invW));

  } else if (hasSourceData && shuntW != null) {
    // Kein BMS, aber Quellen bekannt:
    // Energiebilanz: sourcesIn - shuntW (pos=laden) - invW = DC-Last
    dcNetW = Math.max(0, sourcesIn - shuntW - num(invW));

  }
  // Wenn weder BMS noch Quelldaten: kein Wert anzeigen (würde nur Shunt wiedergeben)

  // ── Landstrom-Präsenz aus Ladegerät ableiten ──────────────────────────
  const hasGrid = chargerW != null && chargerW > 5;

  // ── Solar ─────────────────────────────────────────────────────────────
  _setNode('solar1', s1w != null ? _W(s1w) : '--', s1w > 0 ? '#eab308' : null, _A(data.solar?.current));
  _setNode('solar2', s2w != null ? _W(s2w) : '--', s2w > 0 ? '#eab308' : null, _A(data.solar2?.current));
  _setNode('solar3', s3w != null ? _W(s3w) : '--', s3w > 0 ? '#eab308' : null, _A(data.solar3?.current));

  // ── Ladegerät / Landstrom ─────────────────────────────────────────────
  _setNode('charger', chargerW != null ? _W(chargerW) : '--', chargerW > 5 ? '#3b82f6' : null);
  _setNode('grid', hasGrid ? 'aktiv' : '—');

  // ── Orion XS (Lichtmaschine → Batterie) ──────────────────────────────
  _setNode('orion', orionW != null ? _W(orionW) : '--', orionW > 0 ? '#22d3ee' : null);
  const altW = data.alternator?.power;
  _setNode('alt', altW != null ? _W(altW) : '--');
  // Starter zeigt Spannung
  const startV = bat.starter_voltage;
  _setNode('starter', startV != null ? startV.toFixed(1) + ' V' : '--');

  // ── Inverter ──────────────────────────────────────────────────────────
  _setNode('inv', invW != null ? _W(invW) : '--', invW > 10 ? '#a78bfa' : null);

  // ── Bordnetz (230V) ───────────────────────────────────────────────────
  const bordActive = invW > 10 || hasGrid;
  _setNode('bord', bordActive ? 'aktiv' : '—');

  // ── DC-Verbraucher ────────────────────────────────────────────────────
  const g = $('fg-dcgrid');
  if (dcNetW != null) {
    _setNode('dcgrid', _W(dcNetW), dcNetW > 10 ? '#f87171' : null);
    if (g) g.classList.remove('dim');
  } else {
    _setNode('dcgrid', '--');
    if (g) g.classList.add('dim');
  }

  // ── Batterie (zentral) ───────────────────────────────────────────────
  const bv = $('fv-batt'), bs = $('fs-batt'), bg = $('fg-batt');
  if (bv) {
    bv.textContent = shuntW != null ? _W(shuntW) : '--';
    bv.style.fill = shuntW == null ? '' : shuntW > 0 ? '#f97316' : shuntW < 0 ? '#22c55e' : '#eef4fb';
  }
  if (bs) {
    const parts = [];
    if (bat.soc  != null) parts.push(bat.soc + ' %');
    if (bat.voltage != null) parts.push(bat.voltage.toFixed(2) + ' V');
    if (shuntI  != null) parts.push(_A(shuntI));
    bs.textContent = parts.join('  ·  ');
  }
  if (bg) bg.classList.remove('dim');

  // ── Kanten setzen ────────────────────────────────────────────────────
  _setEdge('fe-solar1',      '#eab308', s1w, false);
  _setEdge('fe-solar2',      '#eab308', s2w, false);
  _setEdge('fe-solar3',      '#f59e0b', s3w, false);
  _setEdge('fe-charger',     '#3b82f6', chargerW, false);
  // Ladegerät→Batterie: wenn Ladegerät läuft aber ohne kWh-Wert → static
  _setEdge('fe-gridcharger', '#3b82f6', chargerW, false, hasGrid && chargerW == null);
  // Landstrom→Bordnetz: wenn Landstrom aktiv → static (Wert unbekannt)
  _setEdge('fe-gridbord',    '#3b82f6', null, false, hasGrid);
  _setEdge('fe-orion',       '#22d3ee', orionW, false);
  _setEdge('fe-starterorion','#22d3ee', orionW, false, altW > 5 && orionW == null);
  _setEdge('fe-altstarter',  '#06b6d4', altW,   false, altW == null && orionW > 5);
  _setEdge('fe-inv',         '#a78bfa', invW, false);
  _setEdge('fe-invbord',     '#a78bfa', invW, false, bordActive && invW == null);
  _setEdge('fe-dcgrid',      '#f87171', dcNetW, false);
}
