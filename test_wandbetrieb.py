"""Wandbetrieb: Bildschirm anlassen, Nachtmodus, Vollbild-Installation.

Drei Dinge fuer das Tablet, das fest an der Wand haengt.

Der groessere Teil davon liegt im Browser, nicht in Python — deshalb laeuft
hier ein echtes Chromium mit. Ohne das waere gerade das Geprueft, was ohnehin
kaum brechen kann, und der Sonnenstand — die einzige richtige Rechnung in der
Datei — bliebe ungeprueft.

Ist Playwright nicht da, werden die Browser-Faelle uebersprungen statt zu
scheitern: sie sagen dann nichts, aber sie luegen auch nicht.

Aufruf:

    ./venv/bin/python -m unittest test_wandbetrieb -v
"""
import os
import pathlib
import threading
import unittest

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

import asyncio                                 # noqa: E402

import main                                    # noqa: E402
from js_bundle_liste import DIAGNOSE_FILES, JS_FILES   # noqa: E402

HIER = pathlib.Path(__file__).parent
JS = HIER / 'static' / 'js'

try:
    from playwright.sync_api import sync_playwright
except ImportError:                            # pragma: no cover
    sync_playwright = None


class FalscheAnfrage:
    """Das Wenigste, was die Route braucht."""

    def __init__(self, etag: str = ''):
        self.headers = {'if-none-match': etag} if etag else {}


def hole(pfad: str, etag: str = ''):
    """Die Route aufrufen — in einem eigenen Faden.

    Playwrights synchrone Schnittstelle haelt im aufrufenden Faden eine eigene
    Ereignisschleife am Laufen. `asyncio.run` weigert sich dann, und zwar zu
    Recht. Ein frischer Faden hat keine.
    """
    route = {'/': main.root, '/wand': main.wandseite}[pfad]
    kasten = {}

    def lauf():
        kasten['antwort'] = asyncio.run(route(FalscheAnfrage(etag)))

    faden = threading.Thread(target=lauf)
    faden.start()
    faden.join()
    return kasten['antwort']


# ── Die Wandfassung der Seite ──────────────────────────────────────────────

class VollbildInstallation(unittest.TestCase):
    """/wand ist dieselbe Seite mit einem anderen Manifest.

    Der Punkt der Uebung: eine als `display: fullscreen` INSTALLIERTE Anwendung
    startet auch nach dem Entsperren ohne Browserleiste. Waere es dasselbe
    Manifest, gaelte das auch fuer das Telefon in der Hosentasche.
    """

    def test_wand_traegt_das_eigene_manifest(self):
        text = hole('/wand').body.decode()
        self.assertIn('href="/static/manifest-wand.json"', text)
        self.assertNotIn('href="/static/manifest.json"', text)

    def test_die_normale_seite_bleibt_unangetastet(self):
        text = hole('/').body.decode()
        self.assertIn('href="/static/manifest.json"', text)
        self.assertNotIn('manifest-wand', text)

    def test_sonst_ist_es_dieselbe_seite(self):
        """Nur das Manifest darf sich unterscheiden — nichts weiter.

        Sonst haette man zwei Oberflaechen zu pflegen, und die zweite faellt
        beim naechsten Umbau hinten runter.
        """
        a = hole('/').body.decode().replace('/static/manifest.json',
                                            '/static/manifest-wand.json')
        self.assertEqual(a, hole('/wand').body.decode())

    def test_eigene_kennung(self):
        """Verschiedener Inhalt, verschiedene ETags.

        Mit derselben Kennung bekaeme ein Browser, der die eine Fassung schon
        hat, fuer die andere ein leeres 304 — und damit die falsche Seite.
        """
        self.assertNotEqual(hole('/').headers['ETag'], hole('/wand').headers['ETag'])

    def test_unveraendert_gibt_304(self):
        etag = hole('/wand').headers['ETag']
        self.assertEqual(hole('/wand', etag).status_code, 304)

    def test_manifest_verlangt_vollbild(self):
        import json
        m = json.loads((HIER / 'static' / 'manifest-wand.json').read_text())
        self.assertEqual(m['display'], 'fullscreen')
        self.assertEqual(m['display_override'][0], 'fullscreen')
        # Eigene Kennung: daran unterscheidet Chrome die beiden Installationen.
        # Waere sie gleich, ueberschriebe die eine die andere.
        haupt = json.loads((HIER / 'static' / 'manifest.json').read_text())
        self.assertNotEqual(m['id'], haupt['id'])
        self.assertEqual(m['start_url'], '/wand')

    def test_wand_startet_auch_ohne_netz(self):
        """Die Wandfassung gehoert in die Huelle des Service Workers.

        Ohne das startet ausgerechnet die fest montierte Anwendung nicht, wenn
        das Boots-WLAN einmal weg ist.
        """
        sw = (HIER / 'static' / 'sw.js').read_text()
        self.assertIn("'/wand',", sw)
        self.assertIn("'/static/manifest-wand.json',", sw)


