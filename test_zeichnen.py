"""Was die Oberflaeche bei jedem Datensatz NICHT mehr tut.

Gemessen am 04.09.2026: ein Datensatz kostete auf einem vierfach gedrosselten
Kern 7,7 ms, und die Live-Verbindung lieferte bis zu zwanzig davon je Sekunde.
Drei Posten trugen das meiste, und alle drei waren Arbeit ohne Anlass:

  * `_kopfLogoPruefen` mass nach, ob derselbe unveraenderte Versionstext noch
    neben den Schriftzug passt — mit einem erzwungenen Neuberechnen des Layouts.
  * die Wartungskachel wurde neu gebaut, obwohl der Wartungsplan gar nicht aus
    der Live-Verbindung kommt.
  * die SOC-Kurve ueber sechs Stunden wurde neu gezeichnet, obwohl hoechstens
    alle fuenf Sekunden ein Punkt dazukommt.

Danach: 1,6 ms. Diese Pruefungen halten das fest — die drei Aufrufe schleichen
sich sonst beim naechsten Umbau lautlos zurueck.

Die Oberflaeche laeuft dafuer in einem echten Chromium gegen einen echten
Server; anders ist "wird nicht aufgerufen" nicht zu pruefen.

Aufruf:

    ./venv/bin/python -m unittest test_zeichnen -v
"""
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

HIER = pathlib.Path(__file__).parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:                            # pragma: no cover
    sync_playwright = None

KONTO, PASSWORT = 'pruefer', 'Pruefstand-2026!'
NOTZUGANG = 'notzugang-fuer-die-pruefung'

# Ein Datensatz mit gefuellten Werten: nur so laufen alle Zweige in handleData.
NUTZLAST = {
    'battery': {'voltage': 13.42, 'current': -8.3, 'soc': 78, 'power': -111,
                'consumed_ah': -42, 'cycles': 61, 'starter_voltage': 12.8,
                'time_since_full': 7200, 'temperature': 19.5, '_age_s': 0.4},
    'tanks': {'tank1': 61, 'tank2': 22, '_age_s': 0.6},
    'lights': {'channels': [120, 0, 80, 255, 0, 0, 40, 0, 0], '_age_s': 0.5},
    'solar': {'power': 210, 'voltage': 13.6, '_age_s': 0.7},
    'bms': {'voltage': 13.4, 'soc': 78, 'lowest_cell_v': 3.31, 'highest_cell_v': 3.36,
            'cell_count': 4, 'cells': [], '_age_s': 0.5},
    'inverter': {'state': 9, 'power': 180, '_age_s': 0.5},
    'charger': {'state': 3, '_age_s': 2.0},
    'alarms': [], 'unack_alarms': 0, 'version': '9.9.9',
}


