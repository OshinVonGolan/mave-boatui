// ── Orte an Bord ───────────────────────────────────────────────────────────
// Eine gemeinsame Ortsliste für alles, was irgendwo an Bord sitzt: Stauplan-
// Artikel heute, Geräte ab jetzt. Namen, Farben und Flächen stammen aus dem
// maßgetreuen Grundriss im Stauplan-Overlay (1:50, LOA 12,80 m, Beam 3,60 m).
//
// Warum die Liste hier gespiegelt und nicht aus stauplan.js gezogen wird:
// stauplan.js hält sie in STAU_FAECHER, aber ohne die Flächen — die stehen im
// Markup. Beides an einer Stelle zusammenzuführen heißt, stauplan.js und
// index.html umzubauen. Das ist der nächste Schritt (siehe KONZEPT-GERAETE.md,
// Abschnitt 10), aber ein eigener: er fasst funktionierende Anzeigen an,
// während hier nur Neues entsteht.

const ORTE = {
  'bug-bb':     { name: 'Bug Backbord',      color: '#f59e0b', x:  14, y:  14, w:  86, h: 148 },
  'bug-sb':     { name: 'Bug Steuerbord',    color: '#fbbf24', x: 100, y:  14, w:  86, h: 148 },
  'kab-bb':     { name: 'Kabine Backbord',   color: '#60a5fa', x:  14, y: 162, w:  75, h: 122 },
  'motor':      { name: 'Motorraum',         color: '#9ca3af', x:  89, y: 162, w:  22, h: 122 },
  'kab-sb':     { name: 'Kabine Steuerbord', color: '#93c5fd', x: 111, y: 162, w:  75, h: 122 },
  'karten':     { name: 'Kartenraum',        color: '#34d399', x:  14, y: 284, w:  84, h:  79 },
  'kombuese':   { name: 'Kombüse',           color: '#fb923c', x:  98, y: 284, w:  90, h:  79 },
  'salon':      { name: 'Salon',             color: '#a78bfa', x:  13, y: 363, w: 174, h: 121 },
  'werkbank':   { name: 'Werkbank / Koje',   color: '#f87171', x:  14, y: 484, w:  86, h:  82 },
  'wc':         { name: 'WC / Bad',          color: '#22d3ee', x: 100, y: 484, w:  87, h:  82 },
  'heck':       { name: 'Heckstauraum',      color: '#94a3b8', x:  24, y: 566, w: 152, h:  84 },
};

// Rumpfkontur des Grundrisses, viewBox 0 0 200 680 — Bug oben, Heck unten.
const ORTE_RUMPF = 'M100,14 C122,26 144,68 150,114 C157,143 169,160 173,168 C180,190 185,232 186,274 L186,360 C185,412 181,452 174,487 C163,524 148,556 138,578 C134,598 132,622 132,644 L68,644 C68,622 66,598 62,578 C52,556 37,524 26,487 C19,452 15,412 14,360 L14,274 C15,232 20,190 27,168 C31,160 43,143 50,114 C56,68 78,26 100,14 Z';

/** Kleiner Schiffsriss mit einem hervorgehobenen Ort. Reines SVG, kein Zustand. */
function orteMiniRiss(ortKey, opts = {}) {
  const ort = ORTE[ortKey];
  const hoehe = opts.hoehe || 150;
  const farbe = ort?.color || 'var(--accent)';
  const flaeche = ort
    ? `<rect x="${ort.x}" y="${ort.y}" width="${ort.w}" height="${ort.h}"
             fill="${farbe}" fill-opacity="0.34" stroke="${farbe}" stroke-width="2.5" rx="3"/>
       <circle cx="${ort.x + ort.w / 2}" cy="${ort.y + ort.h / 2}" r="9" fill="${farbe}"/>`
    : '';
  return `<svg viewBox="0 0 200 680" height="${hoehe}" style="display:block;overflow:visible"
               xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path d="${ORTE_RUMPF}" fill="var(--bg)" stroke="var(--border)" stroke-width="3"/>
    ${Object.values(ORTE).map(o =>
      `<rect x="${o.x}" y="${o.y}" width="${o.w}" height="${o.h}" fill="var(--surface2)"
             fill-opacity="0.5" stroke="var(--border)" stroke-width="1.4"/>`).join('')}
    ${flaeche}
  </svg>`;
}

/** Anzeigename eines Ortes, leer wenn unbekannt. */
function ortName(key) { return ORTE[key]?.name || ''; }
