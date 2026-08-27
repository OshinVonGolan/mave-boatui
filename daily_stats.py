"""Tägliche Energie-Statistiken aus Shunt-Strom (genau) und SOC."""
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from jsonio import read_json, write_json

log = logging.getLogger(__name__)

_SAVE_INTERVAL_S = 60   # maximal alle 60 s auf Disk schreiben
_MAX_DAYS        = 30   # ältere Tage verwerfen — deckt die API-Grenze (days ≤ 30) ab

# Der Pi hat keine Echtzeituhr. Nach einem Stromausfall läuft die Wanduhr falsch,
# bis NTP sie stellt — der Sprung würde sonst Ah unter einem falschen Datum verbuchen.
_MIN_PLAUSIBLE_TS = 1_735_689_600.0   # 2025-01-01 UTC: darunter ist die Uhr sicher ungestellt
_MAX_STEP_S       = 300.0             # längere Lücken werden nicht integriert
_JUMP_TOLERANCE_S = 5.0               # erlaubter Versatz Wanduhr ↔ monotone Uhr je Schritt
_WARN_INTERVAL_S  = 300.0             # Updates kommen mehrmals pro Sekunde → Log nicht fluten


class DailyStatsTracker:
    """Integriert Lade-/Entladestrom vom Shunt und mittelt den SOC pro Tag.

    Wird bei jedem Shunt-Update aufgerufen (PGN 130900 / 127508) — also aus dem
    CAN-Thread heraus. Daten werden als JSON auf die Disk geschrieben und beim
    Start geladen.
    """

    def __init__(self, path: Path):
        self._path       = path
        self._stats: dict[str, dict] = {}   # {'2026-06-01': {...}}
        self._last_ts:   float | None = None   # Wanduhr des letzten Messwerts
        self._last_mono: float | None = None   # monotone Uhr desselben Messwerts
        self._last_save: float        = 0.0
        self._warned: dict[str, float] = {}
        self._load()

    # ── Persistenz ──────────────────────────────────────────────────────────

    def _load(self):
        data = read_json(self._path, {})
        if isinstance(data, dict):
            self._stats = {d: v for d, v in data.items() if isinstance(v, dict)}
            log.info("Daily-Stats geladen: %d Tage", len(self._stats))
        else:
            self._stats = {}

    def _trimmed(self) -> dict:
        """Auf _MAX_DAYS begrenzen: nach Datum und zusätzlich hart nach Anzahl.

        Die Datumsgrenze wird nur bei gestellter Uhr angewandt — sonst würde ein
        Zeitsprung nach vorn den kompletten Bestand wegräumen. Die Anzahlgrenze
        greift immer und fängt Tage unter falschem Datum ab.
        """
        stats = self._stats
        wall  = time.time()
        if wall >= _MIN_PLAUSIBLE_TS:
            cutoff = (datetime.fromtimestamp(wall) - timedelta(days=_MAX_DAYS)).strftime('%Y-%m-%d')
            stats  = {d: v for d, v in stats.items() if d >= cutoff}
        if len(stats) > _MAX_DAYS:
            stats = {d: stats[d] for d in sorted(stats)[-_MAX_DAYS:]}
        return stats

    def save(self, force: bool = False):
        now = time.monotonic()
        if not force and (now - self._last_save) < _SAVE_INTERVAL_S:
            return
        trimmed = self._trimmed()
        try:
            # Läuft im CAN-Thread: write_json ersetzt die Datei atomar (tmp, fsync,
            # replace) — ein Absturz mittendrin hinterlässt keine halbe Datei.
            write_json(self._path, trimmed)
        except Exception as e:
            log.warning("Daily-Stats speichern fehlgeschlagen: %s", e)
            return
        self._stats     = trimmed
        self._last_save = now

    # ── Update ───────────────────────────────────────────────────────────────

    def update(self, current_a: float | None, soc: float | None, ts: float | None = None):
        """Aktualisiert tägliche Stats mit aktuellem Shunt-Strom und SOC.

        current_a: Shunt-Strom in A (positiv = Laden, negativ = Entladen)
        soc:       SOC in % (0–100)
        ts:        Unix-Timestamp; falls None wird time.time() verwendet

        Bei ungestellter Uhr wird der Messwert komplett verworfen, bei einem
        erkannten Zeitsprung nur der Integrationsschritt über den Sprung hinweg.
        """
        now  = time.time() if ts is None else ts
        mono = time.monotonic()

        # Uhr ungestellt (Stromausfall, NTP noch nicht gelaufen): weder Ah noch SOC
        # dürfen gebucht werden — sie landeten unter einem erfundenen Tag.
        if not isinstance(now, (int, float)) or isinstance(now, bool) or now < _MIN_PLAUSIBLE_TS:
            self._warn_once('unset_clock',
                            "Systemzeit unplausibel (%r) — Messwert verworfen", now)
            self._last_ts   = None
            self._last_mono = None
            return

        # Zeitsprung erkennen: Wanduhr und monotone Uhr müssen gleich weit gelaufen
        # sein. Weichen sie ab (NTP-Korrektur), ist das Intervall unbrauchbar.
        jumped = False
        if self._last_ts is not None and self._last_mono is not None:
            dt_wall = now - self._last_ts
            dt_mono = mono - self._last_mono
            if dt_wall <= 0 or abs(dt_wall - dt_mono) > _JUMP_TOLERANCE_S:
                jumped = True
                self._warn_once(
                    'time_jump',
                    "Zeitsprung erkannt (Wanduhr %.1f s vs. monoton %.1f s) — "
                    "Integrationsschritt verworfen", dt_wall, dt_mono)

        day = datetime.fromtimestamp(now).strftime('%Y-%m-%d')
        d   = self._stats.get(day)
        if d is None:
            d = self._stats[day] = {
                'charged_ah':    0.0,
                'discharged_ah': 0.0,
                'soc_sum':       0.0,
                'soc_count':     0,
            }

        # Strom integrieren — nur bei sinnvollem, unverfälschtem Delta (0 < dt < 5 min)
        if not jumped and self._last_ts is not None and current_a is not None:
            dt_s = now - self._last_ts
            if 0 < dt_s < _MAX_STEP_S:
                dt_h = dt_s / 3600.0
                if current_a > 0:
                    d['charged_ah']    += current_a * dt_h
                elif current_a < 0:
                    d['discharged_ah'] += abs(current_a) * dt_h

        if soc is not None:
            d['soc_sum']   += soc
            d['soc_count'] += 1

        self._last_ts   = now
        self._last_mono = mono
        self.save()

    def _warn_once(self, kind: str, msg: str, *args):
        """Warnt höchstens alle 5 Minuten je Ursache — update() läuft sehr häufig."""
        now = time.monotonic()
        if now - self._warned.get(kind, float('-inf')) < _WARN_INTERVAL_S:
            return
        self._warned[kind] = now
        log.warning(msg, *args)

    # ── Abfrage ──────────────────────────────────────────────────────────────

    def get_last_n_days(self, n: int = 7) -> list[dict]:
        """Gibt die letzten n Tage als Liste zurück (ältester zuerst).

        Tage ohne Daten liefern null statt 0.0: nur so kann das Frontend eine
        echte Null (Boot lag still) von einer Lücke (Pi war aus) unterscheiden.
        """
        result = []
        today  = datetime.now().date()
        for i in range(max(0, int(n)) - 1, -1, -1):
            day = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            d   = self._stats.get(day)
            if d is None:
                result.append({
                    'date':          day,
                    'charged_ah':    None,
                    'discharged_ah': None,
                    'avg_soc':       None,
                })
                continue
            cnt = d.get('soc_count', 0)
            result.append({
                'date':          day,
                'charged_ah':    round(d.get('charged_ah',    0.0), 1),
                'discharged_ah': round(d.get('discharged_ah', 0.0), 1),
                'avg_soc':       round(d.get('soc_sum', 0.0) / cnt, 1) if cnt > 0 else None,
            })
        return result
