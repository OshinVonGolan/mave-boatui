// ── Init ───────────────────────────────────────────────────────────────────

applyDisplayConfig();
_kopfhoeheFuehren();   // --header-h an der echten Kopfzeilenhoehe fuehren
_leisteKompaktFuehren();  // Statusleiste schrumpft beim Scrollen an die Kopfzeile
_zeigerartMerken();       // Maus oder Finger? Haengt die Hover-Effekte daran
_sbHaltenBinden();        // Doppeltipp oeffnet die Detailseite eines Feldes
_vollbildFuehren();       // Vollbild nach einem Neuladen wieder herstellen
wandStart();              // Bildschirm-anlassen-Schalter und Nachtmodus
// Steht eine Ansicht in der Adresse, wird sie SOFORT geoeffnet — nicht erst,
// wenn der Ladeschirm faellt. Der kann bis zu acht Sekunden stehen, und so
// lange auf der Startseite zu sitzen, obwohl in der Adresse etwas anderes
// steht, ist genau der Zustand, den das hier beheben soll. Verdeckt ist die
// Ansicht ohnehin: der Ladeschirm liegt darueber, bis alles da ist.
_adresseBeimStart();
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

// Alle wiederkehrenden Abfragen ueber createPoller, nicht ueber ein blankes
// setInterval.
//
// Der Unterschied ist nicht Kosmetik: createPoller haelt an, sobald die Seite
// im Hintergrund liegt (_pollersPause), und nimmt danach von selbst wieder auf.
// Diese fuenf hier liefen frueher weiter, waehrend das Telefon in der Tasche
// steckte — Funkverkehr und Akku fuer Werte, die niemand ansieht. Der
// Mechanismus dafuer stand schon da; sie gingen nur daran vorbei.
const _pConn = fetchConnectivity();
const _connPoller    = createPoller(fetchConnectivity, 25000);
const _pWart = _wartungLoad();
// Der Grundriss: Raumnamen und -farben fuer Stauplan und Geraeteseite.
const _pGrundriss = grundrissLaden();
refreshVersion();
const _versionPoller = createPoller(refreshVersion, 60000);   // Update-Stand frisch halten
refreshChargerStatus();
const _chargerPoller = createPoller(refreshChargerStatus, 300000);  // Badge alle 5 min

// Wetter: erst die gepflegten Orte, dann die Vorhersage. Andersherum holte der
// erste Abruf die Vorgabe des Servers und der zweite gleich darauf den
// gewaehlten Ort — zwei Abrufe fuer eine Kachel, und der falsche zuerst.
_wxBinden();
const _pWetter = fetchWetterOrte().then(fetchWeather);

// Pegel: erst die gepflegten Stationen, dann der Stand — sonst holt der
// erste Abruf den Heimatpegel und der zweite gleich darauf den gewaehlten.
_wlBinden();
const _pWl = fetchPegelOrte().then(fetchWaterLevel);
const _wlPoller      = createPoller(fetchWaterLevel, 600000);  // Wasserstand alle 10 min

// fireNow=false: der erste Abruf steht direkt darueber schon. Sonst liefe jede
// dieser Abfragen beim Start doppelt.
for (const p of [_connPoller, _versionPoller, _chargerPoller, _wlPoller]) p.start(false);
if (typeof _wartungTagPoller !== 'undefined' && _wartungTagPoller) _wartungTagPoller.start(false);

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

// Die dritte Spalte heisst: braucht INTERNET.
//
// Auf die wird beim Start NICHT gewartet. Wetter und Wasserstand kommen von
// fremden Diensten; ohne Internet laufen sie in ihre eigene Frist, und der
// Ladeschirm haette so lange gestanden, obwohl alle Bordwerte laengst da sind
// (Eignermeldung). Sie tragen sich nach, wenn sie kommen — eine Kachel, die
// eine Sekunde spaeter Zahlen bekommt, ist kein Problem; ein Ladeschirm, der
// vor dem fertigen Boot steht, schon.
const _QUELLEN = [
  ['Zustand',    _pStatus,     false],
  ['Verbindung', _pConn,       false],
  ['Wartung',    _pWart,       false],
  ['Grundriss',  _pGrundriss,  false],
  ['Wasserstand', _pWl,        true],
  ['Wetter',     _pWetter,     true],
  ['Heizung',    typeof ladeHeizung === 'function' ? ladeHeizung(false) : null, false],
  ['Beleuchtung', typeof loadPresets === 'function' ? _presetsFertig : null, false],
];
/** Worauf der Start wirklich wartet. */
const _QUELLEN_BORD = _QUELLEN.filter(([, p, extern]) => p && !extern);

function _startFreigeben() {
  document.documentElement.classList.remove('startet');
}

// Im Ladeschirm steht, worauf noch gewartet wird. Das ist nicht Zierde: bleibt
// eine Quelle hängen, sieht man beim nächsten Mal sofort welche, statt zu
// raten.
let _offeneQuellen = _QUELLEN_BORD.map(([n]) => n);

// Der Text bleibt STILL, solange es normal laeuft. Erst wenn es ungewoehnlich
// lange dauert, wird er sichtbar — dann ist er keine Zierde, sondern die
// Antwort auf „woran haengt es". Im Normalfall sieht man die Marke und den
// Strich, sonst nichts (Eignerwunsch).
const _LADETEXT_AB_MS = 3000;
let _textZeigen = false;

function _ladeStandZeigen() {
  const feld = document.getElementById('ladeText');
  if (!feld) return;
  if (!_textZeigen || !_offeneQuellen.length) { feld.hidden = true; return; }
  feld.hidden = false;
  feld.textContent = 'Es fehlt noch: ' + _offeneQuellen.join(', ');
}
setTimeout(() => { _textZeigen = true; _ladeStandZeigen(); }, _LADETEXT_AB_MS);

for (const [name, versprechen] of _QUELLEN_BORD) {
  Promise.resolve(versprechen).finally(() => {
    _offeneQuellen = _offeneQuellen.filter(n => n !== name);
    _ladeStandZeigen();
  });
}

Promise.race([
  Promise.allSettled(_QUELLEN_BORD.map(([, p]) => p)),
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
