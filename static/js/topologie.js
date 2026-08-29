// ── Verbindungskarte ───────────────────────────────────────────────────────
// Das Schaltbild der Anlage: je Netz eine Schiene, daran die Geräte, und
// darunter als Baum, was an ihnen hängt. Die FARBE der Linie sagt, worüber
// verbunden ist — VE.Direct zum MPPT ist etwas anderes als SeaTalk zum
// Autopiloten, auch wenn beide „hängt an" heißen.
//
// Das Layout wird gerechnet, nicht simuliert: keine Kräfte, keine Zufallslage,
// kein Nachwackeln. Dasselbe Boot ergibt immer dasselbe Bild — und auf dem Pi
// Zero kostet es nichts, weil nur einmal gezeichnet wird.

const TOPO_NETZ_FARBE = {
  'n2k-bord': '#06b6d4',   // Bordbus
  'n2k-nav':  '#a78bfa',   // Navigationsnetz
  'seatalk':  '#f59e0b',   // SeaTalk
  'lan':      '#38bdf8',   // Bordnetzwerk
  'vedirect': '#34d399',   // VE.Direct
  'analog':   '#94a3b8',   // fest verdrahtet
  'keins':    '#475569',
};
// Reihenfolge der Schienen von oben nach unten.
const TOPO_SCHIENEN = ['n2k-bord', 'n2k-nav', 'lan', 'vedirect', 'seatalk', 'analog', 'keins'];

const TOPO = {
  knotenH:  38,   // Höhe eines Gerätekästchens
  ebeneLuft: 36,  // senkrechte Luft zwischen zwei Baumebenen (nebeneinander)
  reiheLuft: 10,  // senkrechte Luft zwischen Geschwistern (untereinander)
  spalte:   18,   // waagerechte Luft zwischen Geschwistern
  einzug:   44,   // Einzug der Kinder im Kabelbaum
  randX:    18,
  schieneAbstand: 46,   // Luft zwischen Schiene und erster Geräteebene
  blockLuft: 40,        // Luft zwischen zwei Schienenblöcken
  // Ab wie vielen Kindern nicht mehr nebeneinander, sondern untereinander
  // gezeichnet wird. Sechs Raumknoten nebeneinander machten die Karte doppelt
  // so breit wie jeder Bildschirm; untereinander bleibt sie lesbar.
  maxNeben: 3,
};

function _topoBreite(name) {
  // Textbreite geschätzt statt gemessen: Messen hieße, jeden Knoten einmal in
  // den Baum zu hängen und wieder zu entfernen — auf dem Pi Zero teuer und für
  // ein Kästchen mit gekürztem Text unnötig genau.
  return Math.max(124, Math.min(214, 40 + 6.9 * Math.min((name || '').length, 26)));
}

function _topoKuerzen(name) {
  const n = String(name || '');
  return n.length > 26 ? n.slice(0, 25) + '…' : n;
}

/** Baum aus den verbunden_an-Angaben. Nur Geräte der übergebenen Liste. */
function _topoBaum(liste) {
  const nachId = new Map(liste.map(g => [g.id, g]));
  const kinder = new Map();
  const wurzeln = [];
  liste.forEach(g => {
    const eltern = g.verbunden_an && nachId.has(g.verbunden_an) ? g.verbunden_an : null;
    if (eltern) {
      if (!kinder.has(eltern)) kinder.set(eltern, []);
      kinder.get(eltern).push(g);
    } else {
      wurzeln.push(g);
    }
  });
  const bauen = (g) => ({
    g,
    kinder: (kinder.get(g.id) || [])
      .sort((a, b) => a.name.localeCompare(b.name, 'de'))
      .map(bauen),
  });
  return wurzeln.map(bauen);
}

/** Platzbedarf eines Teilbaums in Pixeln. Ergebnis steht danach im Knoten. */
function _topoMessen(k) {
  const eigen = _topoBreite(k.g.name);
  if (!k.kinder.length) {
    k.breite = eigen;
    k.hoehe  = TOPO.knotenH;
    return k;
  }
  k.kinder.forEach(_topoMessen);
  k.vertikal = k.kinder.length > TOPO.maxNeben;
  if (k.vertikal) {
    k.breite = Math.max(eigen, TOPO.einzug + Math.max(...k.kinder.map(x => x.breite)));
    k.hoehe  = TOPO.knotenH
             + k.kinder.reduce((s, x) => s + x.hoehe + TOPO.reiheLuft, 0);
  } else {
    k.breite = Math.max(eigen, k.kinder.reduce((s, x) => s + x.breite, 0)
                             + TOPO.spalte * (k.kinder.length - 1));
    k.hoehe  = TOPO.knotenH + TOPO.ebeneLuft + Math.max(...k.kinder.map(x => x.hoehe));
  }
  return k;
}

