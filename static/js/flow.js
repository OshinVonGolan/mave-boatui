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

// Ladezustaende, die bedeuten "der Lader haengt am Netz" (gleiche Liste wie
// ws.js — sonst sagt die Startseite "Landstrom da" und der Fluss "keine Quelle").
const _NETZ_ZUSTAENDE = new Set(['Bulk', 'Absorption', 'Float', 'Storage',
  'Equalise', 'Starting', 'Auto-Equalise', 'Const VI', 'Ext. Control', 'External Control']);

function _setEdge(id, color, w, reverse = false, forceStatic = false) {
  const el = $(id);
  if (!el) return;

  // Eine Leitung verschwindet nicht mehr — sie bleibt als ruhige, durchgezogene
  // graue Linie stehen. So ist die Verkabelung immer ablesbar, und trotzdem
  // sieht man sofort, wo Strom laeuft: grau/durchgezogen = verbunden,
  // farbig/laufend = es fliesst.
  //
  // Regel fuer die Richtung: OHNE reverse laufen die Punkte in Zeichenrichtung
  // des d-Attributs. Alle Pfade sind in Flussrichtung gezeichnet, deshalb
  // braucht keine Kante mehr ein reverse.
  const fliesst = forceStatic || (w != null && Math.abs(w) >= 1);

  if (!fliesst) {
    el.classList.remove('on', 'static', 'flow-rev');
    el.classList.add('idle');
    el.style.stroke = '';
    el.style.animationDuration = '';
    return;
  }

  el.classList.remove('idle');
  el.style.stroke = color;

  if (forceStatic) {
    el.classList.remove('on', 'flow-rev');
    el.classList.add('static');
    el.style.animationDuration = '';
  } else {
    el.classList.remove('static');
    el.classList.add('on');
    el.classList.toggle('flow-rev', reverse);
    el.style.animationDuration = _dur(w) + 's';
  }
}

/**
 * Fuellt eine Kachel: grosse Zahl plus bis zu ZWEI Zusatzzeilen.
 *
 * Die zweite Zeile kam mit dem 3x3-Raster dazu — die Kacheln sind gross genug,
 * und ohne sie muessten Angaben wie "Lader Storage" und "Wechselrichter aus"
 * in eine Zeile gequetscht werden, die im SVG nicht umbricht.
 */
/** Solar-Regler als Liste: Bezeichnung linksbuendig, Wert rechtsbuendig. */
function _setListe(id, zeilen) {
  zeilen.forEach(([links, rechts], k) => {
    const l = $('fsl' + (k + 1) + '-' + id), r = $('fsv' + (k + 1) + '-' + id);
    if (l) l.textContent = links || '';
    if (r) r.textContent = rechts || '';
  });
}

/** Wie _setNode, aber mit beliebig vielen Zusatzzeilen (fsa/fsb/fsc). */
function _setNodeN(id, text, color, zeilen) {
  const el = $('fv-' + id), g = $('fg-' + id);
  if (el) { el.textContent = text; el.style.fill = color || ''; }
  ['a', 'b', 'c'].forEach((k, i) => {
    const e = $('fs' + k + '-' + id);
    if (e) e.textContent = (zeilen && zeilen[i]) || '';
  });
  if (g) g.classList.toggle('dim', text === '--' || text === '—');
}

