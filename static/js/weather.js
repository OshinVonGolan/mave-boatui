// ── Wetter ──────────────────────────────────────────────────────────────────
//
// Die Kachel beantwortet "wie wird es". Die Detailseite beantwortet die Frage,
// die davor kommt: WANN geht es los, und was zieht durch. Das ist eine andere
// Frage, und sie braucht Stundenwerte — aus einem Tagesmaximum von 22 Knoten
// liest niemand ab, ob das eine Böe am Nachmittag war oder der ganze Tag.
//
// Ort und Modell sind wählbar. Ort, weil die Frage vor dem Ablegen selten
// "wie wird es hier" ist, sondern "wie wird es dort, wo ich hinwill". Modell,
// weil zwei Modelle, die dasselbe sagen, eine belastbare Vorhersage sind —
// und zwei, die auseinanderlaufen, eine Warnung. Das sieht man nur, wenn man
// vergleichen kann.

let _wxData      = null;    // Vorhersage zum gewählten Ort
let _wxOrte      = [];      // gepflegte Favoriten
let _wxModelle   = {};      // Kennung -> Anzeigename
let _wxModell    = 'auto';
let _wxIndex     = 0;       // Zeiger in die Liste aus _wxOrtListe()
let _wxVergleich = null;    // Modellvergleich, erst auf Wunsch geholt
let _wxLaeuft    = false;

// Zwischenspeicher im Browser: beim Durchschalten soll die Kachel nicht bei
// jedem Tipp leer werden. Der Server hält seinen eigenen (30 min) — dieser
// hier spart zusätzlich den Weg über das Netz.
const _wxCache = new Map();

const _WX_ORT_SPEICHER = 'mave.wetter.ort';

/** Ortsnamen kommen aus den Einstellungen und gehen als HTML raus. */
function _wxEsc(t) {
  return String(t == null ? '' : t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Kleinkram, den ein Segler lesen will ────────────────────────────────────

// Beaufort aus Knoten. Nicht Nostalgie: Segelentscheidungen ("zweites Reff")
// hängen an Bft, nicht an Knoten, und die Grenzen sind nicht gleichmäßig.
const _WX_BFT = [1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64];
function wxBft(kn) {
  if (kn == null) return null;
  let b = 0;
  while (b < _WX_BFT.length && kn >= _WX_BFT[b]) b++;
  return b;
}

/** Das Wettersymbol allein sagt nicht, ob „bedeckt" oder „Nebel" gemeint ist. */
function wxLage(code) {
  if (code == null) return '';
  const t = {
    0: 'klar', 1: 'überwiegend klar', 2: 'wechselnd bewölkt', 3: 'bedeckt',
    45: 'Nebel', 48: 'Reifnebel', 51: 'leichter Niesel', 53: 'Niesel',
    55: 'starker Niesel', 56: 'gefrierender Niesel', 57: 'gefrierender Niesel',
    61: 'leichter Regen', 63: 'Regen', 65: 'starker Regen',
    66: 'gefrierender Regen', 67: 'gefrierender Regen',
    71: 'leichter Schnee', 73: 'Schnee', 75: 'starker Schnee', 77: 'Schneegriesel',
    80: 'Schauer', 81: 'Schauer', 82: 'kräftige Schauer',
    85: 'Schneeschauer', 86: 'Schneeschauer',
    95: 'Gewitter', 96: 'Gewitter mit Hagel', 99: 'Gewitter mit Hagel',
  }[code];
  return t || '';
}

const _WX_STRICHE = ['N', 'NNO', 'NO', 'ONO', 'O', 'OSO', 'SO', 'SSO',
                     'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
function wxStrich(grad) {
  if (grad == null) return '';
  return _WX_STRICHE[Math.round(((grad % 360) + 360) % 360 / 22.5) % 16];
}

/** Zeitstempel von Open-Meteo ("2026-09-05T14:00") als lokale Zeit. */
function _wxZeit(t) {
  if (!t) return null;
  const d = new Date(t);
  return isNaN(d.getTime()) ? null : d;
}

function _wxUhr(t) {
  const d = _wxZeit(t);
  return d ? String(d.getHours()).padStart(2, '0') : '';
}

// ── Orte ────────────────────────────────────────────────────────────────────

/**
 * Die Liste, durch die getippt wird: Favoriten, danach die eigene Position.
 *
 * Die Position steht bewusst NICHT in den Einstellungen — sie ist keine
 * Einstellung, sondern ein Messwert. Sie taucht auf, sobald der Router einen
 * Fix hat, und verschwindet wieder, wenn nicht.
 */
function _wxOrtListe() {
  const liste = _wxOrte.map(o => ({ ...o }));
  const p = (typeof _lastData !== 'undefined' && _lastData) ? _lastData.position : null;
  if (p && typeof p.lat === 'number') {
    liste.push({ name: 'An Bord', lat: p.lat, lon: p.lon, anBord: true });
  }
  // Ganz ohne alles: der Server nimmt seine Vorgabe. Ein leerer Zustand wäre
  // schlechter als eine Vorhersage, die man nicht ausgewählt hat.
  if (!liste.length) liste.push({ name: 'Lübecker Bucht', lat: null, lon: null });
  return liste;
}

function _wxOrt() {
  const liste = _wxOrtListe();
  // Auch nach unten prüfen: im localStorage kann alles stehen.
  if (!(_wxIndex >= 0 && _wxIndex < liste.length)) _wxIndex = 0;
  return liste[_wxIndex];
}

/** Ein Tipp auf den Ortsnamen: einen weiter. */
function wxOrtWeiter(richtung = 1) {
  const n = _wxOrtListe().length;
  _wxIndex = ((_wxIndex + richtung) % n + n) % n;
  try { localStorage.setItem(_WX_ORT_SPEICHER, String(_wxIndex)); } catch (_) {}
  _wxVergleich = null;
  _renderWeather();
  _renderWetterSeite();
  fetchWeather();
}

function wxModellSetzen(kennung) {
  if (!(kennung in _wxModelle)) return;
  _wxModell = kennung;
  _wxCache.clear();
  _wxVergleich = null;
  _renderWetterSeite();
  fetchWeather();
  // Die Wahl gehört zu diesem Boot, nicht zu diesem Browser — also speichern.
  fetch('/api/settings', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ wetter: { modell: kennung } }),
  }).catch(() => {});
}

