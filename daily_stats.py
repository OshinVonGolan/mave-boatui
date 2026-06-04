"""Tägliche Energie-Statistiken aus Shunt-Strom (genau) und SOC."""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_SAVE_INTERVAL_S = 60   # maximal alle 60 s auf Disk schreiben
_MAX_DAYS        = 14   # ältere Tage verwerfen


class DailyStatsTracker:
    """Integriert Lade-/Entladestrom vom Shunt und mittelt den SOC pro Tag.

    Wird bei jedem Shunt-Update aufgerufen (PGN 130900 / 127508).
    Daten werden als JSON auf die Disk geschrieben und beim Start geladen.
    """

    def __init__(self, path: Path):
        self._path       = path
        self._stats: dict[str, dict] = {}   # {'2026-06-01': {...}}
        self._last_ts: float | None  = None
        self._last_save: float       = 0.0
        self._load()

    # ── Persistenz ──────────────────────────────────────────────────────────

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    self._stats = json.load(f)
                log.info("Daily-Stats geladen: %d Tage", len(self._stats))
            except Exception as e:
                log.warning("Daily-Stats laden fehlgeschlagen: %s", e)
                self._stats = {}

    def save(self, force: bool = False):
        now = time.monotonic()
        if not force and (now - self._last_save) < _SAVE_INTERVAL_S:
            return
        try:
            cutoff = (datetime.now() - timedelta(days=_MAX_DAYS)).strftime('%Y-%m-%d')
            trimmed = {d: v for d, v in self._stats.items() if d >= cutoff}
            with open(self._path, 'w') as f:
                json.dump(trimmed, f)
            self._stats   = trimmed
            self._last_save = now
        except Exception as e:
            log.warning("Daily-Stats speichern fehlgeschlagen: %s", e)

    # ── Update ───────────────────────────────────────────────────────────────

    def update(self, current_a: float | None, soc: float | None, ts: float | None = None):
        """Aktualisiert tägliche Stats mit aktuellem Shunt-Strom und SOC.

        current_a: Shunt-Strom in A (positiv = Laden, negativ = Entladen)
        soc:       SOC in % (0–100)
        ts:        Unix-Timestamp; falls None wird time.time() verwendet
        """
        now = ts or time.time()
        day = datetime.fromtimestamp(now).strftime('%Y-%m-%d')

        if day not in self._stats:
            self._stats[day] = {
                'charged_ah':    0.0,
                'discharged_ah': 0.0,
                'soc_sum':       0.0,
                'soc_count':     0,
            }
        d = self._stats[day]

        # Strom integrieren — nur wenn sinnvolles Delta (0 < dt < 5 min)
        if self._last_ts is not None and current_a is not None:
            dt_h = (now - self._last_ts) / 3600.0
            if 0 < dt_h < 300 / 3600:
                if current_a > 0:
                    d['charged_ah']    += current_a * dt_h
                elif current_a < 0:
                    d['discharged_ah'] += abs(current_a) * dt_h

        if soc is not None:
            d['soc_sum']   += soc
            d['soc_count'] += 1

        self._last_ts = now
        self.save()

    # ── Abfrage ──────────────────────────────────────────────────────────────

    def get_last_n_days(self, n: int = 7) -> list[dict]:
        """Gibt die letzten n Tage als Liste zurück (ältester zuerst)."""
        result = []
        today = datetime.now().date()
        for i in range(n - 1, -1, -1):
            day = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            d   = self._stats.get(day, {})
            cnt = d.get('soc_count', 0)
            result.append({
                'date':          day,
                'charged_ah':    round(d.get('charged_ah',    0.0), 1),
                'discharged_ah': round(d.get('discharged_ah', 0.0), 1),
                'avg_soc':       round(d['soc_sum'] / cnt, 1) if cnt > 0 else None,
            })
        return result
