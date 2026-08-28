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
  // Ohne das +0 stand bei -0.04 A ein "-0.0 A" im Schema — sieht aus wie ein
  // Messfehler, ist aber nur eine gerundete Null.
  const v = Math.abs(a) >= 10 ? Math.round(a) : Number(a.toFixed(1)) + 0;
  return v + ' A';
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

  if (Math.abs(w ?? 0) < 1 && !forceStatic) {
    // Es fliesst nichts. Frueher blieb hier eine graue Leitung stehen; die
    // sah aus, als waere das Geraet beteiligt, obwohl es aus war.
    el.classList.remove('on', 'static', 'flow-rev');
    el.style.stroke = '';
    el.style.animationDuration = '';
    return;
  }

  if (forceStatic) {
    // Strom fliesst sicher, Menge unbekannt → farbige Strichlinie ohne Lauf
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

  // Zweite Zeile je Geraet: nur was wirklich vorliegt, mit Mittelpunkt getrennt.
  // Vorher trug ausser der Batterie kein Kasten eine Zusatzzeile — die Kaesten
  // waren gross und sagten wenig.
  // SVG-Text bricht nicht um und wird nicht abgeschnitten — zu lange Zeilen
  // liefen aus dem Kasten heraus (Orion: "Remote, Motor-Absch. · Ein 12.6 V").
  const _kurz = (t, max = 34) => t && t.length > max ? t.slice(0, max - 1) + '…' : t;
  const _sub = (...teile) =>
    _kurz(teile.filter(x => x != null && x !== '').join(' · ')) || null;
  const _v2 = v => v != null ? v.toFixed(2) + ' V' : null;

  // ── Solar ─────────────────────────────────────────────────────────────
  _setNode('solar1', s1w != null ? _W(s1w) : '--', s1w > 0 ? '#eab308' : null,
           _sub(s1w > 0 ? _A(data.solar?.dc_current) : null,
                s1w > 0 ? _v2(data.solar?.dc_voltage) : null,
                data.solar?.cs_label));
  _setNode('solar2', s2w != null ? _W(s2w) : '--', s2w > 0 ? '#eab308' : null,
           _sub(_A(data.solar2?.current)));
  _setNode('solar3', s3w != null ? _W(s3w) : '--', s3w > 0 ? '#eab308' : null,
           _sub(_A(data.solar3?.current)));

  // ── Landstrom / Ladegerät ─────────────────────────────────────────────
  _setNode('grid', hasGrid ? 'aktiv' : '—', null,
           _sub(data.inverter?.ac_voltage > 20
                ? Math.round(data.inverter.ac_voltage) + ' V~' : null));
  _setNode('charger', chargerW != null ? _W(chargerW) : '--',
           chargerW > 5 ? '#3b82f6' : null,
           _sub(data.charger?.cs_label, _v2(data.charger?.dc_voltage),
                _A(data.charger?.dc_current)));

  // ── Inverter + AC-Lasten (= Inverterleistung) ─────────────────────────
  _setNode('inv', invW != null ? _W(invW) : '--', invW > 10 ? '#a78bfa' : null,
           _sub(data.inverter?.cs_label,
                data.inverter?.ac_voltage != null
                  ? data.inverter.ac_voltage.toFixed(0) + ' V~' : null));
  _setNode('bord', invW != null && invW > 0 ? _W(invW) : '—',
           invW > 10 ? '#a78bfa' : null,
           _sub(data.inverter?.ac_current != null
                  ? data.inverter.ac_current.toFixed(1) + ' A~' : null));

  // ── Orion / Lichtmaschine / Starter ──────────────────────────────────
  _setNode('orion', orionW != null ? _W(orionW) : '--', orionW > 0 ? '#22d3ee' : null,
           _sub(data.orion?.cs === 0 ? data.orion?.off_reason_label : data.orion?.cs_label,
                data.orion?.input_voltage != null
                  ? 'Ein ' + data.orion.input_voltage.toFixed(1) + ' V' : null));
  _setNode('alt', altW != null ? _W(altW) : '--', null,
           _sub(_A(data.alternator?.current), _v2(data.alternator?.voltage)));
  _setNode('starter', bat.starter_voltage != null ? bat.starter_voltage.toFixed(1) + ' V' : '--',
           null,
           _sub(bat.starter_min_voltage != null && bat.starter_max_voltage != null
                  ? `${Math.max(0, bat.starter_min_voltage).toFixed(1)}–${bat.starter_max_voltage.toFixed(1)} V`
                  : null));

  // ── DC-Lasten ─────────────────────────────────────────────────────────
  _setNode('dcgrid', dcNetW != null ? _W(dcNetW) : '--',
           dcNetW > 10 ? '#f87171' : null,
           dcNetW != null ? _A(dcRawA) : null);

  // ── Batterie (Hauptknoten) ────────────────────────────────────────────
  const bv = $('fv-batt'), bg = $('fg-batt');
  if (bv) {
    bv.textContent = shuntW != null ? _W(shuntW) : '--';
    // negativ = Entladen → orange; positiv = Laden → grün
    bv.style.fill = shuntW == null ? '' : shuntW < 0 ? '#f97316' : shuntW > 0 ? '#22c55e' : '#eef4fb';
  }
  // Drei Zeilen statt einer langen: die Batterie ist der Mittelpunkt des
  // Schemas und hat Platz. Fehlende Werte lassen ihre Zeile einfach leer.
  const _bset = (id, txt) => { const e = $(id); if (e) e.textContent = txt || ''; };
  _bset('fsa-batt', [bat.soc != null ? Math.round(bat.soc) + ' %' : null,
                     bat.voltage != null ? bat.voltage.toFixed(2) + ' V' : null]
                    .filter(Boolean).join('  ·  '));
  _bset('fsb-batt', [shuntI != null ? _A(shuntI) : null,
                     bms.remaining_kwh != null
                       ? Math.round(bms.remaining_kwh * 1000).toLocaleString('de-DE') + ' Wh' : null]
                    .filter(Boolean).join('  ·  '));
  _bset('fsc-batt', [bms.highest_cell_v != null && bms.lowest_cell_v != null
                       ? Math.round((bms.highest_cell_v - bms.lowest_cell_v) * 1000) + ' mV Zelldiff.' : null,
                     bms.lowest_temp != null && bms.highest_temp != null
                       ? `${bms.lowest_temp.toFixed(0)}–${bms.highest_temp.toFixed(0)} °C` : null]
                    .filter(Boolean).join('  ·  '));
  if (bg) bg.classList.remove('dim');

  // ── Kanten ────────────────────────────────────────────────────────────
  // Grundsatz: eine Leitung wird nur gezeichnet, wenn dort auch Strom fliesst.
  // Ein Geraet, das aus ist, haengt an keiner Linie.
  _setEdge('fe-solar1',  C, s1w);
  _setEdge('fe-solar2',  C, s2w);
  _setEdge('fe-solar3',  C, s3w);
  _setEdge('fe-charger', C, chargerW);
  _setEdge('fe-inv',     C, invW);
  _setEdge('fe-dcgrid',  C, dcNetW);

  // Landstrom -> Ladegeraet: nur wenn das Ladegeraet wirklich zieht.
  _setEdge('fe-gridcharger', C, chargerW, false, hasGrid && chargerW == null);

  // AC-Lasten haengen an einem NETZUMSCHALTER: entweder Landstrom ODER
  // Inverter, nie beides. Der Umschalter legt sie auf den Inverter, wenn
  // Landstrom fehlt und der Inverter laeuft. Vorher lief dauerhaft eine Linie
  // vom Landstrom zu den AC-Lasten, auch wenn der Inverter versorgt haette.
  const invLaeuft   = invW != null && invW > 10;
  const aufLandstrom = hasGrid && !invLaeuft;
  _setEdge('fe-gridbord', C, null, false, aufLandstrom);
  _setEdge('fe-invbord',  C, invW, false, invLaeuft && invW == null);

  // Ladekette Lichtmaschine -> Starter -> Orion -> Batterie: nur zeigen, wenn
  // der Orion auch wirklich laedt. Steht er (Motor aus, Remote-Abschaltung),
  // fliesst nichts, und dann gehoert da auch keine Linie hin.
  const orionLaeuft = orionW != null && orionW > 1;
  _setEdge('fe-orion',        C, orionW);
  _setEdge('fe-starterorion', C, null, false, orionLaeuft);
  _setEdge('fe-altstarter',   C, altW, false, orionLaeuft && altW == null);
}
