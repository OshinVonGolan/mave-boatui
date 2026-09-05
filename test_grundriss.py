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
import asyncio
import json
import os
import shutil
import tempfile
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


class Planvorlage(unittest.TestCase):
    """Das abfotografierte Original, über das im Werkzeug gezeichnet wird.

    Es wird wieder ausgeliefert — deshalb wird nicht der gemeldete Typ
    geprüft, sondern der Dateianfang. Ein Browser darf behaupten, was er will.
    """

    JPEG = b'\xff\xd8\xff' + b'x' * 200
    PNG  = b'\x89PNG\r\n\x1a\n' + b'x' * 200

    def setUp(self):
        self.verz = tempfile.mkdtemp(prefix='mave-riss-')
        self._alt_bild = main.GRUNDRISS_BILD
        self._alt_riss = main.GRUNDRISS_FILE
        main.GRUNDRISS_BILD = Path(self.verz) / 'grundriss-vorlage.jpg'
        main.GRUNDRISS_FILE = Path(self.verz) / 'grundriss.json'

    def tearDown(self):
        main.GRUNDRISS_BILD = self._alt_bild
        main.GRUNDRISS_FILE = self._alt_riss
        shutil.rmtree(self.verz, ignore_errors=True)

    def hochladen(self, daten):
        class Anfrage:
            async def body(self_):
                return daten
        return asyncio.run(main.put_grundriss_bild(Anfrage()))

    def test_jpeg_und_png_kommen_durch(self):
        for daten in (self.JPEG, self.PNG):
            self.assertEqual(self.hochladen(daten)['bytes'], len(daten))
            self.assertEqual(main.GRUNDRISS_BILD.read_bytes(), daten)

    def test_alles_andere_nicht(self):
        """Eine SVG-Datei wäre ein Dokument mit Skripten darin, kein Bild."""
        for daten in (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
                      b'GIF89a' + b'x' * 100, b'%PDF-1.4', b'irgendwas'):
            with self.assertRaises(HTTPException, msg=daten[:12]):
                self.hochladen(daten)

    def test_leere_datei(self):
        with self.assertRaises(HTTPException):
            self.hochladen(b'')

    def test_zu_gross(self):
        """Verkleinert wird im Browser. Was hier gross ankommt, ist entweder
        ein Fehler oder Absicht — auf einem Pi Zero beides schlecht."""
        with self.assertRaises(HTTPException) as e:
            self.hochladen(self.JPEG + b'x' * (3 * 1024 * 1024))
        self.assertEqual(e.exception.status_code, 413)

    def test_ohne_vorlage_ein_ehrliches_404(self):
        with self.assertRaises(HTTPException) as e:
            asyncio.run(main.get_grundriss_bild())
        self.assertEqual(e.exception.status_code, 404)

    def test_entfernen_geht_auch_wenn_nichts_da_ist(self):
        self.assertTrue(asyncio.run(main.del_grundriss_bild())['ok'])
        self.hochladen(self.JPEG)
        asyncio.run(main.del_grundriss_bild())
        self.assertFalse(main.GRUNDRISS_BILD.exists())

    def test_der_riss_sagt_ob_eine_vorlage_daliegt(self):
        """Sonst müsste das Werkzeug danach fragen — und ein 404 auf diese
        Frage schreibt in jedem Browser einen roten Fehler in die Konsole."""
        self.assertFalse(asyncio.run(main.get_grundriss())['hat_vorlage'])
        self.hochladen(self.JPEG)
        self.assertTrue(asyncio.run(main.get_grundriss())['hat_vorlage'])


class BildPlatzierung(unittest.TestCase):
    """Wo die Vorlage liegt, sind fünf Zahlen — und nur Zahlen."""

    def test_wird_uebernommen(self):
        g = main._grundriss_pruefen(mit(bild={'x': 10, 'y': 20, 'w': 180, 'h': 500,
                                              'deckkraft': .4}))
        self.assertEqual(g['bild'], {'x': 10, 'y': 20, 'w': 180, 'h': 500,
                                     'deckkraft': .4})

    def test_deckkraft_bleibt_zwischen_null_und_eins(self):
        self.assertEqual(main._grundriss_pruefen(mit(bild={'deckkraft': 9}))['bild']['deckkraft'], 1)
        self.assertEqual(main._grundriss_pruefen(mit(bild={'deckkraft': -3}))['bild']['deckkraft'], 0)

    def test_fremde_felder_kommen_nicht_mit(self):
        """Der Prüfer baut ein neues Objekt — was er nicht kennt, geht nicht
        durch. Das ist der Punkt: der Inhalt wird im Browser zu SVG."""
        g = main._grundriss_pruefen(mit(bild={'x': 0, 'href': 'javascript:alert(1)'}))
        self.assertEqual(set(g['bild']), {'x', 'y', 'w', 'h', 'deckkraft'})

    def test_ohne_bild_steht_der_schluessel_nicht_da(self):
        self.assertNotIn('bild', main._grundriss_pruefen(mit()))

    def test_kein_objekt_wird_ignoriert(self):
        self.assertNotIn('bild', main._grundriss_pruefen(mit(bild='ja bitte')))


class WerDenRissAendernDarf(unittest.TestCase):
    """Der Grundriss ist keine Bedienung, sondern der Aufbau des Bootes.

    An ihm hängen Stauplan und Geräteseite: wer ihn umzeichnet, ändert, wo für
    ALLE die Dinge liegen. Ohne eigene Zeile fiele er unter die Vorgabe für
    schreibende Aufrufe — und die hat jedes Crewmitglied.
    """

    def recht(self, methode, pfad):
        from sync import zugang
        return zugang.recht_fuer(methode, pfad)

    def test_lesen_darf_jeder_der_lesen_darf(self):
        """Der Stauplan und die Geräteseite brauchen ihn — auch ein Gast."""
        self.assertEqual(self.recht('GET', '/api/grundriss'), 'lesen')

    def test_aendern_verlangt_einstellen(self):
        self.assertEqual(self.recht('PUT', '/api/grundriss'), 'einstellen')

    def test_die_planvorlage_in_jeder_methode(self):
        for m in ('GET', 'PUT', 'DELETE'):
            self.assertEqual(self.recht(m, '/api/grundriss/vorlage'), 'einstellen', m)


if __name__ == '__main__':
    unittest.main()
