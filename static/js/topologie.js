// ── Verbindungskarte ───────────────────────────────────────────────────────
// Je Netz eine Spalte, darin ein aufgeklappter Baum: die SPALTE sagt, an
// welchem Netz der Strang haengt, die ZEILE die Reihenfolge, die EINRUeCKUNG,
// woran ein Geraet haengt. Drei Angaben, drei Achsen — jede Position im Bild
// ist damit in einem Satz erklaerbar.
//
// Vorgaenger war ein Sternbild mit geschwungenen Leitungen. Es sah aus wie ein
// Nervengeflecht: die Lage eines Kaestchens liess sich nicht begruenden. Ein
// Verzeichnisbaum kennt dagegen jeder, bevor er dieses Bild zum ersten Mal
// sieht.
//
// Die FARBE einer Leitung (und der Kante links am Kaestchen) sagt weiterhin,
// worueber verbunden ist: die vier Victron-Geraete stehen in der Bordnetz-
// Spalte, weil ihr Gateway dort haengt, tragen aber gruene VE.Direct-Leitungen.
//
// Gerechnet, nicht simuliert: ganze Zahlen, ein Durchlauf, keine Messung im
// DOM, keine Ausweichschleife. Ueberlappung ist im Raster unmoeglich.

const TOPO_NETZ_FARBE = {
  'n2k-bord': '#06b6d4',   // Bordbus
  'n2k-nav':  '#a78bfa',   // Navigationsnetz
  'seatalk':  '#f59e0b',   // SeaTalk
  'lan':      '#38bdf8',   // Bordnetzwerk
  'vedirect': '#34d399',   // VE.Direct
  'analog':   '#94a3b8',   // fest verdrahtet
  'keins':    '#64748b',
};
// Reihenfolge der Spalten und der Schalter.
const TOPO_NETZ_REIHE = ['n2k-bord', 'lan', 'n2k-nav', 'vedirect', 'seatalk', 'analog', 'keins'];

const TOPO = {
  spalteB:   354,   // Breite einer Netzspalte
  spalteLuft: 34,
  rand:       14,
  kopfH:      44,   // Kopfzeile der Spalte
  zeile:      40,   // Zeilenabstand
  kastenH:    32,
  einzug0:    22,   // Einzug der ersten Ebene im Panel
  einzugTiefe: 26,  // je weitere Ebene
  maxTiefe:    4,
  trennerH:   24,   // Platz fuer die Zeile "nur erkannt"
  band:       22,   // Hoehe der Bahn fuer Bruecken zwischen Spalten
  rail:       11,   // Abstand Kaestchenkante → senkrechte Sammellinie
};

function _topoKuerzen(name, breite) {
  const max = Math.max(6, Math.floor((breite - 46) / 6.4));
  const n = String(name || '');
  return n.length > max ? n.slice(0, max - 1) + '…' : n;
}

// ── Sichtbarkeit ───────────────────────────────────────────────────────────

/** Geraete, die nach dem Ausblenden uebrig bleiben — samt allem, was daran haengt. */
function _topoSichtbar(alle, aus) {
  const nachId = new Map(alle.map(g => [g.id, g]));
  const versteckt = new Set();
  const pruefen = (g, tiefe) => {
    if (tiefe > 12) return true;                     // Schutz vor Ringen
    if (aus.has(g.netz || 'keins')) return true;
    const eltern = g.verbunden_an && nachId.get(g.verbunden_an);
    return eltern ? pruefen(eltern, tiefe + 1) : false;
  };
  alle.forEach(g => { if (pruefen(g, 0)) versteckt.add(g.id); });
  return alle.filter(g => !versteckt.has(g.id));
}

// ── Reihenfolge ────────────────────────────────────────────────────────────

/** Verteiler zuerst, dann Einzelgeraete, zuletzt nur Erkanntes — je alphabetisch.
 *  Eine gesetzte Ordnung, aber eine ablesbare: was etwas traegt, steht oben. */
function _topoSortieren(liste, kinderVon) {
  return [...liste].sort((a, b) => {
    const ak = (kinderVon.get(a.id) || []).length, bk = (kinderVon.get(b.id) || []).length;
    if (!!a.gepflegt !== !!b.gepflegt) return a.gepflegt ? -1 : 1;
    if ((ak > 0) !== (bk > 0)) return ak > 0 ? -1 : 1;
    return a.name.localeCompare(b.name, 'de');
  });
}

