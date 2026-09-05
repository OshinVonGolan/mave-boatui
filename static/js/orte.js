// ── Grundriss und Orte an Bord ─────────────────────────────────────────────
// Eine gemeinsame Quelle fuer alles, was irgendwo an Bord sitzt: Stauplan-
// Artikel, Geraete, spaeter mehr.
//
// Der Grundriss stand bis hierher DREIMAL im Programm: als 250 Zeilen SVG in
// index.html, als Tabelle ORTE in dieser Datei und ein drittes Mal als
// STAU_FAECHER in stauplan.js. Drei Kopien desselben Bootes, von denen jede
// fuer sich altern konnte — und keine davon liess sich einzeichnen.
//
// Jetzt kommt er aus /api/grundriss. Das ist die Voraussetzung dafuer, ihn
// ueberhaupt bearbeiten zu koennen: was im Programmtext steht, kann niemand
// aendern, ohne den Programmtext zu aendern.

let GRUNDRISS = { ansicht: { w: 200, h: 680 }, rumpf: '', hintergrund: [], raeume: [] };

/** Raeume als Nachschlagewerk — dieselbe Form, die ORTE frueher hatte. */
let ORTE = {};

function _orteAufbauen() {
  ORTE = {};
  for (const r of (GRUNDRISS.raeume || [])) {
    const f = r.form || {};
    const kasten = f.t === 'vieleck' ? _vieleckKasten(f.punkte) : f;
    ORTE[r.id] = { name: r.name, color: r.farbe,
                   x: kasten.x, y: kasten.y, w: kasten.w, h: kasten.h,
                   form: f };
  }
}

/** Umschliessendes Rechteck eines Vielecks — fuer Mittelpunkt und Mini-Riss. */
function _vieleckKasten(punkte) {
  const xs = (punkte || []).map(p => p[0]), ys = (punkte || []).map(p => p[1]);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}

async function grundrissLaden() {
  try {
    const d = await fetch('/api/grundriss').then(r => r.ok ? r.json() : null);
    if (d && Array.isArray(d.raeume)) GRUNDRISS = d;
  } catch (_) {
    // Ohne Grundriss bleibt die Liste leer — der Stauplan zeigt dann seine
    // Artikel ohne Riss, statt gar nichts zu zeigen.
  }
  _orteAufbauen();
  return GRUNDRISS;
}

/** Anzeigename eines Ortes, leer wenn unbekannt. */
function ortName(key) { return ORTE[key]?.name || ''; }

// ── Zeichnen ────────────────────────────────────────────────────────────────
// Die Formen kommen aus einer Datei, also aus einer Quelle, die jemand
// beschreiben kann. Sie werden deshalb ueber die DOM-Schnittstelle erzeugt und
// nicht ueber innerHTML zusammengeklebt: ein Text bleibt damit ein Text, auch
// wenn spitze Klammern darin stehen. Der Server prueft dasselbe noch einmal
// von seiner Seite (siehe _grundriss_pruefen in main.py).

const _SVG_NS = 'http://www.w3.org/2000/svg';

function _svg(tag, attrs) {
  const el = document.createElementNS(_SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v !== undefined && v !== null) el.setAttribute(k, v);
  }
  return el;
}

/** Eine Hintergrundform aus den Daten in ein SVG-Element uebersetzen. */
function _formZeichnen(f) {
  const fuellung = f.fill === 'schraffur' ? 'url(#grHatch)' : f.fill;
  const gemeinsam = { fill: fuellung, stroke: f.stroke,
                      'stroke-width': f.sw, 'stroke-dasharray': f.strich };
  switch (f.t) {
    case 'rect':
      return _svg('rect', { ...gemeinsam, x: f.x, y: f.y, width: f.w, height: f.h,
                            rx: f.rx, ry: f.ry });
    case 'line':
      return _svg('line', { ...gemeinsam, x1: f.x1, y1: f.y1, x2: f.x2, y2: f.y2 });
    case 'circle':
      return _svg('circle', { ...gemeinsam, cx: f.cx, cy: f.cy, r: f.r });
    case 'ellipse':
      return _svg('ellipse', { ...gemeinsam, cx: f.cx, cy: f.cy, rx: f.rx, ry: f.ry });
    case 'path':
      return _svg('path', { ...gemeinsam, d: f.d });
    case 'text': {
      const el = _svg('text', { ...gemeinsam, x: f.x, y: f.y, 'font-size': f.fs,
                                'text-anchor': f.anker, 'font-weight': f.fw,
                                'letter-spacing': f.ls, 'font-family': f.ff });
      el.textContent = f.s || '';        // NICHT innerHTML
      return el;
    }
    default:
      return null;
  }
}

