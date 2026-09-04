// ── Hilfsfunktionen ────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);
const fmt  = (v, d=1) => v == null ? '--' : Number(v).toFixed(d);
const fmtV = v => v == null ? '--' : Number(v).toFixed(2);

// HTML-Escaping für alles, was aus Nutzereingaben oder Server-Daten in
// innerHTML landet. Escapt auch ' und ", damit ein Anführungszeichen im
// Freitext weder das Markup noch ein Attribut (onclick="…('x')") zerlegt.
function _esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function colorClass(v, greenMin, yellowMin) {
  if (v == null) return '';
  return v >= greenMin ? 'val-green' : v >= yellowMin ? 'val-yellow' : 'val-red';
}

function timeSince(s) {
  if (s == null) return '--';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h > 48) return `${Math.floor(h/24)} T`;
  if (h > 0)  return `${h} h`;
  return `${m} min`;
}

// Markiert den Topbar-Button der gerade offenen Seite mit blauem Rahmen.
// btnId=null entfernt die Markierung überall.
function _navActive(btnId) {
  document.querySelectorAll('.icon-btn.nav-active').forEach(b => b.classList.remove('nav-active'));
  if (btnId) $(btnId)?.classList.add('nav-active');
}

// ── Poller (sichtbarkeitsgesteuert) ────────────────────────────────────────
// Alle wiederkehrenden Abfragen laufen über createPoller(). Ist die Seite
// versteckt (App im Hintergrund, Display aus), werden die Timer angehalten —
// sonst fragt ein unsichtbares Tab den Pi weiter im Sekundentakt ab. Beim
// Sichtbarwerden aktualisiert jeder Poller sofort einmal und läuft dann weiter.
//
//   const p = createPoller(fetchNetwork, 5000, { shouldRun: () => overlayOffen });
//   p.start();   // sofort abfragen + Intervall starten
//   p.stop();    // Intervall beenden
//
// Optionen:
//   shouldRun  zusätzliche Bedingung, vor jedem Start/Fortsetzen geprüft
//              (z. B. "Overlay ist offen") — schützt davor, dass ein Poller
//              beim Sichtbarwerden für eine längst geschlossene Ansicht wieder
//              anläuft.
//   onTimer    meldet die Timer-ID nach außen. Nötig für Alt-Code, der die
//              Intervalle direkt kennt (_closeAllOverlays in charts.js ruft
//              clearInterval(netTimer) auf).

const _pollers = [];

function createPoller(fn, intervalMs, opts = {}) {
  const p = {
    fn,
    intervalMs,
    shouldRun: opts.shouldRun || null,
    onTimer:   opts.onTimer   || null,
    wanted: false,     // soll laufen (Ansicht offen)
    _t: null,
    _arm() {
      if (p._t) return;
      p._t = setInterval(p.fn, p.intervalMs);
      if (p.onTimer) p.onTimer(p._t);
    },
    _disarm() {
      if (p._t) clearInterval(p._t);
      p._t = null;
      if (p.onTimer) p.onTimer(null);
    },
    _mayRun() {
      return p.wanted && !document.hidden && (!p.shouldRun || p.shouldRun());
    },
    // fireNow=false startet nur das Intervall, ohne sofort abzufragen
    start(fireNow = true) {
      p.wanted = true;
      p._disarm();                 // evtl. von außen gestoppten Timer sauber neu setzen
      if (!p._mayRun()) return;
      if (fireNow) p.fn();
      p._arm();
    },
    stop() {
      p.wanted = false;
      p._disarm();
    },
  };
  _pollers.push(p);
  return p;
}

// Alle Poller anhalten bzw. fortsetzen. Kann auch beim Abschalten des
// Displays aufgerufen werden (display.js), nicht nur bei visibilitychange.
function _pollersPause() {
  _pollers.forEach(p => p._disarm());
}
function _pollersResume() {
  _pollers.forEach(p => {
    if (!p._mayRun()) return;
    p._disarm();
    p.fn();          // beim Sichtbarwerden einmal sofort aktualisieren
    p._arm();
  });
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) _pollersPause();
  else                 _pollersResume();
});

// ── JS-Fehlermelder (gedrosselt) ───────────────────────────────────────────
// Der frühe Melder in index.html fängt Fehler ab, bevor das Bundle geladen
// ist. Ab hier übernimmt _reportJsError: jede Meldung wird nur EINMAL zum Pi
// geschickt, insgesamt höchstens _JSERR_MAX verschiedene. Ungedrosselt schickt
// ein Fehler in einer Schleife (z. B. im Render-Pfad) sonst hunderte POSTs pro
// Minute und das rote Banner wächst endlos weiter.