/** Zeichnet einen Teilbaum. x ist die linke Kante des zustehenden Bereichs. */
function _topoZeichnen(k, x, y, teile, hervor) {
  const eigen = _topoBreite(k.g.name);
  // Nebeneinander: Elternknoten mittig über den Kindern. Untereinander: linksbündig.
  const kx = k.vertikal ? x : x + (k.breite - eigen) / 2;
  const mitteX = kx + eigen / 2;
  const untenY = y + TOPO.knotenH;
  const strich = kind => (kind.g.status === 'stumm' || kind.g.status === 'fremdnetz')
    ? 'stroke-dasharray="5 4"' : '';

  if (k.vertikal) {
    // Kabelbaum: jedes Kind bekommt einen eigenen senkrechten Kanal, sonst
    // lägen sechs verschiedenfarbige Leitungen exakt übereinander und nur die
    // zuletzt gezeichnete wäre zu sehen.
    let ky = untenY + TOPO.reiheLuft;
    const spreizung = k.kinder.length > 1
      ? Math.min(6, (TOPO.einzug - 24) / (k.kinder.length - 1)) : 0;
    k.kinder.forEach((kind, i) => {
      const kanal = kx + 16 + i * spreizung;
      const kindX = kx + TOPO.einzug;
      const zielY = ky + TOPO.knotenH / 2;
      const farbe = TOPO_NETZ_FARBE[kind.g.netz] || TOPO_NETZ_FARBE.keins;
      teile.linien.push(
        `<path d="M ${kanal} ${untenY} V ${zielY - 8} Q ${kanal} ${zielY} ${kanal + 10} ${zielY} H ${kindX}"
               fill="none" stroke="${farbe}" stroke-width="2" ${strich(kind)} opacity="0.85"/>`);
      _topoZeichnen(kind, kindX, ky, teile, hervor);
      ky += kind.hoehe + TOPO.reiheLuft;
    });
  } else {
    const kinderBreite = k.kinder.reduce((s, c) => s + c.breite, 0)
                       + TOPO.spalte * Math.max(0, k.kinder.length - 1);
    let cx = x + (k.breite - kinderBreite) / 2;
    const kindY = untenY + TOPO.ebeneLuft;
    k.kinder.forEach(kind => {
      const kindEigen = _topoBreite(kind.g.name);
      const kindMitte = kind.vertikal
        ? cx + kindEigen / 2
        : cx + (kind.breite - kindEigen) / 2 + kindEigen / 2;
      const farbe = TOPO_NETZ_FARBE[kind.g.netz] || TOPO_NETZ_FARBE.keins;
      // Weiche S-Kurve statt Winkel: die Linien laufen auseinander und bleiben
      // einzeln verfolgbar — genau darum geht es hier.
      const mitte = (untenY + kindY) / 2;
      teile.linien.push(
        `<path d="M ${mitteX} ${untenY} C ${mitteX} ${mitte} ${kindMitte} ${mitte} ${kindMitte} ${kindY}"
               fill="none" stroke="${farbe}" stroke-width="2" ${strich(kind)} opacity="0.85"/>`);
      _topoZeichnen(kind, cx, kindY, teile, hervor);
      cx += kind.breite + TOPO.spalte;
    });
  }

  teile.knoten.push(_topoKnoten(k.g, kx, y, eigen, hervor));
  return { mitteX, obenY: y };
}

function _topoKnoten(g, x, y, breite, hervor) {
  const treffer = hervor && hervor.has(g.id);
  const gedimmt = hervor && !treffer;
  return `
    <g class="topo-knoten ${treffer ? 'topo-treffer' : ''} ${g.gepflegt ? '' : 'topo-neu'}"
       opacity="${gedimmt ? 0.28 : 1}"
       onclick="gerDetail(${_jsAttr(g.id)})">
      <rect x="${x}" y="${y}" width="${breite}" height="${TOPO.knotenH}" rx="9"/>
      <circle class="topo-punkt s-${_esc(g.status)}" cx="${x + 15}" cy="${y + TOPO.knotenH / 2}" r="4.5"/>
      <text x="${x + 27}" y="${y + TOPO.knotenH / 2 + 4}">${_esc(_topoKuerzen(g.name))}</text>
      <title>${_esc(g.name)} — ${_esc(g.status_text)}${g.netz_name ? ' · ' + _esc(g.netz_name) : ''}</title>
    </g>`;
}

