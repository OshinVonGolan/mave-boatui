// ── Logbuch ────────────────────────────────────────────────────────────────
// Das Diagnosewerkzeug auf dem Server. Eigenstaendig gebaut und nicht Teil des
// Bordbundles: es laeuft nur auf dem Server, wird selten geoeffnet, und die
// Bordansicht soll seinetwegen nicht groesser werden — auf dem Pi Zero zaehlt
// jedes Kilobyte im Startpaket.
//
// Es beantwortet eine andere Frage als die Bordansicht. Die zeigt, wie es dem
// Boot jetzt geht; hier will jemand wissen, wie es ihm ergangen ist.

const $ = id => document.getElementById(id);

let _konto = null;
let _tage = 7;
let _alleAusfaelle = false;
let _daten = { verbindung: null, diagnose: null, konten: null,
               zustand: null, anwesend: null };

// Die Takte dieser Oberflaeche. Mit Namen und nicht als nackte Zahl im
// setInterval, weil die Aufbauseite sie ANZEIGT — eine abgeschriebene Zahl
// waere in einem halben Jahr falsch, und eine falsche Zahl auf einer
// Nachschlageseite ist schlimmer als gar keine.
const LOGBUCH_TAKT_MS    = 30000;    // Dashboard: Zustand, Verbindung, Anwesenheit
const LOGBUCH_VERLAUF_MS = 300000;   // die 24-Stunden-Flaechen hinter den Ampeln
const LOGBUCH_MESS_MS    = 120000;   // die Messwerte-Seite
const WETTER_ALTER_S     = 900;      // ab wann ein Wetterwert neu geholt wird

// ── Formate ────────────────────────────────────────────────────────────────

function zeitpunkt(s) {
  if (!s) return '—';
  const d = new Date(s * 1000);
  return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit' });
}

