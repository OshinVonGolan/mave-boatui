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
        'hold_step_v':        0.02,  # Schrittweite je Anpassung
        'hold_settle_h':      3.0,   # so lange muss gehalten worden sein
        'hold_quiet_a':       2.0,   # |Strom| darunter gilt die Bank als eingependelt
        'hold_interval_h':   24.0,   # hoechstens ein Schritt in diesem Abstand
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


# Ab dieser Abweichung zwischen gewollten und zurückgelesenen Sollwerten gilt
# der Lader als verstellt und bekommt sie erneut geschickt.
#
# Eine halbe Registerstufe. Die Sollwert-Register des Laders rechnen in 0,01 V,
# und der Weg dorthin ist verlustfrei: der Pi schickt Millivolt, das Gateway
# rundet auf 0,01 V, der Lader meldet genau das zurück. Was hier ankommt, weicht
# also entweder gar nicht ab oder um mindestens eine ganze Stufe. Die halbe
# Stufe faengt nur die Rundung ab, wenn der eingestellte Wert selbst feiner ist
# als 0,01 V (13,833 minus Solar-Versatz).
#
# Vorher standen hier 0,05 V, und genau daran ist die Regelung am Boot
# vorbeigelaufen: gewollt waren 13,50 V, im Lader standen 13,48 V, und die
# 0,02 V Unterschied galten als "passt schon". Da zugleich kein Wechsel
# zwischen Laden und Halten stattfand, wurde nie geschrieben — der Lader stand
# tagelang auf einem Wert, den niemand gesetzt hatte.
_SOLL_TOLERANZ_V = 0.005

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

# Totband der Selbstermittlung in SOC-Prozentpunkten. Enger lohnt nicht: die
# LiFePO4-Kennlinie ist so flach, dass ein Prozentpunkt bereits im Rauschen der
# SOC-Schaetzung des BMS liegt.
_LERN_TOLERANZ_PCT = 1.0


class ChargeController:
    def __init__(self):
        self._last_soc: float | None = None
        self._nachsetzen: bool = False               # Rückmeldung wich ab → neu senden
        self._halte_seit: float | None = None        # monoton, Beginn des aktuellen Haltens
        self._halte_geladen: bool = False            # waehrend des Haltens floss Ladung
        self._nachsetz_zeit: float | None = None     # monoton, letzter Nachsetz-Versuch
        self._nachsetz_zaehler: int = 0              # vergebliche Versuche in Folge
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
        """Zweistufiger Merge: Schlüssel der zweiten Ebene werden pro Eintrag gemergt."""
        result = {}
        for k, v in base.items():
            if k not in overlay:
                result[k] = v
                continue
            ov = overlay[k]
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
        gelernt = self._state.get('hold_learned_v')
        if isinstance(gelernt, bool) or not isinstance(gelernt, (int, float)):
            return self._halte_grenzen(manuell)
        return self._halte_grenzen(float(gelernt))

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
                   landstrom: bool | None = None) -> bool:
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
        gelernt = self._lernen(soc, current_a, prev_holding, holding)

        if holding != prev_holding:
            self._state['harbor_holding'] = holding
            self._halte_seit    = time.monotonic() if holding else None
            self._halte_geladen = False
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
                prev: bool | None, holding: bool) -> bool:
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

        jetzt = time.monotonic()
        ruhig = _num(h.get('hold_quiet_a'), 2.0)
        reif  = _num(h.get('hold_settle_h'), 3.0)

        if holding and prev:
            if self._halte_seit is None:
                self._halte_seit = jetzt
                return False
            if current_a is not None and abs(current_a) <= ruhig:
                # Der Lader traegt die Last, die Bank wird nicht entladen.
                self._halte_geladen = True
            if (jetzt - self._halte_seit) / 3600.0 < reif:
                return False
            if current_a is None or abs(current_a) > ruhig:
                return False                       # noch nicht eingependelt
            ziel = _num(h.get('target_soc'), 80)
            if soc > ziel + _LERN_TOLERANZ_PCT:
                return self._halte_schritt(-1, 'Ladezustand %.1f %% über dem Ziel' % soc)
            if soc < ziel - _LERN_TOLERANZ_PCT:
                return self._halte_schritt(+1, 'Ladezustand %.1f %% unter dem Ziel' % soc)
            return False

        if prev and not holding:
            # Aus dem Halteband gefallen. Nur auswerten, wenn waehrend des
            # Haltens ueberhaupt Ladung floss — sonst war schlicht kein
            # Landstrom da, und die Haltespannung kann nichts dafuer.
            if self._halte_seit is None or not self._halte_geladen:
                return False
            if (jetzt - self._halte_seit) / 3600.0 < reif:
                return False
            return self._halte_schritt(+1, 'Ladezustand unter das Halteband gefallen')

        return False

    def _halte_schritt(self, richtung: int, grund: str) -> bool:
        """Einen Schritt gehen, sofern der Mindestabstand eingehalten ist."""
        h       = self._settings.get('harbor', {})
        abstand = _num(h.get('hold_interval_h'), 24.0)
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
        alt = self._haltespannung()
        neu = self._halte_grenzen(alt + richtung * _num(h.get('hold_step_v'), 0.02))
        if abs(neu - alt) < 1e-9:
            return False                     # an der Grenze angekommen
        self._state['hold_learned_v']  = neu
        self._state['hold_learn_last'] = datetime.now().isoformat()
        # Uhr neu stellen: der naechste Schritt darf erst nach erneutem
        # Einpendeln kommen, sonst rutscht die Spannung in einem Zug durch.
        self._halte_seit = time.monotonic()
        self._save()
        log.info('Haltespannung selbst ermittelt: %.2f → %.2f V (%s)', alt, neu, grund)
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
            self._state['balance_start']   = None
            self._state['balance_phase']   = None
            self._state['balance_zurueck'] = None
            self._state['balance_spannung'] = None
            self._balance_mono              = None
            self._phase_mono                = None
            self._schritt_mono              = None
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
        if (abs(ist_abs - ziel['absorption_v']) <= _SOLL_TOLERANZ_V and
                abs(ist_flt - ziel['float_v']) <= _SOLL_TOLERANZ_V):
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
        # Hafen-Modus: gegen das gerade gueltige Profil pruefen, nicht gegen die
        # Voreinstellung — beim Halten stehen andere Werte im Lader als beim Laden.
        if mode == 'harbor' and self._last_soc is not None:
            ziel_abs, ziel_flt, _ = self._harbor_profile(self._holding_for(self._last_soc))
            if abs(av - ziel_abs) < 0.06 and abs(fv - ziel_flt) < 0.06:
                return 'harbor'
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
