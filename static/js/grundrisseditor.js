// ── Grundriss zeichnen (Logbuch) ────────────────────────────────────────────
//
// Dieses Werkzeug laeuft auf dem SERVER und nicht am Boot. Zeichnen ist
// Planung: man sitzt in Ruhe davor, hat den Bootsplan daneben liegen und
// braucht Platz auf dem Bildschirm. Der Pi braucht davon nur das Ergebnis —
// eine Datei, die man ihm gibt.
//
// Deshalb hat der Server seinen eigenen Arbeitsstand. Was ans Boot geht, wird
// ausdruecklich exportiert und dort geladen; ein stiller Abgleich waere hier
// falsch, dann stuende jede halbfertige Linie sofort im Stauplan an Bord.
//
// Zwei Dinge bewusst NICHT:
//
//   * Keine Seitenansicht. Uebereinanderliegende Faecher zeichnet man
//     nebeneinander ein — ein Stauplan beantwortet "wo liegt das Ding", und
//     dafuer genuegt die Draufsicht. Eine zweite Ansicht verdoppelt die Arbeit
//     beim Zeichnen und halbiert die Uebersicht beim Suchen.
//   * Keine zehn fertigen Bootszeichnungen. Kein Boot sieht aus wie eine
//     Zeichnung von einem anderen Boot. Stattdessen gerechnete RUMPFFORMEN.

const _GE_RASTER = 2;           // Fangraster in Zeichnungseinheiten
const _GE_VERLAUF_MAX = 40;     // so viele Schritte lassen sich zuruecknehmen
const _GE_GRIFF_R = 5;          // Radius der Anfasser, in Zeichnungseinheiten

let _geRiss     = null;         // Arbeitskopie, erst beim Speichern echt
let _geStand    = null;         // was auf dem Server liegt
let _geWerkzeug = 'auswahl';    // auswahl | rechteck | vieleck | vorlage
let _geAuswahl  = null;         // id des gewaehlten Raums
let _geVieleck  = null;         // Punkte, die gerade gesetzt werden
let _geVerlauf  = [];
let _geZiehen   = null;         // {art, id, ...} waehrend einer Bewegung
let _geSchmutzig = false;       // ungespeicherte Aenderungen
let _geBildUrl  = null;         // Adresse der Planvorlage, wenn eine da ist
let _geStauzahl = null;         // {raumId: Anzahl} — was im Stauplan drinliegt
let _geGeladen  = false;

const _GE_FARBEN = ['#f59e0b', '#fbbf24', '#60a5fa', '#38bdf8', '#4ade80',
                    '#34d399', '#f472b6', '#a78bfa', '#fb923c', '#94a3b8'];

/** Der Umriss eines Raums als Pfad. Steht hier, weil das Logbuch orte.js nicht kennt. */
function _geRaumPfad(form) {
  if (!form) return '';
  if (form.t === 'vieleck') {
    return 'M' + (form.punkte || []).map(p => `${p[0]},${p[1]}`).join(' L') + ' Z';
  }
  const { x, y, w, h } = form;
  return `M${x},${y} L${x + w},${y} L${x + w},${y + h} L${x},${y + h} Z`;
}

// ── Die Seite oeffnen ──────────────────────────────────────────────────────

/** Wird von seiteZeigen('grundriss') gerufen. */
async function _geAnzeigen() {
  if (!_geGeladen) {
    _geGeladen = true;
    try {
      const d = await hole('/api/logbuch/grundriss');
      _geStand = d || {};
    } catch (_) { _geStand = {}; }
  }
  // Arbeitskopie: erst Speichern macht die Aenderung echt. Ohne das waere
  // jeder Fehlgriff sofort im gespeicherten Riss.
  if (!_geRiss) {
    _geRiss = JSON.parse(JSON.stringify(_geStand || {}));
    if (!Array.isArray(_geRiss.raeume)) _geRiss.raeume = [];
    if (!Array.isArray(_geRiss.hintergrund)) _geRiss.hintergrund = [];
    if (!_geRiss.ansicht) _geRiss.ansicht = { w: 200, h: 680 };
    _geVerlauf = [];
    _geSchmutzig = false;
  }
  _geBinden();
  _geVorlagePruefen();
  _geWerkzeugSetzen(_geWerkzeug === 'vorlage' && !_geBildUrl ? 'auswahl' : _geWerkzeug);
  _geMasseFuellen();
  _geZeichnen();
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
      d: _geRaumPfad(r.form), fill: r.farbe, 'fill-opacity': gewaehlt ? .55 : .3,
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

  // Leeres Blatt: sagen, womit man anfaengt. Eine leere Flaeche mit Raster
  // sieht aus, als fehle etwas — und der erste Schritt ist nicht zu erraten.
  if (!_geRiss.rumpf && !_geRiss.raeume.length && !_geBildUrl) {
    const zeilen = ['Leeres Blatt.', 'Fang mit einer Rumpfform an —',
                    'oder lade ein Foto des Bootsplans.'];
    zeilen.forEach((zeile, i) => {
      const t = _geEl('text', { x: w / 2, y: h / 2 - 14 + i * 16,
                                'text-anchor': 'middle', 'font-size': 11,
                                fill: 'rgba(148,163,184,.75)',
                                'pointer-events': 'none' });
      t.textContent = zeile;
      svg.appendChild(t);
    });
  }
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
  // Erst beim ersten Loeschen gefragt und nicht beim Oeffnen: der Stauplan
  // liegt am BOOT, und liegt das ohne Strom, antwortet der Server mit 409 —
  // eine rote Zeile in der Konsole fuer eine Auskunft, die meist niemand
  // braucht.
  if (_geStauzahl !== null) return Promise.resolve();
  _geStauzahl = {};
  return fetch('/api/stauplan')
    .then(r => r.ok ? r.json() : null)
    .then(liste => {
      if (!Array.isArray(liste)) return;
      for (const eintrag of liste) {
        const k = eintrag && eintrag.fach;
        if (k) _geStauzahl[k] = (_geStauzahl[k] || 0) + 1;
      }
    })
    .catch(() => {});
}

