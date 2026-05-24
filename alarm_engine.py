"""Alarm-Engine: prüft State gegen konfigurierte Regeln, verwaltet aktive Alarme."""
import json
import time
import uuid
from pathlib import Path

ALARMS_FILE = Path(__file__).parent / 'alarms.json'


def _get_field(state: dict, field: str):
    val = state
    for part in field.split('.'):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


class AlarmEngine:
    def __init__(self):
        self._rules:   dict = {}
        self._alarms:  list = []   # alle nicht gelöschten Alarme
        self._active:  dict = {}   # key → alarm-id (aktuell ausgelöste Bedingungen)
        self._load()

    def _load(self):
        if ALARMS_FILE.exists():
            self._rules = json.loads(ALARMS_FILE.read_text()).get('rules', {})

    def _save(self):
        ALARMS_FILE.write_text(json.dumps({'rules': self._rules}, indent=2, ensure_ascii=False))

    # ── Regeln ──────────────────────────────────────────────────────────────

    def get_rules(self) -> dict:
        return self._rules

    def update_rules(self, updates: dict) -> dict:
        for key, patch in updates.items():
            if key in self._rules:
                self._rules[key].update(patch)
        self._save()
        return self._rules

    # ── State prüfen ─────────────────────────────────────────────────────────

    def check(self, state: dict) -> bool:
        """Prüft alle Regeln gegen den aktuellen State. Gibt True zurück wenn sich was geändert hat."""
        changed = False

        for key, rule in self._rules.items():
            val = _get_field(state, rule['field'])

            if not rule.get('enabled') or val is None:
                if key in self._active:
                    self._active.pop(key)
                    changed = True
                continue

            op        = rule['op']
            threshold = rule['threshold']
            triggered = (op == '<' and val < threshold) or (op == '>' and val > threshold)

            if triggered and key not in self._active:
                alarm = {
                    'id':           uuid.uuid4().hex[:8],
                    'key':          key,
                    'name':         rule['name'],
                    'value':        round(val, 2),
                    'threshold':    threshold,
                    'op':           op,
                    'severity':     rule['severity'],
                    'timestamp':    time.time(),
                    'acknowledged': False,
                    'resolved':     False,
                }
                self._alarms.append(alarm)
                self._active[key] = alarm['id']
                changed = True

            elif not triggered and key in self._active:
                aid = self._active.pop(key)
                for a in self._alarms:
                    if a['id'] == aid:
                        a['resolved'] = True
                        break
                changed = True

            elif triggered and key in self._active:
                aid = self._active[key]
                for a in self._alarms:
                    if a['id'] == aid:
                        new_val = round(val, 2)
                        if a['value'] != new_val:
                            a['value'] = new_val
                            changed = True
                        break

        return changed

    # ── Alarme abrufen ───────────────────────────────────────────────────────

    def get_alarms(self) -> list:
        return list(self._alarms)

    @property
    def unack_count(self) -> int:
        return sum(1 for a in self._alarms if not a['acknowledged'] and not a['resolved'])

    # ── Aktionen ─────────────────────────────────────────────────────────────

    def acknowledge(self, alarm_id: str) -> bool:
        for a in self._alarms:
            if a['id'] == alarm_id:
                a['acknowledged'] = True
                return True
        return False

    def acknowledge_all(self):
        for a in self._alarms:
            a['acknowledged'] = True

    def delete(self, alarm_id: str) -> bool:
        before = len(self._alarms)
        self._alarms = [a for a in self._alarms if a['id'] != alarm_id]
        if alarm_id in self._active.values():
            self._active = {k: v for k, v in self._active.items() if v != alarm_id}
        return len(self._alarms) < before

    def delete_all_resolved(self):
        keep_ids = set(self._active.values())
        self._alarms = [a for a in self._alarms if not a['resolved'] or a['id'] in keep_ids]
