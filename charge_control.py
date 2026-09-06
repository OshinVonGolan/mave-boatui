"""Intelligente Ladesteuerung — unterstützt IP43, MPPT und Orion XS."""
import json
import logging
import threading
import time
from datetime import date, datetime
from pathlib import Path

from jsonio import read_json, write_json

log = logging.getLogger(__name__)

_STATE_FILE = Path(__file__).parent / 'charger_state.json'

_DEFAULT_SETTINGS: dict = {
    'balance_interval_days':   30,     # so oft ist ein Balance-Lauf fällig
    # Die gelernte Kennlinie der Bank: bei welcher Spannung sich welcher
    # Ladezustand einpendelt.
    'kennlinie': {
        'ruhe_a':      1.0,   # |Strom| darunter gilt die Bank als in Ruhe
        'ruhe_min':   10.0,   # so lange muss sie ruhig sein, bevor gemessen wird
        'abstand_min': 30.0,  # Mindestabstand zwischen zwei Messpunkten
        # Temperaturgang der Kennlinie in V je °C. Ab Werk 0: LiFePO4 wird
        # ueblicherweise OHNE Temperaturkompensation geladen. Die Temperatur
        # wird trotzdem je Fach mitgeschrieben — wer einen Gang beobachtet,
        # kann ihn hier eintragen.
        'temp_koeff':  0.0,
    },
    'solar_priority_offset_v':  0.3,   # Nicht-Solar-Geräte bekommen Absorption − Offset
    # Fünf benannte Ladeprofile. Hafen und Vollladung verweisen darauf, statt
    # eigene Spannungen zu halten — so steht jede Spannung genau einmal, und
    # dasselbe Profil lässt sich an beiden Stellen verwenden.
    'profile': [
        {'id': 1, 'name': 'Vollladung', 'absorption_v': 14.4, 'float_v': 13.5},
        {'id': 2, 'name': 'Hafen',      'absorption_v': 13.8, 'float_v': 13.3},
        {'id': 3, 'name': 'Profil 3',   'absorption_v': 14.0, 'float_v': 13.4},
        {'id': 4, 'name': 'Profil 4',   'absorption_v': 13.6, 'float_v': 13.3},
        {'id': 5, 'name': 'Profil 5',   'absorption_v': 14.2, 'float_v': 13.5},
    ],
    'devices': {
        # max_current_a ist der normale Ladestrom des Geräts (Typenschild, am Bus
        # nachgemessen). Der Balance-Lauf setzt ihn vorübergehend herunter und
        # stellt ihn danach wieder her — ohne diesen Wert wüsste er nicht, worauf.
        'ip43':  {'enabled': True,  'is_solar': False, 'label': 'Smart IP43',  'instance': 1, 'max_current_a': 50.0},
        'mppt':  {'enabled': True,  'is_solar': True,  'label': 'MPPT 75/15',  'instance': 3, 'max_current_a': 15.0},
        'orion': {'enabled': False, 'is_solar': False, 'label': 'Orion XS',    'instance': 0, 'max_current_a': 50.0},
    },
    'harbor':  {
        'profile_id':         2,     # Ladeprofil, bis der Ziel-SOC steht
        'hold_voltage':      13.2,   # Halteprofil: Absorption = Erhaltung = dieser Wert
        'target_soc':        80,     # Ziel-SOC (%)
        'soc_hysteresis_pct': 3,     # Hysterese: zurück ins Laden erst bei Ziel − diesem Wert
        # Wie am Ziel-SOC gehalten wird:
        #   'spannung' — Ladespannung auf hold_voltage, Lader bleibt an. Der Lader
        #                hört von selbst auf zu drücken, der Ladezyklus startet
        #                nicht neu, und ein ausgefallener Pi hinterlässt einen
        #                harmlosen Zustand statt eines abgeschalteten Laders.
        #                Mit hold_auto zieht sich die Haltespannung selbst nach.
        #   'profil'   — auf ein anderes Ladeprofil umschalten (hold_profile_id).
        #                Die von Hand gepflegte Fassung: keine eigene Spannung,
        #                sondern eines der fünf Profile.
        #   'aus'      — Lader abschalten. Harter Stopp, aber jedes
        #                Wiedereinschalten beginnt erneut mit Bulk.
        'hold_mode':         'spannung',
        'hold_profile_id':    4,      # gilt nur bei hold_mode 'profil'
        # Haltespannung selbst ermitteln statt fest vorzugeben. Aus, bis sie
        # jemand einschaltet — eine Regelung, die von sich aus an den
        # Ladespannungen dreht, darf nicht die Vorgabe sein.
        'hold_auto':         False,
        'hold_min_v':        12.8,   # Untergrenze der Selbstermittlung
        'hold_step_v':        0.01,  # kleinste Schrittweite
        'hold_step_max_v':    0.10,  # groesste Schrittweite
        'hold_kp':            0.004, # Schrittweite je Prozentpunkt Abweichung
        'hold_kp_a':          0.01,  # Schrittweite je Ampere, den der Lader beim Halten drueckt
        'hold_schnell_min':  45.0,   # so lange darf er druecken, bevor nachgeregelt wird
        'hold_settle_h':      3.0,   # so lange muss gehalten worden sein
        'hold_quiet_a':       2.0,   # |Strom| darunter gilt die Bank als eingependelt
        'hold_interval_h':    1.0,   # Mindestabstand zwischen zwei Schritten
        'hold_max_pro_tag':   6,     # hoechstens so viele Schritte am Tag
    },
    'full':    {'profile_id': 1},
    # Der Balance-Lauf fährt keine feste Spannung, sondern einen Ablauf:
    # entladen, langsam laden, Spannung schrittweise heben, halten.
    'balance': {
        'start_soc':      60,     # bis hierher wird entladen (Lader aus, warten)
        'strom_a':        10.0,   # Ladestrom in der Ladephase — klein, damit das
                                  # BMS Zeit zum Ausgleichen hat
        'start_v':        13.6,   # Spannung, mit der die Ladephase beginnt
        'max_v':          14.4,   # Obergrenze der Steigerung
        'schritt_v':       0.05,  # Schrittweite
        'zelldiff_mv':    20.0,   # darunter gelten die Zellen als gleich
        'schritt_min':    20.0,   # Mindestabstand zwischen zwei Schritten
        'ziel_soc':      100,     # ab hier wird gehalten
        'halten_h':        2.0,   # so lange auf dem Ziel halten
        'max_h':          48.0,   # Sicherheitsdeckel für den ganzen Lauf
        # Fälligen Lauf von selbst starten. Aus, bis jemand es einschaltet: ein
        # Ablauf, der die Bank erst leerlaufen lässt und danach Stunden lädt,
        # beginnt nicht ungefragt.
        'auto':          False,
    },
}

# Die Modi, die es gibt. Alles andere ist ein Fehler und faellt auf 'harbor'
# zurueck — lieber geregelt laden als unbegrenzt.
_MODI = ('harbor', 'full', 'balance')

# Derselbe Modus als Zahl, fuer den Verlauf. Der traegt ausschliesslich Zahlen:
# gemittelt, verdichtet und gezeichnet wird nur, was sich rechnen laesst, und
# eine Zeichenkette faellt in jeder dieser Stufen stillschweigend heraus.
# Die Reihenfolge ist die des Ladeeifers, damit die Kurve etwas bedeutet.
#
# ACHTUNG: die Namen stehen ein zweites Mal in static/js/diagnose.js
# (MODUS_STUFEN) — beide muessen zusammenpassen.
_MODUS_ZAHL = {'harbor': 1, 'full': 2, 'balance': 3}


def _feldname(kennung) -> str:
    """Geraete-Kennung zu einem Feldnamen fuer den Verlauf.

    Die Kennungen kommen aus den Einstellungen und koennen alles enthalten, was
    durch JSON passt. Im Verlauf werden daraus Spaltennamen, die in CSV-Export
    und Graphen auftauchen — deshalb bleibt nur, was unverfaenglich ist.
    """
    sauber = ''.join(c for c in str(kennung).lower() if c.isalnum())
    return sauber[:8] or 'lader'


_DEFAULT_STATE: dict = {
    'mode':                'harbor',
    'last_balance':        None,
    'balance_start':       None,
    'harbor_holding':      None,    # True = Lader aus (Ziel-SOC erreicht), None = noch unbekannt
    'actual_absorption_v': None,
    'actual_float_v':      None,
    'actual_last_read':    None,
    'hold_learned_v':      None,    # selbst ermittelte Haltespannung
    'hold_learn_last':     None,    # Zeitpunkt der letzten Anpassung
    'balance_phase':       None,    # 'entladen' | 'laden' | 'halten'
    'balance_zurueck':     None,    # Modus, in den nach dem Lauf zurückgekehrt wird
    'balance_spannung':    None,    # gerade gefahrene Spannung der Ladephase
    'balance_phase_seit':  None,    # Beginn der aktuellen Phase
    'balance_schritt':     None,    # Zeitpunkt des letzten Spannungsschritts
    'balance_sperre':      None,    # nach einem Abbruch: fruehestens danach wieder starten
    # Gelernte Kennlinie: je Ladezustands-Fach ein gleitender Mittelwert der
    # Spannung, dazu Anzahl der Beobachtungen und mittlere Zelltemperatur.
    'kennpunkte':          [],
    'kenn_letzte':         None,    # Zeitpunkt der letzten Aufnahme
    'hold_learn_tag':      None,    # Tag, auf den sich hold_learn_zahl bezieht
    'hold_learn_zahl':     0,       # Nachfuehrungen an diesem Tag
}