// ── Symbole ─────────────────────────────────────────────────────────────────

// Wetter-Symbol als Inline-SVG (Icon-System, keine Emojis).
// Ohne Wettercode bleibt es beim schlichten Punkt — sonst würde eine
// fehlende Angabe wie "bewölkt" aussehen.
function _wxIconHtml(code, storm, size) {
  if (storm) return icon('thunder', { size });
  if (code == null) return '<span style="opacity:.5">·</span>';
  return icon(weatherIcon(code), { size });
}

/** Windpfeil: zeigt, WOHIN der Wind weht (meteorologisch kommt er von `grad`). */
function _wxPfeil(grad, size = 14) {
  if (grad == null) return '';
  return `<svg class="wx-pfeil" width="${size}" height="${size}" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
    stroke-linejoin="round" style="transform:rotate(${(grad + 180) % 360}deg)"
    aria-hidden="true"><path d="M12 20V4M6 10l6-6 6 6"/></svg>`;
}

function _wxDayName(date, i) {
  if (i === 0) return 'Heute';
  const d = _wxZeit(date);
  return d ? d.toLocaleDateString('de-DE', { weekday: 'short' }) : '';
}

// ── Die Kachel ──────────────────────────────────────────────────────────────

/**
 * Die Kachel.
 *
 * Sie zeigte fuenf gleich grosse Tagesspalten — das beantwortet "wie wird die
 * Woche", aber nicht die Frage, die man morgens beim Kaffee stellt: was ist
 * HEUTE los. Jetzt steht heute oben und ausfuehrlich, mit Wind, Boeen und
 * Richtung; darunter der Wind der naechsten zwoelf Stunden als Streifen, und
 * erst dann die uebrigen Tage, knapp.
 */
function _renderWeather() {
  const ort = _wxOrt();
  const nameEl = $('wxOrtName');
  if (nameEl) nameEl.textContent = ort.name;
  ortPunkte('wxPunkte', _wxOrtListe().length, _wxIndex);

  const d = _wxData;
  const tage = (d && d.tage) || [];
  const heute = tage[0] || {};
  const jetzt = _wxStundeJetzt() || {};

  const kopf = $('wxHeute');
  const wind = jetzt.wind != null ? jetzt.wind : heute.wind;
  const boe  = jetzt.boe  != null ? jetzt.boe  : heute.gust;
  const dir  = jetzt.dir  != null ? jetzt.dir  : heute.dir;
  const welle = jetzt.welle != null ? jetzt.welle : heute.wave;

  if (kopf) {
    // Der Wind kommt aus der aktuellen Stunde, nicht aus dem Tagesmaximum:
    // "heute bis 22 kn" hilft um neun Uhr morgens nicht weiter.
    const bft = wxBft(wind);
    const faktor = (wind && boe) ? boe / wind : null;
    const lage = wxLage(jetzt.wmo != null ? jetzt.wmo : heute.wmo);
    const wmo = jetzt.wmo != null ? jetzt.wmo : heute.wmo;

    // EIN grosser Wert, nicht zwei. Vorher standen Temperatur und Wind gleich
    // gross nebeneinander und stritten um den Blick — an Bord ist der Wind
    // die Frage. Die Luft steht daneben, kleiner, und die beiden zweiten
    // Zeilen laufen auf derselben Schriftlinie durch: dadurch liest sich das
    // als EIN Kopf und nicht als zwei Kaesten.
    const spanne = (heute.tmin != null && heute.tmax != null)
      ? ` · ${Math.round(heute.tmin)}–${Math.round(heute.tmax)}°` : '';
    kopf.innerHTML = `
      <div class="wx-k-wind">
        <span class="wx-k-pfeil">${_wxPfeil(dir, 30)}</span>
        <span class="wx-k-gross">${wind != null ? Math.round(wind) : '--'}<small>kn</small></span>
        <span class="wx-k-boe${faktor && faktor >= 1.5 ? ' boeig' : ''}">Böen <b>${
          boe != null ? Math.round(boe) : '--'}</b></span>
      </div>
      <div class="wx-k-luft">
        <span class="wx-k-ikon">${_wxIconHtml(wmo, heute.storm, 26)}</span>
        <span class="wx-k-mittel">${jetzt.temp != null ? Math.round(jetzt.temp) : '--'}°</span>
      </div>
      <div class="wx-k-unter">${wxStrich(dir) || '--'}${bft != null ? ` · ${bft} Bft` : ''}</div>
      <div class="wx-k-unter">${_wxEsc(lage)}${spanne}</div>`;
  }

  // Der Fuss: vier Angaben in einem festen Raster. Sie brauchen keinen
  // eigenen Rahmen — sie sind Beiwerk, und Beiwerk bekommt keine Flaeche.
  const werte = $('wxWerte');
  if (werte) {
    const paar = (was, wert) => wert
      ? `<span><i>${was}</i> <b>${wert}</b></span>` : '';
    werte.innerHTML = [
      paar('Welle', welle != null ? welle.toFixed(1) + ' m' : ''),
      paar('Regen', heute.pop != null ? heute.pop + ' %' : ''),
      paar('Druck', jetzt.druck != null ? Math.round(jetzt.druck) + ' hPa' : ''),
      paar('Sonne', _wxUhrZeit(heute.unter) ? 'bis ' + _wxUhrZeit(heute.unter) : ''),
    ].filter(Boolean).join('');
  }

  _wxTagStreifen();

  // Halbe Kachel: heute, knapp
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  const iconEl = $('wxHalfIcon');
  if (iconEl) iconEl.innerHTML = _wxIconHtml(heute.wmo, heute.storm, 40);
  set('wxHalfTemp', (jetzt.temp != null ? Math.round(jetzt.temp)
                     : heute.tmax != null ? Math.round(heute.tmax) : '--') + '°');
  set('wxHalfWind', (jetzt.wind != null ? Math.round(jetzt.wind)
                     : heute.wind != null ? Math.round(heute.wind) : '--') + ' kn');
  set('wxHalfPop',  (heute.pop != null ? heute.pop : '--') + '%');
}