function dauer(s) {
  if (s == null) return '—';
  if (s < 90) return `${Math.round(s)} s`;
  const m = s / 60;
  if (m < 90) return `${Math.round(m)} min`;
  const h = m / 60;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} Tage`;
}

function esc(t) {
  const d = document.createElement('div');
  d.textContent = t == null ? '' : String(t);
  return d.innerHTML;
}

// Die Woerter fuer die Ausfallarten. Sie stehen hier und nicht im Server:
// der liefert die Art, die Formulierung ist Sache der Anzeige.
const ART = {
  funkloch:  { wort: 'kein Netz',    satz: 'Das Boot war da, aber ohne Verbindung.' },
  stromlos:  { wort: 'stromlos',     satz: 'Der Rechner war aus — Strom weg oder abgeschaltet.' },
  neustart:  { wort: 'Neustart',     satz: 'Der Rechner ist neu gestartet.' },
  dienst:    { wort: 'nur Dienst',   satz: 'Nur die Anwendung wurde neu gestartet, der Rechner lief durch.' },
  erststart: { wort: 'erster Start', satz: 'Davor wurde noch nicht Buch geführt.' },
  unbekannt: { wort: 'unklar',       satz: 'Was in dieser Lücke geschah, lässt sich nicht mehr sagen.' },
};

// ── Laden ──────────────────────────────────────────────────────────────────

async function hole(pfad) {
  const r = await fetch(pfad, { cache: 'no-store' });
  if (r.status === 401) { anmeldungZeigen(); return null; }
  if (!r.ok) return null;
  return r.json();
}

/** Wo die Bordansicht liegt — von hier aus gesehen.
 *
 *  Unter dem Logbuch-Namen fuehrt "/" wieder hierher. Ein Verweis darauf
 *  drehte sich im Kreis, und von der Abweisung aus landete man wieder auf der
 *  Abweisung. Deshalb EINE Stelle, die das ausrechnet — vorher stand sie
 *  zweimal da, und die zweite war falsch.
 */
function _bordansicht() {
  const h = location.hostname;
  if (h.startsWith('logbuch.')) {
    return location.protocol + '//' + h.replace(/^logbuch\./, 'mave.') + '/';
  }
  return '/';
}

async function start() {
  // Vor allem anderen: der Nachtmodus soll stehen, bevor irgendetwas leuchtet.
  //
  // Geprueft, weil `wandbetrieb.js` zum BORDbuendel gehoert und diagnose.html
  // es nicht laedt. Ohne die Pruefung starb das ganze Logbuch in dieser Zeile —
  // eine weisse Seite, weil ein Nachtmodus fehlt. Soll er auch hier gelten,
  // braucht es das Skript UND die zugehoerigen Regeln: der Filter haengt an
  // `html` und steht in style.css, die das Logbuch ebenfalls nicht laedt.
  if (typeof wandStart === 'function') wandStart();
  const z = await (await fetch('/api/zugang', { cache: 'no-store' })).json().catch(() => null);
  if (!z) return;
  if (!z.angemeldet) { anmeldungZeigen(); return; }
  _konto = z.konto;

  if (!(_konto.oberflaechen || []).includes('diagnose')) {
    // Das Tor steht am Endpunkt; hier wird es nur erklaert. Wer die Adresse
    // kennt, aber das Recht nicht hat, bekommt von den Daten nichts zu sehen.
    $('sperre').hidden = false;
    // Dieselbe Rechnung wie beim Verweis in der Kopfzeile — sonst landet man
    // von der Abweisung aus wieder auf der Abweisung.
    $('sperreZurueck').href = _bordansicht();
    $('sperreText').textContent =
      `Angemeldet als ${_konto.rolle_name}. Das Logbuch ist dem Eigner und `
      + 'Technikern vorbehalten — die Bordansicht steht dir offen.';
    return;
  }

  $('zurBordansicht').href = _bordansicht();
  $('wer').textContent = (_konto.anzeigename || '') + ' · ' + _konto.rolle_name;
  $('inhalt').hidden = false;
  const darf = _konto.handlungen || [];
  $('slKonten').hidden = !darf.includes('verwalten');
  $('slWartung').hidden = !darf.includes('fernwarten');
  $('slAenderungen').hidden = !darf.includes('fernwarten');
  $('slMitschnitt').hidden = !darf.includes('fernwarten');
  // Eine Gruppenueberschrift ohne Punkte darunter ist eine Ueberschrift ueber
  // nichts.
  $('slGruppeSystem').hidden = !darf.includes('fernwarten');
  $('slGruppeVerwaltung').hidden = !darf.includes('verwalten');

  $('seitenleiste').addEventListener('click', e => {
    const k = e.target.closest('.sl-knopf');
    if (k) seiteZeigen(k.dataset.seite);
  });
  // Die Seite steht in der Adresse: ein Lesezeichen auf die Messwerte soll
  // dort landen, und der Zurück-Knopf soll tun, was er verspricht.
  window.addEventListener('hashchange', () => seiteZeigen(location.hash.slice(1) || 'ueberblick'));
  seiteZeigen(location.hash.slice(1) || 'ueberblick');

  $('zeitwahl').addEventListener('click', e => {
    const k = e.target.closest('.zw-knopf');
    if (!k) return;
    _tage = Number(k.dataset.tage);
    // Nur die Knöpfe DIESER Auswahl umschalten. Ein
    // document.querySelectorAll('.zw-knopf') träfe auch die Zeitwahl der
    // Messwerte weiter unten und würde sie stillschweigend mit zurücksetzen.
    $('zeitwahl').querySelectorAll('.zw-knopf').forEach(b => b.classList.toggle('an', b === k));
    zeichneStreifen();
    zeichneZahlen();
    zeichneAusfaelle();
  });

  $('spurZeit').addEventListener('click', e => {
    const k = e.target.closest('.zw-knopf');
    if (!k) return;
    _spurTage = Number(k.dataset.tage);
    $('spurZeit').querySelectorAll('.zw-knopf').forEach(b => b.classList.toggle('an', b === k));
    _spur = null;
    zeichnePosition();
    spurLaden().then(() => { if (_karte) karteZeichnen(); });
  });

  $('messZeit').addEventListener('click', e => {
    const k = e.target.closest('.zw-knopf');
    if (!k) return;
    if (k.dataset.alles) {
      // Der ganze vorhandene Verlauf. Die Grenzen kommen vom Server, nicht aus
      // einer geratenen Zahl — sonst boete der Knopf Zeitraeume an, in denen
      // nie etwas aufgeschrieben wurde.
      const z = (_daten.diagnose || {}).verlauf_zeitraum || {};
      if (z.von && z.bis) {
        _messDauer = Math.max(3600, z.bis - z.von);
        _messBis = z.bis;
        _messFolgt = false;
      }
    } else {
      _messDauer = Number(k.dataset.std) * 3600;
      _messFolgt = true;
    }
    $('messZeit').querySelectorAll('.zw-knopf').forEach(b => b.classList.toggle('an', b === k));
    messwerteLaden();
  });
  // Beim Drehen oder Größenändern neu zeichnen: Canvas skaliert nicht mit.
  //
  // Der resize-Horcher allein reicht seit dem Umbau nicht mehr. Die Breite des
  // Inhalts haengt jetzt nicht nur am Fenster: die Schiene faehrt im Schmalbild
  // herein und heraus, und ein Rollbalken kann auftauchen oder verschwinden.
  // Beides aendert die Breite, ohne dass das Fenster sich ruehrt — der Graph
  // bliebe dann in der alten Breite stehen und waere gestaucht oder abgeschnitten.
  // Deshalb zusaetzlich ein Beobachter am Behaelter selbst.
  let umbau;
  const neuZeichnen = () => {
    clearTimeout(umbau);
    umbau = setTimeout(() => _messDaten && zeichneMesswerte(), 250);
  };
  window.addEventListener('resize', neuZeichnen);
  if (window.ResizeObserver) {
    const behaelter = $('reihen');
    if (behaelter) {
      let ersteMeldung = true;
      new ResizeObserver(() => {
        // Die erste Meldung kommt sofort beim Beobachten und beschreibt den
        // Zustand, den wir gerade gezeichnet haben. Ein Neuzeichnen darauf
        // waere Arbeit fuer nichts.
        if (ersteMeldung) { ersteMeldung = false; return; }
        neuZeichnen();
      }).observe(behaelter);
    }
  }

  _schieneVerdrahten();

  // Die Hoehe des Ueberblicks an Kopfzeile und Innenabstand fuehren — er soll
  // genau einen Bildschirm fuellen, ohne dass man rollen muss.
  _ueberblickHoeheFuehren();

  await laden();
  await einstellungenLaden();
  // Der Vorhang kommt NACH dem ersten Laden: er soll ueber einer Seite liegen,
  // auf der schon etwas steht. Ueber einer leeren Seite saehe er aus wie ein
  // Fehler beim Start.
  await alarmeLaden();
  await dashboardVerlaufLaden();
  await messwerteLaden();
  setInterval(laden, LOGBUCH_TAKT_MS);
  // Der Verlauf fuers Dashboard aendert sich langsam — ein Punkt je neun
  // Minuten. Alle fuenf Minuten nachzuladen reicht und kostet den Pi wenig.
  setInterval(dashboardVerlaufLaden, LOGBUCH_VERLAUF_MS);
  // Messwerte seltener: sie ändern sich langsam und kosten mehr.
  setInterval(messwerteLaden, LOGBUCH_MESS_MS);
}

// ── Die Schiene im Schmalbild ──────────────────────────────────────────────
// Auf dem Schreibtisch steht sie immer da und dieser Schalter ist unsichtbar.
// Schmal faehrt sie ueber den Inhalt und geht wieder zu, sobald man eine Seite
// gewaehlt hat — sonst muesste man nach jeder Wahl noch einmal danebentippen.

function schieneUmschalten() {
  document.documentElement.classList.toggle('schiene-offen');
}

function schieneZu() {
  document.documentElement.classList.remove('schiene-offen');
}

let _seite = 'ueberblick';

function seiteZeigen(name) {
  const knopf = document.querySelector(`.sl-knopf[data-seite="${name}"]`);
  if (!knopf || knopf.hidden) name = 'ueberblick';
  _seite = name;
  document.querySelectorAll('.sl-knopf').forEach(k =>
    k.classList.toggle('an', k.dataset.seite === name));
  document.querySelectorAll('.seite').forEach(s => { s.hidden = s.dataset.seite !== name; });
  if (location.hash.slice(1) !== name) history.replaceState(null, '', '#' + name);
  // Canvas-Groessen stimmen nur, wenn das Element sichtbar IST — auf einer
  // verborgenen Seite ist die Breite null und der Graph bliebe leer.
  if (name === 'messwerte' && _messDaten) requestAnimationFrame(zeichneMesswerte);
  if (name === 'verfuegbarkeit') { zeichneLegende(); zeichneStreifen(); }
  // Die Karte braucht ein sichtbares Feld, um ihre Groesse zu bestimmen —
  // auf einer verborgenen Seite ist sie null Pixel breit und bliebe grau.
  if (name === 'position') positionOeffnen();
  // Dasselbe fuer die kleine Karte im Ueberblick: auf einer verborgenen Seite
  // ist ihr Feld null Pixel breit und sie bliebe grau.
  if (name === 'ueberblick') dbKarteZeigen();
  if (name === 'einstellungen') zeichneEinstellungen();
  if (name === 'aufbau') zeichneAufbau();
  // Das Abfragen kostet ein git fetch am Boot — also nur beim Öffnen der
  // Seite und nicht im Dauertakt.
  if (name === 'aenderungen') staendeLaden();
  if (name === 'mitschnitt') mitschnittLaden();
  schieneZu();
  window.scrollTo(0, 0);
}

// ── Überblick: das Dashboard ───────────────────────────────────────────────
// Die Startseite beantwortet der Reihe nach die Fragen, mit denen jemand sie
// öffnet, der auf dem Boot LEBT:
//
//   Strom   — reicht er, und wie lange noch?
//   Bord    — ist es warm, reichen die Vorräte, brennt noch Licht?
//   Anlage  — läuft sie überhaupt, und sind die Zahlen oben von jetzt?
//
// Jede Karte beantwortet genau EINE davon, und zwar zuerst als Satz: aus zwei
// Metern Entfernung liest man "Reicht rund 5 Tage", nicht "90,9 %". Die Zahlen
// stehen darunter für den, der genauer hinsieht.
//
// Die Anordnung liegt fest. Eine Karte, die heute Sorgen macht, bekommt eine
// farbige Kante — sie wandert aber nicht nach oben. Eine Oberfläche, die ihre
// Reihenfolge ändert, muss man jedes Mal neu lesen.

// Ein eigener, kurzer Verlauf fürs Dashboard. Getrennt von der Messwerte-Seite:
// die hat ihren eigenen Zeitraum, den der Betrachter einstellt, und der soll
// sich nicht dadurch verstellen, dass jemand kurz auf den Überblick schaut.
let _dbVerlauf = null;

async function dashboardVerlaufLaden() {
  const bis = Date.now() / 1000;
  const von = bis - 24 * 3600;
  // 160 Punkte auf 24 Stunden: ein Punkt je neun Minuten. Für eine Linie ohne
  // Achsen ist mehr nicht sichtbar und kostet den Pi nur Arbeit.
  const d = await hole(`/api/verlauf/reihen?von=${Math.floor(von)}&bis=${Math.ceil(bis)}&punkte=160`);
  if (d) { _dbVerlauf = d; zeichneUeberblick(); }
}

/** Die Mittelwerte einer Reihe aus dem Dashboard-Verlauf, oder null. */
function _reihe(feld) {
  const p = _reihePunkte(feld);
  return p ? p.map(x => x.v) : null;
}

/**
 * Dieselbe Reihe, aber MIT Zeitstempel.
 *
 * Der Unterschied ist nicht kosmetisch. Ohne die Zeit weiss die Fläche nur, wie
 * viele Punkte es gibt, und verteilt sie gleichmässig über ihre Breite. Liegen
 * nur zwei Stunden Verlauf vor — weil das Feld neu ist oder der Rechner stand —,
 * füllen diese zwei Stunden trotzdem das ganze Feld und behaupten damit einen
 * Tag. Genauso verschwindet eine Nacht ohne Aufzeichnung spurlos, statt als
 * Lücke dazustehen.
 */
function _reihePunkte(feld) {
  const p = _dbVerlauf && _dbVerlauf.punkte;
  if (!p || !p.length) return null;
  const raus = [];
  for (const x of p) {
    const v = Array.isArray(x[feld]) ? x[feld][0] : x[feld];
    if (typeof v === 'number' && typeof x.t === 'number') raus.push({ t: x.t, v });
  }
  return raus.length >= 3 ? raus : null;
}

/** Der zuletzt gemessene Wert einer Reihe. */
function _reiheLetzt(feld) {
  const w = _reihe(feld);
  return w ? w[w.length - 1] : null;
}

// Ab wann ein Wert nicht mehr "von jetzt" ist. Der Bus meldet im Sekundentakt;
// zwei Minuten Stille sind kein Rauschen mehr, sondern eine Aussage.
const FRISCH_S = 120;

/**
 * Wie alt ein Messwert WIRKLICH ist.
 *
 * Die Falle: jede Gruppe trägt ihr eigenes `_age_s`, aber das ist die Zeit seit
 * dem letzten Bus-Rahmen AUF DEM BOOT, gemessen zum Zeitpunkt der Aufnahme.
 * Schweigt das Boot seit sechs Stunden, steht in dieser Zahl trotzdem "0,3" —
 * sie ist mit dem Schnappschuss eingefroren. Wer sie ungeprüft anzeigt,
 * behauptet Aktualität, die es nicht gibt. Deshalb kommt das Alter der
 * Übertragung obendrauf.
 *
 * `null` heißt etwas ganz anderes als "alt": das Gerät war seit dem Start nie
 * am Bus. Ladegerät und Lichtmaschine sind nicht verbaut — das ist Absicht und
 * kein Fehler, und es muss auch so dastehen.
 */
function _frische(gruppe) {
  const z = _daten.zustand;
  const g = (z && z[gruppe]) || {};
  const eigen = g._age_s !== undefined ? g._age_s
              : (g.age_s !== undefined ? g.age_s : null);
  if (eigen === null || eigen === undefined) return { art: 'aus', alt: null, text: 'nicht am Bus' };
  const versatz = (z && z.quelle === 'server' && !z.boot_verbunden) ? (z.alter_s || 0) : 0;
  const alt = eigen + versatz;
  return alt < FRISCH_S
    ? { art: 'frisch', alt, text: 'jetzt' }
    : { art: 'alt', alt, text: 'vor ' + dauer(alt) };
}

function _kopf(titel, gruppe) {
  const f = _frische(gruppe);
  const kl = f.art === 'frisch' ? '' : (f.art === 'aus' ? 'aus' : 'alt');
  return `<div class="dk-kopf"><h2>${esc(titel)}</h2>
    <span class="dk-frische"><i class="dk-punkt ${kl}"></i>${esc(f.text)}</span></div>`;
}

function _zeile(name, wert, art, grund) {
  return `<div class="dk-zeile"><span class="dk-name">${esc(name)}</span>
      <span class="dk-wert ${art || ''}">${esc(wert)}</span></div>`
    + (grund ? `<div class="dk-grund">${esc(grund)}</div>` : '');
}

/** Eine Dauer, wie man sie sagt: "rund 5 Tage", nicht "119,4 Stunden". */
function _grob(stunden) {
  if (stunden == null || !isFinite(stunden) || stunden <= 0) return null;
  if (stunden < 1.5) return `${Math.round(stunden * 60)} Minuten`;
  if (stunden < 36) return `rund ${Math.round(stunden)} Stunden`;
  const t = stunden / 24;
  return t < 10 ? `rund ${t.toFixed(t < 3 ? 1 : 0)} Tage` : `über eine Woche`;
}

/**
 * Der Verlauf der letzten 24 Stunden als FLÄCHE hinter dem Text.
 *
 * Dieselbe Sprache wie in der Statusleiste der Bordansicht: nach unten
 * durchsichtig auslaufend, in der Zustandsfarbe des Feldes. Kein Diagramm —
 * es gibt keine Achsen, keine Beschriftung und nichts abzulesen. Die Frage,
 * die es beantwortet, ist nicht "wie viel", sondern "war das schon länger so".
 *
 * Gezeichnet wird über der ZEIT, nicht über der Zahl der Punkte. Das ist der
 * Unterschied zwischen einer Aussage und einer Behauptung: liegen erst zwei
 * Stunden Verlauf vor, sitzt die Fläche rechts am Rand und nimmt ein Zwölftel
 * der Breite ein — bei gleichmässiger Verteilung füllte sie das ganze Feld und
 * behauptete einen Tag, den es nicht gibt. Aus demselben Grund bleibt eine
 * Lücke eine Lücke, statt von einer geraden Linie überbrückt zu werden.
 *
 * Als SVG und nicht als Canvas: es skaliert von selbst mit dem Feld mit. Ein
 * Canvas müsste bei jeder Breitenänderung neu gerechnet werden — genau die
 * Falle, die auf der Messwerte-Seite den Größenbeobachter nötig macht.
 *
 * `unten`/`oben` fest zu setzen ist bei Prozenten Pflicht: eine Skalierung auf
 * die Spannweite malt aus 79 %…81 % einen Berg und aus 5 %…95 % denselben
 * Berg. Wo es keine natürliche Skala gibt (Antwortzeit), wird automatisch
 * skaliert — dann sagt die Fläche wenigstens die Form der Nacht.
 */
function _wasch(punkte, farbe, unten, oben) {
  if (!punkte || punkte.length < 3 || !_dbVerlauf) return '';
  const von = _dbVerlauf.von, bis = _dbVerlauf.bis;
  if (!(bis > von)) return '';

  const werte = punkte.map(p => p.v);
  const min = unten != null ? unten : Math.min(...werte);
  let max = oben != null ? oben : Math.max(...werte);
  if (!(max > min)) max = min + 1;
  const x = t => ((t - von) / (bis - von) * 100).toFixed(2);
  const y = v => (100 - (Math.max(min, Math.min(max, v)) - min) / (max - min) * 100).toFixed(2);

  // Eine Lücke ist mehr als zweieinhalb Eimerbreiten ohne Punkt. Enger
  // angesetzt zerfiele die Linie an jedem einzelnen fehlenden Eimer in
  // Bruchstücke; weiter angesetzt würde eine halbe Nacht überbrückt.
  const eimer = _dbVerlauf.eimer_s || (bis - von) / Math.max(1, punkte.length);
  const luecke = eimer * 2.5;
  const stuecke = [[]];
  let vorher = null;
  for (const p of punkte) {
    if (vorher !== null && p.t - vorher > luecke) stuecke.push([]);
    stuecke[stuecke.length - 1].push(p);
    vorher = p.t;
  }

  const kennung = 'wasch' + (_waschZaehler++);
  let pfade = '';
  for (const stueck of stuecke) {
    if (stueck.length < 2) continue;      // ein einzelner Punkt ist keine Linie
    const pkt = stueck.map(p => `${x(p.t)},${y(p.v)}`);
    const l = x(stueck[0].t), r = x(stueck[stueck.length - 1].t);
    pfade += `<path d="M${l},100 L${pkt.join(' L')} L${r},100 Z" fill="url(#${kennung})"/>`
      + `<polyline points="${pkt.join(' ')}" fill="none" stroke="${farbe}"
          stroke-opacity=".3" stroke-width="1.2" vector-effect="non-scaling-stroke"
          stroke-linejoin="round"/>`;
  }
  if (!pfade) return '';
  return `<div class="ampel-spur"><svg viewBox="0 0 100 100" preserveAspectRatio="none"
      aria-hidden="true">
    <defs><linearGradient id="${kennung}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${farbe}" stop-opacity=".26"/>
      <stop offset="1" stop-color="${farbe}" stop-opacity="0"/>
    </linearGradient></defs>${pfade}
  </svg></div>`;
}
let _waschZaehler = 0;

/** Kopf einer Dashboard-Karte: Name links, ein Wort zum Zustand rechts. */
function _karteKopf(titel, hinweis) {
  return `<div class="tafel-kopf"><h2>${esc(titel)}</h2>`
    + `<span class="hinweis">${esc(hinweis || '')}</span></div>`;
}

// ── Zeile 1: die Ampeln ────────────────────────────────────────────────────
// Farbe vor Zahl. Aus zwei Metern soll man sehen, ob etwas nicht stimmt; die
// Zahl steht daneben, für den zweiten Blick. Fünf Fragen in der Reihenfolge,
// in der sie jemand stellt, der aus der Ferne nachsieht: Hält der Strom? Weiss
// das Boot, wo es ist? Komme ich dran? Meldet sich der Rechner? Ist etwas
// fällig?

/** Eine Ampel. `stufe` ist gut/warn/rot oder leer — Farbe und Punkt in einem. */
function _ampel(name, stufe, wert, einheit, neben, wasch) {
  return `<div class="ampel">${wasch || ''}
    <div class="ampel-kopf"><span class="punkt ${stufe}"></span>
      <span class="ampel-name">${esc(name)}</span></div>
    <div class="ampel-wert ${stufe ? 'w-' + stufe : 'w-ruhe'}">${esc(wert)}${
      einheit ? `<small>${esc(einheit)}</small>` : ''}</div>
    <div class="ampel-neben">${esc(neben || '')}</div>
  </div>`;
}

function _ampelnBauen(z, v) {
  const b = z.battery || {}, pos = z.position || null;
  const soc = b.soc;

  // Batterie. Dieselben Schwellen wie überall sonst in dieser Oberfläche — 50
  // und 25 —, damit Gelb hier nicht etwas anderes heisst als in der
  // Bordansicht.
  const battStufe = soc == null ? '' : soc >= 50 ? 'gut' : soc >= 25 ? 'warn' : 'rot';
  const battNeben = [
    b.voltage != null ? b.voltage.toFixed(2) + ' V' : null,
    b.current != null ? (b.current > 0 ? '+' : '') + b.current.toFixed(1) + ' A' : null,
  ].filter(Boolean).join(' · ') || 'keine Messung';

  // GPS. Gezählt werden Satelliten, nicht Meter: der Router meldet eine Güte
  // (HDOP), und die ist keine Entfernung. Wer "3 m" liest, glaubt an eine
  // Genauigkeit, die so nicht dasteht.
  const sats = pos && pos.satelliten != null ? pos.satelliten : null;
  const fixAlt = pos && pos._age_s != null ? pos._age_s : null;
  const gpsStufe = !pos ? '' : sats == null ? 'warn'
    : sats >= 6 ? 'gut' : sats >= 4 ? 'warn' : 'rot';
  const gpsNeben = !pos ? 'kein Fix vom Router'
    : fixAlt != null ? 'Fix vor ' + dauer(fixAlt) : 'Fix ohne Zeitangabe';

  // Internet. Die Antwortzeit sagt mehr als ein Häkchen: eine Leitung kann
  // durchgehend "verbunden" sein und trotzdem die halbe Nacht auf jede Antwort
  // eine Sekunde brauchen.
  const ping = _reiheLetzt('ping_ms');
  const netzStufe = ping == null ? '' : ping < 120 ? 'gut' : ping < 400 ? 'warn' : 'rot';
  const traeger = ((z.connectivity || {}).router || {}).active_type || '';
  const traegerWort = { wired: 'Kabel', mobile: 'Mobilfunk', wifi: 'WLAN' }[traeger] || '';

  // Verbindung zum Pi. Nicht "verbunden ja/nein", sondern wie zuverlässig:
  // eine Leitung, die zehnmal am Tag abreisst, ist etwas anderes als eine, die
  // steht — und beide stünden als "verbunden" da.
  const verbunden = !!(v && v.verbunden);
  const luecken24 = ((v || {}).luecken || []).filter(
    l => (l.ab || 0) > Date.now() / 1000 - 86400).length;
  const piStufe = !verbunden ? 'rot' : luecken24 > 3 ? 'warn' : 'gut';
  const piWert = !verbunden ? 'weg'
    : (z.quelle === 'server' && z.alter_s != null && z.alter_s > 90 ? dauer(z.alter_s) : 'jetzt');
  const piNeben = verbunden
    ? (luecken24 ? `${luecken24} ${luecken24 === 1 ? 'Lücke' : 'Lücken'} in 24 Std`
                 : 'ohne Unterbrechung')
    : (v && v.seit ? 'zuletzt ' + zeitpunkt(v.seit) : 'nicht verbunden');

  // Wartung. Ohne Verlaufsfläche, und das mit Absicht: ein Wartungsplan hat
  // keinen Tagesverlauf. Eine Fläche wäre hier reine Dekoration.
  const wa = z.wartung || null;
  const wStufe = !wa ? '' : wa.ueberfaellig ? 'rot' : wa.bald ? 'warn' : 'gut';
  const wWert = !wa ? '—' : wa.ueberfaellig ? String(wa.ueberfaellig)
    : wa.bald ? String(wa.bald) : 'alles';
  const wNeben = !wa ? 'kein Wartungsplan übertragen'
    : wa.ueberfaellig ? (wa.bald ? `überfällig · ${wa.bald} bald fällig` : 'überfällig')
    : wa.bald ? `bald fällig, ${wa.frist_tage} Tage Vorlauf`
    : `${wa.gesamt} Aufgaben im Plan`;

  const spurPi = _piSpur(v);
  return '<div class="ampeln">'
    + _ampel('Batterie', battStufe, soc != null ? soc.toFixed(0) : '—',
             soc != null ? '%' : '', battNeben,
             _wasch(_reihePunkte('soc'), 'var(--green)', 0, 100))
    // Feste Skala 0…12 statt automatisch: bei einer Skalierung auf die
    // Spannweite saehe eine Nacht zwischen neun und elf Satelliten aus wie ein
    // Absturz. Zwoelf ist die Oberkante — mehr sieht der Empfaenger selten, und
    // was darueber liegt, liegt eben an der Kante.
    + _ampel('GPS', gpsStufe, sats != null ? String(sats) : (pos ? 'Fix' : '—'),
             sats != null ? 'Sat' : '', gpsNeben,
             _wasch(_reihePunkte('sats'), 'var(--blau)', 0, 12))
    + _ampel('Internet', netzStufe, ping != null ? Math.round(ping) : '—',
             ping != null ? 'ms' : '',
             [traegerWort, 'Antwortzeit'].filter(Boolean).join(' · '),
             _wasch(_reihePunkte('ping_ms'), 'var(--yellow)'))
    // Volle Höhe: seit die Reihe Anteile statt Ja/Nein enthält, springt sie
    // nicht mehr zwischen leer und randvoll, sondern kerbt. Ein Deckel würde
    // die kleinen Aussetzer jetzt nur unsichtbar machen.
    + _ampel('Pi', piStufe, piWert, '', piNeben,
             spurPi ? _wasch(spurPi, 'var(--accent)', 0, 1) : '')
    + _ampel('Wartung', wStufe, wWert, '', wNeben)
    + '</div>';
}

/**
 * Die Erreichbarkeit der letzten 24 Stunden, als Punkte mit Zeitstempel.
 *
 * Anders als die übrigen Flächen kommt diese nicht aus dem Messverlauf,
 * sondern aus den Lücken: eine Reihe "war verbunden" gibt es nicht. Das ist
 * auch die ehrlichere Quelle — während einer Lücke schreibt der Pi nichts auf,
 * eine Verlaufsreihe hätte dort gar keinen Wert und nicht etwa eine Null.
 *
 * Je Abschnitt steht der ANTEIL der Zeit, in der die Verbindung stand — nicht
 * ein Ja oder Nein. Das ist der Unterschied zwischen einer Aussage und einem
 * Zerrbild: am laufenden System nachgemessen gab es an einem Tag 41 Lücken,
 * davon 36 kürzer als zwei Minuten und die meisten zwischen neun und dreißig
 * Sekunden — zusammen 7 % des Tages. Mit einem Ja/Nein je Viertelstunde färbte
 * eine Lücke von sechzehn Sekunden fünfzehn Minuten schwarz, oft sogar dreißig,
 * weil sie über eine Abschnittsgrenze fiel. Aus sieben Prozent wurde so
 * optisch mehr als die Hälfte.
 *
 * Wie OFT es aussetzte, steht daneben in der Zeile unter der Ampel. Die Fläche
 * beantwortet die andere Frage: wie viel Zeit dabei verloren ging.
 *
 * Der Zeitraum ist derselbe wie der der übrigen Flächen. Nähme diese ihren
 * eigenen, lägen zwei Felder nebeneinander, die auf verschiedene Achsen
 * zeichnen — und eine Kerbe säße nicht dort, wo die Delle daneben sitzt.
 */
function _piSpur(v) {
  if (!v || !_dbVerlauf) return null;
  const von = _dbVerlauf.von, bis = _dbVerlauf.bis;
  if (!(bis > von)) return null;
  // Ein Punkt je Minute. Bei 24 Stunden sind das 1440 — mehr, als das Feld
  // Pixel hat, und genau darum geht es: bei einer Viertelstunde je Punkt
  // verschwand eine Lücke von sechzehn Sekunden in einem Abschnitt, der zu
  // 98 % gestanden hatte. Je Minute wird daraus eine schmale, aber TIEFE
  // Kerbe — und sichtbar ist genau das, was gesucht wird. Gerechnet wird das
  // im Browser aus einer Liste von Lücken; es kostet nichts, was der Rede wert
  // wäre.
  const N = Math.max(60, Math.min(2000, Math.round((bis - von) / 60)));
  const schritt = (bis - von) / N;
  const weg = new Array(N).fill(0);             // Sekunden ohne Verbindung
  for (const l of (v.luecken || [])) {
    const a = Math.max(von, l.ab || 0);
    const e = Math.min(bis, (l.ab || 0) + (l.dauer_s || 0));
    if (!(e > a)) continue;
    // Auf die Abschnitte verteilen, anteilig. Eine Lücke, die über eine Grenze
    // fällt, gehört beiden Seiten — jeder so viel, wie in ihr lag.
    const i0 = Math.max(0, Math.floor((a - von) / schritt));
    const i1 = Math.min(N - 1, Math.floor((e - von) / schritt));
    for (let i = i0; i <= i1; i++) {
      const links = von + i * schritt, rechts = links + schritt;
      weg[i] += Math.min(e, rechts) - Math.max(a, links);
    }
  }
  const reihe = weg.map((s_, i) => ({
    t: von + i * schritt,
    v: Math.max(0, 1 - s_ / schritt),
  }));
  return reihe.some(x => x.v < 0.999) ? reihe : null;  // eine gerade Linie sagt nichts
}

// ── Zeile 2 links: Ort und Wetter ──────────────────────────────────────────

function _ortBauen(z) {
  const pos = z.position;
  const hat = pos && typeof pos.lat === 'number';
  const koord = hat
    ? `<div class="koord">${esc(_gradMinuten(pos.lat, 'lat'))}<br>${esc(_gradMinuten(pos.lon, 'lon'))}</div>`
    : '<div class="koord"><span>Der Router meldet keinen gültigen Fix.</span></div>';
  return _karteKopf('Ort und Wetter',
      hat ? (pos._age_s != null ? 'Fix vor ' + dauer(pos._age_s) : 'Position gemeldet')
          : 'keine Position')
    + `<div class="ort">
      <div class="kartenfeld" id="dbKarteFeld">
        <div class="karten-warte">${hat ? 'Karte wird geladen…' : 'Ohne Position keine Karte.'}</div>
      </div>
      <div class="ort-daten">${koord}${_wetterBauen()}</div>
    </div>`;
}

function _wetterBauen() {
  if (_wetter === 'laedt') {
    return '<div class="wetter"><div class="wetter-quelle">Wetter wird geholt…</div></div>';
  }
  if (!_wetter) {
    return '<div class="wetter"><div class="wetter-quelle">Kein Wetter verfügbar.</div></div>';
  }
  const w = _wetter;
  const zeile = (name, wert) => wert
    ? `<div class="wetter-zeile"><span>${esc(name)}</span><b>${esc(wert)}</b></div>` : '';
  const modell = (_wetterModelle.find(m => m.kennung === w.modell) || {}).name || w.modell || 'Open-Meteo';
  return `<div class="wetter">
    <div class="wetter-haupt">
      <span class="wetter-grad">${w.temperatur != null ? Math.round(w.temperatur) + '°' : '—'}</span>
      <span class="wetter-wort">${esc(w.text || '')}</span></div>
    ${zeile('Gefühlt', w.gefuehlt != null ? Math.round(w.gefuehlt) + ' °C' : '')}
    ${zeile('Wind', w.wind_kn != null
        ? (Math.round(w.wind_kn) + ' kn ' + _himmelsrichtung(w.wind_grad)).trim() : '')}
    ${zeile('Böen', w.boe_kn != null ? Math.round(w.boe_kn) + ' kn' : '')}
    ${zeile('Luftdruck', w.druck != null ? Math.round(w.druck) + ' hPa' : '')}
    <div class="wetter-quelle">${esc(modell)}${
      w.gemessen ? ' · ' + esc(String(w.gemessen).slice(11, 16)) : ''}${
      w.veraltet ? ' · alter Wert' : ''}</div>
  </div>`;
}

/** Grad in die Richtung, aus der es weht — so wird an Bord darüber geredet. */
function _himmelsrichtung(grad) {
  if (typeof grad !== 'number') return '';
  const namen = ['N', 'NNO', 'NO', 'ONO', 'O', 'OSO', 'SO', 'SSO',
                 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return namen[Math.round((((grad % 360) + 360) % 360) / 22.5) % 16];
}

// ── Zeile 2 rechts: Strom ──────────────────────────────────────────────────
// Was hereinkommt und was hinausgeht — vier Zeilen, immer dieselben, immer in
// derselben Reihenfolge. Ein Gerät, das nicht am Bus ist, sagt das, statt eine
// Null zu zeigen: eine Null sähe aus wie eine Messung.

function _stromBauen(z) {
  const s = z.solar || {}, o = z.orion || {}, c = z.charger || {},
        i = z.inverter || {}, b = z.battery || {};
  const zeile = (name, strich, wert, einheit, aus) => `<div class="quelle">
    <span class="q-strich ${strich || ''}"></span>
    <span class="q-name">${esc(name)}</span>
    <span class="q-wert ${aus ? 'q-aus' : ''}">${esc(wert)}${
      einheit ? `<small>${esc(einheit)}</small>` : ''}</span></div>`;

  const landDa = c._age_s != null;
  const landAn = landDa && (c.power || 0) > 5;
  const solarAn = (s.power || 0) > 5;
  const orionAn = (o.output_power || 0) > 5;
  const wechselAn = (i.ac_power || 0) > 5 || (!!i.state && i.state !== 'Aus');
  const rein = (s.power || 0) + (o.output_power || 0) + (landDa ? (c.power || 0) : 0);

  return _karteKopf('Strom', landAn ? 'am Landstrom'
      : rein > 5 ? 'es kommt nach' : 'nichts kommt nach')
    + '<div class="quellen">'
    + zeile('Landstrom', landAn ? 'an' : '',
            !landDa ? 'nicht am Bus' : landAn ? Math.round(c.power) : 'aus',
            landAn ? 'W' : '', !landAn)
    + zeile('Solar', solarAn ? 'an' : '', solarAn ? Math.round(s.power) : 'aus',
            solarAn ? 'W' : '', !solarAn)
    + zeile('Lichtmaschine', orionAn ? 'an' : '',
            orionAn ? Math.round(o.output_power) : 'aus', orionAn ? 'W' : '', !orionAn)
    + zeile('Wechselrichter', wechselAn ? 'raus' : '',
            wechselAn ? (i.ac_power != null ? Math.round(i.ac_power) : (i.state || 'an'))
                      : (i.state || 'aus'),
            wechselAn && i.ac_power != null ? 'W' : '', !wechselAn)
    + '</div>'
    + `<div class="bilanz"><span>${b.current == null ? 'Bilanz'
        : b.current >= 0 ? 'In die Batterie' : 'Aus der Batterie'}</span>`
    + `<b class="${b.current == null ? '' : b.current >= 0 ? 'w-gut' : ''}">${
        b.current != null ? (b.current > 0 ? '+' : '') + b.current.toFixed(1) + ' A' : '—'
      }</b></div>`;
}

// ── Zeile 3 links: Heizung ─────────────────────────────────────────────────
// Dieselben Wörter wie in der Bordansicht. Sie stehen hier ein zweites Mal,
// weil das Logbuch ein eigenes Bündel ist und nichts aus dem Bordbündel sieht
// — aber sie müssen dieselben BLEIBEN: "Läuft" darf nicht hier so und dort
// anders heissen, sonst redet dieselbe Anlage in zwei Sprachen.
const HZ_ZUSTAND = {
  off: 'Aus', starting: 'Startet', running: 'Läuft',
  stopping: 'Stoppt', cooldown: 'Nachlauf', fault: 'Störung',
};
const HZ_MODUS = { off: 'Aus', auto: 'Automatik', manual: 'Manuell' };

function _heizungBauen(z) {
  const h = z.heizung || {};
  const st = h.state || {};
  const raeume = st.rooms || h.rooms || [];
  const heizer = st.heater || {};
  // `not_wired` heisst: der Hub läuft, aber am Autoterm-Gerät hängt nichts.
  // Das ist etwas anderes als "aus" und muss auch anders dastehen.
  const verbaut = heizer.availability !== 'not_wired' && heizer.available !== false;

  const kennzahl = (name, wert, einheit, klasse) => `<div class="kennzahl">
    <span class="k-name">${esc(name)}</span>
    <span class="k-wert ${klasse || ''}">${esc(wert)}${
      einheit ? `<small>${esc(einheit)}</small>` : ''}</span></div>`;

  const zustand = !h.configured ? '—' : !verbaut ? 'nicht verbaut'
    : (HZ_ZUSTAND[heizer.state] || heizer.state || '—');
  const stufe = heizer.state === 'running' ? 'w-gut'
    : heizer.state === 'fault' ? 'w-rot'
    : (heizer.state === 'starting' || heizer.state === 'stopping') ? 'w-warn' : 'w-ruhe';

  let zeilen = '';
  for (const r of raeume) {
    const ist = typeof r.roomTemp === 'number';
    zeilen += `<div class="raum">
      <span class="raum-name">${esc(r.name || 'Raum')}</span>
      <span class="raum-ist ${ist ? (r.roomTemp < 6 ? 'w-rot' : '') : 'w-ruhe'}">${
        ist ? r.roomTemp.toFixed(1) + ' °C'
            : (r.conn === 'offline' ? 'meldet sich nicht' : 'kein Wert')}</span>
      <span class="raum-soll">${r.target != null ? 'Soll ' + esc(r.target) : ''}</span>
    </div>`;
  }

  // Nur EIN Hinweis, und zwar der wichtigste. Drei Zeilen Kleingedrucktes
  // untereinander liest niemand — und die unterste wäre die, auf die es
  // ankommt.
  let hinweis = '';
  if (!h.configured) {
    hinweis = '<div class="heiz-hinweis still"><span class="punkt"></span>'
      + 'Keine Heizungssteuerung eingerichtet.</div>';
  } else if (!h.reachable) {
    hinweis = '<div class="heiz-hinweis"><span class="punkt warn"></span>'
      + 'Die Steuerung meldet sich nicht.</div>';
  } else if (!verbaut) {
    hinweis = '<div class="heiz-hinweis"><span class="punkt warn"></span>'
      + esc(heizer.availabilityText || 'Kein Heizgerät angeschlossen') + '</div>';
  } else if (heizer.errorCode) {
    hinweis = '<div class="heiz-hinweis"><span class="punkt rot"></span>'
      + 'Fehlercode ' + esc(heizer.errorCode) + '</div>';
  }

  return _karteKopf('Heizung', st.preset && st.preset.name
      ? 'Vorwahl ' + st.preset.name : (h.configured ? 'ohne Vorwahl' : ''))
    + '<div class="heiz">'
    + kennzahl('Zustand', zustand, '', stufe)
    + kennzahl('Betriebsart', HZ_MODUS[heizer.mode] || heizer.mode || '—')
    + kennzahl('Vorlauf', heizer.flowTemp != null ? heizer.flowTemp.toFixed(1) : '—',
               heizer.flowTemp != null ? '°C' : '')
    + kennzahl('Starts heute', heizer.startsToday != null ? String(heizer.startsToday) : '—')
    + '</div>'
    + (zeilen ? `<div class="raeume">${zeilen}</div>` : '')
    + hinweis;
}

// ── Zeile 3 rechts: Geräte an Bord ─────────────────────────────────────────
// Wer gerade angemeldet ist — und WOMIT. Bisher stand hier, was der Browser
// über sich verrät ("Chrome auf Android"). Das beschreibt eine Software und
// kein Gerät: an Bord melden zwei Geräte dasselbe. Der Pi legt die Adresse der
// Sitzung gegen die Geräteliste des Routers und liefert den Namen mit, den das
// Gerät selbst angibt.

function _geraeteBauen() {
  const a = _daten.anwesend;
  if (!a) return _karteKopf('Geräte an Bord', '') + '<div class="leerlauf">wird geladen…</div>';
  const jetzt = Date.now() / 1000;

  // Mehrere Sitzungen desselben Kontos auf demselben Gerät sind KEINE
  // mehreren Anwesenden — sie entstehen bei jedem neuen Browserfenster und bei
  // jedem Werkzeugaufruf. Zusammengefasst wird nach Konto, Gerät und Adresse;
  // gezeigt wird die jüngste, die Anzahl steht daneben, damit vergessene
  // Anmeldungen trotzdem auffallen.
  const buendeln = (liste, fern) => {
    const m = new Map();
    for (const s of liste || []) {
      const schluessel = [s.konto, s.geraet_name || s.geraet, s.herkunft].join('|');
      const da = m.get(schluessel);
      if (!da) m.set(schluessel, { ...s, anzahl: 1, fern });
      else {
        da.anzahl++;
        if ((s.zuletzt || 0) > (da.zuletzt || 0)) da.zuletzt = s.zuletzt;
      }
    }
    return [...m.values()];
  };

  // "Da" heisst: in der letzten halben Stunde aktiv. Eine Sitzung, die seit
  // Stunden nichts mehr getan hat, ist keine Anwesenheit mehr — das Gerät
  // liegt in der Schublade. Verschwiegen wird sie trotzdem nicht: eine
  // vergessene Anmeldung auf einem fremden Gerät soll auffallen.
  const STILL_S = 30 * 60;
  const alle = [...buendeln(a.an_bord, false), ...buendeln(a.ueber_server, true)]
    .sort((x, y) => (y.zuletzt || 0) - (x.zuletzt || 0));
  const da = alle.filter(s => jetzt - (s.zuletzt || 0) < STILL_S).length;

  const kopf = !a.boot_erreichbar ? 'Boot nicht verbunden'
    : da ? da + ' verbunden' : 'niemand angemeldet';

  if (!alle.length) {
    return _karteKopf('Geräte an Bord', kopf)
      + `<div class="leerlauf">${a.boot_erreichbar
          ? 'Niemand am Bordrechner angemeldet.'
          : 'Das Boot ist nicht verbunden — von dort ist nichts zu erfahren.'}</div>`;
  }

  const zeilen = alle.slice(0, 8).map(s => {
    const seit = jetzt - (s.zuletzt || 0);
    const still = seit >= STILL_S;
    // Der Gerätename oben, alles Übrige klein darunter. Fehlt der Name (Gerät
    // im fremden Netz, oder der Router schweigt), tritt die Gattung nach oben:
    // eine leere erste Zeile wäre schlimmer als eine ungenaue.
    const name = s.geraet_name || s.geraet || 'unbekanntes Gerät';
    // Eine Loopback-Adresse sagt einem Leser nichts — sie bedeutet nur, dass
    // ein Weiterreicher davorsteht und die echte Adresse nicht durchgereicht
    // hat. Dann steht dort lieber gar nichts als eine Zahl, die nach Auskunft
    // aussieht und keine ist.
    const adresse = /^(127\.|::1$|::ffff:127\.)/.test(s.herkunft || '') ? null : s.herkunft;
    const neben = [s.anzeigename || s.konto, adresse,
                   s.geraet_name ? s.geraet : null,
                   s.fern ? 'über den Server' : null,
                   s.kiosk ? 'Kiosk' : null,
                   s.anzahl > 1 ? s.anzahl + ' Anmeldungen' : null]
      .filter(Boolean).join(' · ');
    return `<div class="geraet">
      <span class="punkt ${still ? '' : s.fern ? 'warn' : 'gut'}"></span>
      <span class="g-name">${esc(name)}<span class="g-neben">${esc(neben)}</span></span>
      <span class="g-zeit">${still ? 'vor ' + dauer(seit) : 'jetzt'}</span>
    </div>`;
  }).join('');
  return _karteKopf('Geräte an Bord', kopf) + `<div class="geraete">${zeilen}</div>`;
}

// ── Das Dashboard zeichnen ─────────────────────────────────────────────────

function zeichneUeberblick() {
  const z = _daten.zustand;
  const v = _daten.verbindung;
  if (!z) return;

  // Der Balken ganz oben. Er sagt das, was sonst niemand sagt: dass die GANZE
  // Seite alt ist. Die Gruppenalter in den Karten könnten das nicht — sie sind
  // mit dem Schnappschuss eingefroren. Eine Seite voller Zahlen, die aussehen
  // wie von jetzt, ist auf einem Boot gefährlicher als eine leere Seite.
  const alter = z.alter_s;
  const w = $('dbWarnung');
  if (z.quelle === 'server' && !z.boot_verbunden) {
    const lange = (alter || 0) > 3600;
    w.className = 'db-warnung' + (lange ? ' schwer' : '');
    w.innerHTML = '<span class="dbw-mark">Gespeichert</span>'
      + `<span>Das Boot ist nicht verbunden. Alle Werte auf dieser Seite sind der letzte
         übertragene Stand — ${alter != null ? 'vor ' + esc(dauer(alter)) : 'Alter unbekannt'}.
         Was sich seitdem geändert hat, steht hier nicht.</span>`;
    w.hidden = false;
  } else {
    w.hidden = true;
  }

  $('kAmpeln').innerHTML  = _ampelnBauen(z, v);
  $('kOrt').innerHTML     = _ortBauen(z);
  $('kStrom').innerHTML   = _stromBauen(z);
  $('kHeizung').innerHTML = _heizungBauen(z);
  $('kGeraete').innerHTML = _geraeteBauen();

  // Das innerHTML oben hat das Kartenfeld gerade weggeworfen. Leaflet hielte
  // danach einen Zeiger auf ein Element, das es nicht mehr gibt — deshalb wird
  // die Instanz verworfen und in das frische Feld neu gebaut.
  _dbKarte = null;
  const pos = z.position;
  if (pos && typeof pos.lat === 'number') {
    if (_seite === 'ueberblick') dbKarteZeigen();
    wetterPruefen(pos);
  }
}

// Die Kachelserver von OpenStreetMap werden von Freiwilligen betrieben und
// lehnen Anfragen OHNE Referer ab ("Access blocked" statt einer Karte). Der
// Server schickt aber `Referrer-Policy: no-referrer` — richtig so, das soll er
// auch weiter tun: die Adresse einer Logbuchseite hat niemanden ausserhalb
// etwas anzugehen.
//
// Die Ausnahme gehoert deshalb genau hierhin und nirgendwo sonst. Ein
// `referrerpolicy` am Bildelement schlaegt die Vorgabe des Dokuments, und
// `strict-origin-when-cross-origin` gibt nur den Ursprung heraus — den Namen
// des Servers, ohne Pfad und ohne Abfrage. Das ist genau das, was die
// Kachelserver zur Zuordnung verlangen, und keinen Halbsatz mehr.
const KACHEL_REFERRER = 'strict-origin-when-cross-origin';

// ── Die kleine Karte im Dashboard ──────────────────────────────────────────
// Eine eigene Leaflet-Instanz, nicht die der Positionsseite: zwei Karten in
// einem Dokument sind für Leaflet kein Problem, EINE Karte in zwei Feldern
// dagegen schon. Und die beiden zeigen Verschiedenes — hier nur, wo das Boot
// jetzt liegt, dort die Spur der letzten Wochen.

let _dbKarte = null, _dbKarteBoot = null;

function dbKarteZeigen() {
  const pos = (_daten.zustand || {}).position;
  const feld = $('dbKarteFeld');
  if (!feld || !pos || typeof pos.lat !== 'number') return;
  leafletLaden().then(() => dbKarteZeichnen()).catch(() => {
    const warte = feld.querySelector('.karten-warte');
    if (warte) warte.textContent = 'Die Kartenbibliothek lässt sich nicht laden.';
  });
}

function dbKarteZeichnen() {
  const L = window.L;
  const feld = $('dbKarteFeld');
  const pos = (_daten.zustand || {}).position;
  if (!L || !feld || !pos || typeof pos.lat !== 'number') return;

  if (!_dbKarte) {
    feld.innerHTML = '';
    // Ohne Bedienelemente: das hier ist ein Blick, kein Werkzeug. Wer die
    // Karte wirklich benutzen will, geht auf die Positionsseite — und genau
    // dorthin führt ein Klick.
    _dbKarte = L.map(feld, {
      zoomControl: false, attributionControl: false,
      scrollWheelZoom: false, dragging: false, doubleClickZoom: false,
      boxZoom: false, keyboard: false, touchZoom: false,
    });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18, className: 'karte-grund', referrerPolicy: KACHEL_REFERRER,
    }).addTo(_dbKarte);
    L.tileLayer('https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png',
                { maxZoom: 18, referrerPolicy: KACHEL_REFERRER }).addTo(_dbKarte);
    feld.style.cursor = 'pointer';
    feld.addEventListener('click', () => { location.hash = 'position'; });
    feld.title = 'Zur Positionsseite';
  }
  _dbKarte.setView([pos.lat, pos.lon], 14);
  if (_dbKarteBoot) _dbKarte.removeLayer(_dbKarteBoot);
  _dbKarteBoot = L.circleMarker([pos.lat, pos.lon], {
    radius: 7, color: '#0b1120', weight: 2, fillColor: '#22d3ee', fillOpacity: 1,
  }).addTo(_dbKarte);
  // Nach dem Einblenden stimmt die Größe erst, wenn das Feld wirklich steht.
  setTimeout(() => _dbKarte && _dbKarte.invalidateSize(), 60);
}

// ── Wetter am Liegeplatz ───────────────────────────────────────────────────
// Geholt wird es vom SERVER, nicht vom Boot: der Pi hängt am teuersten
// Datenweg dieses Systems, und das Wetter interessiert ohnehin nur den, der
// gerade ins Logbuch schaut. Über das Boot zu gehen wäre die falsche Richtung.

let _wetter = null;              // null | 'laedt' | Datensatz
let _wetterOrt = null;           // die Position, zu der _wetter gehört
let _wetterModelle = [];

/**
 * Neues Wetter holen, wenn es sich lohnt.
 *
 * Zwei Bremsen, und beide sind nötig. Die Position ändert sich mit jedem Fix
 * um ein paar Meter — ohne die erste Bremse liefe bei jedem Durchlauf ein
 * Abruf. Und am Liegeplatz ändert sie sich gar nicht — ohne die zweite gäbe es
 * nie wieder neues Wetter.
 */
function wetterPruefen(pos) {
  const jetzt = Date.now() / 1000;
  const weit = !_wetterOrt
    || Math.abs(_wetterOrt.lat - pos.lat) > 0.02
    || Math.abs(_wetterOrt.lon - pos.lon) > 0.02;
  const alt = !_wetterOrt || jetzt - _wetterOrt.t > WETTER_ALTER_S;
  if (_wetter === 'laedt' || !(weit || alt)) return;
  _wetterOrt = { lat: pos.lat, lon: pos.lon, t: jetzt };
  wetterLaden(pos.lat, pos.lon);
}

async function wetterLaden(lat, lon) {
  const vorher = _wetter;
  _wetter = vorher && vorher !== 'laedt' ? vorher : 'laedt';
  const d = await hole(`/api/logbuch/wetter?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`);
  // Schlägt der Abruf fehl, bleibt der alte Wert stehen. Das Wetter ändert sich
  // langsamer als die Erreichbarkeit eines fremden Dienstes.
  _wetter = d || (vorher !== 'laedt' ? vorher : null);
  if (_seite === 'ueberblick' && _daten.zustand) {
    const feld = $('kOrt');
    if (feld) {
      // Nur den Wetterteil tauschen: ein innerHTML auf der ganzen Karte würde
      // die Karte darin wegwerfen und Leaflet neu aufbauen lassen — bei jedem
      // Wetterabruf ein Flackern.
      const alt = feld.querySelector('.wetter');
      if (alt) alt.outerHTML = _wetterBauen();
    }
  }
}

// ── Alarme seit dem letzten Blick ──────────────────────────────────────────
// Die Frage, mit der jemand unterwegs ins Logbuch schaut, ist nicht "was ist
// gerade", sondern "was war, während ich nicht hingesehen habe". Ein Alarm,
// der um drei Uhr nachts kam und um vier von selbst wieder ging, taucht in
// keinem Zustand mehr auf — und ist trotzdem genau das, was man wissen will.
//
// Deshalb liegt der Vorhang ÜBER allem und muss weggeklickt werden. Ein
// stiller Streifen am Rand wäre höflicher und würde übersehen; ein übersehener
// Alarm ist der teuerste Fehler, den diese Oberfläche machen kann.
//
// Ob quittiert oder nicht, steht dabei: ein Alarm, der von selbst wieder ging,
// ohne dass ihn je jemand gesehen hat, ist etwas anderes als einer, den jemand
// zur Kenntnis genommen hat.

let _alarmStand = 0;

async function alarmeLaden() {
  const d = await hole('/api/logbuch/alarme');
  if (!d) return;
  _alarmStand = d.stand || 0;
  const liste = d.alarme || [];
  if (!liste.length) { $('alarmVorhang').hidden = true; return; }

  const jetzt = Date.now() / 1000;
  const marke = (a) => {
    const teile = [];
    if (a.quittiert) teile.push('<span class="ak-marke quittiert">bestätigt</span>');
    else teile.push('<span class="ak-marke offen">nicht bestätigt</span>');
    if (a.weg) teile.push('<span class="ak-marke weg">wieder vorbei</span>');
    else teile.push('<span class="ak-marke">steht noch an</span>');
    if (a.male > 1) teile.push(`<span class="ak-marke">${a.male}×</span>`);
    return `<div class="ak-marken">${teile.join('')}</div>`;
  };
  const stufe = { critical: 'rot', warning: 'warn', info: '' };

  $('alarmListe').innerHTML = liste.map(a => {
    // Wert und Schwelle nur, wenn beide dastehen: "12,1 (Schwelle 12,0)" ist
    // eine Aussage, ein einzelnes "12,1" ohne Bezug ist keine.
    const zahlen = (a.wert != null && a.schwelle != null)
      ? `${_zahl(a.wert)} bei Schwelle ${_zahl(a.schwelle)}` : '';
    const alt = a.zeit ? jetzt - a.zeit : null;
    return `<div class="ak-eintrag">
      <span class="punkt ${stufe[a.schwere] || 'warn'}"></span>
      <span>
        <div class="ak-name">${esc(a.name)}</div>
        ${zahlen ? `<div class="ak-neben">${esc(zahlen)}</div>` : ''}
        ${marke(a)}
      </span>
      <span class="ak-zeit">${esc(alt != null && alt < 86400 * 2
        ? 'vor ' + dauer(alt) : zeitpunkt(a.zeit))}</span>
    </div>`;
  }).join('');

  const offen = liste.filter(a => !a.quittiert).length;
  $('alarmZahl').textContent = liste.length === 1 ? '1 Alarm' : liste.length + ' Alarme';
  $('alarmHinweis').textContent = offen
    ? `${offen} davon ${offen === 1 ? 'wurde' : 'wurden'} an Bord nicht bestätigt.`
    : 'Alle wurden an Bord bestätigt.';
  $('alarmVorhang').hidden = false;
}

/** Eine Zahl so kurz wie möglich, ohne zu lügen. */
function _zahl(v) {
  if (typeof v !== 'number') return String(v);
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(Math.abs(v) < 10 ? 2 : 1);
}

async function alarmeGesehen() {
  $('alarmVorhang').hidden = true;
  // Der Stand kommt aus der ANZEIGE und nicht vom Server: zwischen Aufbau und
  // Klick kann ein neuer Alarm eingetroffen sein, und den hätte hier niemand
  // gesehen. Er soll beim nächsten Öffnen wieder auftauchen.
  await fetch('/api/logbuch/alarme/gesehen', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stand: _alarmStand }),
  }).catch(() => {});
}

// ── Aufbau: wie die Teile zusammenhängen ───────────────────────────────────
// Eine Nachschlageseite, keine Anzeige. Sie beantwortet die Fragen, die man
// sich nach einem halben Jahr selbst nicht mehr beantworten kann: Welcher Name
// führt wohin? Warum gibt es drei Oberflächen? Wie alt ist das, was ich sehe?
//
// Die Zahlen stehen NICHT frei in der Seite, sondern nennen jede ihre Quelle
// im Quelltext. Ein Test liest die genannten Stellen und vergleicht — driftet
// der Code, faellt der Test um, nicht der Leser auf eine alte Zahl herein.
// Eine Dokumentationsseite, die still veraltet, ist schlimmer als keine.

const AUFBAU_TAKTE = {
  bordnetz: [
    { was: 'Messwerte vom Bus', wert: '200 ms', neben: 'zugeschoben, nicht abgefragt',
      quelle: 'can_reader.py:RUNDRUF_TAKT_S' },
    { was: 'nach einem Schaltbefehl', wert: '50 ms', neben: 'drei Sekunden lang',
      quelle: 'can_reader.py:EILFENSTER_TAKT_S' },
    { was: 'Heizung, Detailseite', wert: '3 s', quelle: 'static/js/heizung.js:hzDetailPoller' },
    { was: 'Netzwerk-Fenster', wert: '5 s', quelle: 'static/js/alarms.js:netTimer' },
    { was: 'Geräteseite', wert: '10 s', quelle: 'static/js/geraete.js:_gerPoller' },
    { was: 'Heizungskachel', wert: '15 s', quelle: 'static/js/heizung.js:hzPoller' },
    { was: 'Verbindung', wert: '25 s', quelle: 'static/js/init.js:_connPoller' },
    { was: 'Update-Stand', wert: '60 s', quelle: 'static/js/init.js:_versionPoller' },
    { was: 'Tageswerte', wert: '2 min', quelle: 'static/js/battery.js:_dailyPoller' },
    { was: 'Sparklines der Statusleiste', wert: '2 min', quelle: 'static/js/init.js:_sparkPoller' },
    { was: 'Ladegerät-Abzeichen', wert: '5 min', quelle: 'static/js/init.js:_chargerPoller' },
    { was: 'Wasserstand', wert: '10 min', quelle: 'static/js/init.js:_wlPoller' },
    { was: 'Wetter', wert: '30 min', quelle: 'static/js/weather.js:_wxPoller' },
  ],
  server: [
    { was: 'Zustand, volle Übertragung', wert: '10 s', neben: 'Starlink, Kabel oder WLAN',
      quelle: 'sync/protokoll.py:BETRIEBSARTEN.voll' },
    { was: 'Zustand, gedrosselt', wert: '60 s', neben: 'Mobilfunk oder unbekannter Uplink',
      quelle: 'sync/protokoll.py:BETRIEBSARTEN.gedrosselt' },
    { was: 'Alarme', wert: 'sofort', neben: 'in beiden Betriebsarten' },
    { was: 'Verlauf', wert: 'laufend', neben: 'Minutenmittel, ab dem Stand des Servers' },
  ],
  logbuch: [
    { was: 'Dashboard, Ampeln und Karten', wert: '30 s', quelle: 'static/js/diagnose.js:LOGBUCH_TAKT_MS' },
    { was: 'die 24-Stunden-Flächen', wert: '5 min', quelle: 'static/js/diagnose.js:LOGBUCH_VERLAUF_MS' },
    { was: 'Messwerte-Seite', wert: '2 min', quelle: 'static/js/diagnose.js:LOGBUCH_MESS_MS' },
    { was: 'Wetter', wert: '15 min', neben: 'oder sobald das Boot 2 km gewandert ist',
      quelle: 'static/js/diagnose.js:WETTER_ALTER_S' },
    { was: 'Alarmvorhang', wert: 'beim Öffnen', neben: 'einmal je Besuch' },
  ],
};

/**
 * Die drei Namen, aus dem eigenen abgeleitet — nicht fest eingetragen.
 *
 * Fest eingetragen waeren sie beim naechsten Umzug falsch, und niemand wuesste
 * warum. Abgeleitet wird nur, wenn der eigene Name auch wirklich einer der drei
 * IST: hinter einer IP-Adresse oder einem Testnamen ergaebe die Ableitung
 * "mave.127.0.0.1" — eine Adresse, die es nicht gibt und die niemand tippen
 * sollte. Dann steht dort lieber der blosse Rollenname.
 */
function _aufbauNamen() {
  const h = location.hostname;
  const treffer = h.match(/^(logbuch|mave|pi\.mave)\.(.+\..+)$/);
  const stamm = treffer ? treffer[2] : null;
  const voll = (teil) => stamm ? teil + '.' + stamm : teil + '.…';
  return [
    { name: voll('mave'), rolle: 'Bordansicht',
      wohin: 'Im Bordnetz biegt der Router den Namen auf den Pi um. Draußen zeigt er '
           + 'auf den Server. Eine Herkunft, eine Installation — dieselbe App an Bord '
           + 'und unterwegs.' },
    { name: voll('pi.mave'), rolle: 'immer der Pi',
      wohin: 'Zeigt immer auf den Bordrechner, wird nie umgebogen. Der Weg, der auch '
           + 'dann noch trägt, wenn am Router etwas verstellt ist — und bei der '
           + 'Fehlersuche will man wissen, mit wem man spricht.' },
    { name: voll('logbuch'), rolle: 'immer der Server',
      wohin: 'Diagnose und Fernwartung, nie umgebogen. Sonst wäre das Logbuch '
           + 'ausgerechnet an Bord nicht erreichbar — und dort sitzt man, wenn man '
           + 'nachsehen will, warum etwas nicht stimmt. Der eigene Name hält '
           + 'nebenbei das mächtige Verwalter-Cookie vom Bordrechner fern.' },
  ];
}

const AUFBAU_OBERFLAECHEN = [
  { name: 'Bordansicht', wo: 'mave… /',
    text: 'Die eigentliche Anwendung: Messwerte, Licht, Heizung, Schalten. Hängt am '
        + 'WebSocket und bekommt Zustände zugeschoben, statt zu fragen.' },
  { name: 'Wandmonitor', wo: 'mave… /wand',
    text: 'Dieselbe Seite, ein anderes Manifest. Sperrt sich das Tablet, ist ein über '
        + 'requestFullscreen() geholtes Vollbild danach weg, und die Seite darf es '
        + 'nicht selbst zurückholen — das gewährt nur ein Fingergriff. Eine als '
        + 'fullscreen INSTALLIERTE Anwendung startet dagegen immer ohne '
        + 'Browserleiste, auch nach dem Entsperren. Dazu Bildschirm-Wachhalten und '
        + 'Nachtmodus.' },
  { name: 'Logbuch', wo: 'logbuch… /',
    text: 'Dieses Werkzeug. Ein eigenes Bündel, das nichts aus dem Bordbündel lädt — '
        + 'auf dem Pi Zero zählt jedes Kilobyte im Startpaket, und das Logbuch läuft '
        + 'ohnehin nur auf dem Server. Es beantwortet eine andere Frage: nicht wie es '
        + 'dem Boot jetzt geht, sondern wie es ihm ergangen ist.' },
];

function zeichneAufbau() {
  const feld = $('aufbau');
  if (!feld) return;
  const v = _daten.verbindung || {};
  const z = _daten.zustand || {};
  const hier = location.hostname;

  const art = v.betriebsart || null;
  const artZeile = art === 'voll' ? AUFBAU_TAKTE.server[0] : AUFBAU_TAKTE.server[1];

  const takt = (liste) => liste.map(t => `<div class="au-takt">
    <span class="au-was">${esc(t.was)}${t.neben ? `<span class="au-neben">${esc(t.neben)}</span>` : ''}</span>
    <span class="au-wert">${esc(t.wert)}</span>
    ${t.quelle ? `<span class="au-quelle">${esc(t.quelle)}</span>` : '<span></span>'}
  </div>`).join('');

  feld.innerHTML = `
    <section class="tafel">
      <div class="tafel-kopf"><h2>Die drei Namen</h2>
        <span class="hinweis">Sie sind gerade auf ${esc(hier)}</span></div>
      <p class="au-text">Ein Name für die Anwendung, einer für den Bordrechner, einer
        für den Server. Der erste wird umgebogen, die anderen beiden nie.</p>
      <div class="au-namen">${_aufbauNamen().map(n => `
        <div class="au-name${n.name === hier ? ' hier' : ''}">
          <div class="au-name-kopf">
            <code>${esc(n.name)}</code>
            <span class="au-rolle">${esc(n.rolle)}</span>
            ${n.name === hier ? '<span class="au-marke">hier</span>' : ''}
          </div>
          <p>${esc(n.wohin)}</p>
        </div>`).join('')}</div>
    </section>

    <section class="tafel">
      <div class="tafel-kopf"><h2>Drei Oberflächen</h2></div>
      <div class="au-flaechen">${AUFBAU_OBERFLAECHEN.map(o => `
        <div class="au-flaeche">
          <div class="au-flaeche-kopf"><b>${esc(o.name)}</b><code>${esc(o.wo)}</code></div>
          <p>${esc(o.text)}</p>
        </div>`).join('')}</div>
    </section>

    <section class="tafel">
      <div class="tafel-kopf"><h2>Der Weg der Daten</h2>
        <span class="hinweis">${v.verbunden
          ? (art ? 'Betriebsart ' + esc(art) : 'verbunden') : 'Boot nicht verbunden'}</span></div>
      <div class="au-weg">
        <div class="au-stufe"><b>NMEA-2000-Bus</b><span>Geräte melden von sich aus</span></div>
        <div class="au-pfeil" aria-hidden="true"></div>
        <div class="au-stufe"><b>Bordrechner</b><span>liest mit, prüft Alarme, schreibt Verlauf</span></div>
        <div class="au-pfeil" aria-hidden="true"></div>
        <div class="au-stufe"><b>Server</b><span>hält die Kopie, wenn das Boot schweigt</span></div>
        <div class="au-pfeil" aria-hidden="true"></div>
        <div class="au-stufe"><b>dieses Fenster</b><span>fragt alle 30 Sekunden nach</span></div>
      </div>
      <p class="au-text">Der Bordrechner baut die Verbindung auf, nicht der Server: der Pi
        hängt hinter Mobilfunk-NAT und ist von außen nicht erreichbar. Über dieselbe
        Leitung gehen Befehle zurück.</p>
      <p class="au-text">Was Sie hier sehen, ist deshalb zweimal verzögert — einmal durch
        den Takt des Bootes, einmal durch den des Logbuchs. Bei voller Übertragung sind
        das im schlechtesten Fall rund 40 Sekunden, über Mobilfunk rund 90.
        ${art ? `Gerade gilt <b>${esc(artZeile.wert)}</b> (${esc(artZeile.neben || '')}).` : ''}</p>
    </section>

    <section class="tafel">
      <div class="tafel-kopf"><h2>Wie oft etwas neu wird</h2>
        <span class="hinweis">jede Zahl nennt ihre Stelle im Quelltext</span></div>

      <h3 class="au-gruppe">Im Bordnetz, direkt am Pi</h3>
      <p class="au-text">Die Messwerte werden zugeschoben, nicht abgefragt. Alles, was
        nicht am Bus hängt, fragt für sich — und steht still, solange die Seite im
        Hintergrund liegt.</p>
      <div class="au-takte">${takt(AUFBAU_TAKTE.bordnetz)}</div>

      <h3 class="au-gruppe">Vom Boot zum Server</h3>
      <p class="au-text">Der Takt hängt am Uplink und lässt sich in den Einstellungen der
        Bordansicht von Hand festlegen. Wichtig: die Abfragen oben laufen unterwegs
        weiter und werden zum Boot durchgereicht — ein offener Bord-Monitor erzeugt über
        Mobilfunk deutlich mehr Verkehr, als der Zustandstakt vermuten lässt.</p>
      <div class="au-takte">${takt(AUFBAU_TAKTE.server)}</div>

      <h3 class="au-gruppe">Im Logbuch</h3>
      <p class="au-text">Reines Abfragen, kein WebSocket. Diese Seite wird selten geöffnet;
        eine offene Leitung dafür wäre Aufwand ohne Gegenwert.</p>
      <div class="au-takte">${takt(AUFBAU_TAKTE.logbuch)}</div>
    </section>`;
}

// ── Einstellungen des Logbuchs ─────────────────────────────────────────────
// Bootweit und nicht je Konto: welches Wettermodell für dieses Revier taugt,
// ist eine Erkenntnis über die Gegend und keine Geschmacksfrage.

let _einstellungen = null;

async function einstellungenLaden() {
  const d = await hole('/api/logbuch/einstellungen');
  if (!d) return;
  _einstellungen = d;
  _wetterModelle = d.wetter_modelle || [];
  zeichneEinstellungen();
}

function zeichneEinstellungen() {
  const feld = $('einstellungen');
  if (!feld || !_einstellungen) return;
  const darf = (_konto.handlungen || []).includes('einstellen');
  const jetzt = _einstellungen.wetter_modell;
  feld.innerHTML = `
    <div class="tafel-kopf"><h2>Wettermodell</h2>
      <span class="hinweis">${darf ? '' : 'nur lesend — dafür fehlt die Berechtigung'}</span></div>
    <p class="es-text">Welches Modell die Wetterdaten für den Liegeplatz liefert.
      Über Nord- und Ostsee rechnet ICON des Deutschen Wetterdienstes deutlich
      feiner als ein globales Modell; im Mittelmeer oder auf dem Atlantik kann
      eine andere Wahl besser sein. Die Vorhersage kommt in jedem Fall von
      Open-Meteo und wird zehn Minuten lang zwischengespeichert.</p>
    <div class="es-wahl">${(_einstellungen.wetter_modelle || []).map(m => `
      <label class="es-zeile${m.kennung === jetzt ? ' an' : ''}">
        <input type="radio" name="wettermodell" value="${esc(m.kennung)}"
          ${m.kennung === jetzt ? 'checked' : ''} ${darf ? '' : 'disabled'}
          onchange="wetterModellSetzen(this.value)">
        <span><b>${esc(m.name)}</b><span class="es-neben">${esc(m.neben)}</span></span>
      </label>`).join('')}</div>`;
}

async function wetterModellSetzen(kennung) {
  const r = await fetch('/api/logbuch/einstellungen', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ wetter_modell: kennung }),
  }).catch(() => null);
  if (!r || !r.ok) { popZeigen(`<div class="pop-titel">Nicht gespeichert</div>
    <p style="color:var(--text2);font-size:13px">Das Modell ließ sich nicht setzen.</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Gut</button></div>`);
    return; }
  _einstellungen.wetter_modell = kennung;
  zeichneEinstellungen();
  // Das alte Wetter stammt vom alten Modell — es jetzt stehen zu lassen, hiesse
  // eine Zahl unter einem Namen zu zeigen, der sie nicht gerechnet hat.
  _wetter = null; _wetterOrt = null;
  const pos = (_daten.zustand || {}).position;
  if (pos && typeof pos.lat === 'number') wetterPruefen(pos);
}

// ── Position ───────────────────────────────────────────────────────────────
// Wo das Boot liegt und wo es lag — mehr nicht. Ausdrücklich KEINE Navigation:
// kein Kurs, keine Geschwindigkeit, kein Besteck. Die Quelle ist das GNSS des
// Routers; am NMEA-2000-Bus sendet niemand eine Position (am laufenden Bus
// nachgesehen), es gibt also keine zweite Meinung.

let _spurTage = 30;
let _spur = null;
let _karte = null, _karteSpur = null, _karteBoot = null, _karteLiegen = null;
let _leaflet = null;

/**
 * Fuehrt --ueberblick-h an der Hoehe, die dem Ueberblick tatsaechlich bleibt.
 *
 * Nicht gerechnet, sondern gemessen: die Kopfzeile bricht auf schmalen
 * Schirmen um, und der Innenabstand von <main> steht in der Gestaltung, nicht
 * hier. Eine feste Zahl waere genau dann falsch, wenn es darauf ankommt.
 */
function _ueberblickHoeheFuehren() {
  const haupt = document.querySelector('.haupt');
  const kopf  = document.querySelector('header.kopf');
  const haupt_ = document.querySelector('main');
  if (!haupt || !kopf || !haupt_) return;
  let letzte = -1;
  const setzen = () => {
    const cs = getComputedStyle(haupt_);
    const innen = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
    const h = Math.round(window.innerHeight - kopf.getBoundingClientRect().height - innen);
    if (h === letzte) return;
    letzte = h;
    document.documentElement.style.setProperty('--ueberblick-h', h + 'px');
  };
  setzen();
  if (typeof ResizeObserver === 'function') {
    const beo = new ResizeObserver(setzen);
    beo.observe(kopf); beo.observe(haupt_);
  }
  window.addEventListener('resize', setzen);
  window.addEventListener('orientationchange', setzen);
}

/**
 * Die Kartenbibliothek erst holen, wenn jemand die Karte auch sehen will.
 *
 * 145 kB bei jedem Öffnen des Logbuchs mitzuschleppen wäre Verschwendung —
 * die meisten Besuche gelten dem Ladestand, nicht der Karte. Sie liegt bei
 * uns und nicht in einem Verteilnetz: die Seite soll nicht davon abhängen,
 * dass ein fremder Server erreichbar ist.
 */
function leafletLaden() {
  if (_leaflet) return _leaflet;
  _leaflet = new Promise((fertig, schiefgegangen) => {
    const stil = document.createElement('link');
    stil.rel = 'stylesheet';
    stil.href = '/static/karte/leaflet.css';
    document.head.appendChild(stil);
    const js = document.createElement('script');
    js.src = '/static/karte/leaflet.js';
    js.onload = () => fertig(window.L);
    js.onerror = () => schiefgegangen(new Error('Kartenbibliothek nicht ladbar'));
    document.head.appendChild(js);
  });
  return _leaflet;
}

/** Grad und Dezimalminuten — so steht es in jedem Logbuch und auf jedem Plotter. */
function _gradMinuten(wert, achse) {
  if (typeof wert !== 'number') return '—';
  const richtung = achse === 'lat' ? (wert >= 0 ? 'N' : 'S') : (wert >= 0 ? 'E' : 'W');
  const abs = Math.abs(wert);
  const grad = Math.floor(abs);
  const minuten = (abs - grad) * 60;
  const stellen = achse === 'lat' ? 2 : 3;
  return `${String(grad).padStart(stellen, '0')}° ${minuten.toFixed(3).padStart(6, '0')}' ${richtung}`;
}

