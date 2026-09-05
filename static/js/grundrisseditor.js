// ── Grundriss zeichnen ──────────────────────────────────────────────────────
//
// Der Grundriss war 250 Zeilen SVG in index.html — für DIESES Boot gezeichnet
// und für kein anderes. Seit er als Daten unter /api/grundriss liegt, fehlte
// nur noch der Weg, ihn ohne Editor und ohne Entwickler zu ändern. Das ist
// dieser hier.
//
// Zwei Dinge bewusst NICHT:
//
//   * Keine Seitenansicht. Übereinanderliegende Fächer zeichnet man
//     nebeneinander ein — ein Stauplan beantwortet "wo liegt das Ding", und
//     dafür genügt die Draufsicht. Eine zweite Ansicht verdoppelt die Arbeit
//     beim Zeichnen und halbiert die Übersicht beim Suchen.
//   * Keine zehn fertigen Bootszeichnungen. Kein Boot sieht aus wie eine
//     Zeichnung von einem anderen Boot. Stattdessen eine Handvoll RUMPFFORMEN
//     mit Reglern — Länge, Breite, wie spitz der Bug, wie breit das Heck.
//     Damit kommt man jedem Rumpf nahe genug, um Räume einzuzeichnen, und
//     genau dafür ist der Riss da.

const _GE_RASTER = 2;           // Fangraster in Zeichnungseinheiten
const _GE_VERLAUF_MAX = 40;     // so viele Schritte lassen sich zurücknehmen
const _GE_GRIFF_R = 5;          // Radius der Anfasser, in Zeichnungseinheiten

let _geRiss     = null;         // Arbeitskopie, erst beim Speichern echt
let _geWerkzeug = 'auswahl';    // auswahl | rechteck | vieleck | vorlage
let _geAuswahl  = null;         // id des gewählten Raums
let _geVieleck  = null;         // Punkte, die gerade gesetzt werden
let _geVerlauf  = [];
let _geZiehen   = null;         // {art, id, ...} während einer Bewegung
let _geSchmutzig = false;       // ungespeicherte Änderungen
let _geBildUrl  = null;         // Adresse der Planvorlage, wenn eine da ist
let _geStauzahl = null;         // {raumId: Anzahl} — was im Stauplan drinliegt

const _GE_FARBEN = ['#f59e0b', '#fbbf24', '#60a5fa', '#38bdf8', '#4ade80',
                    '#34d399', '#f472b6', '#a78bfa', '#fb923c', '#94a3b8'];

// ── Öffnen und Schließen ───────────────────────────────────────────────────

function openGrundrissEditor() {
  _closeAllOverlays();
  history.pushState({ overlay: 'grundriss' }, '', '#grundriss');
  _geAnzeigen();
}

function _geAnzeigen() {
  $('grEditorOverlay').classList.remove('hidden');
  // Arbeitskopie: erst Speichern macht die Änderung echt. Ohne das wäre jeder
  // Fehlgriff sofort im Stauplan und auf der Geräteseite zu sehen.
  _geRiss = JSON.parse(JSON.stringify(GRUNDRISS || {}));
  if (!Array.isArray(_geRiss.raeume)) _geRiss.raeume = [];
  if (!Array.isArray(_geRiss.hintergrund)) _geRiss.hintergrund = [];
  if (!_geRiss.ansicht) _geRiss.ansicht = { w: 200, h: 680 };
  _geVerlauf = [];
  _geAuswahl = null;
  _geVieleck = null;
  _geSchmutzig = false;
  _geBinden();
  _geVorlagePruefen();
  _geStauplanZaehlen();
  _geWerkzeugSetzen('auswahl');
  _geMasseFuellen();
  _geZeichnen();
}

