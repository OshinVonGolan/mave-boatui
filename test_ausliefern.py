"""Wie die Oberflaeche vom Pi kommt: gepackt, einmal, mit der richtigen Kennung.

Anlass ist eine Messung: nginx schob das 518 kB grosse Buendel bei jedem kalten
Abruf neu durch gzip — 0,54 bis 0,65 s bis zum ersten Byte, auf dem einzigen
Kern des Pi, und das Ergebnis war jedes Mal dasselbe. Jetzt wird beim Bauen
einmal gepackt und beides vorgehalten.

Das Heikle daran sind nicht die Bytes, sondern die Kennungen: liefen beide
Fassungen unter derselben ETag, koennte ein Zwischenspeicher einem Browser ohne
gzip die gepackten Bytes reichen — und der zeigte dann gar nichts.

Aufruf:

    ./venv/bin/python -m unittest test_ausliefern -v
"""
import gzip
import os
import threading
import unittest

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

import asyncio                                 # noqa: E402

import main                                    # noqa: E402


class Anfrage:
    """Nur die Kopfzeilen, die die Routen wirklich lesen."""

    def __init__(self, gzip_ok: bool = False, etag: str = ''):
        self.headers = {}
        if gzip_ok:
            self.headers['accept-encoding'] = 'gzip, deflate, br'
        if etag:
            self.headers['if-none-match'] = etag


def hole(pfad: str, gzip_ok: bool = False, etag: str = ''):
    """Die Route in einem eigenen Faden aufrufen (siehe test_wandbetrieb)."""
    route = {'/': main.root, '/wand': main.wandseite, '/js-bundle.js': main.js_bundle}[pfad]
    kasten = {}

    def lauf():
        kasten['a'] = asyncio.run(route(Anfrage(gzip_ok, etag)))

    faden = threading.Thread(target=lauf)
    faden.start()
    faden.join()
    return kasten['a']


WEGE = ('/', '/wand', '/js-bundle.js')


class GepacktAusliefern(unittest.TestCase):

    def test_gepackt_wenn_der_browser_es_mag(self):
        for weg in WEGE:
            with self.subTest(weg=weg):
                a = hole(weg, gzip_ok=True)
                self.assertEqual(a.headers.get('content-encoding'), 'gzip')

    def test_ungepackt_wenn_nicht(self):
        for weg in WEGE:
            with self.subTest(weg=weg):
                a = hole(weg)
                self.assertIsNone(a.headers.get('content-encoding'))

    def test_derselbe_inhalt(self):
        """Gepackt und ungepackt muessen Byte fuer Byte dasselbe ergeben."""
        for weg in WEGE:
            with self.subTest(weg=weg):
                self.assertEqual(gzip.decompress(hole(weg, gzip_ok=True).body),
                                 hole(weg).body)

    def test_es_lohnt_sich(self):
        """Wenn das Packen nichts spart, ist es nur Rechenzeit."""
        roh = len(hole('/js-bundle.js').body)
        gepackt = len(hole('/js-bundle.js', gzip_ok=True).body)
        self.assertLess(gepackt * 2, roh, f'{roh} -> {gepackt} B ist zu wenig Gewinn')


class KennungenTrennen(unittest.TestCase):
    """Der eigentliche Fallstrick: zwei Fassungen, zwei Kennungen."""

    def test_verschiedene_kennung(self):
        for weg in WEGE:
            with self.subTest(weg=weg):
                self.assertNotEqual(hole(weg).headers['ETag'],
                                    hole(weg, gzip_ok=True).headers['ETag'])

    def test_vary_gesetzt(self):
        """Ohne `Vary` darf kein Zwischenspeicher die beiden auseinanderhalten."""
        for weg in WEGE:
            with self.subTest(weg=weg):
                self.assertEqual(hole(weg).headers.get('Vary'), 'Accept-Encoding')

    def test_unveraendert_gibt_304(self):
        for weg in WEGE:
            for gz in (False, True):
                with self.subTest(weg=weg, gzip=gz):
                    marke = hole(weg, gzip_ok=gz).headers['ETag']
                    self.assertEqual(hole(weg, gzip_ok=gz, etag=marke).status_code, 304)

    def test_kennung_der_falschen_fassung_gibt_kein_304(self):
        """Sonst bekaeme ein Browser ohne gzip die gepackten Bytes als 'unveraendert'
        gemeldet und behielte, was er nie hatte."""
        for weg in WEGE:
            with self.subTest(weg=weg):
                gz_marke = hole(weg, gzip_ok=True).headers['ETag']
                a = hole(weg, gzip_ok=False, etag=gz_marke)
                self.assertEqual(a.status_code, 200)
                self.assertGreater(len(a.body), 0)

    def test_304_traegt_die_kennung_mit(self):
        """Ein 304 ohne ETag und Vary laesst den Zwischenspeicher raten."""
        marke = hole('/js-bundle.js', gzip_ok=True).headers['ETag']
        a = hole('/js-bundle.js', gzip_ok=True, etag=marke)
        self.assertEqual(a.headers.get('ETag'), marke)
        self.assertEqual(a.headers.get('Vary'), 'Accept-Encoding')


class NurEinmalPacken(unittest.TestCase):
    """Der ganze Sinn der Sache: nicht bei jedem Abruf neu."""

    def test_buendel_bleibt_dasselbe_objekt(self):
        hole('/js-bundle.js', gzip_ok=True)
        erstes = main._js_bundle['gz']
        for _ in range(3):
            hole('/js-bundle.js', gzip_ok=True)
        self.assertIs(main._js_bundle['gz'], erstes)

    def test_neu_bauen_wenn_sich_etwas_aendert(self):
        hole('/js-bundle.js', gzip_ok=True)
        alt = main._js_bundle['gz']
        main._js_bundle['mtime'] = 0.0          # so, als waere eine Datei angefasst
        hole('/js-bundle.js', gzip_ok=True)
        self.assertIsNot(main._js_bundle['gz'], alt)
        self.assertEqual(main._js_bundle['gz'], alt)   # Inhalt gleich, Objekt neu


if __name__ == '__main__':
    unittest.main()