/**
 * Der Router nennt seinen Gütewert "accuracy". Er ist KEINE Meterangabe: 0,6
 * Meter kann ein Empfänger dieser Klasse nicht, und der Wert sinkt, wenn mehr
 * Satelliten in Sicht sind — er verhält sich also wie ein HDOP. Deshalb steht
 * hier ein Wort und nicht eine erfundene Meterzahl.
 */
function _guete(wert) {
  if (typeof wert !== 'number') return { wort: '—', art: '' };
  if (wert < 1) return { wort: 'sehr gut', art: 'gut' };
  if (wert < 2) return { wort: 'gut', art: 'gut' };
  if (wert < 5) return { wort: 'brauchbar', art: '' };
  if (wert < 10) return { wort: 'mäßig', art: 'mau' };
  return { wort: 'schlecht', art: 'schlecht' };
}

async function spurLaden() {
  const d = await hole(`/api/verlauf/spur?tage=${_spurTage}`);
  if (!d) return;
  _spur = d;
  zeichnePosition();
  // Und die Karte auch. Sie wird beim Oeffnen gezeichnet, sobald die
  // Bibliothek da ist — das ist meist FRUEHER, als die Spur vom Server
  // zurueckkommt. Ohne diese Zeile blieb die Linie deshalb aus, waehrend die
  // Fusszeile darunter brav "558 Punkte" meldete.
  if (_karte) karteZeichnen();
}

