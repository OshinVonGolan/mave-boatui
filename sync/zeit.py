"""Zeitstempel eines Rechners ohne Uhr.

Der Pi Zero hat keine gepufferte Uhr. Nach einem Stromausfall laeuft er mit
irgendeiner Zeit los — meist der letzten bekannten oder 1970 — bis NTP greift.
Das hat im Frontend schon einmal Zeitfenster zerschossen (CLAUDE.md, Regel 3),
und beim Nachliefern an den Server wuerde es den Langzeitverlauf verschmutzen:
Eintraege, die waehrend des Hochlaufs entstanden sind, landen sonst irgendwo im
Jahr 1970 oder — schlimmer — plausibel falsch.

Die Loesung braucht drei Angaben je Paket statt einer:

    wand      was die Uhr des Pi sagt (kann falsch sein)
    mono      ein monotoner Zaehler seit Systemstart (kann NICHT falsch sein,
              laeuft aber bei jedem Neustart wieder bei ~0 los)
    gestellt  ob die Uhr zu diesem Zeitpunkt per NTP gestellt war

Damit laesst sich nachtraeglich zurueckrechnen: Sobald EIN Paket mit gestellter
Uhr eintrifft, ist der Zusammenhang zwischen mono und echter Zeit bekannt, und
alle Pakete derselben Laufzeit koennen darauf bezogen werden — auch die, die
vorher entstanden sind.
"""
from __future__ import annotations

# Vor diesem Zeitpunkt kann kein Eintrag dieser Anlage entstanden sein. Eine
# Wanduhr, die davor liegt, ist nie echt — das ist der Rueckfall des Pi ohne
# Uhr. (01.01.2024)
_FRUEHESTENS = 1704067200.0

# Ein monotoner Zaehler laeuft nach einem Neustart wieder bei Null los. Faellt
# er um mehr als das, war ein Neustart dazwischen und die alte Referenz gilt
# nicht mehr. Kleine Ruecksprunge gibt es auch ohne Neustart (unterschiedliche
# Threads, Rundung), deshalb eine Toleranz statt strenger Monotonie.
_NEUSTART_TOLERANZ_S = 2.0


class Uhrbuch:
    """Fuehrt Buch darueber, wie der monotone Zaehler zur echten Zeit steht.

    Eine Instanz gehoert zu EINEM Geraet. Der Server haelt sie, weil er die
    Pakete zusammensetzt; der Pi kann dieselbe Klasse benutzen, um seinen
    eigenen Verlauf zu ordnen.
    """

    def __init__(self):
        self._ref_wand: float | None = None   # echte Zeit der Referenz
        self._ref_mono: float | None = None   # Zaehlerstand dazu
        self._letzte_mono: float | None = None

    # ── Buchfuehrung ────────────────────────────────────────────────────────

    def merke(self, wand, mono, gestellt: bool) -> None:
        """Nimmt ein Paket zur Kenntnis und aktualisiert die Referenz."""
        if not _zahl(mono):
            return
        mono = float(mono)

        # Neustart erkennen: der Zaehler faellt zurueck. Die alte Referenz
        # gehoert zu einer anderen Laufzeit und wuerde ab hier falsch rechnen.
        if self._letzte_mono is not None and mono < self._letzte_mono - _NEUSTART_TOLERANZ_S:
            self._ref_wand = None
            self._ref_mono = None
        self._letzte_mono = mono

        if gestellt and _echte_wandzeit(wand):
            # Immer die JUENGSTE gestellte Uhr als Referenz: NTP korrigiert
            # nach, und die letzte Korrektur ist die genaueste.
            self._ref_wand = float(wand)
            self._ref_mono = mono

    def neustart_erkannt(self, mono) -> bool:
        """Ob dieser Zaehlerstand auf eine neue Laufzeit deutet."""
        if not _zahl(mono) or self._letzte_mono is None:
            return False
        return float(mono) < self._letzte_mono - _NEUSTART_TOLERANZ_S

    # ── Auswertung ──────────────────────────────────────────────────────────

    def aufloesen(self, wand, mono, gestellt: bool) -> float | None:
        """Echte Zeit eines Pakets, oder None wenn sie nicht zu ermitteln ist.

        None ist ein gueltiges Ergebnis und heisst: dieser Eintrag hat noch
        keine Zeitachse. Der Server parkt ihn, bis ein Paket mit gestellter Uhr
        kommt — dann laesst er sich nachtraeglich einordnen.
        """
        if gestellt and _echte_wandzeit(wand):
            return float(wand)
        if self._ref_wand is None or self._ref_mono is None or not _zahl(mono):
            return None
        # Rueckrechnung innerhalb derselben Laufzeit: die Referenz kennt den
        # Abstand zwischen Zaehler und echter Zeit.
        gerechnet = self._ref_wand - (self._ref_mono - float(mono))
        return gerechnet if _echte_wandzeit(gerechnet) else None

    @property
    def hat_referenz(self) -> bool:
        return self._ref_wand is not None


def stempel(wand, mono, gestellt: bool) -> dict:
    """Die drei Angaben als Feld fuer einen Umschlag."""
    return {'wand': round(float(wand), 3) if _zahl(wand) else None,
            'mono': round(float(mono), 3) if _zahl(mono) else None,
            'gestellt': bool(gestellt)}


def _zahl(wert) -> bool:
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)


def _echte_wandzeit(wert) -> bool:
    """Ob eine Wanduhrzeit ueberhaupt aus dieser Anlage stammen kann."""
    return _zahl(wert) and float(wert) >= _FRUEHESTENS
