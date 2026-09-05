// ── Display-Konfiguration ───────────────────────────────────────────────────

const _DSP_KEY = 'mave_display_cfg';
// Wird hochgezaehlt, wenn sich die Standard-Aufteilung aendert. Gespeicherte
// Konfigurationen aus einer aelteren Version werden dann EINMALIG verworfen,
// damit die neue Aufteilung auf schon benutzten Geraeten auch ankommt.
const _DSP_VER = 3;

const _TILES = [
  { id: 'battery',  label: 'Batterie',     sizes: ['hidden','normal','half','wide'] },
  { id: 'tanks',    label: 'Tanks',        sizes: ['hidden','normal','half']        },
  { id: 'lights',   label: 'Beleuchtung',  sizes: ['hidden','normal','half','wide'] },
  { id: 'inverter', label: '230V',         sizes: ['hidden','normal','half']        },
  { id: 'wl',       label: 'Wasserstand',  sizes: ['hidden','normal','half']        },
  { id: 'weather',  label: 'Wetter',       sizes: ['hidden','normal','half']        },
  { id: 'heizung',  label: 'Heizung',      sizes: ['hidden','normal','half','wide'] },
  { id: 'wartung',  label: 'Wartungsplan', sizes: ['hidden','normal','half','wide'] },
];

// Selector for each tile element
const _TILE_SEL = {
  battery: '#battCard',
  tanks:   '#tankCard',
  lights:  '#lightsCard',
  inverter:'#inverterCard',
  wl:      '#wlCard',
  weather: '#wxCard',
  heizung: '#heizungCard',
  wartung: '#wartungCard',
};

const _PROFILES = [
  { id: 'kiosk',  label: 'Kiosk (Touch-Display)' },
  { id: 'laptop', label: 'Laptop / Desktop' },
  { id: 'mobile', label: 'Mobil' },
];

// Tile values: 'normal' | 'half' | 'wide' | 'hidden'
// 'wide' = 2 columns, 'half' = reduced height, 'hidden' = not shown
const _DSP_DEFAULTS = {
  activeProfile: 'auto',
  tiles: {
    kiosk:  { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wl:'normal',  weather:'normal', heizung:'normal', wartung:'wide'   },
    laptop: { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wl:'normal',  weather:'normal', heizung:'normal', wartung:'wide'   },
    mobile: { battery:'normal', tanks:'normal', lights:'normal', inverter:'normal', wl:'hidden',  weather:'half',   heizung:'normal', wartung:'hidden' },
  },
};

let _dsp = null;

function _dspLoad() {
  try {
    const raw = localStorage.getItem(_DSP_KEY);
    let saved = raw ? JSON.parse(raw) : {};
    if ((saved.ver ?? 1) < _DSP_VER) saved = { activeProfile: saved.activeProfile,
                                              spalten: saved.spalten,
                                              wandschalter: saved.wandschalter };
    _dsp = { activeProfile: saved.activeProfile ?? _DSP_DEFAULTS.activeProfile,
             spalten: saved.spalten ?? 'auto',
             wandschalter: saved.wandschalter ?? 'auto', tiles: {} };
    // Die Kachelreihenfolge ueberlebt das Neuaufbauen: _dsp wird hier aus den
    // Vorgaben zusammengesetzt, alles Nichtgenannte ginge sonst verloren.
    if (Array.isArray(saved.reihenfolge)) _dsp.reihenfolge = saved.reihenfolge;
    for (const p of _PROFILES) {
      const savedTiles = saved.tiles?.[p.id] ?? {};
      const defTiles   = _DSP_DEFAULTS.tiles[p.id];
      _dsp.tiles[p.id] = {};
      for (const t of _TILES) {
        const sv = savedTiles[t.id];
        if (sv === true)              _dsp.tiles[p.id][t.id] = 'normal'; // migrate old bool
        else if (sv === false)        _dsp.tiles[p.id][t.id] = 'hidden'; // migrate old bool
        else if (typeof sv === 'string') _dsp.tiles[p.id][t.id] = sv;
        else                          _dsp.tiles[p.id][t.id] = defTiles[t.id];
      }
    }
  } catch (_) {
    _dsp = JSON.parse(JSON.stringify(_DSP_DEFAULTS));
  }
}

function _dspSave() {
  if (_dsp) _dsp.ver = _DSP_VER;
  localStorage.setItem(_DSP_KEY, JSON.stringify(_dsp));
}

// ── Benannte Konfigurationen ────────────────────────────────────────────────
const _DSP_CFG_KEY = 'mave_display_configs';

function _dspConfigsLoad() {
  try { return JSON.parse(localStorage.getItem(_DSP_CFG_KEY)) || {}; }
  catch (_) { return {}; }
}
function _dspConfigsSave(obj) {
  localStorage.setItem(_DSP_CFG_KEY, JSON.stringify(obj));
}
function _dspFeedback(msg) {
  const fb = $('dspFeedback');
  if (fb) { fb.textContent = msg; setTimeout(() => { if (fb.textContent === msg) fb.textContent = ''; }, 2500); }
}
function saveDisplayConfig() {
  const inp  = $('dspCfgName');
  const name = (inp?.value || '').trim();
  if (!name) { _dspFeedback('Bitte Namen eingeben'); return; }
  saveDisplaySettings(true);                 // aktuelle Auswahl übernehmen
  const cfgs = _dspConfigsLoad();
  cfgs[name] = JSON.parse(JSON.stringify({ activeProfile: _dsp.activeProfile, tiles: _dsp.tiles }));
  _dspConfigsSave(cfgs);
  openDisplaySettings();
  _dspFeedback('„' + name + '" gespeichert ✓');
}
function loadDisplayConfig(name) {
  const cfg = _dspConfigsLoad()[name];
  if (!cfg) return;
  _dsp = JSON.parse(JSON.stringify(cfg));
  _dspSave();
  applyDisplayConfig();
  openDisplaySettings();
  _dspFeedback('„' + name + '" geladen ✓');
}
function deleteDisplayConfig(name) {
  const cfgs = _dspConfigsLoad();
  delete cfgs[name];
  _dspConfigsSave(cfgs);
  openDisplaySettings();
}

function _dspActiveProfile() {
  if (_dsp.activeProfile !== 'auto') return _dsp.activeProfile;
  return window.innerWidth < 640 ? 'mobile' : 'laptop';
}

// ── Grid-Engine ──────────────────────────────────────────────────────────────
// Eine Basis-Einheit: normale Kachel = Quadrat (Breite == Höhe).
//   normal = 1 Spalte × 2 Zeilen   half = 1 Spalte × 1 Zeile (halbe Höhe)
//   wide   = 2 Spalten × 2 Zeilen (doppelte Breite, gleiche Höhe)
// Zeilenhöhe = (Spaltenbreite − gap) / 2, damit 2 Zeilen exakt 1 Spaltenbreite
// ergeben (Quadrat). grid-auto-flow:dense packt automatisch ohne Lücken oben.

// Fuer welche Kachelbreite die Inhalte gebaut sind. Bei 376 px passt alles;
// darunter faengt es an zu klemmen — die Batteriekachel stellt ihre Werte dann
// untereinander statt nebeneinander und wird zu hoch, Beschriftungen brechen um.
//
// Statt jede Kachel einzeln fuer schmale Bilder umzubauen, wird sie als GANZES
// verkleinert: sie rechnet weiter mit ihren 376 Pixeln und wird beim Zeichnen
// zusammengeschoben. Der Vorschlag kam vom Eigner, und er ist der bessere —
// er loest Breite und Hoehe in einem Zug, und die Kachel sieht aus wie
// entworfen, nur kleiner.
const _KACHEL_ENTWURF_PX = 376;
// Weiter als hierhin nicht: darunter wird es nicht mehr eng, sondern unlesbar.
const _MASSSTAB_MIN = 0.6;

// Ab welcher Kachelbreite es eng wird. Am laufenden System gemessen: bei
// 176 px laufen Batterie-, Wechselrichter- und Lichtkachel ueber ihren Rand,
// ab 205 px nicht mehr. 200 ist die Schwelle mit etwas Luft.
const _KACHEL_ENG_PX = 200;

/** Wie viele Spalten das Raster gerade hat. */
function _cols() {
  // Eine feste Wahl schlaegt die Breite. Grund: das Wandtablet am Kartentisch
  // meldet im Hochformat gut 530 Pixel und bekaeme nach der Breitenregel eine
  // einzige Spalte — die Seite ist damit 1600 Pixel hoch und man scrollt an
  // einem fest montierten Geraet. Mit zwei Spalten sind es 1000, und die
  // Kacheln haben immer noch 243 Pixel. Das ist eine Entscheidung ueber das
  // GERAET, nicht ueber die Breite, und deshalb steht sie in den Einstellungen.
  const fest = parseInt((_dsp && _dsp.spalten) || 'auto', 10);
  if (fest >= 1 && fest <= 4) return fest;
  const w = window.innerWidth;
  if (w < 640)  return 1;
  if (w < 1024) return 2;
  if (w < 1600) return 3;
  return 4;
}

/** Wie breit eine Kachel bei dieser Spaltenzahl waere — fuer die Einstellung. */
function _kachelbreite(spalten) {
  const main = document.querySelector('main');
  if (!main || spalten < 1) return 0;
  const cs = getComputedStyle(main);
  const gap = parseFloat(cs.columnGap) || 10;   // Rueckfall = --gap in style.css
  const innen = main.clientWidth - (parseFloat(cs.paddingLeft) || 0)
                                 - (parseFloat(cs.paddingRight) || 0);
  return Math.round((innen - (spalten - 1) * gap) / spalten);
}

// Kachelelemente einmal merken — _applyGrid läuft bei jedem resize-Frame,
// da sind sieben querySelector-Aufrufe auf dem Pi Zero unnötige Arbeit.
let _tileElCache = {};
function _tileEl(id) {
  if (!_tileElCache[id]) _tileElCache[id] = document.querySelector(_TILE_SEL[id]);
  return _tileElCache[id];
}

// ── Kacheln anordnen ───────────────────────────────────────────────────────
// Die Reihenfolge wird im DOM geaendert, nicht ueber CSS order. Grund: das
// Platzierung (_rasterPacken) setzt gridColumn/gridRow explizit und geht dabei
// von der DOM-Reihenfolge aus — zwei Ordnungssysteme uebereinander
// waeren nicht vorhersagbar. <main> enthaelt ausserdem genau die acht
// Kachel-Sections und sonst nichts, das Umhaengen ist also gefahrlos.

/** Gespeicherte Reihenfolge anwenden. Unbekannte/neue Kacheln bleiben hinten. */
function _kachelOrdnungAnwenden() {
  const main = document.querySelector('main');
  const folge = Array.isArray(_dsp.reihenfolge) ? _dsp.reihenfolge : null;
  if (!main || !folge || !folge.length) return;
  folge.forEach(id => {
    const el = _tileEl(id);
    if (el && el.parentElement === main) main.appendChild(el);
  });
  // Alles, was nicht in der Liste stand, haengt jetzt vorne — ans Ende holen,
  // damit neue Kacheln nach einem Update nicht die Ordnung durcheinanderwerfen.
  _TILES.forEach(t => {
    if (folge.includes(t.id)) return;
    const el = _tileEl(t.id);
    if (el && el.parentElement === main) main.appendChild(el);
  });
  _tilesDomCache = null;          // der Cache haelt die alte Reihenfolge fest
}