const _JSERR_MAX = 10;         // so viele verschiedene Meldungen, dann Schluss
const _jsErrSeen = new Map();  // Schlüssel -> wie oft aufgetreten
let   _jsErrCapNoted = false;

function _jsErrBanner(text) {
  let b = document.getElementById('_jserrBanner');
  if (!b) {
    if (!document.body) return;
    b = document.createElement('div');
    b.id = '_jserrBanner';
    b.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#ef4444;'
                    + 'color:#fff;font-size:11px;padding:6px 10px;z-index:9999;'
                    + 'white-space:pre-wrap;max-height:120px;overflow:auto;';
    document.body.appendChild(b);
  }
  b.textContent += text + '\n';
}

function _reportJsError(msg, src, line, col, err) {
  const key = `${msg}@${src || ''}:${line || 0}`;
  const seen = _jsErrSeen.get(key);
  if (seen != null) {            // dieselbe Meldung: nur mitzählen
    _jsErrSeen.set(key, seen + 1);
    return;
  }
  if (_jsErrSeen.size >= _JSERR_MAX) {
    if (!_jsErrCapNoted) {
      _jsErrCapNoted = true;
      _jsErrBanner('[JS-FEHLER] weitere Meldungen werden unterdrückt');
    }
    return;
  }
  _jsErrSeen.set(key, 1);
  try {
    fetch('/api/jserror', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        msg: String(msg), src: src || '', line: line || 0, col: col || 0,
        stack: err?.stack, ts: new Date().toISOString(),
      }),
    }).catch(() => {});
  } catch(_) {}
  _jsErrBanner(`[JS-FEHLER] ${msg} @ ${src || '?'}:${line || 0}`);
}

// Den frühen Melder aus index.html ablösen …
window.onerror = _reportJsError;
// … und nachziehen, was vor dem Laden des Bundles aufgelaufen ist.
if (Array.isArray(window._jsErrEarly)) {
  const _early = window._jsErrEarly;
  window._jsErrEarly = [];
  _early.forEach(a => _reportJsError(a[0], a[1], a[2], a[3], a[4]));
}

// ── Config (geladen aus /api/presets) ─────────────────────────────────────

let tanksConfig    = { tank1: { name: 'Tank 1', capacity_l: 200 }, tank2: { name: 'Tank 2', capacity_l: 120 } };
let devicesConfig  = {};
let batteriesConfig = { service_instance: 0, starter_instance: 1, primary_source: 'shunt' };
let wartungConfig  = { due_soon_days: 7 };
let state = { lights: { channels: Array(9).fill(0) } };

// ── Clock ──────────────────────────────────────────────────────────────────

function updateClock() {
  const t = new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  const el = $('topbarClock'); if (el) el.textContent = t;
  const k = $('ktpClock');     if (k)  k.textContent = t;   // Kiosk Slide-down
}
const _clockPoller = createPoller(updateClock, 10000);
_clockPoller.start();

// ── Burger-Menü (Mobile) ───────────────────────────────────────────────────

// ── Das Menü ───────────────────────────────────────────────────────────────
// EIN Klappmenü, nicht zwei. Vorher gab es den Burger und daneben ein eigenes
// Kontomenü — beide konnten gleichzeitig offen sein und lagen dann übereinander.
//
// Das Konto ist jetzt eine zweite ANSICHT desselben Menüs: oben steht in einer
// Zeile, wer angemeldet ist, darunter führt ein Knopf hinein. Dadurch bleibt es
// bei einem Menü, und der Weg zurück ist ein Pfeil statt eines zweiten Fensters.

let _burgerAnsicht = 'haupt';

const _BM_SVG = {
  konto:    '<circle cx="12" cy="8" r="3.4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
  zurueck:  '<path d="M15 18l-6-6 6-6"/>',
  internet: '<path d="M1.5 9a14 14 0 0 1 21 0"/><path d="M5 12.5a10 10 0 0 1 14 0"/>'
            + '<path d="M8.5 16a6 6 0 0 1 7 0"/><circle cx="12" cy="20" r="1" fill="currentColor" stroke="none"/>',
  wartung:  '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94'
            + 'l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  stauplan: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
  aufgaben: '<path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>'
            + '<rect x="9" y="3" width="6" height="4" rx="1"/><path d="M9 12h6M9 16h4"/>',
  geraete:  '<path d="M7 7h10v10H7z"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  ordnen:   '<path d="M4 6h6v6H4zM14 6h6v6h-6zM4 16h6v4H4zM14 16h6v4h-6z"/>',
  einst:    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83'
            + 'l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4'
            + 'a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3'
            + 'a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06'
            + 'A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33'
            + 'l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09'
            + 'a1.65 1.65 0 0 0-1.51 1z"/>',
  vollbild: '<path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  logbuch:  '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'
            + '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  schluss:  '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
};

