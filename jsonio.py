"""Robustes Lesen und atomares Schreiben von JSON-Dateien.

Alles läuft auf einer SD-Karte: ein Stromausfall mitten im Schreiben darf keine
halbe Datei hinterlassen, und eine fehlende oder beschädigte Datei darf den
Dienst nicht am Starten hindern. Genau dafür sind diese beiden Funktionen da —
sie werden von allen Modulen benutzt, die Konfiguration/Daten als JSON ablegen.
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def read_json(path: Path, default=None):
    """Liest eine JSON-Datei und gibt bei JEDEM Fehler ``default`` zurück.

    Wirft nie: fehlende Datei, kaputtes/halbes JSON, fehlende Leserechte oder
    ein Kodierungsproblem enden alle in ``default``. So kann eine beschädigte
    presets.json den Dienst nicht schon beim Import abschießen.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log.warning('JSON lesen fehlgeschlagen (%s): %s — nutze Vorgabewert', path, e)
        return default


def write_json(path: Path, data) -> None:
    """Schreibt JSON atomar: erst ``<name>.tmp``, dann ``os.replace``.

    flush + os.fsync stellen sicher, dass die Daten wirklich auf der Karte
    liegen, bevor die alte Datei ersetzt wird. os.replace ist auf POSIX atomar —
    ein Leser sieht also entweder den alten oder den neuen Stand, nie einen
    halben. Bei einem Fehler bleibt die alte Datei unangetastet.
    """
    path = Path(path)
    tmp  = path.with_name(path.name + '.tmp')
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # Angefangene Temp-Datei nicht liegen lassen
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    # Zusätzlich das Verzeichnis synchronisieren, damit auch der Umbenennen-
    # Vorgang einen Stromausfall überlebt. Nur bestmöglich — schlägt das fehl,
    # sind die Daten trotzdem geschrieben.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
