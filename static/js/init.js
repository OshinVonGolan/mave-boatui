// ── Init ───────────────────────────────────────────────────────────────────

applyDisplayConfig();
_kopfhoeheFuehren();   // --header-h an der echten Kopfzeilenhoehe fuehren
loadPresets();

// Sofort aktuellen Zustand laden bevor WebSocket-Push eintrifft → kein Flackern
fetch('/api/status').then(r => r.ok ? r.json() : null).then(d => { if (d) handleData(d); }).catch(() => {});

connect();
fetchConnectivity();
setInterval(fetchConnectivity, 25000);
_wartungLoad();
refreshVersion();
setInterval(refreshVersion, 60000);   // Update-Status periodisch frisch halten
refreshChargerStatus();
setInterval(refreshChargerStatus, 300000);  // Badge alle 5 min aktualisieren

fetchWaterLevel();
setInterval(fetchWaterLevel, 600000);  // Wasserstand alle 10 min

// Heizung: eigener Poller, damit die Kachel auch ohne WebSocket-Daten lebt.
// 6 s reichen — die Heizung aendert sich nicht in Sekundenbruchteilen, und der
// Pi haelt den Zustand ohnehin vor.
hzPoller.start();
