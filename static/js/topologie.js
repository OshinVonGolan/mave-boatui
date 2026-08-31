// ── Verbindungskarte ───────────────────────────────────────────────────────
// EIN Bild für die ganze Anlage: jedes Netz ist ein Knoten, die Geräte liegen
// sternförmig darum, und was an einem Gerät hängt, sitzt weiter außen in
// dessen Richtung. Die FARBE der Leitung sagt, worüber verbunden ist —
// VE.Direct zum MPPT ist etwas anderes als SeaTalk zum Autopiloten.
//
// Netze lassen sich über die Schalter oben ausblenden; mit einem Netz
// verschwindet alles, was daran hängt, sonst blieben Leitungen ins Leere.
//
// Das Layout wird gerechnet, nicht simuliert: kein Kräftemodell, keine
// Zufallslage, kein Nachwackeln. Dasselbe Boot ergibt immer dasselbe Bild, und
// der Pi Zero zeichnet einmal statt zu iterieren. Wo zwei Kästchen trotzdem
// übereinander lägen, schiebt ein Nachlauf sie nach außen — auch das
// deterministisch, in fester Reihenfolge.

const TOPO_NETZ_FARBE = {
  'n2k-bord': '#06b6d4',   // Bordbus
  'n2k-nav':  '#a78bfa',   // Navigationsnetz
  'seatalk':  '#f59e0b',   // SeaTalk
  'lan':      '#38bdf8',   // Bordnetzwerk
  'vedirect': '#34d399',   // VE.Direct
  'analog':   '#94a3b8',   // fest verdrahtet
  'keins':    '#64748b',
};
// Reihenfolge der Schalter und der Cluster im Bild.
const TOPO_NETZ_REIHE = ['n2k-bord', 'lan', 'n2k-nav', 'vedirect', 'seatalk', 'analog', 'keins'];

const TOPO = {
  knotenH:   32,
  netzH:     44,
  ring1:    150,   // kleinster Abstand Netzknoten → Gerät
  ringLuft:  92,   // Abstand von einer Ebene zur nächsten
  luft:      10,   // Mindestabstand zwischen zwei Kästchen
  schritt:   18,   // Ausweichschritt nach außen bei Überlappung
  clusterLuft: 96, // Abstand zwischen zwei Netzen
  faecher:  2.15,  // Winkel eines Halbfächers (rad)
  rand:      30,
  // Platzbedarf eines Knotens auf dem Ring. Bewusst kleiner als die
  // Kästchenbreite: benachbarte Kästchen duerfen sich radial versetzen, dafuer
  // sorgt der Ausweichlauf. Mit der vollen Breite gerechnet wuerde der Ring
  // dreimal so groß und das Bild bestuende aus Leere.
  proKnoten: 104,
};

function _topoBreite(name, gross) {
  // Textbreite geschätzt statt gemessen: Messen hieße, jeden Knoten einmal in
  // den Baum zu hängen und wieder zu entfernen — auf dem Pi Zero teuer und für
  // ein Kästchen mit gekürztem Text unnötig genau.
  const n = String(name || '');
  const zeichen = Math.min(n.length, gross ? 26 : 20);
  return gross ? Math.max(140, 44 + 7.4 * zeichen)
               : Math.max(104, 32 + 6.5 * zeichen);
}

function _topoKuerzen(name, max) {
  const n = String(name || '');
  return n.length > max ? n.slice(0, max - 1) + '…' : n;
}

// ── Sichtbarkeit ───────────────────────────────────────────────────────────

/** Geräte, die nach dem Ausblenden übrig bleiben — samt allem, was daran hängt. */
function _topoSichtbar(alle, aus) {
  const nachId = new Map(alle.map(g => [g.id, g]));
  const versteckt = new Set();
  const pruefen = (g, tiefe) => {
    if (tiefe > 12) return true;                       // Schutz vor Ringen
    if (aus.has(g.netz || 'keins')) return true;
    const eltern = g.verbunden_an && nachId.get(g.verbunden_an);
    return eltern ? pruefen(eltern, tiefe + 1) : false;
  };
  alle.forEach(g => { if (pruefen(g, 0)) versteckt.add(g.id); });
  return alle.filter(g => !versteckt.has(g.id));
}

// ── Baum und Platzbedarf ───────────────────────────────────────────────────

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
  const bauen = (g, tiefe) => ({
    g, tiefe,
    kinder: tiefe > 6 ? [] : (kinder.get(g.id) || [])
      .sort((a, b) => a.name.localeCompare(b.name, 'de'))
      .map(k => bauen(k, tiefe + 1)),
  });
  return wurzeln.map(g => bauen(g, 0));
}