def freier_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class Pruefstand(unittest.TestCase):
    """Ein eigener Server, ein eigener Browser, ein Konto — sonst nichts.

    Traegt selbst keine Pruefungen: die beiden Klassen darunter teilen sich den
    Aufbau, sollen aber nicht die Pruefungen der jeweils anderen mitlaufen
    lassen.
    """

    @classmethod
    def setUpClass(cls):
        cls.port = freier_port()
        cls.verz = tempfile.mkdtemp(prefix='mave-pruef-')
        umgebung = {**os.environ, 'MAVE_DATEN': cls.verz,
                    'MAVE_STATISCH': str(HIER / 'static'),
                    'MAVE_PASSWORT': NOTZUGANG,
                    'MAVE_GERAET_TOKEN': 'pruef-geraet'}
        cls.server = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1',
             '--port', str(cls.port), '--log-level', 'warning'],
            cwd=str(HIER), env=umgebung,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.basis = f'http://127.0.0.1:{cls.port}'
        for _ in range(80):
            try:
                urllib.request.urlopen(cls.basis + '/api/zugang', timeout=1).read()
                break
            except Exception:
                time.sleep(0.25)
        else:                                   # pragma: no cover
            cls.tearDownClass()
            raise RuntimeError('Server kam nicht hoch')
        # Erstes Konto ueber den Notzugang — danach greift die Anmeldepflicht.
        anfrage = urllib.request.Request(
            cls.basis + '/api/konten', method='POST',
            data=(f'{{"name":"{KONTO}","passwort":"{PASSWORT}","rolle":"eigner"}}').encode(),
            headers={'Authorization': f'Bearer {NOTZUGANG}',
                     'Content-Type': 'application/json'})
        urllib.request.urlopen(anfrage, timeout=10).read()

        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        for zu in (getattr(cls, '_browser', None), getattr(cls, '_pw', None)):
            try:
                (zu.close if hasattr(zu, 'close') else zu.stop)()
            except Exception:
                pass
        if getattr(cls, 'server', None):
            cls.server.terminate()
            cls.server.wait(timeout=10)
        shutil.rmtree(getattr(cls, 'verz', ''), ignore_errors=True)

    def setUp(self):
        self.pg = self._browser.new_page(viewport={'width': 1200, 'height': 800})
        self.fehler = []
        self.pg.on('pageerror', lambda e: self.fehler.append(str(e)[:200]))
        self.pg.goto(self.basis + '/', wait_until='domcontentloaded', timeout=30000)
        self.pg.wait_for_timeout(2000)
        if self.pg.evaluate("() => !!document.querySelector('.anmeldung:not(.hidden)')"):
            self.pg.fill('#anmName', KONTO)
            self.pg.fill('#anmPw', PASSWORT)
            self.pg.click('#anmKnopf')
            self.pg.wait_for_timeout(4000)

    def tearDown(self):
        self.assertEqual(self.fehler, [], 'Die Seite hat Fehler geworfen')
        self.pg.close()

    def zaehlen(self, name: str, laeufe: int = 5, daten: dict | None = None) -> int:
        """Wie oft `name` bei `laeufe` Datensaetzen aufgerufen wird."""
        return self.pg.evaluate("""([n, k, d]) => {
            const echt = window[n];
            let zahl = 0;
            window[n] = function (...a) { zahl++; return echt.apply(this, a); };
            try {
                handleData({ ...d });                       // erster Durchlauf
                zahl = 0;                                   // der zaehlt nicht
                for (let i = 0; i < k; i++) {
                    handleData({ ...d, battery: { ...d.battery, soc: 70 + i } });
                }
            } finally { window[n] = echt; }
            return zahl;
        }""", [name, laeufe, daten or NUTZLAST])


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class Zeichenaufwand(Pruefstand):
    """Was bei jedem Datensatz NICHT mehr laeuft."""

    # ── Die Kopfzeile ──────────────────────────────────────────────────────

    def test_logo_nur_bei_neuer_version_nachmessen(self):
        self.assertEqual(self.zaehlen('_kopfLogoPruefen'), 0)

    def test_logo_doch_wenn_die_version_wechselt(self):
        """Der Grund, warum es ueberhaupt dort steht, muss erhalten bleiben:
        ein laengerer Text passt womoeglich nicht mehr neben den Schriftzug."""
        n = self.pg.evaluate("""(d) => {
            const echt = _kopfLogoPruefen;
            let zahl = 0;
            window._kopfLogoPruefen = function (...a) { zahl++; return echt.apply(this, a); };
            try {
                handleData({ ...d, version: '1.0.0' });
                zahl = 0;
                handleData({ ...d, version: '2.0.0-eine-viel-laengere-fassung' });
            } finally { window._kopfLogoPruefen = echt; }
            return zahl;
        }""", NUTZLAST)
        self.assertEqual(n, 1)

    def test_die_version_steht_trotzdem_in_der_kopfzeile(self):
        self.pg.evaluate("(d) => handleData({ ...d, version: '3.2.1' })", NUTZLAST)
        self.assertEqual(
            self.pg.evaluate("() => document.getElementById('versionBadge').textContent"),
            'v3.2.1')

    # ── Die Wartungskachel ─────────────────────────────────────────────────

    def test_wartungskachel_nicht_bei_jedem_wert(self):
        """Der Wartungsplan kommt aus /api/wartung, nicht aus der Live-Verbindung."""
        self.assertEqual(self.zaehlen('updateWartungHomeTile'), 0)

    def test_wartungskachel_beim_groessenwechsel(self):
        n = self.pg.evaluate("""() => {
            const echt = updateWartungHomeTile;
            let zahl = 0;
            window.updateWartungHomeTile = function (...a) { zahl++; return echt.apply(this, a); };
            try { window.dispatchEvent(new Event('resize')); }
            finally { window.updateWartungHomeTile = echt; }
            return zahl;
        }""")
        self.assertGreaterEqual(n, 1)

    def test_der_tageswechsel_hat_einen_eigenen_takt(self):
        """"In 3 Tagen faellig" wird irgendwann "ueberfaellig" — dafuer laeuft ein
        langsamer Takt, statt bei jedem Messwert nachzurechnen."""
        self.assertTrue(self.pg.evaluate(
            "() => !!(_wartungTagPoller && _wartungTagPoller.intervalMs >= 30000)"))

    # ── Die SOC-Kurve ──────────────────────────────────────────────────────

    def test_kurve_nicht_ohne_neue_punkte(self):
        """Sie zeigt sechs Stunden; histData bekommt hoechstens alle fuenf
        Sekunden einen Punkt dazu."""
        gemessen = self.pg.evaluate("""(d) => {
            const canvas = document.getElementById('battWideChart');
            let zahl = 0;
            // offsetWidth haengt an HTMLElement, nicht am Canvas-Prototyp.
            const eigen = Object.getOwnPropertyDescriptor(
                HTMLElement.prototype, 'offsetWidth');
            Object.defineProperty(canvas, 'offsetWidth', {
                configurable: true,
                get() { zahl++; return eigen.get.call(this); },
            });
            try {
                handleData({ ...d });
                zahl = 0;
                for (let i = 0; i < 12; i++) {
                    handleData({ ...d, battery: { ...d.battery, soc: 70 + i } });
                }
            } finally { delete canvas.offsetWidth; }
            return zahl;
        }""", NUTZLAST)
        # Zwoelf Datensaetze, hoechstens zwei Messungen.
        #
        # Nicht null: waehrend der zwoelf Durchlaeufe kann sich der Verlauf
        # tatsaechlich aendern — ein Punkt kommt im Fuenf-Sekunden-Takt dazu,
        # oder einer faellt am anderen Ende aus dem Fenster. Dann ist eine
        # Messung richtig. Die Aussage ist: nicht mehr bei JEDEM Datensatz.
        self.assertLessEqual(gemessen, 2,
                             f'{gemessen} Messungen bei 12 Datensaetzen — '
                             'die Leinwand wird immer noch je Frame vermessen')

    def test_kurve_doch_wenn_ein_punkt_dazukommt(self):
        n = self.pg.evaluate("""(d) => {
            const echt = _renderBattWideChart;
            let gezeichnet = 0;
            window._renderBattWideChart = function (...a) {
                const vorher = _wideStand;
                const r = echt.apply(this, a);
                if (_wideStand !== vorher) gezeichnet++;
                return r;
            };
            try {
                handleData({ ...d });
                gezeichnet = 0;
                histData.push({ ts: Math.floor(Date.now() / 1000), soc: 77 });
                handleData({ ...d });
            } finally { window._renderBattWideChart = echt; }
            return gezeichnet;
        }""", NUTZLAST)
        self.assertEqual(n, 1)

    def test_kurve_laesst_sich_erzwingen(self):
        """Beim Groessenwechsel aendert sich die Leinwand, nicht die Kurve —
        ohne diesen Weg bliebe sie nach dem Drehen des Tablets falsch skaliert."""
        self.assertTrue(self.pg.evaluate("""() => {
            let gerufen = false;
            const canvas = document.getElementById('battWideChart');
            // offsetWidth haengt an HTMLElement, nicht am Canvas-Prototyp.
            const eigen = Object.getOwnPropertyDescriptor(
                HTMLElement.prototype, 'offsetWidth');
            Object.defineProperty(canvas, 'offsetWidth', {
                configurable: true,
                get() { gerufen = true; return eigen.get.call(this); },
            });
            try { _renderBattWideChart(true); } finally { delete canvas.offsetWidth; }
            return gerufen;
        }"""))



