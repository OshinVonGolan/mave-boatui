"""Alarm-Engine: prüft State gegen konfigurierte Regeln, verwaltet aktive Alarme."""
import logging
import math
import time
import uuid
from collections import deque
from pathlib import Path

from jsonio import read_json, write_json

log = logging.getLogger(__name__)

ALARMS_FILE = Path(__file__).parent / 'alarms.json'

# Die Alarmliste ist reine Anzeige-Historie und geht bei jedem Broadcast über den
# WebSocket — sie darf nicht unbegrenzt wachsen. Harte Obergrenze plus Verfallsfrist
# für aufgelöste Einträge, mehr Verwaltung braucht es dafür nicht.
_MAX_ALARMS       = 200          # älteste Einträge fallen hinten aus der Liste
_RESOLVED_TTL_S   = 24 * 3600    # aufgelöste Alarme nach 24 h verwerfen
_PRUNE_INTERVAL_S = 60           # nicht bei jedem Broadcast aufräumen

# Aus dem PATCH-Endpunkt übernehmbare Regelfelder. name/field/op/unit/step/bounds
# beschreiben die Regel selbst und kommen ausschließlich aus alarms.json — sonst
# könnte ein Patch die Regel strukturell zerlegen.
_PATCHABLE_FIELDS = ('enabled', 'threshold', 'min', 'max', 'severity')
_SEVERITIES       = ('info', 'warning', 'critical')
_FALLBACK_BOUNDS  = (-100000.0, 100000.0)   # wenn die Regel keine 'bounds' mitbringt


def _get_field(state: dict, field: str):
    val = state
    for part in field.split('.'):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


def _as_number(value, allow_bool: bool = False) -> float | None:
    """Endliche Zahl oder None.

    Bools zählen nur dort als Zahl, wo der Aufrufer es erlaubt: die BMS-Alarmflags
    im State sind bool, ein Schwellwert aus dem PATCH-Endpunkt darf es nie sein
    (sonst wäre `true` ein gültiger Grenzwert von 1).
    """
    if isinstance(value, bool):
        return float(value) if allow_bool else None
    if not isinstance(value, (int, float)):
        return None
    num = float(value)
    return num if math.isfinite(num) else None


def _rule_bounds(rule: dict) -> tuple[float, float]:
    """Zulässiger Wertebereich einer Regel: 'bounds' aus alarms.json, sonst großzügig."""
    b = rule.get('bounds')
    if isinstance(b, (list, tuple)) and len(b) == 2:
        lo, hi = _as_number(b[0]), _as_number(b[1])
        if lo is not None and hi is not None and lo < hi:
            return lo, hi
    return _FALLBACK_BOUNDS


def _evaluate(rule: dict, val) -> bool | None:
    """True/False = Bedingung (nicht) erfüllt, None = Regel nicht auswertbar.

    Eine unbrauchbare Regel darf die Prüfung nicht abbrechen: check() läuft bei
    jedem Broadcast, eine Ausnahme hier würde den kompletten Broadcast mitreißen.
    """
    num = _as_number(val, allow_bool=True)   # BMS-Alarmflags sind bool
    if num is None:
        return None
    op = rule.get('op')
    if op == 'range':
        lo = _as_number(rule.get('min'))
        hi = _as_number(rule.get('max'))
        if lo is None or hi is None:
            return None
        return num < lo or num > hi
    if op in ('<', '>'):
        thr = _as_number(rule.get('threshold'))
        if thr is None:
            return None
        return num < thr if op == '<' else num > thr
    return None


