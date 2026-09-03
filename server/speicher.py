"""Was der Server vom Boot behaelt.

SQLite, eine Datei, ein Boot. Kein Postgres: es geht um EIN Fahrzeug, und der
Server soll neben Gantrya stehen, ohne dessen Datenbank mitzubenutzen.

Drei Dinge werden gehalten:

  zustand   der neueste Live-Stand, genau eine Zeile
  verlauf   die Verlaufseintraege, nach Folgenummer, append-only
  geparkt   Eintraege, deren Zeit noch nicht feststeht (Pi ohne gestellte Uhr)

Das Parken ist der praktische Teil der Zeitrechnung aus sync/zeit.py: ein
Eintrag ohne Zeitachse wird NICHT geraten und nicht verworfen, sondern
zurueckgelegt. Sobald ein Paket mit gestellter Uhr eintrifft, ist der Bezug
bekannt und die geparkten Eintraege wandern mit richtiger Zeit in den Verlauf.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zustand (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    daten     TEXT NOT NULL,
    zeit      REAL,              -- aufgeloeste Bordzeit, kann NULL sein
    empfangen REAL NOT NULL      -- Serverzeit des Eingangs, nie NULL
);
CREATE TABLE IF NOT EXISTS verlauf (
    folge     INTEGER PRIMARY KEY,
    zeit      REAL NOT NULL,
    daten     TEXT NOT NULL,
    empfangen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS verlauf_zeit ON verlauf (zeit);
CREATE TABLE IF NOT EXISTS geparkt (
    folge     INTEGER PRIMARY KEY,
    mono      REAL NOT NULL,
    daten     TEXT NOT NULL,
    empfangen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sitzung (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ab        REAL NOT NULL,     -- Serverzeit der ersten Nachricht
    bis       REAL NOT NULL,     -- Serverzeit des letzten Lebenszeichens
    geraet    TEXT,
    -- Der Befund des Pi aus seiner Startmarke. Erst er macht eine Luecke
    -- erklaerbar: ein Funkloch sieht auf dem Server genauso aus wie ein
    -- Stromausfall, den Unterschied kennt nur das Boot.
    letztes_ende TEXT,           -- sauber | abbruch | erststart | unbekannt
    rechner_neu  INTEGER,        -- 0/1: hat der ganze Rechner neu gestartet
    nur_dienst   INTEGER,        -- 0/1: nur der Dienst, Rechner lief durch
    rechner_start REAL           -- Wanduhrzeit des Systemstarts, wenn bekannt
);
CREATE TABLE IF NOT EXISTS ereignis (
    folge     INTEGER PRIMARY KEY,
    zeit      REAL,
    art       TEXT NOT NULL,
    daten     TEXT NOT NULL,
    empfangen REAL NOT NULL
);
"""


