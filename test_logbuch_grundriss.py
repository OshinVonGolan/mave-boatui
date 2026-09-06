"""Das Planungswerkzeug liegt auf dem Server — und seine Endpunkte auch.

Gezeichnet wird im Logbuch, gebraucht wird der Riss am Boot. Der Server hält
deshalb einen eigenen Arbeitsstand; was ans Boot geht, wird ausdrücklich
hinausgegeben. Ein stiller Abgleich wäre falsch: dann stünde jede halbfertige
Linie sofort im Stauplan an Bord.

Aufruf:

    ./venv/bin/python -m unittest test_logbuch_grundriss -v
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('MAVE_PASSWORT', 'x' * 24)
os.environ.setdefault('MAVE_GERAET_TOKEN', 'y' * 24)
os.environ.setdefault('MAVE_DATEN', tempfile.mkdtemp(prefix='mave-logb-daten-'))

import asyncio                                 # noqa: E402

from fastapi import HTTPException              # noqa: E402

from server import app as srv                  # noqa: E402


class Anfrage:
    """Gerade so viel Request, wie die Endpunkte anfassen."""

    def __init__(self, rumpf=b'', json_daten=None):
        self._rumpf = rumpf
        self._json = json_daten

    async def body(self):
        return self._rumpf

    async def json(self):
        if self._json is None:
            raise ValueError('kein JSON')
        return self._json


RISS = {
    'name': 'Mave', 'loa_m': 12.8, 'breite_m': 3.6,
    'ansicht': {'w': 200, 'h': 680},
    'rumpf': 'M100,14 L186,360 L14,360 Z',
    'hintergrund': [],
    'raeume': [{'id': 'salon', 'name': 'Salon', 'farbe': '#60a5fa',
                'form': {'t': 'rechteck', 'x': 20, 'y': 100, 'w': 80, 'h': 60}}],
}


class Arbeitsstand(unittest.TestCase):
    """Der Server hat seinen eigenen Riss."""

    JPEG = b'\xff\xd8\xff' + b'x' * 200
    PNG = b'\x89PNG\r\n\x1a\n' + b'x' * 200

    def setUp(self):
        self.verz = tempfile.mkdtemp(prefix='mave-riss-')
        self._alt = (srv.GRUNDRISS_DATEI, srv.GRUNDRISS_BILD)
        srv.GRUNDRISS_DATEI = Path(self.verz) / 'grundriss.json'
        srv.GRUNDRISS_BILD = Path(self.verz) / 'grundriss-vorlage.jpg'

    def tearDown(self):
        srv.GRUNDRISS_DATEI, srv.GRUNDRISS_BILD = self._alt
        shutil.rmtree(self.verz, ignore_errors=True)

    def lesen(self):
        return json.loads(bytes(srv.logbuch_grundriss({}).body))

    def schreiben(self, riss):
        antwort = asyncio.run(srv.logbuch_grundriss_setzen(Anfrage(json_daten=riss), {}))
        return json.loads(bytes(antwort.body))

    def hochladen(self, daten):
        return asyncio.run(srv.logbuch_grundriss_vorlage_setzen(Anfrage(daten), {}))

    # ── Der Riss ───────────────────────────────────────────────────────────

    def test_leer_bis_etwas_gezeichnet_ist(self):
        self.assertEqual(self.lesen().get('raeume'), None)

    def test_speichern_und_zuruecklesen(self):
        self.schreiben(RISS)
        self.assertEqual(len(self.lesen()['raeume']), 1)
        self.assertEqual(self.lesen()['name'], 'Mave')

    def test_geprueft_wird_mit_demselben_raster_wie_am_boot(self):
        """Sonst nimmt der Server etwas an, das der Pi ablehnt — und das fällt
        erst beim Laden auf."""
        with self.assertRaises(HTTPException):
            self.schreiben({**RISS, 'raeume': [{'id': 'A B', 'form': RISS['raeume'][0]['form']}]})
        with self.assertRaises(HTTPException):
            self.schreiben({**RISS, 'rumpf': 'M0,0 <script>'})

    def test_unbekannte_felder_ueberleben_nicht(self):
        """Der Prüfer BAUT ein neues Objekt, statt das eingehende zu säubern."""
        geprueft = self.schreiben({**RISS, 'heimlich': 'javascript:alert(1)'})
        self.assertNotIn('heimlich', geprueft)

    def test_kaputtes_json_wird_abgelehnt(self):
        with self.assertRaises(HTTPException) as e:
            asyncio.run(srv.logbuch_grundriss_setzen(Anfrage(b'{'), {}))
        self.assertEqual(e.exception.status_code, 400)

    # ── Die Planvorlage ────────────────────────────────────────────────────

    def test_jpeg_und_png_kommen_durch(self):
        for daten in (self.JPEG, self.PNG):
            self.assertEqual(json.loads(bytes(self.hochladen(daten).body))['bytes'], len(daten))
            self.assertEqual(srv.GRUNDRISS_BILD.read_bytes(), daten)

    def test_alles_andere_nicht(self):
        """Eine SVG-Datei wäre ein Dokument mit Skripten darin, kein Bild —
        und sie wird später wieder ausgeliefert."""
        for daten in (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
                      b'GIF89a' + b'x' * 80, b'%PDF-1.4', b'irgendwas'):
            with self.assertRaises(HTTPException, msg=daten[:12]):
                self.hochladen(daten)

    def test_leer_und_zu_gross(self):
        with self.assertRaises(HTTPException):
            self.hochladen(b'')
        with self.assertRaises(HTTPException) as e:
            self.hochladen(self.JPEG + b'x' * (3 * 1024 * 1024))
        self.assertEqual(e.exception.status_code, 413)

    def test_ohne_vorlage_ein_ehrliches_404(self):
        with self.assertRaises(HTTPException) as e:
            srv.logbuch_grundriss_vorlage({})
        self.assertEqual(e.exception.status_code, 404)

    def test_der_riss_sagt_ob_eine_vorlage_daliegt(self):
        """Sonst müsste das Werkzeug danach fragen — und ein 404 auf diese
        Frage ist ein roter Fehler in jeder Konsole."""
        self.assertFalse(self.lesen()['hat_vorlage'])
        self.hochladen(self.JPEG)
        self.assertTrue(self.lesen()['hat_vorlage'])
        self.assertTrue(self.schreiben(RISS)['hat_vorlage'])

    def test_entfernen_geht_auch_wenn_nichts_da_ist(self):
        srv.logbuch_grundriss_vorlage_weg({})
        self.hochladen(self.JPEG)
        srv.logbuch_grundriss_vorlage_weg({})
        self.assertFalse(srv.GRUNDRISS_BILD.exists())


class WegAnsBoot(unittest.TestCase):
    """Was das Boot annimmt, muss der Server hinausgeben dürfen."""

    def test_der_riss_darf_durchgeleitet_werden(self):
        """Ohne Eintrag in DURCHLEITEN gibt es den Weg nicht — der Server
        bietet nur an, was dort steht."""
        from server.befehle import DURCHLEITEN
        eintraege = {(m, p) for m, p, _ in DURCHLEITEN}
        self.assertIn(('PUT', '/api/grundriss'), eintraege)

    def test_und_die_einstellungen_auch(self):
        """Hier stand PATCH gegen POST — damit liess sich aus der Ferne
        ueberhaupt keine Einstellung aendern."""
        from server.befehle import DURCHLEITEN
        self.assertIn(('PATCH', '/api/settings'), {(m, p) for m, p, _ in DURCHLEITEN})


if __name__ == '__main__':
    unittest.main()
