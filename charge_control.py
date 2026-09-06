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
    'balance_interval_days':   30,
    'balance_min_hours':        2.0,   # frühestens danach darf Balancing enden
    'balance_max_hours':        8.0,   # spätestens danach endet es in jedem Fall
    'balance_target_soc':     100,     # ab diesem SOC gilt die Bank als durchbalanciert
    'balance_end_current_a':    1.0,   # Schweifstrom-Kriterium (nur mit übergebenem Strom)
    'solar_priority_offset_v':  0.3,   # Nicht-Solar-Geräte bekommen Absorption − Offset
    'devices': {
        'ip43':  {'enabled': True,  'is_solar': False, 'label': 'Smart IP43',  'instance': 1},
        'mppt':  {'enabled': True,  'is_solar': True,  'label': 'MPPT 75/15',  'instance': 3},
        'orion': {'enabled': False, 'is_solar': False, 'label': 'Orion XS',    'instance': 0},
    },
    'harbor':  {
        'absorption_v':      13.8,   # Ladeprofil: Absorption bis der Ziel-SOC steht
        'float_v':           13.3,   # Ladeprofil: Erhaltung
        'hold_voltage':      13.2,   # Halteprofil: Absorption = Erhaltung = dieser Wert
        'target_soc':        80,     # Ziel-SOC (%)
        'soc_hysteresis_pct': 3,     # Hysterese: zurück ins Laden erst bei Ziel − diesem Wert
        # Wie am Ziel-SOC gehalten wird:
        #   'spannung' — Ladespannung auf hold_voltage, Lader bleibt an. Der Lader
        #                hört von selbst auf zu drücken, der Ladezyklus startet
        #                nicht neu, und ein ausgefallener Pi hinterlässt einen
        #                harmlosen Zustand statt eines abgeschalteten Laders.
        #   'aus'      — Lader abschalten. Harter Stopp, aber jedes
        #                Wiedereinschalten beginnt erneut mit Bulk.
        'hold_mode':         'spannung',
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
    'full':    {'absorption_v': 14.4, 'float_v': 13.5},
    'balance': {'absorption_v': 14.4, 'float_v': 14.4},   # CV: Float = Absorption → kein Float-Abfall
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
}


# Obergrenze fuer die Hafen-Hysterese in SOC-Prozentpunkten. Groesser darf sie
# nicht werden, sonst bleibt der Lader zu tief hinunter aus.
_MAX_HYSTERESIS_PCT = 20.0


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
_SOLL_TOLERANZ_V = 0.05