/** Kinder in der Reihenfolge der Netze, damit gleiche Anschlussarten beieinander liegen. */
function _topoKinderSortieren(liste) {
  return [...liste].sort((a, b) => {
    const an = TOPO_NETZ_REIHE.indexOf(a.netz || 'keins');
    const bn = TOPO_NETZ_REIHE.indexOf(b.netz || 'keins');
    if (an !== bn) return an - bn;
    return a.name.localeCompare(b.name, 'de');
  });
}

/** Eine Spalte als flache Zeilenliste in Baumreihenfolge (Tiefensuche). */
function _topoZeilen(wurzeln, kinderVon) {
  const zeilen = [];
  const gehen = (g, tiefe, eltern) => {
    const eintrag = { g, tiefe, eltern, kinder: [] };
    zeilen.push(eintrag);
    if (tiefe < TOPO.maxTiefe) {
      _topoKinderSortieren(kinderVon.get(g.id) || []).forEach(k => {
        eintrag.kinder.push(gehen(k, tiefe + 1, eintrag));
      });
    }
    return eintrag;
  };
  wurzeln.forEach(w => gehen(w, 0, null));
  return zeilen;
}

// ── Zeichnen ───────────────────────────────────────────────────────────────

function _topoKasten(z, x, y, breite, hervor) {
  const g = z.g;
  const treffer = hervor && hervor.has(g.id);
  const gedimmt = hervor && !treffer;
  const farbe = TOPO_NETZ_FARBE[g.netz] || TOPO_NETZ_FARBE.keins;
  return `
    <g class="topo-knoten ${treffer ? 'topo-treffer' : ''} ${g.gepflegt ? '' : 'topo-neu'}"
       opacity="${gedimmt ? 0.3 : 1}" onclick="gerDetail(${_jsAttr(g.id)})">
      <rect class="topo-ziel" x="${x}" y="${y - 4}" width="${breite}" height="${TOPO.zeile}"/>
      <rect class="topo-rahmen-kasten" x="${x}" y="${y}" width="${breite}" height="${TOPO.kastenH}" rx="7"/>
      <rect class="topo-kante" x="${x}" y="${y + 5}" width="3" height="${TOPO.kastenH - 10}"
            rx="1.5" fill="${farbe}"/>
      <circle class="topo-punkt s-${_esc(g.status)}" cx="${x + 17}" cy="${y + TOPO.kastenH / 2}" r="4.2"/>
      <text x="${x + 29}" y="${y + TOPO.kastenH / 2 + 4}">${_esc(_topoKuerzen(g.name, breite))}</text>
      <title>${_esc(g.name)} — ${_esc(g.status_text)}${g.netz_name ? ' · ' + _esc(g.netz_name) : ''}</title>
    </g>`;
}

// ── Die Karte ──────────────────────────────────────────────────────────────