function positionOeffnen() {
  zeichnePosition();
  spurLaden();
  leafletLaden().then(karteZeichnen).catch(() => {
    const f = $('karte');
    if (f) f.innerHTML = '<div class="karte-warte">Die Kartenbibliothek ließ sich '
      + 'nicht laden. Die Koordinaten daneben stimmen trotzdem.</div>';
  });
}

function karteZeichnen() {
  const L = window.L;
  const feld = $('karte');
  if (!L || !feld) return;

  if (!_karte) {
    feld.innerHTML = '';
    _karte = L.map(feld, { zoomControl: true, attributionControl: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18, className: 'karte-grund', referrerPolicy: KACHEL_REFERRER,
      attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(_karte);
    // Die Seezeichen liegen als durchsichtige Schicht darüber und bleiben
    // ungedimmt — sie sind der Grund, warum es OpenSeaMap und nicht irgendeine
    // Karte ist.
    L.tileLayer('https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png', {
      maxZoom: 18, opacity: 1, referrerPolicy: KACHEL_REFERRER,
      attribution: '&copy; <a href="https://openseamap.org">OpenSeaMap</a>',
    }).addTo(_karte);
    _karte.setView([54.0, 10.5], 8);
  }

  // Abschnitte statt einer durchgezogenen Linie. Ein Punkt mit `neu` folgt auf
  // eine Lücke im Aufschrieb — eine Linie darüber wäre gelogen, denn wo das
  // Boot dazwischen war, weiss niemand.
  const roh = (_spur && _spur.punkte) || [];
  const abschnitte = [];
  for (const p of roh) {
    if (!abschnitte.length || p.neu) abschnitte.push([]);
    abschnitte[abschnitte.length - 1].push([p.lat, p.lon]);
  }
  const punkte = roh.map(p => [p.lat, p.lon]);

  if (_karteSpur) { _karte.removeLayer(_karteSpur); _karteSpur = null; }
  const mitLinie = abschnitte.filter(a => a.length > 1);
  if (mitLinie.length) {
    _karteSpur = L.polyline(mitLinie, {
      color: '#22d3ee', weight: 2.5, opacity: .8, lineJoin: 'round',
    }).addTo(_karte);
  }

  // Wo das Boot länger lag, steht ein Ring — statt hundert Punkte übereinander.
  // Wie lange, sagt die Beschriftung beim Darauffahren.
  if (_karteLiegen) { _karte.removeLayer(_karteLiegen); _karteLiegen = null; }
  const liegen = roh.filter(p => (p.bis - p.t) >= 3600);
  if (liegen.length) {
    _karteLiegen = L.layerGroup(liegen.map(p => L.circleMarker([p.lat, p.lon], {
      // Der Ring wächst mit der Liegezeit, aber nur langsam und gedeckelt:
      // sonst verdeckt ein Winterlager die halbe Karte.
      radius: Math.min(11, 4 + Math.log10((p.bis - p.t) / 3600) * 3),
      color: '#22d3ee', weight: 1.6, opacity: .75,
      fillColor: '#22d3ee', fillOpacity: .16,
    }).bindTooltip(`lag hier ${dauer(p.bis - p.t)}<br>ab ${zeitpunkt(p.t)}`,
                   { direction: 'top' }))).addTo(_karte);
  }

  // Der aktuelle Standort: bevorzugt der gemessene aus dem Zustand, sonst der
  // letzte Punkt der Spur. Beides fehlt nur, wenn es nie einen Fix gab.
  const pos = (_daten.zustand || {}).position;
  const jetzt = (pos && typeof pos.lat === 'number')
    ? [pos.lat, pos.lon]
    : (punkte.length ? punkte[punkte.length - 1] : null);
  if (_karteBoot) { _karte.removeLayer(_karteBoot); _karteBoot = null; }
  if (jetzt) {
    _karteBoot = L.circleMarker(jetzt, {
      radius: 7, color: '#0b1120', weight: 2,
      fillColor: '#22d3ee', fillOpacity: 1,
    }).addTo(_karte).bindTooltip('Mave', { direction: 'top', offset: [0, -8] });
  }

  // Zuschnitt über ALLE Punkte, nicht über die Linie: lag das Boot die ganze
  // Zeit an einem Fleck, gibt es überhaupt keine Linie — und früher wäre die
  // Karte dann auf die Ostsee statt auf den Liegeplatz gesprungen.
  const alle = punkte.slice();
  if (jetzt) alle.push(jetzt);
  if (alle.length > 1) {
    _karte.fitBounds(L.latLngBounds(alle), { padding: [34, 34], maxZoom: 16 });
  } else if (alle.length === 1) {
    _karte.setView(alle[0], 15);
  }
  // Nach dem Einblenden stimmt die Größe erst, wenn das Feld wirklich steht.
  setTimeout(() => _karte && _karte.invalidateSize(), 60);
}

function zeichnePosition() {
  const feld = $('posJetzt');
  if (!feld) return;
  const z = _daten.zustand || {};
  const pos = z.position;
  const f = _frische('position');

  if (!pos || typeof pos.lat !== 'number') {
    feld.innerHTML = '<div class="dk-kopf"><h2>Standort</h2></div>'
      + '<div class="dk-satz leer">Zurzeit keine Position</div>'
      + '<div class="dk-neben">Der Router meldet keinen gültigen Fix. Das kann an '
      + 'der Antenne liegen, an der Umgebung — oder daran, dass der Router aus ist.</div>';
  } else {
    const g = _guete(pos.genauigkeit);
    const alt = pos.zeit ? (Date.now() / 1000 - pos.zeit) : null;
    feld.innerHTML = _kopf('Standort', 'position')
      + `<div class="pos-koord">${esc(_gradMinuten(pos.lat, 'lat'))}<br>`
      + `${esc(_gradMinuten(pos.lon, 'lon'))}</div>`
      + `<div class="dk-neben" style="margin-top:6px">${pos.lat.toFixed(6)}, ${pos.lon.toFixed(6)}</div>`
      + `<button class="pos-kopieren" onclick="positionKopieren(${pos.lat},${pos.lon})">Koordinaten kopieren</button>`
      + '<div class="dk-liste">'
      + _zeile('Satelliten', pos.satelliten != null ? String(pos.satelliten) : '—',
               pos.satelliten >= 6 ? 'gut' : pos.satelliten >= 4 ? 'mau' : 'schlecht')
      + _zeile('Güte', `${g.wort}${pos.genauigkeit != null ? ' (' + pos.genauigkeit + ')' : ''}`,
               g.art, 'kleiner ist besser — es ist kein Meterwert')
      + _zeile('Höhe', pos.hoehe_m != null ? pos.hoehe_m.toFixed(1) + ' m' : '—')
      + _zeile('Letzter Fix', alt != null ? 'vor ' + dauer(alt) : '—',
               alt != null && alt > 600 ? 'mau' : '')
      + '</div>';
  }

  const stand = $('spurStand');
  if (stand) {
    if (!_spur) {
      stand.innerHTML = '<span>Spur wird geladen…</span>';
    } else if (!_spur.punkte.length) {
      // Ehrlich statt leer: nach dem Einbau gibt es die Spur erst, wenn sie
      // aufgeschrieben wurde. Dreissig Tage brauchen dreissig Tage.
      stand.innerHTML = '<span>Für diesen Zeitraum liegt keine Position vor. '
        + 'Aufgeschrieben wird sie erst, seit der Bordrechner sie vom Router holt.</span>';
    } else {
      const von = _spur.punkte[0].t;
      const bis = _spur.punkte[_spur.punkte.length - 1].bis;
      const abschnitte = _spur.punkte.filter(p => p.neu).length + 1;
      stand.innerHTML =
        `<span>Spur <b>${_spurTage} Tage</b></span>`
        + `<span><b>${_spur.punkte.length}</b> Punkte</span>`
        + `<span>von <b>${esc(zeitpunkt(von))}</b> bis <b>${esc(zeitpunkt(bis))}</b></span>`
        + `<span>neuer Punkt ab <b>${_spur.abstand_m} m</b> Bewegung</span>`
        + (abschnitte > 1 ? `<span><b>${abschnitte}</b> Abschnitte — dazwischen wurde nichts aufgeschrieben</span>` : '');
    }
  }
}

async function positionKopieren(lat, lon) {
  const text = `${_gradMinuten(lat, 'lat')} ${_gradMinuten(lon, 'lon')}`;
  try {
    await navigator.clipboard.writeText(text);
    popZeigen(`<div class="pop-titel">Kopiert</div>
      <p style="color:var(--text2);font-size:13px;margin:0 0 16px">
        <span class="pos-koord">${esc(text)}</span></p>
      <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Gut</button></div>`);
  } catch (_) {
    // Ohne Zwischenablage (alter Browser, kein sicherer Kontext) wenigstens
    // zum Abschreiben hinlegen.
    popZeigen(`<div class="pop-titel">Koordinaten</div>
      <p style="color:var(--text2);font-size:13px;margin:0 0 16px">
        <span class="pos-koord">${esc(text)}</span></p>
      <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Gut</button></div>`);
  }
}


async function laden() {
  const [v, d, z, a] = await Promise.all([
    hole('/api/verbindung'), hole('/api/diagnose/uebersicht'),
    hole('/api/status'), hole('/api/anwesend'),
  ]);
  if (v) _daten.verbindung = v;
  if (d) _daten.diagnose = d;
  if (z) _daten.zustand = z;
  if (a) _daten.anwesend = a;
  // Der Ort nur zum Rechnen: daraus kommt der echte Sonnenuntergang fuer die
  // Nachtmodus-Automatik.
  const _p = (_daten.zustand || {}).position;
  if (_p && typeof _p.lat === 'number' && typeof wandOrtSetzen === 'function') {
    wandOrtSetzen(_p.lat, _p.lon);
  }
  zeichneZustand();
  zeichneUeberblick();
  if (_seite === 'position') { zeichnePosition(); if (_karte) karteZeichnen(); }
  // Die Aufbauseite zeigt die geltende Betriebsart und ob das Boot dranhaengt.
  // Ohne diese Zeile stuende dort, was beim Seitenaufbau bekannt war — und das
  // war nichts, weil die Seite aus der Adresse heraus VOR dem ersten Laden
  // gezeigt wird.
  if (_seite === 'aufbau') zeichneAufbau();
  zeichneStreifen();
  zeichneZahlen();
  zeichneAusfaelle();
  zeichneEreignisse();
  if (!$('slKonten').hidden) {
    const k = await hole('/api/konten');
    if (k) { _daten.konten = k; zeichneKonten(); }
  }
  if (!$('slWartung').hidden) zeichneWartung();
}

// ── Kopfzeile ──────────────────────────────────────────────────────────────

function zeichneZustand() {
  const v = _daten.verbindung;
  if (!v) return;
  $('zustPunkt').className = 'zust-punkt ' + (v.verbunden ? 'an' : 'weg');
  $('zustText').textContent = v.verbunden
    ? `verbunden seit ${zeitpunkt(v.seit)} · ${v.betriebsart === 'gedrosselt' ? 'gedrosselt (Mobilfunk)' : 'volle Übertragung'}`
    : 'Das Boot ist gerade nicht verbunden';
}

// ── Der Streifen ───────────────────────────────────────────────────────────
// Eine durchgehende Linie, in die die Ausfaelle Loecher schlagen. Bewusst
// nicht ein Balken je Tag: ein Ausfall von zwanzig Minuten soll auch wie
// zwanzig Minuten aussehen und nicht wie ein ganzer Tag.

/** Die Legende zum Verfügbarkeitsstreifen — aus derselben Tabelle wie die
 *  Farben selbst. "verbunden" ist kein Eintrag in ART: es ist der Normalfall
 *  und keine Ausfallart, steht aber im Streifen und gehört deshalb davor. */
function zeichneLegende() {
  const feld = $('legende');
  if (!feld) return;
  feld.innerHTML = '<span class="lg"><i class="lg-farbe an"></i>verbunden</span>'
    + Object.entries(ART).map(([schluessel, a]) =>
        `<span class="lg" title="${esc(a.satz)}"><i class="lg-farbe ${esc(schluessel)}"></i>${esc(a.wort)}</span>`
      ).join('');
}

function zeichneStreifen() {
  _streifenBauen('streifen', 'streifenAchse', _tage);
}

/** Der Streifen, einmal gebaut und zweimal benutzt: auf der Überblicksseite
 *  für den letzten Tag, auf der Verfügbarkeitsseite für den gewählten
 *  Zeitraum. Zwei Fassungen desselben Codes wären zwei Fassungen desselben
 *  Fehlers. */
function _streifenBauen(feldId, achseId, tage) {
  const v = _daten.verbindung;
  const feld = $(feldId);
  if (!v || !feld) return;
  const jetzt = Date.now() / 1000;
  const von = jetzt - tage * 86400;
  const spanne = jetzt - von;

  const luecken = (v.luecken || [])
    .filter(l => l.bis > von)
    .map(l => ({ ...l, ab: Math.max(l.ab, von) }))
    .sort((a, b) => a.ab - b.ab);

  let html = '';
  let stand = von;
  for (const l of luecken) {
    if (l.ab > stand) {
      html += `<div class="st-teil an" style="width:${((l.ab - stand) / spanne * 100).toFixed(3)}%"
                    title="verbunden — ${dauer(l.ab - stand)}"></div>`;
    }
    const breite = Math.max((l.bis - l.ab) / spanne * 100, 0.35);   // sonst unsichtbar
    const a = ART[l.art] || ART.unbekannt;
    html += `<div class="st-teil ${esc(l.art)}" style="width:${breite.toFixed(3)}%"
                  title="${esc(a.wort)} · ${esc(dauer(l.dauer_s))} · ab ${esc(zeitpunkt(l.ab))}"></div>`;
    stand = Math.max(stand, l.bis);
  }
  if (stand < jetzt) {
    html += `<div class="st-teil an" style="width:${((jetzt - stand) / spanne * 100).toFixed(3)}%"
                  title="verbunden — ${dauer(jetzt - stand)}"></div>`;
  }
  feld.innerHTML = html || '<div class="st-teil leer" style="width:100%"></div>';

  const achse = $(achseId);
  if (!achse) return;
  const schritte = 6;
  const kurz = tage <= 2;      // beim Tagesstreifen die Uhrzeit, sonst das Datum
  let a = '';
  for (let i = 0; i <= schritte; i++) {
    const t = new Date((von + spanne * i / schritte) * 1000);
    a += `<span>${t.toLocaleString('de-DE', kurz
      ? { hour: '2-digit', minute: '2-digit' }
      : { day: '2-digit', month: '2-digit' })}</span>`;
  }
  achse.innerHTML = a;
}

// ── Kennzahlen ─────────────────────────────────────────────────────────────

function zeichneZahlen() {
  const v = _daten.verbindung, d = _daten.diagnose;
  if (!v) return;
  const jetzt = Date.now() / 1000;
  const von = jetzt - _tage * 86400;
  const luecken = (v.luecken || []).filter(l => l.bis > von)
    .map(l => ({ ...l, ab: Math.max(l.ab, von) }));
  const weg = luecken.reduce((s, l) => s + (l.bis - l.ab), 0);
  const spanne = jetzt - von;
  const quote = spanne > 0 ? (1 - weg / spanne) * 100 : 100;
  const laengste = luecken.reduce((m, l) => Math.max(m, l.bis - l.ab), 0);
  const stromlos = luecken.filter(l => l.art === 'stromlos').length;

  const zahlen = [
    { wert: quote.toFixed(1) + ' %', lbl: 'verbunden', neben: `in ${_tage} Tagen`,
      art: quote > 95 ? 'gut' : (quote > 80 ? 'mau' : '') },
    { wert: String(luecken.length), lbl: 'Unterbrechungen',
      neben: luecken.length ? `längste ${dauer(laengste)}` : 'keine' },
    { wert: String(stromlos), lbl: 'davon stromlos',
      neben: stromlos ? 'Rechner war aus' : 'kein Stromausfall', art: stromlos ? 'mau' : 'gut' },
    { wert: d ? String(d.verlauf_stand) : '—', lbl: 'Messwerte im Verlauf',
      neben: d && d.geparkt ? `${d.geparkt} ohne sichere Zeit` : 'alle zeitlich eingeordnet' },
  ];
  $('zahlen').innerHTML = zahlen.map(z => `
    <div class="zahl">
      <div class="zahl-wert ${z.art || ''}">${esc(z.wert)}</div>
      <div class="zahl-lbl">${esc(z.lbl)}</div>
      <div class="zahl-neben">${esc(z.neben)}</div>
    </div>`).join('');
}

// ── Ausfälle ───────────────────────────────────────────────────────────────

function zeichneAusfaelle() {
  const v = _daten.verbindung;
  if (!v) return;
  const jetzt = Date.now() / 1000;
  const von = jetzt - _tage * 86400;
  const luecken = (v.luecken || []).filter(l => l.bis > von)
    .sort((a, b) => b.ab - a.ab);
  $('ausfaelleZahl').textContent = luecken.length
    ? `${luecken.length} in ${_tage} Tagen` : '';
  if (!luecken.length) {
    $('ausfaelle').innerHTML =
      `<div class="leerlauf">Keine Unterbrechung in den letzten ${_tage} Tagen.</div>`;
    return;
  }
  // Nur die jüngsten zeigen. Bei einem Update-Abend stehen hier schnell zwanzig
  // Einträge, und die drängen alles Wichtige aus dem Blick — gesucht wird meist
  // der letzte Ausfall, nicht der vorletzte Monat.
  const zeigen = _alleAusfaelle ? luecken : luecken.slice(0, 6);
  const mehr = $('ausfaelleMehr');
  if (mehr) {
    mehr.hidden = luecken.length <= 6 || _alleAusfaelle;
    mehr.textContent = `Alle ${luecken.length} zeigen`;
  }
  $('ausfaelle').innerHTML = zeigen.map(l => {
    const a = ART[l.art] || ART.unbekannt;
    return `<div class="zeile">
      <div class="z-zeit">${esc(zeitpunkt(l.ab))}</div>
      <div>
        <div class="z-text"><span class="z-marke ${esc(l.art)}">${esc(a.wort)}</span>${esc(l.grund || a.satz)}</div>
        <div class="z-neben">bis ${esc(zeitpunkt(l.bis))}</div>
      </div>
      <div class="z-dauer">${esc(dauer(l.dauer_s))}</div>
    </div>`;
  }).join('');
}

function ausfaelleAlle() {
  _alleAusfaelle = true;
  zeichneAusfaelle();
}

function zeichneEreignisse() {
  const d = _daten.diagnose;
  if (!d) return;
  const e = (d.ereignisse || []).slice(0, 40);
  if (!e.length) {
    $('ereignisse').innerHTML = '<div class="leerlauf">Noch nichts gemeldet.</div>';
    return;
  }
  $('ereignisse').innerHTML = e.map(x => `
    <div class="zeile">
      <div class="z-zeit">${esc(zeitpunkt(x.zeit || x.wand))}</div>
      <div><div class="z-text">${esc(ereignisText(x))}</div></div>
      <div class="z-dauer"></div>
    </div>`).join('');
}

function ereignisText(x) {
  const d = x.daten || x;
  switch (x.art) {
    case 'betriebsart':
      return d.betriebsart === 'gedrosselt'
        ? 'Übertragung gedrosselt — das Boot hängt am Mobilfunk'
        : 'Volle Übertragung — das Boot hat wieder festes Netz';
    case 'start':   return 'Das Boot hat sich angemeldet';
    case 'alarm': {
      // Der Wert gehört dazu: "Alarm: Ladestand" sagt nicht, ob es knapp oder
      // dramatisch war. Mit Wert und Schwelle liest man es in einer Zeile.
      const name = d.name || d.text || 'ohne Angabe';
      const zusatz = (d.wert != null && d.schwelle != null)
        ? ` (${d.wert} statt ${d.schwelle})`
        : (d.wert != null ? ` (${d.wert})` : '');
      return `Alarm: ${name}${zusatz}`
        + (d.schwere === 'critical' ? ' — kritisch' : '');
    }
    case 'alarm_weg': return 'Alarm vorbei';
    default:        return x.art || 'Ereignis';
  }
}

// ── Konten ─────────────────────────────────────────────────────────────────

function zeichneKonten() {
  const k = _daten.konten;
  if (!k) return;
  const rollen = Object.fromEntries((k.rollen || []).map(r => [r.schluessel, r]));
  $('konten').innerHTML = (k.konten || []).map(c => {
    const r = rollen[c.rolle] || {};
    const ich = c.name === (_konto && _konto.name);
    // Angezeigt wird der Spitzname; der Anmeldename steht klein daneben. An
    // Bord ruft niemand die Steuerfrau bei ihrem Anmeldenamen — beim Anmelden
    // braucht man ihn aber, deshalb verschwindet er nicht.
    const zeigen = c.anzeigename || c.name;
    return `<div class="konto">
      <div>
        <div class="k-name ${c.gesperrt ? 'gesperrt' : ''}">${esc(zeigen)}${ich ? ' <span class="hinweis">(du)</span>' : ''}</div>
        <div class="k-rolle">${esc(r.name || c.rolle)}${c.gesperrt ? ' · gesperrt' : ''}${
          c.eingeladen ? ' · <span style="color:var(--yellow)">eingeladen, Passwort noch nicht gesetzt</span>' : ''}
          ${zeigen !== c.name ? '· meldet sich an als <b>' + esc(c.name) + '</b>' : ''}
          ${
          c.gueltig_bis ? ' · <span style="color:var(--yellow)">befristet bis '
            + esc(new Date(c.gueltig_bis * 1000).toLocaleDateString('de-DE')) + '</span>' : ''}</div>
        <div class="k-darf">${esc((r.handlungen || []).join(' · '))}</div>
      </div>
      <div class="k-tat">
        <button class="knopf stumm" onclick="kontoBearbeiten('${esc(c.name)}')">Bearbeiten</button>
        ${ich ? '' : `<button class="knopf stumm" onclick="kontoSperren('${esc(c.name)}', ${!c.gesperrt})">${c.gesperrt ? 'Entsperren' : 'Sperren'}</button>`}
        ${ich ? '' : `<button class="knopf warn" onclick="kontoLoeschen('${esc(c.name)}')">Löschen</button>`}
      </div>
    </div>`;
  }).join('') || '<div class="leerlauf">Noch kein Konto.</div>';
}

function _rollenWahl(gewaehlt) {
  const rollen = (_daten.konten && _daten.konten.rollen) || [];
  return rollen.map(r =>
    `<option value="${esc(r.schluessel)}"${r.schluessel === gewaehlt ? ' selected' : ''}>`
    + `${esc(r.name)} — ${esc(r.handlungen.join(', '))}</option>`).join('');
}

function kontoNeuOeffnen() {
  popZeigen(`
    <div class="pop-titel">Konto anlegen</div>
    <!-- EIN Name. Vorher standen hier Anmeldename UND Name der Person — für
         eine Handvoll Leute auf einem Boot ist das eine Unterscheidung ohne
         Unterschied. Der Spitzname bleibt: er ist das, was in der Anzeige
         steht, und der ist oft ein anderer als der zum Anmelden. -->
    <label class="pop-feld"><span>Name</span>
      <input id="nkName" type="text" autocapitalize="none" autocorrect="off" spellcheck="false"
             placeholder="damit meldet sich die Person an"></label>
    <label class="pop-feld"><span>Anzeigename (wenn abweichend)</span>
      <input id="nkSpitz" type="text" placeholder="optional"></label>
    <label class="pop-feld"><span>Rolle</span>
      <select id="nkRolle">${_rollenWahl('crew')}</select></label>

    <div class="pop-feld"><span>Passwort</span>
      <div class="wahl-zeile">
        <label class="wahl"><input type="radio" name="pwArt" value="einladung" checked
               onchange="_pwArtWechsel()"> Einladungslink schicken</label>
        <label class="wahl"><input type="radio" name="pwArt" value="selbst"
               onchange="_pwArtWechsel()"> selbst vergeben</label>
      </div>
    </div>
    <div class="pop-hinweis" id="nkErklaerung">Die eingeladene Person setzt ihr
      Passwort selbst — es wandert dann nie per Nachricht durch die Gegend, und
      niemand sonst kennt es. Der Link gilt sieben Tage und lässt sich nur
      einmal einlösen.</div>
    <label class="pop-feld hidden" id="nkPwFeld"><span>Passwort (mindestens 8 Zeichen)</span>
      <input id="nkPw" type="text"></label>

    <div class="pop-fehler hidden" id="nkFehler"></div>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="kontoAnlegen()">Anlegen</button>
    </div>`);
}

function _pwArtWechsel() {
  const einladen = document.querySelector('input[name="pwArt"]:checked').value === 'einladung';
  $('nkPwFeld').classList.toggle('hidden', einladen);
  $('nkErklaerung').textContent = einladen
    ? 'Die eingeladene Person setzt ihr Passwort selbst — es wandert dann nie '
      + 'per Nachricht durch die Gegend, und niemand sonst kennt es. Der Link '
      + 'gilt sieben Tage und lässt sich nur einmal einlösen.'
    : 'Du vergibst das Passwort und musst es der Person mitteilen. Das ist der '
      + 'schnellere, aber schwächere Weg — teile es nicht über einen Kanal, der '
      + 'irgendwo mitgeschrieben wird.';
}

function _einladungZeigen(name, pfad) {
  const link = location.origin + pfad;
  popZeigen(`
    <div class="pop-titel">Einladung für ${esc(name)}</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 14px">
      Schick diesen Link an die Person. Sie setzt darüber ihr eigenes Passwort
      und findet dort auch eine kurze Anleitung.</p>
    <div class="link-kasten" id="einlLink">${esc(link)}</div>
    <p style="color:var(--text3);font-size:11px;line-height:1.5;margin:12px 0 16px">
      Der Link gilt sieben Tage und lässt sich nur einmal einlösen. Er wird
      <b>nicht gespeichert</b> — geht er verloren, lade die Person neu ein.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Schließen</button>
      <button class="knopf" onclick="_linkKopieren()" id="kopierKnopf">Link kopieren</button>
    </div>`);
}

async function _linkKopieren() {
  const t = $('einlLink').textContent;
  try {
    await navigator.clipboard.writeText(t);
    $('kopierKnopf').textContent = 'Kopiert';
  } catch (_) {
    // Ohne Zwischenablage (unsicherer Kontext, alte Browser): markieren, dann
    // kann man von Hand kopieren.
    const r = document.createRange();
    r.selectNodeContents($('einlLink'));
    getSelection().removeAllRanges();
    getSelection().addRange(r);
    $('kopierKnopf').textContent = 'Markiert — jetzt kopieren';
  }
}

function kontoBearbeiten(name) {
  const c = ((_daten.konten && _daten.konten.konten) || []).find(x => x.name === name);
  if (!c) return;
  const ich = c.name === (_konto && _konto.name);
  popZeigen(`
    <div class="pop-titel">${esc(c.anzeigename || c.name)} bearbeiten</div>
    <label class="pop-feld"><span>Anzeigename (wenn abweichend)</span>
      <input id="bkSpitz" type="text" value="${esc(c.spitzname || '')}"
             placeholder="${esc(c.name)}"></label>
    <label class="pop-feld"><span>Rolle</span>
      <select id="bkRolle"${ich ? ' disabled' : ''}>${_rollenWahl(c.rolle)}</select></label>
    <label class="pop-feld"><span>Zugang befristen (leer = unbefristet)</span>
      <input id="bkBis" type="date" value="${c.gueltig_bis
        ? new Date(c.gueltig_bis * 1000).toISOString().slice(0, 10) : ''}"></label>
    ${c.rolle === 'techniker' && !c.gueltig_bis
      ? '<div class="pop-hinweis">Ein Technikerzugang sollte befristet sein — er ist der '
        + 'einzige Fremde im System, und einer, den niemand zurückzieht, bleibt für immer offen.</div>'
      : ''}
    <label class="pop-feld"><span>Neues Passwort (leer lassen = unverändert)</span>
      <input id="bkPw" type="text" placeholder="oder unten einen Link schicken"></label>
    ${ich ? '<div class="pop-hinweis">Die eigene Rolle lässt sich nicht ändern — sonst könnte man sich '
          + 'selbst aussperren, und hier ist niemand, der es wieder geradezieht.</div>' : ''}
    <div class="pop-fehler hidden" id="bkFehler"></div>
    <div class="konto-weitere">
      <button class="knopf stumm" onclick="linkErneuern('${esc(name)}')">
        ${c.eingeladen ? 'Neuen Einladungslink' : 'Link zum Passwort-Zurücksetzen'}</button>
      ${c.eingeladen
        ? `<button class="knopf stumm" onclick="einladungZurueck('${esc(name)}')">Einladung zurücknehmen</button>`
        : `<button class="knopf stumm" onclick="ueberallAbmelden('${esc(name)}')">Überall abmelden</button>`}
    </div>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="kontoSpeichern('${esc(name)}', ${ich})">Speichern</button>
    </div>`);
}

async function kontoSpeichern(name, ich) {
  const rumpf = { spitzname: $('bkSpitz').value };
  const bis = $('bkBis').value;
  // Ende des gewählten Tages, nicht sein Anfang: wer "bis 5. September" wählt,
  // meint diesen Tag noch mit.
  rumpf.laeuft_ab = bis ? (new Date(bis + 'T23:59:59').getTime() / 1000) : 0;
  if (!ich) rumpf.rolle = $('bkRolle').value;
  const pw = $('bkPw').value.trim();
  if (pw) rumpf.passwort = pw;
  const r = await fetch('/api/konten/' + encodeURIComponent(name), {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rumpf),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    const f = $('bkFehler');
    f.textContent = d.detail || 'Das hat nicht geklappt.';
    f.classList.remove('hidden');
    return;
  }
  popSchliessen();
  laden();
}

async function kontoAnlegen() {
  const name = $('nkName').value.trim(), rolle = $('nkRolle').value;
  const einladen = document.querySelector('input[name="pwArt"]:checked').value === 'einladung';
  const fehler = $('nkFehler');
  const r = await fetch('/api/konten', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, rolle, einladen,
                           passwort: einladen ? '' : $('nkPw').value,
                           spitzname: $('nkSpitz').value }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    fehler.textContent = d.detail || 'Das hat nicht geklappt.';
    fehler.classList.remove('hidden');
    return;
  }
  laden();
  if (d.einladungslink) _einladungZeigen(d.anzeigename || name, d.einladungslink);
  else popSchliessen();
}

async function linkErneuern(name) {
  const r = await fetch(`/api/konten/${encodeURIComponent(name)}/einladung`, { method: 'POST' });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { popZeigen(`<div class="pop-titel">Nicht möglich</div>
    <p style="color:var(--text2);font-size:13px">${esc(d.detail || 'Unbekannter Fehler')}</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Schließen</button></div>`); return; }
  laden();
  _einladungZeigen(name, d.einladungslink);
}

async function einladungZurueck(name) {
  await fetch(`/api/konten/${encodeURIComponent(name)}/einladung`, { method: 'DELETE' });
  popSchliessen();
  laden();
}

async function ueberallAbmelden(name) {
  const r = await fetch(`/api/konten/${encodeURIComponent(name)}/abmelden`, { method: 'POST' });
  const d = await r.json().catch(() => ({}));
  popZeigen(`<div class="pop-titel">Abgemeldet</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5">
      ${d.beendet || 0} Anmeldung${d.beendet === 1 ? '' : 'en'} beendet. Das Passwort
      gilt weiter — es muss also niemand ein neues suchen.</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Schließen</button></div>`);
  laden();
}

async function kontoSperren(name, sperren) {
  await fetch('/api/konten/' + encodeURIComponent(name), {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gesperrt: sperren }),
  });
  laden();
}

