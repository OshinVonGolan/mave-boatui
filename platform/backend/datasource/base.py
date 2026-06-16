"""Quellen-unabhängige Datenschicht (Konzept §2, Prinzip 3: Quellen-Abstraktion).

Die Business-/UI-Schicht spricht nur gegen dieses Interface, nie direkt gegen ein
konkretes Gerät oder einen Bus. Heute liefert `SignalKSource` die Daten aus
Signal K; eine andere Quelle (Mock, künftiges System) ließe sich ohne Änderung an
den Konsumenten einsetzen.
"""
from abc import ABC, abstractmethod


class DataSource(ABC):
    @abstractmethod
    def snapshot(self) -> dict:
        """Aktuelles normalisiertes Modell des eigenen Schiffs (Signal-K `vessels.self`)."""

    def tanks(self) -> list[dict]:
        """Alle Tanks – dynamische Anzahl/Typen aus dem Modell, nicht hartcodiert.
        Rückgabe: [{type, instance, level(0..1)}], Default leer."""
        model = self.snapshot()
        out: list[dict] = []
        for ttype, insts in (model.get("tanks") or {}).items():
            if not isinstance(insts, dict):
                continue
            for inst, node in insts.items():
                level = (((node or {}).get("currentLevel") or {}).get("value"))
                out.append({"type": ttype, "instance": inst, "level": level})
        return out
