"""Persistenz des Batterie-Verlaufs über Neustarts hinweg.

Der Verlauf lag bisher nur im RAM (deque) und war nach jedem Neustart weg.
Hier wird er als NDJSON (eine JSON-Zeile je Eintrag) angehängt und beim Start
wieder eingelesen.

Randbedingungen des Pi Zero W:
  * SD-Karte → NICHT bei jedem Eintrag fsync(), sondern gepuffert schreiben und
    nur alle paar Sekunden anfügen. Der Seiten-Cache des Kernels erledigt den
    Rest; bei einem harten Stromausfall fehlen höchstens die letzten Sekunden.
  * Ein Kern → geschrieben wird in einem eigenen Thread, der Event-Loop fasst
    die Datei nie an. ``append()`` legt den Eintrag nur in einen RAM-Puffer.
  * Wenig RAM → beim Start wird die Datei VON HINTEN gelesen, nur so weit wie
    nötig; eine große Altdatei wird nicht komplett geparst.
"""
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

_TAIL_BLOCK = 256 * 1024   # Blockgröße beim Rückwärtslesen


class HistoryStore:
    """Append-only-NDJSON-Speicher für Verlaufseinträge (thread-sicher)."""

    def __init__(self, path: Path,
                 retention_s: float = 16 * 3600,
                 max_entries: int = 10800,
                 flush_interval_s: float = 20.0,
                 rotate_age_s: float = 24 * 3600,
                 max_bytes: int = 4 * 1024 * 1024):
        self._path             = Path(path)
        # Fortlaufende Nummer je Eintrag. Sie ist das, was der Server zum
        # Nachliefern braucht ("ich habe bis N, schick ab N+1") — Zeitstempel
        # taugen dafuer nicht, weil der Pi ohne gestellte Uhr hochlaeuft.
        # Vergeben wird sie beim Anhaengen, ermittelt beim Laden aus der Datei.
        self._folge            = 0
        self._retention_s      = retention_s
        self._max_entries      = max_entries
        self._flush_interval_s = flush_interval_s
        self._rotate_age_s     = rotate_age_s
        self._max_bytes        = max_bytes
        # Puffer begrenzt: sollte das Schreiben dauerhaft scheitern, darf der
        # Speicher nicht volllaufen (512 MB RAM gesamt).
        self._buf: deque       = deque(maxlen=4 * max_entries)
        self._lock             = threading.Lock()
        self._stop             = threading.Event()
        self._thread: threading.Thread | None = None
        self._oldest_ts: float | None = None   # ältester Zeitstempel IN der Datei

    # ── Laden ───────────────────────────────────────────────────────────────

    def load(self) -> list[dict]:
        """Liest die letzten ``retention_s`` Sekunden aus der Datei (ältester zuerst).

        Blockierend — beim Start bewusst über einen Executor aufrufen.
        Fehler werden geschluckt: ohne Verlauf startet der Dienst trotzdem.
        """
        if not self._path.exists():
            return []
        try:
            entries = self._read_tail(self._retention_s, self._max_entries)
        except Exception as e:
            log.warning('Verlauf laden fehlgeschlagen: %s', e)
            return []
        self._oldest_ts = self._read_first_ts()
        # Weiterzaehlen, wo die Datei aufhoert. Ohne das begaenne die Zaehlung
        # nach jedem Neustart wieder bei eins, und der Server haette
        # Folgenummern, die es zweimal gibt.
        def ganzzahlig(x) -> bool:
            return isinstance(x, int) and not isinstance(x, bool)

        for e in reversed(entries):
            if ganzzahlig(e.get('n')):
                self._folge = max(self._folge, e['n'])
                break

        # Eintraege mit krummer oder fehlender Nummer bekommen eine gueltige.
        # Solche entstanden, weil die Minutenmittelung `n` wie einen Messwert
        # behandelte und Kommazahlen daraus machte (1476.5). Jede Abfrage
        # uebersprang sie stillschweigend — der Server bekam deshalb ueber Tage
        # ueberhaupt keinen Verlauf, ohne dass irgendwo ein Fehler auftauchte.
        if any(not ganzzahlig(e.get('n')) for e in entries):
            self._folge = self._nummern_reparieren()
            entries = self._read_tail(self._retention_s, self._max_entries)

        if entries:
            log.info('Verlauf geladen: %d Einträge über %.1f h',
                     len(entries), (entries[-1]['ts'] - entries[0]['ts']) / 3600.0)
        return entries

    def _nummern_reparieren(self) -> int:
        """Die Folgenummern der ganzen Datei einmalig geradeziehen.

        Noetig geworden, weil die Minutenmittelung `n` wie einen Messwert
        behandelte und Kommazahlen daraus machte (1476.5). Solche Eintraege
        uebersprang jede Abfrage stillschweigend — der Server bekam ueber Tage
        keinen Verlauf, ohne dass irgendwo ein Fehler auftauchte.

        Es waere billiger, beim Lesen einfach zu runden. Das wuerde aber
        Nummern doppelt vergeben (1476.5 und 1477 werden beide zu 1477), und
        der Server erkennt Luecken genau an diesen Nummern. Deshalb wird die
        Datei wirklich umgeschrieben — einmalig, beim Start, atomar.

        Gibt die hoechste vergebene Nummer zurueck.
        """
        neu = self._path.with_suffix(self._path.suffix + '.neu')
        folge, geaendert, gesamt = 0, 0, 0
        try:
            with open(self._path, 'r', encoding='utf-8') as alt, \
                 open(neu, 'w', encoding='utf-8') as ziel:
                for zeile in alt:
                    zeile = zeile.strip()
                    if not zeile:
                        continue
                    try:
                        e = json.loads(zeile)
                    except Exception:
                        continue          # kaputte Zeile faellt weg
                    gesamt += 1
                    folge += 1
                    if e.get('n') != folge:
                        geaendert += 1
                    e['n'] = folge
                    ziel.write(json.dumps(e, ensure_ascii=False) + '\n')
                ziel.flush()
                os.fsync(ziel.fileno())
            os.replace(neu, self._path)
            log.warning('Folgenummern im Verlauf neu vergeben: %d von %d Einträgen '
                        'berichtigt (krumme Nummern aus der Minutenmittelung)',
                        geaendert, gesamt)
            return folge
        except OSError as e:
            log.error('Folgenummern konnten nicht berichtigt werden: %s', e)
            try:
                neu.unlink()
            except OSError:
                pass
            return self._folge

    def _read_first_ts(self) -> float | None:
        """Zeitstempel der ersten Zeile — nur dafür da, das Alter der Datei zu kennen."""
        try:
            with open(self._path, 'rb') as f:
                line = f.readline(4096)
            entry = json.loads(line)
            ts = entry.get('ts')
            return float(ts) if isinstance(ts, (int, float)) else None
        except Exception:
            return None

    def _read_tail(self, max_age_s: float, max_entries: int) -> list[dict]:
        """Liest die Datei blockweise von hinten, bis alt genug oder genug Einträge.

        Bricht beim ersten Eintrag ab, der älter als das Zeitfenster ist. Die
        Einträge stehen chronologisch in der Datei; springt die Uhr (der Pi hat
        keine Echtzeituhr), wird im Zweifel weniger geladen — nie mehr.
        """
        cutoff = time.time() - max_age_s
        out: list[dict] = []           # neueste zuerst
        with open(self._path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            pos  = f.tell()
            rest = b''
            while pos > 0 and len(out) < max_entries:
                size = min(_TAIL_BLOCK, pos)
                pos -= size
                f.seek(pos)
                chunk = f.read(size) + rest
                lines = chunk.split(b'\n')
                # Die erste Zeile eines Blocks ist unvollständig, solange noch
                # etwas davor liegt — sie wandert in den nächsten Durchlauf.
                rest = lines.pop(0) if pos > 0 else b''
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue   # abgeschnittene/kaputte Zeile überspringen
                    ts = entry.get('ts')
                    if not isinstance(ts, (int, float)):
                        continue
                    if ts < cutoff:
                        out.reverse()
                        return out
                    out.append(entry)
                    if len(out) >= max_entries:
                        break
        out.reverse()
        return out

    # ── Schreiben ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Startet den Schreib-Thread (idempotent)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._writer_loop, daemon=True,
                                        name='history-store')
        self._thread.start()

    def append(self, entry: dict) -> None:
        """Nimmt einen Eintrag entgegen — reine RAM-Operation, blockiert nie."""
        with self._lock:
            self._folge += 1
            # setdefault, nicht zuweisen: ein Eintrag, der schon eine Nummer
            # traegt (etwa beim Wiedereinspielen), behaelt seine.
            entry.setdefault('n', self._folge)
            self._buf.append(entry)

    def hoechste_folge(self) -> int:
        """Die zuletzt vergebene Nummer."""
        with self._lock:
            return self._folge

    def ab_folge(self, ab: int, grenze: int = 200) -> list[dict]:
        """Eintraege mit Nummer >= ab, aelteste zuerst, hoechstens `grenze`.

        Blockierend (liest die Datei) — vom Aufrufer in einen Thread legen.

        Liegt `ab` vor dem aeltesten vorhandenen Eintrag, kommt zurueck, was da
        ist. Das ist kein Fehler, sondern die Wahrheit: der Verlauf haelt nur
        eine begrenzte Zeit vor, und nach einem langen Ausfall FEHLT der
        Anfang. Der Server sieht die Luecke an den Nummern und kann sie als
        solche anzeigen, statt sie zu verschweigen.
        """
        gefunden = []
        # Erst der Puffer im RAM: er enthaelt das Neueste und ist billig.
        with self._lock:
            puffer = [e for e in self._buf if isinstance(e.get('n'), int) and e['n'] >= ab]
        if not self._path.exists():
            return sorted(puffer, key=lambda e: e['n'])[:grenze]
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                for zeile in f:
                    zeile = zeile.strip()
                    if not zeile:
                        continue
                    try:
                        e = json.loads(zeile)
                    except Exception:
                        continue
                    if isinstance(e.get('n'), int) and e['n'] >= ab:
                        gefunden.append(e)
                        if len(gefunden) >= grenze:
                            break
        except OSError as e:
            log.warning('Verlauf ab Folge %d lesen fehlgeschlagen: %s', ab, e)
        if len(gefunden) < grenze:
            bekannt = {e['n'] for e in gefunden}
            gefunden += [e for e in puffer if e['n'] not in bekannt]
        return sorted(gefunden, key=lambda e: e['n'])[:grenze]

    def close(self, timeout: float = 5.0) -> None:
        """Beendet den Schreib-Thread und schreibt den Rest weg (blockierend)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        else:
            self._flush()

    def _writer_loop(self) -> None:
        # wait() liefert True, sobald gestoppt wird — sonst läuft der Timeout ab
        while not self._stop.wait(self._flush_interval_s):
            self._flush()
        self._flush()   # letzter Durchlauf beim Herunterfahren

    def _flush(self) -> None:
        """Hängt den gepufferten Rest an die Datei an (kein fsync, SD-Karte)."""
        with self._lock:
            if not self._buf:
                batch = []
            else:
                batch = list(self._buf)
                self._buf.clear()
        if batch:
            try:
                text = ''.join(json.dumps(e, separators=(',', ':')) + '\n' for e in batch)
                with open(self._path, 'a', encoding='utf-8') as f:
                    f.write(text)
                if self._oldest_ts is None:
                    ts = batch[0].get('ts')
                    self._oldest_ts = float(ts) if isinstance(ts, (int, float)) else None
            except Exception as e:
                log.warning('Verlauf schreiben fehlgeschlagen: %s', e)
                return
        try:
            if self._needs_rotate():
                self._rotate()
        except Exception as e:
            log.warning('Verlauf rotieren fehlgeschlagen: %s', e)

    # ── Rotation ────────────────────────────────────────────────────────────

    def _needs_rotate(self) -> bool:
        """Zu alt oder zu groß? Beides prüft nur ein stat() — kostet nichts."""
        try:
            size = self._path.stat().st_size
        except OSError:
            return False
        if size > self._max_bytes:
            return True
        if self._oldest_ts is not None and (time.time() - self._oldest_ts) > self._rotate_age_s:
            return True
        return False

    def _rotate(self) -> None:
        """Schreibt die Datei neu — nur noch das Aufbewahrungsfenster.

        Läuft im Schreib-Thread, höchstens einmal pro Tag. Neu geschrieben wird
        atomar über eine Temp-Datei, damit ein Stromausfall die Datei nicht auf
        halber Strecke zerlegt.
        """
        entries = self._read_tail(self._retention_s, self._max_entries)
        tmp = self._path.with_name(self._path.name + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            for e in entries:
                f.write(json.dumps(e, separators=(',', ':')) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)
        self._oldest_ts = entries[0]['ts'] if entries else None
        log.info('Verlaufsdatei rotiert: %d Einträge behalten', len(entries))