function kontoLoeschen(name) {
  popZeigen(`
    <div class="pop-titel">Konto ${esc(name)} löschen?</div>
    <p style="color:var(--text2);font-size:13px;margin:0 0 18px">
      Laufende Sitzungen dieses Kontos enden sofort — auch an Bord.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf warn" onclick="kontoLoeschenJa('${esc(name)}')">Löschen</button>
    </div>`);
}

async function kontoLoeschenJa(name) {
  await fetch('/api/konten/' + encodeURIComponent(name), { method: 'DELETE' });
  popSchliessen();
  laden();
}

// ── Änderungen ─────────────────────────────────────────────────────────────
// Was sich geändert hat, in der Sprache des Eigners. Die ausführliche
// Begründung steht in derselben Commit-Nachricht, aber sie gehört nicht
// hierher — hier zählt, was für IHN anders wird.

let _staende = null;

async function staendeLaden() {
  const feld = $('verlaufStaende');
  if (!_staende) feld.innerHTML = '<div class="leerlauf">wird abgefragt…</div>';
  const d = await hole('/api/system/versionen');
  if (!d) { feld.innerHTML = '<div class="leerlauf">Das Boot antwortet nicht.</div>'; return; }
  _staende = d;
  zeichneStaende();
}

function _standBlock(e, art, tat) {
  const zeit = e.zeit ? zeitpunkt(e.zeit) : '';
  // Die Bereiche sagen, ob einen die Änderung überhaupt angeht: wer nur die
  // Bordansicht benutzt, muss eine Änderung am Logbuch nicht lesen.
  const bereiche = (e.bereiche || []).map(b =>
    `<span class="ae-bereich ${esc(b.toLowerCase())}">${esc(b)}</span>`).join('');
  return `<div class="ae-stand">
    <div>
      <div class="ae-kopf">
        ${art ? `<span class="ae-marke ${art === 'neu' ? 'neu' : ''}">${art === 'neu' ? 'neu' : 'läuft'}</span>` : ''}
        ${e.version ? `<span class="ae-version">v${esc(e.version)}</span>` : ''}
        <span class="ae-zeit">${esc(zeit)}</span>
        ${bereiche}
        <span class="ae-hash">${esc(e.hash)}</span>
      </div>
      <div class="ae-titel">${esc(e.titel)}</div>
      ${e.punkte && e.punkte.length
        ? '<ul class="ae-punkte">' + e.punkte.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>'
        : '<div class="ae-ohne">Ohne Kurzfassung — nur die Überschrift.</div>'}
    </div>
    <div class="ae-tat">${tat || ''}</div>
  </div>`;
}