# Obergrenze fuer die Hafen-Hysterese in SOC-Prozentpunkten. Groesser darf sie
# nicht werden, sonst bleibt der Lader zu tief hinunter aus.
_MAX_HYSTERESIS_PCT = 20.0


def _stunden_seit(iso: str | None) -> float:
    """Stunden seit einem gespeicherten Zeitpunkt, 0 wenn er fehlt oder unlesbar ist."""
    if not iso:
        return 0.0
    try:
        return max(0.0, (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        return 0.0


def _num(value, default: float) -> float:
    """Zahl aus den Einstellungen oder Vorgabewert.

    Die Einstellungen kommen als JSON aus dem PATCH-Endpunkt — ein Text oder None
    darf hier nicht in eine Rechnung geraten und die Regelung anhalten.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    return float(value)


def _register_stufe(v: float) -> int:
    """Was nach dem Weg zum Lader in seinem Register steht, in 0,01-V-Stufen.

    Der Pi schickt Millivolt, das Gateway rundet auf 0,01 V, der Lader meldet
    genau das zurueck. Frueher wurde die Rueckmeldung mit einer
    Gleitkomma-Toleranz von einer halben Stufe verglichen — und bei GENAU
    halber Stufe faellt der Abstand in Gleitkomma auf die falsche Seite
    (13,055 V → Register 13,06 → Abstand 0.005000000000000782 > 0.005).
    Nachgerechnet scheitern so 90 der 1601 moeglichen Ziele zwischen 12,80 und
    14,40 V, und solche Werte entstehen im Normalbetrieb: die Selbstermittlung
    und die Kennlinie liefern beliebige Tausendstel.

    Die Folge waere nicht nur ein ueberfluessiges Nachsetzen gewesen, sondern
    ein dauerhaft totes Sicherheitsnetz: nach drei vergeblichen Versuchen gibt
    der Regler auf, und eine spaetere ECHTE Verstellung des Laders faellt dann
    niemandem mehr auf.

    Also in derselben Einheit vergleichen, in der das Geraet rechnet.
    """
    return (int(round(v * 1000)) + 5) // 10


# Bremse fuer das Nachsetzen. Uebernimmt der Lader einen Wert nicht — weil das
# Register ihn neu berechnet, weil er ihn ablehnt, weil er defekt ist —, wuerde
# der Regler ihn sonst bei jeder Rueckmeldung erneut schreiben. Bei einer
# Abfrage alle fuenf Minuten waeren das ueber 100 Schreibvorgaenge am Tag in ein
# Flash, das dafuer nicht gedacht ist. Also: Mindestabstand zwischen zwei
# Versuchen, und nach _NACHSETZ_MAX vergeblichen Versuchen wird aufgegeben. Zu
# sehen bleibt es trotzdem — preset_match steht dann auf 'custom'.
_NACHSETZ_ABSTAND_S = 1800.0
_NACHSETZ_MAX       = 3

# Zulässige Werte für harbor.hold_mode.
_HALTEARTEN = ('spannung', 'profil', 'aus')

# So viele Ladeprofile gibt es, fest. Die Liste hat immer genau diese Länge:
# eine Auswahl, die je nach gespeichertem Stand mal drei und mal fünf Einträge
# hat, ist in der Oberfläche schwerer zu bedienen als eine feste.
_PROFIL_ZAHL   = 5
_PROFIL_NAME_MAX = 40

# Die Phasen des Balance-Laufs, in dieser Reihenfolge.
#
#   entladen  Alle Lader aus, warten bis der Start-Ladezustand erreicht ist.
#             Aktiv entladen kann das System nicht — es gibt keine steuerbare
#             Last. Es wird abgeschaltet und gewartet, mehr geht nicht.
#   laden     Mit kleinem Strom laden und die Spannung schrittweise heben,
#             solange die Zellen beieinanderliegen. Der kleine Strom ist der
#             eigentliche Zweck: er gibt dem BMS Zeit zum Ausgleichen.
#   halten    Das erreichte Ziel eine Weile halten, dann zurück in den Modus,
#             aus dem gestartet wurde.
_BAL_PHASEN = ('entladen', 'laden', 'halten')

# So lange wird nach einem abgebrochenen Lauf nicht automatisch neu gestartet.
_BAL_SPERRE_H = 24.0

# ── Die gelernte Kennlinie ──────────────────────────────────────────────────
#
# Bei jedem Einpendeln faellt ein Messpunkt an: anliegende Spannung,
# Ladezustand, Zelltemperatur. Frueher wurde er einmal mit dem Ziel verglichen
# und weggeworfen. Jetzt sammeln sich die Punkte in einer Tabelle ueber den
# Ladezustand, und daraus laesst sich fuer JEDES Ziel die passende Spannung
# ablesen, statt sie in kleinen Schritten zu erlaufen.
#
# Die Tabelle ist bewusst grob: Faecher von 5 Prozentpunkten, je Fach ein
# gleitender Mittelwert. Feiner waere Selbstbetrug — die LiFePO4-Kennlinie ist
# flach, und der Ladezustand des BMS ist selbst nur eine Schaetzung.
_KENN_FACH    = 5     # Breite eines Fachs in Prozentpunkten
_KENN_GEWICHT = 10    # ab so vielen Beobachtungen folgt der Mittelwert nur noch traege
_KENN_MIN_N   = 3     # so viele braucht ein Fach, bevor daraus abgelesen wird

# Totband der Selbstermittlung in SOC-Prozentpunkten. Enger lohnt nicht: die
# LiFePO4-Kennlinie ist so flach, dass ein Prozentpunkt bereits im Rauschen der
# SOC-Schaetzung des BMS liegt.
_LERN_TOLERANZ_PCT = 1.0


class ChargeController:
    def __init__(self):
        self._last_soc: float | None = None
        self._nachsetzen: bool = False               # Rückmeldung wich ab → neu senden
        self._halte_seit: float | None = None        # monoton, Beginn des aktuellen Haltens
        self._nachsetz_zeit: float | None = None     # monoton, letzter Nachsetz-Versuch
        self._nachsetz_zaehler: int = 0              # vergebliche Versuche in Folge
        self._ruhig_seit: float | None = None         # monoton, seit wann der Strom klein ist
        self._laedt_seit: float | None = None         # monoton, seit wann trotz Halten geladen wird
        self._letzte_temp: float | None = None        # zuletzt gesehene Zelltemperatur
        self._phase_mono: float | None = None        # monoton, Beginn der Balance-Phase
        self._schritt_mono: float | None = None      # monoton, letzter Spannungsschritt
        self._last_written: str | None = None        # zuletzt auf Disk geschriebener Stand
        self._balance_mono: float | None = None      # Start des laufenden Balance-Laufs (monoton)
        # _save() wird aus zwei Richtungen aufgerufen: aus dem Event-Loop
        # (update_soc/update_settings/set_mode) und aus dem CAN-Thread
        # (update_actual_setpoints). write_json legt immer dieselbe
        # charger_state.json.tmp an — zwei gleichzeitige Schreiber wuerden sich
        # gegenseitig die Temp-Datei abschneiden und einen halben Stand
        # einsetzen. Der Lock serialisiert das.
        self._io_lock = threading.Lock()
        self._load()

    # ── Persistenz ──────────────────────────────────────────────────────────

    def _load(self):
        data = read_json(_STATE_FILE, {})
        if not isinstance(data, dict):
            data = {}
        state    = data.get('state')
        settings = data.get('settings')
        self._state    = {**_DEFAULT_STATE, **(state if isinstance(state, dict) else {})}
        # Ein gespeichertes null schlaegt beim Zusammenfuehren den Vorgabewert —
        # und ein unbekannter Modus schaltet die Hafen-Regelung KOMPLETT ab:
        # `if mode == 'harbor'` ist dann falsch, der Zweig mit SOC-Ziel,
        # Halten und Hysterese wird uebersprungen, und der Lader bekommt
        # stumm 13,8/13,3 V ohne jede Begrenzung. Am Boot genau so
        # vorgefunden (mode war null) — bei SOC 85 kam `on: None` heraus,
        # also kein Halten bei 80 %.
        if self._state.get('mode') not in _MODI:
            log.warning('Unbekannter Lademodus %r — zurueck auf %s',
                        self._state.get('mode'), _DEFAULT_STATE['mode'])
            self._state['mode'] = _DEFAULT_STATE['mode']
        settings = self._umstellen(settings if isinstance(settings, dict) else {})
        self._settings = self._deep_merge(_DEFAULT_SETTINGS, settings)
        self._settings['profile'] = self._profile_saeubern(self._settings.get('profile'))
        if self._state.get('mode') == 'balance':
            # Die monotonen Uhren nach einem Neustart NEU stellen, nicht der
            # Wanduhr ueberlassen. Der Pi hat keine Echtzeituhr; steht sie nach
            # dem Start hinter dem gespeicherten Beginn, wird die Differenz
            # negativ und auf 0 geklemmt — dauerhaft, weil im laufenden Modus
            # nie wieder eine monotone Uhr gesetzt wurde. Damit war der
            # Sicherheitsdeckel des Balance-Laufs fuer immer wirkungslos und
            # die Bank haette unbegrenzt auf Konstantspannung gehangen.
            # Neu gestellt zaehlt ab dem Neustart — der Lauf ist damit wieder
            # begrenzt, im schlimmsten Fall auf max_h ab hier.
            jetzt = time.monotonic()
            self._balance_mono = jetzt
            self._phase_mono   = jetzt
            self._schritt_mono = jetzt
            log.info('Balance-Lauf nach Neustart uebernommen — Uhren neu gestellt')

    @staticmethod
    def _umstellen(roh: dict) -> dict:
        """Alte Stände auf die Profilliste heben.

        Bis zum Umbau trugen `harbor` und `full` ihre Spannungen selbst. Diese
        Werte sind vom Eigner eingestellt und duerfen nicht verlorengehen — sie
        wandern in Profil 2 (Hafen) und Profil 1 (Vollladung), und die beiden
        Modi verweisen darauf. Ohne das griffe _deep_merge, das nur bekannte
        Schluessel behaelt, und die alten Spannungen waeren stumm weg.

        Laeuft nur, solange keine Profilliste da ist — danach ist der Stand neu
        und wird nicht noch einmal angefasst.
        """
        if not isinstance(roh, dict) or isinstance(roh.get('profile'), list):
            return roh
        alt_h = roh.get('harbor') if isinstance(roh.get('harbor'), dict) else {}
        alt_f = roh.get('full')   if isinstance(roh.get('full'), dict)   else {}
        hat_alt = any(k in d for d in (alt_h, alt_f) for k in ('absorption_v', 'float_v'))
        if not hat_alt:
            return roh
        profile = [dict(pr) for pr in _DEFAULT_SETTINGS['profile']]
        for ziel, quelle in ((profile[0], alt_f), (profile[1], alt_h)):
            for k in ('absorption_v', 'float_v'):
                if isinstance(quelle.get(k), (int, float)) and not isinstance(quelle.get(k), bool):
                    ziel[k] = float(quelle[k])
        neu = dict(roh)
        neu['profile'] = profile
        neu['harbor']  = {**alt_h, 'profile_id': 2}
        neu['full']    = {**alt_f, 'profile_id': 1}
        log.info('Ladeeinstellungen auf Profile umgestellt: '
                 'Profil 1 = %.2f/%.2f V (Vollladung), Profil 2 = %.2f/%.2f V (Hafen)',
                 profile[0]['absorption_v'], profile[0]['float_v'],
                 profile[1]['absorption_v'], profile[1]['float_v'])
        return neu

    @staticmethod
    def _deep_merge(base: dict, overlay: dict) -> dict:
        """Zweistufiger Merge: Schlüssel der zweiten Ebene werden pro Eintrag gemergt.

        Wo die Vorgabe ein Objekt ist, muss auch der gespeicherte Wert eines
        sein — sonst gewinnt die Vorgabe. Ein `"harbor": "kaputt"` in der
        Zustandsdatei oder aus dem PATCH-Endpunkt haette sonst die ganze
        Ladesteuerung lahmgelegt: jeder Zugriff der Form
        `self._settings.get('harbor', {}).get(...)` faellt auf einer
        Zeichenkette mit AttributeError um, und weil der kaputte Wert
        gespeichert wird, auch nach jedem Neustart wieder.
        """
        result = {}
        for k, v in base.items():
            if k not in overlay:
                result[k] = v
                continue
            ov = overlay[k]
            if isinstance(v, dict) and not isinstance(ov, dict):
                log.warning('Einstellung %r ist kein Objekt (%s) — Vorgabe bleibt stehen',
                            k, type(ov).__name__)
                result[k] = v
                continue
            if isinstance(v, dict) and isinstance(ov, dict):
                inner = {}
                for ik, iv in v.items():
                    if ik in ov:
                        oiv = ov[ik]
                        if isinstance(iv, dict) and isinstance(oiv, dict):
                            inner[ik] = {**iv, **oiv}
                        else:
                            inner[ik] = oiv
                    else:
                        inner[ik] = iv
                result[k] = inner
            else:
                result[k] = ov
        return result

    def _save(self):
        """Schreibt Zustand + Einstellungen — atomar und nur bei echter Änderung.

        Der Aufruf hängt an CAN-Ereignissen; ein unveränderter Stand darf die
        SD-Karte nicht anfassen.
        """
        payload = {'state': self._state, 'settings': self._settings}
        with self._io_lock:
            snapshot = json.dumps(payload, sort_keys=True)
            if snapshot == self._last_written:
                return
            write_json(_STATE_FILE, payload)
            self._last_written = snapshot

    # ── P-Regler Hafen-Modus ────────────────────────────────────────────────

    def _holding_for(self, soc: float | None) -> bool:
        """Soll im Hafen-Modus gehalten (Lader aus) werden? — mit Hysterese.

        Ausschalten bei Ziel-SOC, wieder einschalten erst bei Ziel − Hysterese.
        Innerhalb des Bandes bleibt der bisherige Zustand stehen; ohne das taktet
        der Lader am Ziel-SOC ständig zwischen DeviceMode 0 und 1.
        """
        prev = self._state.get('harbor_holding')
        if soc is None:
            return bool(prev)
        h      = self._settings.get('harbor', {})
        target = _num(h.get('target_soc'), 80)
        # Die Hysterese kommt ungeprueft aus PATCH /api/charger/settings und darf
        # _MAX_HYSTERESIS_PCT nicht ueberschreiten. Ohne diese Grenze schaltet ein
        # Wert >= Ziel-SOC nie wieder ins Laden zurueck: der SOC faellt dann bis
        # zur BMS-Abschaltung, also Tiefentladung.
        hyst   = min(max(0.0, _num(h.get('soc_hysteresis_pct'), 3)), _MAX_HYSTERESIS_PCT)
        if soc >= target:
            return True
        if soc <= target - hyst:
            return False
        return bool(prev) if prev is not None else False   # unbekannt im Band → laden

    def _profile(self) -> list[dict]:
        liste = self._settings.get('profile')
        return liste if isinstance(liste, list) else _DEFAULT_SETTINGS['profile']

    def _profil(self, pid) -> dict:
        """Das Profil zu einer Nummer — oder das erste, wenn die Nummer nichts trifft.

        Nie None: der Aufrufer rechnet mit Spannungen, und ein fehlendes Profil
        (geloescht, nie angelegt, Zahlendreher in den Einstellungen) darf die
        Ladung nicht anhalten. Das erste Profil ist die Vollladung und damit die
        sichere Seite — lieber zu viel geladen als der Lader steht.
        """
        liste = self._profile()
        for pr in liste:
            if isinstance(pr, dict) and pr.get('id') == pid:
                return pr
        return liste[0] if liste else _DEFAULT_SETTINGS['profile'][0]

    def _profil_spannungen(self, pid) -> tuple[float, float]:
        pr    = self._profil(pid)
        abs_v = _num(pr.get('absorption_v'), 14.4)
        flt_v = _num(pr.get('float_v'),      13.5)
        # Erhaltung nie ueber Absorption — sonst laedt der Lader in der
        # Erhaltungsphase haerter als in der Absorption.
        return abs_v, min(flt_v, abs_v)

    @staticmethod
    def _profile_saeubern(liste) -> list[dict]:
        """Macht aus dem, was gespeichert war, eine brauchbare Profilliste.

        Die Liste kommt aus einer Datei und aus einem PATCH-Endpunkt. Fehlt ein
        Eintrag, ist er kein Objekt oder trägt er eine unbrauchbare Nummer, wird
        er durch die Vorgabe ersetzt — die Reihenfolge der Nummern 1..N steht
        fest, damit eine Auswahl in der Oberfläche stabil bleibt.
        """
        vorgabe = _DEFAULT_SETTINGS['profile']
        roh     = liste if isinstance(liste, list) else []
        nach_id = {}
        for e in roh:
            if isinstance(e, dict) and isinstance(e.get('id'), int) and not isinstance(e.get('id'), bool):
                nach_id[e['id']] = e
        sauber = []
        for i in range(_PROFIL_ZAHL):
            pid = i + 1
            v   = vorgabe[i] if i < len(vorgabe) else vorgabe[-1]
            e   = nach_id.get(pid, {})
            name = e.get('name')
            if not isinstance(name, str) or not name.strip():
                name = v['name']
            sauber.append({
                'id':           pid,
                'name':         name.strip()[:_PROFIL_NAME_MAX],
                'absorption_v': _num(e.get('absorption_v'), v['absorption_v']),
                'float_v':      _num(e.get('float_v'),      v['float_v']),
            })
        return sauber

    def _kenn(self) -> dict:
        k = self._settings.get('kennlinie')
        return k if isinstance(k, dict) else _DEFAULT_SETTINGS['kennlinie']

    def _kennpunkte(self) -> list:
        p = self._state.get('kennpunkte')
        return p if isinstance(p, list) else []

    def _kenn_beobachten(self, soc: float | None, spannung: float | None,
                         current_a: float | None, zelltemp: float | None) -> None:
        """Nimmt einen Messpunkt auf, wenn die Bank wirklich in Ruhe ist.

        "In Ruhe" heisst: der Betrag des Stroms liegt eine Weile am Stueck unter
        der Schwelle. Ein einzelner Messwert genuegt nicht — der Strom geht beim
        Vorzeichenwechsel durch null, und ein Punkt aus diesem Moment saehe wie
        Ruhe aus, waehrend die Bank in Wahrheit gerade umschlaegt.

        Die anliegende Spannung IST hier die Ruhespannung: fliesst kein Strom,
        steht die Bank auf ihrer Leerlaufspannung. Ob der Lader sie haelt oder
        ob gar nicht geladen wird, spielt keine Rolle — gelernt wird beides.
        """
        if zelltemp is not None:
            self._letzte_temp = zelltemp
        k       = self._kenn()
        ruhig_a = _num(k.get('ruhe_a'), 1.0)
        if (soc is None or spannung is None or current_a is None
                or abs(current_a) > ruhig_a):
            self._ruhig_seit = None
            return
        jetzt = time.monotonic()
        if self._ruhig_seit is None:
            self._ruhig_seit = jetzt
            return
        if (jetzt - self._ruhig_seit) / 60.0 < _num(k.get('ruhe_min'), 10.0):
            return
        # Nicht bei jeder ruhigen Minute einen Punkt: die Tabelle wird auf die
        # SD-Karte geschrieben, und eine stille Nacht wuerde sie hundertmal
        # anfassen, ohne etwas Neues zu lernen.
        if _stunden_seit(self._state.get('kenn_letzte')) * 60.0 < _num(k.get('abstand_min'), 30.0) \
                and self._state.get('kenn_letzte'):
            return

        fach = min(100, max(0, int(round(soc / _KENN_FACH)) * _KENN_FACH))
        punkte = [dict(p) for p in self._kennpunkte() if isinstance(p, dict)]
        eintrag = next((p for p in punkte if p.get('soc') == fach), None)
        if eintrag is None:
            eintrag = {'soc': fach, 'v': round(float(spannung), 3), 'n': 1,
                       't': round(float(zelltemp), 1) if zelltemp is not None else None}
            punkte.append(eintrag)
        else:
            # Gleitender Mittelwert mit gedeckeltem Gewicht: die ersten Punkte
            # zaehlen voll, spaeter folgt das Fach nur noch traege. So bleibt es
            # ruhig und kann der Bank trotzdem folgen, wenn sie sich aendert.
            gew = min(int(_num(eintrag.get('n'), 1)) + 1, _KENN_GEWICHT)
            alt = _num(eintrag.get('v'), float(spannung))
            eintrag['v'] = round(alt + (float(spannung) - alt) / gew, 3)
            if zelltemp is not None:
                alt_t = eintrag.get('t')
                eintrag['t'] = round(float(zelltemp) if alt_t is None
                                     else _num(alt_t, zelltemp) + (float(zelltemp) - _num(alt_t, zelltemp)) / gew, 1)
            eintrag['n'] = int(_num(eintrag.get('n'), 1)) + 1
        punkte.sort(key=lambda p: p.get('soc', 0))
        self._state['kennpunkte']  = punkte
        self._state['kenn_letzte'] = datetime.now().isoformat()
        self._ruhig_seit = jetzt
        self._save()
        log.info('Kennlinie: %.0f %% bei %.3f V (Fach %d, %d. Beobachtung)',
                 soc, spannung, fach, eintrag['n'])

    def _kenn_spannung(self, ziel_soc: float) -> float | None:
        """Die Spannung, bei der sich der Ziel-Ladezustand einpendelt.

        None, wenn die Tabelle es nicht hergibt. Es wird ausschliesslich
        INTERPOLIERT, nie ueber den gemessenen Bereich hinaus gerechnet: eine
        aus zwei Punkten verlaengerte Gerade waere geraten, und geraten wird
        hier nicht — dann lieber der bisherige, langsame Weg ueber Schritte.
        """
        punkte = [p for p in self._kennpunkte()
                  if isinstance(p, dict)
                  and isinstance(p.get('soc'), (int, float))
                  and isinstance(p.get('v'), (int, float))
                  and _num(p.get('n'), 0) >= _KENN_MIN_N]
        if len(punkte) < 2:
            return None
        punkte.sort(key=lambda p: p['soc'])
        if ziel_soc < punkte[0]['soc'] or ziel_soc > punkte[-1]['soc']:
            return None
        unten = max((p for p in punkte if p['soc'] <= ziel_soc), key=lambda p: p['soc'])
        oben  = min((p for p in punkte if p['soc'] >= ziel_soc), key=lambda p: p['soc'])
        if oben['soc'] == unten['soc']:
            v, t_fach = float(unten['v']), unten.get('t')
        else:
            anteil = (ziel_soc - unten['soc']) / (oben['soc'] - unten['soc'])
            v = float(unten['v']) + anteil * (float(oben['v']) - float(unten['v']))
            t_u, t_o = unten.get('t'), oben.get('t')
            t_fach = (_num(t_u, 0) + anteil * (_num(t_o, 0) - _num(t_u, 0))
                      if isinstance(t_u, (int, float)) and isinstance(t_o, (int, float)) else None)
        koeff = _num(self._kenn().get('temp_koeff'), 0.0)
        if koeff and t_fach is not None and self._letzte_temp is not None:
            v += koeff * (self._letzte_temp - t_fach)
        return round(v, 3)

    def _haltespannung(self) -> float:
        """Die Spannung, auf der im Hafen-Modus gehalten wird.

        Entweder die eingestellte oder die selbst ermittelte — umschaltbar über
        harbor.hold_auto. Solange noch nichts ermittelt wurde, gilt die
        eingestellte als Startwert; die Selbstermittlung geht von dort aus.
        """
        h       = self._settings.get('harbor', {})
        manuell = _num(h.get('hold_voltage'), 13.2)
        if h.get('hold_auto') is not True or h.get('hold_mode') not in (None, 'spannung'):
            return manuell
        # Zuerst die gelernte Kennlinie: sie kennt die Spannung zum Ziel direkt,
        # statt sie in Schritten zu erlaufen — und sie kennt sie auch fuer ein
        # geaendertes Ziel sofort.
        aus_kurve = self._kenn_spannung(_num(h.get('target_soc'), 80))
        if aus_kurve is not None:
            return self._halte_grenzen(aus_kurve)
        gelernt = self._state.get('hold_learned_v')
        if isinstance(gelernt, bool) or not isinstance(gelernt, (int, float)):
            return self._halte_grenzen(manuell)
        return self._halte_grenzen(float(gelernt))

    def _haltespannung_quelle(self) -> str:
        """Woher die geltende Haltespannung stammt — fuer die Anzeige."""
        h = self._settings.get('harbor', {})
        if h.get('hold_auto') is not True or h.get('hold_mode') not in (None, 'spannung'):
            return 'manuell'
        if self._kenn_spannung(_num(h.get('target_soc'), 80)) is not None:
            return 'kennlinie'
        if isinstance(self._state.get('hold_learned_v'), (int, float)):
            return 'nachgefuehrt'
        return 'manuell'

    def _halte_grenzen(self, v: float) -> float:
        """Haelt die Haltespannung im erlaubten Band.

        Nach oben begrenzt die Absorptionsspannung des Hafen-Profils: die
        Selbstermittlung darf nie mehr verlangen als die Ladephase selbst.
        Nach unten hold_min_v. Beide Werte kommen ungeprueft aus dem
        Einstellungs-Endpunkt — steht die Untergrenze ueber der Obergrenze,
        gewinnt die Obergrenze, sonst liesse sich die Grenze umgehen.
        """
        h     = self._settings.get('harbor', {})
        oben  = self._profil_spannungen(h.get('profile_id'))[0]
        unten = min(_num(h.get('hold_min_v'), 12.8), oben)
        return round(min(max(v, unten), oben), 3)

    def _harbor_profile(self, holding: bool) -> tuple[float, float, bool]:
        """Die Sollwerte des Hafen-Modus: (Absorption, Erhaltung, Lader ein).

        Zwei feste Profile statt einer laufend nachgeführten Spannung. Der Grund
        steht in der Hardware: die Sollwert-Register des Laders liegen in seinem
        Flash. Eine Regelung, die bei jeder SOC-Änderung schreibt, nutzt es ab.
        Geschrieben wird deshalb nur beim Wechsel zwischen Laden und Halten.
        """
        h = self._settings.get('harbor', {})
        abs_v, flt_v = self._profil_spannungen(h.get('profile_id'))
        if not holding:
            return abs_v, flt_v, True
        art = h.get('hold_mode')
        if art not in _HALTEARTEN:
            art = 'spannung'
        if art == 'aus':
            # Sollwerte unverändert stehen lassen, nur abschalten — so muss beim
            # Wiedereinschalten nichts ins Flash zurückgeschrieben werden.
            return abs_v, flt_v, False
        if art == 'profil':
            return (*self._profil_spannungen(h.get('hold_profile_id')), True)
        hold_v = self._haltespannung()
        return hold_v, hold_v, True

    def _harbor_voltage(self, soc: float, holding: bool | None = None) -> float:
        """Absorptionsspannung, die im Hafen-Modus gerade gelten soll.

        holding=None ermittelt den Halte-Zustand selbst (rein lesend, ohne die
        Hysterese umzuschalten) — so darf status() jederzeit rechnen.
        """
        if holding is None:
            holding = self._holding_for(soc)
        return self._harbor_profile(holding)[0]

    # ── Lese-API ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        harbor_v = None
        if self._state['mode'] == 'harbor' and self._last_soc is not None:
            harbor_v = self._harbor_voltage(self._last_soc)
        return {
            'mode':                self._state['mode'],
            'harbor_voltage':      harbor_v,
            'harbor_holding':      self._state.get('harbor_holding'),
            'hold_voltage_eff':    self._haltespannung(),
            'hold_quelle':         self._haltespannung_quelle(),
            'kennpunkte':          self._kennpunkte(),
            'kenn_letzte':         self._state.get('kenn_letzte'),
            'profile_name':        self._profil(
                self._settings.get(self._state['mode'], {}).get('profile_id')).get('name'),
            'hold_learned_v':      self._state.get('hold_learned_v'),
            'hold_learn_last':     self._state.get('hold_learn_last'),
            'last_balance':        self._state['last_balance'],
            'balance_start':       self._state['balance_start'],
            'balance_hours':       self._balance_hours(),
            'balance_phase':       self._state.get('balance_phase'),
            'balance_phase_hours': self._phase_stunden() if self._state['mode'] == 'balance' else None,
            'balance_spannung':    self._state.get('balance_spannung'),
            'balance_zurueck':     self._state.get('balance_zurueck'),
            'actual_absorption_v': self._state['actual_absorption_v'],
            'actual_float_v':      self._state['actual_float_v'],
            'actual_last_read':    self._state['actual_last_read'],
            'preset_match':        self._preset_match(),
            'balance_due':         self._days_until_balance() == 0,
            'days_since_balance':  self._days_since_balance(),
            'days_until_balance':  self._days_until_balance(),
            'device_setpoints':    self.device_setpoints(),
            'settings':            self._settings,
        }

    def verlaufswerte(self) -> dict:
        """Was von der Ladesteuerung in den Verlauf gehoert — nur Zahlen.

        Bisher stand im Verlauf ausschliesslich, WAS die Batterie getan hat,
        nicht, was ihr AUFGETRAGEN war. Hinterher liess sich damit nicht
        unterscheiden, ob der Lader nichts tat, weil nichts zu tun war, oder
        weil die Steuerung ihn heruntergesetzt hatte — und genau das ist die
        Frage, mit der man ins Logbuch schaut.

        Geschrieben wird nur, was im jeweiligen Modus etwas BEDEUTET. Ziel-SOC,
        Halten und Solar-Vorrang gibt es allein im Hafen-Modus; sie in der
        Vollladung mitzuschreiben ergaebe eine Linie, die etwas behauptet, was
        gerade nicht gilt. Im Verlauf fehlen sie dann — das ist die Auskunft.

        Die Felder:

          ld_modus     1 Hafen, 2 Vollladung, 3 Balance (siehe _MODUS_ZAHL)
          ld_halten    1, sobald der Ziel-SOC erreicht ist (nur Hafen)
          ld_an        1, wenn die Lader eingeschaltet sein sollen (nur Hafen)
          ld_vorrang   1, wenn der Solar-Vorrang gerade WIRKT (nur Hafen)
          ld_ziel_soc  der Ladestand, auf den gehalten wird (nur Hafen)
          ld_abs_<x>   Absorptionsspannung, die Geraet <x> bekommen soll
          ld_flt_<x>   dessen Erhaltungsspannung
          ld_ist_abs   was der Smart IP43 als seinen Sollwert zurueckmeldet
          ld_ist_flt   dito Erhaltung

        Soll und Ist getrennt, weil sie auseinanderlaufen koennen: verstellt
        jemand den Lader mit der Victron-App, steht es genau hier — zwei
        Kurven, die sich trennen.
        """
        modus = self._state['mode']
        raus: dict = {'ld_modus': _MODUS_ZAHL.get(modus, 0)}

        for s in self.device_setpoints():
            kurz = _feldname(s['id'])
            raus[f'ld_abs_{kurz}'] = s['absorption_v']
            raus[f'ld_flt_{kurz}'] = s['float_v']

        if modus == 'harbor':
            # Dieselbe Rechnung wie in device_setpoints(), und bewusst NICHT
            # aus deren Ergebnis zurueckgeschlossen: was der Solar-Vorrang
            # bewirkt, ist ein Abstand zwischen zwei Geraeten — bei nur einem
            # eingeschalteten Geraet waere daraus nichts abzulesen.
            soc         = self._last_soc
            halten      = self._holding_for(soc) if soc is not None else False
            ein         = self._harbor_profile(halten)[2]
            offset      = _num(self._settings.get('solar_priority_offset_v'), 0.3)
            h           = self._settings.get('harbor', {})
            raus['ld_halten']   = 1 if halten else 0
            raus['ld_an']       = 1 if ein else 0
            raus['ld_vorrang']  = 1 if (ein and not halten and offset > 0) else 0
            raus['ld_ziel_soc'] = round(_num(h.get('target_soc'), 80), 1)

        for feld, wert in (('ld_ist_abs', self._state.get('actual_absorption_v')),
                           ('ld_ist_flt', self._state.get('actual_float_v'))):
            if isinstance(wert, (int, float)) and not isinstance(wert, bool):
                raus[feld] = round(float(wert), 3)
        return raus

    def device_setpoints(self) -> list[dict]:
        """Gibt pro aktiviertem Gerät die effektiven Spannungs-Sollwerte zurück.

        Hafen-Modus: Lade- oder Halteprofil, Solar-Priorität nur beim Laden.
        Balance-Modus: Erhaltung = Absorption → Konstantspannung (CV).
        """
        mode   = self._state['mode']
        preset = self._settings.get(mode, self._settings['harbor'])

        if mode == 'harbor':
            soc          = self._last_soc
            holding      = self._holding_for(soc) if soc is not None else False
            abs_v, flt_v, ein = self._harbor_profile(holding)
            # Solar-Vorrang nur beim aktiven Laden: die Nicht-Solar-Geräte gehen
            # etwas tiefer, damit die Solaranlage den Rest macht. Beim Halten
            # gilt für alle derselbe Wert, sonst läge der Landlader unter der
            # Ruhespannung und wäre faktisch aus.
            offset = _num(self._settings.get('solar_priority_offset_v'), 0.3) if (ein and not holding) else 0.0
            result = []
            for dev_id, dev in self._settings.get('devices', {}).items():
                if not dev.get('enabled', False):
                    continue
                is_solar = dev.get('is_solar', False)
                v_abs    = round(abs_v - (0.0 if is_solar else offset), 3)
                # Erhaltung darf nie über der Absorption liegen — sonst würde der
                # Lader in der Erhaltungsphase härter laden als in der Absorption.
                v_flt    = round(min(flt_v, v_abs), 3)
                result.append({
                    'id':           dev_id,
                    'label':        dev.get('label', dev_id),
                    'instance':     dev.get('instance', 1),
                    'is_solar':     is_solar,
                    'absorption_v': v_abs,
                    'float_v':      v_flt,
                    'on':           ein,   # False → DeviceMode=0 (aus), True → DeviceMode=1 (ein)
                    'max_a':        _num(dev.get('max_current_a'), 0.0) or None,
                })
            return result

        if mode == 'balance':
            return self._balance_setpoints()

        abs_v, flt_v = self._profil_spannungen(preset.get('profile_id'))
        # Kein Solar-Offset außerhalb des Hafen-Modus
        result = []
        for dev_id, dev in self._settings.get('devices', {}).items():
            if not dev.get('enabled', False):
                continue
            result.append({
                'id':           dev_id,
                'label':        dev.get('label', dev_id),
                'instance':     dev.get('instance', 1),
                'is_solar':     dev.get('is_solar', False),
                'absorption_v': abs_v,
                'float_v':      flt_v,
                'max_a':        _num(dev.get('max_current_a'), 0.0) or None,
            })
        return result

    def _balance_setpoints(self) -> list[dict]:
        """Sollwerte des Balance-Laufs — sie haengen an der Phase, nicht an einem Profil.

        Entladephase: alle Lader aus. Ladephase und Halten: Konstantspannung
        (Erhaltung = Absorption, damit der Lader nicht von selbst abfaellt) und
        der kleine Ladestrom, der dem BMS die Zeit zum Ausgleichen verschafft.
        """
        b     = self._bal()
        phase = self._state.get('balance_phase')
        ein   = phase in ('laden', 'halten')
        v     = self._state.get('balance_spannung')
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            v = _num(b.get('start_v'), 13.6)
        v     = round(min(float(v), _num(b.get('max_v'), 14.4)), 3)
        strom = _num(b.get('strom_a'), 10.0)
        result = []
        for dev_id, dev in self._settings.get('devices', {}).items():
            if not dev.get('enabled', False):
                continue
            normal = _num(dev.get('max_current_a'), strom)
            result.append({
                'id':           dev_id,
                'label':        dev.get('label', dev_id),
                'instance':     dev.get('instance', 1),
                'is_solar':     dev.get('is_solar', False),
                'absorption_v': v,
                'float_v':      v,
                'on':           ein,
                # Nie ueber den normalen Ladestrom des Geraets hinaus: der
                # Balance-Strom ist eine Begrenzung, keine Anhebung.
                'max_a':        round(min(strom, normal), 1),
            })
        return result

    # ── Schreibe-API ─────────────────────────────────────────────────────────

    def update_soc(self, soc: float | None, current_a: float | None = None,
                   zelldiff_mv: float | None = None,
                   landstrom: bool | None = None,
                   spannung: float | None = None,
                   zelltemp: float | None = None,
                   lader_a: float | None = None) -> bool:
        """Aktualisiert SOC (optional den Batteriestrom). True = neue Setpoints senden.

        Hafen-Modus: nur beim Wechsel zwischen Laden und Halten. Die Sollwerte
        liegen im Flash des Laders — eine Regelung, die bei jeder SOC-Änderung
        schreibt, nutzt es ab. Frueher loeste hier jede Aenderung ab 0,5 % aus.

        Balance-Modus: prüft die Abbruchbedingungen; ist der Lauf fertig, wechselt
        der Regler selbst in den Hafen-Modus und meldet True.

        Ausserdem in jedem Modus: hat die Rueckmeldung des Laders von den
        gewollten Sollwerten abgewichen, wird einmal nachgesetzt.

        current_a ist optional: das Schweifstrom-Kriterium greift nur, wenn der
        Aufrufer den Batteriestrom mitgibt. Ohne ihn beendet allein die Höchstdauer
        balance_max_hours den Balance-Lauf.
        """
        if soc is None:
            return False
        self._last_soc = soc

        # Immer beobachten, in jedem Modus: die Kennlinie ist eine Eigenschaft
        # der Bank, nicht des Lademodus. Je mehr ruhige Momente eingehen, desto
        # frueher traegt sie.
        self._kenn_beobachten(soc, spannung, current_a, zelltemp)

        nachsetzen = self._nachsetzen
        if nachsetzen:
            self._nachsetzen        = False
            self._nachsetz_zeit     = time.monotonic()
            self._nachsetz_zaehler += 1
            if self._nachsetz_zaehler >= _NACHSETZ_MAX:
                log.warning('Der Lader uebernimmt die Sollwerte nicht (%d Versuche) — '
                            'es wird nicht weiter geschrieben, um sein Flash zu schonen. '
                            'Der Unterschied bleibt in der Oberflaeche sichtbar.',
                            self._nachsetz_zaehler)

        mode = self._state['mode']
        if mode in ('harbor', 'full') and self._balance_faellig_starten(landstrom):
            return True
        if mode == 'balance':
            return self._balance_takten(soc, current_a, zelldiff_mv, landstrom) or nachsetzen
        if mode != 'harbor':
            return nachsetzen

        prev_holding = self._state.get('harbor_holding')
        holding      = self._holding_for(soc)

        # Erst lernen, dann umschalten: der Lernschritt braucht den bisherigen
        # Zustand, und faellt die Bank gerade aus dem Halten, ist genau das das
        # Signal, dass die Haltespannung zu niedrig war.
        gelernt = self._lernen(soc, current_a, prev_holding, holding, landstrom, lader_a)

        if holding != prev_holding:
            self._state['harbor_holding'] = holding
            self._halte_seit = time.monotonic() if holding else None
            self._save()
            abs_v, flt_v, ein = self._harbor_profile(holding)
            log.info('Hafen: SOC=%.1f%% → %s (%.2f/%.2f V, Lader %s)', soc,
                     'Halten' if holding else 'Laden', abs_v, flt_v,
                     'ein' if ein else 'aus')
            return True
        return gelernt or nachsetzen

    # ── Selbstermittlung der Haltespannung ──────────────────────────────────
    #
    # Kein Modell, sondern eine langsame Rueckkopplung: gemessen wird der
    # Ladezustand, den die Bank bei der aktuellen Haltespannung tatsaechlich
    # annimmt, und die Spannung wird um einen kleinen Schritt nachgezogen.
    # Nachvollziehbar, ohne Trainingsdaten, und auf einem Pi Zero bezahlbar.
    #
    # Zwei Signale:
    #   1. Die Bank haelt und ist eingependelt (Strom klein, lange genug
    #      gehalten) → Ladezustand mit dem Ziel vergleichen.
    #   2. Die Bank faellt aus dem Halteband heraus, obwohl geladen werden
    #      konnte → die Haltespannung traegt nicht, einen Schritt hoch.
    #
    # Die LiFePO4-Kennlinie ist flach: mehr als etwa +-5 % ist ueber die
    # Spannung nicht zu treffen, und jeder Schritt braucht Stunden. Das
    # konvergiert ueber Tage — genau dafuer ist es gedacht.

    def _lernen(self, soc: float, current_a: float | None,
                prev: bool | None, holding: bool,
                landstrom: bool | None, lader_a: float | None) -> bool:
        """Zieht die Haltespannung nach. True = neue Sollwerte senden."""
        h = self._settings.get('harbor', {})
        if h.get('hold_auto') is not True:
            return False
        if h.get('hold_mode') not in (None, 'spannung'):
            # Nur die Haltespannung wird ermittelt. Bei abgeschaltetem Lader
            # sagt die Spannung nichts ueber den Ladezustand aus, und ein von
            # Hand gewaehltes Halteprofil soll nicht hinter dem Ruecken des
            # Eigners verschoben werden.
            return False

        ziel = _num(h.get('target_soc'), 80)
        # Traegt die Kennlinie das Ziel, wird nicht mehr geschrittelt: sie
        # korrigiert sich ueber neue Beobachtungen von selbst, und jeder
        # Schritt waere ein zusaetzlicher Schreibvorgang ins Flash des Laders.
        if self._kenn_spannung(ziel) is not None:
            return False

        jetzt = time.monotonic()
        ruhig = _num(h.get('hold_quiet_a'), 2.0)
        reif  = _num(h.get('hold_settle_h'), 3.0)
        kp    = _num(h.get('hold_kp'), 0.004)

        if holding and prev:
            if self._halte_seit is None:
                self._halte_seit = jetzt

            # Das schnelle Signal — und hier zaehlt ausschliesslich, was UNSERE
            # geregelten Lader schicken. Der Shunt-Strom der Bank enthaelt auch
            # die Lichtmaschine, den nicht geregelten Orion und jede andere
            # Quelle; als "unser Lader drueckt" gelesen, faehrt er die
            # Haltespannung beim Motoren binnen ein bis zwei Tagen an die
            # Untergrenze und der Hafen-Modus haelt danach gar nichts mehr.
            if lader_a is not None and lader_a > ruhig:
                if self._laedt_seit is None:
                    self._laedt_seit = jetzt
                elif (jetzt - self._laedt_seit) / 60.0 >= _num(h.get('hold_schnell_min'), 45.0):
                    self._laedt_seit = jetzt
                    return self._halte_schritt(
                        -1, _num(h.get('hold_kp_a'), 0.01) * lader_a,
                        'Lader drueckt beim Halten %.1f A' % lader_a)
            else:
                self._laedt_seit = None

            if (jetzt - self._halte_seit) / 3600.0 < reif:
                return False
            if current_a is None or abs(current_a) > ruhig:
                return False                       # noch nicht eingependelt
            if soc > ziel + _LERN_TOLERANZ_PCT:
                # Nach unten immer erlaubt: weniger Spannung kann nie schaden.
                return self._halte_schritt(-1, kp * (soc - ziel),
                                           'Ladezustand %.1f %% über dem Ziel' % soc)
            if soc < ziel - _LERN_TOLERANZ_PCT:
                # Nach OBEN nur, wenn ueberhaupt geladen werden kann.
                #
                # Ohne Landstrom liegt der Ladezustand beim Halten zwangslaeufig
                # unter dem Ziel — darueber hebt ihn nur ein aktiv druckender
                # Lader. Der Messwert ist dann kein Urteil ueber die
                # Haltespannung, sondern die blosse Feststellung, dass niemand
                # laedt. Wer daraus einen Schritt macht, hat ein einseitiges
                # Signal: jeder Toern ohne Steckdose schiebt die Spannung weiter
                # nach oben, bis sie an der Absorptionsspannung klebt und die
                # Bank am Steg dauerhaft nahe 100 % steht.
                if landstrom is not True:
                    return False
                return self._halte_schritt(+1, kp * (ziel - soc),
                                           'Ladezustand %.1f %% unter dem Ziel' % soc)
            return False

        if prev and not holding:
            self._laedt_seit = None
            # Aus dem Halteband gefallen — nur auswerten, wenn ueberhaupt
            # geladen werden konnte.
            #
            # Hier stand frueher ein Merker _halte_geladen, der das GEGENTEIL
            # dessen mass, was sein Kommentar behauptete: gesetzt wurde er,
            # wenn der Strom KLEIN war. Bei einer entladenden Bank mit 1,5 A
            # Grundlast trifft das immer zu, der Schutz griff also nie — und
            # ausgerechnet der Fall, fuer den er gebaut war (kein Lader da, der
            # Ladezustand faellt durch), hob die Haltespannung.
            if landstrom is not True:
                return False
            if self._halte_seit is None:
                return False
            if (jetzt - self._halte_seit) / 3600.0 < reif:
                return False
            return self._halte_schritt(+1, kp * max(ziel - soc, _LERN_TOLERANZ_PCT),
                                       'Ladezustand unter das Halteband gefallen')

        return False

    def _halte_schritt(self, richtung: int, groesse: float, grund: str) -> bool:
        """Einen Schritt gehen — mit Mindestabstand und Tageskappe.

        Jede Aenderung ist ein Schreibvorgang in das Flash des Ladegeraets.
        Deshalb zwei unabhaengige Bremsen: ein Mindestabstand zwischen zwei
        Schritten und eine feste Obergrenze je Tag. Die Kappe ist die
        wichtigere — der Mindestabstand allein liesse bei einem hartnaeckigen
        Fehler rund um die Uhr schreiben.
        """
        h = self._settings.get('harbor', {})
        heute = date.today().isoformat()
        if self._state.get('hold_learn_tag') != heute:
            self._state['hold_learn_tag']  = heute
            self._state['hold_learn_zahl'] = 0
        if _num(self._state.get('hold_learn_zahl'), 0) >= _num(h.get('hold_max_pro_tag'), 6):
            return False

        abstand = _num(h.get('hold_interval_h'), 1.0)
        letzte  = self._state.get('hold_learn_last')
        if letzte:
            try:
                seither = (datetime.now() - datetime.fromisoformat(letzte)).total_seconds() / 3600.0
            except (TypeError, ValueError):
                seither = abstand            # unlesbar → nicht blockieren
            if 0 <= seither < abstand:
                # Negatives seither heisst Uhrensprung nach hinten (der Pi hat
                # keine Echtzeituhr) — dann lieber zulassen als ewig sperren.
                return False

        # Schrittweite aus dem gemessenen Fehler, begrenzt nach unten und oben.
        # Fest waere sie in beide Richtungen falsch: bei halbem Volt Abweichung
        # zaghaft, bei einem Prozentpunkt nervoes.
        schritt = min(max(abs(groesse), _num(h.get('hold_step_v'), 0.01)),
                      _num(h.get('hold_step_max_v'), 0.10))
        alt = self._haltespannung()
        neu = self._halte_grenzen(alt + richtung * schritt)
        if abs(neu - alt) < 1e-9:
            return False                     # an der Grenze angekommen
        self._state['hold_learned_v']  = neu
        self._state['hold_learn_last'] = datetime.now().isoformat()
        self._state['hold_learn_zahl'] = int(_num(self._state.get('hold_learn_zahl'), 0)) + 1
        # Uhr neu stellen: der naechste Schritt darf erst nach erneutem
        # Einpendeln kommen, sonst rutscht die Spannung in einem Zug durch.
        self._halte_seit = time.monotonic()
        self._save()
        log.info('Haltespannung nachgefuehrt: %.2f → %.2f V (%s)', alt, neu, grund)
        return True

    def set_mode(self, mode: str) -> dict:
        if mode not in _MODI:
            raise ValueError(f'Unbekannter Modus: {mode}')
        vorher = self._state.get('mode')
        self._state['mode'] = mode
        if mode == 'balance':
            # Wohin es danach zurueckgeht, wird beim Start gemerkt: der Lauf
            # dauert Stunden, und niemand soll sich hinterher erinnern muessen,
            # wo er hergekommen ist.
            self._state['balance_start']   = datetime.now().isoformat()
            self._state['balance_zurueck'] = vorher if vorher in ('harbor', 'full') else 'harbor'
            self._state['balance_spannung'] = None
            self._state['balance_schritt']  = None
            self._balance_mono              = time.monotonic()
            self._schritt_mono              = None
            self._phase_setzen('entladen')
            log.info('Balance-Lauf gestartet, danach zurueck nach %s',
                     self._state['balance_zurueck'])
        else:
            if vorher == 'balance':
                # Von Hand aus dem Lauf heraus ist auch ein Abbruch. Ohne die
                # Sperre wuerde der automatische Start ihn binnen Sekunden neu
                # anwerfen, und der Lauf waere nicht verlassbar.
                self._state['balance_sperre'] = datetime.now().isoformat()
            self._state['balance_start']   = None
            self._state['balance_phase']   = None
            self._state['balance_zurueck'] = None
            self._state['balance_spannung'] = None
            self._balance_mono              = None
            self._phase_mono                = None
            self._schritt_mono              = None
        self._laedt_seit = None                # gilt nur im Hafen-Halten
        self._nachsetz_zeit    = None          # neuer Modus, neue Absicht
        self._nachsetz_zaehler = 0
        if mode == 'harbor':
            self._state['harbor_holding'] = None   # Hysterese neu entscheiden lassen
        self._save()
        return self.status()

    def complete_balance(self, erfolgreich: bool = True):
        """Beendet den Balance-Lauf und kehrt dorthin zurueck, wo er herkam.

        Nur ein erfolgreicher Lauf setzt das Datum: sonst gaelte ein Abbruch
        nach dem Sicherheitsdeckel als durchbalancierte Bank, und der naechste
        faellige Lauf waere einen Monat spaeter.
        """
        if erfolgreich:
            self._state['last_balance'] = date.today().isoformat()
        else:
            # Ein abgebrochener Lauf setzt das Datum nicht — ohne Sperre wuerde
            # der automatische Start ihn sofort wieder anwerfen, in einer
            # Schleife aus Abbrechen und Neustarten.
            self._state['balance_sperre'] = datetime.now().isoformat()
        zurueck = self._state.get('balance_zurueck')
        if zurueck not in ('harbor', 'full'):
            zurueck = 'harbor'
        self._state['mode']             = zurueck
        self._state['balance_start']    = None
        self._state['balance_phase']    = None
        self._state['balance_zurueck']  = None
        self._state['balance_spannung'] = None
        self._state['balance_schritt']  = None
        self._state['harbor_holding']   = None
        self._balance_mono = self._phase_mono = self._schritt_mono = None
        # Neuer Modus, neue Absicht — und die Lader muessen ihren normalen
        # Ladestrom zurueckbekommen.
        self._nachsetzen       = True
        self._nachsetz_zeit    = None
        self._nachsetz_zaehler = 0
        self._save()
        log.info('Balance-Lauf %s → Modus %s',
                 'beendet' if erfolgreich else 'abgebrochen', zurueck)

    def update_actual_setpoints(self, absorption_v: float, float_v: float):
        """Aktualisiert die vom IP43 zurückgelesenen Ist-Sollwerte.

        Läuft bei JEDEM empfangenen PGN 130914 — deshalb wird nur bei echter
        Wertänderung geschrieben. Der Lesezeitpunkt allein ist kein Schreibgrund,
        er wird nur im Speicher fortgeschrieben.
        """
        new_abs = round(absorption_v, 3)
        new_flt = round(float_v, 3)
        changed = (self._state['actual_absorption_v'] != new_abs or
                   self._state['actual_float_v'] != new_flt)
        self._state['actual_absorption_v'] = new_abs
        self._state['actual_float_v']      = new_flt
        self._state['actual_last_read']    = datetime.now().isoformat()
        if changed:
            self._save()
        self._pruefe_abweichung(new_abs, new_flt)

    def _pruefe_abweichung(self, ist_abs: float, ist_flt: float):
        """Merkt sich, wenn der Lader andere Sollwerte meldet als gewollt.

        Zurueckgelesen wird nur Instanz 1 (Smart IP43). Weicht sie ab, hat den
        Lader etwas anderes verstellt — die App, ein Werksreset, ein Stromausfall.
        Dann wird beim naechsten SOC-Takt einmal nachgesetzt. Das ersetzt ein
        periodisches Wiederholen, das sonst nur das Flash des Laders abnutzen
        wuerde.

        Ohne bekannten SOC wird nicht geprueft: device_setpoints() nimmt dann
        'Laden' an, und ein tatsaechlich haltender Lader saehe faelschlich
        verstellt aus — bei jedem Neustart ein Schreibvorgang.
        """
        if self._last_soc is None:
            return
        ziel = next((d for d in self.device_setpoints() if d.get('instance') == 1), None)
        if not ziel or ziel.get('on') is False:
            return
        if (int(round(ist_abs * 100)) == _register_stufe(ziel['absorption_v']) and
                int(round(ist_flt * 100)) == _register_stufe(ziel['float_v'])):
            if self._nachsetz_zaehler:
                log.info('Lader hat die Sollwerte uebernommen: %.2f/%.2f V', ist_abs, ist_flt)
            self._nachsetz_zaehler = 0
            return
        if self._nachsetz_zaehler >= _NACHSETZ_MAX:
            return                                   # aufgegeben, siehe _NACHSETZ_MAX
        if (self._nachsetz_zeit is not None
                and time.monotonic() - self._nachsetz_zeit < _NACHSETZ_ABSTAND_S):
            return                                   # der letzte Versuch ist zu frisch
        if not self._nachsetzen:
            log.info('Lader meldet %.2f/%.2f V, gewollt sind %.2f/%.2f V — wird nachgesetzt',
                     ist_abs, ist_flt, ziel['absorption_v'], ziel['float_v'])
        self._nachsetzen = True

    def kennlinie_zuruecksetzen(self) -> dict:
        """Wirft die gelernte Kennlinie weg und faengt von vorn an.

        Auch die nachgefuehrte Haltespannung: sie stammt aus derselben
        Beobachtung. Bliebe sie stehen, liefe der Regler nach dem Ruecksetzen
        mit einem Wert weiter, dessen Herkunft niemand mehr nachvollziehen
        kann. Was von Hand eingestellt ist, bleibt unangetastet.
        """
        anzahl = len(self._kennpunkte())
        self._state['kennpunkte']      = []
        self._state['kenn_letzte']     = None
        self._state['hold_learned_v']  = None
        self._state['hold_learn_last'] = None
        self._state['hold_learn_tag']  = None
        self._state['hold_learn_zahl'] = 0
        self._ruhig_seit = None
        self._laedt_seit = None
        self._nachsetzen = True          # die Lader bekommen die Ausgangswerte
        self._save()
        log.info('Kennlinie zurueckgesetzt (%d Faecher verworfen)', anzahl)
        return self.status()

    def update_settings(self, patch: dict) -> dict:
        """Einstellungen aendern — und die neuen Sollwerte auch hinschicken.

        Ohne das Nachsetzen bliebe eine geaenderte Absorptionsspannung im
        Ladegeraet stehen, bis zufaellig zwischen Laden und Halten gewechselt
        wird. Das kann Tage dauern.
        """
        self._settings          = self._deep_merge(self._settings, patch)
        self._settings['profile'] = self._profile_saeubern(self._settings.get('profile'))
        self._nachsetzen        = True
        self._nachsetz_zeit     = None
        self._nachsetz_zaehler  = 0
        self._save()
        return self.status()

    # ── Interne Hilfsfunktionen ───────────────────────────────────────────────

    def _balance_hours(self) -> float | None:
        """Laufzeit des aktuellen Balance-Laufs in Stunden, None wenn keiner läuft.

        Bevorzugt die monotone Uhr: der Pi hat keine Echtzeituhr, ein NTP-Sprung
        würde einen Balance-Lauf sonst verlängern oder vorzeitig beenden. Nach
        einem Neustart mitten im Lauf bleibt nur der gespeicherte Zeitstempel.
        """
        if self._state.get('mode') != 'balance':
            return None
        if self._balance_mono is not None:
            return max(0.0, (time.monotonic() - self._balance_mono) / 3600.0)
        bs = self._state.get('balance_start')
        if not bs:
            return None
        try:
            started = datetime.fromisoformat(bs)
        except (TypeError, ValueError):
            return None
        return max(0.0, (datetime.now() - started).total_seconds() / 3600.0)

    # ── Der Balance-Lauf ────────────────────────────────────────────────────
    #
    # Drei Phasen, siehe _BAL_PHASEN. Getaktet wird er aus update_soc(); der
    # Rueckgabewert sagt wie ueberall, ob neue Sollwerte an die Lader muessen.

    def _balance_faellig_starten(self, landstrom: bool | None) -> bool:
        """Startet einen faelligen Balance-Lauf von selbst — wenn erlaubt.

        Drei Bedingungen, alle notwendig: eingeschaltet, faellig, und Landstrom.
        Der Landstrom ist die wichtigste: der Lauf laesst die Bank zuerst
        leerlaufen. Ihn ohne Steckdose zu beginnen hiesse, die Bank zu entladen
        ohne sie danach fuellen zu koennen.
        """
        b = self._bal()
        if b.get('auto') is not True:
            return False
        if landstrom is not True:
            return False
        if self._days_until_balance() > 0:
            return False
        sperre = self._state.get('balance_sperre')
        if sperre and _stunden_seit(sperre) < _BAL_SPERRE_H:
            return False
        log.info('Balance-Lauf faellig und Landstrom da — automatisch gestartet')
        self.set_mode('balance')
        return True

    def _bal(self) -> dict:
        b = self._settings.get('balance')
        return b if isinstance(b, dict) else _DEFAULT_SETTINGS['balance']

    def _phase_stunden(self) -> float:
        """Wie lange die aktuelle Phase schon laeuft.

        Wie bei der Laufzeit des ganzen Laufs: die monotone Uhr zuerst, weil der
        Pi keine Echtzeituhr hat und ein NTP-Sprung eine Phase sonst verlaengern
        oder vorzeitig beenden wuerde. Nach einem Neustart mitten im Lauf bleibt
        nur der gespeicherte Zeitstempel.
        """
        if self._phase_mono is not None:
            return max(0.0, (time.monotonic() - self._phase_mono) / 3600.0)
        return _stunden_seit(self._state.get('balance_phase_seit'))

    def _phase_setzen(self, phase: str):
        self._state['balance_phase']      = phase
        self._state['balance_phase_seit'] = datetime.now().isoformat()
        self._phase_mono                  = time.monotonic()

    def _zellen_gleich(self, zelldiff_mv: float | None) -> bool:
        """Liegen die Zellen beieinander?

        Ohne Zellwerte wird NICHT weitergeschaltet. Der ganze Zweck des Laufs
        ist, dem BMS Zeit zum Ausgleichen zu geben; die Spannung zu heben, ohne
        zu wissen wie es den Zellen geht, waere genau das Gegenteil.
        """
        if zelldiff_mv is None:
            return False
        return zelldiff_mv <= _num(self._bal().get('zelldiff_mv'), 20.0)

    def _balance_takten(self, soc: float, current_a: float | None,
                        zelldiff_mv: float | None, landstrom: bool | None) -> bool:
        b = self._bal()

        # Sicherheitsdeckel ueber den ganzen Lauf. Ein Lauf, der nicht
        # konvergiert — schwache Zelle, kaputter Fuehler, Lader zu klein —, darf
        # die Bank nicht auf Dauer auf erhoehter Spannung stehen lassen.
        lauf = self._balance_hours()
        if lauf is not None and lauf > _num(b.get('max_h'), 48.0):
            log.warning('Balance-Lauf nach %.1f h abgebrochen (Deckel %.1f h) — '
                        'zurueck in den vorherigen Modus', lauf, _num(b.get('max_h'), 48.0))
            self.complete_balance(erfolgreich=False)
            return True

        phase = self._state.get('balance_phase')
        if phase not in _BAL_PHASEN:
            self._phase_setzen('entladen')
            phase = 'entladen'

        if phase == 'entladen':
            # Aktiv entladen geht nicht, es gibt keine steuerbare Last. Die
            # Lader sind aus (siehe device_setpoints), gewartet wird auf das
            # Boot selbst.
            if soc <= _num(b.get('start_soc'), 60):
                self._phase_setzen('laden')
                self._state['balance_spannung'] = round(_num(b.get('start_v'), 13.6), 3)
                self._state['balance_schritt']  = datetime.now().isoformat()
                self._schritt_mono              = time.monotonic()
                self._save()
                log.info('Balance: %.1f %% erreicht → Ladephase mit %.2f V und %.1f A',
                         soc, self._state['balance_spannung'], _num(b.get('strom_a'), 10.0))
                return True
            return False

        # Ab hier wird geladen — ohne Landstrom geht das nicht. Der Lauf wartet
        # dann, statt abzubrechen: der Landstrom kommt in aller Regel wieder.
        if landstrom is False:
            return False

        if phase == 'laden':
            if soc >= _num(b.get('ziel_soc'), 100):
                self._phase_setzen('halten')
                self._save()
                log.info('Balance: %.1f %% erreicht → halten fuer %.1f h',
                         soc, _num(b.get('halten_h'), 2.0))
                return False
            return self._balance_schritt(zelldiff_mv)

        if phase == 'halten':
            if self._phase_stunden() >= _num(b.get('halten_h'), 2.0):
                self.complete_balance()
                return True
            return False

        return False

    def _balance_schritt(self, zelldiff_mv: float | None) -> bool:
        """Hebt die Ladespannung um einen Schritt, wenn die Zellen es zulassen."""
        b     = self._bal()
        jetzt = self._state.get('balance_spannung')
        if not isinstance(jetzt, (int, float)) or isinstance(jetzt, bool):
            jetzt = _num(b.get('start_v'), 13.6)
        deckel = _num(b.get('max_v'), 14.4)
        if jetzt >= deckel:
            return False
        if not self._zellen_gleich(zelldiff_mv):
            return False
        abstand = _num(b.get('schritt_min'), 20.0) / 60.0
        if self._schritt_mono is not None:
            seither = (time.monotonic() - self._schritt_mono) / 3600.0
        else:
            seither = _stunden_seit(self._state.get('balance_schritt'))
        if seither < abstand:
            return False
        neu = round(min(deckel, jetzt + _num(b.get('schritt_v'), 0.05)), 3)
        if abs(neu - jetzt) < 1e-9:
            return False
        self._state['balance_spannung'] = neu
        self._state['balance_schritt']  = datetime.now().isoformat()
        self._schritt_mono              = time.monotonic()
        self._save()
        log.info('Balance: Zellen gleich (%.0f mV) → Spannung %.2f → %.2f V',
                 zelldiff_mv if zelldiff_mv is not None else -1.0, jetzt, neu)
        return True

    def _days_since_balance(self) -> int | None:
        lb = self._state.get('last_balance')
        if not lb:
            return None
        try:
            return (date.today() - date.fromisoformat(lb)).days
        except (TypeError, ValueError):
            return None

    def _days_until_balance(self) -> int:
        ds = self._days_since_balance()
        if ds is None:
            return 0
        interval = int(_num(self._settings.get('balance_interval_days'), 30))
        return max(0, interval - ds)

    def _preset_match(self) -> str | None:
        mode = self._state['mode']
        av   = self._state.get('actual_absorption_v')
        fv   = self._state.get('actual_float_v')
        if av is None or fv is None:
            return None
        # Gegen das, was dem Lader tatsaechlich geschickt wurde — nicht gegen das
        # Profil. Zurueckgelesen wird Instanz 1 (der IP43), und die bekommt im
        # Hafen-Laden den Solar-Versatz abgezogen. Ohne ihn stand hier waehrend
        # des ganz normalen Ladens dauerhaft "Extern geaendert".
        soll = next((d for d in self.device_setpoints() if d.get('instance') == 1), None)
        if soll is not None and self._last_soc is not None:
            if (abs(av - soll['absorption_v']) < 0.06
                    and abs(fv - soll['float_v']) < 0.06):
                return mode
            return 'custom'
        for name in ('harbor', 'full', 'balance'):
            p = self._settings.get(name, {})
            if name == 'balance':
                # Der Balance-Lauf faehrt keine feste Spannung, sondern die
                # gerade erreichte Stufe.
                a = f = _num(self._state.get('balance_spannung'),
                             _num(self._bal().get('start_v'), 13.6))
            else:
                a, f = self._profil_spannungen(p.get('profile_id'))
            if abs(av - a) < 0.06 and abs(fv - f) < 0.06:
                return name
        return 'custom'