async function geRaumLoeschen() {
  const r = _geRaum(_geAuswahl);
  if (!r) return;
  await _geStauplanZaehlen();
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
    if (typeof _seite !== 'undefined' && _seite !== 'grundriss') return;
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
  { id: 'langkieler', name: 'Langkieler',       voll: .26, breiteBei: .50, heck: .34, spiegel: .45, lauf: .35 },
  { id: 'fahrten',    name: 'Fahrtenyacht',     voll: .34, breiteBei: .56, heck: .58, spiegel: .70, lauf: .50 },
  { id: 'breitheck',  name: 'Breites Heck',     voll: .42, breiteBei: .62, heck: .88, spiegel: 1.0, lauf: .72 },
  { id: 'spitzgatt',  name: 'Spitzgatter',      voll: .44, breiteBei: .51, heck: .03, spiegel: 0,   lauf: .42, kappe: .12 },
  { id: 'motor',      name: 'Motorboot',        voll: .52, breiteBei: .58, heck: .95, spiegel: 1.0, lauf: .82, kappe: .18 },
  { id: 'platt',      name: 'Plattbodenschiff', voll: .78, breiteBei: .48, heck: .78, spiegel: .50, lauf: .62, kappe: .80 },
  { id: 'kat',        name: 'Katamaran',        art: 'kat' },
];

function _z(n) { return Math.round(n * 10) / 10; }

function rumpfSeiten(o) {
  const { w, h, voll, breiteBei, heck, spiegel, lauf, kappe = 0, rand = 12,
          mitte = w / 2, halbMax = w / 2 - rand } = o;
  const mx = mitte, halb = halbMax;
  const yBug = rand, yHeck = h - rand;
  const yMax = yBug + breiteBei * (yHeck - yBug);
  const L1 = yMax - yBug, L2 = yHeck - yMax;
  const halbHeck = halb * heck;

  // Vorschiff: der erste Kontrollpunkt liegt nah an der Mittellinie und tief
  // unter dem Steven — dann verlaesst die Linie ihn laengs und nicht quer.
  const a = 0.06 + 0.55 * voll;
  const bq = 0.62 - 0.44 * voll;
  const c = 0.40 + 0.18 * voll;
  const d = 0.25 + 0.60 * lauf;
  const e = 0.55 + 0.35 * lauf;

  // Stumpfer Steven: eine kurze Rundung statt eines Punktes.
  const r = kappe * halb * 0.55;
  const yK = yBug + r * 0.5;

  const s = [];
  s.push(`M${_z(mx - r)},${_z(yK)}`);
  if (r > 0.5) s.push(`Q${_z(mx)},${_z(yBug)} ${_z(mx + r)},${_z(yK)}`);
  s.push(`C${_z(mx + halb * a)},${_z(yBug + L1 * bq)} ${_z(mx + halb)},${_z(yMax - L1 * c)} ${_z(mx + halb)},${_z(yMax)}`);
  s.push(`C${_z(mx + halb)},${_z(yMax + L2 * d)} ${_z(mx + halbHeck + (halb - halbHeck) * (1 - e))},${_z(yHeck)} ${_z(mx + halbHeck)},${_z(yHeck)}`);
  s.push(halbHeck < halb * 0.06
    ? `L${_z(mx - halbHeck)},${_z(yHeck)}`
    // Die Woelbung darf nicht aus dem Blatt laufen: `rand` ist alles, was
    // unterhalb von yHeck noch da ist.
    : `Q${_z(mx)},${_z(yHeck + Math.min(rand * 0.7, (1 - spiegel) * L2 * 0.10))} ${_z(mx - halbHeck)},${_z(yHeck)}`);
  s.push(`C${_z(mx - halbHeck - (halb - halbHeck) * (1 - e))},${_z(yHeck)} ${_z(mx - halb)},${_z(yMax + L2 * d)} ${_z(mx - halb)},${_z(yMax)}`);
  s.push(`C${_z(mx - halb)},${_z(yMax - L1 * c)} ${_z(mx - halb * a)},${_z(yBug + L1 * bq)} ${_z(mx - r)},${_z(yK)}`);
  s.push('Z');
  return s.join(' ');
}