@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class VollbildAngebot(Pruefstand):
    """Wann die Schaltfläche „Vollbild wiederherstellen" erscheinen darf.

    Sie hing an `document.fullscreenElement`. In der installierten Wandfassung
    ist der Bildschirm voll, ohne dass die Fullscreen-API je beteiligt war —
    das Feld bleibt leer, und die Schaltfläche stand dauerhaft unten auf der
    Seite und bot etwas an, das längst da war.
    """

    def vollbild_vortaeuschen(self, an: bool):
        """`display-mode: fullscreen` vortäuschen, ohne die API zu benutzen —
        genau die Lage der installierten Wandfassung."""
        self.pg.evaluate("""(an) => {
            if (!window._echtesMatchMedia) window._echtesMatchMedia = window.matchMedia;
            window.matchMedia = q => (an && q.includes('display-mode: fullscreen'))
                ? { matches: true, addEventListener() {}, addListener() {} }
                : window._echtesMatchMedia.call(window, q);
        }""", an)

    def test_pille_im_browser_wenn_gewuenscht(self):
        self.pg.evaluate("""() => {
            localStorage.setItem('mave_vollbild', '1');
            _vollbildPilleSetzen();
        }""")
        self.assertIsNotNone(self.pg.query_selector('#vollbildPille'))

    def test_keine_pille_in_der_wandfassung(self):
        self.vollbild_vortaeuschen(True)
        self.pg.evaluate("""() => {
            localStorage.setItem('mave_vollbild', '1');
            document.getElementById('vollbildPille')?.remove();
            _vollbildPilleSetzen();
        }""")
        self.assertIsNone(self.pg.query_selector('#vollbildPille'),
                          'Die Wandfassung bekommt ein Angebot, das nichts zu bieten hat')

    def test_menueeintrag_faellt_in_der_wandfassung_weg(self):
        self.vollbild_vortaeuschen(True)
        html = self.pg.evaluate("() => { burgerBauen(); return $('burgerMenu').innerHTML; }")
        self.assertNotIn('vollbildUmschalten()', html)

    def test_aber_nicht_waehrend_man_selbst_im_vollbild_steht(self):
        """Sonst gäbe es keinen Weg mehr hinaus — man hätte sich eingesperrt.

        `display-mode: fullscreen` ist auch dann wahr, wenn die Fullscreen-API
        dafür gesorgt hat; nur dann muss der Eintrag bleiben.
        """
        self.vollbild_vortaeuschen(True)
        self.pg.evaluate("""() => {
            Object.defineProperty(document, 'fullscreenElement', {
                configurable: true, get: () => document.documentElement });
        }""")
        try:
            html = self.pg.evaluate("() => { burgerBauen(); return $('burgerMenu').innerHTML; }")
            self.assertIn('vollbildUmschalten()', html)
            self.assertIn('Vollbild verlassen', html)
        finally:
            self.pg.evaluate("() => { delete document.fullscreenElement; }")