/**
 * Der Wind der naechsten zwoelf Stunden.
 *
 * Er beantwortet die eine Frage, die eine Tageszahl nie beantwortet: frischt
 * es auf oder schlaeft es ein.
 *
 * Die erste Fassung war nicht abzulesen (Eignermeldung mit Foto). Drei Gruende,
 * alle behoben: der Streifen war 44 Pixel hoch, die Skala begann bei null — bei
 * 5 Knoten Wind und 19 Knoten Spitze klebte die Linie am Boden —, und es gab
 * keine einzige Zahl darin ausser der Obergrenze.
 *
 * Jetzt: hoeher, eine Hilfslinie auf einem runden Wert MIT Beschriftung, der
 * Boeenbereich als Flaeche darueber, und die Spitze mit ihrer Zahl markiert.
 */
function _wxTagStreifen() {
  const cv = $('wxTagCanvas');
  if (!cv || !_wxData) return;
  const alle = _wxData.stunden || [];
  const jetzt = _wxStundeJetzt();
  // Drei Stunden Vergangenheit gehoeren dazu. Vorher fing der Streifen bei
  // JETZT an — dann ist die Jetzt-Marke der linke Rand und sagt nichts. Mit
  // dem Stueck davor beantwortet die Kurve die eigentliche Frage: frischt es
  // auf oder schlaeft es ein.
  const RUECK = 3;
  const iJetzt = Math.max(0, alle.indexOf(jetzt));
  const von = Math.max(0, iJetzt - RUECK);
  const st = alle.slice(von, iJetzt + 13);
  const iNun = iJetzt - von;
  if (st.length < 2) return;

  const dpr = window.devicePixelRatio || 1;
  const W = cv.offsetWidth, H = cv.offsetHeight;
  if (!W || !H) return;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  // Vier Schichten, nicht acht. Vorher lagen hier eine Achse mit zwei Zahlen,
  // eine Hilfslinie, ein Nachtblock, eine Flaeche zwischen Wind und Boee,
  // zwei Linien, ein Punkt mit Beschriftung und die Stunden uebereinander —
  // in 96 Pixeln Hoehe. Genau das war unlesbar (Eignerurteil).
  //
  // Geblieben ist: Nacht als Hauch, der Wind als Flaeche mit Linie, die Boee
  // als duenne Linie darueber, und EINE Zahl — die hoechste Boe. Die Skala
  // braucht keine Beschriftung, wenn ihr Hoechstwert dransteht.
  const pad = { t: 16, b: 17 };
  const cH = H - pad.t - pad.b;
  const spitze = Math.max(...st.map(s => s.boe || s.wind || 0), 0);
  // Nie unter 10 kn Skala: sonst wirkt eine Flaute wie ein Sturm. Sonst nur
  // so viel Luft ueber der Spitze, wie ihre Beschriftung braucht.
  //
  // Aufgerundet wurde frueher auf Fuenferschritte, weil eine Achse mit Zahlen
  // dranstand — 18 kn wurden dann zu einer Skala bis 25, und ein gutes Drittel
  // des Bildes blieb leer. Die Achse gibt es nicht mehr, also auch keinen
  // Grund fuer glatte Zahlen.
  const max = Math.max(10, spitze * 1.18);
  const n = st.length - 1;
  const xOf = i => (i / n) * W;
  const yOf = v => pad.t + (1 - v / max) * cH;

  // 1. Nacht — 20 Knoten um drei Uhr sind etwas anderes als um drei. Als
  //    Hauch und nicht als Block: der Block war der dunkelste Fleck im Bild
  //    und stritt mit der Kurve.
  const tag = (_wxData.tage || [])[0] || {};
  const auf = _wxZeit(tag.auf), unter = _wxZeit(tag.unter);
  if (auf && unter) {
    // Ueber die VOLLE Hoehe: als Kasten in der Bildmitte sah es aus wie ein
    // zweites Diagramm, das ueber dem ersten liegt.
    //
    // Und mit weicher Kante. Der Fleck war leise genug, aber seine SENKRECHTE
    // KANTE fing den Blick — sie sah aus wie eine Linie im Diagramm, und die
    // Sonne geht ohnehin nicht auf die Minute unter. Sie laeuft jetzt ueber
    // eine halbe Stunde ein und aus.
    const nacht = st.map(s => {
      const t = _wxZeit(s.t);
      if (!t) return false;
      const h = t.getHours();
      return h < auf.getHours() || h >= unter.getHours();
    });
    let i = 0;
    while (i < nacht.length) {
      if (!nacht[i]) { i++; continue; }
      let j = i;
      while (j + 1 < nacht.length && nacht[j + 1]) j++;
      const a = xOf(Math.max(0, i - .5)), b = xOf(Math.min(n, j + .5));
      const weich = Math.min(14, Math.max(2, (b - a) / 4));
      const lauf = ctx.createLinearGradient(a, 0, b, 0);
      const ton = 'rgba(148,163,184,';
      lauf.addColorStop(0, ton + '0)');
      lauf.addColorStop(Math.min(.5, weich / (b - a)), ton + '.055)');
      lauf.addColorStop(Math.max(.5, 1 - weich / (b - a)), ton + '.055)');
      lauf.addColorStop(1, ton + (b >= W - 1 ? '.055)' : '0)'));
      ctx.fillStyle = lauf;
      ctx.fillRect(a, 0, b - a, H);
      i = j + 1;
    }
  }

  // 2. Der Wind als Flaeche bis zum Boden — die Hoehe IST die Staerke.
  const akzent = _wxFarbe('--accent', '#38bdf8');
  const pfad = () => {
    ctx.beginPath();
    let erste = true;
    st.forEach((s, i) => {
      if (s.wind == null) return;
      erste ? ctx.moveTo(xOf(i), yOf(s.wind)) : ctx.lineTo(xOf(i), yOf(s.wind));
      erste = false;
    });
  };
  const boden = pad.t + cH;
  pfad();
  ctx.lineTo(W, boden); ctx.lineTo(0, boden); ctx.closePath();
  const fuell = ctx.createLinearGradient(0, pad.t, 0, boden);
  fuell.addColorStop(0, _wxAlpha(akzent, .30));
  fuell.addColorStop(1, _wxAlpha(akzent, .02));
  ctx.fillStyle = fuell;
  ctx.fill();

  pfad();
  ctx.strokeStyle = akzent; ctx.lineWidth = 2.2; ctx.lineJoin = 'round';
  ctx.stroke();

  // 3. Die Boee nur als Linie. Die Flaeche dazwischen war ein brauner Fleck.
  ctx.beginPath();
  let erste = true;
  st.forEach((s, i) => {
    const v = s.boe != null ? s.boe : s.wind;
    if (v == null) return;
    erste ? ctx.moveTo(xOf(i), yOf(v)) : ctx.lineTo(xOf(i), yOf(v));
    erste = false;
  });
  ctx.strokeStyle = 'rgba(251,146,60,.75)';
  ctx.lineWidth = 1.4; ctx.lineJoin = 'round';
  ctx.stroke();

  // 4. Die Jetzt-Linie. Sie steht ueber den Kurven, damit sie nicht unter der
  //    Windflaeche verschwindet, und bleibt duenn und gestrichelt: sie ist
  //    eine Marke an der Zeitachse und kein Messwert.
  if (iNun > 0 && iNun < n) {
    const xn = xOf(iNun);
    ctx.save();
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = 'rgba(226,232,240,.45)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(xn, 2); ctx.lineTo(xn, pad.t + cH);
    ctx.stroke();
    ctx.restore();
  }

  // 5. Die hoechste Boe mit ihrer Zahl — darauf richtet man sich ein, und sie
  //    ersetzt die Achsenbeschriftung.
  let iMax = 0, vMax = -1;
  st.forEach((s, i) => {
    const v = s.boe != null ? s.boe : s.wind;
    if (v != null && v > vMax) { vMax = v; iMax = i; }
  });
  if (vMax > 0) {
    // Punkt und Zahl bleiben im Bild. Liegt die Spitze auf der ersten Stunde
    // — was oft so ist, es ist die jetzige —, sass der Punkt halb ausserhalb
    // und die Zahl lag auf der Linie.
    const x = Math.min(W - 3, Math.max(3, xOf(iMax)));
    const y = Math.max(11, yOf(vMax));
    ctx.fillStyle = 'rgba(251,146,60,.95)';
    ctx.beginPath(); ctx.arc(x, y, 2.6, 0, Math.PI * 2); ctx.fill();
    ctx.font = '600 11px sans-serif';
    const rechts = x > W - 46;
    ctx.textAlign = rechts ? 'right' : 'left';
    ctx.fillText(`${Math.round(vMax)} kn`, x + (rechts ? -7 : 7), y - 7);
  }

  // Die Stunden darunter, alle drei. Die erste ist jetzt und steht deshalb
  // links buendig, die letzte rechts — sonst haengen sie halb ueber der Kante.
  // Vom Jetzt-Punkt aus gezaehlt, nicht vom linken Rand: so sitzt eine
  // Beschriftung genau unter der Jetzt-Linie, und die Marke bekommt ihre Zeit.
  ctx.font = '11px sans-serif';
  st.forEach((s, i) => {
    if ((i - iNun) % 3) return;
    ctx.fillStyle = i === iNun ? 'rgba(226,232,240,.85)' : 'rgba(148,163,184,.7)';
    ctx.textAlign = i === 0 ? 'left' : (i === n ? 'right' : 'center');
    ctx.fillText(_wxUhr(s.t), Math.min(W, Math.max(0, xOf(i))), H - 3);
  });
}