function zeichneStaende() {
  const d = _staende;
  if (!d) return;
  $('standJetzt').textContent = `installiert: ${d.version || ''} (${d.installiert || '—'})`;

  const bereit = d.bereit || [];
  $('bereitTafel').hidden = !bereit.length;
  if (bereit.length) {
    $('bereit').innerHTML = bereit.map(e => _standBlock(e, 'neu', '')).join('');
  }

  const verlauf = d.verlauf || [];
  $('verlaufStaende').innerHTML = verlauf.length
    ? verlauf.map((e, i) => _standBlock(e, i === 0 ? 'jetzt' : '',
        i === 0 ? '' : `<button class="knopf stumm" onclick="zurueckFragen('${esc(e.hash)}')">Hierhin zurück</button>`
      )).join('')
    : '<div class="leerlauf">Kein Verlauf abrufbar.</div>';
}

function einspielenFragen() {
  const anzahl = (_staende?.bereit || []).length;
  popZeigen(`
    <div class="pop-titel">${anzahl} Änderung${anzahl === 1 ? '' : 'en'} einspielen?</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 18px">
      Der Bordrechner holt den neuen Stand und startet die Anwendung neu. Wer
      gerade davorsitzt, sieht für etwa eine halbe Minute nichts.
      Zurückgehen ist danach jederzeit möglich.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf" onclick="fernUpdateJa()">Einspielen</button>
    </div>`);
}

