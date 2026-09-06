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
        'absorption_v':      13.8,   # Maximalspannung beim Laden
        'float_v':           13.3,   # Float (nur für Preset-Match-Erkennung)
        'hold_voltage':      13.2,   # P-Regler-Nullpunkt: Ruhespannung bei Ziel-SOC (LiFePO4 ~13.2 V)
        'soc_ramp_pct':      15,     # % SOC unter Ziel bis volle Absorption einsetzt
        'target_soc':        80,     # Ziel-SOC (%)
        'soc_hysteresis_pct': 3,     # Hysterese: Wiedereinschalten erst bei Ziel − diesem Wert
        'off_voltage':       11.5,   # Spannung wenn gehalten wird → 0 A (unter Batterieruhespannung)
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


class ChargeController:
    def __init__(self):
        self._last_soc: float | None = None
        self._last_applied_soc: float | None = None  # SOC bei letztem Setpoint-Senden
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
        ramp   = max(1.0, _num(h.get('soc_ramp_pct'), 15))
        # Die Hysterese kommt ungeprueft aus PATCH /api/charger/settings. Sie darf
        # weder das Rampenfenster ueberschreiten (dort will der Regler bereits
        # volle Absorption) noch _MAX_HYSTERESIS_PCT. Ohne diese Grenze schaltet
        # ein Wert >= Ziel-SOC den Lader nie wieder ein: der SOC faellt dann bis
        # zur BMS-Abschaltung, also Tiefentladung.
        hyst   = min(max(0.0, _num(h.get('soc_hysteresis_pct'), 3)), ramp, _MAX_HYSTERESIS_PCT)
        if soc >= target:
            return True
        if soc <= target - hyst:
            return False
        return bool(prev) if prev is not None else False   # unbekannt im Band → laden

    def _harbor_voltage(self, soc: float, holding: bool | None = None) -> float:
        """P-Regler: berechnet Absorptionsspannung basierend auf SOC.

        Halten (Ziel-SOC erreicht) → off_voltage (unter Ruhespannung → 0 A)
        Ziel-ramp ≤ SOC < Ziel     → linearer Anstieg hold_voltage → absorption_v
        SOC < Ziel-ramp            → absorption_v (volle Ladung)

        holding=None ermittelt den Halte-Zustand selbst (rein lesend, ohne die
        Hysterese umzuschalten) — so darf status() jederzeit rechnen.
        """
        h      = self._settings.get('harbor', {})
        target = _num(h.get('target_soc'),    80)
        abs_v  = _num(h.get('absorption_v'), 13.8)
        hold_v = _num(h.get('hold_voltage'), 13.2)
        off_v  = _num(h.get('off_voltage'),  11.5)
        ramp   = max(1.0, _num(h.get('soc_ramp_pct'), 15))

        if holding is None:
            holding = self._holding_for(soc)
        if holding:
            return off_v
        t = min(1.0, max(0.0, (target - soc) / ramp))   # 0 bei Ziel, 1 bei Ziel−ramp
        return round(hold_v + t * (abs_v - hold_v), 3)

    # ── Lese-API ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        harbor_v = None
        if self._state['mode'] == 'harbor' and self._last_soc is not None:
            harbor_v = self._harbor_voltage(self._last_soc)
        return {
            'mode':                self._state['mode'],
            'harbor_voltage':      harbor_v,
            'harbor_holding':      self._state.get('harbor_holding'),
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

        Hafen-Modus: P-Regler-Spannung, Solar-Priorität nur beim aktiven Laden.
        Balance-Modus: Float = Absorption → Konstantspannung (CV).
        """
        mode   = self._state['mode']
        preset = self._settings.get(mode, self._settings['harbor'])

        if mode == 'harbor':
            soc     = self._last_soc
            holding = self._holding_for(soc) if soc is not None else False
            eff_v   = self._harbor_voltage(soc, holding) if soc is not None else preset['absorption_v']
            offset  = self._settings.get('solar_priority_offset_v', 0.3) if not holding else 0.0
            # Normaler Charge-Voltage wenn eingeschaltet, sonst Referenzwert (spielt keine Rolle)
            charge_v = preset['absorption_v'] if holding else eff_v
            result  = []
            for dev_id, dev in self._settings.get('devices', {}).items():
                if not dev.get('enabled', False):
                    continue
                is_solar = dev.get('is_solar', False)
                v = round(charge_v - (0.0 if is_solar else offset), 3) if not holding else charge_v
                result.append({
                    'id':           dev_id,
                    'label':        dev.get('label', dev_id),
                    'instance':     dev.get('instance', 1),
                    'is_solar':     is_solar,
                    'absorption_v': v,
                    'float_v':      v,
                    'on':           not holding,   # False → DeviceMode=0 (aus), True → DeviceMode=1 (ein)
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

        Hafen-Modus: bei SOC-Änderung ≥ 0.5 % oder beim Umschalten Laden↔Halten.
        Balance-Modus: prüft die Abbruchbedingungen; ist der Lauf fertig, wechselt
        der Regler selbst in den Hafen-Modus und meldet True.

        current_a ist optional: das Schweifstrom-Kriterium greift nur, wenn der
        Aufrufer den Batteriestrom mitgibt. Ohne ihn beendet allein die Höchstdauer
        balance_max_hours den Balance-Lauf.
        """
        if soc is None:
            return False
        self._last_soc = soc

        mode = self._state['mode']
        if mode == 'balance':
            return self._check_balance_end(soc, current_a)
        if mode != 'harbor':
            return False

        prev_holding = self._state.get('harbor_holding')
        holding      = self._holding_for(soc)
        flipped      = holding != prev_holding

        old_v = (self._harbor_voltage(self._last_applied_soc, prev_holding)
                 if self._last_applied_soc is not None and prev_holding is not None else None)

        if flipped:
            self._state['harbor_holding'] = holding
            self._save()
            log.info('Hafen-Hysterese: SOC=%.1f%% → %s', soc, 'Halten' if holding else 'Laden')

        if flipped or self._last_applied_soc is None or abs(soc - self._last_applied_soc) >= 0.5:
            new_v = self._harbor_voltage(soc, holding)
            self._last_applied_soc = soc
            if old_v is None or abs(new_v - old_v) >= 0.01:
                log.info('Hafen-P-Regler: SOC=%.1f%% → %.3f V', soc, new_v)
            return True
        return False

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
            self._last_applied_soc        = None   # sofort neu berechnen bei nächstem SOC
            self._state['harbor_holding'] = None   # Hysterese neu entscheiden lassen
        self._save()
        return self.status()

    def complete_balance(self):
        """Nach erfolgreichem Balance-Abschluss aufrufen → setzt last_balance + wechselt zu Hafen."""
        self._state['last_balance']   = date.today().isoformat()
        self._state['balance_start']  = None
        self._state['mode']           = 'harbor'
        self._state['harbor_holding'] = None
        self._last_applied_soc        = None
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
        # Hafen-Modus: Soll-Spannung ändert sich kontinuierlich → P-Regler-Wert prüfen
        if mode == 'harbor' and self._last_soc is not None:
            harbor_v = self._harbor_voltage(self._last_soc)
            if abs(av - harbor_v) < 0.1 and abs(fv - harbor_v) < 0.1:
                return 'harbor'
            return 'custom'
        for name in ('harbor', 'full', 'balance'):
            p = self._settings.get(name, {})
            if (abs(av - p.get('absorption_v', 0)) < 0.06 and
                    abs(fv - p.get('float_v', 0)) < 0.06):
                return name
        return 'custom'
