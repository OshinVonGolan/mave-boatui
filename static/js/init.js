// ── Init ───────────────────────────────────────────────────────────────────

applyDisplayConfig();
_kopfhoeheFuehren();   // --header-h an der echten Kopfzeilenhoehe fuehren
_leisteKompaktFuehren();  // Statusleiste schrumpft beim Scrollen an die Kopfzeile
const _presetsFertig = Promise.resolve(loadPresets());

// Service Worker anmelden. Erst dadurch bietet Chrome die Anwendung als
// installierbar an (eigenes Fenster statt blosser Verknuepfung im Browser).
// Voraussetzung ist ein sicherer Kontext: ueber den Server (https) ist das
// gegeben, direkt am Pi per http nur ueber localhost — dort passiert schlicht
// nichts, was richtig ist und keine Fehlermeldung wert.
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .catch(e => console.warn('Service Worker nicht angemeldet:', e && e.message));
  });
}

// Zuerst klaeren, ob wir ueberhaupt hereinduerfen. Laeuft parallel zum Rest:
// wer angemeldet ist, soll nicht auf diese Antwort warten muessen, und wer es
// nicht ist, bekommt die Maske ueber die schon geladene Oberflaeche gelegt.
const _pZugang = (typeof zugangPruefen === 'function') ? zugangPruefen() : Promise.resolve();

// Sofort aktuellen Zustand laden bevor WebSocket-Push eintrifft → kein Flackern
const _pStatus = fetch('/api/status').then(r => r.ok ? r.json() : null)
  .then(d => { if (d) handleData(d); }).catch(() => {});

connect();
const _pConn = fetchConnectivity();
setInterval(fetchConnectivity, 25000);
const _pWart = _wartungLoad();
refreshVersion();
setInterval(refreshVersion, 60000);   // Update-Status periodisch frisch halten
refreshChargerStatus();
setInterval(refreshChargerStatus, 300000);  // Badge alle 5 min aktualisieren

const _pWl = fetchWaterLevel();
setInterval(fetchWaterLevel, 600000);  // Wasserstand alle 10 min

/**
 * Gemeinsamer Start.
 *
 * Die Kacheln haengen an fuenf verschiedenen Quellen mit sehr verschiedenen
 * Antwortzeiten — beim Neuladen bauten sie sich deshalb einzeln auf. Hier
 * wird EINMAL gewartet, bis alle da sind, und dann alles zusammen gezeigt.
 *
 * allSettled statt all: eine Quelle, die nicht antwortet (Wetter ohne
 * Internet, Heizung offline), darf die Seite nicht aufhalten. Zusaetzlich
 * ein Deckel — nach 2 Sekunden wird gezeigt, was da ist. Die harte Notbremse
 * in index.html (4 s) bleibt als zweite Ebene bestehen, falls dieses Bundle
 * gar nicht erst laeuft.
 */
// Acht Sekunden statt zwei. Der alte Deckel stammt aus der Zeit, als der Start
// eine LEERE Seite zeigte — da war jede Sekunde Wartezeit eine Sekunde vor dem
// Nichts, und Freigeben war das kleinere Übel. Mit dem Ladeschirm ist es
// umgekehrt: man sieht, dass gearbeitet wird, und eine halb gefüllte Seite
// wäre die schlechtere Antwort.
//
// Wichtig ist das geworden, seit die Oberfläche auch über den Server läuft:
// dort wird die Heizung ans Boot durchgereicht und braucht ein Vielfaches der
// Zeit. Bei zwei Sekunden erschien die Seite regelmäßig mit grauer
// Heizungskachel — was aussah, als fehlten die Rechte.
const _START_DECKEL_MS = 8000;

const _QUELLEN = [
  ['Zustand',    _pStatus],
  ['Verbindung', _pConn],
  ['Wartung',    _pWart],
  ['Wasserstand', _pWl],
  ['Wetter',     typeof fetchWeather === 'function' ? fetchWeather() : null],
  ['Heizung',    typeof ladeHeizung === 'function' ? ladeHeizung(false) : null],
  ['Beleuchtung', typeof loadPresets === 'function' ? _presetsFertig : null],
];

function _startFreigeben() {
  document.documentElement.classList.remove('startet');
}

// Im Ladeschirm steht, worauf noch gewartet wird. Das ist nicht Zierde: bleibt
// eine Quelle hängen, sieht man beim nächsten Mal sofort welche, statt zu
// raten.
let _offeneQuellen = _QUELLEN.filter(([, p]) => p).map(([n]) => n);
function _ladeStandZeigen() {
  const feld = document.querySelector('.lade-text');
  if (!feld) return;
  feld.textContent = _offeneQuellen.length
    ? 'Es fehlen noch: ' + _offeneQuellen.join(', ')
    : 'Fertig';
}
_ladeStandZeigen();
for (const [name, versprechen] of _QUELLEN) {
  if (!versprechen) continue;
  Promise.resolve(versprechen).finally(() => {
    _offeneQuellen = _offeneQuellen.filter(n => n !== name);
    _ladeStandZeigen();
  });
}

Promise.race([
  Promise.allSettled(_QUELLEN.map(([, p]) => p)),
  new Promise(r => setTimeout(r, _START_DECKEL_MS)),
]).then(() => requestAnimationFrame(_startFreigeben));

// Heizung: eigener Poller, damit die Kachel auch ohne WebSocket-Daten lebt.
// 6 s reichen — die Heizung aendert sich nicht in Sekundenbruchteilen, und der
// Pi haelt den Zustand ohnehin vor.
hzPoller.start();

// Hintergrundgraphen der Statusleiste. Alle zwei Minuten genuegt: die Reihen
// sind Minutenmittel ueber 24 Stunden, und der Server haelt die Berechnung
// ohnehin eine Minute lang fest.
const _sparkPoller = createPoller(ladeSparklines, 120000);
_sparkPoller.start();