function zurueckFragen(hash) {
  const e = (_staende?.verlauf || []).find(x => x.hash === hash);
  popZeigen(`
    <div class="pop-titel">Auf diesen Stand zurückgehen?</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 8px">
      <b>${esc(e?.titel || hash)}</b></p>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 18px">
      Alles, was danach kam, ist anschließend nicht mehr aktiv. Die Daten auf
      dem Boot bleiben unberührt — nur der Programmstand geht zurück. Über
      "Einspielen" kommst du jederzeit wieder nach vorn.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <button class="knopf warn" onclick="zurueckJa('${esc(hash)}')">Zurückgehen</button>
    </div>`);
}

async function zurueckJa(hash) {
  const k = $('popKarte');
  k.innerHTML = '<div class="pop-titel">Läuft…</div><p style="color:var(--text2);font-size:13px">Das Boot arbeitet.</p>';
  const r = await fetch('/api/system/zurueck', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hash }),
  });
  const d = await r.json().catch(() => ({}));
  k.innerHTML = `<div class="pop-titel">${r.ok ? 'Zurückgegangen' : 'Nicht möglich'}</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5">${
      esc(r.ok ? `Der Bordrechner läuft jetzt auf "${d.titel}" und startet neu.`
               : (d.detail || 'Unbekannter Fehler'))}</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen();setTimeout(staendeLaden, 20000)">Schließen</button></div>`;
}

// ── Mitschnitt ─────────────────────────────────────────────────────────────
// Der Anlass: "die Schaltflächen sind ausgegraut" — und eine Stunde später ist
// der Fehler weg. Ohne Mitschnitt bleibt nur Raten.

let _msSuchUhr = null;

function mitschnittSuchen() {
  // Nicht bei jedem Tastendruck fragen: das läuft über die Verbindung zum Boot.
  clearTimeout(_msSuchUhr);
  _msSuchUhr = setTimeout(mitschnittLaden, 400);
}

async function mitschnittLaden() {
  const feld = $('mitschnitt');
  const art = $('msArt').value, quelle = $('msQuelle').value, suche = $('msSuche').value.trim();
  const p = new URLSearchParams({ stunden: '24', grenze: '400' });
  if (art) p.set('art', art);
  if (quelle) p.set('quelle', quelle);
  if (suche) p.set('suche', suche);
  const d = await hole('/api/debug/log?' + p);
  if (!d) { feld.innerHTML = '<div class="leerlauf">Nicht abrufbar.</div>'; return; }

  // Die Quellenauswahl aus dem füllen, was tatsächlich vorkommt — eine feste
  // Liste liefe sonst der Wirklichkeit hinterher.
  const wahl = $('msQuelle');
  const jetzige = wahl.value;
  wahl.innerHTML = '<option value="">alle Quellen</option>'
    + (d.quellen || []).map(q => `<option value="${esc(q)}"${q === jetzige ? ' selected' : ''}>${esc(q)}</option>`).join('');

  const e = d.eintraege || [];
  $('msStand').textContent = `${e.length} von ${d.gesamt} Einträgen der letzten 24 Stunden`;
  feld.innerHTML = e.length ? e.map(x => {
    const t = new Date(x.zeit * 1000);
    return `<div class="ms-zeile ${esc(x.art)}">
      <span class="ms-zeit">${t.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
      <span class="ms-quelle">${esc(x.quelle)}</span>
      <span>
        <div class="ms-text">${esc(x.text)}</div>
        ${x.daten ? `<div class="ms-daten">${esc(JSON.stringify(x.daten))}</div>` : ''}
      </span>
    </div>`;
  }).join('') : '<div class="leerlauf">Nichts gefunden — was in diesem Fall eine gute Nachricht ist.</div>';
}

// ── Fernwartung ────────────────────────────────────────────────────────────

function zeichneWartung() {
  const v = _daten.verbindung;
  const da = v && v.verbunden;
  $('wartung').innerHTML = `
    <div class="w-zeile">
      <div>
        <div class="w-text">Uhr des Bordrechners stellen</div>
        <div class="w-neben">Der Pi hat keine gepufferte Uhr. Nach einem Stromausfall
          geht sie falsch, bis er Netz hat — dann lassen sich Messwerte nicht
          zeitlich einordnen.</div>
      </div>
      <button class="knopf stumm" ${da ? '' : 'disabled'} onclick="fernZeit()">Stellen</button>
    </div>
    ${da ? '' : '<div class="leerlauf">Das Boot ist nicht verbunden — Fernwartung geht erst wieder, wenn es sich meldet.</div>'}`;
}

async function fernUpdateJa() {
  const k = $('popKarte');
  k.innerHTML = '<div class="pop-titel">Läuft…</div><p style="color:var(--text2);font-size:13px">Das Boot arbeitet.</p>';
  const r = await fetch('/api/system/update', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  const d = await r.json().catch(() => ({}));
  k.innerHTML = `<div class="pop-titel">${r.ok ? 'Angestoßen' : 'Nicht möglich'}</div>
    <p style="color:var(--text2);font-size:13px;white-space:pre-wrap;max-height:220px;overflow:auto">${
      esc(r.ok ? (d.output || 'Der Bordrechner aktualisiert sich.') : (d.detail || 'Unbekannter Fehler'))}</p>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Schließen</button></div>`;
}

async function fernZeit() {
  const r = await fetch('/api/system/time-sync', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ zeit: Date.now() / 1000 }) });
  popZeigen(`<div class="pop-titel">${r.ok ? 'Uhr gestellt' : 'Nicht möglich'}</div>
    <div class="pop-tat"><button class="knopf" onclick="popSchliessen()">Schließen</button></div>`);
}

// ── Popup ──────────────────────────────────────────────────────────────────

function popZeigen(html) {
  $('popKarte').innerHTML = html;
  $('pop').classList.remove('hidden');
}
function popSchliessen() { $('pop').classList.add('hidden'); }

// ── Anmeldung ──────────────────────────────────────────────────────────────
// Eigene, kleine Fassung: die Bordansicht bringt ihre eigene mit, und diese
// Seite soll deren Bundle nicht laden muessen.

function anmeldungZeigen(meldung) {
  let f = $('anmeldung');
  if (!f) {
    f = document.createElement('div');
    f.id = 'anmeldung'; f.className = 'anmeldung';
    document.body.appendChild(f);
  }
  f.innerHTML = `
    <form class="anm-karte" onsubmit="return anmelden(event)">
      <div class="anm-marke"><span class="marke-mave">Mave</span> <span>Logbuch</span></div>
      <div class="anm-wohin">Diagnose und Fernwartung</div>
      <label class="anm-feld"><span>Name</span>
        <input id="anmName" type="text" autocomplete="username" autocapitalize="none"
               autocorrect="off" spellcheck="false" required></label>
      <label class="anm-feld"><span>Passwort</span>
        <input id="anmPw" type="password" autocomplete="current-password" required></label>
      <div class="anm-fehler${meldung ? '' : ' hidden'}" id="anmFehler">${esc(meldung || '')}</div>
      <button class="anm-knopf" type="submit" id="anmKnopf">Anmelden</button>
    </form>`;
  f.classList.remove('hidden');
  setTimeout(() => $('anmName') && $('anmName').focus(), 60);
}

async function anmelden(ev) {
  ev.preventDefault();
  const knopf = $('anmKnopf'), fehler = $('anmFehler');
  knopf.disabled = true; knopf.textContent = 'Einen Moment…';
  try {
    const r = await fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('anmName').value, passwort: $('anmPw').value }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      fehler.textContent = d.detail || 'Anmeldung nicht möglich.';
      fehler.classList.remove('hidden');
      $('anmPw').value = '';
      return false;
    }
    location.reload();
  } catch (_) {
    fehler.textContent = 'Keine Verbindung.';
    fehler.classList.remove('hidden');
  } finally {
    knopf.disabled = false; knopf.textContent = 'Anmelden';
  }
  return false;
}

async function abmelden() {
  try { await fetch('/api/logout', { method: 'POST' }); } catch (_) {}
  location.reload();
}

// Denselben Service Worker wie die Bordansicht anmelden. Nicht wegen des
// Zwischenspeichers — das Logbuch braucht ohnehin frische Daten —, sondern
// wegen der Installierbarkeit: Chrome bietet "Installieren" (eigenes Fenster,
// eigenes Symbol) nur an, wenn ein Service Worker mit fetch-Handler laeuft.
// Ohne ihn bleibt es bei einer Verknuepfung, die im Browser aufgeht.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

start();

// ── Messwerte ──────────────────────────────────────────────────────────────
// Mehrere Graphen untereinander an EINER Zeitachse. Das ist der Unterschied zur
// Bordansicht: dort liest man einen Wert im Vorbeigehen ab, hier sucht man
// Zusammenhänge — dass die Spannung einbricht, WÄHREND der Strom steigt, sieht
// man nur, wenn beide übereinanderliegen und dieselbe Achse teilen.
//
// Gezeichnet wird je Reihe ein Band zwischen kleinstem und größtem Wert des
// Zeitfensters und die Mittellinie hinein. Nur den Mittelwert zu zeichnen wäre
// glatter, würde aber genau das verschlucken, weswegen man hier hinschaut: die
// kurze Spitze, den Einbruch, den Ausreißer.

const GRUPPEN = [
  { schluessel: 'ladung',   name: 'Ladestand',  einheit: '%',
    felder: [{ f: 'soc', n: 'Ladestand', farbe: '#34d399' }] },
  { schluessel: 'spannung', name: 'Spannung',   einheit: 'V',
    felder: [{ f: 'voltage', n: 'Bordspannung', farbe: '#60a5fa' }] },
  { schluessel: 'strom',    name: 'Strom',      einheit: 'A',
    felder: [{ f: 'current', n: 'Bilanz', farbe: '#22d3ee' },
             { f: 'current_charge', n: 'Zufluss', farbe: '#34d399' },
             { f: 'current_discharge', n: 'Verbrauch', farbe: '#f87171' }] },
  { schluessel: 'quellen',  name: 'Ladequellen', einheit: 'W',
    felder: [{ f: 'charger', n: 'Ladegerät', farbe: '#fbbf24' },
             { f: 'solar1', n: 'Solar', farbe: '#a78bfa' },
             { f: 'orion', n: 'Lichtmaschine', farbe: '#fb923c' }] },
  { schluessel: 'tanks',    name: 'Tanks',      einheit: '%',
    felder: [{ f: 'tank1', n: 'Wasser', farbe: '#60a5fa' },
             { f: 'tank2', n: 'Diesel', farbe: '#fbbf24' }] },
  { schluessel: 'zellen',   name: 'Zellabweichung', einheit: 'V',
    felder: [{ f: 'zelldiff', n: 'Größte Abweichung', farbe: '#a78bfa' }] },

  // Drei getrennte Kurven statt einer: Minuten, Prozent und An/Aus teilen sich
  // keine Achse. Nebeneinander auf derselben ZEITachse beantworten sie
  // zusammen die Frage, warum der Router faellt — bricht 2,4 GHz weg und ERST
  // DANN geht die Laufzeit auf null, hat sich der Funkchip aufgehaengt und der
  // Watchdog hinterhergeraeumt. Klettert stattdessen der Speicher, ist es der
  // Speicher.
  // Alles, was nur an oder aus sein kann, in EINE Kurve — sie teilen sich die
  // Achse und, viel wichtiger, die Zeit. Die Reihenfolge ist die Antwort:
  // faellt erst 2,4 GHz und geht ERST DANN der Router weg, hat sich der
  // Funkchip aufgehängt und der Watchdog hinterhergeräumt. Geht der Router weg,
  // während beide Bänder oben waren, war es etwas anderes.
  { schluessel: 'rtfunk',   name: 'Router und Funk', einheit: 'an',
    felder: [{ f: 'rt_an', n: 'Router antwortet', farbe: '#22d3ee' },
             { f: 'wl24', n: '2,4 GHz', farbe: '#fb923c' },
             { f: 'wl5', n: '5 GHz', farbe: '#60a5fa' },
             { f: 'rt_neu', n: 'Neustart', farbe: '#f87171' }] },
  // Die Gegenprobe. Last in Prozent statt als Lastzahl, damit sie neben dem
  // Speicher auf derselben Achse steht.
  { schluessel: 'rtlast',   name: 'Router-Auslastung', einheit: '%',
    felder: [{ f: 'rt_ram', n: 'Speicher', farbe: '#f87171' },
             { f: 'rt_cpu', n: 'Last', farbe: '#fbbf24' }] },
];

// Der gezeigte Zeitraum.
//
// Vorher stand hier nur "die letzten N Stunden". Das genuegt, solange man
// ausschliesslich vorwaerts lebt — sobald man blaettern oder ein Datum eingeben
// kann, ist "jetzt minus X" keine Beschreibung mehr, sondern eine Bewegung.
// Deshalb ein echtes Fenster aus zwei Zeitpunkten.
//
// `_messFolgt` ist der Unterschied zwischen beidem: solange es gilt, haengt das
// Fenster am rechten Rand und wandert beim Nachladen mit. Wer blaettert oder
// ein Datum eintraegt, loest es — sonst risse ihn der Zwei-Minuten-Takt jedes
// Mal zurueck in die Gegenwart, mitten im Hinsehen.
let _messDauer = 24 * 3600;    // Breite des Fensters in Sekunden
let _messBis   = null;         // rechter Rand; null heisst "jetzt"
let _messFolgt = true;
let _messDaten = null;
let _aus = new Set();          // abgewählte Gruppen

/** Das Fenster als Sekundenpaar — die einzige Stelle, die das ausrechnet. */
function _messFenster() {
  const bis = _messFolgt ? Date.now() / 1000 : _messBis;
  return { von: Math.floor(bis - _messDauer), bis: Math.ceil(bis) };
}

async function messwerteLaden() {
  const { von, bis } = _messFenster();
  const feld = $('reihen');
  // Den Platzhalter NUR, wenn noch nichts dasteht. Vorher wurden bei jedem
  // Laden alle Graphen weggeworfen und gleich darauf neu gebaut — das sah aus,
  // als lade die ganze Seite neu, und zwar bei jedem Loslassen des Schiebers
  // und alle zwei Minuten von selbst.
  if (!_messDaten) feld.innerHTML = '<div class="mess-leer">wird geladen…</div>';
  const d = await hole(`/api/verlauf/reihen?von=${von}&bis=${bis}&punkte=700`);
  if (!d) { feld.innerHTML = '<div class="mess-leer">Keine Daten abrufbar.</div>'; return; }
  _messDaten = d;
  _deckung = { von, bis };
  zeichneZeitleiste();
  zeichneMesswerte();
}

function zeichneMesswerte() {
  const d = _messDaten;
  const feld = $('reihen');
  if (!d || !d.punkte.length) {
    feld.innerHTML = '<div class="mess-leer">Für diesen Zeitraum liegt nichts vor.</div>';
    $('messStand').textContent = '';
    $('zeitachse').innerHTML = '';
    $('gruppenwahl').innerHTML = '';
    return;
  }

  const stunden = (d.bis - d.von) / 3600;
  $('messStand').textContent =
    `${d.roh_anzahl.toLocaleString('de-DE')} Messwerte, zusammengefasst zu ${d.punkte.length} `
    + `Punkten à ${dauer(d.eimer_s)}`;

  // Nur Gruppen zeigen, für die es Daten gibt
  const da = GRUPPEN.filter(g => g.felder.some(x => d.felder.includes(x.f)));
  $('gruppenwahl').innerHTML = da.map(g => `
    <button class="gw-knopf${_aus.has(g.schluessel) ? '' : ' an'}"
            onclick="gruppeUmschalten('${g.schluessel}')">
      <span class="gw-punkt" style="background:${g.felder[0].farbe}"></span>${esc(g.name)}
    </button>`).join('');

  const sichtbar = da.filter(g => !_aus.has(g.schluessel));
  feld.innerHTML = sichtbar.map(g => `
    <div class="reihe" data-gruppe="${g.schluessel}">
      <div class="reihe-kopf">
        <span class="reihe-name">${esc(g.name)}</span>
        <span class="reihe-jetzt" id="jetzt-${g.schluessel}"></span>
      </div>
      <div class="reihe-bild">
        <canvas id="cv-${g.schluessel}"></canvas>
        <span class="reihe-spanne" id="oben-${g.schluessel}"></span>
        <span class="reihe-spanne unten" id="unten-${g.schluessel}"></span>
      </div>
    </div>`).join('') + '<div class="faden" id="faden"><span class="faden-zeit" id="fadenZeit"></span></div>';

  for (const g of sichtbar) zeichneReihe(g, d);
  zeichneZeitachse(d);
  fadenVerdrahten(d, sichtbar);
}

function zeichneReihe(g, d) {
  const cv = $('cv-' + g.schluessel);
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const b = cv.getBoundingClientRect();
  cv.width = Math.max(1, Math.round(b.width * dpr));
  cv.height = Math.max(1, Math.round(b.height * dpr));
  const c = cv.getContext('2d');
  c.scale(dpr, dpr);
  const B = b.width, H = b.height;

  const felder = g.felder.filter(x => d.felder.includes(x.f));
  // Gemeinsame Skala je Gruppe: nur so sind Zufluss und Verbrauch vergleichbar.
  let lo = Infinity, hi = -Infinity;
  for (const p of d.punkte) {
    // Nur was im Ausschnitt liegt. Beim Ziehen bleiben kurz Punkte von
    // ausserhalb in den Daten stehen; wuerden sie die Skala bestimmen, spraenge
    // sie in dem Moment zurecht, in dem die neuen Daten eintreffen.
    if (p.t < d.von || p.t > d.bis) continue;
    for (const x of felder) {
      const w = p[x.f];
      if (!w) continue;
      lo = Math.min(lo, w[1]); hi = Math.max(hi, w[2]);
    }
  }
  if (!isFinite(lo)) return;
  if (hi - lo < 1e-9) { hi = lo + 1; lo -= 0; }
  const luft = (hi - lo) * 0.08;
  lo -= luft; hi += luft;

  const x = t => (t - d.von) / (d.bis - d.von) * B;
  const y = w => H - (w - lo) / (hi - lo) * H;

  // Nulllinie, wo sie in den Ausschnitt fällt — bei Strömen die wichtigste
  // Orientierung: darüber fließt hinein, darunter hinaus.
  if (lo < 0 && hi > 0) {
    c.strokeStyle = 'rgba(148,163,184,.35)';
    c.lineWidth = 1;
    c.setLineDash([3, 3]);
    c.beginPath(); c.moveTo(0, y(0)); c.lineTo(B, y(0)); c.stroke();
    c.setLineDash([]);
  }

  for (const x_ of felder) {
    // Erst das Band zwischen kleinstem und größtem Wert…
    c.fillStyle = x_.farbe + '26';
    c.beginPath();
    let auf = false;
    for (const p of d.punkte) {
      const w = p[x_.f]; if (!w) continue;
      const px = x(p.t);
      if (!auf) { c.moveTo(px, y(w[2])); auf = true; } else c.lineTo(px, y(w[2]));
    }
    for (let i = d.punkte.length - 1; i >= 0; i--) {
      const w = d.punkte[i][x_.f]; if (!w) continue;
      c.lineTo(x(d.punkte[i].t), y(w[1]));
    }
    if (auf) { c.closePath(); c.fill(); }

    // …dann die Mittellinie hinein
    c.strokeStyle = x_.farbe;
    c.lineWidth = 1.6;
    c.lineJoin = 'round';
    c.beginPath();
    auf = false;
    for (const p of d.punkte) {
      const w = p[x_.f]; if (!w) continue;
      const px = x(p.t), py = y(w[0]);
      if (!auf) { c.moveTo(px, py); auf = true; } else c.lineTo(px, py);
    }
    c.stroke();
  }

  const k = (hi - lo) < 2 ? 2 : ((hi - lo) < 20 ? 1 : 0);
  $('oben-' + g.schluessel).textContent = hi.toFixed(k) + ' ' + g.einheit;
  $('unten-' + g.schluessel).textContent = lo.toFixed(k) + ' ' + g.einheit;
  werteZeigen(g, d, d.punkte.length - 1);
}