/**
 * Aktuelle DOM-Reihenfolge in die Konfiguration uebernehmen.
 *
 * OHNE zu speichern — das ist wichtig: _applyGrid() stellt als Erstes die in
 * _dsp.reihenfolge stehende Ordnung wieder her. Waehrend des Ziehens muss die
 * Konfiguration deshalb der DOM-Reihenfolge FOLGEN, sonst macht jede
 * Neuberechnung den gerade gezogenen Schritt wieder rueckgaengig und die
 * Kachel schnappt zurueck.
 */
function _kachelOrdnungUebernehmen() {
  const main = document.querySelector('main');
  if (!main) return;
  const nachId = {};
  Object.entries(_TILE_SEL).forEach(([id, sel]) => { nachId[sel.replace('#', '')] = id; });
  _dsp.reihenfolge = [...main.children].map(el => nachId[el.id]).filter(Boolean);
  _tilesDomCache = null;
}

/** Wie oben, zusaetzlich dauerhaft sichern. Nur am Ende eines Zuges. */
function _kachelOrdnungMerken() {
  _kachelOrdnungUebernehmen();
  _dspSave();
}

let _ordnenAn = false;
let _zieh = null;               // { el, dx, dy }

function kachelnOrdnenAn(an) {
  _ordnenAn = an === undefined ? !_ordnenAn : !!an;
  document.body.classList.toggle('kacheln-ordnen', _ordnenAn);
  const leiste = $('ordnenLeiste');
  if (leiste) leiste.classList.toggle('hidden', !_ordnenAn);
  if (!_ordnenAn) { _kachelOrdnungMerken(); applyDisplayConfig(); }
}

// Die Reihenfolge, wie sie in index.html steht — EINMAL beim Start gemerkt,
// bevor irgendetwas umgehaengt wird. Sie ist der Ausgangszustand fuer
// "Zuruecksetzen". Aus _TILE_SEL laesst sie sich NICHT ableiten: dort stehen
// die Kacheln in der Reihenfolge der Registrierung, nicht der des Markups.
const _KACHEL_URORDNUNG = (() => {
  const main = document.querySelector('main');
  return main ? [...main.children].map(el => el.id).filter(Boolean) : [];
})();

function kachelnOrdnungZuruecksetzen() {
  delete _dsp.reihenfolge;
  _dspSave();
  const main = document.querySelector('main');
  if (main) _KACHEL_URORDNUNG.forEach(domId => {
    const el = document.getElementById(domId);
    if (el && el.parentElement === main) main.appendChild(el);
  });
  _tilesDomCache = null;
  applyDisplayConfig();
}

// Ziehen per Pointer-Events: funktioniert mit Maus UND auf dem Touchscreen
// am Kartentisch. HTML5-Drag-and-drop kann Touch nicht.
function _ordnenPointerDown(e) {
  if (!_ordnenAn) return;
  const el = e.target.closest('main > .card');
  if (!el) return;
  e.preventDefault();
  const r = el.getBoundingClientRect();
  _zieh = { el, dx: e.clientX - r.left, dy: e.clientY - r.top };
  el.classList.add('kachel-zieht');
  el.setPointerCapture?.(e.pointerId);
}

function _ordnenPointerMove(e) {
  if (!_zieh) return;
  e.preventDefault();
  const { el } = _zieh;
  // Die gezogene Kachel kurz durchlaessig machen, sonst findet elementFromPoint
  // immer nur sie selbst.
  el.style.pointerEvents = 'none';
  const unter = document.elementFromPoint(e.clientX, e.clientY);
  el.style.pointerEvents = '';
  const ziel = unter && unter.closest('main > .card');
  if (!ziel || ziel === el) return;
  const r = ziel.getBoundingClientRect();
  // Vor oder hinter das Ziel — je nachdem, auf welcher Haelfte der Zeiger steht.
  const davor = (e.clientY < r.top + r.height / 2) ||
                (Math.abs(e.clientY - (r.top + r.height / 2)) < 8 && e.clientX < r.left + r.width / 2);
  ziel.parentElement.insertBefore(el, davor ? ziel : ziel.nextSibling);
  _ordnenVorschau();
}

// Live-Vorschau waehrend des Ziehens.
//
// Seit die Platzierung explizit ueber gridColumn/gridRow laeuft, hat das
// Umhaengen im DOM allein KEINE sichtbare Wirkung mehr — die alten Koordinaten
// bleiben stehen, bis neu gepackt wird. Vorher passierte das erst beim
// Loslassen; man sah also nichts, waehrend man zog. Jetzt wird nach jeder
// Umstellung neu gepackt, aber hoechstens einmal je Bild, damit das Packen auf
// dem Pi Zero nicht bei jedem Pointer-Ereignis laeuft.
let _ordnenRaf = null;
function _ordnenVorschau() {
  if (_ordnenRaf !== null) return;
  _ordnenRaf = requestAnimationFrame(() => {
    _ordnenRaf = null;
    // ERST die neue DOM-Reihenfolge in die Konfiguration ziehen, DANN neu
    // rechnen. Andersherum stellt _applyGrid die alte Ordnung wieder her.
    _kachelOrdnungUebernehmen();
    _applyGrid();
  });
}

function _ordnenPointerUp() {
  if (!_zieh) return;
  _zieh.el.classList.remove('kachel-zieht');
  _zieh = null;
  _kachelOrdnungMerken();
  applyDisplayConfig();          // Bandlayout auf die neue Reihenfolge rechnen
}

document.addEventListener('pointerdown', _ordnenPointerDown);
document.addEventListener('pointermove', _ordnenPointerMove);
document.addEventListener('pointerup', _ordnenPointerUp);
document.addEventListener('pointercancel', _ordnenPointerUp);
// Im Ordnen-Modus darf ein Tap keine Detailseite oeffnen.
document.addEventListener('click', e => {
  if (_ordnenAn && e.target.closest('main > .card')) {
    e.preventDefault(); e.stopPropagation();
  }
}, true);

/** Kachel liegt zwar in der Konfiguration, ist aber mangels Daten versteckt.
 *  tanks.js/battery.js setzen dafür style.display; .tile-nodata ist der
 *  gleichwertige Weg ueber eine Klasse. Beides zaehlt hier als "nicht da". */
function _tileOhneDaten(el) {
  return el.style.display === 'none' || el.classList.contains('tile-nodata');
}

/** Kacheln in DOM-Reihenfolge, nicht in _TILES-Reihenfolge.
 *  Die Karten stehen in index.html anders als in _TILES (Batterie, 230V,
 *  Tanks, Licht, Pegel, Wetter, Wartung). Das Raster verteilt bei gleichem
 *  order-Wert aber nach DOM-Reihenfolge — rechnete die Bandaufteilung nach
 *  _TILES, bekam die falsche Kachel die Restzelle und rechts blieb eine
 *  ganze Spalte leer (z. B. Beleuchtung auf 'breit' bei 3 Spalten).
 *  Die Karten sind fest in index.html, die Reihenfolge wird einmal gemerkt. */
