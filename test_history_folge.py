"""Tests der Folgenummern im Verlauf.

Sie sind das, woran das Nachliefern haengt: der Server nennt seinen Stand, der
Pi schickt ab dort weiter. Aufruf:

    python3 -m unittest test_history_folge -v
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from history_store import HistoryStore


class Folgenummern(unittest.TestCase):

    def setUp(self):
        self.pfad = Path(tempfile.mkdtemp()) / 'verlauf.ndjson'

    def _store(self):
        return HistoryStore(self.pfad, retention_s=3600, max_entries=1000)

    def test_jeder_eintrag_bekommt_eine_nummer(self):
        s = self._store()
        s.append({'ts': time.time(), 'v': 12.6})
        s.append({'ts': time.time(), 'v': 12.5})
        self.assertEqual(s.hoechste_folge(), 2)
        self.assertEqual([e['n'] for e in s.ab_folge(1)], [1, 2])

    def test_vorhandene_nummer_bleibt(self):
        # Beim Wiedereinspielen darf ein Eintrag seine Nummer behalten.
        s = self._store()
        s.append({'ts': time.time(), 'n': 42, 'v': 1})
        self.assertEqual(s.ab_folge(1)[0]['n'], 42)

    def test_nach_neustart_wird_weitergezaehlt(self):
        # Ohne das gaebe es Folgenummern zweimal, und der Server wuerde
        # Eintraege verwerfen, die er noch nie gesehen hat.
        s = self._store()
        for i in range(5):
            s.append({'ts': time.time(), 'v': i})
        s.close()

        neu = self._store()
        neu.load()
        self.assertEqual(neu.hoechste_folge(), 5)
        neu.append({'ts': time.time(), 'v': 99})
        self.assertEqual(neu.hoechste_folge(), 6)

    def test_ab_folge_liefert_nur_neueres(self):
        s = self._store()
        for i in range(10):
            s.append({'ts': time.time(), 'v': i})
        s.close()
        s2 = self._store(); s2.load()
        nummern = [e['n'] for e in s2.ab_folge(7)]
        self.assertEqual(nummern, [7, 8, 9, 10])

    def test_grenze_wird_eingehalten(self):
        s = self._store()
        for i in range(50):
            s.append({'ts': time.time(), 'v': i})
        s.close()
        s2 = self._store(); s2.load()
        self.assertEqual(len(s2.ab_folge(1, grenze=20)), 20)

    def test_ungeschriebenes_aus_dem_puffer_kommt_mit(self):
        # Der Puffer wird nur alle 20 s geschrieben. Was darin liegt, muss der
        # Sync trotzdem sehen, sonst haengt die Fernansicht dem Boot hinterher.
        s = self._store()
        s.append({'ts': time.time(), 'v': 1})
        s.append({'ts': time.time(), 'v': 2})
        nummern = [e['n'] for e in s.ab_folge(1)]
        self.assertEqual(nummern, [1, 2])

    def test_luecke_wird_nicht_verschwiegen(self):
        # Nach einem langen Ausfall ist der Anfang weg. Dann kommt zurueck, was
        # da ist — der Server sieht an den Nummern, dass etwas fehlt.
        s = self._store()
        for i in range(3):
            s.append({'ts': time.time(), 'v': i})
        s.close()
        s2 = self._store(); s2.load()
        erhalten = s2.ab_folge(1000)
        self.assertEqual(erhalten, [])
        vorhanden = s2.ab_folge(1)
        self.assertEqual([e['n'] for e in vorhanden], [1, 2, 3])

    def test_alte_datei_ohne_nummern_bricht_nichts(self):
        # Auf dem Pi liegt eine Datei aus der Zeit vor dieser Aenderung.
        self.pfad.write_text('{"ts": 1, "v": 1}\n{"ts": 2, "v": 2}\n', encoding='utf-8')
        s = self._store()
        s.load()
        self.assertEqual(s.hoechste_folge(), 0)      # nichts zu uebernehmen
        s.append({'ts': 3, 'v': 3})
        self.assertEqual(s.hoechste_folge(), 1)
        # Die alten Zeilen ohne Nummer werden nicht ausgeliefert — sie haetten
        # keine, an der sich der Server orientieren koennte.
        self.assertEqual([e['n'] for e in s.ab_folge(1)], [1])


if __name__ == '__main__':
    unittest.main()


class KrummeFolgenummern(unittest.TestCase):
    """Der Fehler, der den Server tagelang ohne Verlauf ließ.

    Die Minutenmittelung behandelte `n` wie einen Messwert und mittelte es mit.
    Heraus kamen Kommazahlen (1476.5). Jede Abfrage filtert auf ganze Zahlen —
    also lieferte sie nichts, ohne dass irgendwo ein Fehler auftauchte. Der
    Server meldete brav "Verlauf ab 1" und bekam nie etwas.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pfad = Path(self._tmp.name) / 'v.ndjson'

    def tearDown(self):
        self._tmp.cleanup()

    def _schreiben(self, eintraege):
        with open(self.pfad, 'w') as f:
            for e in eintraege:
                f.write(json.dumps(e) + '\n')

    def test_kommazahlen_werden_beim_laden_geradegezogen(self):
        jetzt = time.time()
        self._schreiben([
            {'ts': jetzt - 300, 'soc': 90, 'n': 1476.5},
            {'ts': jetzt - 240, 'soc': 91, 'n': 1477.5},
            {'ts': jetzt - 180, 'soc': 92, 'n': 1478.5},
        ])
        s = HistoryStore(self.pfad, retention_s=3600, max_entries=100)
        eintraege = s.load()
        self.assertEqual(len(eintraege), 3)
        for e in eintraege:
            self.assertIsInstance(e['n'], int, f"n={e['n']!r} muss ganzzahlig sein")
        # Und sie sind jetzt abrufbar — das war der eigentliche Schaden
        self.assertEqual(len(s.ab_folge(1, 100)), 3,
                         'Nach der Reparatur muss der Verlauf abrufbar sein')

    def test_fehlende_nummern_bekommen_eine(self):
        jetzt = time.time()
        self._schreiben([{'ts': jetzt - 120, 'soc': 80}, {'ts': jetzt - 60, 'soc': 81}])
        s = HistoryStore(self.pfad, retention_s=3600, max_entries=100)
        s.load()
        self.assertEqual(len(s.ab_folge(1, 100)), 2)

    def test_gute_nummern_bleiben_unangetastet(self):
        jetzt = time.time()
        self._schreiben([{'ts': jetzt - 120, 'soc': 80, 'n': 7},
                         {'ts': jetzt - 60, 'soc': 81, 'n': 8}])
        s = HistoryStore(self.pfad, retention_s=3600, max_entries=100)
        eintraege = s.load()
        self.assertEqual([e['n'] for e in eintraege], [7, 8])
        self.assertEqual(s.hoechste_folge(), 8, 'die Zählung muss weiterlaufen')