/** Der Umriss eines Raumes als Pfadangabe. */
function raumPfad(form) {
  if (!form) return '';
  if (form.t === 'vieleck') {
    return 'M' + (form.punkte || []).map(p => `${p[0]},${p[1]}`).join(' L') + ' Z';
  }
  const { x, y, w, h } = form;
  return `M${x},${y} L${x + w},${y} L${x + w},${y + h} L${x},${y + h} Z`;
}

/**
 * Den Grundriss in ein leeres <svg> zeichnen.
 *
 * @param {SVGElement} ziel     das svg-Element
 * @param {object} opts
 *   opts.klick    fn(raumId) — macht die Raeume anfassbar
 *   opts.klasse   Klassenname der Raumflaechen (fuer das Stylesheet)
 */
function grundrissZeichnen(ziel, opts = {}) {
  if (!ziel) return;
  const g = GRUNDRISS;
  ziel.setAttribute('viewBox', `0 0 ${g.ansicht?.w || 200} ${g.ansicht?.h || 680}`);
  ziel.textContent = '';

  // Schraffur einmal je Zeichnung. Die Kennung ist auf das Element bezogen,
  // damit zwei Grundrisse auf einer Seite sich nicht gegenseitig die
  // Musterdefinition wegnehmen.
  const defs = _svg('defs', {});
  const muster = _svg('pattern', { id: 'grHatch', x: 0, y: 0, width: 5, height: 5,
                                   patternUnits: 'userSpaceOnUse',
                                   patternTransform: 'rotate(45)' });
  muster.appendChild(_svg('line', { x1: 0, y1: 0, x2: 0, y2: 5,
                                    stroke: '#2d3f57', 'stroke-width': 1.2 }));
  const schnitt = _svg('clipPath', { id: 'grClip' });
  if (g.rumpf) schnitt.appendChild(_svg('path', { d: g.rumpf }));
  defs.append(muster, schnitt);
  ziel.appendChild(defs);

  if (g.rumpf) ziel.appendChild(_svg('path', { d: g.rumpf, fill: '#0f1e30' }));

  // Zwei Ebenen. Was im Boot liegt, wird am Rumpf beschnitten — sonst haengen
  // Moebel in der Luft. Was daneben steht (die Aussenlinie selbst, "Bug" und
  // "Heck") traegt `frei` und darf ueber den Rand hinaus.
  const innen = _svg('g', g.rumpf ? { 'clip-path': 'url(#grClip)' } : {});
  const frei  = _svg('g', {});
  for (const f of (g.hintergrund || [])) {
    const el = _formZeichnen(f);
    if (el) (f.frei ? frei : innen).appendChild(el);
  }
  ziel.append(innen, frei);

  // Die Raeume zuletzt: sie liegen ueber allem und nehmen die Beruehrung an.
  for (const r of (g.raeume || [])) {
    const flaeche = _svg('path', { d: raumPfad(r.form), fill: 'transparent',
                                   class: opts.klasse || 'stauplan-fach' });
    flaeche.dataset.fach = r.id;
    if (opts.klick) {
      flaeche.style.cursor = 'pointer';
      flaeche.addEventListener('click', () => opts.klick(r.id));
    }
    ziel.appendChild(flaeche);
  }
}

/** Kleiner Schiffsriss mit einem hervorgehobenen Ort. Reines SVG, kein Zustand. */
function orteMiniRiss(ortKey, opts = {}) {
  const ort = ORTE[ortKey];
  const hoehe = opts.hoehe || 150;
  const farbe = ort?.color || 'var(--accent)';
  const w = GRUNDRISS.ansicht?.w || 200, h = GRUNDRISS.ansicht?.h || 680;
  const flaeche = ort
    ? `<path d="${_esc(raumPfad(ort.form))}" fill="${_esc(farbe)}" fill-opacity="0.34"
             stroke="${_esc(farbe)}" stroke-width="2.5"/>
       <circle cx="${ort.x + ort.w / 2}" cy="${ort.y + ort.h / 2}" r="9" fill="${_esc(farbe)}"/>`
    : '';
  return `<svg viewBox="0 0 ${w} ${h}" height="${hoehe}" style="display:block;overflow:visible"
               xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path d="${_esc(GRUNDRISS.rumpf || '')}" fill="var(--bg)" stroke="var(--border)" stroke-width="3"/>
    ${Object.values(ORTE).map(o =>
      `<path d="${_esc(raumPfad(o.form))}" fill="var(--surface2)"
             fill-opacity="0.5" stroke="var(--border)" stroke-width="1.4"/>`).join('')}
    ${flaeche}
  </svg>`;
}