let _tilesDomCache = null;
function _tilesInDomOrder() {
  if (_tilesDomCache) return _tilesDomCache;
  const da = _TILES.filter(t => _tileEl(t.id));
  if (da.length !== _TILES.length) return da;      // noch nicht alles im DOM
  _tilesDomCache = da.sort((a, b) => {
    const pos = _tileEl(a.id).compareDocumentPosition(_tileEl(b.id));
    return (pos & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1;
  });
  return _tilesDomCache;
}

/**
 * Verteilt eine Kachelgruppe bandweise auf `cols` Spalten und schliesst dabei
 * die Lücken. Ein Band ist eine Reihe von `cols` Spalten; volle Kacheln sind
 * `rowSpan` Zeilen hoch. 'wide' will zwei Spalten, alles andere eine.
 * Passt eine breite Kachel nicht mehr ins laufende Band, fängt sie ein neues
 * an — die Restzellen des alten Bandes bekommen dann die Kacheln davor, damit
 * mitten im Raster kein Loch stehen bleibt. Bleiben im letzten Band Zellen
 * frei, wird nur eine breite Kachel aufgezogen (sie ist ohnehin als Banner
 * gedacht); normale Kacheln bleiben dort quadratisch.
 */
/**
 * Kacheln in der gewuenschten Reihenfolge ins Raster setzen.
 *
 * Vorher trennte _bandLayout in zwei Baender: erst alle vollhohen Kacheln,
 * dann alle halben. Das vermied Loecher, machte die Reihenfolge aber nur
 * INNERHALB einer Groesse aenderbar — eine halbe Kachel liess sich nicht
 * dorthin ziehen, wo eine normale steht, und zwei halbe nicht untereinander
 * legen.
 *
 * Jetzt wird tatsaechlich gepackt: jede Kachel kommt der Reihe nach in den
 * ERSTEN freien Platz, in den sie passt (zeilenweise von links oben gesucht).
 * Damit gilt die Reihenfolge ueber alle Groessen hinweg, und es entstehen
 * trotzdem keine Loecher — was nicht in eine angefangene Zeile passt, rutscht
 * in die naechste, und kleinere Kacheln fuellen die Luecken davor auf.
 *
 * Groessen in Rastereinheiten (eine Zeile = halbe Kachelhoehe):
 *   normal  1 Spalte x 2 Zeilen      half  1 x 1      wide  2 x 2
 */
/**
 * Wie viele Rasterzeilen der Inhalt einer Kachel WIRKLICH braucht.
 *
 * Die Zeilenhoehe haengt an der Spaltenbreite: eine normale Kachel ist ein
 * Quadrat, also zwei Zeilen à (Spaltenbreite − Abstand) / 2. Das geht auf,
 * solange eine Spalte breit genug ist.
 *
 * Auf dem Wandtablet im Hochformat sind es bei zwei Spalten 243 Pixel — und
 * damit ist eine Zeile nur noch 113 hoch. Die Batteriekachel braucht bei
 * dieser Breite aber 359 Pixel: was auf einem breiten Bild nebeneinander
 * steht, steht hier untereinander. Sie stand einfach ueber ihren Rand hinaus
 * und ins Feld darunter. Genau das war "klappt so maessig".
 *
 * Deshalb wird gemessen statt gerechnet. Die Kachel bekommt dafuer kurz ihre
 * natuerliche Hoehe (`align-self: start` hebt das Strecken auf die Zeilenhoehe
 * auf), und daraus ergibt sich, wie viele Zeilen sie belegen muss. Die
 * eingestellte Groesse bleibt die UNTERGRENZE: eine Kachel wird nie kleiner
 * als gewuenscht, nur bei Bedarf hoeher.
 */
function _zeilenBedarf(el, mindest, rowH, gap, massstab) {
  if (!rowH) return mindest;
  const merk = { h: el.style.height, as: el.style.alignSelf, gr: el.style.gridRow };
  // Damit die Messung nicht von der aktuellen Platzierung abhaengt: erst die
  // Zeilenbindung loesen, sonst streckt das Raster die Kachel weiter.
  el.style.gridRow = '';
  el.style.alignSelf = 'start';
  el.style.height = 'auto';
  // scrollHeight zaehlt in den EIGENEN Einheiten der Kachel — die sind bei
  // verkleinerter Darstellung groesser als die Pixel auf dem Schirm. Erst mit
  // dem Massstab multipliziert wird daraus die Hoehe, die sie wirklich einnimmt.
  const noetig = el.scrollHeight * (massstab || 1);
  el.style.height = merk.h;
  el.style.alignSelf = merk.as;
  el.style.gridRow = merk.gr;
  // n Zeilen decken n*rowH plus (n−1) Abstaende dazwischen ab.
  const zeilen = Math.ceil((noetig + gap) / (rowH + gap));
  // Nach oben begrenzt: eine Kachel, die aus irgendeinem Grund riesig meldet,
  // soll nicht das halbe Raster belegen.
  return Math.max(mindest, Math.min(8, zeilen));
}

function _rasterPacken(kacheln, cols, rowH, gap, massstab) {
  const belegt = [];                       // belegt[zeile][spalte]
  const frei = (r, c, w, h) => {
    if (c + w > cols) return false;
    for (let y = r; y < r + h; y++)
      for (let x = c; x < c + w; x++)
        if (belegt[y]?.[x]) return false;
    return true;
  };
  const setzen = (r, c, w, h) => {
    for (let y = r; y < r + h; y++) {
      belegt[y] = belegt[y] || [];
      for (let x = c; x < c + w; x++) belegt[y][x] = true;
    }
  };

  // Erst alle Hoehen messen, dann platzieren. Getrennt, weil das Messen die
  // Zeilenbindung kurz loest — mitten im Packen waere das ein Durcheinander.
  for (const k of kacheln) {
    k.w = (k.sz === 'wide') ? Math.min(2, cols) : 1;
    k.hMin = (k.sz === 'half') ? 1 : 2;
    k.h = _zeilenBedarf(k.el, k.hMin, rowH, gap, massstab);
  }

  const platziert = [];
  for (const k of kacheln) {
    const w = k.w;
    const h = k.h;
    let r = 0, c = 0;
    sucher: for (r = 0; r < 200; r++) {          // 200 Zeilen sind reichlich
      for (c = 0; c <= cols - w; c++) if (frei(r, c, w, h)) break sucher;
    }
    setzen(r, c, w, h);
    k.r = r; k.c = c; k.w = w; k.h = h;
    platziert.push(k);
  }

  // KEIN Wachsen mehr — weder in der Hoehe noch in der Breite.
  //
  // Frueher wurden Loecher gestopft, indem der Nachbar groesser gemacht wurde.
  // Damit stand eine Kachel in den Einstellungen auf "normal" und war im
  // Raster doppelt so breit oder anderthalbmal so hoch. Fuer beides gibt es
  // eigene Groessen ("Doppelte Breite", "Halbe Hoehe") — eine Kachel, die
  // sich selbst umdimensioniert, macht die Einstellung wertlos.
  //
  // Was hier steht, ist jetzt genau das, was eingestellt ist:
  //   half = 1x1, normal = 1x2, wide = 2x2 Rastereinheiten.
  // Bleibt beim Mischen von Groessen ein Loch, bleibt es sichtbar. Wer es
  // wegbekommen will, aendert die Reihenfolge oder eine Groesse.

  for (const k of platziert) {
    k.el.style.gridColumn = `${k.c + 1} / span ${k.w}`;
    k.el.style.gridRow    = `${k.r + 1} / span ${k.h}`;
    k.el.style.order      = '';                 // Platzierung ist jetzt explizit
  }
}

function _applyGrid() {
  if (!_dsp) _dspLoad();
  const main = document.querySelector('main');
  if (!main) return;

  const profile = _dspActiveProfile();
  const tileCfg = _dsp.tiles[profile] ?? {};
  const cols    = _cols();

  main.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;

  let rowH = 0, gap = 10, massstab = 1;        // Rueckfall = --gap in style.css
  if (cols === 1) {
    main.style.gridAutoRows = 'auto';
  } else {
    const cs    = getComputedStyle(main);
    gap         = parseFloat(cs.columnGap)    || 10;
    const padL  = parseFloat(cs.paddingLeft)  || 0;
    const padR  = parseFloat(cs.paddingRight) || 0;
    const inner = main.clientWidth - padL - padR;
    const colW  = (inner - (cols - 1) * gap) / cols;
    rowH        = Math.max(70, (colW - gap) / 2);
    main.style.gridAutoRows = rowH + 'px';
    // Ist die Spalte schmaler als der Entwurf, wird die ganze Kachel kleiner
    // gezeichnet — Inhalt eingeschlossen. `zoom` und nicht `transform`: zoom
    // wirkt auf die Anordnung, die Kachel RECHNET also mit ihrer vollen Breite
    // und passt anschliessend in die schmale Spalte. Ein transform wuerde sie
    // nur optisch schrumpfen und darunter weiter zu gross bleiben.
    massstab = Math.min(1, Math.max(_MASSSTAB_MIN, colW / _KACHEL_ENTWURF_PX));
  }

  _kachelOrdnungAnwenden();     // gespeicherte Reihenfolge VOR der Verteilung

  // Erst einsammeln, wer ueberhaupt im Raster liegt — danach platzieren.
  // NICHT mehr nach Groesse trennen: die Reihenfolge des Eigners gilt ueber
  // alle Groessen hinweg, _rasterPacken vermeidet die Loecher stattdessen
  // durch echtes Packen.
  const kacheln = [];
  for (const t of _tilesInDomOrder()) {
    const el = _tileEl(t.id);
    if (!el) continue;
    const sz = tileCfg[t.id] ?? 'normal';
    if (sz === 'hidden') {
      // Klasse mit !important — schlägt inline display:'' aus Daten-Updates
      // (updateBattery/updateTanks), damit ausgeblendet wirklich weg bleibt.
      el.classList.add('tile-hidden');
      el.style.gridColumn = el.style.gridRow = el.style.order = '';
      continue;
    }
    el.classList.remove('tile-hidden');
    // ACHTUNG: hier NICHT el.style.display zurücksetzen. Das hat früher das
    // datengetriebene Verstecken aus tanks.js/battery.js bei jedem resize
    // wieder aufgerissen — die leere Tank-Kachel blitzte dann kurz auf.
    if (cols === 1 || _tileOhneDaten(el)) {
      el.style.gridColumn = el.style.gridRow = el.style.order = '';
      el.style.zoom = '';
      continue;
    }
    // Der Massstab MUSS vor dem Messen stehen: die Hoehe, die eine Kachel
    // braucht, haengt an der Breite, mit der sie rechnet.
    el.style.zoom = (cols > 1 && massstab < 1) ? String(massstab.toFixed(4)) : '';
    kacheln.push({ el, sz });
  }

  if (cols > 1) _rasterPacken(kacheln, cols, rowH, gap, massstab);

  _gridSig = _gridVisSig();
  if (!_gridWatchOn) _gridWatchInit();
}

// ── Sichtbarkeitswechsel aus den Datenmodulen nachziehen ─────────────────────
// tanks.js/battery.js blenden ihre Kachel aus, sobald keine Werte mehr
// anliegen. Damit aendert sich die Kachelzahl im Raster; ohne erneuten Lauf
// bliebe an ihrer Stelle eine Lücke stehen. Der Beobachter meldet sich zwar
// bei jedem Datensatz (die Module schreiben style.display jedes Mal neu),
// rechnet aber nur weiter, wenn sich die Sichtbarkeit wirklich geändert hat.

let _gridSig      = '';
let _gridWatchOn  = false;
let _gridWatchRaf = null;

function _gridVisSig() {
  let s = '';
  for (const t of _TILES) {
    const el = _tileEl(t.id);
    s += !el ? '-' : (_tileOhneDaten(el) || el.classList.contains('tile-hidden')) ? '0' : '1';
  }
  return s;
}

function _gridWatchInit() {
  if (typeof MutationObserver !== 'function') return;
  const mo = new MutationObserver(() => {
    if (_gridVisSig() === _gridSig || _gridWatchRaf) return;
    _gridWatchRaf = requestAnimationFrame(() => { _gridWatchRaf = null; _applyGrid(); });
  });
  let beobachtet = 0;
  for (const t of _TILES) {
    const el = _tileEl(t.id);
    if (el) { mo.observe(el, { attributes: true, attributeFilter: ['style', 'class'] }); beobachtet++; }
  }
  if (beobachtet) _gridWatchOn = true;
}

let _resizeRaf = null;
window.addEventListener('resize', () => {
  if (_resizeRaf) cancelAnimationFrame(_resizeRaf);
  _resizeRaf = requestAnimationFrame(() => {
    _applyGrid();
    if (typeof updateWartungHomeTile === 'function') updateWartungHomeTile();
    if (typeof _renderBattWideChart === 'function') _renderBattWideChart(true);
    if (typeof chNamenPassend === 'function') chNamenPassend();
  });
});

// ── Kachelsichtbarkeit & Größe anwenden ────────────────────────────────────

function applyDisplayConfig() {
  if (!_dsp) _dspLoad();
  const profile = _dspActiveProfile();
  const tileCfg = _dsp.tiles[profile] ?? {};

  // Die Groessenklassen gelten NUR im mehrspaltigen Raster. In der
  // einspaltigen Ansicht stehen die Kacheln untereinander und sind
  // inhaltsgross — "halbe Hoehe" hat dort keine Bedeutung mehr, richtet aber
  // Schaden an: .tile--half.card-tanks nimmt den Tankbalken ihre Mindesthoehe
  // (style.css), und ohne eine feste Zeilenhoehe, gegen die flex:1 sich
  // strecken koennte, fielen die Balken auf zwei Pixel zusammen. Die Tanks
  // waren dann faktisch unsichtbar.
  const einspaltig = _cols() <= 1;
  // Marke fuers Stylesheet: nur im mehrspaltigen Raster haben die Kacheln eine
  // feste Zeilenhoehe, gegen die Inhalte schrumpfen duerfen.
  document.body.classList.toggle('raster-mehrspaltig', !einspaltig);
  for (const t of _TILES) {
    const el = _tileEl(t.id);
    if (!el) continue;
    const sz = tileCfg[t.id] ?? 'normal';
    el.classList.toggle('tile--half', !einspaltig && sz === 'half');
    el.classList.toggle('tile--wide', !einspaltig && sz === 'wide');
  }

  _applyGrid();

  if (profile === 'kiosk') {
    document.body.classList.add('kiosk-mode');
    _kioskNavInit();
  } else {
    document.body.classList.remove('kiosk-mode');
  }

  // Der Schalter „Bildschirm an" haengt am Profil und an der Uebersteuerung —
  // beides kann sich hier gerade geaendert haben.
  if (typeof _wandKnopfSetzen === 'function') _wandKnopfSetzen();

  // Größenabhängige Inhalte neu rendern
  requestAnimationFrame(() => {
    if (typeof updateWartungHomeTile === 'function') updateWartungHomeTile();
    if (typeof _renderBattWideChart === 'function') _renderBattWideChart(true);
    if (typeof chNamenPassend === 'function') chNamenPassend();
  });
}

// ── Kiosk Bottom-Nav ────────────────────────────────────────────────────────

const _KIOSK_TABS = [
  {
    id: 'home', label: 'Übersicht',
    icon: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    action() { _closeAllOverlays(); _kioskSetActive('home'); },
  },
  {
    id: 'energie', label: 'Energie',
    icon: '<rect x="2" y="7" width="18" height="11" rx="2"/><path d="M22 11v3"/><path d="M7 7V4"/><path d="M11 7V4"/>',
    action() { openBattDetail(); _kioskSetActive('energie'); },
  },
  {
    id: 'licht', label: 'Licht',
    icon: '<circle cx="12" cy="12" r="5"/><path d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>',
    action() { openLightDetail(); _kioskSetActive('licht'); },
  },
  {
    id: 'wasser', label: 'Wasser',
    icon: '<path d="M12 2C6 9 4 13 4 16a8 8 0 0 0 16 0c0-3-2-7-8-14z"/>',
    action() { openWaterLevel(); _kioskSetActive('wasser'); },
  },
  {
    id: 'system', label: 'System',
    icon: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    action() { openSettings(); _kioskSetActive('system'); },
  },
];

let _kioskReady = false;

function _kioskNavInit() {
  if (_kioskReady) return;
  _kioskReady = true;
  const side = $('kioskSidebar');
  if (side) {
    side.innerHTML = _KIOSK_TABS.map(t => `
      <button class="kiosk-side-btn" data-kt="${t.id}" onclick="_kioskTab('${t.id}')">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${t.icon}</svg>
        <span>${t.label}</span>
      </button>`).join('');
  }
  _kioskSetActive('home');
  _kioskQuickInit();
}

function _kioskTab(id) {
  const tab = _KIOSK_TABS.find(t => t.id === id);
  if (tab) tab.action();
}

function _kioskSetActive(id) {
  document.querySelectorAll('.kiosk-side-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.kt === id)
  );
}

// ── Slide-down Topbar mit Quick-Einstellungen ───────────────────────────────

function _kioskToggleTop() {
  const t = $('kioskTop');
  if (!t) return;
  t.classList.toggle('open');
  if (t.classList.contains('open')) _kioskInvSync();
}

async function _kioskQuickInit() {
  const q = $('ktpQuick');
  if (!q) return;
  let cap = { power_available: false, brightness_available: false, brightness: 50 };
  try { cap = await fetch('/api/display/state').then(r => r.json()); } catch (_) {}
  const bri = cap.brightness ?? 50;

  q.innerHTML = `
    <div class="ktp-tile">
      <span class="ktp-tile-lbl">Inverter</span>
      <button class="ktp-toggle" id="ktpInvBtn" onclick="_kioskQuickInverter()">–</button>
    </div>
    <div class="ktp-tile${cap.power_available ? '' : ' disabled'}">
      <span class="ktp-tile-lbl">Display</span>
      <button class="ktp-toggle" id="ktpDispBtn" ${cap.power_available ? '' : 'disabled'} onclick="_kioskDisplayOff()">Ausschalten</button>
    </div>
    <div class="ktp-tile${cap.brightness_available ? '' : ' disabled'}">
      <span class="ktp-tile-lbl">Helligkeit${cap.brightness_available ? '' : ' · n/v'}</span>
      <input type="range" class="ktp-range" min="10" max="100" value="${bri}"
        ${cap.brightness_available ? '' : 'disabled'} oninput="_kioskBrightness(this.value)">
    </div>
    <div class="ktp-tile disabled">
      <span class="ktp-tile-lbl">Gas · bald</span>
      <button class="ktp-toggle" disabled>—</button>
    </div>
  `;
  _kioskInvSync();
}

function _kioskInvSync() {
  const btn = $('ktpInvBtn');
  if (!btn) return;
  const on = (typeof _invCurrentState !== 'undefined') &&
             (_invCurrentState === 'Aktiv' || _invCurrentState === 'Eco');
  btn.textContent = on ? 'An' : 'Aus';
  btn.classList.toggle('on', on);
}

function _kioskQuickInverter() {
  if (typeof toggleInverter === 'function') toggleInverter();
  setTimeout(_kioskInvSync, 60);
}

function _kioskDisplayOff() {
  fetch('/api/display/power', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on: false }),
  }).then(r => { if (!r.ok) _toast('Display-Steuerung (noch) nicht verfügbar'); })
    .catch(() => _toast('Display-Steuerung (noch) nicht verfügbar'));
}