/** Eine Farbe aus dem Stylesheet mit Deckkraft versehen.
 *
 * Die Design-Marken stehen als `#rrggbb` da; `color-mix` gaebe es zwar, aber
 * die Leinwand nimmt keine CSS-Funktionen entgegen — sie will eine fertige
 * Farbe. */
function _wxAlpha(farbe, a) {
  const m = /^#([0-9a-f]{6})$/i.exec((farbe || '').trim());
  if (!m) return farbe;
  const z = parseInt(m[1], 16);
  return `rgba(${(z >> 16) & 255},${(z >> 8) & 255},${z & 255},${a})`;
}

// ── Abruf ───────────────────────────────────────────────────────────────────

function _wxSchluessel(ort) {
  return `${ort.lat == null ? '' : ort.lat.toFixed(3)},${ort.lon == null ? '' : ort.lon.toFixed(3)},${_wxModell}`;
}

/** Die gepflegten Orte und die Modellwahl — einmal beim Start und nach dem Speichern. */
function fetchWetterOrte() {
  return fetch('/api/wetter/orte')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      _wxOrte    = Array.isArray(d.orte) ? d.orte : [];
      _wxModelle = d.modelle || {};
      _wxModell  = d.modell || 'auto';
      try {
        const i = parseInt(localStorage.getItem(_WX_ORT_SPEICHER), 10);
        if (!isNaN(i)) _wxIndex = i;
      } catch (_) {}
      _renderWeather();
      _renderWetterSeite();
    })
    .catch(() => {});
}

function fetchWeather() {
  const ort = _wxOrt();
  const schluessel = _wxSchluessel(ort);
  const bekannt = _wxCache.get(schluessel);
  // Ohne Zwischenspeicher fuer diesen Ort: erst LEEREN. Sonst stehen ein paar
  // Sekunden lang die Zahlen des vorherigen Ortes unter dem neuen Namen — und
  // das ist schlimmer als ein Strich, weil es aussieht, als waere es fertig.
  _wxData = bekannt || null;
  _renderWeather();
  _renderWetterSeite();

  const p = new URLSearchParams();
  if (ort.lat != null) { p.set('lat', ort.lat.toFixed(4)); p.set('lon', ort.lon.toFixed(4)); }
  p.set('modell', _wxModell);
  _wxLaeuft = true;
  return fetch('/api/weather?' + p)
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      // Die Antwort kann zu einem Ort gehören, von dem längst weggetippt
      // wurde. Dann in den Speicher, aber nicht auf den Schirm.
      _wxCache.set(schluessel, d);
      if (_wxSchluessel(_wxOrt()) !== schluessel) return;
      _wxData = d;
      _renderWeather();
      _renderWetterSeite();
    })
    .catch(() => {})
    .finally(() => { _wxLaeuft = false; });
}

// ── Detailseite ─────────────────────────────────────────────────────────────

function openWetter() {
  _closeAllOverlays();
  history.pushState({ overlay: 'wetter' }, '', '#wetter');
  _wetterSeiteAnzeigen();
}

