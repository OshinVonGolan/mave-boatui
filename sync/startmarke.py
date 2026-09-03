"""Woran der Pi erkennt, dass er neu gestartet ist — und ob es sauber war.

Der Server sieht nur, dass eine Verbindung abgerissen und wiedergekommen ist.
Er kann daraus NICHT schliessen, was an Bord passiert ist: ein Funkloch sieht
genauso aus wie ein Stromausfall. Den Unterschied kennt nur der Pi, und nur
wenn er Buch fuehrt.

Drei Quellen, jede beantwortet eine andere Frage:

  boot_id      Hat der RECHNER neu gestartet? Der Kernel wuerfelt sie bei jedem
               Systemstart neu (/proc/sys/kernel/random/boot_id). Bleibt sie
               gleich, war es nur der Dienst.
  Laufzeit     Wie lange laeuft der Rechner schon (/proc/uptime)? Damit laesst
               sich der Startzeitpunkt zurueckrechnen, sobald die Uhr steht.
  Stoppmarke   War das Ende geordnet? Beim Start wird eine Marke geschrieben,
               beim geordneten Stoppen wieder entfernt. Ist sie beim naechsten
               Start noch da, wurde der Dienst NICHT geordnet beendet —
               Stromausfall, Kernel-Panik, harter Reset.

Das ist bewusst grob: eine genaue Absturzursache liefert kein Verfahren, das
ohne fremde Dienste auskommt. Aber "der Strom war weg" von "ich wurde
neugestartet" zu unterscheiden, reicht fuer die Frage des Eigners.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Wie das Ende der letzten Laufzeit zu deuten ist.
SAUBER    = 'sauber'       # Dienst wurde geordnet beendet (Update, Neustart per Befehl)
ABBRUCH   = 'abbruch'      # Marke lag noch da: Stromausfall, Panik, harter Reset
ERSTSTART = 'erststart'    # keine Marke vorhanden — erste Inbetriebnahme
UNBEKANNT = 'unbekannt'    # Marke unlesbar


def _boot_id() -> str:
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return ''


def _laufzeit_s() -> float | None:
    """Laufzeit des RECHNERS, nicht des Dienstes."""
    try:
        return float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


class Startmarke:
    """Fuehrt die Marke und beantwortet, was beim letzten Mal geschah.

    Die Datei gehoert zum Laufzeitzustand und darf NICHT ins Repo (wie die
    anderen 14 Dateien, siehe .gitignore).
    """

    def __init__(self, pfad: str | Path):
        self._pfad = Path(pfad)
        self._vorher: dict = {}
        self._jetzt: dict = {}

    def start(self, *, wand=None, gestellt: bool = False) -> dict:
        """Beim Hochlauf aufrufen. Liest die alte Marke und schreibt die neue.

        Gibt den Befund zurueck: was beim letzten Mal passiert ist.
        """
        self._vorher = self._lesen()
        self._jetzt = {
            'boot_id':   _boot_id(),
            'laufzeit_s': _laufzeit_s(),
            'start_wand': float(wand) if (gestellt and wand) else None,
            'gestellt':  bool(gestellt),
            'gestartet': time.time(),
        }
        self._schreiben(self._jetzt)
        return self.befund()

    def geordnet_beenden(self) -> None:
        """Beim geordneten Stoppen aufrufen (SIGTERM-Handler).

        Danach ist die Marke weg, und der naechste Start weiss: das Ende war
        gewollt. Bleibt sie liegen, war es ein Abbruch — genau diese
        Unterscheidung ist der Zweck der Datei.
        """
        try:
            self._pfad.unlink()
        except OSError:
            pass

    def befund(self) -> dict:
        """Was beim letzten Mal geschah, in einer Form fuer das hallo-Paket."""
        if not self._vorher:
            ende = ERSTSTART
        elif self._vorher.get('_unlesbar'):
            ende = UNBEKANNT
        else:
            # Die Marke lag noch da: der Dienst wurde nicht geordnet beendet.
            ende = ABBRUCH
        neuer_rechner = bool(self._jetzt.get('boot_id')) and \
            self._vorher.get('boot_id') != self._jetzt.get('boot_id')
        return {
            'letztes_ende': ende,
            'rechner_neu':  neuer_rechner,
            'nur_dienst':   (ende != ERSTSTART) and not neuer_rechner,
            'laufzeit_s':   self._jetzt.get('laufzeit_s'),
            'boot_id':      self._jetzt.get('boot_id'),
            'vorher_boot_id': self._vorher.get('boot_id') or None,
            # Wann der Rechner hochgelaufen ist, sofern die Uhr steht. Erst das
            # macht die Luecke im Verlauf erklaerbar.
            'rechner_start_wand': _rechner_start(self._jetzt),
        }

    # ── Datei ───────────────────────────────────────────────────────────────

    def _lesen(self) -> dict:
        try:
            roh = self._pfad.read_text(encoding='utf-8')
        except OSError:
            return {}
        try:
            daten = json.loads(roh)
            return daten if isinstance(daten, dict) else {'_unlesbar': True}
        except ValueError:
            return {'_unlesbar': True}

    def _schreiben(self, daten: dict) -> None:
        # Atomar, wie jsonio: ein halb geschriebener Zustand wuerde beim
        # naechsten Start als unlesbar gelten und einen Abbruch verschleiern.
        tmp = self._pfad.with_suffix('.tmp')
        try:
            self._pfad.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(daten, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._pfad)
        except OSError:
            pass


def _rechner_start(jetzt: dict) -> float | None:
    """Wanduhrzeit des Systemstarts, wenn die Uhr steht."""
    wand, lauf = jetzt.get('start_wand'), jetzt.get('laufzeit_s')
    if wand is None or lauf is None:
        return None
    return round(float(wand) - float(lauf), 1)