function werteZeigen(g, d, i) {
  const ziel = $('jetzt-' + g.schluessel);
  if (!ziel) return;
  const p = d.punkte[i];
  if (!p) return;
  const felder = g.felder.filter(x => d.felder.includes(x.f) && p[x.f]);
  const k = g.einheit === 'V' ? 2 : (g.einheit === 'W' ? 0 : 1);
  ziel.innerHTML = felder.map(x => `
    <span class="rj-wert">
      <span class="rj-punkt" style="background:${x.farbe}"></span>
      <span class="rj-zahl">${p[x.f][0].toFixed(k)}</span>
      <span class="rj-einheit">${esc(g.einheit)} ${esc(x.n)}</span>
    </span>`).join('');
}

function zeichneZeitachse(d) {
  const n = 6;
  let h = '';
  for (let i = 0; i <= n; i++) {
    const t = new Date((d.von + (d.bis - d.von) * i / n) * 1000);
    const lang = (d.bis - d.von) > 3 * 86400;
    h += `<span>${t.toLocaleString('de-DE', lang
      ? { day: '2-digit', month: '2-digit' }
      : { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>`;
  }
  $('zeitachse').innerHTML = h;
}

function fadenVerdrahten(d, sichtbar) {
  const feld = $('reihen'), faden = $('faden');
  if (!feld || !faden) return;
  feld.onmousemove = e => {
    const b = feld.getBoundingClientRect();
    const rel = (e.clientX - b.left) / b.width;
    if (rel < 0 || rel > 1) return;
    const i = Math.min(d.punkte.length - 1, Math.max(0, Math.round(rel * (d.punkte.length - 1))));
    faden.classList.add('an');
    faden.style.left = (d.punkte[i].t - d.von) / (d.bis - d.von) * b.width + 'px';
    $('fadenZeit').textContent = new Date(d.punkte[i].t * 1000)
      .toLocaleString('de-DE', { day: '2-digit', month: '2-digit',
                                 hour: '2-digit', minute: '2-digit' });
    for (const g of sichtbar) werteZeigen(g, d, i);
  };
  feld.onmouseleave = () => {
    faden.classList.remove('an');
    for (const g of sichtbar) werteZeigen(g, d, d.punkte.length - 1);
  };
}

function gruppeUmschalten(schluessel) {
  if (_aus.has(schluessel)) _aus.delete(schluessel); else _aus.add(schluessel);
  zeichneMesswerte();
}

// ── Die Zeitleiste ─────────────────────────────────────────────────────────
// Blaettern, Datum eintragen, zurueck ins Jetzt. Bewusst KEIN Ziehbalken ueber
// dem Graphen: der laesst sich mit der Maus fein bedienen und mit dem Daumen
// gar nicht, und diese Seite wird auch auf dem Telefon geoeffnet. Zwei Pfeile
// und zwei Felder koennen beide.

/** Sekunden -> "2026-09-05T18:30" fuer ein datetime-local-Feld. */
function _fuerFeld(s) {
  const d = new Date(s * 1000);
  // Ortszeit, nicht UTC: das Feld zeigt sie so an, und toISOString() waere um
  // die Zeitzone verschoben — im Sommer um zwei Stunden.
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
       + `T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Die Felder und den Hinweis auf den Stand des Fensters bringen. */
function zeichneZeitleiste() {
  const { von, bis } = _messFenster();
  const v = $('messVon'), b = $('messBis');
  // Nicht ueberschreiben, waehrend jemand darin tippt — sonst springt ihm die
  // Eingabe unter den Fingern weg, sobald der Zwei-Minuten-Takt nachlaedt.
  if (v && document.activeElement !== v) v.value = _fuerFeld(von);
  if (b && document.activeElement !== b) b.value = _fuerFeld(bis);
  const j = $('messJetzt');
  if (j) j.classList.toggle('an', _messFolgt);

  zeichneSchiene();
  const feld = $('messVorhanden');
  if (!feld) return;
  const z = (_daten.diagnose || {}).verlauf_zeitraum || {};
  feld.textContent = (z.von && z.bis)
    ? `vorhanden ab ${zeitpunkt(z.von)}`
    : '';
}

// ── Der Bereichsschieber ───────────────────────────────────────────────────
// Zwei Griffe auf einer Spur, die den GESAMTEN vorhandenen Verlauf zeigt. Das
// Band dazwischen ist der Ausschnitt, den die Graphen zeigen; es laesst sich
// als Ganzes verschieben.
//
// Selbst gebaut und nicht mit zwei <input type=range> uebereinandergelegt: die
// koennen nicht aneinander anstossen, und der obere fraesse die Klicks des
// unteren. Ein paar Zeigerereignisse sind ehrlicher als ein Stapel Tricks.

// Das kleinste Fenster. Darunter zeigt der Graph nur noch Rauschen, und die
// beiden Griffe liegen uebereinander.
const SCHIENE_MIN_S = 300;

let _zieht = null;      // 'a' | 'b' | 'band' | null

// Nachladen WAEHREND des Ziehens.
//
// Der erste Anlauf zeichnete aus einer einmal geholten Grobuebersicht. Das war
// sparsam und in einem Fall unbrauchbar: zieht man auf einen engen Ausschnitt,
// liegt in der groben Uebersicht dort KEIN einziger Punkt, und der Graph wurde
// leer. Ein leeres Bild ist schlechter als ein spaeteres.
//
// Jetzt werden echte Daten geholt, aber selbstbegrenzend: es ist immer nur
// EINE Abfrage unterwegs, und zwischen zweien liegen mindestens 120 ms. Kommt
// waehrenddessen eine neue Zeigerbewegung, wird sie gemerkt und nach der
// laufenden Abfrage nachgeholt — nicht eingereiht. Damit haengt die Bildrate
// von selbst daran, wie schnell der Server antwortet: heute misst er 19 ms
// fuer einen Tag und 66 fuer sieben, also fluessig; wird der Verlauf in einem
// Jahr traege, kommen eben weniger Bilder statt einer Warteschlange.
const ZIEH_TAKT_MS = 70;
// Wie viel mehr geholt wird, als gerade zu sehen ist: das Dreifache, also je
// eine Fensterbreite links und rechts als Vorrat.
//
// Das ist der Unterschied zwischen "es ruckelt weniger" und "es ruckelt
// nicht". Am laufenden Server gemessen kostet eine Antwort fuer sieben Tage
// 217 ms — jede Zeigerbewegung darauf warten zu lassen, kann nicht fluessig
// werden. Und die Punktzahl half nicht: 200 statt 700 Stuetzstellen sparten
// ganze 29 ms, weil die Arbeit an den ZEILEN haengt und nicht an den Punkten.
//
// Mit Vorrat kostet Verschieben und Verkleinern gar keine Abfrage mehr; nur
// wer ueber den Rand hinauszieht, wartet einmal. Die Aufloesung bleibt dabei
// gleich: dreifache Breite, dreifach viele Stuetzstellen.
const ZIEH_VORRAT = 1.0;
const ZIEH_PUNKTE = 200;

let _ziehBedarf = false;
let _ziehLaeuft = false;
let _ziehZuletzt = 0;
let _deckung = null;      // welchen Zeitraum die vorliegenden Punkte abdecken

/** Reicht, was schon da ist, fuer das gezeigte Fenster? */
function _gedeckt() {
  if (!_deckung || !_messDaten) return false;
  const { von, bis } = _messFenster();
  return von >= _deckung.von && bis <= _deckung.bis;
}

async function _ziehNachladen() {
  if (_ziehLaeuft) return;
  if (_gedeckt()) { _ziehBedarf = false; return; }   // nichts zu holen
  const wartet = ZIEH_TAKT_MS - (Date.now() - _ziehZuletzt);
  if (wartet > 0) { setTimeout(() => { if (_ziehBedarf) _ziehNachladen(); }, wartet); return; }
  _ziehBedarf = false;
  _ziehLaeuft = true;
  try {
    const { von, bis } = _messFenster();
    const rand = (bis - von) * ZIEH_VORRAT;
    const a = Math.floor(von - rand), b = Math.ceil(bis + rand);
    const punkte = Math.round(ZIEH_PUNKTE * (1 + 2 * ZIEH_VORRAT));
    const d = await hole(`/api/verlauf/reihen?von=${a}&bis=${b}&punkte=${punkte}`);
    if (d && _zieht) {
      _deckung = { von: a, bis: b };
      // Die Punkte decken mehr ab, als gezeigt wird; die Achse bleibt das
      // Fenster. Was daneben liegt, zeichnet der Canvas ausserhalb seiner
      // Kante — und die Hochwertskala rechnet ohnehin nur mit dem Sichtbaren.
      const f = _messFenster();
      _messDaten = { ...d, von: f.von, bis: f.bis };
      zeichneMesswerte();
    }
  } finally {
    _ziehLaeuft = false;
    _ziehZuletzt = Date.now();
    if (_ziehBedarf && _zieht) _ziehNachladen();
  }
}

/** Der Zeitraum, den die Spur abbildet: alles Vorhandene, mindestens bis jetzt. */
function _schieneRaum() {
  const z = (_daten.diagnose || {}).verlauf_zeitraum || {};
  if (!z.von || !z.bis) return null;
  const jetzt = Date.now() / 1000;
  const von = z.von;
  // Bis jetzt und nicht bis zum letzten Eintrag: sonst laege der rechte Griff
  // bei laufender Anzeige staendig ein Stueck vor dem Rand, obwohl "jetzt"
  // gemeint ist.
  const bis = Math.max(z.bis, jetzt);
  return bis > von ? { von, bis } : null;
}

function zeichneSchiene() {
  const w = $('messSchiene');
  if (!w) return;
  const raum = _schieneRaum();
  if (!raum) { w.hidden = true; return; }
  w.hidden = false;
  const { von, bis } = _messFenster();
  const anteil = t => Math.max(0, Math.min(1, (t - raum.von) / (raum.bis - raum.von))) * 100;
  const a = anteil(von), b = anteil(bis);
  $('messBand').style.left = a + '%';
  $('messBand').style.width = Math.max(0, b - a) + '%';
  $('messGriffA').style.left = a + '%';
  $('messGriffB').style.left = b + '%';
  $('messSpurVon').textContent = zeitpunkt(raum.von);
  $('messSpurBis').textContent = zeitpunkt(raum.bis);
}

/** Bildschirm-x auf einen Zeitpunkt der Spur abbilden. */
function _schieneZeit(x) {
  const raum = _schieneRaum();
  const spur = $('messSpur');
  if (!raum || !spur) return null;
  const r = spur.getBoundingClientRect();
  const anteil = Math.max(0, Math.min(1, (x - r.left) / r.width));
  return raum.von + anteil * (raum.bis - raum.von);
}

function _schieneStart(e, was) {
  const raum = _schieneRaum();
  if (!raum) return;
  e.preventDefault();
  _zieht = was;
  const { von, bis } = _messFenster();
  _ziehStart = { x: e.clientX, von, bis, raum };
  e.target.setPointerCapture && e.target.setPointerCapture(e.pointerId);
}
let _ziehStart = null;

function _schieneZieht(e) {
  if (!_zieht || !_ziehStart) return;
  const raum = _ziehStart.raum;
  let { von, bis } = _ziehStart;
  if (_zieht === 'band') {
    // Als Ganzes verschieben, ohne die Breite zu aendern — und nicht ueber die
    // Raender der Spur hinaus.
    const proPixel = (raum.bis - raum.von) / $('messSpur').getBoundingClientRect().width;
    let versatz = (e.clientX - _ziehStart.x) * proPixel;
    versatz = Math.max(raum.von - von, Math.min(raum.bis - bis, versatz));
    von += versatz; bis += versatz;
  } else {
    const t = _schieneZeit(e.clientX);
    if (t === null) return;
    // Die Griffe stossen aneinander an, statt sich zu ueberholen. Ein Fenster
    // mit negativer Breite gibt es nicht.
    if (_zieht === 'a') von = Math.min(t, bis - SCHIENE_MIN_S);
    else bis = Math.max(t, von + SCHIENE_MIN_S);
  }
  _messDauer = bis - von;
  _messBis = bis;
  // Reicht der rechte Griff bis an den Rand der Spur, laeuft die Anzeige
  // weiter mit — genau wie beim Blaettern.
  _messFolgt = bis >= raum.bis - 60;
  zeichneSchiene();
  zeichneZeitleiste();
  // Sofort neu zeichnen, mit den Punkten, die schon da sind: nur die Achse
  // wandert. Das kostet nichts und laeuft mit der Bildwiederholrate — die
  // Kurve folgt dem Griff, statt in Stufen nachzurucken. Die neuen Daten
  // ersetzen sie, sobald sie eintreffen.
  _achseNachziehen();
  if (!_gedeckt()) { _ziehBedarf = true; _ziehNachladen(); }
}

/** Dieselben Punkte, neue Achse. Kein Netz, kein Warten. */
function _achseNachziehen() {
  if (!_messDaten) return;
  const { von, bis } = _messFenster();
  _messDaten = { ..._messDaten, von, bis };
  zeichneMesswerte();
}

function _schieneEnde() {
  if (!_zieht) return;
  _zieht = null; _ziehStart = null;
  _voreinstellungLoesen();
  // Erst beim Loslassen laden. Bei jedem Pixel nachzufragen hiesse, den Pi
  // waehrend eines Ziehens mit hundert Abfragen zu belegen.
  messwerteLaden();
}

function _schieneVerdrahten() {
  const spur = $('messSpur');
  if (!spur) return;
  $('messGriffA').addEventListener('pointerdown', e => _schieneStart(e, 'a'));
  $('messGriffB').addEventListener('pointerdown', e => _schieneStart(e, 'b'));
  $('messBand').addEventListener('pointerdown', e => _schieneStart(e, 'band'));
  // Auf der Spur selbst: den naechstgelegenen Griff dorthin holen. Wer neben
  // das Band tippt, meint diese Stelle — und nicht "nichts passiert".
  spur.addEventListener('pointerdown', e => {
    if (e.target !== spur) return;
    const t = _schieneZeit(e.clientX);
    const { von, bis } = _messFenster();
    _schieneStart(e, Math.abs(t - von) < Math.abs(t - bis) ? 'a' : 'b');
    _schieneZieht(e);
  });
  window.addEventListener('pointermove', _schieneZieht);
  window.addEventListener('pointerup', _schieneEnde);
  window.addEventListener('pointercancel', _schieneEnde);
  // Mit der Tastatur: ein Zehntel der Spur je Tastendruck.
  for (const [id, was] of [['messGriffA', 'a'], ['messGriffB', 'b']]) {
    $(id).addEventListener('keydown', e => {
      const schritt = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
      if (!schritt) return;
      e.preventDefault();
      const raum = _schieneRaum();
      if (!raum) return;
      let { von, bis } = _messFenster();
      const d = schritt * (raum.bis - raum.von) / 10;
      if (was === 'a') von = Math.max(raum.von, Math.min(von + d, bis - SCHIENE_MIN_S));
      else bis = Math.min(raum.bis, Math.max(bis + d, von + SCHIENE_MIN_S));
      _messDauer = bis - von; _messBis = bis;
      _messFolgt = bis >= raum.bis - 60;
      _voreinstellungLoesen();
      messwerteLaden();
    });
  }
}

/**
 * Um ein ganzes Fenster vor oder zurueck.
 *
 * Um die eigene Breite und nicht um einen festen Tag: wer sechs Stunden
 * ansieht, will sechs Stunden weiter, und wer dreissig Tage ansieht, dreissig.
 */
function messSchieben(richtung) {
  const { bis } = _messFenster();
  const neu = bis + richtung * _messDauer;
  const jetzt = Date.now() / 1000;
  // Ueber die Gegenwart hinaus gibt es nichts. Dort heftet es sich wieder an —
  // dann laeuft die Anzeige weiter mit, statt an einem toten Rand zu stehen.
  if (neu >= jetzt) {
    _messFolgt = true;
    _messBis = null;
  } else {
    _messFolgt = false;
    _messBis = neu;
  }
  _voreinstellungLoesen();
  messwerteLaden();
}

/** Zurueck an den rechten Rand. */
function messJetzt() {
  _messFolgt = true;
  _messBis = null;
  messwerteLaden();
}

/** Zwei eingetragene Zeitpunkte uebernehmen. */
function messSpanne() {
  const v = Date.parse($('messVon').value) / 1000;
  const b = Date.parse($('messBis').value) / 1000;
  if (!isFinite(v) || !isFinite(b)) return;
  if (b <= v) {
    // Umgekehrt eingetragen. Nicht kommentarlos tauschen und auch nicht stumm
    // verwerfen — die Felder zeigen wieder, was wirklich gilt.
    zeichneZeitleiste();
    return;
  }
  _messDauer = b - v;
  _messBis = b;
  // Reicht das Fenster bis in die Gegenwart, laeuft es weiter mit.
  _messFolgt = b >= Date.now() / 1000 - 60;
  _voreinstellungLoesen();
  messwerteLaden();
}

/** Keine der Voreinstellungen trifft mehr zu — dann soll auch keine leuchten. */
function _voreinstellungLoesen() {
  const w = $('messZeit');
  if (!w) return;
  const passt = [...w.querySelectorAll('.zw-knopf')].find(
    k => _messFolgt && !k.dataset.alles && Number(k.dataset.std) * 3600 === _messDauer);
  w.querySelectorAll('.zw-knopf').forEach(k => k.classList.toggle('an', k === passt));
}

function exportOeffnen() {
  const { von, bis } = _messFenster();
  popZeigen(`
    <div class="pop-titel">Daten holen</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.5;margin:0 0 16px">
      Der angezeigte Zeitraum als CSV — <b>ungekürzt</b>, also jeder einzelne
      Messwert und nicht die zusammengefassten Punkte des Graphen.
      Semikolon als Trenner, Komma als Dezimalzeichen: so öffnet eine deutsche
      Tabellenkalkulation die Datei ohne Nachfragen.</p>
    <div class="pop-tat">
      <button class="knopf stumm" onclick="popSchliessen()">Abbrechen</button>
      <a class="knopf" href="/api/verlauf/export?von=${von}&bis=${bis}"
         onclick="setTimeout(popSchliessen, 400)">Herunterladen</a>
    </div>`);
}