function _wetterSeiteAnzeigen() {
  $('wxOverlay').classList.remove('hidden');
  _renderWetterSeite();
  if (!_wxData) fetchWeather();
  // Nach zwei Frames haben die Leinwände ihre echte Breite — vorher messen
  // sie 0 und alles Gezeichnete verschwindet still.
  requestAnimationFrame(() => requestAnimationFrame(_wxDiagramme));
}

function closeWetter() {
  $('wxOverlay').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

function _wetterOffen() {
  const o = $('wxOverlay');
  return o && !o.classList.contains('hidden');
}

function _renderWetterSeite() {
  if (!_wetterOffen()) return;
  const ort = _wxOrt();

  // Kopf: Orte und Modelle als Reihe von Schaltern.
  const orte = $('wxOrtLeiste');
  if (orte) {
    orte.innerHTML = _wxOrtListe().map((o, i) =>
      `<button class="wx-chip${i === _wxIndex ? ' aktiv' : ''}" data-index="${i}">${
        o.anBord ? icon('pin', { size: 12 }) + ' ' : ''}${_wxEsc(o.name)}</button>`).join('');
  }
  const modelle = $('wxModellLeiste');
  if (modelle) {
    modelle.innerHTML = Object.entries(_wxModelle).map(([k, name]) =>
      `<button class="wx-chip${k === _wxModell ? ' aktiv' : ''}" data-modell="${_wxEsc(k)}">${_wxEsc(name)}</button>`).join('');
  }

  const d = _wxData;
  const kopf = $('wxJetzt');
  if (!d) {
    if (kopf) kopf.innerHTML = '<div class="wx-leer">Wetter wird geholt…</div>';
    // Die Leinwaende zeigen sonst weiter den vorherigen Ort.
    for (const id of ['wxWindCanvas', 'wxWelleCanvas', 'wxRegenCanvas']) {
      const c = $(id);
      if (c && c.width) c.getContext('2d').clearRect(0, 0, c.width, c.height);
    }
    const tab = $('wxTage');
    if (tab) tab.innerHTML = '';
    return;
  }

  const jetzt = _wxStundeJetzt();
  const heute = (d.tage || [])[0] || {};
  const s = jetzt || {};
  const bft = wxBft(s.wind);
  const boeFaktor = (s.wind && s.boe) ? s.boe / s.wind : null;

  const feld = (label, wert, zusatz) => `<div class="wx-feld">
      <div class="wx-feld-kopf">${label}</div>
      <div class="wx-feld-wert">${wert}</div>
      ${zusatz ? `<div class="wx-feld-sub">${zusatz}</div>` : ''}</div>`;

  const zeilen = [];
  zeilen.push(feld('Wind',
    `${s.wind != null ? Math.round(s.wind) : '--'} <small>kn</small>`,
    bft != null ? `${bft} Bft` : ''));
  zeilen.push(feld('Böen',
    `${s.boe != null ? Math.round(s.boe) : '--'} <small>kn</small>`,
    // Der Böenfaktor ist die eigentliche Auskunft: bei 1,3 ist der Wind
    // gleichmäßig, ab etwa 1,5 kommt er in Schlägen — und dann reffe ich
    // nach der Böe, nicht nach dem Mittelwind.
    boeFaktor ? `Faktor ${boeFaktor.toFixed(1)}${boeFaktor >= 1.5 ? ' · böig' : ''}` : ''));
  zeilen.push(feld('Richtung',
    `${_wxPfeil(s.dir, 20)} ${wxStrich(s.dir) || '--'}`,
    s.dir != null ? `${Math.round(s.dir)}°` : ''));
  if (s.welle != null) {
    zeilen.push(feld('Welle', `${s.welle.toFixed(1)} <small>m</small>`,
      [s.welle_periode != null ? `${s.welle_periode.toFixed(1)} s` : '',
       wxStrich(s.welle_dir)].filter(Boolean).join(' · ')));
  }
  zeilen.push(feld('Luft', `${s.temp != null ? Math.round(s.temp) : '--'}°`,
    s.regen ? `${s.regen.toFixed(1)} mm/h` : ''));
  zeilen.push(feld('Druck',
    `${s.druck != null ? Math.round(s.druck) : '--'} <small>hPa</small>`,
    _wxDruckTrend()));
  zeilen.push(feld('Sonne',
    `${_wxUhrZeit(heute.auf)}`, `bis ${_wxUhrZeit(heute.unter)}`));

  if (kopf) {
    kopf.innerHTML = `
      <div class="wx-jetzt-kopf">
        <span class="wx-jetzt-icon">${_wxIconHtml(s.wmo != null ? s.wmo : heute.wmo, heute.storm, 44)}</span>
        <div>
          <div class="wx-jetzt-ort">${_wxEsc(ort.name)}</div>
          <div class="wx-jetzt-quelle">${_wxEsc(d.modell_name || '')}${
            s.welle == null ? ' · kein Seegang für diesen Punkt' : ''}</div>
        </div>
      </div>
      <div class="wx-felder">${zeilen.join('')}</div>`;
  }

  _wxTageTabelle();
  _wxDiagramme();
}

function _wxUhrZeit(t) {
  const d = _wxZeit(t);
  return d ? d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : '--:--';
}

/**
 * Die Stunde, in der wir gerade SIND — nicht die naechstgelegene.
 *
 * Der Unterschied faellt erst spaet auf: um 23:40 waere die naechstgelegene
 * Stunde 00:00, also der naechste Tag. Unter der Ueberschrift "jetzt" stuende
 * dann eine Vorhersage. Die angebrochene Stunde ist die ehrlichere Antwort.
 *
 * Der Anfang der Liste ist Mitternacht des heutigen Tages, sie reicht also
 * immer bis in die Vergangenheit.
 */
function _wxStundeJetzt() {
  const st = (_wxData && _wxData.stunden) || [];
  if (!st.length) return null;
  const jetzt = Date.now();
  let beste = null;
  for (const s of st) {
    const d = _wxZeit(s.t);
    if (d && d.getTime() <= jetzt) beste = s;
  }
  // Faengt die Liste erst in der Zukunft an (kann bei Zeitzonen-Versatz
  // passieren), ist ihr erster Eintrag das Naechstbeste.
  return beste || st[0];
}

/**
 * Fällt oder steigt der Druck?
 *
 * Drei Stunden ist die übliche Spanne, und die Schwellen sind die aus der
 * Seefahrt: ab 1 hPa/3 h passiert etwas, ab 3 hPa/3 h passiert es schnell.
 */
function _wxDruckTrend() {
  const st = (_wxData && _wxData.stunden) || [];
  const jetzt = _wxStundeJetzt();
  if (!jetzt || jetzt.druck == null) return '';
  const i = st.indexOf(jetzt);
  const spaeter = st[i + 3];
  if (!spaeter || spaeter.druck == null) return '';
  const delta = spaeter.druck - jetzt.druck;
  const pfeil = delta > 0.3 ? '↑' : delta < -0.3 ? '↓' : '→';
  const wort = Math.abs(delta) >= 3 ? ' rasch' : Math.abs(delta) >= 1 ? '' : ' gleichbleibend';
  return `${pfeil} ${delta > 0 ? '+' : ''}${delta.toFixed(1)} hPa/3 h${wort}`;
}

function _wxTageTabelle() {
  const ziel = $('wxTage');
  if (!ziel || !_wxData) return;
  const tage = _wxData.tage || [];
  const hatSee = tage.some(t => t.wave != null);
  ziel.innerHTML = `<table class="wx-tab">
    <thead><tr>
      <th></th><th></th><th>Temp</th><th>Wind</th><th>Böen</th>
      <th>Ri.</th>${hatSee ? '<th>Welle</th>' : ''}<th>Regen</th>
    </tr></thead><tbody>${tage.map((t, i) => `<tr>
      <td class="wx-tab-tag">${_wxDayName(t.date, i)}</td>
      <td>${_wxIconHtml(t.wmo, t.storm, 20)}</td>
      <td>${t.tmax != null ? Math.round(t.tmax) : '--'}° <span class="wx-tab-min">${t.tmin != null ? Math.round(t.tmin) : '--'}°</span></td>
      <td><b>${t.wind != null ? Math.round(t.wind) : '--'}</b> <span class="wx-tab-bft">${wxBft(t.wind) != null ? wxBft(t.wind) + ' Bft' : ''}</span></td>
      <td>${t.gust != null ? Math.round(t.gust) : '--'}</td>
      <td>${_wxPfeil(t.dir, 13)} ${wxStrich(t.dir)}</td>
      ${hatSee ? `<td>${t.wave != null ? t.wave.toFixed(1) + ' m' : '–'}</td>` : ''}
      <td>${t.pop != null ? t.pop + ' %' : '--'}${t.precip ? ` <span class="wx-tab-min">${t.precip.toFixed(1)} mm</span>` : ''}</td>
    </tr>`).join('')}</tbody></table>`;
}

// ── Die Diagramme ───────────────────────────────────────────────────────────

function _wxFarbe(name, rueckfall) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || rueckfall;
  } catch (_) { return rueckfall; }
}