class Speicher:
    """Alle Schreibzugriffe laufen ueber ein Schloss.

    SQLite kann Nebenlaeufigkeit, aber der Server hat genau einen Schreiber (die
    Verbindung zum Boot) und viele Leser. Ein Schloss ist billiger als
    Fehlersuche in gelegentlichen "database is locked".
    """

    def __init__(self, pfad: str | Path):
        self._pfad = str(pfad)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._pfad, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # WAL: Leser blockieren den Schreiber nicht. Bei einer Datei auf
        # Serverplatte ist das der richtige Modus.
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=NORMAL')
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def schliessen(self) -> None:
        with self._lock:
            self._db.close()

    # ── Zustand ─────────────────────────────────────────────────────────────

    def zustand_setzen(self, daten: dict, zeit: float | None) -> None:
        with self._lock:
            self._db.execute(
                'INSERT INTO zustand (id, daten, zeit, empfangen) VALUES (1, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET daten=excluded.daten, '
                'zeit=excluded.zeit, empfangen=excluded.empfangen',
                (json.dumps(daten, ensure_ascii=False), zeit, time.time()))
            self._db.commit()

    def zustand(self) -> dict | None:
        """Der letzte Stand samt Alter.

        Das Alter gehoert ZUM Ergebnis, nicht daneben: ein Wert von vor drei
        Tagen darf in der Oberflaeche nicht aussehen wie live.
        """
        z = self._db.execute('SELECT * FROM zustand WHERE id = 1').fetchone()
        if not z:
            return None
        return {
            'daten': json.loads(z['daten']),
            'bordzeit': z['zeit'],
            'empfangen': z['empfangen'],
            'alter_s': round(time.time() - z['empfangen'], 1),
        }

    # ── Verlauf ─────────────────────────────────────────────────────────────

    def verlauf_stand(self) -> int:
        """Hoechste zusammenhaengend vorhandene Folgenummer.

        Sie ist die Zahl, die der Pi braucht: 'ich habe bis hier, schick ab
        hier weiter'. Geparkte Eintraege zaehlen mit, sonst wuerde der Pi sie
        ein zweites Mal schicken.
        """
        a = self._db.execute('SELECT MAX(folge) AS m FROM verlauf').fetchone()['m']
        b = self._db.execute('SELECT MAX(folge) AS m FROM geparkt').fetchone()['m']
        return max(a or 0, b or 0)

    def verlauf_anhaengen(self, eintraege: list[dict]) -> dict:
        """Eintraege ablegen. Jeder braucht folge, daten und entweder zeit oder mono.

        Rueckgabe zaehlt, was wohin ging — der Aufrufer protokolliert das, damit
        im Betrieb sichtbar ist, ob Eintraege geparkt liegen bleiben.
        """
        jetzt = time.time()
        neu = geparkt = 0
        with self._lock:
            for e in eintraege:
                folge = e.get('folge')
                if not isinstance(folge, int):
                    continue
                roh = json.dumps(e.get('daten', {}), ensure_ascii=False)
                if e.get('zeit') is not None:
                    self._db.execute(
                        'INSERT OR IGNORE INTO verlauf (folge, zeit, daten, empfangen) '
                        'VALUES (?, ?, ?, ?)', (folge, float(e['zeit']), roh, jetzt))
                    neu += 1
                else:
                    self._db.execute(
                        'INSERT OR IGNORE INTO geparkt (folge, mono, daten, empfangen) '
                        'VALUES (?, ?, ?, ?)', (folge, float(e.get('mono') or 0.0), roh, jetzt))
                    geparkt += 1
            self._db.commit()
        return {'verlauf': neu, 'geparkt': geparkt}

    def geparkte_aufloesen(self, uhrbuch) -> int:
        """Versucht, geparkte Eintraege einzuordnen. Gibt die Zahl der
        Umgezogenen zurueck.

        Wird gerufen, sobald ein Paket mit gestellter Uhr eintrifft — dann ist
        der Bezug zwischen Zaehler und echter Zeit bekannt.
        """
        if not uhrbuch.hat_referenz:
            return 0
        umgezogen = 0
        with self._lock:
            zeilen = self._db.execute('SELECT * FROM geparkt ORDER BY folge').fetchall()
            for z in zeilen:
                zeit = uhrbuch.aufloesen(None, z['mono'], False)
                if zeit is None:
                    continue
                self._db.execute(
                    'INSERT OR IGNORE INTO verlauf (folge, zeit, daten, empfangen) '
                    'VALUES (?, ?, ?, ?)', (z['folge'], zeit, z['daten'], z['empfangen']))
                self._db.execute('DELETE FROM geparkt WHERE folge = ?', (z['folge'],))
                umgezogen += 1
            self._db.commit()
        return umgezogen

    def verlauf(self, seit: float | None = None, grenze: int = 5000) -> list[dict]:
        if seit is None:
            zeilen = self._db.execute(
                'SELECT * FROM verlauf ORDER BY zeit DESC LIMIT ?', (grenze,)).fetchall()
        else:
            zeilen = self._db.execute(
                'SELECT * FROM verlauf WHERE zeit >= ? ORDER BY zeit DESC LIMIT ?',
                (seit, grenze)).fetchall()
        return [{'folge': z['folge'], 'zeit': z['zeit'], **json.loads(z['daten'])}
                for z in reversed(zeilen)]

    def geparkt_anzahl(self) -> int:
        return self._db.execute('SELECT COUNT(*) AS n FROM geparkt').fetchone()['n']

    # ── Ereignisse ──────────────────────────────────────────────────────────

    def ereignis_anhaengen(self, folge: int, art: str, daten: dict,
                           zeit: float | None) -> None:
        with self._lock:
            self._db.execute(
                'INSERT OR IGNORE INTO ereignis (folge, zeit, art, daten, empfangen) '
                'VALUES (?, ?, ?, ?, ?)',
                (int(folge), zeit, str(art), json.dumps(daten, ensure_ascii=False), time.time()))
            self._db.commit()

    # ── Sitzungen und Luecken ───────────────────────────────────────────────
    # Die Frage des Eigners: war das Boot in einem Zeitraum offline, oder ist
    # der Pi abgestuerzt? Das sind ZWEI Dinge, und sie brauchen zwei Quellen.
    # Der Server weiss, wann er Kontakt hatte. Warum der Kontakt fehlte, sagt
    # ihm erst der Befund, den der Pi beim naechsten Verbinden mitschickt.

    def sitzung_beginnen(self, geraet: str, befund: dict | None = None) -> int:
        b = befund or {}
        jetzt = time.time()
        with self._lock:
            cur = self._db.execute(
                'INSERT INTO sitzung (ab, bis, geraet, letztes_ende, rechner_neu, '
                'nur_dienst, rechner_start) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (jetzt, jetzt, geraet, b.get('letztes_ende'),
                 1 if b.get('rechner_neu') else 0,
                 1 if b.get('nur_dienst') else 0,
                 b.get('rechner_start_wand')))
            self._db.commit()
            return int(cur.lastrowid)

    def lebenszeichen(self, sitzung_id: int) -> None:
        """Bei jeder Nachricht aufrufen. Haelt das Ende der Sitzung aktuell.

        Kein eigener Zeitgeber: solange Pakete kommen, steht die Verbindung, und
        wenn keine mehr kommen, ist `bis` genau der letzte Kontakt.
        """
        with self._lock:
            self._db.execute('UPDATE sitzung SET bis = ? WHERE id = ?',
                             (time.time(), int(sitzung_id)))
            self._db.commit()

    def sitzungen(self, seit: float | None = None) -> list[dict]:
        if seit is None:
            zeilen = self._db.execute('SELECT * FROM sitzung ORDER BY ab').fetchall()
        else:
            zeilen = self._db.execute(
                'SELECT * FROM sitzung WHERE bis >= ? ORDER BY ab', (seit,)).fetchall()
        return [dict(z) for z in zeilen]

    def luecken(self, seit: float | None = None) -> list[dict]:
        """Die Zeitraeume ohne Kontakt, jeder mit einer Deutung.

        Vier Faelle, und die Unterscheidung ist der ganze Sinn:

          funkloch    Der Pi lief durch, nur die Verbindung fehlte. Sein Verlauf
                      ist vollstaendig und wurde nachgeliefert.
          stromlos    Der Rechner war aus und ist unsauber wieder hochgekommen —
                      Stromausfall oder Absturz. Im Verlauf fehlt dieser
                      Zeitraum wirklich.
          neustart    Der Rechner wurde geordnet neu gestartet.
          dienst      Nur der Dienst wurde neu gestartet, etwa durch ein Update.
        """
        s = self.sitzungen(seit)
        raus = []
        for vorige, naechste in zip(s, s[1:]):
            ab, bis = vorige['bis'], naechste['ab']
            if bis - ab < 1.0:
                continue                      # nahtlos, keine Luecke
            if naechste['letztes_ende'] == 'erststart':
                # Der allererste Start nach dem Einbau dieser Fassung: es gibt
                # keine Startmarke, mit der sich vergleichen liesse. "Rechner
                # neu gestartet" waere geraten — richtig ist: wir wissen es
                # nicht, weil vorher niemand Buch gefuehrt hat.
                art, grund = 'erststart', 'Erster Start mit Ausfallerkennung — vorher wurde nicht Buch geführt'
            elif naechste['nur_dienst'] and not naechste['rechner_neu']:
                art, grund = 'dienst', 'Dienst neu gestartet, Rechner lief durch'
            elif naechste['rechner_neu'] and naechste['letztes_ende'] == 'abbruch':
                art, grund = 'stromlos', 'Rechner war aus, unsauberes Ende — Stromausfall oder Absturz'
            elif naechste['rechner_neu']:
                art, grund = 'neustart', 'Rechner wurde neu gestartet'
            else:
                art, grund = 'funkloch', 'Pi lief durch, nur die Verbindung fehlte'
            eintrag = {'ab': ab, 'bis': bis, 'dauer_s': round(bis - ab, 1),
                       'art': art, 'grund': grund,
                       'letztes_ende': naechste['letztes_ende']}
            # Wenn der Systemstart bekannt ist, laesst sich die Luecke teilen:
            # bis dahin war der Rechner AUS, danach nur ohne Verbindung.
            start = naechste['rechner_start']
            if art in ('stromlos', 'neustart') and start and ab <= start <= bis:
                eintrag['aus_bis'] = start
                eintrag['ohne_verbindung_ab'] = start
            raus.append(eintrag)
        return raus

    def ereignisse(self, grenze: int = 200) -> list[dict]:
        zeilen = self._db.execute(
            'SELECT * FROM ereignis ORDER BY folge DESC LIMIT ?', (grenze,)).fetchall()
        return [{'folge': z['folge'], 'zeit': z['zeit'], 'art': z['art'],
                 'daten': json.loads(z['daten'])} for z in zeilen]