# Zulässige Werte für harbor.hold_mode.
_HALTEARTEN = ('spannung', 'aus')

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
        self._settings = self._deep_merge(_DEFAULT_SETTINGS,
                                          settings if isinstance(settings, dict) else {})

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

    def _haltespannung(self) -> float:
        """Die Spannung, auf der im Hafen-Modus gehalten wird.

        Entweder die eingestellte oder die selbst ermittelte — umschaltbar über
        harbor.hold_auto. Solange noch nichts ermittelt wurde, gilt die
        eingestellte als Startwert; die Selbstermittlung geht von dort aus.
        """
        h       = self._settings.get('harbor', {})
        manuell = _num(h.get('hold_voltage'), 13.2)
        if h.get('hold_auto') is not True:
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
        oben  = _num(h.get('absorption_v'), 13.8)
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
        abs_v = _num(h.get('absorption_v'), 13.8)
        flt_v = _num(h.get('float_v'),      13.3)
        if not holding:
            return abs_v, flt_v, True
        art = h.get('hold_mode')
        if art not in _HALTEARTEN:
            art = 'spannung'
        if art == 'aus':
            # Sollwerte unverändert stehen lassen, nur abschalten — so muss beim
            # Wiedereinschalten nichts ins Flash zurückgeschrieben werden.
            return abs_v, flt_v, False
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
            'hold_learned_v':      self._state.get('hold_learned_v'),
            'hold_learn_last':     self._state.get('hold_learn_last'),
            'last_balance':        self._state['last_balance'],
            'balance_start':       self._state['balance_start'],
            'balance_hours':       self._balance_hours(),
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
                })
            return result

        abs_v  = preset['absorption_v']
        flt_v  = preset['float_v']
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
            })
        return result

    # ── Schreibe-API ─────────────────────────────────────────────────────────

    def update_soc(self, soc: float | None, current_a: float | None = None) -> bool:
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
        self._nachsetzen = False

        mode = self._state['mode']
        if mode == 'balance':
            return self._check_balance_end(soc, current_a) or nachsetzen
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
        if h.get('hold_mode') == 'aus':
            # Bei abgeschaltetem Lader sagt die Spannung nichts ueber den
            # Ladezustand aus — dann gibt es auch nichts zu lernen.
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
        self._state['mode'] = mode
        if mode == 'balance':
            self._state['balance_start'] = datetime.now().isoformat()
            self._balance_mono           = time.monotonic()
        else:
            self._state['balance_start'] = None
            self._balance_mono           = None
        if mode == 'harbor':
            self._state['harbor_holding'] = None   # Hysterese neu entscheiden lassen
        self._save()
        return self.status()

    def complete_balance(self):
        """Nach erfolgreichem Balance-Abschluss aufrufen → setzt last_balance + wechselt zu Hafen."""
        self._state['last_balance']   = date.today().isoformat()
        self._state['balance_start']  = None
        self._state['mode']           = 'harbor'
        self._state['harbor_holding'] = None
        self._balance_mono            = None
        self._save()

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
            return
        if not self._nachsetzen:
            log.info('Lader meldet %.2f/%.2f V, gewollt sind %.2f/%.2f V — wird nachgesetzt',
                     ist_abs, ist_flt, ziel['absorption_v'], ziel['float_v'])
        self._nachsetzen = True

    def update_settings(self, patch: dict) -> dict:
        self._settings = self._deep_merge(self._settings, patch)
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

    def _check_balance_end(self, soc: float, current_a: float | None) -> bool:
        """Beendet den Balance-Modus, sobald die Bank durch ist oder die Zeit reicht.

        Ohne Abbruch bleibt die Bank dauerhaft auf 14,4 V CV und 'balance_due'
        immer wahr. Reihenfolge der Kriterien:
          • Höchstdauer balance_max_hours überschritten → beenden, immer
          • ab balance_min_hours: Ziel-SOC erreicht UND Ladestrom unter
            balance_end_current_a (nur wenn der Strom übergeben wurde)
        Gibt True zurück, wenn der Modus gewechselt hat (→ Setpoints neu senden).
        """
        hours = self._balance_hours()
        if hours is None:
            # Modus ohne Startzeitpunkt (z. B. aus einer alten Zustandsdatei) → nachtragen
            self._state['balance_start'] = datetime.now().isoformat()
            self._balance_mono           = time.monotonic()
            self._save()
            return False

        max_h = max(0.1, _num(self._settings.get('balance_max_hours'), 8.0))
        min_h = max(0.0, _num(self._settings.get('balance_min_hours'), 2.0))

        reason = None
        if hours >= max_h:
            reason = f'Höchstdauer {max_h:.1f} h erreicht'
        elif hours >= min_h:
            target = _num(self._settings.get('balance_target_soc'), 100)
            end_a  = _num(self._settings.get('balance_end_current_a'), 1.0)
            # Der Ziel-SOC allein beendet den Lauf NICHT: 100 % stehen am ANFANG
            # der CV-Phase auf der Anzeige, die Zellen sind dann noch lange nicht
            # durchbalanciert. Erst der Schweifstrom zeigt, dass die Bank voll ist.
            # Gibt der Aufrufer keinen Strom mit, bleibt nur die Höchstdauer.
            if current_a is not None and soc >= target and 0.0 <= current_a < end_a:
                reason = (f'Ziel-SOC {target:.0f} % erreicht und '
                          f'Schweifstrom unter {end_a:.1f} A')

        if reason is None:
            return False

        log.info('Balancing nach %.1f h beendet: %s → Hafen-Modus', hours, reason)
        self.complete_balance()
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
            if (abs(av - p.get('absorption_v', 0)) < 0.06 and
                    abs(fv - p.get('float_v', 0)) < 0.06):
                return name
        return 'custom'