/**
 * Grundgerüst für alle drei Streifen: gleiche Breite, gleiche Stunden,
 * gleiche Nachtschattierung. Gibt die Rechenhilfen zurück.
 *
 * Die Nacht wird hinterlegt, weil sie die halbe Auskunft ist: 25 Knoten am
 * Nachmittag sind etwas anderes als 25 Knoten um drei Uhr nachts.
 */
function _wxRahmen(cv, hoehe, stunden) {
  const dpr = window.devicePixelRatio || 1;
  const W = cv.offsetWidth || 600, H = hoehe;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const pad = { t: 10, r: 8, b: 18, l: 34 };
  const cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
  const n = Math.max(1, stunden.length - 1);
  const xOf = i => pad.l + (i / n) * cW;

  // Nacht: alles außerhalb von Auf- und Untergang des jeweiligen Tages.
  const tage = (_wxData && _wxData.tage) || [];
  ctx.fillStyle = 'rgba(128,128,160,0.10)';
  stunden.forEach((s, i) => {
    const d = _wxZeit(s.t);
    if (!d) return;
    const tag = tage.find(t => (t.date || '').slice(0, 10) === s.t.slice(0, 10));
    const auf = _wxZeit(tag && tag.auf), unter = _wxZeit(tag && tag.unter);
    if (!auf || !unter) return;
    if (d < auf || d >= unter) {
      const x0 = xOf(Math.max(0, i - 0.5)), x1 = xOf(Math.min(n, i + 0.5));
      ctx.fillRect(x0, pad.t, x1 - x0, cH);
    }
  });

  // Tagesgrenzen und Stundenbeschriftung
  ctx.strokeStyle = 'rgba(128,128,128,0.30)';
  ctx.fillStyle = 'rgba(128,128,128,0.75)';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'center';
  // Wie dicht die Stunden stehen duerfen, haengt an der Breite: auf dem Telefon
  // liegen 72 Stunden auf 330 Pixeln, und alle sechs Stunden eine Zahl waere
  // ein grauer Strich. Zusaetzlich weicht die Stundenzahl der Tagesmarke aus —
  // sonst stand dort "So06" ineinander.
  const proStunde = cW / Math.max(1, stunden.length);
  const takt = proStunde < 6 ? 12 : 6;
  const tagesMarken = [];
  stunden.forEach((s, i) => {
    const d = _wxZeit(s.t);
    if (!d) return;
    if (i === 0) {
      // Der linke Rand bekommt seinen Wochentag ohne Trennlinie — sonst faengt
      // die Achse mit einem namenlosen Tag an.
      tagesMarken.push(xOf(0) + 12);
      ctx.fillText(d.toLocaleDateString('de-DE', { weekday: 'short' }), xOf(0) + 12, H - 6);
      return;
    }
    if (d.getHours() === 0) {
      ctx.beginPath();
      ctx.moveTo(xOf(i), pad.t); ctx.lineTo(xOf(i), pad.t + cH); ctx.stroke();
      const x = xOf(i) + 15;
      tagesMarken.push(x);
      ctx.fillText(d.toLocaleDateString('de-DE', { weekday: 'short' }), x, H - 6);
    }
  });
  stunden.forEach((s, i) => {
    const d = _wxZeit(s.t);
    if (!d || d.getHours() % takt || d.getHours() === 0) return;
    const x = xOf(i);
    if (tagesMarken.some(m => Math.abs(m - x) < 20)) return;
    ctx.fillText(_wxUhr(s.t), x, H - 6);
  });

  // Jetzt-Marke
  const nun = Date.now();
  const iJetzt = stunden.findIndex(s => {
    const d = _wxZeit(s.t);
    return d && d.getTime() >= nun;
  });
  if (iJetzt > 0) {
    ctx.strokeStyle = _wxFarbe('--accent', '#38bdf8');
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(xOf(iJetzt), pad.t); ctx.lineTo(xOf(iJetzt), pad.t + cH); ctx.stroke();
    ctx.setLineDash([]);
  }
  return { ctx, W, H, pad, cW, cH, xOf };
}

