"""SignalKSource: liest das normalisierte Modell aus einem Signal-K-Server.

Liefert `vessels.self` als Snapshot. Damit erbt der ganze Stack die
Selbst­beschreibung von Signal K (dynamische Geräte/Tanks ohne Code-Änderung).
REST genügt für den Snapshot; für Live-Updates kommt später der Delta-WebSocket
(`signalk-ws`) dazu.
"""
import json
import urllib.request

from .base import DataSource


class SignalKSource(DataSource):
    def __init__(self, base_url: str = "http://localhost:3000"):
        self._base = base_url.rstrip("/")

    def snapshot(self) -> dict:
        url = f"{self._base}/signalk/v1/api/vessels/self"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode())


if __name__ == "__main__":
    # Schneller Selbsttest gegen einen laufenden SK-Server.
    src = SignalKSource()
    print("Tanks (dynamisch aus Signal K):")
    for t in src.tanks():
        pct = "--" if t["level"] is None else f"{round(t['level'] * 100)}%"
        print(f"  {t['type']}/{t['instance']}: {pct}")