/**
 * Katamaran: zwei schlanke Ruempfe und das Deck DAZWISCHEN.
 *
 * Das Deck spannt nur von Innenkante zu Innenkante. Ueber die Ruempfe hinweg
 * gezeichnet lagen drei Umrisse uebereinander, und man sah lauter Linien, wo
 * in Wirklichkeit eine durchgehende Flaeche ist.
 */
function _bez(a, b, c, d, t) {
  const u = 1 - t;
  return u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
}

/**
 * Eine Rumpfseite als Punktfolge, Bug nach Heck.
 *
 * Fuer den Katamaran gebraucht: das Deck soll die Ruempfe BERUEHREN. Mit der
 * groessten Breite gerechnet schwebte es zwischen ihnen (dort, wo es ansetzt,
 * sind sie schmaler); mit Ueberlappung kreuzten sich die Linien. Die einzige
 * ehrliche Antwort ist, der echten Kante zu folgen.
 */
function _seitePunkte(cx, halb, yBug, yMax, yHeck, halbHeck, s, n = 26) {
  const L1 = yMax - yBug, L2 = yHeck - yMax;
  const voll = 0.34, lauf = 0.78;
  const a = 0.06 + 0.55 * voll, bq = 0.62 - 0.44 * voll, c = 0.40 + 0.18 * voll;
  const d = 0.25 + 0.60 * lauf, e = 0.55 + 0.35 * lauf;
  const pkt = [];
  for (let i = 0; i <= n; i++) {                       // Vorschiff
    const t = i / n;
    pkt.push([_bez(cx, cx + s * halb * a, cx + s * halb, cx + s * halb, t),
              _bez(yBug, yBug + L1 * bq, yMax - L1 * c, yMax, t)]);
  }
  for (let i = 1; i <= n; i++) {                       // Achterschiff
    const t = i / n;
    pkt.push([_bez(cx + s * halb, cx + s * halb,
                   cx + s * (halbHeck + (halb - halbHeck) * (1 - e)), cx + s * halbHeck, t),
              _bez(yMax, yMax + L2 * d, yHeck, yHeck, t)]);
  }
  return pkt;
}

function rumpfKatamaran(o) {
  const { w, h, rand = 12 } = o;
  const mx = w / 2, halb = mx - rand;
  const r = halb * 0.25;                    // halbe Breite EINES Rumpfes
  const versatz = halb - r;
  const yBug = rand, yHeck = h - rand;
  const nutz = h - 2 * rand;
  const yMax = yBug + nutz * 0.58, rHeck = r * 0.72;
  const P = n => n.map(([x, y]) => `${_z(x)},${_z(y)}`).join(' L');

  const teile = [];
  const innen = {};
  for (const s of [-1, 1]) {
    const cx = mx + s * versatz;
    const aussen = _seitePunkte(cx, r, yBug, yMax, yHeck, rHeck, s);
    const nach   = _seitePunkte(cx, r, yBug, yMax, yHeck, rHeck, -s);
    innen[s] = nach;
    teile.push(`M${P(aussen)} L${P([...nach].reverse())} Z`);
  }

  // Das Deck folgt den Innenkanten — vorn ein geschwungener Querbalken.
  const yA = yBug + nutz * 0.30, yB = yHeck;
  const stueck = s => innen[s].filter(([, y]) => y >= yA && y <= yB);
  const links = stueck(-1), rechts = stueck(1);
  if (links.length > 1 && rechts.length > 1) {
    teile.push(
      `M${_z(links[0][0])},${_z(links[0][1])} ` +
      `Q${_z(mx)},${_z(yA - nutz * 0.05)} ${_z(rechts[0][0])},${_z(rechts[0][1])} ` +
      `L${P(rechts.slice(1))} L${P([...links].reverse())} Z`);
  }
  return teile.join(' ');
}