function _wxSkala(r, max, einheit, stufen = 4) {
  const { ctx, pad, cW, cH } = r;
  ctx.strokeStyle = 'rgba(128,128,128,0.15)';
  ctx.fillStyle = 'rgba(128,128,128,0.75)';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'right';
  ctx.lineWidth = 1;
  for (let i = 0; i <= stufen; i++) {
    const y = pad.t + (i / stufen) * cH;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + cW, y); ctx.stroke();
    const wert = max - i * max / stufen;
    // Bei kleinen Spannen (Wellenhoehe in Metern) rundet Math.round alles auf
    // 0 und 1 — die Skala haette dann zweimal dieselbe Zahl.
    const txt = max <= 2 ? wert.toFixed(1) : String(Math.round(wert));
    ctx.fillText(i === 0 ? `${txt} ${einheit}` : txt, pad.l - 4, y + 3.5);
  }
}

function _wxLinie(r, werte, yOf, farbe, breite = 2) {
  const { ctx, xOf } = r;
  ctx.beginPath();
  ctx.strokeStyle = farbe; ctx.lineWidth = breite; ctx.lineJoin = 'round';
  let auf = false;
  werte.forEach((v, i) => {
    if (v == null) { auf = false; return; }
    auf ? ctx.lineTo(xOf(i), yOf(v)) : ctx.moveTo(xOf(i), yOf(v));
    auf = true;
  });
  ctx.stroke();
}

function _wxDiagramme() {
  if (!_wetterOffen() || !_wxData) return;
  const stunden = _wxData.stunden || [];
  if (!stunden.length) return;
  _wxWindZeichnen(stunden);
  _wxWelleZeichnen(stunden);
  _wxRegenZeichnen(stunden);
  _wxVergleichZeichnen();
}

function _wxWindZeichnen(stunden) {
  const cv = $('wxWindCanvas');
  if (!cv) return;
  const r = _wxRahmen(cv, 190, stunden);
  const { ctx, pad, cH, xOf } = r;

  const hoechst = Math.max(12, ...stunden.map(s => s.boe || s.wind || 0));
  const max = Math.ceil(hoechst / 5) * 5;
  const yOf = v => pad.t + (1 - v / max) * cH;
  _wxSkala(r, max, 'kn');

  // Böen als Fläche über dem Wind: die Fläche IST der Unterschied, und der
  // ist die Auskunft. Zwei Linien nebeneinander liest man nicht so.
  ctx.beginPath();
  let start = true;
  stunden.forEach((s, i) => {
    const v = s.boe != null ? s.boe : s.wind;
    if (v == null) return;
    start ? ctx.moveTo(xOf(i), yOf(v)) : ctx.lineTo(xOf(i), yOf(v));
    start = false;
  });
  for (let i = stunden.length - 1; i >= 0; i--) {
    const v = stunden[i].wind;
    if (v == null) continue;
    ctx.lineTo(xOf(i), yOf(v));
  }
  ctx.closePath();
  ctx.fillStyle = 'rgba(251,146,60,0.22)';   // orange, wie die Warnfarbe
  ctx.fill();

  _wxLinie(r, stunden.map(s => s.boe), yOf, 'rgba(251,146,60,0.85)', 1.4);
  _wxLinie(r, stunden.map(s => s.wind), yOf, _wxFarbe('--accent', '#38bdf8'), 2.2);

  // Windpfeile alle drei Stunden, unten im Bild. Ohne Richtung ist eine
  // Windvorhersage die Hälfte wert — eine Drehung von SW auf NW kann aus
  // Lee eine Legerwall-Küste machen.
  ctx.strokeStyle = _wxFarbe('--text3', '#94a3b8');
  ctx.lineWidth = 1.4;
  ctx.lineCap = 'round';
  const yP = pad.t + cH - 9;
  stunden.forEach((s, i) => {
    if (i % 3 || s.dir == null) return;
    const w = ((s.dir + 180) % 360) * Math.PI / 180;
    const x = xOf(i), l = 6;
    const dx = Math.sin(w) * l, dy = -Math.cos(w) * l;
    ctx.beginPath();
    ctx.moveTo(x - dx, yP - dy); ctx.lineTo(x + dx, yP + dy);
    // Spitze
    ctx.moveTo(x + dx, yP + dy);
    ctx.lineTo(x + dx - dx * 0.5 - dy * 0.4, yP + dy - dy * 0.5 + dx * 0.4);
    ctx.moveTo(x + dx, yP + dy);
    ctx.lineTo(x + dx - dx * 0.5 + dy * 0.4, yP + dy - dy * 0.5 - dx * 0.4);
    ctx.stroke();
  });
}

function _wxWelleZeichnen(stunden) {
  const box = $('wxWelleBox'), cv = $('wxWelleCanvas');
  if (!box || !cv) return;
  const hatWelle = stunden.some(s => s.welle != null);
  box.hidden = !hatWelle;
  if (!hatWelle) return;

  const r = _wxRahmen(cv, 120, stunden);
  const { ctx, pad, cH, xOf } = r;
  const max = Math.max(0.5, Math.ceil(Math.max(...stunden.map(s => s.welle || 0)) * 2) / 2);
  const yOf = v => pad.t + (1 - v / max) * cH;
  _wxSkala(r, max, 'm', 2);

  ctx.beginPath();
  let start = true;
  stunden.forEach((s, i) => {
    if (s.welle == null) return;
    start ? ctx.moveTo(xOf(i), yOf(s.welle)) : ctx.lineTo(xOf(i), yOf(s.welle));
    start = false;
  });
  ctx.lineTo(xOf(stunden.length - 1), pad.t + cH);
  ctx.lineTo(xOf(0), pad.t + cH);
  ctx.closePath();
  ctx.fillStyle = 'rgba(56,189,248,0.20)';
  ctx.fill();
  _wxLinie(r, stunden.map(s => s.welle), yOf, '#38bdf8', 2);
}

