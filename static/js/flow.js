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

/**
 * Fuellt eine Kachel: grosse Zahl plus bis zu ZWEI Zusatzzeilen.
 *
 * Die zweite Zeile kam mit dem 3x3-Raster dazu — die Kacheln sind gross genug,
 * und ohne sie muessten Angaben wie "Lader Storage" und "Wechselrichter aus"
 * in eine Zeile gequetscht werden, die im SVG nicht umbricht.
 */
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

  // ── Solar: alle Regler in EINER Kachel ────────────────────────────────
  // Wie bei Victron ("Solar yield") zaehlt die Kachel zusammen, was alle
  // Regler liefern; die Zusatzzeile nennt die Strings einzeln, damit die
  // vorbereiteten Regler 2 und 3 sichtbar bleiben.
  const solarSumme = [s1w, s2w, s3w].filter(v => v != null && v > 0)
                                    .reduce((a, v) => a + v, 0);
  const solarDa    = [s1w, s2w, s3w].some(v => v != null);
  // Zusatzzeile: nur die Regler nennen, die auch etwas melden. Die noch nicht
  // verbauten stehen als "vorbereitet" dahinter — "MPPT 1 0 W · 2 — · 3 —"
  // las sich wie eine Stoerung, obwohl da schlicht nichts angeschlossen ist.
  const _regler = [['1', s1w], ['2', s2w], ['3', s3w]];
  const _melden = _regler.filter(([, w]) => w != null).map(([n, w]) => `MPPT ${n} ${_W(w)}`);
  const _offen  = _regler.filter(([, w]) => w == null).map(([n]) => n);
  _setNode('solar1', solarDa ? _W(solarSumme) : '--', solarSumme > 0 ? '#eab308' : null,
           _sub(_melden.join(' · ') || null,
                _offen.length ? `${_offen.join(', ')} vorbereitet` : null),
           _sub(data.solar?.yield_today_wh != null
                  ? 'heute ' + Math.round(data.solar.yield_today_wh) + ' Wh' : null,
                s1w > 0 ? data.solar?.cs_label : null));

  // ── Landstrom ─────────────────────────────────────────────────────────
  _setNode('grid', hasGrid ? 'aktiv' : '—', hasGrid ? '#38bdf8' : null,
           _sub(data.inverter?.ac_voltage > 20
                ? Math.round(data.inverter.ac_voltage) + ' V~' : null));

  // ── Wandlerstufe: Lader UND Wechselrichter in einer Kachel ────────────
  // Beide sitzen zwischen AC und DC und laufen nie gleichzeitig. Die Kachel
  // zeigt, was gerade passiert; die Zusatzzeilen nennen beide Geraete, damit
  // keines verschwindet.
  const laedt    = chargerW != null && chargerW > 5;
  const invLaeuft = invW != null && invW > 10;
  // Grosse Zeile ist der ZUSTAND, nicht die Leistung — wie beim Vorbild, wo
  // dort "Absorption" steht. "Wechselrichten 430 W" lief bei 32 px schlicht
  // aus dem Kasten heraus; SVG-Text bricht nicht um und wird nicht beschnitten.
  _setNode('conv',
           laedt ? 'Laden' : invLaeuft ? 'Wechselrichten' : 'Aus',
           laedt ? '#3b82f6' : invLaeuft ? '#a78bfa' : null,
           _sub(laedt ? _W(chargerW) : invLaeuft ? _W(invW) : null,
                laedt ? data.charger?.cs_label : null,
                laedt ? _v2(data.charger?.dc_voltage) : null),
           _sub(laedt ? null : 'Lader ' + (data.charger?.cs_label ?? '—'),
                invLaeuft && data.inverter?.ac_voltage != null
                  ? data.inverter.ac_voltage.toFixed(0) + ' V~' : null));

  // ── AC-Lasten: nennen ihre Quelle selbst (frueher ein eigener Kasten) ──
  const aufLandstrom = hasGrid && !invLaeuft;
  _setNode('bord', invLaeuft ? _W(invW) : hasGrid ? 'am Netz' : '—',
           invLaeuft ? '#a78bfa' : hasGrid ? '#38bdf8' : null,
           _sub(aufLandstrom ? 'vom Landstrom' : invLaeuft ? 'vom Wechselrichter' : 'keine Quelle'),
           _sub(invLaeuft && data.inverter?.ac_current != null
                  ? data.inverter.ac_current.toFixed(1) + ' A~' : null));

  // ── DC-Lasten ─────────────────────────────────────────────────────────
  _setNode('dcgrid', dcNetW != null ? _W(dcNetW) : '--',
           dcNetW > 10 ? '#f87171' : null,
           dcNetW != null ? _A(dcRawA) : null);

  // ── Startbatteriekreis ────────────────────────────────────────────────
  _setNode('alt', altW != null ? _W(altW) : '--', altW > 0 ? '#38bdf8' : null,
           _sub(_A(data.alternator?.current), _v2(data.alternator?.voltage)));
  _setNode('starter', bat.starter_voltage != null ? bat.starter_voltage.toFixed(1) + ' V' : '--',
           null,
           _sub(bat.starter_max_voltage != null && bat.starter_max_voltage <= 20
                  ? 'max ' + bat.starter_max_voltage.toFixed(1) + ' V' : null));
  _setNode('orion', orionW != null ? _W(orionW) : '--', orionW > 0 ? '#22d3ee' : null,
           _sub(data.orion?.cs === 0 ? data.orion?.off_reason_label : data.orion?.cs_label),
           _sub(data.orion?.input_voltage != null
                  ? 'Ein ' + data.orion.input_voltage.toFixed(1) + ' V' : null));

  // ── Batterie (Mittelpunkt) ────────────────────────────────────────────
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
                     shuntI != null ? _A(shuntI) : null]
                    .filter(Boolean).join('  ·  '));
  _bset('fsc-batt', [bms.lowest_temp != null && bms.highest_temp != null
                       ? `${bms.lowest_temp.toFixed(0)}–${bms.highest_temp.toFixed(0)} °C` : null,
                     bms.highest_cell_v != null && bms.lowest_cell_v != null
                       ? Math.round((bms.highest_cell_v - bms.lowest_cell_v) * 1000) + ' mV' : null]
                    .filter(Boolean).join('  ·  '));
  if (bg) bg.classList.remove('dim');

  // ── Kanten ────────────────────────────────────────────────────────────
  // Grundsatz bleibt: eine Leitung wird nur gezeichnet, wenn dort auch Strom
  // fliesst. Ein Geraet, das aus ist, haengt an keiner Linie.
  _setEdge('fe-gridconv',  C, null, false, laedt);
  _setEdge('fe-convbord',  C, invLaeuft ? invW : null, false, aufLandstrom);
  // Wandler <-> Batterie: beim Laden fliesst es nach UNTEN in die Batterie,
  // beim Wechselrichten zieht der Wechselrichter nach OBEN aus ihr heraus.
  // Vorher war nur der Ladefall verdrahtet — am Anker mit laufendem
  // Wechselrichter fehlte die Leitung ganz, obwohl dort der meiste Strom floss.
  _setEdge('fe-convbatt',  C, laedt ? chargerW : invLaeuft ? invW : null, invLaeuft);
  _setEdge('fe-solarbatt', C, solarSumme > 0 ? solarSumme : null);
  _setEdge('fe-battdc',    C, dcNetW);

  // Ladekette Lichtmaschine -> Starter -> Orion -> Bordbatterie: nur zeigen,
  // wenn der Orion auch wirklich laedt. Steht er (Motor aus,
  // Remote-Abschaltung), fliesst nichts, und dann gehoert da keine Linie hin.
  const orionLaeuft = orionW != null && orionW > 1;
  _setEdge('fe-orionbatt',    C, orionW);
  _setEdge('fe-starterorion', C, null, false, orionLaeuft);
  _setEdge('fe-altstarter',   C, altW, false, orionLaeuft && altW == null);
}