function geRumpfPfad(o) {
  return o.art === 'kat' ? rumpfKatamaran(o) : rumpfSeiten(o);
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
    // Ein Katamaran ist breiter als eine Yacht — im selben Feld waere er ein
    // Strich mit zwei Spitzen.
    const w = v.art === 'kat' ? 170 : 120, h = 250;
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
  _geBildUrl = (_geStand && _geStand.hat_vorlage)
    ? '/api/logbuch/grundriss/vorlage?t=' + Date.now() : null;
  _geVorlageLeiste();
}

function _geVorlageLeiste() {
  const box = $('geVorlageBox');
  if (!box) return;
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
  try {
    const klein = await _geVerkleinern(datei);
    const antwort = await fetch('/api/logbuch/grundriss/vorlage', {
      method: 'PUT', headers: { 'Content-Type': 'image/jpeg' }, body: klein.blob });
    if (!antwort.ok) {
      const d = await antwort.json().catch(() => ({}));
      throw new Error(d.detail || `Fehler ${antwort.status}`);
    }
    _geBildUrl = '/api/logbuch/grundriss/vorlage?t=' + Date.now();
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
    _geMeldung(e.message, true);
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
  await fetch('/api/logbuch/grundriss/vorlage', { method: 'DELETE' }).catch(() => {});
  _geBildUrl = null;
  _geMerken();
  delete _geRiss.bild;
  _geVorlageLeiste();
  if (_geWerkzeug === 'vorlage') _geWerkzeugSetzen('auswahl');
  else _geZeichnen();
}

// ── Hinaus ans Boot ─────────────────────────────────────────────────────────
//
// Zwei Wege, und beide werden gebraucht. Die DATEI geht immer — auch wenn das
// Boot seit Wochen ohne Strom liegt; sie wird am Boot in den Einstellungen
// geladen. Der direkte Weg ist bequemer, setzt aber voraus, dass die
// Verbindung steht.
//
// Was hinausgeht, ist der GESPEICHERTE Stand und nicht die Arbeitskopie: sonst
// schickte man ans Boot etwas, das man selbst noch nicht behalten wollte.

/** Was das Boot bekommt — ohne die Planvorlage, die ist Werkzeug und kein Riss. */
function _geAusfuhr() {
  const r = JSON.parse(JSON.stringify(_geStand || {}));
  delete r.bild;
  delete r.hat_vorlage;
  return r;
}

function grundrissExportieren() {
  const r = _geAusfuhr();
  if (!r.raeume || !r.raeume.length) {
    _geMeldung('Noch nichts zu exportieren — erst Räume einzeichnen und speichern.', true);
    return;
  }
  const name = (r.name || 'boot').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const blob = new Blob([JSON.stringify(r, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `grundriss-${name || 'boot'}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  _geMeldung('Datei erzeugt. Am Boot unter Einstellungen › Grundriss laden.');
}

async function grundrissAnsBoot() {
  const knopf = $('geAnsBoot');
  if (knopf) knopf.disabled = true;
  try {
    const antwort = await fetch('/api/grundriss', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_geAusfuhr()),
    });
    if (antwort.status === 409) throw new Error('Das Boot ist gerade nicht verbunden.');
    if (!antwort.ok) {
      const d = await antwort.json().catch(() => ({}));
      throw new Error(d.detail || `Fehler ${antwort.status}`);
    }
    _geMeldung('Der Riss liegt jetzt am Boot.');
  } catch (e) {
    _geMeldung(e.message, true);
  } finally {
    if (knopf) knopf.disabled = false;
  }
}

function _geMeldung(text, schlimm = false) {
  const fb = $('geFeedback');
  if (!fb) return;
  fb.textContent = text;
  fb.className = 'ge-meldung' + (schlimm ? ' schlimm' : ' gut');
  clearTimeout(_geMeldung._uhr);
  _geMeldung._uhr = setTimeout(() => { fb.className = 'ge-meldung'; }, 6000);
}

// ── Speichern ──────────────────────────────────────────────────────────────

async function grundrissSpeichern() {
  const knopf = $('geSpeichern');
  if (knopf) knopf.disabled = true;
  try {
    const antwort = await fetch('/api/logbuch/grundriss', {
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
    _geStand = geprueft;
    _geBildUrl = geprueft.hat_vorlage ? (_geBildUrl || '/api/logbuch/grundriss/vorlage?t=' + Date.now()) : null;
    _geRiss = JSON.parse(JSON.stringify(geprueft));
      _geSchmutzig = false;
    _geKnoepfe();
    _geMeldung('Gespeichert.');
    _geZeichnen();
  } catch (e) {
    _geMeldung(e.message, true);
    if (knopf) knopf.disabled = false;
  }
}
