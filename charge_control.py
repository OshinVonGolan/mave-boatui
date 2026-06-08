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
    'harbor':  {'absorption_v': 13.8, 'float_v': 13.3},
    'full':    {'absorption_v': 14.4, 'float_v': 13.5},
    'balance': {'absorption_v': 14.4, 'float_v': 13.5},
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
        """Zweistufiger Merge: Schlüssel der zweiten Ebene werden pro Eintrag gemergt.
        Bewahrt unveränderliche Felder wie is_solar/label/instance in devices-Einträgen.
        """
        result = {}
        for k, v in base.items():
            if k not in overlay:
                result[k] = v
                continue
            ov = overlay[k]
            if isinstance(v, dict) and isinstance(ov, dict):
                # Zweite Ebene: jeden Unter-Schlüssel einzeln mergen
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

    # ── Lese-API ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            'mode':                self._state['mode'],
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
        Solar-Geräte erhalten den vollen Preset-Wert.
        Nicht-Solar-Geräte erhalten Absorption − solar_priority_offset_v (nur Hafen-Modus).
        """
        mode   = self._state['mode']
        preset = self._settings.get(mode, self._settings['harbor'])
        abs_v  = preset['absorption_v']
        flt_v  = preset['float_v']
        # Offset nur im Hafen-Modus sinnvoll — bei Vollladung/Balance alle gleich
        offset = self._settings.get('solar_priority_offset_v', 0.3) if mode == 'harbor' else 0.0

        result = []
        for dev_id, dev in self._settings.get('devices', {}).items():
            if not dev.get('enabled', False):
                continue
            is_solar = dev.get('is_solar', False)
            eff_abs  = round(abs_v - (0.0 if is_solar else offset), 3)
            eff_flt  = round(flt_v - (0.0 if is_solar else offset), 3)
            result.append({
                'id':           dev_id,
                'label':        dev.get('label', dev_id),
                'instance':     dev.get('instance', 1),
                'is_solar':     is_solar,
                'absorption_v': eff_abs,
                'float_v':      eff_flt,
            })
        return result

    # ── Schreibe-API ─────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> dict:
        if mode not in ('harbor', 'full', 'balance'):
            raise ValueError(f'Unbekannter Modus: {mode}')
        self._state['mode'] = mode
        if mode == 'balance':
            self._state['balance_start'] = datetime.now().isoformat()
        else:
            self._state['balance_start'] = None
        self._save()
        return self.status()

    def complete_balance(self):
        """Nach erfolgreichem Balance-Abschluss aufrufen → setzt last_balance + wechselt zu Hafen."""
        self._state['last_balance']  = date.today().isoformat()
        self._state['balance_start'] = None
        self._state['mode']          = 'harbor'
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
        av = self._state.get('actual_absorption_v')
        fv = self._state.get('actual_float_v')
        if av is None or fv is None:
            return None
        for name in ('harbor', 'full', 'balance'):
            p = self._settings.get(name, {})
            if (abs(av - p.get('absorption_v', 0)) < 0.06 and
                    abs(fv - p.get('float_v', 0)) < 0.06):
                return name
        return 'custom'