const _bmIcon = d => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"`
  + ` stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const _bmEsc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toggleBurger(e) {
  e?.stopPropagation();
  const m = $('burgerMenu');
  if (!m) return;
  const oeffnet = m.classList.contains('hidden');
  if (oeffnet) {
    // Nur EIN Menü darf offen stehen. Genau das war der Fehler: Burger und
    // Kontomenü klappten unabhängig auf und lagen übereinander.
    if (typeof closeQuelle === 'function') closeQuelle();
    _burgerAnsicht = 'haupt';
    burgerBauen();
  }
  m.classList.toggle('hidden');
}

function closeBurger() {
  $('burgerMenu')?.classList.add('hidden');
  _burgerAnsicht = 'haupt';
}

// Beide MÜSSEN den Klick anhalten. Sie bauen das Menü neu, und danach steckt
// der angeklickte Knopf nicht mehr im Dokument — der Wächter für Klicks
// daneben hielt das für "außerhalb getippt" und machte das Menü sofort wieder
// zu. Man kam so nie in die Kontoansicht.
function burgerKontoAnsicht(e) { e?.stopPropagation(); _burgerAnsicht = 'konto'; burgerBauen(); }
function burgerZurueck(e)      { e?.stopPropagation(); _burgerAnsicht = 'haupt'; burgerBauen(); }

function burgerBauen() {
  const m = $('burgerMenu');
  if (!m) return;
  m.innerHTML = _burgerAnsicht === 'konto' ? _burgerKonto() : _burgerHaupt();
}

function _burgerHaupt() {
  const k = (typeof _zugangStand !== 'undefined' && _zugangStand) ? _zugangStand.konto : null;
  const zeile = k
    ? `<div class="bm-wer"><b>${_bmEsc(k.anzeigename || k.name)}</b>`
      + `<span>${_bmEsc(k.rolle_name || '')}</span></div>`
    : '<div class="bm-wer bm-wer-leer">Nicht angemeldet</div>';
  const kontoKnopf = k
    ? `<button class="burger-item bm-konto" onclick="burgerKontoAnsicht(event)">
         ${_bmIcon(_BM_SVG.konto)}Konto
         <span class="bm-pfeil">${_bmIcon('<path d="M9 6l6 6-6 6"/>')}</span>
       </button>`
    : `<button class="burger-item bm-konto" onclick="closeBurger();anmeldungZeigen('')">
         ${_bmIcon(_BM_SVG.konto)}Anmelden</button>`;

  const eintrag = (svg, text, ruf, extra = '') =>
    `<button class="burger-item" onclick="closeBurger();${ruf}">${_bmIcon(svg)}${text}${extra}</button>`;

  // Die Alarme stehen bewusst NICHT hier: sie haben ihren Platz in der
  // Kopfzeile und wären hier ein zweiter Weg zur selben Sache.
  return zeile + kontoKnopf
    + '<div class="burger-trenner"></div>'
    + eintrag(_BM_SVG.internet, 'Internet', 'openConnectivity()')
    + eintrag(_BM_SVG.wartung, 'Wartungsplan', 'openWartung()',
              '<span id="bmWartungPunkt" class="bm-punkt hidden"></span>')
    + eintrag(_BM_SVG.stauplan, 'Stauplan', 'openStauplan()')
    + eintrag(_BM_SVG.aufgaben, 'Boot-Aufgaben', 'openMonday()')
    + eintrag(_BM_SVG.geraete, 'Geräte', 'openGeraete()')
    + '<div class="burger-trenner"></div>'
    + eintrag(_BM_SVG.ordnen, 'Kacheln anordnen', 'kachelnOrdnenAn(true)')
    + eintrag(_BM_SVG.vollbild,
              (typeof _vollbildAktiv === 'function' && _vollbildAktiv()) ? 'Vollbild verlassen' : 'Vollbild',
              'vollbildUmschalten()')
    + eintrag(_BM_SVG.einst, 'Einstellungen', 'openSettings()');
}

function _burgerKonto() {
  const k = (typeof _zugangStand !== 'undefined' && _zugangStand) ? _zugangStand.konto : null;
  if (!k) { _burgerAnsicht = 'haupt'; return _burgerHaupt(); }
  const handlungen = k.handlungen || [];
  const zeigen = k.anzeigename || k.name || '';
  const darunter = (k.name && k.name !== zeigen) ? 'meldet sich an als ' + k.name : '';
  const darfLogbuch = (k.oberflaechen || []).includes('diagnose');
  return `<button class="burger-item bm-zurueck" onclick="burgerZurueck(event)">`
    + `${_bmIcon(_BM_SVG.zurueck)}Zurück</button>`
    + '<div class="burger-trenner"></div>'
    + `<div class="bm-konto-kopf">${_bmEsc(zeigen)}</div>`
    + (darunter ? `<div class="bm-konto-neben">${_bmEsc(darunter)}</div>` : '')
    + `<div class="bm-konto-rolle">${_bmEsc(k.rolle_name || '')}</div>`
    + `<div class="bm-konto-darf">${_bmEsc(handlungen.join(' · '))}</div>`
    + '<div class="burger-trenner"></div>'
    + (darfLogbuch
       ? `<a class="burger-item" href="${typeof _logbuchAdresse === 'function' ? _logbuchAdresse() : '/diagnose'}">`
         + `${_bmIcon(_BM_SVG.logbuch)}Zum Logbuch</a>` : '')
    + `<button class="burger-item" onclick="closeBurger();passwortAendernOeffnen()">`
    + `${_bmIcon('<rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>')}`
    + 'Passwort ändern</button>'
    + `<button class="burger-item bm-warn" onclick="closeBurger();abmelden()">`
    + `${_bmIcon(_BM_SVG.schluss)}Abmelden</button>`;
}
// Außerhalb klicken schließt das Menü
document.addEventListener('click', e => {
  const wrap = $('burgerWrap');
  if (!wrap) return;
  // Ein Ziel, das nicht mehr im Dokument haengt, wurde gerade weggerendert —
  // das ist kein Klick daneben. Ohne diese Zeile schliesst sich das Menue bei
  // jedem Eintrag, der es neu aufbaut.
  if (!e.target?.isConnected) return;
  if (!wrap.contains(e.target)) closeBurger();
});

// ── Kopfzeilenhoehe ────────────────────────────────────────────────────────
// --header-h war eine feste Zahl (57 px, schmal 51 px). Die Kopfzeile liegt
// aber position:fixed, und ALLES darunter richtet sich nach dieser Konstante:
// die Statusleiste ueber ihren margin-top und jede Detailseite ueber
// .overlay { top: var(--header-h) }. Sobald die Kopfzeile umbrach, stimmte die
// Zahl nicht mehr — am Geraet gemessen 96 px statt 57 bei 640 px Fensterbreite,
// wodurch 23 px der Statusleiste hinter der Kopfzeile verschwanden.
//
// Statt die Zahl zu pflegen, wird sie jetzt gemessen. Damit kann kein
// kuenftiger Umbau der Kopfzeile das Layout mehr aus dem Tritt bringen.
// Im Kiosk ist die Kopfzeile ausgeblendet — dann ist die Hoehe 0, und genau
// das ist auch richtig.
/**
 * Versionsnummer nur zeigen, wenn sie neben den Schriftzug passt.
 *
 * .logo enthaelt "MAVE", den Untertitel und die Version. Ohne nowrap rutschte
 * die Version auf schmalen Displays unter den Schriftzug und machte die
 * Kopfzeile eine Zeile hoeher. Gemessen statt an einer Breite geraten: erst
 * einblenden, dann pruefen, ob der Inhalt noch in die Rasterspalte passt.
 */
function _kopfLogoPruefen() {
  const logo = document.querySelector('.logo');
  if (!logo) return;
  logo.classList.remove('ohne-version');
  // scrollWidth > clientWidth heisst: der Inhalt ist breiter als die Spalte.
  // +1 px Toleranz gegen Rundung bei gebrochenen Zoomstufen.
  if (logo.scrollWidth > logo.clientWidth + 1) logo.classList.add('ohne-version');
}

/**
 * Schaltet die Statusleiste beim Scrollen auf die kompakte Fassung.
 *
 * Beobachtet wird ein 1-Element-Fuehler ueber der Leiste, NICHT das Scrollen
 * selbst: ein scroll-Handler laeuft bei jedem Bild, der IntersectionObserver
 * nur bei der eigentlichen Zustandsaenderung. Das faellt auf dem anzeigenden
 * Geraet an — Tablet, Telefon, Laptop —, nicht auf dem Pi: der liefert die
 * Seite nur aus und stellt sie nicht dar.
 *
 * Der Fuehler steht VOR dem Halter im Fluss und wandert deshalb nicht mit,
 * wenn die Leiste schrumpft — sonst wuerde das Umschalten den Fuehler wieder
 * ins Bild schieben und die Leiste faenge an zu flackern.
 */
/**
 * Zwischen normaler und kompakter Leiste umschalten.
 *
 * Hier wird nur noch eine Klasse gesetzt. Alles Sichtbare an der Leiste —
 * Groessen, Abstaende, Schriften, Randlinien, Breite — haengt stufenlos am
 * Scrollstand (--leiste-eng, siehe style.css) und laeuft dem Finger direkt
 * hinterher.
 *
 * Vorher schaltete diese Stelle Schriften, Randlinien und Breite auf einen
 * Schlag um; die Leiste sprang dabei am Ende der Bewegung 9 px flacher und
 * 72 px breiter. Der Sprung wurde mit einer gemessenen Gleitbewegung
 * (FLIP: alte Plaetze merken, umschalten, per transform zuruecksetzen,
 * hinlaufen lassen) ueberdeckt. Zwei Nachteile hatte das: die Bewegung lief
 * dem Scrollen um ihre eigene Dauer hinterher, und weil die Klasse an einem
 * IntersectionObserver haengt, kam sie beim schnellen Scrollen zusaetzlich
 * verspaetet. Beides faellt weg, wenn nichts mehr springt.
 *
 * Die Klasse bleibt trotzdem: in Browsern ohne scroll-gesteuerte Animation
 * setzt sie --leiste-eng auf 1, und der Uebergang laeuft dort ueber eine
 * Transition.
 */
function _leisteUmschalten(kompakt) {
  document.body.classList.toggle('leiste-kompakt', kompakt);
}

// Ab dieser Breite bleibt die Statusleiste in voller Groesse stehen.
// Das Zusammenschieben ist eine Massnahme gegen Platzmangel: auf einem Handy
// frisst eine grosse Leiste beim Scrollen den halben Bildschirm. Auf einem
// Monitor gibt es dieses Problem nicht — dort ist die schrumpfende Leiste nur
// Unruhe, und die Werte springen beim Scrollen in andere Groessen.
const _LEISTE_ENG_BIS = 1024;

function _leisteKompaktFuehren() {
  const fuehler = document.querySelector('.statusbar-fuehler');
  if (!fuehler) return;
  if (typeof IntersectionObserver !== 'function') return;   // dann bleibt sie gross

  let beobachtet = false;
  const beobachter = new IntersectionObserver(([eintrag]) => {
    _leisteUmschalten(!eintrag.isIntersecting);
  }, { threshold: 0 });

  const pruefen = () => {
    const eng = window.innerWidth < _LEISTE_ENG_BIS;
    if (eng && !beobachtet) {
      beobachter.observe(fuehler);
      beobachtet = true;
    } else if (!eng && beobachtet) {
      beobachter.unobserve(fuehler);
      beobachtet = false;
      // Den Beobachter abzuschalten allein genuegt nicht: war die Leiste im
      // Moment des Umschaltens gerade eng, bliebe sie es fuer immer.
      _leisteUmschalten(false);
    }
  };
  pruefen();
  window.addEventListener('resize', pruefen);
  window.addEventListener('orientationchange', pruefen);
}

function _kopfhoeheFuehren() {
  const kopf = document.querySelector('header');
  if (!kopf) return;
  let letzte = -1;
  const setzen = () => {
    _kopfLogoPruefen();                    // erst entscheiden, dann messen
    const h = Math.round(kopf.getBoundingClientRect().height);
    if (h === letzte) return;              // nichts tun, wenn sich nichts aendert
    letzte = h;
    // --header-basis, NICHT --header-h: die Kopfzeile ist nur der eine
    // Summand. Liegt darunter noch das Kopie-Banner, rechnet CSS es
    // dazu (--header-h = basis + kopie). Wuerde hier --header-h gesetzt,
    // waere das Banner in jeder Layout-Rechnung verschwunden und
    // ueberdeckte den Seitenanfang.
    document.documentElement.style.setProperty('--header-basis', h + 'px');
  };
  setzen();
  if (typeof ResizeObserver === 'function') {
    new ResizeObserver(setzen).observe(kopf);
  } else {
    window.addEventListener('resize', setzen);       // Rueckfall fuer alte Browser
    window.addEventListener('orientationchange', setzen);
  }
  // Schriften kommen spaeter an und aendern die Hoehe noch einmal.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(setzen).catch(() => {});
}