class AlarmEngine:
    def __init__(self):
        self._rules:      dict  = {}
        self._alarms            = deque(maxlen=_MAX_ALARMS)   # alle nicht gelöschten Alarme
        self._active:     dict  = {}     # key → alarm-id (aktuell ausgelöste Bedingungen)
        self._data_seen:  set   = set()  # keys die mindestens einmal Daten hatten
        self._alert_cb          = None   # Callback(key, active) für kritische Alarme (NMEA-Buzzer)
        self._broken:     set   = set()  # keys deren Regel bereits als unbrauchbar gemeldet wurde
        self._last_prune: float = 0.0
        self._load()

    def _load(self):
        data  = read_json(ALARMS_FILE, {})
        rules = data.get('rules') if isinstance(data, dict) else None
        self._rules = rules if isinstance(rules, dict) else {}

    def _save(self):
        write_json(ALARMS_FILE, {'rules': self._rules})

    # ── Kritischer-Alarm-Hook (NMEA-2000-Buzzer via main.py) ─────────────────

    def set_alert_callback(self, cb):
        """cb(key:str, active:bool) wird bei kritischen Alarmen aufgerufen
        (active=True auslösen, active=False zurücknehmen). Die Engine sendet
        selbst KEIN CAN — main.py verdrahtet den Callback mit can_if.send_alert."""
        self._alert_cb = cb

    def _fire_alert(self, key: str, active: bool):
        if self._alert_cb:
            try:
                self._alert_cb(key, active)
            except Exception:
                pass

    # ── Regeln ──────────────────────────────────────────────────────────────

    def get_rules(self) -> dict:
        return {k: {**v, 'data_available': k in self._data_seen} for k, v in self._rules.items()}

    def update_rules(self, updates: dict) -> dict:
        """Übernimmt geprüfte Regelwerte aus dem PATCH-Endpunkt.

        Unbekannte Regeln, nicht patchbare Felder und unplausible Werte werden
        verworfen statt gespeichert: ein kaputter Wert würde sonst dauerhaft in
        alarms.json stehen und die Alarmprüfung bei jedem Broadcast stören.
        """
        if not isinstance(updates, dict):
            return self.get_rules()
        changed = False
        for key, patch in updates.items():
            rule = self._rules.get(key)
            if not isinstance(rule, dict) or not isinstance(patch, dict):
                log.warning("Alarmregel '%s': unbekannt oder ungültiger Patch — ignoriert", key)
                continue
            clean = _validate_patch(key, rule, patch)
            if clean:
                rule.update(clean)
                self._broken.discard(key)   # evtl. repariert → erneut melden dürfen
                changed = True
        if changed:
            self._save()
        return self.get_rules()

    # ── State prüfen ─────────────────────────────────────────────────────────

    def check(self, state: dict) -> bool:
        """Prüft alle Regeln gegen den aktuellen State. Gibt True zurück wenn sich was geändert hat."""
        changed = False
        now     = time.time()

        for key, rule in self._rules.items():
            if not isinstance(rule, dict):
                continue
            field = rule.get('field')
            val   = _get_field(state, field) if isinstance(field, str) else None

            if val is not None:
                self._data_seen.add(key)

            if not rule.get('enabled'):
                if key in self._active:
                    self._active.pop(key)
                    changed = True
                continue
            if val is None:
                continue  # Datenlücke: aktiven Alarm nicht löschen

            triggered = _evaluate(rule, val)
            if triggered is None:
                # Regel oder Wert unbrauchbar → Regel überspringen, nicht die Prüfung abbrechen
                if key not in self._broken:
                    self._broken.add(key)
                    log.warning("Alarmregel '%s' nicht auswertbar (op=%r, Wert=%r) — übersprungen",
                                key, rule.get('op'), val)
                continue

            severity = rule.get('severity', 'warning')

            if triggered and key not in self._active:
                alarm = {
                    'id':           uuid.uuid4().hex[:8],
                    'key':          key,
                    'name':         rule.get('name', key),
                    'value':        round(val, 2),
                    'threshold':    rule.get('threshold') if rule.get('op') != 'range' else None,
                    'min':          rule.get('min'),
                    'max':          rule.get('max'),
                    'op':           rule.get('op'),
                    'severity':     severity,
                    'timestamp':    now,
                    'acknowledged': False,
                    'resolved':     False,
                    'resolved_at':  None,
                }
                self._alarms.append(alarm)
                self._active[key] = alarm['id']
                changed = True
                if severity == 'critical':
                    self._fire_alert(key, True)

            elif not triggered and key in self._active:
                aid = self._active.pop(key)
                for a in self._alarms:
                    if a['id'] == aid:
                        a['resolved']    = True
                        a['resolved_at'] = now   # Startpunkt der Verfallsfrist
                        break
                changed = True
                if severity == 'critical':
                    self._fire_alert(key, False)

            elif triggered and key in self._active:
                aid = self._active[key]
                for a in self._alarms:
                    if a['id'] == aid:
                        new_val = round(val, 2)
                        if a['value'] != new_val:
                            a['value'] = new_val
                            changed = True
                        break

        if now - self._last_prune >= _PRUNE_INTERVAL_S:
            self._last_prune = now
            if self._prune(now):
                changed = True

        return changed

    def _prune(self, now: float) -> bool:
        """Verwirft abgelaufene aufgelöste Alarme und hält _active konsistent.

        Zweiter Teil ist wichtig: läuft die Liste in die Obergrenze, fällt der
        älteste Eintrag heraus — steht der noch in _active, würde die Bedingung
        nie wieder auslösen. Solche Waisen werden hier gelöst.
        """
        keep_ids = set(self._active.values())
        kept = [a for a in self._alarms
                if not a.get('resolved')
                or a['id'] in keep_ids
                or (now - (a.get('resolved_at') or a.get('timestamp') or now)) < _RESOLVED_TTL_S]

        changed = len(kept) != len(self._alarms)
        if changed:
            self._alarms = deque(kept, maxlen=_MAX_ALARMS)

        live_ids = {a['id'] for a in self._alarms}
        for key in [k for k, aid in self._active.items() if aid not in live_ids]:
            self._active.pop(key)
            changed = True
        return changed

    # ── Alarme abrufen ───────────────────────────────────────────────────────

    def get_alarms(self) -> list:
        return list(self._alarms)

    @property
    def unack_count(self) -> int:
        return sum(1 for a in self._alarms if not a.get('acknowledged') and not a.get('resolved'))

    # ── Aktionen ─────────────────────────────────────────────────────────────

    def acknowledge(self, alarm_id: str) -> bool:
        for a in self._alarms:
            if a['id'] == alarm_id:
                a['acknowledged'] = True
                if a.get('severity') == 'critical' and not a.get('resolved'):
                    self._fire_alert(a['key'], False)   # Buzzer quittieren
                return True
        return False

    def acknowledge_all(self):
        for a in self._alarms:
            a['acknowledged'] = True
            if a.get('severity') == 'critical' and not a.get('resolved'):
                self._fire_alert(a['key'], False)

    def delete(self, alarm_id: str) -> bool:
        before = len(self._alarms)
        self._alarms = deque((a for a in self._alarms if a['id'] != alarm_id), maxlen=_MAX_ALARMS)
        if alarm_id in self._active.values():
            self._active = {k: v for k, v in self._active.items() if v != alarm_id}
        return len(self._alarms) < before

    def delete_all_resolved(self):
        keep_ids = set(self._active.values())
        self._alarms = deque(
            (a for a in self._alarms if not a.get('resolved') or a['id'] in keep_ids),
            maxlen=_MAX_ALARMS,
        )