function closeGrundrissEditor() {
  if (_geSchmutzig && !confirm('Der Grundriss ist geändert und noch nicht gespeichert. Verwerfen?')) return;
  $('grEditorOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

// ── Zustand ────────────────────────────────────────────────────────────────

/** Vor jeder Änderung: den Stand für "Zurück" wegschreiben. */
function _geMerken() {
  _geVerlauf.push(JSON.stringify(_geRiss));
  if (_geVerlauf.length > _GE_VERLAUF_MAX) _geVerlauf.shift();
  _geSchmutzig = true;
  _geKnoepfe();
}

function grundrissZurueck() {
  const alt = _geVerlauf.pop();
  if (!alt) return;
  _geRiss = JSON.parse(alt);
  _geAuswahl = null;
  _geVieleck = null;
  _geZeichnen();
  _geKnoepfe();
}

function _geKnoepfe() {
  const zurueck = $('geZurueck');
  if (zurueck) zurueck.disabled = !_geVerlauf.length;
  const speichern = $('geSpeichern');
  if (speichern) speichern.disabled = !_geSchmutzig;
}

function _geWerkzeugSetzen(name) {
  _geWerkzeug = name;
  _geVieleck = null;
  document.querySelectorAll('#geWerkzeuge [data-werkzeug]').forEach(b =>
    b.classList.toggle('aktiv', b.dataset.werkzeug === name));
  const svg = $('geSvg');
  if (svg) svg.style.cursor = name === 'auswahl' ? 'default' : 'crosshair';
  _geZeichnen();
}

// ── Zeichnen ───────────────────────────────────────────────────────────────

const _GE_NS = 'http://www.w3.org/2000/svg';

function _geEl(tag, attrs) {
  const el = document.createElementNS(_GE_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v != null) el.setAttribute(k, v);
  }
  return el;
}

function _geRaum(id) {
  return (_geRiss.raeume || []).find(r => r.id === id) || null;
}

function _geKasten(form) {
  if (form.t === 'vieleck') {
    const xs = form.punkte.map(p => p[0]), ys = form.punkte.map(p => p[1]);
    return { x: Math.min(...xs), y: Math.min(...ys),
             w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
  }
  return { x: form.x, y: form.y, w: form.w, h: form.h };
}

function _geZeichnen() {
  const svg = $('geSvg');
  if (!svg || !_geRiss) return;
  const w = _geRiss.ansicht.w, h = _geRiss.ansicht.h;
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.textContent = '';

  // Raster als Orientierung. Es ist auch das Fangraster — was man sieht, ist
  // das, woran der Finger einrastet.
  const defs = _geEl('defs', {});
  const raster = _geEl('pattern', { id: 'geRaster', width: 10, height: 10,
                                    patternUnits: 'userSpaceOnUse' });
  raster.appendChild(_geEl('path', { d: 'M10,0 L0,0 0,10', fill: 'none',
                                     stroke: 'rgba(148,163,184,.18)', 'stroke-width': .5 }));
  defs.appendChild(raster);
  svg.appendChild(defs);
  svg.appendChild(_geEl('rect', { x: 0, y: 0, width: w, height: h, fill: 'url(#geRaster)' }));

  // Die Planvorlage liegt UNTER allem: darüber wird gezeichnet, und was man
  // zeichnet, muss sichtbar bleiben.
  const bild = _geRiss.bild;
  if (_geBildUrl && bild && bild.w > 0) {
    const el = _geEl('image', { x: bild.x, y: bild.y, width: bild.w, height: bild.h,
                                opacity: bild.deckkraft,
                                preserveAspectRatio: 'none' });
    el.setAttributeNS('http://www.w3.org/1999/xlink', 'href', _geBildUrl);
    el.setAttribute('href', _geBildUrl);
    el.dataset.vorlage = '1';
    if (_geWerkzeug === 'vorlage') el.style.cursor = 'move';
    else el.style.pointerEvents = 'none';
    svg.appendChild(el);
  }

  if (_geRiss.rumpf) {
    // Beim Ausrichten der Vorlage darf der Rumpf keine Klicks abfangen —
    // sonst schiebt man das Bild nur neben dem Boot.
    svg.appendChild(_geEl('path', {
      d: _geRiss.rumpf,
      fill: _geWerkzeug === 'vorlage' ? 'none' : '#0f1e30',
      stroke: 'rgba(148,163,184,.55)', 'stroke-width': 1.2,
      'pointer-events': 'none' }));
  }

  for (const r of _geRiss.raeume) {
    const gewaehlt = r.id === _geAuswahl;
    const flaeche = _geEl('path', {
      d: raumPfad(r.form), fill: r.farbe, 'fill-opacity': gewaehlt ? .55 : .3,
      stroke: r.farbe, 'stroke-width': gewaehlt ? 2 : 1,
    });
    flaeche.dataset.raum = r.id;
    flaeche.style.cursor = _geWerkzeug === 'auswahl' ? 'move' : 'crosshair';
    // Beim Ausrichten der Vorlage nehmen die Räume keine Berührung an — sonst
    // fasst man beim Schieben des Bildes einen Raum an, der darüber liegt.
    if (_geWerkzeug === 'vorlage') flaeche.style.pointerEvents = 'none';
    svg.appendChild(flaeche);

    // Name in die Mitte, aber nur wenn er dort auch hinpasst.
    const k = _geKasten(r.form);
    if (k.w > 26 && k.h > 12) {
      const t = _geEl('text', { x: k.x + k.w / 2, y: k.y + k.h / 2 + 3,
                                'text-anchor': 'middle', 'font-size': 9,
                                fill: 'rgba(226,232,240,.9)',
                                'pointer-events': 'none' });
      t.textContent = r.name;
      svg.appendChild(t);
    }
  }

  if (_geAuswahl) _geGriffeZeichnen(svg, _geRaum(_geAuswahl));
  if (_geVieleck) _geVieleckVorschau(svg);
  _geSeitenleiste();
}

function _geGriffeZeichnen(svg, raum) {
  if (!raum) return;
  const punkte = raum.form.t === 'vieleck'
    ? raum.form.punkte.map((p, i) => ({ x: p[0], y: p[1], i }))
    : (() => {
        const { x, y, w, h } = raum.form;
        return [{ x, y, e: 'nw' }, { x: x + w, y, e: 'ne' },
                { x: x + w, y: y + h, e: 'se' }, { x, y: y + h, e: 'sw' }];
      })();
  for (const p of punkte) {
    const g = _geEl('circle', { cx: p.x, cy: p.y, r: _GE_GRIFF_R,
                                fill: '#38bdf8', stroke: '#0b1220', 'stroke-width': 1.5 });
    g.dataset.griff = p.e != null ? p.e : String(p.i);
    g.style.cursor = 'pointer';
    svg.appendChild(g);
  }
}

function _geVieleckVorschau(svg) {
  const pk = _geVieleck;
  if (!pk.length) return;
  svg.appendChild(_geEl('polyline', {
    points: pk.map(p => p.join(',')).join(' '),
    fill: 'rgba(56,189,248,.15)', stroke: '#38bdf8', 'stroke-width': 1.5,
    'stroke-dasharray': '4 3', 'pointer-events': 'none' }));
  for (const p of pk) {
    svg.appendChild(_geEl('circle', { cx: p[0], cy: p[1], r: 3, fill: '#38bdf8',
                                      'pointer-events': 'none' }));
  }
}

// ── Seitenleiste: der gewählte Raum ────────────────────────────────────────

function _geSeitenleiste() {
  const box = $('geRaumBox');
  if (!box) return;
  const r = _geRaum(_geAuswahl);
  box.hidden = !r;
  if (!r) return;
  $('geRaumName').value = r.name;
  const farben = $('geRaumFarben');
  farben.innerHTML = _GE_FARBEN.map(f =>
    `<button class="ge-farbe${f === r.farbe ? ' aktiv' : ''}" data-farbe="${f}"
             style="background:${f}" title="${f}"></button>`).join('');
}

function geRaumNameGeaendert(wert) {
  const r = _geRaum(_geAuswahl);
  if (!r) return;
  _geMerken();
  r.name = String(wert || '').slice(0, 60);
  _geZeichnen();
}

/**
 * Wie viele Gegenstaende in welchem Raum liegen.
 *
 * Ein Raum ist nicht nur eine Flaeche: der Stauplan haengt mit der Raumkennung
 * daran. Wer ihn loescht, macht aus "liegt in der Achterpiek" ein leeres Feld
 * — still, und erst beim naechsten Suchen zu merken.
 */
function _geStauplanZaehlen() {
  fetch('/api/stauplan')
    .then(r => r.ok ? r.json() : null)
    .then(liste => {
      if (!Array.isArray(liste)) return;
      _geStauzahl = {};
      for (const eintrag of liste) {
        const k = eintrag && eintrag.fach;
        if (k) _geStauzahl[k] = (_geStauzahl[k] || 0) + 1;
      }
    })
    .catch(() => {});
}

function geRaumLoeschen() {
  const r = _geRaum(_geAuswahl);
  if (!r) return;
  const drin = _geStauzahl && _geStauzahl[r.id];
  if (drin && !confirm(
      `Im Raum „${r.name}" liegen laut Stauplan ${drin} ` +
      `${drin === 1 ? 'Gegenstand' : 'Gegenstände'}. Der Raum wird entfernt — ` +
      `die Einträge behalten dann keinen Ort mehr. Trotzdem?`)) return;
  _geMerken();
  _geRiss.raeume = _geRiss.raeume.filter(x => x.id !== r.id);
  _geAuswahl = null;
  _geZeichnen();
}

// ── Zeigerbedienung ────────────────────────────────────────────────────────

/** Bildschirmpunkt in Zeichnungskoordinaten — über die echte Matrix des SVG,
 *  damit Skalierung, Rand und Seitenverhältnis nicht von Hand zu rechnen sind. */
function _geOrt(e) {
  const svg = $('geSvg');
  const pt = svg.createSVGPoint();
  pt.x = e.clientX; pt.y = e.clientY;
  const m = svg.getScreenCTM();
  if (!m) return { x: 0, y: 0 };
  const p = pt.matrixTransform(m.inverse());
  return _geImBild({ x: p.x, y: p.y });
}

function _geFangen(v) {
  return Math.round(v / _GE_RASTER) * _GE_RASTER;
}

/**
 * Auf die Zeichenflaeche begrenzen.
 *
 * Was ausserhalb liegt, ist unsichtbar — und ein Raum, den man nicht sieht,
 * ist nicht wiederzufinden und auch nicht mehr zu loeschen. Beim Ziehen mit
 * dem Finger passiert das leicht, weil die Flaeche am Rand aufhoert und die
 * Hand nicht.
 */
function _geImBild(p) {
  const w = _geRiss.ansicht.w, h = _geRiss.ansicht.h;
  return { x: Math.max(0, Math.min(w, p.x)), y: Math.max(0, Math.min(h, p.y)) };
}

function _geNeueKennung(basis) {
  let n = 1, id = basis;
  while (_geRaum(id)) id = `${basis}-${++n}`;
  return id;
}

function _geBinden() {
  const svg = $('geSvg');
  if (!svg || svg.dataset.bereit) return;
  svg.dataset.bereit = '1';

  svg.addEventListener('pointerdown', e => {
    const p = _geOrt(e);

    if (_geWerkzeug === 'vieleck') {
      e.preventDefault();
      const x = _geFangen(p.x), y = _geFangen(p.y);
      if (!_geVieleck) _geVieleck = [];
      // Zurück auf den ersten Punkt schließt die Fläche — dieselbe Geste wie
      // in jedem Zeichenprogramm.
      const erster = _geVieleck[0];
      if (erster && _geVieleck.length >= 3 &&
          Math.hypot(erster[0] - x, erster[1] - y) < _GE_GRIFF_R * 2) {
        _geVieleckAbschliessen();
        return;
      }
      _geVieleck.push([x, y]);
      _geZeichnen();
      return;
    }

    if (_geWerkzeug === 'rechteck') {
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      _geZiehen = { art: 'neu', x0: _geFangen(p.x), y0: _geFangen(p.y) };
      return;
    }

    if (_geWerkzeug === 'vorlage') {
      if (!(e.target.dataset && e.target.dataset.vorlage)) return;
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      _geMerken();
      _geZiehen = { art: 'bild', px: p.x, py: p.y,
                    x0: _geRiss.bild.x, y0: _geRiss.bild.y };
      return;
    }

    // Auswahl
    const griff = e.target.dataset && e.target.dataset.griff;
    if (griff != null && _geAuswahl) {
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      _geMerken();
      _geZiehen = { art: 'griff', griff, id: _geAuswahl };
      return;
    }
    const id = e.target.dataset && e.target.dataset.raum;
    if (id) {
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      _geAuswahl = id;
      _geMerken();
      const r = _geRaum(id);
      _geZiehen = { art: 'schieben', id, px: p.x, py: p.y,
                    start: JSON.parse(JSON.stringify(r.form)) };
      _geZeichnen();
      return;
    }
    // Ins Leere getippt: Auswahl aufheben.
    if (_geAuswahl) { _geAuswahl = null; _geZeichnen(); }
  });

  svg.addEventListener('pointermove', e => {
    if (!_geZiehen) return;
    const p = _geOrt(e);
    if (_geZiehen.art === 'neu') {
      _geZiehen.x1 = _geFangen(p.x);
      _geZiehen.y1 = _geFangen(p.y);
      _geZeichnen();
      _geNeuVorschau();
      return;
    }
    if (_geZiehen.art === 'bild') {
      _geRiss.bild.x = Math.round(_geZiehen.x0 + (p.x - _geZiehen.px));
      _geRiss.bild.y = Math.round(_geZiehen.y0 + (p.y - _geZiehen.py));
      _geZeichnen();
      return;
    }
    const r = _geRaum(_geZiehen.id);
    if (!r) return;
    if (_geZiehen.art === 'schieben') {
      const dx = _geFangen(p.x - _geZiehen.px), dy = _geFangen(p.y - _geZiehen.py);
      const s = _geZiehen.start;
      if (r.form.t === 'vieleck') {
        r.form.punkte = s.punkte.map(([x, y]) => [x + dx, y + dy]);
      } else {
        r.form.x = s.x + dx; r.form.y = s.y + dy;
      }
    } else if (_geZiehen.art === 'griff') {
      const x = _geFangen(p.x), y = _geFangen(p.y);
      if (r.form.t === 'vieleck') {
        r.form.punkte[Number(_geZiehen.griff)] = [x, y];
      } else {
        _geEckeZiehen(r.form, _geZiehen.griff, x, y);
      }
    }
    _geZeichnen();
  });

  const fertig = e => {
    if (!_geZiehen) return;
    if (_geZiehen.art === 'neu') _geNeuAbschliessen();
    _geZiehen = null;
    try { svg.releasePointerCapture(e.pointerId); } catch (_) {}
    _geZeichnen();
  };
  svg.addEventListener('pointerup', fertig);
  svg.addEventListener('pointercancel', fertig);

  // Farbwahl und Tastatur
  const farben = $('geRaumFarben');
  if (farben) farben.addEventListener('click', e => {
    const k = e.target.closest('[data-farbe]');
    const r = _geRaum(_geAuswahl);
    if (!k || !r) return;
    _geMerken();
    r.farbe = k.dataset.farbe;
    _geZeichnen();
  });

  document.addEventListener('keydown', e => {
    if ($('grEditorOverlay').classList.contains('hidden')) return;
    if (e.target.tagName === 'INPUT') return;
    if (e.key === 'Escape') {
      if (_geVieleck) { _geVieleck = null; _geZeichnen(); }
      else if (_geAuswahl) { _geAuswahl = null; _geZeichnen(); }
    } else if (e.key === 'Enter' && _geVieleck && _geVieleck.length >= 3) {
      _geVieleckAbschliessen();
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && _geAuswahl) {
      e.preventDefault();
      geRaumLoeschen();
    } else if (e.key === 'z' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      grundrissZurueck();
    }
  });
}

/** Eine Ecke ziehen — und dabei nicht negativ werden lassen. */
function _geEckeZiehen(form, ecke, x, y) {
  let { x: x0, y: y0, w, h } = form;
  let x1 = x0 + w, y1 = y0 + h;
  if (ecke.includes('w')) x0 = x;
  if (ecke.includes('e')) x1 = x;
  if (ecke.includes('n')) y0 = y;
  if (ecke.includes('s')) y1 = y;
  form.x = Math.min(x0, x1);
  form.y = Math.min(y0, y1);
  form.w = Math.max(_GE_RASTER, Math.abs(x1 - x0));
  form.h = Math.max(_GE_RASTER, Math.abs(y1 - y0));
}

function _geNeuVorschau() {
  const z = _geZiehen;
  if (!z || z.x1 == null) return;
  const svg = $('geSvg');
  svg.appendChild(_geEl('rect', {
    x: Math.min(z.x0, z.x1), y: Math.min(z.y0, z.y1),
    width: Math.abs(z.x1 - z.x0), height: Math.abs(z.y1 - z.y0),
    fill: 'rgba(56,189,248,.2)', stroke: '#38bdf8', 'stroke-width': 1.5,
    'stroke-dasharray': '4 3', 'pointer-events': 'none' }));
}

function _geNeuAbschliessen() {
  const z = _geZiehen;
  const w = Math.abs((z.x1 ?? z.x0) - z.x0), h = Math.abs((z.y1 ?? z.y0) - z.y0);
  // Ein Tipp ohne Bewegung ist kein Raum, sondern ein Tipp.
  if (w < _GE_RASTER * 2 || h < _GE_RASTER * 2) return;
  _geMerken();
  _geRaumAnlegen({ t: 'rechteck', x: Math.min(z.x0, z.x1), y: Math.min(z.y0, z.y1),
                   w, h });
  _geWerkzeugSetzen('auswahl');
}

function _geVieleckAbschliessen() {
  if (!_geVieleck || _geVieleck.length < 3) { _geVieleck = null; _geZeichnen(); return; }
  _geMerken();
  _geRaumAnlegen({ t: 'vieleck', punkte: _geVieleck.slice() });
  _geVieleck = null;
  _geWerkzeugSetzen('auswahl');
}

function _geRaumAnlegen(form) {
  const nr = (_geRiss.raeume.length % _GE_FARBEN.length);
  const id = _geNeueKennung('raum');
  _geRiss.raeume.push({ id, name: `Raum ${_geRiss.raeume.length + 1}`,
                        farbe: _GE_FARBEN[nr], form });
  _geAuswahl = id;
  _geZeichnen();
  const feld = $('geRaumName');
  if (feld) { feld.focus(); feld.select(); }
}

// ── Rumpfformen ────────────────────────────────────────────────────────────
//
// Kein Boot sieht aus wie die Zeichnung von einem anderen Boot — feste
// Vorlagen wären deshalb immer knapp daneben. Diese hier sind gerechnet: aus
// vier Zahlen wird ein Umriss, und die vier Zahlen kann man verschieben, bis
// er zum eigenen Boot passt.

const _GE_RUMPF_VORLAGEN = [
  { id: 'klassisch', name: 'Langkieler',      bug: .28, heck: .40, breiteBei: .52, spiegel: .35 },
  { id: 'fahrten',   name: 'Fahrtenyacht',    bug: .38, heck: .62, breiteBei: .56, spiegel: .55 },
  { id: 'modern',    name: 'Moderne Yacht',   bug: .50, heck: .88, breiteBei: .62, spiegel: .85 },
  { id: 'doppelend', name: 'Doppelender',     bug: .30, heck: .18, breiteBei: .50, spiegel: .10 },
  { id: 'motor',     name: 'Motorboot',       bug: .45, heck: .92, breiteBei: .45, spiegel: 1.0 },
  { id: 'platt',     name: 'Plattbodenschiff', bug: .62, heck: .78, breiteBei: .50, spiegel: .70 },
];

/**
 * Den Rumpfumriss rechnen — Bug oben, Heck unten, spiegelsymmetrisch.
 *
 * `bug` sagt, wie voll der Vorschiffsbereich ist (klein = spitz),
 * `heck` wie breit der Spiegel im Verhältnis zur größten Breite,
 * `breiteBei` wo die größte Breite liegt (0 = ganz vorn, 1 = ganz hinten),
 * `spiegel` wie stark das Heck gerundet ist (0 = spitz, 1 = gerade Kante).
 */
function geRumpfPfad({ w, h, bug, heck, breiteBei, spiegel, rand = 12 }) {
  const mx = w / 2;
  const halb = mx - rand;                       // größte Halbbreite
  const yBug = rand, yHeck = h - rand;
  const yMax = yBug + breiteBei * (yHeck - yBug);
  const halbHeck = halb * heck;
  // Kontrollpunkte: `bug` schiebt sie nach außen, der Bug wird völliger.
  // Ein voelliger Bug wird frueh breit, ein spitzer erst spaet. Beides steckt
  // in denselben zwei Zahlen: wie weit der Kontrollpunkt nach aussen geht und
  // wie hoch er liegt.
  const k1 = yBug + (yMax - yBug) * (.08 + .42 * (1 - bug));
  const k2 = yBug + (yMax - yBug) * .88;
  const k3 = yMax + (yHeck - yMax) * .38;
  const k4 = yHeck - (yHeck - yMax) * .14 * (1 - spiegel);
  const bugBreite = halb * bug;

  // Die Bugspitze: bei einem spitzen Rumpf ein Punkt, bei einem voelligen
  // eine kurze Rundung. Ohne sie bekam das Motorboot eine Schildform mit
  // scharfer Ecke oben — zwei Kurven, die sich im Winkel treffen.
  const kappe = Math.max(0, bug - .5) * halb * 1.3;
  const yKappe = yBug + kappe * .45;
  const z = n => n.toFixed(1);

  const s = [];
  s.push(`M${z(mx - kappe)},${z(yKappe)}`);
  s.push(`Q${z(mx)},${z(yBug)} ${z(mx + kappe)},${z(yKappe)}`);
  // Steuerbordseite nach achtern
  s.push(`C${z(mx + bugBreite)},${z(k1)} ${z(mx + halb)},${z(k2)} ${z(mx + halb)},${z(yMax)}`);
  s.push(`C${z(mx + halb)},${z(k3)} ${z(mx + halbHeck)},${z(k4)} ${z(mx + halbHeck)},${z(yHeck)}`);
  // Spiegel: bei 1 eine gerade Kante, bei 0 laufen die Seiten im Punkt zusammen
  if (spiegel > .02) {
    const bauch = (1 - spiegel) * (yHeck - yMax) * .10;
    s.push(`Q${z(mx)},${z(yHeck + bauch)} ${z(mx - halbHeck)},${z(yHeck)}`);
  } else {
    s.push(`L${z(mx - halbHeck)},${z(yHeck)}`);
  }
  // Backbordseite zurueck nach vorn
  s.push(`C${z(mx - halbHeck)},${z(k4)} ${z(mx - halb)},${z(k3)} ${z(mx - halb)},${z(yMax)}`);
  s.push(`C${z(mx - halb)},${z(k2)} ${z(mx - bugBreite)},${z(k1)} ${z(mx - kappe)},${z(yKappe)}`);
  s.push('Z');
  return s.join(' ');
}

function geRumpfDialogOeffnen() {
  $('geRumpfDialog').hidden = false;
  const liste = $('geRumpfListe');
  // Die Vorschau steht bewusst NICHT im echten Verhaeltnis. Ein Boot ist
  // dreieinhalbmal so lang wie breit — sechs solche Streifen nebeneinander
  // sind sechs Striche, und der Unterschied zwischen Langkieler und Motorboot
  // waere nicht zu sehen. Gewaehlt wird der CHARAKTER des Rumpfes; die Masse
  // stehen daneben und bestimmen den fertigen Riss.
  liste.innerHTML = _GE_RUMPF_VORLAGEN.map(v => {
    const w = 120, h = 250;
    const d = geRumpfPfad({ w, h, ...v });
    return `<button class="ge-vorlage" data-vorlage="${v.id}"
            onclick="geRumpfUebernehmen('${v.id}')">
      <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet">
        <path d="${d}" fill="rgba(56,189,248,.18)" stroke="#38bdf8" stroke-width="2"/>
      </svg>
      <span>${v.name}</span></button>`;
  }).join('');
}

function geRumpfDialogSchliessen() { $('geRumpfDialog').hidden = true; }

/**
 * Eine Rumpfform übernehmen.
 *
 * Dabei wird die Zeichenfläche auf das Verhältnis Länge zu Breite gesetzt.
 * Sonst stünde ein 12-Meter-Boot mit 3,5 Metern Breite in einem Feld, das
 * andere Maße hat, und jeder eingezeichnete Raum wäre verzerrt.
 */
function geRumpfUebernehmen(id) {
  const v = _GE_RUMPF_VORLAGEN.find(x => x.id === id);
  if (!v) return;
  const loa = Math.max(1, parseFloat($('geLoa').value) || 12);
  const breite = Math.max(0.5, parseFloat($('geBreite').value) || 3.5);
  _geMerken();
  const w = 200, h = Math.round(w * loa / breite);
  const alt = _geRiss.ansicht || { w, h };
  // Die Zeichenflaeche aendert ihre Hoehe mit dem Laengen-Breiten-Verhaeltnis.
  // Die vorhandenen Raeume ziehen mit — sonst saessen sie nach dem Wechsel
  // alle im Vorschiff, obwohl sich am Boot nichts geaendert hat.
  const fx = w / (alt.w || w), fy = h / (alt.h || h);
  if (Math.abs(fx - 1) > .001 || Math.abs(fy - 1) > .001) {
    for (const r of _geRiss.raeume) {
      if (r.form.t === 'vieleck') {
        r.form.punkte = r.form.punkte.map(([x, y]) => [+(x * fx).toFixed(1), +(y * fy).toFixed(1)]);
      } else {
        r.form.x = +(r.form.x * fx).toFixed(1); r.form.y = +(r.form.y * fy).toFixed(1);
        r.form.w = +(r.form.w * fx).toFixed(1); r.form.h = +(r.form.h * fy).toFixed(1);
      }
    }
  }
  _geRiss.ansicht = { w, h };
  _geRiss.loa_m = loa;
  _geRiss.breite_m = breite;
  _geRiss.rumpf = geRumpfPfad({ w, h, ...v });
  // Der alte Hintergrund gehörte zum alten Rumpf. Ein Möbelstück, das jetzt
  // im Wasser steht, ist schlechter als gar keins.
  _geRiss.hintergrund = [];
  geRumpfDialogSchliessen();
  _geZeichnen();
}

function _geMasseFuellen() {
  const n = $('geName'), l = $('geLoa'), b = $('geBreite');
  if (n) n.value = _geRiss.name || '';
  if (l) l.value = _geRiss.loa_m || '';
  if (b) b.value = _geRiss.breite_m || '';
}

function geMasseGeaendert() {
  if (!_geRiss) return;
  _geRiss.name = ($('geName').value || '').slice(0, 60);
  _geRiss.loa_m = parseFloat($('geLoa').value) || 0;
  _geRiss.breite_m = parseFloat($('geBreite').value) || 0;
  _geSchmutzig = true;
  _geKnoepfe();
}

// ── Planvorlage ────────────────────────────────────────────────────────────
//
// Der Weg, den fast jeder gehen will: das Bild des Bootsplans hochladen und
// die Räume darüber nachziehen. Das ist noch nicht die automatische Erkennung
// (dafür braucht es einen Bilddienst und einen Zugang dazu, siehe Bericht) —
// aber es ist der Teil, der die Arbeit wirklich abnimmt.
//
// Verkleinert wird HIER im Browser, nicht auf dem Pi. Pillow und numpy sind
// dort nicht installiert, und sie auf einem ARMv6-Kern zu bauen dauert
// Stunden. Ein Handyfoto hat acht Megapixel; was ankommt, sind danach ein
// paar hundert Kilobyte.

const _GE_BILD_KANTE = 1400;    // längste Kante nach dem Verkleinern

/**
 * Liegt eine Planvorlage da?
 *
 * Die Antwort steht im Riss selbst (`hat_vorlage`). Vorher wurde danach
 * gefragt — und ein 404 auf diese Frage schreibt in jedem Browser einen roten
 * Fehler in die Konsole, für einen Zustand, der völlig in Ordnung ist.
 */
function _geVorlagePruefen() {
  _geBildUrl = (GRUNDRISS && GRUNDRISS.hat_vorlage)
    ? '/api/grundriss/vorlage?t=' + Date.now() : null;
  _geVorlageLeiste();
}

/**
 * Geht das Hochladen hier ueberhaupt?
 *
 * Nur am Boot. Der Server leitet an den Pi nur JSON durch und hoechstens
 * 256 kB — ein Planfoto ueber die Mobilfunkverbindung des Bootes zu schicken
 * waere ohnehin die falsche Richtung. Statt es scheitern zu lassen, steht es
 * vorher da.
 */
function _geAmBoot() {
  return typeof _quelle === 'undefined' || !_quelle || _quelle.art === 'direkt'
      || _quelle.art === 'unbekannt';
}

function _geVorlageLeiste() {
  const box = $('geVorlageBox');
  if (!box) return;
  const amBoot = _geAmBoot();
  const waehlen = box.querySelector('.ge-datei');
  if (waehlen) waehlen.hidden = !amBoot;
  const fern = $('geVorlageFern');
  if (fern) fern.hidden = amBoot;
  const da = !!_geBildUrl;
  box.querySelectorAll('.ge-vorlage-an').forEach(el => { el.hidden = !da; });
  const hinweis = $('geVorlageLeer');
  if (hinweis) hinweis.hidden = da;
  // Ohne Vorlage gibt es nichts auszurichten — ein Werkzeug, das dann nichts
  // tut, sieht aus wie ein Fehler.
  const wz = document.querySelector('[data-werkzeug="vorlage"]');
  if (wz) {
    wz.disabled = !da;
    if (!da && _geWerkzeug === 'vorlage') _geWerkzeugSetzen('auswahl');
  }
  if (da && _geRiss.bild) {
    const s = $('geVorlageDeckkraft'), g = $('geVorlageGroesse');
    if (s) s.value = Math.round((_geRiss.bild.deckkraft ?? .5) * 100);
    if (g) g.value = Math.round(_geRiss.bild.w || _geRiss.ansicht.w);
  }
}

async function geVorlageWaehlen(eingabe) {
  const datei = eingabe.files && eingabe.files[0];
  if (!datei) return;
  const fb = $('geFeedback');
  try {
    const klein = await _geVerkleinern(datei);
    const antwort = await fetch('/api/grundriss/vorlage', {
      method: 'PUT', headers: { 'Content-Type': 'image/jpeg' }, body: klein.blob });
    if (!antwort.ok) {
      // 404/405 heisst hier fast immer: die Oberflaeche laeuft ueber den
      // Server, und der leitet Bilder nicht durch. Die nackte Zahl waere
      // keine Auskunft.
      if (antwort.status === 404 || antwort.status === 405) {
        throw new Error('Die Planvorlage lässt sich nur direkt am Boot hochladen.');
      }
      const d = await antwort.json().catch(() => ({}));
      throw new Error(d.detail || `Fehler ${antwort.status}`);
    }
    _geBildUrl = '/api/grundriss/vorlage?t=' + Date.now();
    _geMerken();
    // Erstmal so einpassen, dass die Vorlage die Zeichenfläche ausfüllt und
    // dabei ihr Seitenverhältnis behält — von da aus wird geschoben.
    const av = _geRiss.ansicht.w / _geRiss.ansicht.h;
    const bv = klein.breite / klein.hoehe;
    const w = bv > av ? _geRiss.ansicht.w : _geRiss.ansicht.h * bv;
    const h = bv > av ? _geRiss.ansicht.w / bv : _geRiss.ansicht.h;
    _geRiss.bild = { x: Math.round((_geRiss.ansicht.w - w) / 2),
                     y: Math.round((_geRiss.ansicht.h - h) / 2),
                     w: Math.round(w), h: Math.round(h), deckkraft: .5 };
    _geVorlageLeiste();
    _geWerkzeugSetzen('vorlage');
  } catch (e) {
    if (fb) { fb.className = 'settings-feedback error show'; fb.textContent = e.message; }
  } finally {
    eingabe.value = '';
  }
}

/** Bild auf eine vernünftige Kantenlänge bringen und als JPEG zurückgeben. */
function _geVerkleinern(datei) {
  return new Promise((los, schief) => {
    const url = URL.createObjectURL(datei);
    const bild = new Image();
    bild.onload = () => {
      URL.revokeObjectURL(url);
      const f = Math.min(1, _GE_BILD_KANTE / Math.max(bild.width, bild.height));
      const w = Math.max(1, Math.round(bild.width * f));
      const h = Math.max(1, Math.round(bild.height * f));
      const lw = document.createElement('canvas');
      lw.width = w; lw.height = h;
      const ctx = lw.getContext('2d');
      // Weißer Grund: ein PNG mit Transparenz würde als JPEG sonst schwarz.
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(bild, 0, 0, w, h);
      lw.toBlob(b => b ? los({ blob: b, breite: w, hoehe: h })
                       : schief(new Error('Bild ließ sich nicht umwandeln')),
                'image/jpeg', 0.82);
    };
    bild.onerror = () => { URL.revokeObjectURL(url); schief(new Error('Das ist kein Bild')); };
    bild.src = url;
  });
}

function geVorlageDeckkraft(wert) {
  if (!_geRiss.bild) return;
  _geRiss.bild.deckkraft = Math.max(0, Math.min(1, Number(wert) / 100));
  _geSchmutzig = true;
  _geKnoepfe();
  _geZeichnen();
}

/** Größe ändern und dabei die Mitte festhalten — sonst wandert das Bild weg. */
function geVorlageGroesse(wert) {
  const b = _geRiss.bild;
  if (!b || !b.w) return;
  const neu = Math.max(20, Number(wert) || 20);
  const f = neu / b.w;
  const mx = b.x + b.w / 2, my = b.y + b.h / 2;
  b.w = Math.round(neu);
  b.h = Math.round(b.h * f);
  b.x = Math.round(mx - b.w / 2);
  b.y = Math.round(my - b.h / 2);
  _geSchmutzig = true;
  _geKnoepfe();
  _geZeichnen();
}

async function geVorlageEntfernen() {
  if (!confirm('Die Planvorlage entfernen?')) return;
  await fetch('/api/grundriss/vorlage', { method: 'DELETE' }).catch(() => {});
  _geBildUrl = null;
  _geMerken();
  delete _geRiss.bild;
  _geVorlageLeiste();
  if (_geWerkzeug === 'vorlage') _geWerkzeugSetzen('auswahl');
  else _geZeichnen();
}

// ── Speichern ──────────────────────────────────────────────────────────────

async function grundrissSpeichern() {
  const fb = $('geFeedback');
  const knopf = $('geSpeichern');
  if (knopf) knopf.disabled = true;
  try {
    const antwort = await fetch('/api/grundriss', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_geRiss),
    });
    if (!antwort.ok) {
      const d = await antwort.json().catch(() => ({}));
      throw new Error(d.detail || `Fehler ${antwort.status}`);
    }
    // Der Pi hat geprüft und zurückgegeben, was er behalten hat — das ist ab
    // jetzt die Wahrheit, nicht die Arbeitskopie.
    const geprueft = await antwort.json();
    GRUNDRISS = geprueft;
    _geBildUrl = geprueft.hat_vorlage ? (_geBildUrl || '/api/grundriss/vorlage?t=' + Date.now()) : null;
    _geRiss = JSON.parse(JSON.stringify(geprueft));
    if (typeof _orteAufbauen === 'function') _orteAufbauen();
    _geSchmutzig = false;
    _geKnoepfe();
    if (fb) { fb.className = 'settings-feedback show'; fb.textContent = 'Gespeichert'; 
              setTimeout(() => fb.classList.remove('show'), 2500); }
    _geZeichnen();
  } catch (e) {
    if (fb) { fb.className = 'settings-feedback error show'; fb.textContent = e.message; }
    if (knopf) knopf.disabled = false;
  }
}