/** Die ganze Karte als SVG.

    Suche und Problemfilter heben hervor, statt zu filtern: ein Schaltbild, aus
    dem Geräte herausgeschnitten sind, zeigt Verbindungen ins Nichts. */
function gerKarteHtml(alle, suche, nurProbleme) {
  if (!alle.length) return '<div class="ger-leer">Keine Geräte.</div>';

  let hervor = null;
  if (nurProbleme) {
    hervor = new Set(alle.filter(g => g.status === 'offline' || g.status === 'unbekannt')
                         .map(g => g.id));
  } else if ((suche || '').trim()) {
    hervor = new Set(alle.filter(g => _gerPasst(g, suche)).map(g => g.id));
  }
  const baeume = _topoBaum(alle).map(_topoMessen);

  // Wurzeln nach Netz auf Schienen verteilen; die Reihenfolge ist fest, damit
  // dasselbe Boot immer dasselbe Bild ergibt.
  const nachNetz = new Map();
  baeume.forEach(b => {
    const netz = TOPO_NETZ_FARBE[b.g.netz] ? b.g.netz : 'keins';
    if (!nachNetz.has(netz)) nachNetz.set(netz, []);
    nachNetz.get(netz).push(b);
  });

  const teile = { linien: [], knoten: [], schienen: [] };
  let y = 26;
  let maxX = 0;

  TOPO_SCHIENEN.filter(n => nachNetz.has(n)).forEach(netz => {
    const gruppe = nachNetz.get(netz).sort((a, b) =>
      (a.g.gepflegt === b.g.gepflegt ? 0 : a.g.gepflegt ? -1 : 1)
      || b.hoehe - a.hoehe
      || a.g.name.localeCompare(b.g.name, 'de'));
    const farbe = TOPO_NETZ_FARBE[netz];
    const name = (_gerDaten?.netze || []).find(n => n.key === netz)?.name || netz;

    const schieneY = y;
    const knotenY  = y + TOPO.schieneAbstand;
    let x = TOPO.randX;
    const anker = [];
    gruppe.forEach(b => {
      const pos = _topoZeichnen(b, x, knotenY, teile, hervor);
      anker.push(pos.mitteX);
      x += b.breite + TOPO.spalte * 2;
    });
    const breite = Math.max(x - TOPO.spalte * 2, 320);
    maxX = Math.max(maxX, breite);

    // Schiene mit Stichleitungen zu jedem Gerät, das direkt daran hängt
    teile.schienen.push(`
      <g class="topo-schiene">
        <line x1="${TOPO.randX}" y1="${schieneY}" x2="${breite}" y2="${schieneY}"
              stroke="${farbe}" stroke-width="3" stroke-linecap="round" opacity="0.75"/>
        <text x="${TOPO.randX}" y="${schieneY - 11}" fill="${farbe}"
              class="topo-schiene-titel">${_esc(name)}
          <tspan opacity="0.65">· ${gruppe.length} direkt angeschlossen</tspan></text>
        ${anker.map(ax => `<line x1="${ax}" y1="${schieneY}" x2="${ax}" y2="${knotenY}"
              stroke="${farbe}" stroke-width="2" opacity="0.6"/>`).join('')}
      </g>`);

    y = knotenY + Math.max(...gruppe.map(b => b.hoehe)) + TOPO.blockLuft + 22;
  });

  const legende = TOPO_SCHIENEN
    .filter(n => alle.some(g => (g.netz || 'keins') === n))
    .map(n => {
      const name = (_gerDaten?.netze || []).find(x => x.key === n)?.name || n;
      return `<span class="topo-leg"><i style="background:${TOPO_NETZ_FARBE[n]}"></i>${_esc(name)}</span>`;
    }).join('');

  return `
    <div class="topo-hinweis">
      Linien zeigen, worüber ein Gerät angebunden ist. Gestrichelt: meldet sich nicht selbst.
      Tippen öffnet die Einzelheiten.
    </div>
    <div class="topo-legende">${legende}</div>
    <div class="topo-rahmen">
      <svg class="topo-svg" width="${maxX + TOPO.randX}" height="${y}"
           viewBox="0 0 ${maxX + TOPO.randX} ${y}" xmlns="http://www.w3.org/2000/svg">
        ${teile.schienen.join('')}
        ${teile.linien.join('')}
        ${teile.knoten.join('')}
      </svg>
    </div>`;
}