def _validate_patch(key: str, rule: dict, patch: dict) -> dict:
    """Filtert einen Regel-Patch auf übernehmbare Felder mit plausiblen Werten."""
    lo, hi = _rule_bounds(rule)
    clean: dict = {}

    for field, raw in patch.items():
        if field not in _PATCHABLE_FIELDS:
            continue   # strukturelle Felder kommen aus alarms.json, nicht aus dem Netz
        if field == 'enabled':
            if isinstance(raw, bool):
                clean['enabled'] = raw
            else:
                log.warning("Alarmregel '%s': enabled=%r ist kein Wahrheitswert — ignoriert", key, raw)
            continue
        if field == 'severity':
            if raw in _SEVERITIES:
                clean['severity'] = raw
            else:
                log.warning("Alarmregel '%s': severity=%r unbekannt — ignoriert", key, raw)
            continue
        num = _as_number(raw)
        if num is None:
            log.warning("Alarmregel '%s': %s=%r ist keine Zahl — ignoriert", key, field, raw)
            continue
        if not lo <= num <= hi:
            log.warning("Alarmregel '%s': %s=%s außerhalb %s…%s — ignoriert", key, field, num, lo, hi)
            continue
        clean[field] = num

    # Bei Bereichsregeln muss min < max bleiben, sonst schlägt die Regel dauerhaft an
    if rule.get('op') == 'range' and ('min' in clean or 'max' in clean):
        new_min = _as_number(clean.get('min', rule.get('min')))
        new_max = _as_number(clean.get('max', rule.get('max')))
        if new_min is None or new_max is None or new_min >= new_max:
            log.warning("Alarmregel '%s': min=%r muss unter max=%r liegen — beide ignoriert",
                        key, clean.get('min', rule.get('min')), clean.get('max', rule.get('max')))
            clean.pop('min', None)
            clean.pop('max', None)

    return clean