/** Blätter zählen — sie bestimmen, wie viel Winkel ein Zweig bekommt. */
function _topoBlaetter(k) {
  k.blaetter = k.kinder.length
    ? k.kinder.reduce((s, x) => s + _topoBlaetter(x), 0)
    : 1;
  k.ebenen = k.kinder.length ? 1 + Math.max(...k.kinder.map(x => x.ebenen)) : 1;
  return k.blaetter;
}

// ── Platzieren ─────────────────────────────────────────────────────────────

function _topoUeberlappt(a, b, luft) {
  return Math.abs(a.x - b.x) * 2 < a.b + b.b + luft * 2
      && Math.abs(a.y - b.y) * 2 < a.h + b.h + luft;
}

/** Setzt einen Knoten auf den Winkel, rückt bei Überlappung nach außen. */
function _topoSetzen(belegt, cx, cy, winkel, radius, breite, hoehe) {
  for (let versuch = 0; versuch < 16; versuch++) {
    const r = radius + versuch * TOPO.schritt;
    const kasten = {
      x: cx + Math.cos(winkel) * r,
      y: cy + Math.sin(winkel) * r,
      b: breite, h: hoehe,
    };
    if (!belegt.some(v => _topoUeberlappt(kasten, v, TOPO.luft))) {
      belegt.push(kasten);
      return kasten;
    }
  }
  const kasten = {
    x: cx + Math.cos(winkel) * (radius + 16 * TOPO.schritt),
    y: cy + Math.sin(winkel) * (radius + 16 * TOPO.schritt),
    b: breite, h: hoehe,
  };
  belegt.push(kasten);
  return kasten;
}

/** Ein Netz mit allem, was daran hängt. Der Fächer öffnet sich nach außen. */
function _topoCluster(baeume, cx, cy, mitte, spanne, belegt, ablage) {
  const gewicht = baeume.reduce((s, b) => s + b.blaetter, 0) || 1;
  // Der Ring muss so weit außen liegen, dass die Kästchen nebeneinander passen.
  const ring1 = Math.max(TOPO.ring1, (baeume.length * TOPO.proKnoten) / Math.max(spanne, 0.8));

  let a = mitte - spanne / 2;
  baeume.forEach(b => {
    const anteil = spanne * (b.blaetter / gewicht);
    _topoZweig(b, cx, cy, a + anteil / 2, anteil, ring1, belegt, ablage);
    a += anteil;
  });
}

function _topoZweig(k, cx, cy, winkel, spanne, radius, belegt, ablage) {
  const breite = _topoBreite(k.g.name);
  const kasten = _topoSetzen(belegt, cx, cy, winkel, radius, breite, TOPO.knotenH);
  ablage.push({ knoten: k, kasten, winkel });

  if (!k.kinder.length) return;
  const gewicht = k.kinder.reduce((s, x) => s + x.blaetter, 0) || 1;
  // Kinder bleiben im Winkelbereich ihres Geräts, sonst zeigt die Karte eine
  // Nähe, die es nicht gibt. Bei nur einem Kind sitzt es genau dahinter.
  const kSpanne = Math.min(spanne * 0.94, 1.5);
  let a = winkel - kSpanne / 2;
  k.kinder.forEach(kind => {
    const anteil = kSpanne * (kind.blaetter / gewicht);
    _topoZweig(kind, cx, cy, k.kinder.length === 1 ? winkel : a + anteil / 2,
               anteil, radius + TOPO.ringLuft, belegt, ablage);
    a += anteil;
  });
}

// ── Zeichnen ───────────────────────────────────────────────────────────────

function _topoKnotenSvg(g, kasten, hervor) {
  const treffer = hervor && hervor.has(g.id);
  const gedimmt = hervor && !treffer;
  const x = kasten.x - kasten.b / 2;
  const y = kasten.y - kasten.h / 2;
  return `
    <g class="topo-knoten ${treffer ? 'topo-treffer' : ''} ${g.gepflegt ? '' : 'topo-neu'}"
       opacity="${gedimmt ? 0.25 : 1}" onclick="gerDetail(${_jsAttr(g.id)})">
      <rect x="${x}" y="${y}" width="${kasten.b}" height="${kasten.h}" rx="8"/>
      <circle class="topo-punkt s-${_esc(g.status)}" cx="${x + 14}" cy="${kasten.y}" r="4.2"/>
      <text x="${x + 24}" y="${kasten.y + 4}">${_esc(_topoKuerzen(g.name, 20))}</text>
      <title>${_esc(g.name)} — ${_esc(g.status_text)}${g.netz_name ? ' · ' + _esc(g.netz_name) : ''}</title>
    </g>`;
}