@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class AnsichtInDerAdresse(Pruefstand):
    """Neu laden auf einer Unterseite muss dort wieder herauskommen.

    Die Zuordnung Adresse → Ansicht hing nur am popstate-Horcher, und der feuert
    beim Neuladen nicht: wer auf der Verbindungsseite neu lud, landete auf der
    Startseite, während in der Adresse weiter #connectivity stand.
    """

    def neu_laden(self, hash_: str):
        """Laden, anmelden, und warten, bis die Seite WIRKLICH steht.

        Das Warten ist nicht Bequemlichkeit: die Adresse wird erst ausgewertet,
        wenn der Ladeschirm faellt (bis dahin vergehen im schlechtesten Fall
        acht Sekunden), und das Anmelden laedt die Seite noch einmal neu. Wer
        vorher nachsieht, misst den Zwischenzustand.
        """
        self.pg.goto(self.basis + '/' + hash_, wait_until='domcontentloaded',
                     timeout=30000)
        # RICHTIG neu laden. Der Sprung von / auf /#irgendwas bleibt im selben
        # Dokument — die Seite laedt dabei NICHT neu, init.js laeuft nicht noch
        # einmal, und geprueft waere der Zurueck-Weg statt des Neuladens. Genau
        # dieser Unterschied ist der Fehler, um den es hier geht.
        self.pg.reload(wait_until='domcontentloaded', timeout=30000)
        self._init_abwarten()
        if self.pg.evaluate("() => !!document.querySelector('.anmeldung:not(.hidden)')"):
            self.pg.fill('#anmName', KONTO)
            self.pg.fill('#anmPw', PASSWORT)
            self.pg.click('#anmKnopf')          # das laedt die Seite neu
            self.pg.wait_for_load_state('domcontentloaded')
            self._init_abwarten()

    def _init_abwarten(self):
        """Warten, bis init.js durchgelaufen ist.

        NICHT auf den Ladeschirm warten: den nimmt nach vier Sekunden die
        Notbremse in index.html weg, unabhaengig davon, ob das Buendel schon
        gelaufen ist. `_sparkPoller` entsteht als eines der letzten Dinge in
        init.js — steht er, ist die Adresse ausgewertet.
        """
        self.pg.wait_for_function("() => typeof _sparkPoller !== 'undefined'",
                                  timeout=25000)
        self.pg.wait_for_timeout(300)

    def test_verbindungsseite_ueberlebt_das_neuladen(self):
        self.neu_laden('#connectivity')
        self.assertFalse(self.pg.evaluate(
            "() => $('connInetOverlay').classList.contains('hidden')"),
            'nach dem Neuladen steht die Startseite statt der Verbindungsseite')
        self.assertEqual(self.pg.evaluate('() => location.hash'), '#connectivity')

    def test_auch_die_alarme(self):
        self.neu_laden('#alarms')
        self.assertFalse(self.pg.evaluate(
            "() => $('alarmOverlay').classList.contains('hidden')"))

    def test_zurueck_schliesst_die_ansicht_statt_die_app_zu_verlassen(self):
        """Nach einem Neuladen gäbe es sonst nichts, wohin zurück führen kann."""
        self.neu_laden('#connectivity')
        self.pg.go_back()
        self.pg.wait_for_timeout(1200)
        self.assertTrue(self.pg.evaluate(
            "() => $('connInetOverlay').classList.contains('hidden')"))
        self.assertEqual(self.pg.evaluate('() => location.hash'), '')

    def test_unbekannter_name_raeumt_die_adresse_auf(self):
        """Ein Verweis ins Leere soll nicht in der Adresszeile stehen bleiben."""
        self.neu_laden('#gibtesnicht')
        z = self.pg.evaluate("""() => ({
            hash: location.hash, href: location.href,
            init: typeof _sparkPoller, fn: typeof _adresseBeimStart,
            eintraege: history.length,
        })""")
        self.assertEqual(z['hash'], '', f'Zustand: {z}')

    def test_ohne_hash_bleibt_die_startseite(self):
        self.neu_laden('')
        self.assertTrue(self.pg.evaluate(
            "() => $('connInetOverlay').classList.contains('hidden')"))


if __name__ == '__main__':
    unittest.main()
