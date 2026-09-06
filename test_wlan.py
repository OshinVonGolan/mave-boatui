"""Gäste-WLAN: der QR-Code, den Besucher scannen.

Der wunde Punkt ist die Maskierung. Ein QR-Code mit einer falsch aufgebauten
WIFI-Zeichenkette sieht aus wie jeder andere — das Telefon sagt nur, dass der
Beitritt nicht klappt, und gesucht wird der Fehler dann beim WLAN. Deshalb
steht die Zeichenkette hier Zeichen für Zeichen in den Prüfungen.

Dass ein damit erzeugter Code tatsächlich wieder auslesbar ist, wurde beim Bau
einmal mit einem Decoder (zxing-cpp) nachgewiesen: aus dem SVG kam
`WIFI:T:WPA;S:Mave\;Gast;P:seemannsgarn;;` zurück. Der Decoder ist bewusst
keine Prüfabhängigkeit — er zöge zwei Pakete nach, die auf dem Pi nichts zu
suchen haben.

Aufruf:

    ./venv/bin/python -m unittest test_wlan -v
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
from sync import rechte as r                   # noqa: E402
from sync import zugang as z                   # noqa: E402


def lauf(x):
    return asyncio.get_event_loop().run_until_complete(x)


class Anfrage:
    """Gerade so viel Request, wie `_json_body` braucht: Kopfzeilen und einen
    Strom. Ein echter Starlette-Request braeuchte einen ASGI-Kontext."""

    def __init__(self, koerper):
        self._roh = json.dumps(koerper).encode()
        self.headers = {'content-length': str(len(self._roh))}

    async def stream(self):
        yield self._roh


class Kette(unittest.TestCase):
    """Die WIFI-Zeichenkette."""

    def kette(self, **kw):
        w = {'ssid': 'Mave', 'passwort': 'seemannsgarn', 'art': 'WPA',
             'versteckt': False, **kw}
        return main.wlan_kette(w)

    def test_der_einfache_fall(self):
        self.assertEqual(self.kette(),
                         'WIFI:T:WPA;S:Mave;P:seemannsgarn;;')

    def test_semikolon_im_namen_wird_maskiert(self):
        """Sonst endet der Name für das Telefon beim Semikolon."""
        self.assertEqual(self.kette(ssid='Mave;Gast'),
                         'WIFI:T:WPA;S:Mave\;Gast;P:seemannsgarn;;')

    def test_der_backslash_zuerst(self):
        """Wird er nicht zuerst maskiert, maskiert man die eigenen
        Maskierungen gleich mit."""
        self.assertEqual(self.kette(passwort='a\\b;c'),
                         'WIFI:T:WPA;S:Mave;P:a\\\\b\;c;;')

    def test_alle_sonderzeichen(self):
        self.assertEqual(self.kette(passwort='a:b"c,d'),
                         'WIFI:T:WPA;S:Mave;P:a\\:b\\"c\\,d;;')

    def test_hexverdaechtiges_passwort_kommt_in_anfuehrungszeichen(self):
        """Sieht ein Passwort wie eine Hexzahl aus, liest das Telefon es als
        rohen Schlüssel statt als Text — und tritt dem Netz nicht bei."""
        self.assertEqual(self.kette(passwort='0a1b2c3d'),
                         'WIFI:T:WPA;S:Mave;P:"0a1b2c3d";;')

    def test_normales_passwort_bekommt_keine_anfuehrungszeichen(self):
        self.assertNotIn('"', self.kette(passwort='seemannsgarn'))

    def test_ohne_passwort_faellt_das_feld_weg(self):
        self.assertEqual(self.kette(art='nopass', passwort='egal'),
                         'WIFI:T:nopass;S:Mave;;')

    def test_verstecktes_netz(self):
        self.assertIn(';H:true;', self.kette(versteckt=True))


class Endpunkte(unittest.TestCase):

    def setUp(self):
        self.verz = Path(tempfile.mkdtemp(prefix='mave-wlan-'))
        self._alt = main.WLAN_FILE
        main.WLAN_FILE = self.verz / 'wlan.json'

    def tearDown(self):
        main.WLAN_FILE = self._alt
        shutil.rmtree(self.verz, ignore_errors=True)

    def test_ohne_eintrag_ist_nichts_eingerichtet(self):
        d = lauf(main.get_wlan())
        self.assertFalse(d['eingerichtet'])
        self.assertEqual(d['ssid'], '')

    def test_ohne_eintrag_gibt_es_keinen_code(self):
        """404 und nicht ein leeres Bild: die Oberfläche soll den Unterschied
        zwischen „nicht eingerichtet" und „kaputt" zeigen können."""
        with self.assertRaises(HTTPException) as f:
            lauf(main.get_wlan_qr())
        self.assertEqual(f.exception.status_code, 404)

    def test_speichern_und_wieder_lesen(self):
        lauf(main.put_wlan(Anfrage({'ssid': 'Mave-Gast', 'passwort': 'seemannsgarn'})))
        d = lauf(main.get_wlan())
        self.assertEqual((d['ssid'], d['passwort'], d['art']),
                         ('Mave-Gast', 'seemannsgarn', 'WPA'))
        self.assertTrue(d['eingerichtet'])

    def test_zu_kurzes_wpa_passwort_wird_abgelehnt(self):
        """WPA verlangt acht Zeichen. Ein kürzeres ergäbe einen Code, den jedes
        Telefon annimmt und an dem jeder Beitritt scheitert."""
        with self.assertRaises(HTTPException) as f:
            lauf(main.put_wlan(Anfrage({'ssid': 'X', 'passwort': 'kurz'})))
        self.assertEqual(f.exception.status_code, 400)

    def test_ohne_passwort_ist_kurz_egal(self):
        lauf(main.put_wlan(Anfrage({'ssid': 'Hafen', 'passwort': '', 'art': 'nopass'})))
        self.assertTrue(lauf(main.get_wlan())['eingerichtet'])

    def test_leerzeichen_am_rand_bleiben_stehen(self):
        """Ein Netzname DARF auf ein Leerzeichen enden. Das `.strip()`, das hier
        stand, machte daraus stillschweigend einen anderen Namen — der QR-Code
        war gültig und führte trotzdem in kein Netz (Eignermeldung)."""
        lauf(main.put_wlan(Anfrage({'ssid': 'SY_Mave ', 'passwort': 'seemannsgarn'})))
        self.assertEqual(lauf(main.get_wlan())['ssid'], 'SY_Mave ')

    def test_das_leerzeichen_steht_auch_im_code(self):
        lauf(main.put_wlan(Anfrage({'ssid': ' Rand ', 'passwort': 'seemannsgarn'})))
        self.assertEqual(main.wlan_kette(main._wlan_lesen()),
                         'WIFI:T:WPA;S: Rand ;P:seemannsgarn;;')

    def test_unbekannte_art_faellt_auf_wpa_zurueck(self):
        lauf(main.put_wlan(Anfrage({'ssid': 'X', 'passwort': 'seemannsgarn',
                                    'art': 'quatsch'})))
        self.assertEqual(lauf(main.get_wlan())['art'], 'WPA')

    def test_der_code_ist_ein_svg(self):
        lauf(main.put_wlan(Anfrage({'ssid': 'Mave-Gast', 'passwort': 'seemannsgarn'})))
        antwort = lauf(main.get_wlan_qr())
        self.assertEqual(antwort.media_type, 'image/svg+xml')
        text = antwort.body.decode()
        self.assertIn('<svg', text)
        # Dunkel auf HELL — umgekehrt lesen manche Telefone nicht. segno kuerzt
        # die Farben auf drei Stellen, deshalb wird darauf geprueft.
        self.assertIn('fill="#fff"', text)
        self.assertIn('stroke="#000"', text)

    def test_der_code_wird_nicht_zwischengespeichert(self):
        """Sonst zeigt das Tablet nach einer Änderung den alten Code weiter."""
        lauf(main.put_wlan(Anfrage({'ssid': 'Mave-Gast', 'passwort': 'seemannsgarn'})))
        antwort = lauf(main.get_wlan_qr())
        self.assertEqual(antwort.headers.get('cache-control'), 'no-store')

    def test_die_datei_gehoert_nur_dem_dienst(self):
        lauf(main.put_wlan(Anfrage({'ssid': 'Mave-Gast', 'passwort': 'seemannsgarn'})))
        self.assertEqual(main.WLAN_FILE.stat().st_mode & 0o777, 0o600)


class Rechte(unittest.TestCase):
    """Lesen darf jeder Angemeldete, ändern nur, wer einstellen darf."""

    def test_anzeigen_verlangt_lesen(self):
        self.assertEqual(z.recht_fuer('GET', '/api/wlan'), r.LESEN)
        self.assertEqual(z.recht_fuer('GET', '/api/wlan/qr.svg'), r.LESEN)

    def test_aendern_verlangt_einstellen(self):
        """Ohne eigene Zeile fiele der PUT unter die Vorgabe „schalten", und
        die hat jedes Crewmitglied."""
        self.assertEqual(z.recht_fuer('PUT', '/api/wlan'), r.EINSTELLEN)

    def test_ohne_anmeldung_geht_gar_nichts(self):
        erlaubt, code, _ = z.pruefen(None, 'GET', '/api/wlan', schonfrist=False)
        self.assertFalse(erlaubt)
        self.assertEqual(code, 401)


if __name__ == '__main__':
    unittest.main()