function _topoNetzSvg(netz, name, anzahl, kasten) {
  const farbe = TOPO_NETZ_FARBE[netz] || TOPO_NETZ_FARBE.keins;
  const x = kasten.x - kasten.b / 2;
  const y = kasten.y - kasten.h / 2;
  return `
    <g class="topo-netz" onclick="gerNetzToggle(${_jsAttr(netz)})">
      <rect x="${x}" y="${y}" width="${kasten.b}" height="${kasten.h}" rx="12"
            fill="${farbe}" fill-opacity="0.13" stroke="${farbe}" stroke-width="2"/>
      <text x="${kasten.x}" y="${kasten.y - 1}" text-anchor="middle" fill="${farbe}"
            class="topo-netz-name">${_esc(name)}</text>
      <text x="${kasten.x}" y="${kasten.y + 13}" text-anchor="middle"
            class="topo-netz-zahl">${anzahl} ${anzahl === 1 ? 'Gerät' : 'Geräte'}</text>
      <title>${_esc(name)} — antippen blendet dieses Netz aus</title>
    </g>`;
}

/** Punkt auf dem Rand eines Kaestchens in Richtung eines Ziels.

    Ohne das laufen alle Leitungen durch die Mitte der Kaestchen und damit quer
    ueber die Beschriftung — besonders beim Netzknoten, an dem ein Dutzend
    Leitungen zusammenkommen. */
function _topoRand(kasten, zielX, zielY) {
  const dx = zielX - kasten.x, dy = zielY - kasten.y;
  if (!dx && !dy) return { x: kasten.x, y: kasten.y };
  const t = Math.min((kasten.b / 2 + 3) / Math.max(Math.abs(dx), 0.001),
                     (kasten.h / 2 + 3) / Math.max(Math.abs(dy), 0.001));
  return { x: kasten.x + dx * t, y: kasten.y + dy * t };
}

