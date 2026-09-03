// ── Init ───────────────────────────────────────────────────────────────────

applyDisplayConfig();
_kopfhoeheFuehren();   // --header-h an der echten Kopfzeilenhoehe fuehren
_leisteKompaktFuehren();  // Statusleiste schrumpft beim Scrollen an die Kopfzeile
loadPresets();

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
const _START_DECKEL_MS = 2000;
function _startFreigeben() {
  document.documentElement.classList.remove('startet');
}
Promise.race([
  Promise.allSettled([_pStatus, _pConn, _pWart, _pWl,
                      typeof fetchWeather === 'function' ? fetchWeather() : null,
                      typeof ladeHeizung  === 'function' ? ladeHeizung(false) : null]),
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