class ImBuendel(unittest.TestCase):
    """Der Nachtmodus gilt fuer den Bildschirm, nicht fuer eine Seite."""

    def test_beide_oberflaechen_haben_die_datei(self):
        self.assertIn('wandbetrieb.js', JS_FILES)
        self.assertIn('wandbetrieb.js', DIAGNOSE_FILES)

    def test_vor_diagnose(self):
        """diagnose.js ruft wandStart() beim Start — die Datei muss vorher da sein."""
        self.assertLess(DIAGNOSE_FILES.index('wandbetrieb.js'),
                        DIAGNOSE_FILES.index('diagnose.js'))

    def test_nach_display(self):
        """wandbetrieb.js fragt _dsp und _GESTE_MIN_BREITE aus display.js ab."""
        self.assertLess(JS_FILES.index('display.js'), JS_FILES.index('wandbetrieb.js'))


# ── Was im Browser laeuft ──────────────────────────────────────────────────

@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class ImBrowser(unittest.TestCase):
    """Der Sonnenstand und die Schalter — an einem echten Chromium."""

    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.pg = self._browser.new_page()
        # Eine echte Adresse, nicht set_content: auf `about:blank` hat das
        # Dokument keinen Ursprung, und localStorage wirft dort. Der Modul-Code
        # ueberlebt das (alle Zugriffe sind abgesichert), aber die Pruefung
        # koennte nicht nachsehen, ob wirklich gespeichert wurde.
        self.pg.route('**/*', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<div id="wandKnoepfe"></div>'))
        self.pg.goto('http://mave.pruefstand/')
        # add_script_tag statt evaluate(quelltext): evaluate legt einen eigenen
        # Gueltigkeitsbereich an, in dem `let` auf oberster Ebene nicht nach
        # aussen wirkt — die Datei waere geladen und trotzdem nicht da.
        self.pg.add_script_tag(content=(JS / 'wandbetrieb.js').read_text())

    def tearDown(self):
        self.pg.close()

    # ── Sonnenstand ────────────────────────────────────────────────────────

    def sonne(self, lat, lon, iso):
        return self.pg.evaluate(
            '([la, lo, d]) => _sonnenzeiten(la, lo, new Date(d))', [lat, lon, iso])

    def test_kiel_im_sommer(self):
        """Kiel, Sommersonnenwende: auf 02:45 UTC, unter 20:00 UTC.

        Werte aus der Ephemeride; erlaubt sind zwei Minuten Abweichung — das
        Verfahren rechnet mit genaeherten Bahnelementen.
        """
        z = self.sonne(54.32, 10.14, '2026-06-21T12:00:00Z')
        self.assertAlmostEqual(z['auf'], 2.75, delta=0.04)
        self.assertAlmostEqual(z['unter'], 20.00, delta=0.04)

    def test_kiel_im_winter(self):
        z = self.sonne(54.32, 10.14, '2026-12-21T12:00:00Z')
        self.assertAlmostEqual(z['auf'], 7.66, delta=0.05)
        self.assertAlmostEqual(z['unter'], 14.94, delta=0.05)

    def test_am_aequator_steht_es_still(self):
        """Am Aequator ist der Tag das ganze Jahr ueber gleich lang."""
        sommer = self.sonne(0.0, 0.0, '2026-06-21T12:00:00Z')
        winter = self.sonne(0.0, 0.0, '2026-12-21T12:00:00Z')
        for z in (sommer, winter):
            self.assertAlmostEqual(z['unter'] - z['auf'], 12.1, delta=0.15)

    def test_polarnacht_gibt_null(self):
        """Ueber dem Polarkreis geht die Sonne nicht auf. Dann gibt es keine
        Zeiten — und die Automatik faellt auf die Uhr zurueck, statt eine
        Daemmerung zu erfinden."""
        self.assertIsNone(self.sonne(78.2, 15.6, '2026-12-21T12:00:00Z'))
        self.assertIsNone(self.sonne(78.2, 15.6, '2026-06-21T12:00:00Z'))

    def test_dunkel_zur_richtigen_zeit(self):
        dunkel = lambda iso: self.pg.evaluate(              # noqa: E731
            '([la, lo, d]) => _istDunkel(la, lo, new Date(d))', [54.32, 10.14, iso])
        # Mitte Januar, Kiel: 12 Uhr UTC ist hell, 22 Uhr ist dunkel.
        self.assertFalse(dunkel('2026-01-15T12:00:00Z'))
        self.assertTrue(dunkel('2026-01-15T22:00:00Z'))
        self.assertTrue(dunkel('2026-01-15T05:00:00Z'))
        # Mitte Juni: um 21 Uhr UTC (23 Uhr Ortszeit) ist es dunkel, um 03 nicht.
        self.assertTrue(dunkel('2026-06-15T21:30:00Z'))
        self.assertFalse(dunkel('2026-06-15T12:00:00Z'))

    def test_ohne_ort_entscheidet_die_uhr(self):
        """Kein Ort heisst nicht "kein Nachtmodus" — nur eine gröbere Regel."""
        self.pg.evaluate("() => { _wandLaden(); _wand.nacht = 'auto'; _wandOrt = null;"
                         " _nachtPruefen(); }")
        # Wahr oder falsch je nach Tageszeit — geprueft wird, dass es entscheidet
        # und nicht abbricht.
        self.assertIsInstance(
            self.pg.evaluate("() => document.documentElement.classList.contains('nachtmodus')"),
            bool)

    # ── Der Schalter ───────────────────────────────────────────────────────

    def test_umschalten_setzt_die_klasse(self):
        self.pg.evaluate('() => { _wandLaden(); nachtUmschalten(); }')
        self.assertTrue(self.pg.evaluate(
            "() => document.documentElement.classList.contains('nachtmodus')"))
        self.pg.evaluate('() => nachtUmschalten()')
        self.assertFalse(self.pg.evaluate(
            "() => document.documentElement.classList.contains('nachtmodus')"))

    def test_von_hand_schlaegt_die_automatik(self):
        """Wer von Hand schaltet, will das auch so.

        Bliebe die Automatik stehen, drehte sie die Wahl beim naechsten Takt
        wieder um — und der Schalter waere scheinbar kaputt.
        """
        art = self.pg.evaluate("() => { _wandLaden(); nachtAutomatik(true);"
                               " nachtUmschalten(); return _wand.nacht; }")
        self.assertIn(art, ('an', 'aus'))

    def test_wahl_ueberlebt_das_neuladen(self):
        self.pg.evaluate('() => { _wandLaden(); nachtUmschalten(); }')
        gespeichert = self.pg.evaluate(
            "() => JSON.parse(localStorage.getItem('mave_wandbetrieb')).nacht")
        self.assertEqual(gespeichert, 'an')

    def test_uebersteuerung_schlaegt_die_vermutung(self):
        """Ein Tablet ist vom Telefon nicht zu unterscheiden — deshalb darf die
        Vermutung nie das letzte Wort haben."""
        self.pg.evaluate("() => { window._dsp = { wandschalter: 'nie' }; }")
        self.assertFalse(self.pg.evaluate('() => _istWandgeraet()'))
        self.pg.evaluate("() => { window._dsp = { wandschalter: 'immer' }; }")
        self.assertTrue(self.pg.evaluate('() => _istWandgeraet()'))

    def test_der_knopf_zeigt_ob_die_sperre_steht(self):
        """Nicht ob der Schalter an ist — ob die Sperre HAELT.

        Genau dieser Unterschied fuehrt sonst zu "der Schalter steht auf an,
        und das Ding schlaeft trotzdem ein".
        """
        html = self.pg.evaluate("""() => {
            window._dsp = { wandschalter: 'immer' };
            _wandLaden(); _wand.wachhalten = true; _wachSperre = null;
            _wandKnopfSetzen();
            return document.getElementById('wandKnoepfe').innerHTML;
        }""")
        self.assertIn('wartet', html)
        self.assertNotIn('wand-knopf an', html)

        html = self.pg.evaluate("""() => {
            _wachSperre = { release: () => {} };
            _wandKnopfSetzen();
            return document.getElementById('wandKnoepfe').innerHTML;
        }""")
        self.assertIn('wand-knopf an', html)
        self.assertNotIn('wartet', html)

    def test_ohne_speicher_geht_es_trotzdem(self):
        """Manche Geraete verweigern localStorage — Kiosk-Aufsaetze, strenge
        Datenschutzeinstellungen, ein Dokument ohne Ursprung. Dann soll der
        Nachtmodus fuer diese Sitzung gelten und nicht die Seite mitreissen."""
        seite = self._browser.new_page()
        try:
            seite.set_content('<div id="wandKnoepfe"></div>')   # ohne Ursprung
            seite.add_script_tag(content=(JS / 'wandbetrieb.js').read_text())
            seite.evaluate('() => { wandStart(); nachtUmschalten(); }')
            self.assertTrue(seite.evaluate(
                "() => document.documentElement.classList.contains('nachtmodus')"))
        finally:
            seite.close()

    def test_ohne_wandgeraet_nur_der_mond(self):
        """Der Wachschalter ist fuer fest montierte Geraete. Der Nachtmodus
        gilt ueberall — nachts sitzt man genauso am Laptop am Kartentisch."""
        html = self.pg.evaluate("""() => {
            window._dsp = { wandschalter: 'nie' };
            _wandLaden(); _wandKnopfSetzen();
            return document.getElementById('wandKnoepfe').innerHTML;
        }""")
        self.assertIn('nachtUmschalten', html)
        self.assertNotIn('wachUmschalten', html)


if __name__ == '__main__':
    unittest.main()