function _wxRegenZeichnen(stunden) {
  const cv = $('wxRegenCanvas');
  if (!cv) return;
  const r = _wxRahmen(cv, 90, stunden);
  const { ctx, pad, cH, xOf, cW } = r;
  const max = Math.max(1, Math.ceil(Math.max(...stunden.map(s => s.regen || 0))));
  const yOf = v => pad.t + (1 - v / max) * cH;
  _wxSkala(r, max, 'mm', 2);

  const breite = Math.max(2, cW / stunden.length - 1);
  ctx.fillStyle = 'rgba(56,189,248,0.55)';
  stunden.forEach((s, i) => {
    if (!s.regen) return;
    const y = yOf(s.regen);
    ctx.fillRect(xOf(i) - breite / 2, y, breite, pad.t + cH - y);
  });
}

// ── Modellvergleich ─────────────────────────────────────────────────────────

function wxVergleichLaden() {
  const ort = _wxOrt();
  const knopf = $('wxVergleichKnopf');
  if (knopf) { knopf.disabled = true; knopf.textContent = 'Wird geholt…'; }
  const p = new URLSearchParams();
  if (ort.lat != null) { p.set('lat', ort.lat.toFixed(4)); p.set('lon', ort.lon.toFixed(4)); }
  fetch('/api/wetter/vergleich?' + p)
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      _wxVergleich = d;
      if (knopf) knopf.hidden = !!d;
      _wxVergleichZeichnen();
    })
    .catch(() => {})
    .finally(() => {
      if (knopf) { knopf.disabled = false; knopf.textContent = 'Modelle vergleichen'; }
    });
}

const _WX_VGL_FARBEN = ['#38bdf8', '#fb923c', '#4ade80', '#f472b6', '#a78bfa'];

function _wxVergleichZeichnen() {
  const box = $('wxVergleichBox');
  if (!box) return;
  box.hidden = !_wxVergleich;
  if (!_wxVergleich) return;

  const zeiten = _wxVergleich.zeiten || [];
  const modelle = _wxVergleich.modelle || [];
  if (!zeiten.length || !modelle.length) return;
  const stunden = zeiten.map(t => ({ t }));

  const cv = $('wxVergleichCanvas');
  const r = _wxRahmen(cv, 170, stunden);
  const { ctx, pad, cH, xOf } = r;
  const alle = modelle.flatMap(m => m.wind).filter(v => v != null);
  const max = Math.max(10, Math.ceil(Math.max(...alle) / 5) * 5);
  const yOf = v => pad.t + (1 - v / max) * cH;
  _wxSkala(r, max, 'kn');

  // Erst die Spanne als Fläche: wo sie breit wird, ist die Vorhersage weich.
  ctx.beginPath();
  const oben = [], unten = [];
  zeiten.forEach((_, i) => {
    const w = modelle.map(m => m.wind[i]).filter(v => v != null);
    oben.push(w.length ? Math.max(...w) : null);
    unten.push(w.length ? Math.min(...w) : null);
  });
  let start = true;
  oben.forEach((v, i) => {
    if (v == null) return;
    start ? ctx.moveTo(xOf(i), yOf(v)) : ctx.lineTo(xOf(i), yOf(v));
    start = false;
  });
  for (let i = unten.length - 1; i >= 0; i--) {
    if (unten[i] == null) continue;
    ctx.lineTo(xOf(i), yOf(unten[i]));
  }
  ctx.closePath();
  ctx.fillStyle = 'rgba(148,163,184,0.20)';
  ctx.fill();

  modelle.forEach((m, k) => _wxLinie(r, m.wind, yOf, _WX_VGL_FARBEN[k % _WX_VGL_FARBEN.length], 1.6));

  // Legende und ein Satz dazu, was die Spanne bedeutet.
  const legende = $('wxVergleichLegende');
  if (legende) {
    legende.innerHTML = modelle.map((m, k) =>
      `<span class="wx-legende"><i style="background:${_WX_VGL_FARBEN[k % _WX_VGL_FARBEN.length]}"></i>${_wxEsc(m.name)}</span>`).join('');
  }
  const urteil = $('wxVergleichUrteil');
  if (urteil) {
    const spannen = oben.map((o, i) => (o != null && unten[i] != null) ? o - unten[i] : null)
                        .filter(v => v != null);
    const mittel = spannen.reduce((a, b) => a + b, 0) / (spannen.length || 1);
    const groesste = Math.max(...spannen, 0);
    const wort = mittel < 3 ? 'Die Modelle sind sich weitgehend einig.'
      : mittel < 6 ? 'Die Modelle weichen spürbar voneinander ab — mit dem oberen Rand rechnen.'
      : 'Die Modelle sind sich uneins. Diese Vorhersage trägt keine Planung.';
    urteil.textContent = `${wort} Mittlere Spanne ${mittel.toFixed(1)} kn, größte ${groesste.toFixed(1)} kn.`;
  }
}

// ── Verdrahtung ─────────────────────────────────────────────────────────────

function _wxBinden() {
  wischenBinden($('wxCard'), wxOrtWeiter);
  const orte = $('wxOrtLeiste');
  if (orte) orte.addEventListener('click', e => {
    const k = e.target.closest('[data-index]');
    if (!k) return;
    _wxIndex = parseInt(k.dataset.index, 10) || 0;
    try { localStorage.setItem(_WX_ORT_SPEICHER, String(_wxIndex)); } catch (_) {}
    _wxVergleich = null;
    const knopf = $('wxVergleichKnopf');
    if (knopf) knopf.hidden = false;
    _renderWeather(); _renderWetterSeite(); fetchWeather();
  });
  const modelle = $('wxModellLeiste');
  if (modelle) modelle.addEventListener('click', e => {
    const k = e.target.closest('[data-modell]');
    if (k) wxModellSetzen(k.dataset.modell);
  });
  // Die Kachel neu zeichnen, wenn sich die Breite ändert — die Leinwände
  // rechnen in Pixeln und wissen sonst nichts davon.
  addEventListener('resize', () => { if (_wetterOffen()) _wxDiagramme(); });
}

// Alle 30 Minuten — pausiert automatisch, solange die Seite versteckt ist.
const _wxPoller = createPoller(fetchWeather, 30 * 60 * 1000);
_wxPoller.start();