function _topoLeitung(vonK, nachK, farbe, gestrichelt, deckkraft) {
  const von  = _topoRand(vonK,  nachK.x, nachK.y);
  const nach = _topoRand(nachK, vonK.x,  vonK.y);
  // Sanfter Bogen statt Gerade: bei vielen Leitungen aus einem Knoten bleiben
  // die einzelnen Verläufe unterscheidbar.
  const mx = (von.x + nach.x) / 2;
  const my = (von.y + nach.y) / 2;
  const dx = nach.x - von.x, dy = nach.y - von.y;
  const laenge = Math.hypot(dx, dy) || 1;
  const bauch = Math.min(laenge * 0.12, 26);
  const kx = mx - (dy / laenge) * bauch;
  const ky = my + (dx / laenge) * bauch;
  return `<path d="M ${von.x.toFixed(1)} ${von.y.toFixed(1)}
                   Q ${kx.toFixed(1)} ${ky.toFixed(1)} ${nach.x.toFixed(1)} ${nach.y.toFixed(1)}"
                fill="none" stroke="${farbe}" stroke-width="1.8" opacity="${deckkraft ?? 0.8}"
                ${gestrichelt ? 'stroke-dasharray="5 4"' : ''}/>`;
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
      <div class="topo-hinweis">Farbe der Leitung = Art der Verbindung. Gestrichelt: meldet sich
        nicht selbst. Ein Netz antippen blendet es samt allem aus, was daran hängt.</div>
    </div>`;

  if (!sichtbar.length) {
    return kopf + '<div class="ger-leer">Alle Netze ausgeblendet.</div>';
  }

  let hervor = null;
  if (nurProbleme) {
    hervor = new Set(sichtbar.filter(g => g.status === 'offline' || g.status === 'unbekannt').map(g => g.id));
  } else if ((suche || '').trim()) {
    hervor = new Set(sichtbar.filter(g => _gerPasst(g, suche)).map(g => g.id));
  }

  // Wurzeln nach Netz gruppieren — jede Gruppe wird ein Stern.
  const baeume = _topoBaum(sichtbar);
  baeume.forEach(_topoBlaetter);
  const gruppen = [];
  TOPO_NETZ_REIHE.forEach(netz => {
    const teil = baeume.filter(b => (b.g.netz || 'keins') === netz);
    if (teil.length) {
      teil.sort((a, b) => b.blaetter - a.blaetter || a.g.name.localeCompare(b.g.name, 'de'));
      gruppen.push({ netz, baeume: teil });
    }
  });

  const belegt = [];
  const ablage = [];
  const netzKasten = {};

  // Die Sterne stehen NEBENEINANDER, jeder faechert nach oben und nach unten.
  // Zwei Gruende: Bildschirme sind breit, und bei Sternen auf einem Kreis
  // bleibt die Mitte leer, waehrend die Raender ueberlaufen. Nebeneinander
  // waechst das Bild dort, wo Platz ist.
  // Die Breite eines Sterns steht erst fest, wenn er gezeichnet ist: wie weit
  // die Kinder ausschlagen, haengt am Ausweichlauf. Deshalb wird jeder Stern
  // gesetzt, danach gemessen, und der naechste beginnt hinter seinem Rand.
  let lauf = 0;
  gruppen.forEach(gr => {
    const vorher = belegt.length;
    const haelfte = Math.ceil(gr.baeume.length / 2);
    const cx = lauf + Math.max(2, haelfte) * TOPO.proKnoten * 0.6;
    const cy = 0;

    const name = (_gerDaten?.netze || []).find(x => x.key === gr.netz)?.name || gr.netz;
    const kasten = { x: cx, y: cy, b: _topoBreite(name, true), h: TOPO.netzH };
    belegt.push(kasten);
    netzKasten[gr.netz] = kasten;

    // Groesste Zweige zuerst und abwechselnd oben/unten: so verteilt sich die
    // Last gleichmaessig, statt dass eine Seite ausfranst.
    const oben = [], unten = [];
    gr.baeume.forEach((b, i) => ((i % 2) ? unten : oben).push(b));
    if (oben.length)  _topoCluster(oben,  cx, cy, -Math.PI / 2, TOPO.faecher, belegt, ablage);
    if (unten.length) _topoCluster(unten, cx, cy,  Math.PI / 2, TOPO.faecher, belegt, ablage);

    const neue = belegt.slice(vorher);
    lauf = Math.max(...neue.map(k => k.x + k.b / 2)) + TOPO.clusterLuft;
  });

  // Leitungen
  const linien = [];
  const nachId = new Map(ablage.map(e => [e.knoten.g.id, e]));
  ablage.forEach(e => {
    const g = e.knoten.g;
    const eltern = g.verbunden_an && nachId.get(g.verbunden_an);
    const farbe = TOPO_NETZ_FARBE[g.netz] || TOPO_NETZ_FARBE.keins;
    const still = g.status === 'stumm' || g.status === 'fremdnetz';
    if (eltern) {
      linien.push(_topoLeitung(eltern.kasten, e.kasten, farbe, still));
    } else if (netzKasten[g.netz || 'keins']) {
      linien.push(_topoLeitung(netzKasten[g.netz || 'keins'], e.kasten, farbe, still));
    }
    // Zweite Zugehörigkeit: der Pi hängt im Bordnetzwerk und liest den Bus.
    (g.bruecke_zu || []).forEach(netz => {
      if (netzKasten[netz]) {
        linien.push(_topoLeitung(e.kasten, netzKasten[netz], TOPO_NETZ_FARBE[netz], true, 0.45));
      }
    });
  });

  const knoten = ablage.map(e => _topoKnotenSvg(e.knoten.g, e.kasten, hervor)).join('');
  const netze = gruppen.map(gr => {
    const name = (_gerDaten?.netze || []).find(x => x.key === gr.netz)?.name || gr.netz;
    const anzahl = sichtbar.filter(g => (g.netz || 'keins') === gr.netz).length;
    return _topoNetzSvg(gr.netz, name, anzahl, netzKasten[gr.netz]);
  }).join('');

  // Alles in den sichtbaren Bereich schieben
  const alleKasten = belegt;
  const minX = Math.min(...alleKasten.map(k => k.x - k.b / 2)) - TOPO.rand;
  const maxX = Math.max(...alleKasten.map(k => k.x + k.b / 2)) + TOPO.rand;
  const minY = Math.min(...alleKasten.map(k => k.y - k.h / 2)) - TOPO.rand;
  const maxY = Math.max(...alleKasten.map(k => k.y + k.h / 2)) + TOPO.rand;
  const breite = Math.round(maxX - minX);
  const hoehe  = Math.round(maxY - minY);

  return `${kopf}
    <div class="topo-rahmen">
      <svg class="topo-svg" width="${breite}" height="${hoehe}"
           viewBox="${minX.toFixed(1)} ${minY.toFixed(1)} ${breite} ${hoehe}"
           xmlns="http://www.w3.org/2000/svg">
        <g class="topo-leitungen">${linien.join('')}</g>
        ${netze}
        ${knoten}
      </svg>
    </div>`;
}