let _briTimer = null;
function _kioskBrightness(v) {
  clearTimeout(_briTimer);
  _briTimer = setTimeout(() => {
    fetch('/api/display/brightness', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: +v }),
    }).catch(() => {});
  }, 140);
}

// Aktualisiert die Mini-Batterie in der Slide-down Topbar (vom Batterie-Update).
function _kioskUpdateBatt(soc) {
  const fill = $('ktpBattFill'), pct = $('ktpBattPct'), wrap = $('ktpBatt');
  if (!fill) return;
  if (soc == null) { fill.setAttribute('width', '0'); if (pct) pct.textContent = '--%'; return; }
  const v = Math.max(0, Math.min(100, soc));
  fill.setAttribute('width', String((v / 100 * 16).toFixed(1)));
  const color = v >= 50 ? 'var(--green)' : v >= 20 ? 'var(--yellow)' : 'var(--red)';
  if (wrap) wrap.style.color = color;
  if (pct)  pct.textContent = Math.round(v) + '%';
}

// Mini-Toast
let _toastTimer = null;
function _toast(msg) {
  let el = $('miniToast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'miniToast'; el.className = 'mini-toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  requestAnimationFrame(() => el.classList.add('show'));
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

// ── Display-Einstellungen ───────────────────────────────────────────────────

const _SIZE_OPTS = [
  { value: 'hidden', label: 'Ausgeblendet' },
  { value: 'normal', label: 'Normal'       },
  { value: 'half',   label: 'Halbe Höhe'  },
  { value: 'wide',   label: 'Doppelte Breite' },
];

// ── Vollbild ────────────────────────────────────────────────────────────────
// Bewusst je GERAET und nicht im Manifest: display:fullscreen im Manifest
// gaelte fuer jede neue Installation und fuer alle — das Wandtablet soll aber
// Vollbild, das Telefon in der Hosentasche nicht. Deshalb steht das Manifest
// weiter auf standalone, und hier entscheidet jedes Geraet fuer sich
// (localStorage, wie die uebrigen Anzeige-Einstellungen auch).
//
// Die Umschaltung braucht eine Beruehrung: von sich aus darf keine Seite in
// den Vollbild gehen. Der Wunsch wird gemerkt und beim naechsten Tippen
// wieder hergestellt, weil ein Neuladen den Vollbild verlaesst und man ihn
// nicht selbst zurueckholen darf.
const _VOLLBILD_KEY = 'mave_vollbild';

function _vollbildAktiv() {
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

/**
 * Laeuft der Bildschirm gerade ohne Browserleisten?
 *
 * Das ist NICHT dasselbe wie `_vollbildAktiv()`. Es gibt zwei Wege dorthin, und
 * nur der eine ist an der Fullscreen-API abzulesen:
 *
 *   1. Der Schalter im Menue holt sich den Vollbild ueber `requestFullscreen()`.
 *      Dann steht `document.fullscreenElement`.
 *   2. Die installierte Wandfassung startet ohne Leisten, weil ihr Manifest
 *      `display: fullscreen` sagt. Die API war daran nie beteiligt —
 *      `document.fullscreenElement` bleibt LEER, obwohl der Bildschirm voll ist.
 *
 * Fall 2 kostete den Eigner eine Meldung: die Schaltflaeche „Vollbild
 * wiederherstellen" stand dauerhaft unten auf der Seite und bot an, einen
 * Zustand herzustellen, der laengst da war.
 */
function _ohneLeisten() {
  if (_vollbildAktiv()) return true;
  return typeof anzeigeArt === 'function' && anzeigeArt() === 'fullscreen';
}

async function vollbildUmschalten() {
  try {
    if (_vollbildAktiv()) {
      localStorage.setItem(_VOLLBILD_KEY, '0');
      await (document.exitFullscreen?.() ?? document.webkitExitFullscreen?.());
    } else {
      localStorage.setItem(_VOLLBILD_KEY, '1');
      const el = document.documentElement;
      await (el.requestFullscreen?.({ navigationUI: 'hide' })
             ?? el.webkitRequestFullscreen?.());
    }
  } catch (e) {
    // Manche Browser lehnen ab (kein Nutzergriff, in einem Rahmen, iOS).
    // Dann bleibt es beim jetzigen Zustand — nur nicht behaupten, es klappte.
    console.debug('Vollbild nicht moeglich:', e);
  }
  _vollbildKnopfSetzen();
  _vollbildPilleSetzen();
}

function _vollbildKnopfSetzen() {
  // Der Schalter sitzt im Menue (core.js). Das baut seinen Inhalt bei jedem
  // Oeffnen neu, die Beschriftung stimmt dort also von selbst — hier wird sie
  // nur nachgezogen, falls das Menue offen steht, waehrend der Vollbild anders
  // beendet wird (Zurueck-Geste, Escape).
  const menue = document.getElementById('burgerMenu');
  if (!menue || menue.classList.contains('hidden')) return;
  if (typeof burgerBauen === 'function') burgerBauen();
}

/** Nach einem Neuladen den Wunsch wieder herstellen — auf Nachfrage.
 *
 *  Vorher hing das am ersten Tippen IRGENDWO auf der Seite. Das war bequem
 *  gedacht und in der Hand unangenehm: der erste Griff gilt fast immer einem
 *  Knopf, und dann geschahen zwei Dinge auf einmal — der Knopf tat, was er
 *  sollte, und darunter sprang die Seite in den Vollbild, wobei sich das
 *  ganze Bild verschob. Wer nur das Konto-Menue oeffnen wollte, bekam einen
 *  Vollbild dazu, den er nicht verlangt hatte.
 *
 *  Ein Griff darf nur eine Sache tun. Deshalb steht der Wunsch jetzt als
 *  kleine Schaltflaeche da und wartet, statt sich den naechsten Griff zu
 *  nehmen, der vorbeikommt.
 */
function _vollbildFuehren() {
  document.addEventListener('fullscreenchange', _vollbildKnopfSetzen);
  document.addEventListener('fullscreenchange', _vollbildPilleSetzen);
  _vollbildPilleSetzen();
}

function _vollbildPilleSetzen() {
  const gewuenscht = localStorage.getItem(_VOLLBILD_KEY) === '1';
  // `_ohneLeisten` und nicht `_vollbildAktiv`: in der installierten
  // Wandfassung ist der Bildschirm voll, ohne dass die Fullscreen-API je im
  // Spiel war. Sonst stuende hier dauerhaft ein Angebot, das nichts zu bieten
  // hat.
  const zeigen = gewuenscht && !_ohneLeisten();
  let pille = document.getElementById('vollbildPille');
  if (!zeigen) { pille?.remove(); return; }
  if (pille) return;
  pille = document.createElement('div');
  pille.id = 'vollbildPille';
  pille.innerHTML =
    '<button type="button" class="vp-haupt" onclick="vollbildUmschalten()">'
    + 'Vollbild wiederherstellen</button>'
    + '<button type="button" class="vp-weg" onclick="vollbildNichtMehr()" '
    + 'title="Nicht mehr danach fragen">Nicht mehr</button>';
  document.body.appendChild(pille);
}

/** Der Wunsch war einmal da und ist es nicht mehr. */
function vollbildNichtMehr() {
  localStorage.setItem(_VOLLBILD_KEY, '0');
  _vollbildPilleSetzen();
}

// ── Zwei Finger: Spalten statt Zoom ────────────────────────────────────────
// Der Browser-Zoom ist abgeschaltet (siehe viewport in index.html) — auf einem
// fest montierten Tablet ist er ein Ärgernis, nicht ein Werkzeug. Die Geste
// selbst bleibt aber sinnvoll, sie meint ja "größer" und "kleiner". Genau das
// tut sie jetzt: auseinander = weniger Spalten (größere Kacheln), zusammen =
// mehr Spalten.
//
// Die Wahl landet in derselben Einstellung wie unter "Anzeige" — es gibt also
// nur EINEN Ort, an dem die Spaltenzahl steht, und die Geste ist eine
// Abkürzung dorthin.

const _GESTE_SCHWELLE = 1.25;   // ab 25 % Abstandsänderung ist es gemeint

// Ab welcher Fensterbreite die Geste überhaupt etwas bewirkt.
//
// Am laufenden System gemessen: bei zwei Spalten laufen unterhalb von rund
// 205 px je Kachel Inhalte über ihren Rand, und die Kacheln werden dafür schon
// auf 60 % verkleinert. Auf einem Telefon (390–430 px Fensterbreite) bleiben
// zwei Spalten damit unter jeder brauchbaren Grösse — die Geste tut dort so,
// als gäbe es eine Wahl, die es nicht gibt.
//
// 480 px: darunter ist Schluss. Das trifft Telefone im Hochformat und lässt
// Tablets ab 8 Zoll durch.
const _GESTE_MIN_BREITE = 480;

// Und mehr als zwei Spalten macht die Geste nicht. Drei oder vier sind eine
// Entscheidung für einen grossen Bildschirm — die trifft man einmal in den
// Einstellungen und nicht im Vorbeiwischen.
const _GESTE_MAX_SPALTEN = 2;

// Der Abstand beim Aufsetzen und der zuletzt gemessene. Ausgewertet wird erst
// beim Loslassen — waehrend des Spreizens wuerde es sonst mehrfach schalten.
let _gesteAnfang = 0, _gesteJetzt = 0;

function _fingerAbstand(t) {
  return Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
}

function _spaltenGeste(richtung) {
  if (!_dsp) _dspLoad();
  if (window.innerWidth < _GESTE_MIN_BREITE) {
    // Nicht schweigen: die Geste war Absicht, und seit der Browser-Zoom aus ist
    // passiert sonst gar nichts — man haelt es für einen Fehler.
    _toast('Zu schmal für zwei Spalten');
    return;
  }
  const jetzt = _cols();
  const ziel = Math.max(1, Math.min(_GESTE_MAX_SPALTEN, jetzt + richtung));
  if (ziel === jetzt) {
    _toast(richtung > 0 ? 'Mehr Spalten gehen nicht' : 'Weniger Spalten gehen nicht');
    return;
  }
  _dsp.spalten = String(ziel);
  _dspSave();
  applyDisplayConfig();
  _toast(ziel === 1 ? 'Eine Spalte' : ziel + ' Spalten');
}

document.addEventListener('touchstart', e => {
  if (e.touches.length === 2) {
    _gesteAnfang = _fingerAbstand(e.touches);
    _gesteJetzt = _gesteAnfang;
  }
}, { passive: true });

document.addEventListener('touchmove', e => {
  if (e.touches.length === 2 && _gesteAnfang) _gesteJetzt = _fingerAbstand(e.touches);
}, { passive: true });

document.addEventListener('touchend', () => {
  // Ausgewertet wird, sobald der ERSTE Finger geht — dann ist die Geste vorbei.
  //
  // Vorher stand hier "nur wenn beide Finger weg sind" (e.touches.length > 0
  // → abbrechen und den Anfangsabstand verwerfen). Ein Mensch hebt die Finger
  // aber nacheinander: beim ersten Loslassen ist noch einer da, der
  // Anfangsabstand wurde weggeworfen, und beim zweiten war nichts mehr zum
  // Rechnen da. Die Geste hat deshalb nie ausgelöst. Im Test fiel es nicht auf,
  // weil der beide Finger gleichzeitig hob — was niemand tut.
  if (_gesteAnfang < 20) { _gesteAnfang = 0; return; }
  const v = _gesteJetzt / _gesteAnfang;
  _gesteAnfang = 0;
  if (v >= _GESTE_SCHWELLE) _spaltenGeste(-1);          // auseinander → größer
  else if (v <= 1 / _GESTE_SCHWELLE) _spaltenGeste(+1); // zusammen → kleiner
}, { passive: true });

// Bricht das System die Berührung ab (Anruf, Benachrichtigung), soll nichts
// hängenbleiben und beim nächsten Tippen losgehen.
document.addEventListener('touchcancel', () => { _gesteAnfang = 0; }, { passive: true });

function openDisplaySettings() {
  if (!_dsp) _dspLoad();
  const pane = $('setPane-display');
  if (!pane) return;

  const sizeSelect = (pid, t, cur) => {
    const opts = _SIZE_OPTS.filter(o => (t.sizes ?? _SIZE_OPTS.map(x => x.value)).includes(o.value));
    return `<select class="settings-input" id="dsp_${pid}_${t.id}" style="max-width:180px;cursor:pointer">
      ${opts.map(o => `<option value="${o.value}"${cur === o.value ? ' selected' : ''}>${o.label}</option>`).join('')}
    </select>`;
  };

  const cfgs  = _dspConfigsLoad();
  const names = Object.keys(cfgs);
  const esc   = s => s.replace(/'/g, "\\'");
  const cfgList = names.length
    ? names.map(n => `
        <div class="settings-row" style="align-items:center">
          <label class="settings-label" style="min-width:0;flex:1">${n}</label>
          <span style="display:flex;gap:8px">
            <button class="btn-secondary" onclick="loadDisplayConfig('${esc(n)}')">Laden</button>
            <button class="btn-secondary" onclick="deleteDisplayConfig('${esc(n)}')" style="color:var(--red)">Löschen</button>
          </span>
        </div>`).join('')
    : '<div style="font-size:12px;color:var(--text3);padding:4px 0">Noch keine Konfiguration gespeichert.</div>';

  pane.innerHTML = `
    <div class="set-card">
      <div class="set-card-hd">Kacheln anordnen</div>
      <div class="settings-row" style="align-items:center;border-bottom:none">
        <label class="settings-label" style="min-width:0;flex:1">Reihenfolge der Kacheln
          auf der Startseite per Ziehen ändern</label>
        <span style="display:flex;gap:8px">
          <button class="btn-secondary" onclick="kachelnOrdnungZuruecksetzen()">Zurücksetzen</button>
          <button class="btn-primary" onclick="closeSettings();kachelnOrdnenAn(true)">Anordnen</button>
        </span>
      </div>
    </div>

    <div class="set-card">
      <div class="set-card-hd">Konfigurationen</div>
      ${cfgList}
      <div class="settings-row" style="align-items:center;gap:10px;border-bottom:none;padding-top:12px">
        <input class="settings-input" id="dspCfgName" placeholder="Name der Konfiguration…" style="flex:1;max-width:none">
        <button class="btn-secondary" onclick="saveDisplayConfig()">Aktuelle speichern</button>
      </div>
    </div>

    <div class="set-card">
      <div class="set-card-hd">Geräte-Profil</div>
      <div class="settings-row">
        <label class="settings-label">Aktives Profil</label>
        <select class="settings-input" id="dspProfileSel" style="max-width:220px;cursor:pointer">
          <option value="auto">Automatisch</option>
          <option value="kiosk">Kiosk (Touch-Display)</option>
          <option value="laptop">Laptop / Desktop</option>
          <option value="mobile">Mobil</option>
        </select>
      </div>
      <div style="font-size:12px;color:var(--text3);margin-top:4px">
        Automatisch: bei Breite &lt;640 px → Mobil, sonst Laptop.
        Kiosk aktiviert die Bottom-Navigation.
      </div>

      <div class="settings-row" style="margin-top:12px">
        <label class="settings-label">Spalten</label>
        <select class="settings-input" id="dspSpaltenSel" style="max-width:220px;cursor:pointer"
                onchange="_spaltenHinweis()">
          <option value="auto">Automatisch (nach Breite)</option>
          <option value="1">1</option>
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
        </select>
      </div>
      <div style="font-size:12px;color:var(--text3);margin-top:4px" id="dspSpaltenHinweis"></div>

      <div class="settings-row" style="margin-top:12px">
        <label class="settings-label">Schalter „Bildschirm an"</label>
        <select class="settings-input" id="dspWandSel" style="max-width:220px;cursor:pointer">
          <option value="auto">Automatisch (fest montierte Geräte)</option>
          <option value="immer">Immer zeigen</option>
          <option value="nie">Nie zeigen</option>
        </select>
      </div>
      <div style="font-size:12px;color:var(--text3);margin-top:4px">
        Hält den Bildschirm wach, solange die Seite offen ist. Ein Tablet ist
        vom Telefon technisch nicht zu unterscheiden — automatisch heißt daher:
        Berührbildschirm ab 480&nbsp;px Breite oder Kiosk-Profil. Der Nachtmodus
        steht immer in der Kopfzeile.
      </div>
    </div>

    <div class="set-card">
      <div class="set-card-hd">Vollbild am Wandtablet</div>
      <div id="dspWandStand"></div>
    </div>

    ${_PROFILES.map(p => `
      <div class="set-card">
        <div class="set-card-hd">Kacheln — ${p.label}</div>
        ${_TILES.map(t => `
          <div class="settings-row" style="align-items:center">
            <label class="settings-label" for="dsp_${p.id}_${t.id}">${t.label}</label>
            ${sizeSelect(p.id, t, _dsp.tiles[p.id]?.[t.id] ?? 'normal')}
          </div>
        `).join('')}
      </div>
    `).join('')}

    <div class="settings-actions">
      <span class="settings-feedback" id="dspFeedback"></span>
      <button class="btn-primary" onclick="saveDisplaySettings()">Speichern &amp; anwenden</button>
    </div>
  `;

  $('dspProfileSel').value = _dsp.activeProfile;
  $('dspSpaltenSel').value = _dsp.spalten || 'auto';
  $('dspWandSel').value = _dsp.wandschalter || 'auto';
  _dspWandStand();
  _spaltenHinweis();
}

/**
 * Wie die Anwendung auf DIESEM Geraet gerade laeuft — und was noch fehlt.
 *
 * Ohne diese Zeile war der Zustand nicht feststellbar: /wand sieht im Browser
 * genau aus wie die normale Seite, und ob die Installation geglueckt ist,
 * merkte man erst beim naechsten Sperren des Bildschirms.
 */
function _dspWandStand() {
  const feld = $('dspWandStand');
  if (!feld || typeof wandLage !== 'function') return;
  const lage = wandLage();
  const knopf = lage.kannInstallieren
    ? '<button class="btn-primary" style="margin-top:10px" onclick="wandInstallieren()">'
      + 'Als Vollbild-App installieren</button>'
    : (lage.gut ? ''
       : '<div style="font-size:12px;color:var(--text3);margin-top:10px">Dieser '
         + 'Browser bietet die Installation nicht von sich aus an — dann über '
         + 'sein Menü „Zum Startbildschirm hinzufügen“, aufgerufen unter '
         + '<a href="/wand" style="color:var(--accent)">/wand</a>.</div>');
  feld.innerHTML = `
    <div class="settings-row" style="border-bottom:none;padding-bottom:6px">
      <span class="settings-label">Läuft gerade</span>
      <b style="color:${lage.gut ? 'var(--green)' : 'var(--yellow)'}">${lage.text}</b>
    </div>
    <div style="font-size:12px;color:var(--text3);line-height:1.55">
      ${lage.folge}
      ${lage.gut ? '' : ' Den Vollbild gewährt nur ein Fingergriff — keine Seite '
        + 'darf ihn sich selbst zurückholen. Dauerhaft geht es nur über eine '
        + 'eigene Installation mit Vollbild im Manifest. Die bisherige '
        + 'Installation bleibt daneben bestehen.'}
    </div>
    ${knopf}`;
}

/** Was die gewaehlte Spaltenzahl auf DIESEM Bildschirm bedeutet.
 *
 *  Eine Zahl ohne Folge ist eine Zumutung: "3 Spalten" sagt niemandem, ob das
 *  hier noch lesbar ist. Deshalb steht daneben, wie breit eine Kachel damit
 *  waere — und ab wann es zu eng wird.
 */
function _spaltenHinweis() {
  const feld = $('dspSpaltenHinweis'), wahl = $('dspSpaltenSel');
  if (!feld || !wahl) return;
  const gewaehlt = wahl.value === 'auto' ? _cols() : parseInt(wahl.value, 10);
  const breite = _kachelbreite(gewaehlt);
  const mass = gewaehlt > 1
    ? Math.min(1, Math.max(_MASSSTAB_MIN, breite / _KACHEL_ENTWURF_PX)) : 1;
  const kleiner = mass < 0.995
    ? ` Die Kacheln werden dafür auf <b>${Math.round(mass * 100)} %</b> verkleinert — `
      + 'samt Inhalt, damit nichts umbricht oder abgeschnitten wird.' : '';
  feld.innerHTML = (wahl.value === 'auto'
    ? `Hier ergibt das zurzeit <b>${gewaehlt}</b> Spalte${gewaehlt === 1 ? '' : 'n'} `
      + `à ${breite} px. Dreht man das Gerät, ändert sich das mit.`
    : `Feste Wahl: ${breite} px je Kachel. Gilt nur auf diesem Gerät.`) + kleiner
    + (mass <= _MASSSTAB_MIN + 0.001
       ? ' <span style="color:var(--yellow)">Kleiner geht es nicht — hier wird es unlesbar.</span>' : '');
}

function saveDisplaySettings(silent) {
  _dsp.activeProfile = $('dspProfileSel').value;
  _dsp.spalten = ($('dspSpaltenSel') || {}).value || 'auto';
  _dsp.wandschalter = ($('dspWandSel') || {}).value || 'auto';
  for (const p of _PROFILES) {
    for (const t of _TILES) {
      const el = $(`dsp_${p.id}_${t.id}`);
      if (el) _dsp.tiles[p.id][t.id] = el.value;
    }
  }
  _dspSave();
  applyDisplayConfig();
  if (!silent) _dspFeedback('Gespeichert ✓');
}


// ── Statusleiste ────────────────────────────────────────────────────────────
// Fuellt die dichte Kernwert-Zeile ueber den Kacheln. Wird aus handleData()
// heraus bei jedem State-Update gerufen. Schreibt ausschliesslich per
// textContent — kein innerHTML, damit das auf dem Pi Zero billig bleibt.

let _sbWartung = null;   // { overdue, total } — von wartung.js gemeldet

function _sbSet(id, txt) {
  const el = document.getElementById(id);
  if (el && el.textContent !== txt) el.textContent = txt;
}

function _sbState(itemId, cls) {
  const el = document.getElementById(itemId);
  if (!el) return;
  const item = el.closest('.sb-item');
  if (!item) return;
  item.classList.remove('sb-ok', 'sb-low', 'sb-warn', 'sb-idle', 'sb-stale');
  if (cls) item.classList.add(cls);
}

const _n = (v, d = 0) => (v == null || !isFinite(v)) ? null : Number(v).toFixed(d);

// ── Hintergrundgraphen der Statusleiste ─────────────────────────────────────
// Jedes Feld zeigt den Verlauf der letzten 24 Stunden als Flaeche hinter dem
// Text. Die Daten kommen fertig verdichtet vom Server (60 Stuetzstellen je
// Reihe, /api/statusleiste/verlauf) — der Browser rechnet nur noch Pfade.
const SPARK_H = 32;          // Hoehe des Zeichenraums (viewBox, nicht Pixel)
let _sparkDaten = null;

/** Pfade fuer EINEN zusammenhaengenden Abschnitt ohne Luecken. */
function _sparkAbschnitt(werte, von, bis, tief, spanne, n) {
  const x = i => (n > 1 ? (i / (n - 1)) * 100 : 50);
  // Der Hoechstwert der Skala liegt auf der OBERKANTE des Feldes: bei einem
  // Prozentwert heisst das, die Hoehe der Flaeche ist der Fuellstand. Nur
  // eine halbe Einheit Luft oben, damit die Linie nicht halb abgeschnitten
  // wird — sie ist einen Bildpunkt breit.
  const y = v => (SPARK_H - 0.5) - ((v - tief) / spanne) * (SPARK_H - 0.5);
  let linie = '', flaeche = `M ${x(von).toFixed(2)} ${SPARK_H}`;
  for (let i = von; i <= bis; i++) {
    const px = x(i).toFixed(2), py = y(werte[i]).toFixed(2);
    linie   += (i === von ? 'M ' : ' L ') + px + ' ' + py;
    flaeche += ` L ${px} ${py}`;
  }
  flaeche += ` L ${x(bis).toFixed(2)} ${SPARK_H} Z`;
  return { linie, flaeche };
}

/**
 * @param opt.tief    feste Untergrenze der Skala (undefined = aus den Daten)
 * @param opt.hoch    feste Obergrenze der Skala  (undefined = aus den Daten)
 * @param opt.spanne  Mindestspanne, wenn eine Grenze aus den Daten kommt
 */
function _sparkSvg(werte, opt, id) {
  const echt = werte.filter(v => typeof v === 'number');
  if (echt.length < 2) return '';        // ein Punkt ist kein Verlauf
  const dTief = Math.min(...echt), dHoch = Math.max(...echt);
  const mind  = opt.spanne || 1;
  let tief, spanne;
  if (opt.tief !== undefined && opt.hoch !== undefined) {
    // Feste Skala, z. B. 0..100 %. Die Hoehe der Flaeche IST dann der Wert —
    // nicht die zufaellige Streuung der letzten 24 Stunden.
    tief = opt.tief; spanne = Math.max(opt.hoch - opt.tief, 1e-6);
  } else if (opt.tief !== undefined) {
    // Untergrenze fest (z. B. 0 W), Obergrenze aus den Daten. Die
    // Mindestobergrenze verhindert, dass 5 W Ladung das Feld fuellen.
    tief = opt.tief; spanne = Math.max(dHoch - opt.tief, mind);
  } else {
    // Beides aus den Daten: Hoechstwert an die Oberkante, Mindestspanne
    // mittig verteilt, damit ein fast waagerechter Verlauf nicht zum
    // Gebirge aufgeblasen wird.
    spanne = Math.max(dHoch - dTief, mind);
    tief = dTief - (spanne - (dHoch - dTief)) / 2;
  }
  const basis = tief;
  const n = werte.length;
  let linien = '', flaechen = '';
  let i = 0;
  while (i < n) {
    if (typeof werte[i] !== 'number') { i++; continue; }
    let j = i;
    while (j + 1 < n && typeof werte[j + 1] === 'number') j++;
    if (j > i) {                          // Luecken bleiben Luecken
      const a = _sparkAbschnitt(werte, i, j, basis, spanne, n);
      linien += a.linie; flaechen += ` ${a.flaeche}`;
    }
    i = j + 1;
  }
  if (!flaechen) return '';
  return `<svg viewBox="0 0 100 ${SPARK_H}" preserveAspectRatio="none" focusable="false">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="currentColor" stop-opacity=".22"/>
      <stop offset=".85" stop-color="currentColor" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${flaechen.trim()}" fill="url(#${id})"/>
    <path d="${linien}" fill="none" stroke="currentColor" stroke-opacity=".3"
      stroke-width="1" vector-effect="non-scaling-stroke"
      stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

function _sparkZeichnen() {
  const serien = _sparkDaten?.serien;
  if (!serien) return;
  document.querySelectorAll('.sb-spark').forEach(el => {
    const reihe = el.dataset.reihe;
    const werte = serien[reihe];
    const zahl = a => (el.dataset[a] === undefined || el.dataset[a] === ''
      ? undefined : +el.dataset[a]);
    const neu = Array.isArray(werte)
      ? _sparkSvg(werte, { tief: zahl('tief'), hoch: zahl('hoch'),
                           spanne: +el.dataset.spanne || 1 }, 'spk_' + reihe) : '';
    // Nur schreiben, wenn sich wirklich etwas geaendert hat: der Streifen wird
    // bei jedem Broadcast angefasst, und innerHTML kostet jedes Mal Umbruch —
    // auf dem anzeigenden Geraet, nicht auf dem Pi.
    if (el.innerHTML !== neu) el.innerHTML = neu;
  });
}

async function ladeSparklines() {
  try {
    const r = await fetch('/api/statusleiste/verlauf');
    if (!r.ok) return;
    _sparkDaten = await r.json();
    _sparkZeichnen();
  } catch (_) {
    // Ohne Verlauf bleibt der Hintergrund leer — die Werte stehen trotzdem da.
  }
}

function updateStatusBar(data) {
  if (!data || !document.getElementById('statusBar')) return;

  _sbRenderBatterie(data);

  _sbRenderLaden(data);

  _sbRenderTank(data);

  // Der Pegel stand hier bis v1.54.0. An seiner Stelle steht jetzt die Heizung
  // — der Wasserstand hat eine eigene Seite und ist dort vollstaendiger.
  _sbRenderHeizung();
  _sbRenderInternet();
  _sbRenderWartung();
}

// ── Halten oeffnet die Detailseite ──────────────────────────────────────────
// Die Felder der Leiste schalten beim Tippen durch — damit war der kurze Weg
// zur Detailseite weg, den einige von ihnen vorher hatten. Er kommt hier
// zurueck, und zwar fuer ALLE Felder, zu denen es eine Seite gibt: zwei
// Sekunden halten.
//
// Dieselbe Geste wie beim Relais in der Lichtkachel, mit derselben Wartezeit
// und demselben mitlaufenden Streifen. Eine Bedienung, die man einmal lernt,
// soll ueberall dasselbe bedeuten.

// Eine Sekunde. Zwei waren gemessen zu lang — man haelt den Finger drauf und
// glaubt, es passiere nichts.
const _SB_HALTEN_MS = 1000;
let _sbHalten = null;          // {uhr, feld, gehalten}

function _sbHaltenBinden() {
  const leiste = document.getElementById('statusBar');
  if (!leiste || leiste.dataset.haltenBereit) return;
  leiste.dataset.haltenBereit = '1';

  leiste.addEventListener('pointerdown', e => {
    const feld = e.target.closest('.sb-item');
    const ziel = feld?.dataset.detail;
    if (!ziel) return;
    // Der Streifen entsteht erst beim ersten Halten — die meisten Felder
    // werden nie gehalten, und ein leeres Element je Feld waere Ballast.
    let streifen = feld.querySelector('.sb-halten');
    if (!streifen) {
      streifen = document.createElement('span');
      streifen.className = 'sb-halten';
      feld.appendChild(streifen);
    }
    streifen.style.transition = `width ${_SB_HALTEN_MS}ms linear`;
    streifen.style.width = '100%';
    feld.setPointerCapture?.(e.pointerId);
    _sbHalten = { feld, gehalten: false, uhr: setTimeout(() => {
      _sbHalten && (_sbHalten.gehalten = true);
      _sbHaltenLoesen(feld);
      if (navigator.vibrate) navigator.vibrate(20);
      const fn = window[ziel];
      if (typeof fn === 'function') fn();
    }, _SB_HALTEN_MS) };
  });

  const beenden = () => {
    if (!_sbHalten) return;
    clearTimeout(_sbHalten.uhr);
    _sbHaltenLoesen(_sbHalten.feld);
    // Nach einem Halten NICHT auch noch weiterschalten: der Klick kommt
    // unmittelbar nach dem Loslassen, und beides zugleich waere Unsinn.
    if (_sbHalten.gehalten) _sbKlickSchlucken = true;
    _sbHalten = null;
  };
  leiste.addEventListener('pointerup', beenden);
  leiste.addEventListener('pointercancel', beenden);
  leiste.addEventListener('pointerleave', beenden);

  leiste.addEventListener('click', e => {
    if (!_sbKlickSchlucken) return;
    _sbKlickSchlucken = false;
    e.stopPropagation();
    e.preventDefault();
  }, true);
}

let _sbKlickSchlucken = false;

function _sbHaltenLoesen(feld) {
  const streifen = feld?.querySelector('.sb-halten');
  if (!streifen) return;
  streifen.style.transition = 'width .15s ease';
  streifen.style.width = '0%';
}

// ── Batterie-Feld: Service und Starter ──────────────────────────────────────
// Beim Starter gibt es nur die Spannung — er haengt an einem einfachen
// Spannungseingang, kein Shunt, also kein Ladestand und kein Strom. Genau
// deshalb wechselt hier auch die Einheit und die Skala des Graphen: 0..100 %
// waere fuer 12 Volt keine Skala, sondern ein flacher Strich am Boden.

const _SB_BATT = [
  // "Service" und nicht "Batterie": daneben steht der Starter, und der ist
  // auch eine Batterie. Der Name muss sagen, WELCHE.
  { schluessel: 'service', label: 'Service', einheit: '%', reihe: 'soc',
    tief: '0', hoch: '100' },
  // 11,5 bis 14,5 V deckt vom tiefentladenen Bleiakku bis zur Ladeschlusssp.
  // alles ab, was an einem Starter vorkommt.
  { schluessel: 'starter', label: 'Starter',  einheit: 'V', reihe: 'starter',
    tief: '11.5', hoch: '14.5' },
];
let _sbBattIdx = 0;

function sbBattWeiter() {
  _sbBattIdx = (_sbBattIdx + 1) % _SB_BATT.length;
  const wahl = _SB_BATT[_sbBattIdx];
  const spark = document.querySelector('#sbBattItem .sb-spark');
  if (spark) {
    spark.dataset.reihe = wahl.reihe;
    spark.dataset.tief  = wahl.tief;
    spark.dataset.hoch  = wahl.hoch;
    if (typeof _sparkZeichnen === 'function') _sparkZeichnen();
  }
  if (typeof _lastData !== 'undefined' && _lastData) _sbRenderBatterie(_lastData);
}

function _sbRenderBatterie(data) {
  const b = data.battery || {};
  const wahl = _SB_BATT[_sbBattIdx];
  _sbSet('sbBattLbl', wahl.label);
  _sbSet('sbBattUnit', wahl.einheit);

  if (wahl.schluessel === 'starter') {
    const v = _n(b.starter_voltage, 2);
    _sbSet('sbSoc', v ?? '--');
    // Kein Strom, kein Ladestand — und deshalb hier auch keine zweite Zeile,
    // die so tut, als gaebe es noch etwas. Das Leerzeichen haelt die Hoehe.
    _sbSet('sbBattSub', v == null ? 'keine Daten' : '\u00a0');
    // Schwellen fuer einen 12-V-Starter in Ruhe: unter 12,0 ist er leer,
    // unter 12,4 angeknabbert.
    _sbState('sbSoc', v == null ? 'sb-idle'
      : v < 12.0 ? 'sb-warn' : v < 12.4 ? 'sb-low' : 'sb-ok');
    return;
  }

  _sbSet('sbSoc', _n(b.soc) ?? '--');
  const volt = _n(b.voltage, 2), cur = _n(b.current, 1);
  _sbSet('sbBattSub', (volt == null && cur == null) ? 'keine Daten'
    : `${volt ?? '--'} V · ${cur != null && cur > 0 ? '+' : ''}${cur ?? '--'} A`);
  _sbState('sbSoc', b.soc == null ? 'sb-idle'
    : b.soc < 30 ? 'sb-warn' : b.soc < 50 ? 'sb-low' : 'sb-ok');
}

// ── Tank-Feld: Tanks durchschalten ──────────────────────────────────────────
// Ein Tipp wechselt den Tank. Vorher stand hier fest das Frischwasser und der
// Tipp schaltete zwischen Liter und Prozent um — die Umschaltung sitzt jetzt
// nur noch auf der Tankkachel, und die Leiste folgt ihr.
//
// Die Liste wird bei jedem Zeichnen aus den Presets gebildet und nicht fest
// verdrahtet: kommt ein dritter Tank dazu, faehrt er von selbst mit.

let _sbTankIdx = 0;

/** Welche Tanks es gibt, in der Reihenfolge der Presets. */
function _sbTanks() {
  const cfg = (typeof tanksConfig === 'object' && tanksConfig) || {};
  return Object.keys(cfg).filter(k => /^tank\d+$/.test(k)).sort();
}

function sbTankWeiter() {
  const tanks = _sbTanks();
  if (tanks.length < 2) return;            // nichts zum Durchschalten
  _sbTankIdx = (_sbTankIdx + 1) % tanks.length;
  // Der Graph zeigt ab jetzt denselben Tank wie die Zahl.
  const spark = document.querySelector('#sbTankItem .sb-spark');
  if (spark) {
    spark.dataset.reihe = tanks[_sbTankIdx];
    if (typeof _sparkZeichnen === 'function') _sparkZeichnen();
  }
  if (typeof _lastData !== 'undefined' && _lastData) _sbRenderTank(_lastData);
}

function _sbRenderTank(data) {
  const tanks = _sbTanks();
  if (!tanks.length) return;
  if (_sbTankIdx >= tanks.length) _sbTankIdx = 0;
  const key = tanks[_sbTankIdx];
  const c   = ((typeof tanksConfig === 'object' && tanksConfig) || {})[key] || {};
  const pct = (data.tanks || {})[key];

  _sbSet('sbT1Lbl', c.name || key);
  // Liter oder Prozent — dieselbe Umschaltung wie auf der Tankkachel
  // (tankShowLiters in tanks.js).
  const inLiter = (typeof tankShowLiters !== 'undefined') && tankShowLiters;
  if (pct != null && inLiter && c.capacity_l) {
    _sbSet('sbT1', String(Math.round(pct * c.capacity_l / 100)));
    _sbSet('sbT1Unit', 'L');
    _sbSet('sbT1Sub', `${Math.round(pct)} % von ${c.capacity_l} L`);
  } else {
    _sbSet('sbT1', _n(pct) ?? '--');
    _sbSet('sbT1Unit', '%');
    _sbSet('sbT1Sub', (pct != null && c.capacity_l)
      ? `${Math.round(pct * c.capacity_l / 100)} von ${c.capacity_l} L`
      : (pct == null ? 'keine Daten' : ''));
  }
  // Der Zustand haengt immer am Prozentwert, egal welche Einheit dasteht.
  _sbState('sbT1', pct == null ? 'sb-idle'
    : pct < 15 ? 'sb-warn' : pct < 30 ? 'sb-low' : 'sb-ok');

  // Waagerechter Balken: die Breite IST der Fuellstand, die Farbe die des
  // Tanks. Sie steht in den Presets und wird auf der Kachel schon benutzt —
  // hier stand bisher immer derselbe Akzent, und Diesel sah aus wie Wasser.
  const bar = document.getElementById('sbT1Bar');
  if (bar) {
    bar.style.width = pct == null ? '0%'
      : Math.max(0, Math.min(100, pct)).toFixed(1) + '%';
    // Ohne eigene Farbe bleibt es beim Zustandsfarbton aus dem Stylesheet.
    bar.style.color = c.color || '';
  }
  // Die Zahl auch. Ein blauer Balken unter einer gruenen Zahl sind zwei
  // Aussagen ueber denselben Tank.
  const zahl = document.getElementById('sbT1');
  if (zahl) zahl.style.color = c.color || '';
  // Mehrere Tanks: sagen, dass hier etwas zu tippen ist.
  const feld = document.getElementById('sbTankItem');
  if (feld) {
    const mehr = _sbTanks().length > 1;
    feld.title = mehr ? 'Tippen: nächster Tank' : (c.name || '');
    feld.style.cursor = mehr ? 'pointer' : 'default';
  }
}

// ── Laden-Feld: Quellen durchschalten ───────────────────────────────────────
// Ein Tipp wechselt die Quelle. Gezeigt werden Leistung jetzt und die in den
// letzten 24 Stunden geladenen Amperestunden — die Zahl, die am Ende zaehlt.
// 'reihe' benennt die Serie des Hintergrundgraphen, 'ah' den Schluessel in
// der Ah-Bilanz vom Server.
const _SB_LADEN = [
  { schluessel: 'gesamt', label: 'Laden',     reihe: 'laden',   ah: 'gesamt'  },
  { schluessel: 'charger', label: 'Landstrom', reihe: 'charger', ah: 'charger' },
  { schluessel: 'solar',   label: 'Solar',     reihe: 'solar1',  ah: 'solar'   },
  { schluessel: 'orion',   label: 'Orion',     reihe: 'orion',   ah: 'orion'   },
];
let _sbLadenIdx = 0;

function sbLadenWeiter() {
  _sbLadenIdx = (_sbLadenIdx + 1) % _SB_LADEN.length;
  // Der Graph zeigt ab jetzt dieselbe Quelle wie die Zahl.
  const spark = document.querySelector('#sbChgItem .sb-spark');
  if (spark) {
    spark.dataset.reihe = _SB_LADEN[_sbLadenIdx].reihe;
    if (typeof _sparkZeichnen === 'function') _sparkZeichnen();
  }
  // Sofort neu zeichnen statt auf den naechsten Broadcast zu warten —
  // _lastData haelt den letzten Zustand (ws.js).
  if (typeof _lastData !== 'undefined' && _lastData) _sbRenderLaden(_lastData);
}

function _sbRenderLaden(data) {
  const w = q => {
    const v = data?.[q]?.power;
    return (typeof v === 'number') ? v : null;
  };
  const einzeln = { charger: w('charger'), solar: w('solar'), orion: w('orion') };
  const aktiv = _SB_LADEN[_sbLadenIdx];

  let leistung;
  if (aktiv.schluessel === 'gesamt') {
    // Gesamt heisst wirklich gesamt: auch die Lichtmaschine zaehlt mit, auch
    // wenn sie kein eigenes Feld im Durchschalten hat.
    const alle = [einzeln.charger, einzeln.solar, einzeln.orion, w('alternator')]
      .filter(v => v != null && v > 1);
    leistung = alle.length ? alle.reduce((a, b) => a + b, 0) : 0;
  } else {
    leistung = einzeln[aktiv.schluessel];
  }

  // Einheit folgt der Batteriekachel: dort steht der Schalter Ah/Wh, und zwei
  // verschiedene Masse nebeneinander auf einem Bildschirm waeren Unsinn.
  const inAh = (typeof _battEnergyUnit !== 'undefined') && _battEnergyUnit === 'ah';
  const spannung = (typeof _lastBattery !== 'undefined' && _lastBattery?.voltage) || null;

  _sbSet('sbChgLbl', aktiv.label);
  const einheitEl = document.querySelector('#sbChgItem .sb-val i');

  // Die Einheit haengt am gewaehlten Mass und an der Spannung — NICHT daran,
  // ob diese eine Quelle gerade etwas liefert.
  //
  // Vorher stand beides in derselben Bedingung. Meldete das Landstromgeraet
  // keine Leistung (also immer, wenn kein Kabel dranhaengt), fiel der Zweig
  // durch und die Einheit sprang auf Watt, obwohl auf dem ganzen Rest des
  // Bildschirms Ampere stand. Ohne Spannung wird weiter nicht gerechnet: dann
  // ist Watt richtig, weil Ampere geraten waere.
  const inA = inAh && spannung > 1;
  _sbSet('sbChg', leistung == null ? '--'
    : inA ? (leistung / spannung).toFixed(1) : String(Math.round(leistung)));
  if (einheitEl) einheitEl.textContent = inA ? 'A' : 'W';

  // Untere Zeile: geladene Menge der letzten 24 Stunden, in derselben Einheit.
  // Fehlt die Bilanz noch (erster Abruf laeuft), lieber nichts behaupten.
  let menge = '';
  if (inAh) {
    const ah = _sparkDaten?.ah24?.[aktiv.ah];
    if (ah != null) menge = `${ah.toFixed(1)} Ah / 24 h`;
  } else {
    const wh = _sparkDaten?.wh24?.[aktiv.ah];
    if (wh != null) {
      menge = (wh >= 1000 ? (wh / 1000).toFixed(2) + ' kWh' : Math.round(wh) + ' Wh') + ' / 24 h';
    }
  }
  if (aktiv.schluessel === 'gesamt' && !(leistung > 1)) {
    _sbSet('sbChgSub', menge || (data?.inverter?.state === 'Aktiv'
      ? 'Inverter an' : 'keine Quelle'));
  } else {
    _sbSet('sbChgSub', menge || (leistung == null ? 'keine Daten' : ''));
  }
  _sbState('sbChg', leistung == null ? 'sb-idle'
    : leistung > 1 ? 'sb-ok' : 'sb-idle');
}

/**
 * Internet-Feld der Statusleiste.
 *
 * Gezeigt wird die Antwortzeit, nicht der Durchsatz: der liegt im Leerlauf
 * bei fast null und saehe dann nach kaputter Leitung aus, obwohl alles geht.
 * Laeuft die Verbindung ueber Mobilfunk, gibt es keine Antwortzeit — dann
 * steht dort die Signalstaerke.
 *
 * Die Daten kommen aus _connData (connectivity.js, eigener Poller), deshalb
 * wird diese Funktion von dort ebenfalls aufgerufen.
 */
function _sbRenderInternet() {
  const d = (typeof _connData !== 'undefined') ? _connData : null;
  const r = d?.router, sl = d?.starlink;
  const einheit = document.getElementById('sbNetUnit');
  const setz = (wert, e, sub, zustand) => {
    _sbSet('sbNet', wert);
    if (einheit) einheit.textContent = e;
    _sbSet('sbNetSub', sub);
    _sbState('sbNet', zustand);
  };

  if (!r) { setz('--', '', 'keine Daten', 'sb-idle'); return; }

  // Kabel am WAN heisst an diesem Boot: Starlink.
  if (r.active_type === 'wired') {
    if (sl?.state === 'CONNECTED') {
      const ping = (typeof sl.ping_ms === 'number') ? Math.round(sl.ping_ms) : null;
      setz(ping != null ? String(ping) : '--', 'ms',
           sl.obstructed ? 'Starlink · Sicht frei?' : 'Starlink',
           sl.obstructed ? 'sb-low' : 'sb-ok');
    } else {
      setz('--', '', sl?.state ? `Starlink ${sl.state.toLowerCase()}` : 'Starlink still',
           'sb-warn');
    }
    return;
  }

  if (r.active_type === 'mobile') {
    const m = r.mobile || {};
    const pct = (typeof m.signal_pct === 'number') ? m.signal_pct : null;
    setz(pct != null ? String(pct) : '--', pct != null ? '%' : '',
         [m.operator, m.ntype].filter(Boolean).join(' · ') || 'Mobilfunk',
         m.state !== 'Connected' ? 'sb-warn' : pct != null && pct < 25 ? 'sb-low' : 'sb-ok');
    return;
  }

  // Weder Kabel noch Mobilfunk aktiv — aber es gibt noch das Bordnetz selbst.
  setz('--', '', r.wired_up || r.mobile_up ? 'kein Uplink' : 'offline', 'sb-warn');
}

/**
 * Heizungsfeld der Statusleiste: Vorlauftemperatur als Zahl, darunter das
 * gewaehlte Preset und die Betriebsart.
 *
 * Eigene Funktion, weil die Heizung an einem eigenen Poller haengt (alle 6 s,
 * heizung.js) und nicht am WebSocket. Sie wird deshalb von BEIDEN Seiten
 * aufgerufen: hier bei jedem Broadcast und in ladeHeizung(), sobald neue
 * Heizungsdaten da sind. Sonst haette das Feld je nach Reihenfolge bis zu
 * sechs Sekunden alte Werte gezeigt.
 */
// ── Heizungs-Feld: Heizung, Vorlauf, Raeume ─────────────────────────────────
// Die Liste der Raeume steht nicht hier, sie kommt vom Hub: er weiss, welche
// Fuehler angelernt sind, und meldet sie mitsamt Namen. Kommt ein Raumknoten
// dazu, faehrt er von selbst mit; wird einer entfernt, verschwindet er.
//
// Raeume, die gerade nichts senden, bleiben in der Liste und zeigen "--" mit
// dem Vermerk "offline". Sie zu ueberspringen hiesse, einen ausgefallenen
// Fuehler dadurch zu verstecken, dass er ausgefallen ist.

let _sbHzIdx = 0;

/** Was sich im Heizungsfeld durchschalten laesst — Heizung, Vorlauf, Raeume. */
function _sbHzSchritte() {
  const d = (typeof _hzDaten !== 'undefined') ? _hzDaten : null;
  const schritte = [
    { art: 'heizung', label: 'Heizung', einheit: '%',
      reihe: 'heizleistung', tief: '0', spanne: '20' },
    { art: 'vorlauf', label: 'Vorlauf', einheit: '°C',
      reihe: 'vorlauf', spanne: '4' },
  ];
  for (const r of (d?.state?.rooms || [])) {
    schritte.push({ art: 'raum', raum: r, label: r.name || ('Raum ' + r.id),
                    einheit: '°C', reihe: 'raum' + r.id, spanne: '4' });
  }
  return schritte;
}

function sbHeizungWeiter() {
  const schritte = _sbHzSchritte();
  if (schritte.length < 2) return;
  _sbHzIdx = (_sbHzIdx + 1) % schritte.length;
  const wahl = schritte[_sbHzIdx];
  const spark = document.querySelector('#sbHzItem .sb-spark');
  if (spark) {
    spark.dataset.reihe  = wahl.reihe;
    spark.dataset.spanne = wahl.spanne;
    // tief nur setzen, wo es eine feste Untergrenze gibt — bei Temperaturen
    // waere eine Null am Boden verschenkte Hoehe.
    if (wahl.tief !== undefined) spark.dataset.tief = wahl.tief;
    else delete spark.dataset.tief;
    if (typeof _sparkZeichnen === 'function') _sparkZeichnen();
  }
  _sbRenderHeizung();
}

function _sbRenderHeizung() {
  const d = (typeof _hzDaten !== 'undefined') ? _hzDaten : null;
  const st = d?.state, h = st?.heater;
  const erreichbar = d && d.enabled && d.configured && d.reachable !== false;

  const schritte = _sbHzSchritte();
  if (_sbHzIdx >= schritte.length) _sbHzIdx = 0;
  const wahl = schritte[_sbHzIdx];
  _sbSet('sbHzLbl', wahl.label);
  _sbSet('sbHzUnit', wahl.einheit);

  if (!d || !d.configured) {
    _sbSet('sbHz', '--');
    _sbSet('sbHzSub', 'nicht eingerichtet');
    _sbState('sbHz', 'sb-idle');
    return;
  }
  if (!erreichbar || !h) {
    _sbSet('sbHz', '--');
    _sbSet('sbHzSub', 'nicht erreichbar');
    _sbState('sbHz', 'sb-warn');
    return;
  }

  if (wahl.art === 'raum') {
    // Der Raum aus der frischen Liste, nicht der beim Umschalten gemerkte:
    // zwischen zwei Tipps koennen neue Werte gekommen sein.
    const r = (st.rooms || []).find(x => x.id === wahl.raum.id) || wahl.raum;
    const still = r.conn !== 'online';
    const temp = (typeof r.roomTemp === 'number') ? r.roomTemp : null;
    _sbSet('sbHz', temp != null ? temp.toFixed(1) : '--');
    _sbSet('sbHzSub', still ? 'offline'
      : (typeof r.target === 'number' ? `Soll ${r.target.toFixed(1)} °C` : '\u00a0'));
    _sbState('sbHz', still || temp == null ? 'sb-idle'
      : r.wantsHeat ? 'sb-ok' : null);
    return;
  }

  if (wahl.art === 'vorlauf') {
    _sbSet('sbHz', h.flowTemp != null ? String(Math.round(h.flowTemp)) : '--');
    _sbSet('sbHzSub', h.flowTemp == null ? 'keine Daten'
      : (h.boiler?.active ? 'Kessel läuft' : 'Kessel aus'));
    _sbState('sbHz', h.errorCode ? 'sb-warn'
      : h.state && h.state !== 'off' ? 'sb-ok' : 'sb-idle');
    return;
  }

  // Heizung: was das Geraet TUT. Der Vorlauf hat jetzt einen eigenen Schritt.
  _sbSet('sbHz', h.powerLevel != null ? String(Math.round(h.powerLevel)) : '--');
  // Preset und Betriebsart in einer Zeile. Ohne Preset steht nur die
  // Betriebsart da, statt eines fuehrenden Trennzeichens.
  const preset = st.preset?.name;
  const modus  = (typeof HZ_MODUS === 'object' && HZ_MODUS[h.mode]) || h.mode || '';
  _sbSet('sbHzSub', [preset, modus].filter(Boolean).join(' · ') || '--');

  // Farbe nach dem, was das Geraet TUT: laeuft es, ist das Feld gruen; steht
  // es bereit, bleibt es ruhig; eine Stoerung faerbt rot.
  _sbState('sbHz',
    h.errorCode ? 'sb-warn'
    : h.state && h.state !== 'off' ? 'sb-ok'
    : 'sb-idle');
}

/** Schreibt NUR die beiden Wartungsfelder — laesst den Rest der Leiste in Ruhe. */
function _sbRenderWartung() {
  if (!_sbWartung) return;
  _sbSet('sbWart', String(_sbWartung.overdue));
  _sbSet('sbWartSub', _sbWartung.overdue === 0 ? 'alles aktuell' : 'überfällig');
  _sbState('sbWart', _sbWartung.overdue > 0 ? 'sb-warn' : 'sb-ok');
}

/** Wird von wartung.js gerufen, sobald der Wartungsplan geladen ist. */
function setStatusWartung(overdue, total) {
  _sbWartung = { overdue: overdue || 0, total: total || 0 };
  // Frueher stand hier updateStatusBar({}) — das hat die komplette Leiste mit
  // einem leeren Datenobjekt ueberschrieben und alle Werte auf "--" gesetzt.
  // Ausgeloest wurde das u.a. bei jedem resize (Handy: Ein-/Ausblenden der
  // URL-Leiste), was ein sichtbares Flackern erzeugte.
  _sbRenderWartung();
}
