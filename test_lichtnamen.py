"""Namen der Lichtkreise — einstellbar statt fest verdrahtet.

Sie standen an zwei Stellen im Skript: CH_NAMES (charts.js, lang) und
_WIDE_LABELS (lights.js, kurz). Zwei Listen fuer dieselbe Sache, beide nur
durch ein Update zu aendern — dabei ist ein Kanalname eine Angabe ueber DIESES
Boot und kein Programmtext.

Aufruf:

    ./venv/bin/python -m unittest test_lichtnamen -v
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

import asyncio                                 # noqa: E402

from fastapi import HTTPException              # noqa: E402

import main                                    # noqa: E402


class Lichtnamen(unittest.TestCase):

    def setUp(self):
        self.verz = tempfile.mkdtemp(prefix='mave-licht-')
        self.datei = Path(self.verz) / 'presets.json'
        self.datei.write_text(json.dumps({'presets': [], 'tanks': {}}))
        self._alt = main.PRESETS_FILE
        main.PRESETS_FILE = self.datei

    def tearDown(self):
        main.PRESETS_FILE = self._alt
        shutil.rmtree(self.verz, ignore_errors=True)

    def setzen(self, lights):
        return asyncio.run(main.update_settings({'lights': lights}))

    def gespeichert(self):
        return json.loads(self.datei.read_text()).get('lights', {})

    def test_name_wird_gespeichert(self):
        self.setzen({'0': {'name': 'Kombüse'}})
        self.assertEqual(self.gespeichert(), {'0': {'name': 'Kombüse'}})

    def test_antwort_enthaelt_die_namen(self):
        """Die Oberflaeche uebernimmt die Antwort direkt — steht der Name nicht
        drin, zeigt sie bis zum naechsten Neuladen den alten."""
        antwort = self.setzen({'3': {'name': 'Achterkabine'}})
        self.assertEqual(antwort['lights']['3']['name'], 'Achterkabine')

    def test_leerer_name_loescht(self):
        """Loeschen heisst: die Vorgabe gilt wieder. Ein leerer Name waere ein
        Balken ohne Beschriftung."""
        self.setzen({'0': {'name': 'Kombüse'}})
        self.setzen({'0': {'name': '   '}})
        self.assertEqual(self.gespeichert(), {})

    def test_das_relais_hat_auch_einen_namen(self):
        self.setzen({'8': {'name': 'Ankerlicht'}})
        self.assertEqual(self.gespeichert()['8']['name'], 'Ankerlicht')

    def test_andere_abschnitte_bleiben_stehen(self):
        self.datei.write_text(json.dumps({'tanks': {'tank1': {'name': 'Wasser'}}}))
        self.setzen({'0': {'name': 'Kombüse'}})
        alles = json.loads(self.datei.read_text())
        self.assertEqual(alles['tanks']['tank1']['name'], 'Wasser')

    # ── Was zurueckgewiesen wird ───────────────────────────────────────────

    def test_kanal_ausserhalb(self):
        with self.assertRaises(HTTPException) as f:
            self.setzen({'9': {'name': 'x'}})
        self.assertIn('0 bis 8', f.exception.detail)

    def test_keine_kanalnummer(self):
        with self.assertRaises(HTTPException) as f:
            self.setzen({'kombuese': {'name': 'x'}})
        self.assertIn('Kanalnummer', f.exception.detail)

    def test_unbekanntes_feld(self):
        """Ein Tippfehler im Feldnamen soll auffallen und nicht still
        verschwinden."""
        with self.assertRaises(HTTPException):
            self.setzen({'0': {'farbe': '#fff'}})

    def test_zu_langer_name_wird_gekuerzt(self):
        self.setzen({'0': {'name': 'x' * 100}})
        self.assertEqual(len(self.gespeichert()['0']['name']), 32)


if __name__ == '__main__':
    unittest.main()
