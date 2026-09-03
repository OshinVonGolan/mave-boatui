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

function toggleBurger(e) {
  e?.stopPropagation();
  $('burgerMenu')?.classList.toggle('hidden');
}
function closeBurger() {
  $('burgerMenu')?.classList.add('hidden');
}
// Außerhalb klicken schließt das Menü
document.addEventListener('click', e => {
  const wrap = $('burgerWrap');
  if (wrap && !wrap.contains(e.target)) closeBurger();
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

function _leisteKompaktFuehren() {
  const fuehler = document.querySelector('.statusbar-fuehler');
  if (!fuehler) return;
  if (typeof IntersectionObserver !== 'function') return;   // dann bleibt sie gross
  new IntersectionObserver(([eintrag]) => {
    _leisteUmschalten(!eintrag.isIntersecting);
  }, { threshold: 0 }).observe(fuehler);
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
    document.documentElement.style.setProperty('--header-h', h + 'px');
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