function _setNode(id, text, color, sub1, sub2) {
  const el = $('fv-' + id), g = $('fg-' + id);
  const sa = $('fsa-' + id), sb = $('fsb-' + id);
  if (el) { el.textContent = text; el.style.fill = color || ''; }
  if (sa) { sa.textContent = sub1 || ''; }
  if (sb) { sb.textContent = sub2 || ''; }
  if (g)  g.classList.toggle('dim', text === '--' || text === '—' || text === 'Aus');
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


  // Zweite Zeile je Geraet: nur was wirklich vorliegt, mit Mittelpunkt getrennt.
  // Vorher trug ausser der Batterie kein Kasten eine Zusatzzeile — die Kaesten
  // waren gross und sagten wenig.
  // SVG-Text bricht nicht um und wird nicht abgeschnitten — zu lange Zeilen
  // liefen aus dem Kasten heraus (Orion: "Remote, Motor-Absch. · Ein 12.6 V").
  const _kurz = (t, max = 34) => t && t.length > max ? t.slice(0, max - 1) + '…' : t;
  const _sub = (...teile) =>
    _kurz(teile.filter(x => x != null && x !== '').join(' · ')) || null;
  const _v2 = v => v != null ? v.toFixed(2) + ' V' : null;

  // ── Frische: ein Geraet, dessen Werte alt sind, ist nicht beteiligt ───
  // Ohne diese Pruefung blieben Kachel und Leitung eingefroren stehen, wenn
  // man am Steg den Stecker zieht — der letzte Messwert haette ewig weiter
  // Landstrom behauptet.
  const _frisch = (g, s = 15) => (g && (g._age_s == null || g._age_s <= s)) ? g : null;
  const chgD = _frisch(data.charger), invD = _frisch(data.inverter);
  const solD = _frisch(data.solar),   oriD = _frisch(data.orion);

  // ── Eine Entscheidung je Geraet, fuer Kachel UND Leitung ─────────────
  // Vorher hatten Kachel (>5 W / >10 W) und Kante (>=1 W) verschiedene
  // Schwellen: eine laufende Linie hing an einer Kachel, die "Aus" sagte.
  const AN_W = 5;
  const _an  = (w) => w != null && Math.abs(w) > AN_W;

  // Landstrom NICHT an der Ladeleistung erkennen: am Steg mit voller Batterie
  // steht der IP43 in Storage/Float und liefert 0-3 W. Dieselbe Quelle wie auf
  // der Startseite (ws.js) verwenden, sonst widersprechen sich zwei Ansichten.
  const hasGrid = chgD != null && (chgD.active === true ||
    (chgD.active == null && _NETZ_ZUSTAENDE.has(chgD.cs_label ?? chgD.state)));
  const laedt      = _an(chargerW);
  const invLaeuft  = _an(invW);
  const orionLaeuft = _an(orionW);

  // Netzumschalter: EINE Quelle, hier einmal entschieden statt aus zwei
  // Schwellen nebenbei zu entstehen.
  const acQuelle = invLaeuft ? 'inv' : hasGrid ? 'netz' : null;

  // ── Solar: eine Kachel, die Regler als Liste ─────────────────────────
  const solarWerte = [s1w, s2w, s3w].filter(v => v != null);
  const solarSumme = solarWerte.reduce((a, v) => a + v, 0);
  const solarAn    = solarWerte.length > 0 && solarSumme > AN_W;
  _setNode('solar1', solarWerte.length ? _W(solarSumme) : '--',
           solarAn ? '#eab308' : null);
  // Regler ohne Wert heissen "vorbereitet", nicht "—": ein Strich neben einem
  // laufenden Regler liest sich wie ein ausgefallener Regler.
  _setListe('solar1', [
    ['Regler 1', s1w == null ? 'vorbereitet' : _W(s1w)],
    ['Regler 2', s2w == null ? 'vorbereitet' : _W(s2w)],
    ['Regler 3', s3w == null ? 'vorbereitet' : _W(s3w)],
  ]);

  // ── Landstrom ────────────────────────────────────────────────────────
  // Die frueher hier gezeigte Spannung war die AC-AUSGANGSspannung des
  // Wechselrichters — sie stand also genau dann auf 230 V, wenn KEIN Landstrom
  // anlag. Eine Messung der Landspannung gibt es an Bord nicht; stattdessen
  // der Ladezustand, der wirklich zum Landanschluss gehoert.
  _setNode('grid', hasGrid ? 'aktiv' : '—', hasGrid ? '#38bdf8' : null,
           _sub(hasGrid ? (chgD?.cs_label ?? null) : null));

  // ── Ladegeraet und Inverter: zwei Geraete, zwei Kacheln ──────────────
  _setNode('charger', laedt ? _W(chargerW) : 'Aus', laedt ? '#3b82f6' : null,
           _sub(chgD?.cs_label),
           _sub(laedt ? _v2(chgD?.dc_voltage) : null, laedt ? _A(chgD?.dc_current) : null));
  _setNode('inv', invLaeuft ? _W(invW) : 'Aus', invLaeuft ? '#a78bfa' : null,
           _sub(invD?.cs_label),
           _sub(invLaeuft && invD?.ac_voltage != null ? invD.ac_voltage.toFixed(0) + ' V~' : null,
                invLaeuft && invD?.ac_current != null ? invD.ac_current.toFixed(1) + ' A~' : null));

  // ── AC-Lasten: nennen ihre Quelle ────────────────────────────────────
  _setNode('bord', acQuelle === 'inv' ? _W(invW) : acQuelle === 'netz' ? 'am Netz' : '—',
           acQuelle === 'inv' ? '#a78bfa' : acQuelle === 'netz' ? '#38bdf8' : null,
           _sub(acQuelle === 'netz' ? 'vom Landstrom'
              : acQuelle === 'inv'  ? 'vom Wechselrichter' : 'keine Quelle'),
           _sub(acQuelle === 'inv' && invD?.ac_current != null
                  ? invD.ac_current.toFixed(1) + ' A~' : null));

  // ── DC-Lasten ────────────────────────────────────────────────────────
  // Der BMS-Entladestrom enthaelt AUCH den Strom, den der Wechselrichter
  // zieht. Ohne Abzug stand der Inverterverbrauch zweimal im Bild: einmal als
  // eigene Leitung, einmal noch einmal in den DC-Lasten.
  const INV_WIRKUNGSGRAD = 0.9;
  let dcA = null, dcW = null;
  if (dcRawA != null && bat.voltage) {
    const invDcA = (invLaeuft && invW != null) ? invW / INV_WIRKUNGSGRAD / bat.voltage : 0;
    dcA = Math.max(0, dcRawA - invDcA);
    dcW = dcA * bat.voltage;
  }
  _setNode('dcgrid', dcW != null ? _W(dcW) : '--', dcW > AN_W ? '#f87171' : null,
           _sub(dcA != null ? _A(dcA) : null));

  // ── Startbatteriekreis ───────────────────────────────────────────────
  // Die Lichtmaschinen-Kachel ist auf Wunsch des Eigners aus DIESER Ansicht
  // genommen. Die Verarbeitung dahinter (altW, data.alternator) bleibt
  // unangetastet, damit sie ohne JS-Aenderung wieder eingehaengt werden kann.
  // Die Kacheln fuer Lichtmaschine UND Starterbatterie sind auf Wunsch des
  // Eigners aus DIESER Ansicht genommen — im Energiefluss zaehlt, was in die
  // Bordbatterie speist, und das ist der Orion. Beide Werte werden weiterhin
  // ueberall sonst verarbeitet (Batterieseite, Alarme, ws.js), damit sie ohne
  // JS-Aenderung wieder eingehaengt werden koennen.
  _setNode('orion', orionLaeuft ? _W(orionW) : 'Aus', orionLaeuft ? '#22d3ee' : null,
           _sub(oriD?.cs === 0 ? oriD?.off_reason_label : oriD?.cs_label),
           _sub(oriD?.input_voltage != null
                  ? 'aus Starter ' + oriD.input_voltage.toFixed(1) + ' V' : null));

  // ── Batterie (Mittelpunkt) ───────────────────────────────────────────
  const bv = $('fv-batt'), bg = $('fg-batt');
  if (bv) {
    bv.textContent = bat.soc != null ? Math.round(bat.soc) + ' %' : '--';
    bv.style.fill = bat.soc == null ? ''
      : bat.soc >= 50 ? '#22c55e' : bat.soc >= 20 ? '#eab308' : '#ef4444';
  }
  const _bset = (id, txt) => { const e = $(id); if (e) e.textContent = txt || ''; };
  _bset('fsa-batt', [shuntW != null ? _W(shuntW) : null,
                     shuntW != null ? (shuntW < 0 ? 'entnimmt' : shuntW > 0 ? 'lädt' : 'ruht') : null]
                    .filter(Boolean).join('  ·  '));
  _bset('fsb-batt', [bat.voltage != null ? bat.voltage.toFixed(2) + ' V' : null,
                     shuntI != null ? _A(shuntI) : null].filter(Boolean).join('  ·  '));
  _bset('fsc-batt', [bms.lowest_temp != null && bms.highest_temp != null
                       ? `${bms.lowest_temp.toFixed(0)}–${bms.highest_temp.toFixed(0)} °C` : null,
                     bms.highest_cell_v != null && bms.lowest_cell_v != null
                       ? Math.round((bms.highest_cell_v - bms.lowest_cell_v) * 1000) + ' mV' : null]
                    .filter(Boolean).join('  ·  '));
  if (bg) bg.classList.remove('dim');

  // ── Kanten ───────────────────────────────────────────────────────────
  // Jede Kante haengt an DERSELBEN Entscheidung wie ihre Kachel. Alle Pfade
  // sind in Flussrichtung gezeichnet, deshalb kein reverse mehr.
  _setEdge('fe-gridcharger',  C, null, false, hasGrid && laedt);
  _setEdge('fe-chargerbatt',  C, laedt ? chargerW : null);
  _setEdge('fe-battinv',      C, invLaeuft ? invW : null);
  _setEdge('fe-invbord',      C, acQuelle === 'inv' ? invW : null);
  _setEdge('fe-gridbord',     C, null, false, acQuelle === 'netz');
  _setEdge('fe-solarbatt',    C, solarAn ? solarSumme : null);
  _setEdge('fe-battdc',       C, dcW);
  _setEdge('fe-orionbatt',    C, orionLaeuft ? orionW : null);
}
