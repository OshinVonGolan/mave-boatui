// ── Stromverlauf ───────────────────────────────────────────────────────────
// Eigene Seite nach Cerbo-Vorbild: Erzeugung als gestapelte Flaechen, Verbrauch
// als Linie darueber, darunter die Energiesummen des gewaehlten Fensters.
//
// Holt die Daten selbst per /api/history?range=..., statt histData mitzubenutzen:
// der Client-Puffer ist zeitlich gekappt und taugt fuer 7 Tage nicht. Gezeichnet
// wird auf Canvas — bei bis zu 900 Punkten waeren SVG-Pfade auf dem Pi Zero
// unnoetig teuer im DOM.

const VL_FARBEN = {
  landstrom: '#06b6d4',
  solar:     '#eab308',
  verbrauch: '#f97316',
  batterie:  '#22c55e',
};

const VL_BEREICHE = [
  { label: '2 Std', secs: 2 * 3600 },
  { label: '12 Std', secs: 12 * 3600 },
  { label: '24 Std', secs: 24 * 3600 },
  { label: '7 Tage', secs: 7 * 86400 },
];

let _vlBereich  = 12 * 3600;
let _vlDaten    = null;
let _vlLaeuft   = false;

function openVerlauf() {
  _closeAllOverlays();
  history.pushState({ overlay: 'verlauf' }, '', '#verlauf');
  $('verlaufOverlay').classList.remove('hidden');
  _vlBereichKnoepfe();
  ladeVerlauf();
}

