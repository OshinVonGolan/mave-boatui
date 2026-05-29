// ── Init ───────────────────────────────────────────────────────────────────

loadPresets();
connect();
fetchConnectivity();
setInterval(fetchConnectivity, 25000);
_wartungLoad();
