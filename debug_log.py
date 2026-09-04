"""Ein Mitschnitt der letzten 24 Stunden — zum Nachsehen, nicht zum Zuschauen.

Der Anlass: Der Eigner meldet "die Schaltflächen sind ausgegraut", und der
Fehler ist eine Stunde später nicht mehr da. Ohne Mitschnitt bleibt nur Raten,
und Raten kostet mehr Zeit als jedes Protokoll.

Warum nicht einfach journalctl? Das gibt es, aber es hilft hier wenig:

  - Es steht nur auf dem Pi und nur per SSH zur Verfügung. Wer aus der Ferne
    nachsieht, kommt nicht heran.
  - Es kennt die Oberfläche nicht. Ein Fehler im Browser des Eigners taucht
    dort nie auf.
  - Es ist voll mit Zugriffszeilen, in denen das Wesentliche untergeht.

Deshalb ein eigener Ring: begrenzt auf 24 Stunden und auf eine feste Zahl von
Einträgen, im Arbeitsspeicher gehalten und in Abständen auf die Platte
geschrieben. Auf einer SD-Karte ist jeder Schreibvorgang einer zu viel — aber
ein Mitschnitt, der beim Absturz verschwindet, wäre genau dann nutzlos, wenn
man ihn braucht.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

DAUER_S = 24 * 3600
# Eine harte Obergrenze zusätzlich zur Zeit: bei einem Fehler, der sich
# tausendmal je Minute wiederholt, wäre der Ring sonst nur noch voll davon —
# und der eine interessante Eintrag von vorhin herausgefallen.
MAX_EINTRAEGE = 4000
SICHERN_ALLE_S = 120


class DebugLog:
    """Ringpuffer für Meldungen aus Bordrechner und Oberfläche."""

    def __init__(self, pfad: str | Path):
        self._pfad = Path(pfad)
        self._lock = threading.Lock()
        self._ring: deque = deque(maxlen=MAX_EINTRAEGE)
        self._zuletzt_gesichert = 0.0
        self._laden()

    # ── Schreiben ───────────────────────────────────────────────────────────

    def merken(self, art: str, quelle: str, text: str, daten: dict | None = None) -> None:
        """Eine Meldung ablegen.

        `art` ist eine der üblichen Stufen (info, warnung, fehler), `quelle`
        sagt, WOHER sie kommt (bord, oberflaeche, sync, heizung …). Beides
        zusammen macht den Mitschnitt filterbar — ohne das wäre er eine Wand
        aus Text.
        """
        eintrag = {
            'zeit': time.time(),
            'art': str(art)[:12],
            'quelle': str(quelle)[:24],
            'text': str(text)[:600],
        }
        if daten:
            # Begrenzt: ein versehentlich mitgegebener Zustandsbaum würde den
            # Ring sonst mit einer einzigen Meldung füllen.
            eintrag['daten'] = json.loads(json.dumps(daten, default=str)[:1200]) \
                if len(json.dumps(daten, default=str)) <= 1200 else {'gekuerzt': True}
        with self._lock:
            self._ring.append(eintrag)
        self._vielleicht_sichern()

    def _vielleicht_sichern(self) -> None:
        jetzt = time.time()
        if jetzt - self._zuletzt_gesichert < SICHERN_ALLE_S:
            return
        self._zuletzt_gesichert = jetzt
        threading.Thread(target=self.sichern, daemon=True).start()

    def sichern(self) -> None:
        try:
            with self._lock:
                daten = list(self._ring)
            tmp = self._pfad.with_suffix(self._pfad.suffix + '.neu')
            tmp.write_text(json.dumps(daten, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self._pfad)
        except OSError as e:
            log.warning('Mitschnitt nicht gesichert: %s', e)

    # ── Lesen ───────────────────────────────────────────────────────────────

    def holen(self, *, seit: float | None = None, art: str = '',
              quelle: str = '', suche: str = '', grenze: int = 500) -> list[dict]:
        grenze_zeit = seit if seit is not None else (time.time() - DAUER_S)
        such = (suche or '').lower()
        with self._lock:
            alle = list(self._ring)
        raus = []
        for e in reversed(alle):            # neueste zuerst
            if e['zeit'] < grenze_zeit:
                break                       # der Ring ist zeitlich sortiert
            if art and e['art'] != art:
                continue
            if quelle and e['quelle'] != quelle:
                continue
            if such and such not in e['text'].lower() and such not in e['quelle'].lower():
                continue
            raus.append(e)
            if len(raus) >= grenze:
                break
        return raus

    def quellen(self) -> list[str]:
        with self._lock:
            return sorted({e['quelle'] for e in self._ring})

    def anzahl(self) -> int:
        with self._lock:
            return len(self._ring)

    # ── Platte ──────────────────────────────────────────────────────────────

    def _laden(self) -> None:
        try:
            daten = json.loads(self._pfad.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if not isinstance(daten, list):
            return
        grenze = time.time() - DAUER_S
        with self._lock:
            for e in daten:
                if isinstance(e, dict) and e.get('zeit', 0) >= grenze:
                    self._ring.append(e)
        log.info('Mitschnitt geladen: %d Einträge', len(self._ring))

    def aufraeumen(self) -> None:
        """Was älter als 24 Stunden ist, fliegt heraus.

        Regelmäßig aufzurufen. Ohne das läge nach einem stillen Tag der
        Mitschnitt von gestern noch da und täuschte Aktualität vor.
        """
        grenze = time.time() - DAUER_S
        with self._lock:
            while self._ring and self._ring[0].get('zeit', 0) < grenze:
                self._ring.popleft()


class RingHandler(logging.Handler):
    """Hängt sich in das Protokoll des Bordrechners und schreibt in den Ring.

    So landet alles, was ohnehin protokolliert wird, auch im Mitschnitt — ohne
    dass an fünfzig Stellen ein zweiter Aufruf stehen muss. Nur ab `WARNING`:
    unterhalb davon ist es Betriebsgeräusch, und der Ring soll die interessanten
    Meldungen halten, nicht die häufigen.
    """

    def __init__(self, ring: DebugLog):
        super().__init__(level=logging.WARNING)
        self._ring = ring

    def emit(self, record: logging.LogRecord) -> None:
        try:
            art = ('fehler' if record.levelno >= logging.ERROR else 'warnung')
            self._ring.merken(art, record.name.split('.')[-1][:24], record.getMessage())
        except Exception:
            pass          # ein Protokoll darf niemals den Betrieb stören