function closeVerlauf() {
  $('verlaufOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

function _vlBereichKnoepfe() {
  const box = $('vlBereiche');
  if (!box) return;
  box.innerHTML = VL_BEREICHE.map(b =>
    `<button class="vl-btn${b.secs === _vlBereich ? ' an' : ''}"
       onclick="setVerlaufBereich(${b.secs})">${b.label}</button>`).join('');
}

function setVerlaufBereich(secs) {
  _vlBereich = secs;
  _vlBereichKnoepfe();
  ladeVerlauf();
}

async function ladeVerlauf() {
  if (_vlLaeuft) return;
  _vlLaeuft = true;
  const hinweis = $('vlHinweis');
  if (hinweis) hinweis.textContent = 'lädt …';
  try {
    const r = await fetch(`/api/history?range=${_vlBereich}&max_points=900`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const res = await r.json();
    if (typeof res.server_now === 'number') setClockOffset(res.server_now);
    _vlDaten = Array.isArray(res) ? res : (res.entries || []);
    zeichneVerlauf();
  } catch (e) {
    console.warn('Stromverlauf konnte nicht geladen werden:', e);
    if (hinweis) hinweis.textContent = 'Verlauf nicht verfügbar';
  } finally {
    _vlLaeuft = false;
  }
}

/** Rechnet einen History-Eintrag in die vier Groessen der Darstellung um. */
function _vlZeile(e) {
  const ls = Math.max(0, e.charger ?? 0);
  const so = Math.max(0, e.solar1 ?? 0);
  // Batterieleistung steht nicht im Verlauf, laesst sich aber aus Spannung und
  // Strom rechnen. Positiv = laedt, negativ = entnimmt.
  const bp = (e.voltage != null && e.current != null) ? e.voltage * e.current : null;
  return {
    ts: e.ts, ls, so, rein: ls + so, batt: bp,
    verbrauch: bp != null ? Math.max(0, ls + so - bp) : null,
  };
}

/**
 * Gleitender Mittelwert ueber die Zeichenreihe.
 *
 * Die Fensterbreite waechst mit der Punktzahl: bei 900 Punkten auf einer rund
 * 1100 px breiten Flaeche liegen mehrere Messwerte je Pixel, das ergibt ein
 * Rauschband statt einer Kurve. Rund 120 sichtbare Stuetzstellen sind ein guter
 * Kompromiss zwischen Ruhe und Detail.
 */
function _vlGlaetten(R) {
  const fenster = Math.max(1, Math.round(R.length / 120));
  if (fenster < 2) return R;
  const halb = Math.floor(fenster / 2);
  const mittel = (i, hol) => {
    let summe = 0, n = 0;
    for (let k = Math.max(0, i - halb); k <= Math.min(R.length - 1, i + halb); k++) {
      const v = hol(R[k]);
      if (v != null) { summe += v; n++; }
    }
    return n ? summe / n : null;
  };
  return R.map((r, i) => ({
    ts: r.ts,
    ls: mittel(i, x => x.ls),
    so: mittel(i, x => x.so),
    rein: mittel(i, x => x.rein),
    batt: mittel(i, x => x.batt),
    verbrauch: mittel(i, x => x.verbrauch),
  }));
}

/** Energie ueber das Fenster in Wh (Trapezregel, Luecken werden uebersprungen). */
function _vlWh(reihen, hol) {
  let summe = 0;
  for (let i = 1; i < reihen.length; i++) {
    const dt = (reihen[i].ts - reihen[i - 1].ts) / 3600;
    if (dt > 0 && dt < 0.5) summe += (hol(reihen[i]) + hol(reihen[i - 1])) / 2 * dt;
  }
  return Math.round(summe);
}

function _vlZeit(ts, spanneS) {
  const d = new Date(ts * 1000);
  const zz = n => String(n).padStart(2, '0');
  return spanneS > 36 * 3600
    ? `${zz(d.getDate())}.${zz(d.getMonth() + 1)}.`
    : `${zz(d.getHours())}:${zz(d.getMinutes())}`;
}

function zeichneVerlauf() {
  const cv = $('vlChart');
  if (!cv || !_vlDaten) return;
  const R = _vlDaten.map(_vlZeile);
  const hinweis = $('vlHinweis');

  if (R.length < 2) {
    const ctx0 = cv.getContext('2d');
    ctx0.clearRect(0, 0, cv.width, cv.height);
    if (hinweis) hinweis.textContent = 'Für diesen Zeitraum liegt noch kein Verlauf vor.';
    _vlSummen(R);
    return;
  }

  const dpr = window.devicePixelRatio || 1;
  const b = cv.getBoundingClientRect();
  cv.width  = Math.max(1, Math.round(b.width * dpr));
  cv.height = Math.max(1, Math.round(b.height * dpr));
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = b.width, H = b.height;
  ctx.clearRect(0, 0, W, H);

  const PL = 52, PR = 14, PT = 12, PB = 26;
  const CW = W - PL - PR, CH = H - PT - PB;

  // Gezeichnet wird geglaettet: 5-Sekunden-Rohwerte ergeben ein Rauschband, in
  // dem der Verlauf untergeht. Die Energiesummen unten bleiben bewusst auf den
  // ROHDATEN — geglaettete Kurven duerfen die Bilanz nicht verfaelschen.
  const G = _vlGlaetten(R);
  const maxW = Math.max(60, ...G.map(r => Math.max(r.rein, r.verbrauch ?? 0)));
  const x = i => PL + CW * i / (G.length - 1);
  const y = w => PT + CH * (1 - Math.min(1, w / maxW));

  // Gitter und Achsenbeschriftung
  ctx.strokeStyle = 'rgba(148,163,184,.18)';
  ctx.fillStyle   = 'rgba(148,163,184,.75)';
  ctx.font = '10px system-ui, sans-serif';
  ctx.lineWidth = 1;
  [0, .25, .5, .75, 1].forEach(f => {
    const w = maxW * f, yy = Math.round(y(w)) + .5;
    ctx.beginPath(); ctx.moveTo(PL, yy); ctx.lineTo(PL + CW, yy); ctx.stroke();
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(Math.round(w) + ' W', PL - 7, yy);
  });

  const spanne = R[R.length - 1].ts - R[0].ts;
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  [0, .25, .5, .75, 1].forEach(f => {
    const i = Math.round(f * (G.length - 1));
    ctx.fillText(_vlZeit(G[i].ts, spanne), x(i), H - PB + 7);
  });

  // Gestapelte Flaechen: Landstrom unten, Solar als Zuwachs darueber
  const flaeche = (oben, unten, farbe, deckkraft) => {
    ctx.beginPath();
    ctx.moveTo(x(0), y(unten(G[0])));
    for (let i = 0; i < G.length; i++) ctx.lineTo(x(i), y(oben(G[i])));
    for (let i = G.length - 1; i >= 0; i--) ctx.lineTo(x(i), y(unten(G[i])));
    ctx.closePath();
    ctx.globalAlpha = deckkraft; ctx.fillStyle = farbe; ctx.fill(); ctx.globalAlpha = 1;
  };
  flaeche(r => r.rein, r => r.ls, VL_FARBEN.solar, .8);
  flaeche(r => r.ls, () => 0, VL_FARBEN.landstrom, .5);

  const linie = (hol, farbe, breite) => {
    ctx.beginPath();
    G.forEach((r, i) => {
      const v = hol(r);
      if (v == null) return;
      i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v));
    });
    ctx.strokeStyle = farbe; ctx.lineWidth = breite; ctx.stroke();
  };
  linie(r => r.ls, VL_FARBEN.landstrom, 1.4);
  linie(r => r.verbrauch, VL_FARBEN.verbrauch, 2);

  if (hinweis) {
    hinweis.textContent =
      `Flächen: Erzeugung gestapelt. Linie: Verbrauch. ${R.length} Punkte über `
      + `${spanne >= 86400 ? (spanne / 86400).toFixed(1) + ' Tage' : (spanne / 3600).toFixed(1) + ' Stunden'}.`;
  }
  _vlSummen(R);
}

function _vlSummen(R) {
  const box = $('vlSummen');
  if (!box) return;
  const zeilen = [
    ['Landstrom', VL_FARBEN.landstrom, _vlWh(R, r => r.ls)],
    ['Solar',     VL_FARBEN.solar,     _vlWh(R, r => r.so)],
    ['Verbrauch', VL_FARBEN.verbrauch, _vlWh(R, r => r.verbrauch ?? 0)],
  ];
  box.innerHTML = zeilen.map(([n, f, wh]) => `<div class="vl-summe-zeile">
      <span class="vl-punkt" style="background:${f}"></span>
      <span class="vl-name">${n}</span>
      <span class="vl-wh">${wh >= 1000 ? (wh / 1000).toFixed(2) + ' kWh' : wh + ' Wh'}</span>
    </div>`).join('');
}

// Neu zeichnen, wenn sich die Groesse aendert — die Canvas-Bitmap haengt an der
// Pixeldichte und wuerde sonst verzerrt stehenbleiben.
let _vlResizeRaf = null;
window.addEventListener('resize', () => {
  if (!$('verlaufOverlay') || $('verlaufOverlay').classList.contains('hidden')) return;
  if (_vlResizeRaf) cancelAnimationFrame(_vlResizeRaf);
  _vlResizeRaf = requestAnimationFrame(() => { _vlResizeRaf = null; zeichneVerlauf(); });
});
