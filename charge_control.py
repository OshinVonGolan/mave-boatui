"""Intelligente Ladesteuerung — unterstützt IP43, MPPT und Orion XS."""
import json
import logging
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_STATE_FILE = Path(__file__).parent / 'charger_state.json'

_DEFAULT_SETTINGS: dict = {
    'balance_interval_days':   30,
    'balance_min_hours':        2.0,
    'balance_end_current_a':    1.0,
    'solar_priority_offset_v':  0.3,   # Nicht-Solar-Geräte bekommen Absorption − Offset
    'devices': {
        'ip43':  {'enabled': True,  'is_solar': False, 'label': 'Smart IP43',  'instance': 1},
        'mppt':  {'enabled': True,  'is_solar': True,  'label': 'MPPT 75/15',  'instance': 3},
        'orion': {'enabled': False, 'is_solar': False, 'label': 'Orion XS',    'instance': 0},
    },
    'harbor':  {
        'absorption_v':  13.8,   # Maximalspannung beim Laden
        'float_v':       13.3,   # Float (nur für Preset-Match-Erkennung)
        'hold_voltage':  13.2,   # P-Regler-Nullpunkt: Ruhespannung bei Ziel-SOC (LiFePO4 ~13.2 V)
        'soc_ramp_pct':  15,     # % SOC unter Ziel bis volle Absorption einsetzt
        'target_soc':    80,     # Ziel-SOC (%)
        'off_voltage':   11.5,   # Spannung wenn SOC ≥ Ziel → 0 A (unter Batterieruhespannung)
    },
    'full':    {'absorption_v': 14.4, 'float_v': 13.5},
    'balance': {'absorption_v': 14.4, 'float_v': 14.4},   # CV: Float = Absorption → kein Float-Abfall
}

_DEFAULT_STATE: dict = {
    'mode':                'harbor',
    'last_balance':        None,
    'balance_start':       None,
    'actual_absorption_v': None,
    'actual_float_v':      None,
    'actual_last_read':    None,
}


class ChargeController:
    def __init__(self):
        self._last_soc: float | None = None
        self._last_applied_soc: float | None = None   # SOC bei letztem Setpoint-Senden
        self._load()

    # ── Persistenz ──────────────────────────────────────────────────────────

    def _load(self):
        if _STATE_FILE.exists():
            try:
                data = json.loads(_STATE_FILE.read_text())
                self._state    = {**_DEFAULT_STATE,    **data.get('state',    {})}
                self._settings = self._deep_merge(_DEFAULT_SETTINGS, data.get('settings', {}))
                return
            except Exception as e:
                log.warning('charger_state.json Ladefehler: %s', e)
        self._state    = dict(_DEFAULT_STATE)
        self._settings = self._deep_merge(_DEFAULT_SETTINGS, {})

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
        _STATE_FILE.write_text(
            json.dumps({'state': self._state, 'settings': self._settings}, indent=2)
        )

    # ── P-Regler Hafen-Modus ────────────────────────────────────────────────

    def _harbor_voltage(self, soc: float) -> float:
        """P-Regler: berechnet Absorptionsspannung basierend auf SOC.

        SOC ≥ Ziel          → off_voltage (unter Ruhespannung → 0 A)
        Ziel-ramp ≤ SOC < Ziel → linearer Anstieg hold_voltage → absorption_v
        SOC < Ziel-ramp     → absorption_v (volle Ladung)
        """
        h      = self._settings.get('harbor', {})
        target = h.get('target_soc',    80)
        abs_v  = h.get('absorption_v', 13.8)
        hold_v = h.get('hold_voltage', 13.2)
        off_v  = h.get('off_voltage',  11.5)
        ramp   = max(1, h.get('soc_ramp_pct', 15))

        if soc >= target:
            return off_v
        t = min(1.0, (target - soc) / ramp)   # 0 bei Ziel, 1 bei Ziel−ramp
        return round(hold_v + t * (abs_v - hold_v), 3)

    # ── Lese-API ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        harbor_v = None
        if self._state['mode'] == 'harbor' and self._last_soc is not None:
            harbor_v = self._harbor_voltage(self._last_soc)
        return {
            'mode':                self._state['mode'],
            'harbor_voltage':      harbor_v,
            'last_balance':        self._state['last_balance'],
            'balance_start':       self._state['balance_start'],
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
            soc   = self._last_soc
            eff_v = self._harbor_voltage(soc) if soc is not None else preset['absorption_v']
            off_v = self._settings['harbor'].get('off_voltage', 11.5)
            holding = eff_v <= off_v + 0.01   # SOC über Ziel → Lader ausschalten
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

    def update_soc(self, soc: float | None) -> bool:
        """Aktualisiert SOC. Gibt True zurück wenn neue Setpoints gesendet werden sollen.

        Im Hafen-Modus: bei jeder SOC-Änderung ≥ 0.5 % → P-Regler neu berechnen.
        """
        if soc is None:
            return False
        self._last_soc = soc
        if self._state['mode'] != 'harbor':
            return False
        if self._last_applied_soc is None or abs(soc - self._last_applied_soc) >= 0.5:
            old_v = (self._harbor_voltage(self._last_applied_soc)
                     if self._last_applied_soc is not None else None)
            new_v = self._harbor_voltage(soc)
            self._last_applied_soc = soc
            if old_v is None or abs(new_v - old_v) >= 0.01:
                log.info('Hafen-P-Regler: SOC=%.1f%% → %.3f V', soc, new_v)
            return True
        return False

    def set_mode(self, mode: str) -> dict:
        if mode not in ('harbor', 'full', 'balance'):
            raise ValueError(f'Unbekannter Modus: {mode}')
        self._state['mode'] = mode
        if mode == 'balance':
            self._state['balance_start'] = datetime.now().isoformat()
        else:
            self._state['balance_start'] = None
        if mode == 'harbor':
            self._last_applied_soc = None   # sofort neu berechnen bei nächstem SOC
        self._save()
        return self.status()

    def complete_balance(self):
        """Nach erfolgreichem Balance-Abschluss aufrufen → setzt last_balance + wechselt zu Hafen."""
        self._state['last_balance']  = date.today().isoformat()
        self._state['balance_start'] = None
        self._state['mode']          = 'harbor'
        self._last_applied_soc       = None
        self._save()

    def update_actual_setpoints(self, absorption_v: float, float_v: float):
        """Aktualisiert die vom IP43 zurückgelesenen Ist-Sollwerte."""
        self._state['actual_absorption_v'] = round(absorption_v, 3)
        self._state['actual_float_v']      = round(float_v, 3)
        self._state['actual_last_read']    = datetime.now().isoformat()
        self._save()

    def update_settings(self, patch: dict) -> dict:
        self._settings = self._deep_merge(self._settings, patch)
        self._save()
        return self.status()

    # ── Interne Hilfsfunktionen ───────────────────────────────────────────────

    def _days_since_balance(self) -> int | None:
        lb = self._state.get('last_balance')
        if not lb:
            return None
        return (date.today() - date.fromisoformat(lb)).days

    def _days_until_balance(self) -> int:
        ds = self._days_since_balance()
        if ds is None:
            return 0
        return max(0, self._settings['balance_interval_days'] - ds)

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
