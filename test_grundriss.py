"""Der Grundriss als Daten — und was beim Speichern nicht durchkommt.

Er stand bis zum 05.09.2026 dreifach im Programm: als 250 Zeilen SVG in
index.html, als Tabelle ORTE in orte.js und als STAU_FAECHER in stauplan.js.
Drei Kopien desselben Bootes, und keine davon liess sich einzeichnen.

Der heikle Teil ist das Speichern: der Inhalt wird im Browser zu SVG. Wer
schreiben darf, koennte sonst Zeichenketten unterbringen, die im Dokument etwas
anderes tun als zeichnen.

Aufruf:

    ./venv/bin/python -m unittest test_grundriss -v
"""
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

from fastapi import HTTPException              # noqa: E402

import main                                    # noqa: E402

VORLAGE = json.loads((Path(__file__).parent / 'grundriss.example.json').read_text())


def mit(**felder):
    return {**VORLAGE, **felder}


class DieVorlage(unittest.TestCase):
    """Was mitgeliefert wird, muss durch die eigene Pruefung passen."""

    def test_geht_unveraendert_durch(self):
        geprueft = main._grundriss_pruefen(VORLAGE)
        self.assertEqual(len(geprueft['raeume']), 11)
        self.assertEqual(len(geprueft['hintergrund']), len(VORLAGE['hintergrund']))

    def test_raeume_haben_namen_und_farbe(self):
        for r in main._grundriss_pruefen(VORLAGE)['raeume']:
            self.assertTrue(r['name'])
            self.assertRegex(r['farbe'], r'^#[0-9a-fA-F]{6}$')

    def test_beschriftung_ausserhalb_des_rumpfes_ist_frei(self):
        """"Bug" und "Heck" stehen NEBEN dem Boot. Ohne die Markierung wuerden
        sie am Rumpfumriss abgeschnitten und waeren weg."""
        frei = [f for f in VORLAGE['hintergrund'] if f.get('frei')]
        texte = {f.get('s') for f in frei if f['t'] == 'text'}
        self.assertEqual(texte, {'▲ BUG', 'HECK ▼'})


class WasNichtDurchkommt(unittest.TestCase):
    """Der Inhalt landet als SVG im Dokument. Deshalb: bekannte Formen, Zahlen
    in Grenzen, Farben nach Muster, Pfade nur aus Pfadzeichen."""

    def test_fremde_form(self):
        with self.assertRaises(HTTPException) as f:
            main._grundriss_pruefen(mit(hintergrund=[{'t': 'script', 's': 'alert(1)'}]))
        self.assertIn('bekannte Form', f.exception.detail)

    def test_fremdes_feld(self):
        with self.assertRaises(HTTPException):
            main._grundriss_pruefen(mit(hintergrund=[{'t': 'rect', 'onclick': 'x()'}]))

    def test_farbe_muss_eine_farbe_sein(self):
        for boese in ('url(#x)', 'javascript:1', 'red;x', '#12'):
            with self.subTest(farbe=boese), self.assertRaises(HTTPException):
                main._grundriss_pruefen(mit(hintergrund=[{'t': 'rect', 'fill': boese}]))

    def test_pfad_nur_aus_pfadzeichen(self):
        with self.assertRaises(HTTPException) as f:
            main._grundriss_pruefen(mit(hintergrund=[{'t': 'path', 'd': 'M0,0 <script>'}]))
        self.assertIn('Pfadangabe', f.exception.detail)

    def test_rumpf_ebenso(self):
        with self.assertRaises(HTTPException):
            main._grundriss_pruefen(mit(rumpf='M0,0 L10,10" onload="x'))

    def test_text_bleibt_text(self):
        """Spitze Klammern IM Text sind erlaubt — er wird im Browser ueber
        textContent gesetzt und nie als Auszeichnung gelesen. Was hier zaehlt,
        ist nur die Laenge."""
        g = main._grundriss_pruefen(mit(hintergrund=[
            {'t': 'text', 'x': 1, 'y': 1, 's': '<b>Salon</b>'}]))
        self.assertEqual(g['hintergrund'][0]['s'], '<b>Salon</b>')

    def test_zahl_in_grenzen(self):
        with self.assertRaises(HTTPException):
            main._grundriss_pruefen(mit(hintergrund=[{'t': 'rect', 'x': 1e9}]))

    def test_raumkennung_nur_schlicht(self):
        for boese in ('Salon Gross', 'a"b', '', 'ÜBER'):
            with self.subTest(id=boese), self.assertRaises(HTTPException):
                main._grundriss_pruefen(mit(raeume=[
                    {'id': boese, 'name': 'x',
                     'form': {'t': 'rechteck', 'x': 0, 'y': 0, 'w': 1, 'h': 1}}]))

    def test_kennung_nur_einmal(self):
        rechteck = {'t': 'rechteck', 'x': 0, 'y': 0, 'w': 1, 'h': 1}
        with self.assertRaises(HTTPException) as f:
            main._grundriss_pruefen(mit(raeume=[
                {'id': 'salon', 'name': 'A', 'form': rechteck},
                {'id': 'salon', 'name': 'B', 'form': rechteck}]))
        self.assertIn('mehrfach', f.exception.detail)


class Vielecke(unittest.TestCase):
    """Raeume sind nicht immer rechteckig — ein Vorschiff ist spitz."""

    def test_vieleck_geht_durch(self):
        g = main._grundriss_pruefen(mit(raeume=[
            {'id': 'bug', 'name': 'Vorschiff', 'farbe': '#22d3ee',
             'form': {'t': 'vieleck', 'punkte': [[100, 14], [150, 114], [50, 114]]}}]))
        self.assertEqual(len(g['raeume'][0]['form']['punkte']), 3)

    def test_zu_wenige_punkte(self):
        with self.assertRaises(HTTPException):
            main._grundriss_pruefen(mit(raeume=[
                {'id': 'bug', 'name': 'x',
                 'form': {'t': 'vieleck', 'punkte': [[0, 0], [1, 1]]}}]))

    def test_unbekannte_form(self):
        with self.assertRaises(HTTPException):
            main._grundriss_pruefen(mit(raeume=[
                {'id': 'bug', 'name': 'x', 'form': {'t': 'kreis', 'r': 5}}]))


if __name__ == '__main__':
    unittest.main()
