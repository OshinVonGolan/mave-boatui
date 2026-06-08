// ── Init ───────────────────────────────────────────────────────────────────

loadPresets();
connect();
fetchConnectivity();
setInterval(fetchConnectivity, 25000);
_wartungLoad();
refreshVersion();
setInterval(refreshVersion, 60000);   // Update-Status periodisch frisch halten
refreshChargerStatus();
setInterval(refreshChargerStatus, 300000);  // Badge alle 5 min aktualisieren