function gerKarteHtml(alle, suche, nurProbleme, ausgeblendet) {
  const aus = ausgeblendet || new Set();
  const vorhanden = TOPO_NETZ_REIHE.filter(n => alle.some(g => (g.netz || 'keins') === n));

  const schalter = vorhanden.map(n => {
    const name = (_gerDaten?.netze || []).find(x => x.key === n)?.name || n;
    const anzahl = alle.filter(g => (g.netz || 'keins') === n).length;
    const an = !aus.has(n);
    return `<button class="topo-chip ${an ? '' : 'inaktiv'}" onclick="gerNetzToggle(${_jsAttr(n)})"
                    aria-pressed="${an}">
              <i style="background:${TOPO_NETZ_FARBE[n]}"></i>${_esc(name)}
              <b>${anzahl}</b></button>`;
  }).join('');

  const sichtbar = _topoSichtbar(alle, aus);
  const kopf = `
    <div class="topo-kopf">
      <div class="topo-chips">${schalter}</div>
      <div class="topo-hinweis">Spalte = an welchem Netz der Strang hängt · Einrückung = woran ein
        Gerät hängt · Farbe der Leitung und der Kante = Art des Anschlusses. Ein Netz antippen
        blendet es samt allem aus, was daran hängt.</div>
    </div>`;

  if (!sichtbar.length) return kopf + '<div class="ger-leer">Alle Netze ausgeblendet.</div>';

  let hervor = null;
  if (nurProbleme) {
    hervor = new Set(sichtbar.filter(g => g.status === 'offline' || g.status === 'unbekannt').map(g => g.id));
  } else if ((suche || '').trim()) {
    hervor = new Set(sichtbar.filter(g => _gerPasst(g, suche)).map(g => g.id));
  }

  // Baum aufbauen
  const nachId = new Map(sichtbar.map(g => [g.id, g]));
  const kinderVon = new Map();
  const wurzeln = [];
  sichtbar.forEach(g => {
    const e = g.verbunden_an && nachId.has(g.verbunden_an) ? g.verbunden_an : null;
    if (e) { if (!kinderVon.has(e)) kinderVon.set(e, []); kinderVon.get(e).push(g); }
    else wurzeln.push(g);
  });

  // Eine Spalte je Netz, das Wurzelgeraete traegt. Netze, die nur als
  // Anschlussart vorkommen (VE.Direct, SeaTalk, analog), bekommen keine
  // Spalte — sie sind Leitungsfarbe innerhalb einer fremden Spalte.
  const spalten = [];
  TOPO_NETZ_REIHE.forEach(netz => {
    const teil = wurzeln.filter(g => (g.netz || 'keins') === netz);
    if (teil.length) {
      spalten.push({ netz, zeilen: _topoZeilen(_topoSortieren(teil, kinderVon), kinderVon) });
    }
  });
  if (!spalten.length) return kopf + '<div class="ger-leer">Nichts anzuzeigen.</div>';

  const bruecken = sichtbar.filter(g => (g.bruecke_zu || []).length);
  const bandH = bruecken.length ? TOPO.band * bruecken.length + 8 : 0;
  const y0 = TOPO.rand + TOPO.kopfH + bandH + 12;

  // Lage jedes Kaestchens: ganze Zahlen, ein Durchlauf.
  const platz = new Map();
  let maxY = y0;
  spalten.forEach((sp, ci) => {
    sp.x = TOPO.rand + ci * (TOPO.spalteB + TOPO.spalteLuft);
    const trennerAb = sp.zeilen.findIndex(z => !z.g.gepflegt);
    sp.trennerAb = trennerAb;
    sp.zeilen.forEach((z, i) => {
      const tiefe = Math.min(z.tiefe, TOPO.maxTiefe);
      z.x = sp.x + TOPO.einzug0 + TOPO.einzugTiefe * tiefe;
      z.b = TOPO.spalteB - TOPO.einzug0 - TOPO.einzugTiefe * tiefe;
      z.y = y0 + i * TOPO.zeile + (trennerAb >= 0 && i >= trennerAb ? TOPO.trennerH : 0);
      platz.set(z.g.id, z);
      maxY = Math.max(maxY, z.y + TOPO.kastenH);
    });
  });

  const breite = TOPO.rand * 2 + spalten.length * TOPO.spalteB
               + (spalten.length - 1) * TOPO.spalteLuft;
  const hoehe = maxY + TOPO.rand + 6;

  // Leitungen: senkrecht an der Sammellinie des Elternteils, dann waagerecht
  // in das Kaestchen. Von unten nach oben gezeichnet, damit die Farbe direkt
  // ueber jedem Stich die des zugehoerigen Geraets ist.
  const leitungen = [];
  spalten.forEach(sp => {
    sp.zeilen.forEach(z => {
      if (!z.kinder.length) return;
      // EINE ruhige Sammellinie je Verteiler, in seiner eigenen Netzfarbe,
      // dazu je Kind ein kurzer farbiger Stich. Vorher lief pro Kind eine
      // eigene lange Linie an derselben Stelle — sechs Kinder ergaben sechs
      // uebereinanderliegende Striche in verschiedenen Farben.
      const railX = z.x + TOPO.rail;
      const letztes = z.kinder[z.kinder.length - 1];
      leitungen.push(
        `<path d="M ${railX} ${z.y + TOPO.kastenH} V ${letztes.y + TOPO.kastenH / 2}"
               fill="none" stroke="${TOPO_NETZ_FARBE[z.g.netz] || TOPO_NETZ_FARBE.keins}"
               stroke-width="1.2" opacity="0.4"/>`);
      z.kinder.forEach(k => {
        const farbe = TOPO_NETZ_FARBE[k.g.netz] || TOPO_NETZ_FARBE.keins;
        const still = k.g.status === 'stumm' || k.g.status === 'fremdnetz';
        const y = k.y + TOPO.kastenH / 2;
        leitungen.push(
          `<path d="M ${railX} ${y - 7} Q ${railX} ${y} ${railX + 8} ${y} H ${k.x}"
                 fill="none" stroke="${farbe}" stroke-width="1.8" opacity="0.9"
                 ${still ? 'stroke-dasharray="4 3"' : ''}/>`);
      });
    });
  });

  // Bruecken: ein Geraet, das in einem zweiten Netz haengt. Die Leitung laeuft
  // rechts an der Spalte hoch in eine eigene Bahn und von dort zum Kopf der
  // anderen Spalte — so kreuzt sie kein Kaestchen.
  const spalteVon = {};
  spalten.forEach(sp => { spalteVon[sp.netz] = sp; });
  bruecken.forEach((g, i) => {
    const z = platz.get(g.id);
    if (!z) return;
    (g.bruecke_zu || []).forEach(netz => {
      const ziel = spalteVon[netz];
      if (!ziel || !z) return;
      const bahnY = TOPO.rand + TOPO.kopfH + 10 + i * TOPO.band;
      const eigene = spalten.find(sp => sp.zeilen.includes(z));
      // Auf der Seite austreten, auf der das andere Netz liegt, und in der
      // Gasse zwischen den Spalten hochlaufen. Andersherum umschloesse die
      // Leitung die eigene Spalte wie ein Rahmen.
      const linksHerum = ziel.x < (eigene ? eigene.x : z.x);
      const gasse = linksHerum
        ? (eigene ? eigene.x : z.x) - TOPO.spalteLuft / 2
        : (eigene ? eigene.x + TOPO.spalteB : z.x + z.b) + TOPO.spalteLuft / 2;
      const start = linksHerum ? z.x : z.x + z.b;
      const zielX = linksHerum ? ziel.x + TOPO.spalteB - 26 : ziel.x + 26;
      const farbe = TOPO_NETZ_FARBE[netz] || TOPO_NETZ_FARBE.keins;
      leitungen.push(
        `<path d="M ${start} ${z.y + TOPO.kastenH / 2} H ${gasse} V ${bahnY} H ${zielX} V ${TOPO.rand + TOPO.kopfH}"
               fill="none" stroke="${farbe}" stroke-width="1.6" stroke-dasharray="5 4" opacity="0.65"/>
         <title>${_esc(g.name)} hängt auch am ${_esc((_gerDaten?.netze || []).find(x => x.key === netz)?.name || netz)}</title>`);
    });
  });

  // Spaltenpanel und Kopfzeile
  const panels = spalten.map(sp => {
    const name = (_gerDaten?.netze || []).find(x => x.key === sp.netz)?.name || sp.netz;
    const farbe = TOPO_NETZ_FARBE[sp.netz];
    const unten = (sp.zeilen.length ? sp.zeilen[sp.zeilen.length - 1].y + TOPO.kastenH : y0) + 12;
    const trenner = sp.trennerAb >= 0 ? (() => {
      const z = sp.zeilen[sp.trennerAb];
      const ty = z.y - TOPO.trennerH / 2 - 4;
      return `<line x1="${sp.x + 10}" y1="${ty}" x2="${sp.x + TOPO.spalteB - 10}" y2="${ty}"
                    stroke="var(--border)" stroke-width="1" stroke-dasharray="3 4"/>
              <text class="topo-trenner" x="${sp.x + TOPO.spalteB - 10}" y="${ty - 5}"
                    text-anchor="end">nur erkannt</text>`;
    })() : '';
    return `
      <g class="topo-spalte" onclick="gerNetzToggle(${_jsAttr(sp.netz)})">
        <rect x="${sp.x}" y="${TOPO.rand + TOPO.kopfH + bandH}" width="${TOPO.spalteB}"
              height="${unten - TOPO.rand - TOPO.kopfH - bandH}" rx="12"
              fill="${farbe}" fill-opacity="0.035" stroke="${farbe}" stroke-opacity="0.28"/>
        <rect x="${sp.x}" y="${TOPO.rand}" width="${TOPO.spalteB}" height="${TOPO.kopfH}" rx="10"
              fill="${farbe}" fill-opacity="0.13" stroke="${farbe}" stroke-width="1.6"/>
        <text class="topo-spalte-name" x="${sp.x + 14}" y="${TOPO.rand + 27}"
              fill="${farbe}">${_esc(name)}</text>
        <text class="topo-spalte-zahl" x="${sp.x + TOPO.spalteB - 14}" y="${TOPO.rand + 27}"
              text-anchor="end">${sp.zeilen.length}</text>
        <title>${_esc(name)} — antippen blendet diese Spalte aus</title>
      </g>${trenner}`;
  }).join('');

  const kaesten = spalten.map(sp =>
    sp.zeilen.map(z => _topoKasten(z, z.x, z.y, z.b, hervor)).join('')).join('');

  return `${kopf}
    <div class="topo-rahmen">
      <svg class="topo-svg" width="${breite}" height="${hoehe}"
           viewBox="0 0 ${breite} ${hoehe}" xmlns="http://www.w3.org/2000/svg">
        ${panels}
        <g class="topo-leitungen">${leitungen.join('')}</g>
        ${kaesten}
      </svg>
    </div>`;
}
