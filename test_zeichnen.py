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
import base64
import datetime
import json
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
        # try/catch: `const _sparkPoller` ist bis zu seiner Zeile in der
        # zeitlichen Totzone, und `typeof` WIRFT dort eine ReferenceError,
        # statt 'undefined' zu liefern. Ohne den Fang scheitert die Pruefung
        # zufaellig, je nachdem wie schnell das Buendel durchlaeuft.
        self.pg.wait_for_function(
            "() => { try { return typeof _sparkPoller !== 'undefined'; }"
            "        catch (_) { return false; } }", timeout=25000)
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



# Ein Heizungsschnappschuss, wie ihn der Hub liefert — mit einem Raum, der
# meldet, und einem, der still ist. Beide muessen im Feld auftauchen.
HEIZUNG = {
    'enabled': True, 'configured': True, 'reachable': True,
    'state': {
        'heater': {'mode': 'auto', 'state': 'heating', 'powerLevel': 62,
                   'flowTemp': 48.3, 'errorCode': 0, 'boiler': {'active': True}},
        'preset': {'name': 'Tag'},
        'rooms': [
            {'id': 1, 'name': 'Salon', 'conn': 'online', 'roomTemp': 19.4,
             'target': 20.0, 'wantsHeat': True},
            {'id': 2, 'name': 'Bugkabine', 'conn': 'offline', 'roomTemp': None,
             'target': 18.0},
        ],
    },
}

TANKS = {'tank1': {'name': 'Wasser', 'capacity_l': 200, 'color': '#1a5fb4'},
         'tank2': {'name': 'Diesel', 'capacity_l': 200, 'color': '#ff7800'}}


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class StreifenDurchschalten(Pruefstand):
    """Die Felder der Statusleiste schalten durch — wie das Laden-Feld schon.

    Der Wert kommt aus einer eingespielten Nutzlast, nicht vom Boot: geprüft
    wird das Umschalten, nicht die Messtechnik.
    """

    def setUp(self):
        super().setUp()
        # `_hzDaten` und `tanksConfig` sind `let` auf oberster Ebene des
        # Bündels — also lexikalische Bindungen und KEINE window-Eigenschaften.
        # `window._hzDaten = …` legt daneben eine zweite Variable an, die
        # niemand liest; die blanke Zuweisung trifft die richtige.
        self.pg.evaluate("""([d, hz, tk]) => {
            _hzDaten = hz;
            tanksConfig = tk;
            handleData(d);
        }""", [NUTZLAST, HEIZUNG, TANKS])
        self.pg.wait_for_timeout(200)

    def feld(self, *ids):
        return self.pg.evaluate(
            "(ids) => ids.map(i => document.getElementById(i)?.textContent)", list(ids))

    # ── Tanks ──────────────────────────────────────────────────────────────

    def test_tank_schaltet_weiter(self):
        self.assertEqual(self.feld('sbT1Lbl', 'sbT1'), ['Wasser', '61'])
        self.pg.evaluate('() => sbTankWeiter()')
        self.assertEqual(self.feld('sbT1Lbl', 'sbT1'), ['Diesel', '22'])
        self.pg.evaluate('() => sbTankWeiter()')
        self.assertEqual(self.feld('sbT1Lbl', 'sbT1'), ['Wasser', '61'])

    def test_tank_traegt_seine_farbe(self):
        """Die Farbe steht in den Presets und wird auf der Kachel schon
        benutzt; in der Leiste stand bisher immer derselbe Akzent."""
        farbe = lambda: self.pg.evaluate(          # noqa: E731
            "() => document.getElementById('sbT1Bar').style.color")
        self.assertEqual(farbe(), 'rgb(26, 95, 180)')     # #1a5fb4
        self.pg.evaluate('() => sbTankWeiter()')
        self.assertEqual(farbe(), 'rgb(255, 120, 0)')     # #ff7800

    def test_ein_einziger_tank_schaltet_nicht(self):
        self.pg.evaluate("""(d) => {
            tanksConfig = { tank1: { name: 'Wasser', capacity_l: 200 } };
            handleData(d);
        }""", NUTZLAST)
        self.pg.evaluate('() => sbTankWeiter()')
        self.assertEqual(self.feld('sbT1Lbl'), ['Wasser'])

    # ── Batterie ───────────────────────────────────────────────────────────

    def test_batterie_und_starter(self):
        self.assertEqual(self.feld('sbBattLbl', 'sbSoc', 'sbBattUnit'),
                         ['Service', '78', '%'])
        self.pg.evaluate('() => sbBattWeiter()')
        self.assertEqual(self.feld('sbBattLbl', 'sbSoc', 'sbBattUnit'),
                         ['Starter', '12.80', 'V'])

    def test_starter_zeigt_nur_die_spannung(self):
        """Kein Strom, kein Ladestand — der Starter hängt an einem einfachen
        Spannungseingang. Eine zweite Zeile hätte nichts zu sagen."""
        self.pg.evaluate('() => sbBattWeiter()')
        self.assertEqual(self.feld('sbBattSub')[0].strip(), '')

    def test_starter_wechselt_auch_die_skala(self):
        """0..100 % wäre für 12 Volt keine Skala, sondern ein Strich am Boden."""
        skala = lambda: self.pg.evaluate(          # noqa: E731
            "() => { const s = document.querySelector('#sbBattItem .sb-spark');"
            "        return [s.dataset.reihe, s.dataset.tief, s.dataset.hoch]; }")
        self.assertEqual(skala(), ['soc', '0', '100'])
        self.pg.evaluate('() => sbBattWeiter()')
        self.assertEqual(skala(), ['starter', '11.5', '14.5'])

    # ── Heizung ────────────────────────────────────────────────────────────

    def test_heizung_vorlauf_dann_raeume(self):
        schritte = []
        for _ in range(4):
            schritte.append(self.feld('sbHzLbl', 'sbHz', 'sbHzUnit'))
            self.pg.evaluate('() => sbHeizungWeiter()')
        self.assertEqual(schritte, [
            ['Heizung', '62', '%'],
            ['Vorlauf', '48', '°C'],
            ['Salon', '19.4', '°C'],
            ['Bugkabine', '--', '°C'],
        ])

    def test_stiller_raum_bleibt_in_der_liste(self):
        """Einen ausgefallenen Fühler dadurch zu verstecken, dass er
        ausgefallen ist, wäre die falsche Antwort."""
        for _ in range(3):
            self.pg.evaluate('() => sbHeizungWeiter()')
        self.assertEqual(self.feld('sbHzLbl', 'sbHz', 'sbHzSub'),
                         ['Bugkabine', '--', 'offline'])

    def test_raumliste_kommt_vom_hub(self):
        """Keine fest verdrahtete Raumliste: verschwindet ein Raum aus dem
        Schnappschuss, verschwindet er auch aus dem Feld."""
        self.pg.evaluate("""() => {
            _hzDaten.state.rooms = [_hzDaten.state.rooms[0]];
            _sbRenderHeizung();
        }""")
        namen = self.pg.evaluate("() => _sbHzSchritte().map(s => s.label)")
        self.assertEqual(namen, ['Heizung', 'Vorlauf', 'Salon'])

    def test_graph_folgt_der_auswahl(self):
        reihe = lambda: self.pg.evaluate(          # noqa: E731
            "() => document.querySelector('#sbHzItem .sb-spark').dataset.reihe")
        self.assertEqual(reihe(), 'heizleistung')
        self.pg.evaluate('() => sbHeizungWeiter()')
        self.assertEqual(reihe(), 'vorlauf')
        self.pg.evaluate('() => sbHeizungWeiter()')
        self.assertEqual(reihe(), 'raum1')



@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class DoppeltippOeffnetDetail(Pruefstand):
    """Ein schneller Doppeltipp führt zur Detailseite.

    Vorher war es langes Drücken — erst zwei Sekunden, dann eine, dann eine
    Drittel, und jedes Mal fühlte es sich falsch an: zu lang wirkte kaputt, zu
    kurz löste beim bloßen Weiterschalten aus.
    """

    def setUp(self):
        super().setUp()
        self.pg.evaluate('(d) => handleData(d)', NUTZLAST)
        self.pg.wait_for_timeout(200)
        k = self.pg.locator('#sbBattItem').bounding_box()
        self.x, self.y = k['x'] + k['width'] / 2, k['y'] + k['height'] / 2

    def offen(self):
        return self.pg.evaluate(
            "() => !document.getElementById('battOverlay').classList.contains('hidden')")

    def label(self):
        return self.pg.evaluate("() => document.getElementById('sbBattLbl').textContent")

    def test_einzelner_tipp_schaltet_nur_weiter(self):
        self.pg.mouse.click(self.x, self.y)
        self.pg.wait_for_timeout(600)          # die Doppeltipp-Frist abwarten
        self.assertEqual(self.label(), 'Starter')
        self.assertFalse(self.offen(), 'ein einzelner Tipp soll keine Seite öffnen')

    def test_doppeltipp_oeffnet(self):
        self.pg.mouse.click(self.x, self.y, click_count=2, delay=40)
        self.pg.wait_for_timeout(500)
        self.assertTrue(self.offen())

    def test_doppeltipp_schaltet_gar_nicht(self):
        """Nicht vor und wieder zurück: der erste Tipp wartet die Frist ab, und
        kommt der zweite, schaltet er gar nicht erst. Sonst sprang die Anzeige
        sichtbar hin und her."""
        vorher = self.label()
        zwischen = []
        self.pg.mouse.click(self.x, self.y)
        zwischen.append(self.label())          # sofort danach: noch unveraendert
        self.pg.mouse.click(self.x, self.y)
        self.pg.wait_for_timeout(700)
        self.assertEqual(zwischen[0], vorher, 'der erste Tipp hat schon geschaltet')
        self.assertEqual(self.label(), vorher)
        self.assertTrue(self.offen())

    def test_zu_langsam_ist_kein_doppeltipp(self):
        self.pg.mouse.click(self.x, self.y)
        self.pg.wait_for_timeout(700)
        self.pg.mouse.click(self.x, self.y)
        self.pg.wait_for_timeout(700)
        self.assertFalse(self.offen())
        self.assertEqual(self.label(), 'Service', 'zweimal getippt, zweimal weiter')

    def test_nur_felder_mit_seite(self):
        """Für die Tanks gibt es keine Detailseite — dort darf der Doppeltipp
        auch nichts vortäuschen."""
        self.assertIsNone(self.pg.evaluate(
            "() => document.getElementById('sbTankItem').dataset.detail ?? null"))

    def test_kein_nachleuchten_auf_der_detailseite(self):
        """Der Knopf behält sonst den Tastaturfokus, und das Feld leuchtet als
        heller Block hinter der geöffneten Seite weiter."""
        self.pg.mouse.click(self.x, self.y, click_count=2, delay=40)
        self.pg.wait_for_timeout(500)
        self.assertTrue(self.offen())
        self.assertFalse(self.pg.evaluate(
            "() => document.activeElement === document.getElementById('sbBattItem')"))

    def test_kein_halten_mehr(self):
        self.pg.mouse.move(self.x, self.y)
        self.pg.mouse.down()
        self.pg.wait_for_timeout(1200)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(300)
        self.assertFalse(self.offen(), 'langes Drücken soll nichts mehr öffnen')


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class Lichtkreise(Pruefstand):
    """Namen aus den Einstellungen, Balken als Regler, Relais auf Halten."""

    def setUp(self):
        super().setUp()
        self.pg.route('**/api/lights/channels',
                      lambda r: r.fulfill(status=200, body='{}'))
        self.pg.evaluate("""(d) => {
            lightsConfig = { '0': {name:'Kombüse'}, '1': {name:'Kartentisch'},
                             '8': {name:'Ankerlicht'} };
            chNamenAuffrischen();
            handleData(d);
        }""", NUTZLAST)
        self.pg.wait_for_timeout(300)

    def kasten(self, ch):
        k = self.pg.locator(f'.ch-bar-wrap[data-ch="{ch}"]').bounding_box()
        return k['x'] + k['width'] / 2, k['y'] + k['height'] / 2

    def test_namen_stehen_in_den_balken(self):
        """Vorher stand dort die Kanalnummer. "3" sagt niemandem, welches
        Licht das ist."""
        namen = self.pg.evaluate(
            "() => [...document.querySelectorAll('.ch-name')].map(e => e.textContent)")
        self.assertEqual(namen[:2], ['Kombüse', 'Kartentisch'])
        self.assertEqual(namen[-1], 'Ankerlicht')
        # Ohne Eintrag bleibt der Balken ein Balken.
        self.assertEqual(namen[2], '')

    def test_ohne_eintrag_bleibt_der_name_leer(self):
        """Vorgabenamen gab es einmal — "Küche", "Salon". Das waren Angaben über
        ein bestimmtes Boot, fest im Programm. Ohne Eingabe steht jetzt nichts
        da; wo eine Beschriftung sein MUSS, tritt die Kanalnummer ein."""
        self.assertEqual(self.pg.evaluate("() => chName(2)"), '')
        self.assertEqual(self.pg.evaluate("() => chBezeichnung(2)"), 'Kanal 3')
        # Das Relais heisst in dieser Vorlage "Ankerlicht" — ohne Eintrag
        # traegt es seine Rolle als Bezeichnung.
        self.assertEqual(self.pg.evaluate("() => chBezeichnung(8)"), 'Ankerlicht')
        self.assertEqual(self.pg.evaluate(
            "() => { const alt = lightsConfig['8']; delete lightsConfig['8'];"
            "        const b = chBezeichnung(8); lightsConfig['8'] = alt; return b; }"),
            'Relais')

    def test_passt_einer_nicht_gehen_alle_nach_unten(self):
        """Alle oder keiner: eine Reihe, in der zwei Namen innen und drei
        darunter stehen, sieht nach Fehler aus. Und unten steht der ganze Name
        (gekürzt vom Stylesheet), statt innen drei Buchstaben hochkant."""
        innen = lambda: self.pg.evaluate(          # noqa: E731
            "() => document.getElementById('channelsRow').classList.contains('namen-innen')")
        self.assertTrue(innen(), 'hier ist Platz, die Namen gehören in die Balken')

        self.pg.evaluate("""() => {
            lightsConfig['1'] = { name: 'Ein wirklich absurd langer Raumname '
                                        + 'der niemals in einen Balken passt' };
            chNamenPassend();
        }""")
        self.assertFalse(innen())
        self.assertEqual(self.pg.evaluate(
            "() => [...document.querySelectorAll('.ch-name')].map(e => e.textContent)"),
            [''] * 5, 'in den Balken darf dann nichts mehr stehen')
        unten = self.pg.evaluate(
            "() => [...document.querySelectorAll('.ch-name-unten')].map(e => e.textContent)")
        self.assertEqual(unten[0], 'Kombüse')
        self.assertTrue(unten[1].startswith('Ein wirklich absurd'))

    def test_relais_zeigt_beim_halten_die_richtung(self):
        """Der Balken faehrt dorthin, wo er nach dem Schalten steht — man sieht
        WAS gleich passiert, nicht nur DASS etwas passiert."""
        x, y = self.kasten(8)
        self.pg.evaluate("() => { _wideCh[8] = 0; updateChannels(_wideCh); }")
        self.pg.wait_for_timeout(200)
        self.pg.mouse.move(x, y)
        self.pg.mouse.down()
        self.pg.wait_for_timeout(120)
        hoehe = self.pg.evaluate("() => document.getElementById('chBar8').style.height")
        self.pg.mouse.up()
        self.pg.wait_for_timeout(300)
        self.assertEqual(hoehe, '100%', 'aus -> der Balken muss hochlaufen')

        # Zu frueh losgelassen heisst: kurzer Tipp, und der oeffnet die
        # Steuerseite. Sie liegt dann ueber der Kachel, und der naechste Griff
        # ginge ins Leere.
        self.pg.evaluate("() => closeLightDetail()")
        self.pg.wait_for_timeout(300)

        # Und andersherum.
        self.pg.evaluate("() => { _wideCh[8] = 1; updateChannels(_wideCh); }")
        self.pg.wait_for_timeout(200)
        self.pg.mouse.move(x, y)
        self.pg.mouse.down()
        self.pg.wait_for_timeout(120)
        hoehe = self.pg.evaluate("() => document.getElementById('chBar8').style.height")
        self.pg.mouse.up()
        self.pg.wait_for_timeout(300)
        self.pg.evaluate("() => closeLightDetail()")
        self.assertEqual(hoehe, '0%', 'an -> der Balken muss herunterlaufen')

    def test_zu_frueh_losgelassen_faellt_zurueck(self):
        """Der angefangene Lauf war eine Ankündigung, keine Schaltung."""
        x, y = self.kasten(8)
        self.pg.evaluate("() => { _wideCh[8] = 0; updateChannels(_wideCh); }")
        self.pg.wait_for_timeout(200)
        self.pg.mouse.move(x, y)
        self.pg.mouse.down()
        self.pg.wait_for_timeout(100)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(400)
        self.assertEqual(self.pg.evaluate("() => _wideCh[8]"), 0)
        self.assertEqual(
            self.pg.evaluate("() => document.getElementById('chBar8').style.height"), '0%')

    def test_ziehen_rechnet_die_bewegung(self):
        """Nicht die Position: wer unten auf den Balken tippt, will nicht, dass
        das Licht auf 5 % springt."""
        x, y = self.kasten(0)
        vorher = self.pg.evaluate("() => _wideCh[0]")
        self.pg.mouse.move(x, y)
        self.pg.mouse.down()
        for dy in range(0, 76, 15):
            self.pg.mouse.move(x, y - dy)
            self.pg.wait_for_timeout(30)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(300)
        nachher = self.pg.evaluate("() => _wideCh[0]")
        # 75 px von 150 px Vollausschlag sind rund die Hälfte von 255.
        self.assertAlmostEqual(nachher - vorher, 128, delta=12)

    def test_nach_dem_ziehen_keine_detailseite(self):
        x, y = self.kasten(0)
        self.pg.mouse.move(x, y)
        self.pg.mouse.down()
        for dy in range(0, 61, 20):
            self.pg.mouse.move(x, y - dy)
            self.pg.wait_for_timeout(30)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(400)
        self.assertTrue(self.pg.evaluate(
            "() => document.getElementById('lightOverlay').classList.contains('hidden')"))

    def test_kurzer_tipp_oeffnet_die_detailseite(self):
        x, y = self.kasten(0)
        self.pg.mouse.click(x, y)
        self.pg.wait_for_timeout(400)
        self.assertFalse(self.pg.evaluate(
            "() => document.getElementById('lightOverlay').classList.contains('hidden')"))

    def test_relais_erst_nach_dem_halten(self):
        """Ohne die Wartezeit schaltete es jedes Mal mit, wenn jemand die
        Detailseite öffnen wollte. Eine Sekunde — zwei waren zu lang."""
        x, y = self.kasten(8)
        vorher = self.pg.evaluate("() => _wideCh[8]")
        self.pg.mouse.click(x, y)
        self.pg.wait_for_timeout(400)
        self.assertEqual(self.pg.evaluate("() => _wideCh[8]"), vorher,
                         'ein Tipp hat das Relais geschaltet')
        self.pg.evaluate("() => closeLightDetail()")
        self.pg.wait_for_timeout(200)

        self.pg.mouse.move(x, y)
        self.pg.mouse.down()
        self.pg.wait_for_timeout(700)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(400)
        self.assertNotEqual(self.pg.evaluate("() => _wideCh[8]"), vorher)
        self.assertTrue(self.pg.evaluate(
            "() => document.getElementById('lightOverlay').classList.contains('hidden')"),
            'nach dem Halten soll nicht auch noch die Seite aufgehen')


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class ZeigerartStattMedienabfrage(Pruefstand):
    """Das angetippte Feld der Statusleiste darf nicht hell stehen bleiben.

    Der Eigner hat es zweimal gemeldet: nach dem Doppeltipp öffnet sich die
    Detailseite, und das Feld darunter leuchtet weiter. Der Grund war nicht der
    Tastaturfokus (der wird längst weggenommen), sondern `:hover` — auf
    Berührgeräten bleibt der am zuletzt angetippten Element haften, bis man
    woanders hintippt.

    `@media (hover: hover)` fängt das NICHT ab: iPadOS meldet dort `hover:
    hover`, obwohl kein Zeiger existiert, und Tablets mit Stift oder
    angesteckter Tastatur ebenso. Genau diese Lage hat dieser Prüfstand von
    selbst — ein Chromium am Schreibtisch meldet `hover: hover` und `pointer:
    fine`, und die Berührung kommt hier als Ereignis mit `pointerType:
    'touch'` herein. Anders gesagt: die Medienabfrage sagt Maus, das Gerät
    sagt Finger. Wer der Abfrage glaubt, fällt durch.
    """

    def setUp(self):
        super().setUp()
        self.pg.evaluate('(d) => handleData(d)', NUTZLAST)
        self.pg.wait_for_timeout(200)
        self.cdp = self.pg.context.new_cdp_session(self.pg)
        self.cdp.send('DOM.enable')
        self.cdp.send('CSS.enable')

    # ── Werkzeug ───────────────────────────────────────────────────────────

    def hover_erzwingen(self, sel: str) -> None:
        """`:hover` setzen, ohne einen Zeiger zu bewegen.

        Mit der Maus hinzufahren würde die Messung zerstören: das Programm
        merkt sich daran ja gerade, dass ein Zeiger da ist.
        """
        wurzel = self.cdp.send('DOM.getDocument')['root']['nodeId']
        knoten = self.cdp.send('DOM.querySelector',
                               {'nodeId': wurzel, 'selector': sel})['nodeId']
        self.cdp.send('CSS.forcePseudoState',
                      {'nodeId': knoten, 'forcedPseudoClasses': ['hover']})

    def grund(self, sel: str = '#sbBattItem') -> str:
        return self.pg.evaluate(
            "(s) => getComputedStyle(document.querySelector(s)).backgroundColor", sel)

    def zeigerart(self) -> str:
        return self.pg.evaluate("() => document.documentElement.dataset.zeiger")

    def beruehren(self, sel: str = '#sbBattItem') -> None:
        """Ein Fingertipp, wie ihn das Tablet meldet."""
        self.pg.evaluate("""(s) => {
            const z = document.querySelector(s), r = z.getBoundingClientRect();
            const lage = { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
                           bubbles: true, cancelable: true,
                           pointerId: 7, pointerType: 'touch', isPrimary: true };
            for (const art of ['pointerdown', 'pointerup'])
                z.dispatchEvent(new PointerEvent(art, lage));
            z.click();
        }""", sel)
        self.pg.wait_for_timeout(120)

    # ── Prüfungen ──────────────────────────────────────────────────────────

    def test_die_medienabfrage_sagt_hier_maus(self):
        """Ohne diese Voraussetzung prüft der Rest der Klasse nichts."""
        self.assertTrue(self.pg.evaluate(
            "() => matchMedia('(hover: hover) and (pointer: fine)').matches"),
            'der Prüfstand stellt die Lage nicht mehr nach')

    def test_nach_beruehrung_faerbt_hover_nicht_mehr(self):
        ruhe = self.grund()
        self.beruehren()
        self.assertEqual(self.zeigerart(), 'grob')
        self.hover_erzwingen('#sbBattItem')
        self.pg.wait_for_timeout(300)          # die Überblendung abwarten
        self.assertEqual(self.grund(), ruhe,
                         'das angetippte Feld leuchtet nach')

    def test_mit_maus_faerbt_hover_weiterhin(self):
        """Am Schreibtisch ist die Hervorhebung erwünscht — sie muss bleiben."""
        ruhe = self.grund()
        self.beruehren()                       # erst auf 'grob' bringen
        self.pg.mouse.move(600, 400)
        self.pg.wait_for_timeout(120)
        self.assertEqual(self.zeigerart(), 'fein')
        self.hover_erzwingen('#sbBattItem')
        self.pg.wait_for_timeout(300)
        self.assertNotEqual(self.grund(), ruhe,
                            'mit der Maus soll das Feld unter dem Zeiger heller sein')

    def test_beruehrung_bekommt_keine_eigene_hervorhebung(self):
        """Der Browser malt sonst zusätzlich seine eigene über das Feld."""
        self.assertEqual(
            self.pg.evaluate("""() => getComputedStyle(document.getElementById('sbBattItem'))
                                        .getPropertyValue('-webkit-tap-highlight-color')"""),
            'rgba(0, 0, 0, 0)')


# ── Wetter ─────────────────────────────────────────────────────────────────
# Die Vorhersage kommt hier NICHT vom Prüfserver: der reicht /api/weather ans
# Boot durch, und ein Boot gibt es hier nicht. Stattdessen wird die Antwort im
# Browser abgefangen. Das ist obendrein das Richtige — ein Test, der Open-Meteo
# braucht, prüft irgendwann das Wetter statt den Quelltext.

def _wx_vorlage():
    """Drei Tage Vorhersage rund um JETZT, damit "die aktuelle Stunde" trägt."""
    jetzt = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
    start = jetzt - datetime.timedelta(hours=6)
    stunden, tage = [], []
    for i in range(72):
        t = start + datetime.timedelta(hours=i)
        stunden.append({
            't': t.strftime('%Y-%m-%dT%H:00'),
            'temp': 15.0 + (i % 8), 'wmo': 3, 'regen': 0.2 if i % 9 == 0 else 0.0,
            # Ein wiedererkennbarer Wert genau in der aktuellen Stunde: daran
            # laesst sich pruefen, dass der Kopf die richtige Stunde nimmt.
            'wind': 33.0 if i == 6 else 10.0 + (i % 5),
            'boe': 44.0 if i == 6 else 18.0 + (i % 5),
            'dir': (270 + i) % 360, 'druck': 1010 + i * 0.2,
            'welle': 0.4 + (i % 3) / 10, 'welle_dir': 260, 'welle_periode': 3.2,
        })
    for k in range(5):
        d = (start + datetime.timedelta(days=k)).strftime('%Y-%m-%d')
        tage.append({
            'date': d, 'wmo': 95 if k == 1 else 3, 'tmax': 18.0 + k, 'tmin': 12.0 + k,
            'precip': 1.0, 'pop': 40, 'wind': 15.0 + k, 'gust': 28.0 + k,
            'dir': 270, 'wave': 0.6 if k < 3 else None,
            'wave_dir': 261, 'wave_periode': 3.1,
            'auf': f'{d}T06:32', 'unter': f'{d}T19:58',
            'storm': k == 1,
        })
    return {'updated': 0, 'source': 'open-meteo', 'modell': 'auto',
            'modell_name': 'Automatisch', 'ort': {'lat': 54.0, 'lon': 11.0},
            'tage': tage, 'stunden': stunden}


WX_ORTE = {'orte': [{'name': 'Travemünde', 'lat': 53.9585, 'lon': 10.8752},
                    {'name': 'Fehmarnsund', 'lat': 54.4139, 'lon': 11.1058}],
           'modell': 'auto',
           'modelle': {'auto': 'Automatisch', 'icon': 'ICON (DWD)', 'ecmwf': 'ECMWF'}}

WX_VERGLEICH = {
    'zeiten': _wx_vorlage()['stunden'][:24] and [s['t'] for s in _wx_vorlage()['stunden'][:24]],
    'modelle': [{'kennung': 'icon', 'name': 'ICON (DWD)',
                 'wind': [10 + (i % 7) for i in range(24)], 'boe': []},
                {'kennung': 'ecmwf', 'name': 'ECMWF',
                 'wind': [18 + (i % 5) for i in range(24)], 'boe': []}],
    'ort': {'lat': 54.0, 'lon': 11.0}, 'updated': 0,
}


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class Wetterkachel(Pruefstand):
    """Ort durchschalten, Detailseite öffnen, und was dabei NICHT passieren darf."""

    MIT_POSITION = True

    def setUp(self):
        self.pg = self._browser.new_page(viewport={'width': 1280, 'height': 900})
        self.fehler = []
        self.pg.on('pageerror', lambda e: self.fehler.append(str(e)[:250]))
        self.wx_abrufe = []
        self._wx_routen()
        self.pg.goto(self.basis + '/', wait_until='domcontentloaded', timeout=30000)
        self.pg.wait_for_timeout(2000)
        if self.pg.evaluate("() => !!document.querySelector('.anmeldung:not(.hidden)')"):
            self.pg.fill('#anmName', KONTO)
            self.pg.fill('#anmPw', PASSWORT)
            self.pg.click('#anmKnopf')
            self.pg.wait_for_timeout(4000)
        nutzlast = {**NUTZLAST}
        if self.MIT_POSITION:
            nutzlast['position'] = {'lat': 53.8961, 'lon': 10.7695}
        self.pg.evaluate('(d) => handleData(d)', nutzlast)
        self.pg.wait_for_timeout(400)

    def tearDown(self):
        self.assertEqual(self.fehler, [], 'Die Seite hat Fehler geworfen')
        self.pg.close()

    def _wx_routen(self):
        def json_antwort(route, daten):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps(daten))
        self.pg.route('**/api/wetter/orte', lambda r: json_antwort(r, WX_ORTE))
        self.pg.route('**/api/wetter/vergleich*', lambda r: json_antwort(r, WX_VERGLEICH))

        def wetter(route):
            self.wx_abrufe.append(route.request.url)
            json_antwort(route, _wx_vorlage())
        self.pg.route('**/api/weather*', wetter)

    # ── Werkzeug ───────────────────────────────────────────────────────────

    def ort(self):
        return self.pg.evaluate("() => document.getElementById('wxOrtName').textContent")

    def offen(self):
        return self.pg.evaluate(
            "() => !document.getElementById('wxOverlay').classList.contains('hidden')")

    # ── Die Kachel ─────────────────────────────────────────────────────────

    def test_erster_favorit_steht_in_der_kachel(self):
        self.assertEqual(self.ort(), 'Travemünde')

    def test_tippen_schaltet_durch_und_die_position_haengt_hinten_an(self):
        """"An Bord" steht nicht in den Einstellungen — es ist ein Messwert und
        taucht auf, sobald der Router einen Fix hat."""
        gesehen = [self.ort()]
        for _ in range(3):
            self.pg.click('#wxOrtKnopf')
            self.pg.wait_for_timeout(300)
            gesehen.append(self.ort())
        self.assertEqual(gesehen, ['Travemünde', 'Fehmarnsund', 'An Bord', 'Travemünde'])

    def test_tippen_auf_den_namen_oeffnet_die_seite_nicht(self):
        """Sonst wäre Durchschalten unmöglich: jeder Tipp landete auf der
        Detailseite."""
        self.pg.click('#wxOrtKnopf')
        self.pg.wait_for_timeout(400)
        self.assertFalse(self.offen())


    def wischen(self, sel, dx):
        """Mit dem Finger über die Kachel ziehen."""
        self.pg.locator(sel).scroll_into_view_if_needed()
        self.pg.wait_for_timeout(250)
        k = self.pg.locator(sel).bounding_box()
        y = k['y'] + k['height'] * 0.5
        x0 = k['x'] + k['width'] * (0.78 if dx < 0 else 0.22)
        self.pg.mouse.move(x0, y)
        self.pg.mouse.down()
        self.pg.mouse.move(x0 + dx, y + 3, steps=12)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(700)

    def test_wischen_schaltet_den_ort_weiter(self):
        """Wenn der Finger ohnehin auf der Kachel liegt, ist Wischen der
        kürzere Weg als der Namensknopf in der Ecke."""
        self.assertEqual(self.ort(), 'Travemünde')
        self.wischen('#wxCard', -200)
        self.assertEqual(self.ort(), 'Fehmarnsund')
        self.wischen('#wxCard', 200)
        self.assertEqual(self.ort(), 'Travemünde')

    def test_wischen_oeffnet_die_seite_nicht(self):
        """Sonst wäre Wischen unbrauchbar: jeder Zug endete auf der
        Detailseite. Der Klick nach dem Wisch wird geschluckt."""
        self.wischen('#wxCard', -200)
        self.assertFalse(self.offen())

    def test_tippen_auf_die_kachel_oeffnet_die_seite(self):
        self.pg.click('#wxHeute')
        self.pg.wait_for_timeout(600)
        self.assertTrue(self.offen())
        self.assertEqual(self.pg.evaluate('() => location.hash'), '#wetter')

    def test_gewaehlter_ort_ueberlebt_das_neuladen(self):
        """Wer auf dem Zielhafen steht, will nach jedem Neuladen nicht wieder
        beim Liegeplatz anfangen."""
        self.pg.click('#wxOrtKnopf')
        self.pg.wait_for_timeout(400)
        self.pg.reload(wait_until='domcontentloaded')
        self.pg.wait_for_timeout(4000)
        self.assertEqual(self.ort(), 'Fehmarnsund')

    def test_der_ort_geht_als_koordinate_mit(self):
        self.wx_abrufe.clear()
        self.pg.click('#wxOrtKnopf')
        self.pg.wait_for_timeout(700)
        self.assertTrue(any('lat=54.4139' in u for u in self.wx_abrufe),
                        f'abgerufen wurde: {self.wx_abrufe}')

    def test_heute_steht_oben_und_ausfuehrlich(self):
        """Fünf gleich große Tagesspalten beantworten „wie wird die Woche" —
        aber nicht die Frage beim Kaffee: was ist HEUTE los."""
        text = self.pg.evaluate("() => document.getElementById('wxHeute').innerText")
        for teil in ('kn', 'Bft', 'Böen', 'hPa'):
            self.assertIn(teil, text, f'{teil} fehlt: {text}')

    def test_der_wind_kommt_aus_der_aktuellen_stunde(self):
        """„Heute bis 22 kn" hilft um neun Uhr morgens nicht weiter. In der
        Prüfvorlage steht in der laufenden Stunde 33 kn."""
        self.assertIn('33', self.pg.evaluate(
            "() => document.getElementById('wxHeute').innerText"))

    def test_die_kachel_zeigt_nur_heute(self):
        """Vier Tagesspalten daneben machten beides klein und keins lesbar
        (Eignermeldung mit Foto). Die weitere Vorschau steht auf der
        Detailseite."""
        self.assertEqual(self.pg.locator('.card-wx .wx-day').count(), 0)
        text = self.pg.evaluate("() => document.querySelector('.wx-koerper').innerText")
        for tag in ('Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'):
            self.assertNotIn(f'\n{tag}\n', text, f'{tag} steht noch auf der Kachel')

    def test_der_tagesstreifen_zeichnet(self):
        """Er beantwortet, was eine Tageszahl nie beantwortet: frischt es auf
        oder schläft es ein. Ein Canvas der Breite 0 malt still nichts."""
        n = self.pg.evaluate("""() => {
            const c = document.getElementById('wxTagCanvas');
            if (!c || !c.width) return 0;
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let z = 0;
            for (let i = 3; i < d.length; i += 4) if (d[i]) z++;
            return z;
        }""")
        self.assertGreater(n, 300, 'der Streifen ist leer geblieben')

    def test_die_werte_stehen_mit_ihrer_bezeichnung_da(self):
        """Eine Zahl ohne Wort daneben muss man raten."""
        text = self.pg.evaluate("() => document.getElementById('wxHeute').innerText")
        for was in ('Regen', 'Druck', 'Sonne'):
            self.assertIn(was, text)

    # ── Die Detailseite ────────────────────────────────────────────────────

    def test_der_kopf_zeigt_die_aktuelle_stunde(self):
        """Nicht die erste der Liste — die ist Mitternacht."""
        self.pg.evaluate('() => openWetter()')
        self.pg.wait_for_timeout(600)
        text = self.pg.evaluate("() => document.getElementById('wxJetzt').innerText")
        self.assertIn('33', text, f'gezeigt wurde: {text}')
        self.assertIn('44', text)

    def test_boeenfaktor_steht_dabei(self):
        """44 zu 33 ist Faktor 1,3 — gleichmäßiger Wind. Die Zahl ist die
        eigentliche Auskunft, nicht die Böe allein."""
        self.pg.evaluate('() => openWetter()')
        self.pg.wait_for_timeout(600)
        self.assertIn('Faktor 1.3',
                      self.pg.evaluate("() => document.getElementById('wxJetzt').innerText"))

    def test_beaufort_und_himmelsrichtung(self):
        werte = self.pg.evaluate("""() => ({
            b0: wxBft(0), b3: wxBft(10), b4: wxBft(11), b7: wxBft(33), b8: wxBft(34),
            n: wxStrich(0), o: wxStrich(90), sw: wxStrich(225), w: wxStrich(272),
            rund: wxStrich(359), leer: wxStrich(null),
        })""")
        self.assertEqual([werte['b0'], werte['b3'], werte['b4'], werte['b7'], werte['b8']],
                         [0, 3, 4, 7, 8])
        self.assertEqual([werte['n'], werte['o'], werte['sw'], werte['w'], werte['rund']],
                         ['N', 'O', 'SW', 'W', 'N'])
        self.assertEqual(werte['leer'], '')

    def test_die_diagramme_zeichnen_wirklich(self):
        """Ein Canvas mit Breite 0 malt still nichts. Genau das passiert, wenn
        gezeichnet wird, bevor das Overlay sichtbar ist."""
        self.pg.evaluate('() => openWetter()')
        self.pg.wait_for_timeout(900)
        gemalt = self.pg.evaluate("""() => ['wxWindCanvas', 'wxWelleCanvas', 'wxRegenCanvas']
            .map(id => {
                const c = document.getElementById(id);
                if (!c || !c.width) return [id, 0];
                const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                let n = 0;
                for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
                return [id, n];
            })""")
        for name, pixel in gemalt:
            self.assertGreater(pixel, 500, f'{name} ist leer geblieben')

    def test_modellvergleich_faellt_ein_urteil(self):
        """Zwei Modelle, die 8 Knoten auseinanderliegen, sind keine Planung."""
        self.pg.evaluate('() => openWetter()')
        self.pg.wait_for_timeout(500)
        self.pg.evaluate('() => wxVergleichLaden()')
        self.pg.wait_for_timeout(900)
        urteil = self.pg.evaluate("() => document.getElementById('wxVergleichUrteil').textContent")
        self.assertIn('Spanne', urteil)
        self.assertIn('uneins', urteil.lower() + ' ' if 'uneins' in urteil else urteil)


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class WetterOhnePosition(Wetterkachel):
    """Ohne Fix vom Router gibt es "An Bord" nicht — und zwar gar nicht."""

    MIT_POSITION = False

    # Die Prüfungen der Elternklasse laufen hier bewusst NICHT noch einmal:
    # sie setzen die Position voraus. Nur diese eine gilt.
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

    def test_erster_favorit_steht_in_der_kachel(self): pass
    def test_tippen_auf_den_namen_oeffnet_die_seite_nicht(self): pass
    def test_wischen_schaltet_den_ort_weiter(self): pass
    def test_wischen_oeffnet_die_seite_nicht(self): pass
    def test_tippen_auf_die_kachel_oeffnet_die_seite(self): pass
    def test_gewaehlter_ort_ueberlebt_das_neuladen(self): pass
    def test_der_ort_geht_als_koordinate_mit(self): pass
    def test_heute_steht_oben_und_ausfuehrlich(self): pass
    def test_der_wind_kommt_aus_der_aktuellen_stunde(self): pass
    def test_die_kachel_zeigt_nur_heute(self): pass
    def test_der_tagesstreifen_zeichnet(self): pass
    def test_letzte_zeile_ist_ueberall_dieselbe_groesse(self): pass
    def test_der_kopf_zeigt_die_aktuelle_stunde(self): pass
    def test_boeenfaktor_steht_dabei(self): pass
    def test_beaufort_und_himmelsrichtung(self): pass
    def test_die_diagramme_zeichnen_wirklich(self): pass
    def test_modellvergleich_faellt_ein_urteil(self): pass

    def test_tippen_schaltet_durch_und_die_position_haengt_hinten_an(self):
        gesehen = [self.ort()]
        for _ in range(2):
            self.pg.click('#wxOrtKnopf')
            self.pg.wait_for_timeout(300)
            gesehen.append(self.ort())
        self.assertEqual(gesehen, ['Travemünde', 'Fehmarnsund', 'Travemünde'])


# ── Wasserstand ────────────────────────────────────────────────────────────

WL_TRAVE = 'c7383149-1f77-430d-8bef-c5667be3846b'
WL_WARNE = '220ff4c6-83da-4a1b-9c13-dfee5a2a8798'
WL_KIEL  = '3ad4013f-644b-47f5-a641-44b332bfecb2'

WL_ORTE = {'stationen': [{'name': 'Travemünde', 'uuid': WL_TRAVE},
                         {'name': 'Warnemünde', 'uuid': WL_WARNE},
                         {'name': 'Kiel-Holtenau', 'uuid': WL_KIEL}],
           'gepflegt': True}


def _wl_stand(uuid):
    """Drei Pegel mit klar verschiedenen Zahlen — sonst faellt nicht auf, wenn
    beim Umschalten der alte Wert stehen bleibt."""
    jetzt = datetime.datetime.now(datetime.timezone.utc)
    tabelle = {WL_TRAVE: (11, 514.0, -5.025, 'Travemünde'),
               WL_WARNE: (28, 526.0, -4.979, 'Warnemünde'),
               WL_KIEL:  (9,  509.0, -4.997, 'Kiel-Holtenau')}
    nhn, cm, pnp, name = tabelle[uuid]
    reihe = [{'ts': (jetzt - datetime.timedelta(minutes=(60 - i) * 10)).isoformat(),
              'v': cm + (i % 7) - 3} for i in range(60)]
    d = {'current_cm': cm, 'current_nhn_cm': nhn, 'trend': 'stable', 'delta_cm': 1.0,
         'measurements': reihe,
         'station': {'name': name, 'uuid': uuid, 'pnp_m': pnp}}
    # Nur Travemuende hat eine BSH-Kurve — wie in Wirklichkeit.
    if uuid == WL_TRAVE:
        d['forecast_img'] = 'https://example.invalid/WVD_Travemuende.png'
        d['forecast_min_nhn_cm'] = -20
        d['forecast_alarm'] = False
    return d


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class Pegelkachel(Pruefstand):
    """Pegel durchschalten — und was dabei NICHT stehen bleiben darf."""

    ORTE = WL_ORTE
    BREMSE_MS = 0          # kuenstliche Verzoegerung des Abrufs

    def setUp(self):
        self.pg = self._browser.new_page(viewport={'width': 1280, 'height': 900})
        self.fehler = []
        self.pg.on('pageerror', lambda e: self.fehler.append(str(e)[:250]))
        self.wl_abrufe = []
        self._wl_routen()
        self.pg.goto(self.basis + '/', wait_until='domcontentloaded', timeout=30000)
        self.pg.wait_for_timeout(2000)
        if self.pg.evaluate("() => !!document.querySelector('.anmeldung:not(.hidden)')"):
            self.pg.fill('#anmName', KONTO)
            self.pg.fill('#anmPw', PASSWORT)
            self.pg.click('#anmKnopf')
            self.pg.wait_for_timeout(4000)
        self.pg.evaluate('(d) => handleData(d)', NUTZLAST)
        self.pg.wait_for_timeout(600)

    def tearDown(self):
        self.assertEqual(self.fehler, [], 'Die Seite hat Fehler geworfen')
        self.pg.close()

    def _wl_routen(self):
        def orte(route):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps(self.ORTE))

        def stand(route):
            self.wl_abrufe.append(route.request.url)
            uuid = route.request.url.split('station=')[-1] if 'station=' in route.request.url \
                else self.ORTE['stationen'][0]['uuid']
            if self.BREMSE_MS:
                time.sleep(self.BREMSE_MS / 1000)
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps(_wl_stand(uuid)))

        # Das Vorhersagebild darf nicht wirklich geladen werden.
        self.pg.route('**/WVD_*.png', lambda r: r.fulfill(
            status=200, content_type='image/png', body=b''))
        self.pg.route('**/api/pegel/orte', orte)
        self.pg.route('**/api/waterlevel*', stand)

    # ── Werkzeug ───────────────────────────────────────────────────────────

    def name(self):
        return self.pg.evaluate("() => document.getElementById('wlStationName').textContent")

    def wert(self):
        return self.pg.evaluate("() => document.getElementById('wlTileVal').textContent")

    def weiter(self):
        self.pg.click('#wlStationKnopf')
        self.pg.wait_for_timeout(500)

    # ── Prüfungen ──────────────────────────────────────────────────────────

    def test_erster_pegel_steht_in_der_kachel(self):
        self.assertEqual(self.name(), 'Travemünde')
        self.assertEqual(self.wert(), '+11')

    def test_tippen_schaltet_durch(self):
        gesehen = [(self.name(), self.wert())]
        for _ in range(3):
            self.weiter()
            gesehen.append((self.name(), self.wert()))
        self.assertEqual(gesehen, [('Travemünde', '+11'), ('Warnemünde', '+28'),
                                   ('Kiel-Holtenau', '+9'), ('Travemünde', '+11')])

    def test_tippen_auf_den_namen_oeffnet_die_seite_nicht(self):
        self.weiter()
        self.assertTrue(self.pg.evaluate(
            "() => document.getElementById('wlOverlay').classList.contains('hidden')"))


    def wischen(self, sel, dx):
        """Mit dem Finger über die Kachel ziehen."""
        self.pg.locator(sel).scroll_into_view_if_needed()
        self.pg.wait_for_timeout(250)
        k = self.pg.locator(sel).bounding_box()
        y = k['y'] + k['height'] * 0.5
        x0 = k['x'] + k['width'] * (0.78 if dx < 0 else 0.22)
        self.pg.mouse.move(x0, y)
        self.pg.mouse.down()
        self.pg.mouse.move(x0 + dx, y + 3, steps=12)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(700)

    def test_wischen_schaltet_den_pegel_weiter(self):
        self.assertEqual(self.name(), 'Travemünde')
        self.wischen('#wlCard', -200)
        self.assertEqual(self.name(), 'Warnemünde')
        self.wischen('#wlCard', 200)
        self.assertEqual(self.name(), 'Travemünde')

    def test_wischen_oeffnet_die_seite_nicht(self):
        self.wischen('#wlCard', -200)
        self.assertTrue(self.pg.evaluate(
            "() => document.getElementById('wlOverlay').classList.contains('hidden')"))

    def test_tippen_auf_die_kachel_oeffnet_die_seite(self):
        self.pg.click('#wlTileSpark')
        self.pg.wait_for_timeout(600)
        self.assertFalse(self.pg.evaluate(
            "() => document.getElementById('wlOverlay').classList.contains('hidden')"))
        self.assertEqual(self.pg.evaluate('() => location.hash'), '#waterlevel')

    def test_der_pegel_geht_als_kennung_mit(self):
        self.wl_abrufe.clear()
        self.weiter()
        self.assertTrue(any(WL_WARNE in u for u in self.wl_abrufe),
                        f'abgerufen wurde: {self.wl_abrufe}')

    def test_gewaehlter_pegel_ueberlebt_das_neuladen(self):
        self.weiter()
        self.pg.reload(wait_until='domcontentloaded')
        self.pg.wait_for_timeout(4000)
        self.assertEqual(self.name(), 'Warnemünde')

    def test_die_seite_traegt_den_namen_und_den_nullpunkt(self):
        self.pg.evaluate('() => openWaterLevel()')
        self.pg.wait_for_timeout(600)
        self.assertEqual(self.pg.evaluate("() => document.getElementById('wlDetailTitel').textContent"),
                         'Wasserstand Travemünde')
        self.assertIn('-5.025',
                      self.pg.evaluate("() => document.getElementById('wlDetailQuelle').textContent"))

    def test_vorhersageblock_nur_wo_es_eine_kurve_gibt(self):
        """Fuer die allermeisten Pegel gibt es keine — dann faellt der ganze
        Block weg statt einen leeren Rahmen zu zeigen."""
        self.pg.evaluate('() => openWaterLevel()')
        self.pg.wait_for_timeout(600)
        sichtbar = lambda: self.pg.evaluate(
            "() => !document.getElementById('wlPrognoseBlock').hidden")
        self.assertTrue(sichtbar(), 'Travemünde hat eine Kurve')
        # Auf der offenen Seite wird ueber die Pegelreihe umgeschaltet — die
        # Kachel liegt darunter und nimmt keine Klicks an.
        self.pg.click('#wlStationLeiste .wx-chip:nth-child(2)')
        self.pg.wait_for_timeout(700)
        self.assertFalse(sichtbar(), 'Warnemünde hat hier keine')

    def test_beim_umschalten_wird_der_wert_geleert(self):
        """Bis die Antwort da ist, darf NICHT der alte Wert unter dem neuen
        Namen stehen.

        pegelonline braucht beim ersten Abruf eines Pegels mehrere Sekunden. So
        lange sah die Kachel fertig aus und zeigte den falschen Pegel — der
        gefährlichste Zustand, den eine Anzeige haben kann.

        Gemessen wird SYNCHRON, im selben Schritt wie der Aufruf: danach ist
        die Antwort womöglich schon da, und dann wäre nichts mehr zu sehen.
        """
        self.assertEqual(self.wert(), '+11')
        sofort = self.pg.evaluate("""() => {
            _wlIndex = 1; _wlNamenSetzen(); fetchWaterLevel();
            return document.getElementById('wlTileVal').textContent;
        }""")
        self.assertEqual(sofort, '--', 'der alte Wert steht unter dem neuen Namen')
        self.pg.wait_for_timeout(700)
        self.assertEqual(self.wert(), '+28')

    def test_zurueck_auf_einen_bekannten_pegel_zeigt_sofort(self):
        """Was schon geholt wurde, liegt im Browser — sonst wäre jeder Rückweg
        wieder ein Wartezeichen."""
        self.weiter()                          # Warnemünde holen
        self.pg.wait_for_timeout(500)
        self.weiter()                          # Kiel
        self.pg.wait_for_timeout(500)
        self.weiter()                          # zurück auf Travemünde
        sofort = self.pg.evaluate(
            "() => document.getElementById('wlTileVal').textContent")
        self.assertEqual(sofort, '+11')

    def test_ein_einziger_pegel_zeigt_keinen_pfeil(self):
        """Ein Pfeil, der nirgendwo hinfuehrt, ist eine Luege."""
        self.pg.evaluate("""() => {
            _wlStationen = [{ name: 'Nur einer', uuid: 'x' }];
            _wlIndex = 0; _wlNamenSetzen();
        }""")
        self.assertEqual(self.pg.evaluate(
            "() => document.querySelector('#wlStationKnopf svg').style.display"), 'none')
        self.assertTrue(self.pg.evaluate(
            "() => document.getElementById('wlStationLeiste').hidden"))


# ── Grundriss-Werkzeug ─────────────────────────────────────────────────────

def _test_png(w: int, h: int) -> bytes:
    """Ein echtes PNG, von Hand gebaut — Pillow ist hier nicht installiert."""
    import struct
    import zlib

    def block(typ, daten):
        return (struct.pack('>I', len(daten)) + typ + daten
                + struct.pack('>I', zlib.crc32(typ + daten) & 0xffffffff))

    roh = b''
    for y in range(h):
        zeile = bytes([(x * 7 + y * 3) % 256 if (x // 8 + y // 8) % 2 else 240
                       for x in range(w) for _ in range(3)])
        roh += b'\x00' + zeile
    return (b'\x89PNG\r\n\x1a\n'
            + block(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + block(b'IDAT', zlib.compress(roh))
            + block(b'IEND', b''))


@unittest.skipIf(sync_playwright is None, 'Playwright nicht installiert')
class Grundrisswerkzeug(Pruefstand):
    """Räume zeichnen, verschieben, löschen — und was dabei gespeichert wird.

    Das Werkzeug lebt im LOGBUCH auf dem Server, nicht am Boot: Zeichnen ist
    Planung, dafür sitzt man in Ruhe davor und hat den Bootsplan daneben. Der
    Pi bekommt davon nur das Ergebnis, als Datei.
    """

    def setUp(self):
        self.pg = self._browser.new_page(viewport={'width': 1400, 'height': 950})
        self.fehler = []
        self.pg.on('pageerror', lambda e: self.fehler.append(str(e)[:250]))
        self.pg.on('dialog', lambda d: d.accept())
        self.gespeichert = []
        self.ans_boot = []
        self.vorlage = None
        self._routen()
        self.pg.goto(self.basis + '/diagnose', wait_until='domcontentloaded', timeout=30000)
        self.pg.wait_for_timeout(1500)
        if self.pg.locator('#anmName').count():
            self.pg.fill('#anmName', KONTO)
            self.pg.fill('#anmPw', PASSWORT)
            self.pg.click('#anmKnopf')
            self.pg.wait_for_timeout(3000)
        self.pg.click('.sl-knopf[data-seite="grundriss"]')
        self.pg.wait_for_timeout(1200)
        self.kasten = self.pg.locator('#geSvg').bounding_box()

    def tearDown(self):
        self.assertEqual(self.fehler, [], 'Die Seite hat Fehler geworfen')
        self.pg.close()

    def _routen(self):
        def riss(route):
            if route.request.method == 'PUT':
                rumpf = json.loads(route.request.post_data or '{}')
                self.gespeichert.append(rumpf)
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps({**rumpf, 'hat_vorlage': bool(self.vorlage)}))
            else:
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps({'hat_vorlage': bool(self.vorlage)}))
        self.pg.route('**/api/logbuch/grundriss', riss)

        def vorlage(route):
            if route.request.method == 'PUT':
                self.vorlage = route.request.post_data_buffer
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps({'ok': True, 'bytes': len(self.vorlage or b'')}))
            elif route.request.method == 'DELETE':
                self.vorlage = None
                route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
            elif self.vorlage:
                route.fulfill(status=200, content_type='image/jpeg', body=self.vorlage)
            else:
                route.fulfill(status=404, body='')
        self.pg.route('**/api/logbuch/grundriss/vorlage*', vorlage)

        # Der Weg ans Boot geht über den Durchleiter des Servers.
        def boot(route):
            self.ans_boot.append(json.loads(route.request.post_data or '{}'))
            route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
        self.pg.route('**/api/grundriss', lambda r:
                      boot(r) if r.request.method == 'PUT' else r.fallback())
        # Der Stauplan liegt am Boot; ohne Boot antwortet der Server mit 409.
        self.pg.route('**/api/stauplan', lambda r: r.fulfill(
            status=200, content_type='application/json', body='[]'))

    # ── Werkzeug ───────────────────────────────────────────────────────────

    def punkt(self, fx, fy):
        k = self.kasten
        return (k['x'] + k['width'] * fx, k['y'] + k['height'] * fy)

    def zahl(self):
        return self.pg.evaluate('() => _geRiss.raeume.length')

    def werkzeug(self, name):
        self.pg.click(f'[data-werkzeug="{name}"]')
        self.pg.wait_for_timeout(150)

    def rechteck(self, x0, y0, x1, y1):
        self.werkzeug('rechteck')
        a, b = self.punkt(x0, y0)
        c, d = self.punkt(x1, y1)
        self.pg.mouse.move(a, b)
        self.pg.mouse.down()
        self.pg.mouse.move(c, d, steps=8)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(300)

    # ── Prüfungen ──────────────────────────────────────────────────────────

    def test_das_werkzeug_arbeitet_auf_einer_kopie(self):
        """Erst Speichern macht die Änderung echt. Ohne das stünde jede
        halbfertige Linie sofort im gespeicherten Riss."""
        vorher = self.pg.evaluate('() => (_geStand.raeume || []).length')
        self.rechteck(.35, .60, .65, .70)
        self.assertEqual(self.zahl(), vorher + 1)
        self.assertEqual(self.pg.evaluate('() => (_geStand.raeume || []).length'), vorher)

    def test_rechteck_aufziehen_legt_einen_raum_an(self):
        vorher = self.zahl()
        self.rechteck(.35, .60, .65, .70)
        self.assertEqual(self.zahl(), vorher + 1)
        self.assertEqual(self.pg.evaluate('() => _geRaum(_geAuswahl).form.t'), 'rechteck')

    def test_ein_tipp_ohne_bewegung_ist_kein_raum(self):
        """Sonst legt jeder Fehlgriff eine unsichtbare Null-Fläche an."""
        vorher = self.zahl()
        self.werkzeug('rechteck')
        a, b = self.punkt(.5, .5)
        self.pg.mouse.click(a, b)
        self.pg.wait_for_timeout(300)
        self.assertEqual(self.zahl(), vorher)

    def test_vieleck_mit_der_eingabetaste_schliessen(self):
        vorher = self.zahl()
        self.werkzeug('vieleck')
        for fx, fy in ((.35, .74), (.65, .74), (.60, .84), (.40, .84)):
            self.pg.mouse.click(*self.punkt(fx, fy))
            self.pg.wait_for_timeout(120)
        self.pg.keyboard.press('Enter')
        self.pg.wait_for_timeout(300)
        self.assertEqual(self.zahl(), vorher + 1)
        self.assertEqual(self.pg.evaluate('() => _geRaum(_geAuswahl).form.t'), 'vieleck')
        self.assertEqual(self.pg.evaluate('() => _geRaum(_geAuswahl).form.punkte.length'), 4)

    def test_zwei_punkte_sind_keine_flaeche(self):
        self.werkzeug('vieleck')
        for fx, fy in ((.4, .8), (.6, .8)):
            self.pg.mouse.click(*self.punkt(fx, fy))
            self.pg.wait_for_timeout(120)
        vorher = self.zahl()
        self.pg.keyboard.press('Enter')
        self.pg.wait_for_timeout(250)
        self.assertEqual(self.zahl(), vorher)

    def test_umbenennen_und_faerben(self):
        self.rechteck(.35, .60, .65, .70)
        self.pg.fill('#geRaumName', 'Achterpiek')
        self.pg.wait_for_timeout(250)
        self.assertEqual(self.pg.evaluate('() => _geRaum(_geAuswahl).name'), 'Achterpiek')
        self.pg.click('#geRaumFarben .ge-farbe:nth-child(3)')
        self.pg.wait_for_timeout(200)
        self.assertEqual(self.pg.evaluate('() => _geRaum(_geAuswahl).farbe'), '#60a5fa')

    def test_entfernen_taste_loescht_den_gewaehlten_raum(self):
        self.rechteck(.35, .60, .65, .70)
        n = self.zahl()
        # Nach dem Aufziehen steht der Zeiger im Namensfeld — dort loescht die
        # Taste Buchstaben und keine Raeume. Erst zurueck auf die Flaeche.
        self.pg.evaluate('() => document.getElementById("geRaumName").blur()')
        self.pg.keyboard.press('Delete')
        self.pg.wait_for_timeout(250)
        self.assertEqual(self.zahl(), n - 1)
        self.assertIsNone(self.pg.evaluate('() => _geAuswahl'))

    def test_zurueck_nimmt_den_letzten_schritt_zurueck(self):
        self.rechteck(.35, .60, .65, .70)
        n = self.zahl()
        self.pg.click('#geZurueck')
        self.pg.wait_for_timeout(250)
        self.assertEqual(self.zahl(), n - 1)

    def test_nichts_liegt_ausserhalb_der_zeichenflaeche(self):
        """Was draußen liegt, ist unsichtbar — und damit weder wiederzufinden
        noch zu löschen. Der Zeiger geht über den Rand hinaus, die Fläche nicht."""
        # Innerhalb anfangen (sonst nimmt die Fläche den Druck gar nicht an)
        # und weit nach links hinausziehen.
        self.rechteck(.30, .60, -.45, .70)
        f = self.pg.evaluate('() => _geRaum(_geAuswahl).form')
        self.assertGreaterEqual(f['x'], 0)
        self.assertLessEqual(f['x'] + f['w'], self.pg.evaluate('() => _geRiss.ansicht.w'))

    def test_rumpfform_setzt_das_seitenverhaeltnis(self):
        self.pg.fill('#geLoa', '9')
        self.pg.fill('#geBreite', '3')
        self.pg.click('button:has-text("Rumpfform")')
        self.pg.wait_for_timeout(400)
        self.pg.click('.ge-vorlage[data-vorlage="fahrten"]')
        self.pg.wait_for_timeout(400)
        a = self.pg.evaluate('() => _geRiss.ansicht')
        self.assertAlmostEqual(a['h'] / a['w'], 3.0, places=1)
        self.assertTrue(self.pg.evaluate('() => !!_geRiss.rumpf'))

    def test_raeume_ziehen_beim_rumpfwechsel_mit(self):
        """Sonst säßen sie danach alle im Vorschiff, obwohl sich am Boot
        nichts geändert hat."""
        self.rechteck(.35, .60, .65, .70)
        vorher = self.pg.evaluate('() => ({y: _geRaum(_geAuswahl).form.y, h: _geRiss.ansicht.h})')
        self.pg.fill('#geLoa', '20')
        self.pg.fill('#geBreite', '4')
        self.pg.click('button:has-text("Rumpfform")')
        self.pg.wait_for_timeout(400)
        self.pg.click('.ge-vorlage[data-vorlage="breitheck"]')
        self.pg.wait_for_timeout(400)
        nach = self.pg.evaluate('() => ({y: _geRaum(_geAuswahl).form.y, h: _geRiss.ansicht.h})')
        self.assertAlmostEqual(vorher['y'] / vorher['h'], nach['y'] / nach['h'], places=2)

    def test_alle_rumpfformen_sind_gueltige_pfade(self):
        """Sie gehen als SVG in den Browser und durch die Prüfung des Pi.
        Ein Zeichen zu viel, und der Riss lässt sich nicht mehr speichern."""
        pfade = self.pg.evaluate("""() => _GE_RUMPF_VORLAGEN.map(v =>
            [v.id, geRumpfPfad({ w: 200, h: 680, ...v })])""")
        self.assertEqual(len(pfade), 7)
        self.assertIn('kat', [k for k, _ in pfade])
        import main as _main
        for kennung, d in pfade:
            self.assertTrue(_main._GR_PFAD.match(d), f'{kennung}: {d[:60]}')
            self.assertTrue(d.endswith('Z'), kennung)

    def test_der_katamaran_hat_drei_teile(self):
        """Zwei Rümpfe und das Deck dazwischen. Ein einzelner Umriss wäre
        kein Katamaran, sondern ein sehr breites Boot."""
        d = self.pg.evaluate(
            "() => geRumpfPfad({ w: 200, h: 480, art: 'kat' })")
        self.assertEqual(d.count('Z'), 3, d[:80])

    def test_kein_rumpf_verlaesst_die_zeichenflaeche(self):
        """Sonst steht das Boot halb neben dem Blatt."""
        grenzen = self.pg.evaluate("""() => _GE_RUMPF_VORLAGEN.map(v => {
            const zahlen = geRumpfPfad({ w: 200, h: 680, ...v })
                .match(/-?\\d+(\\.\\d+)?/g).map(Number);
            const xs = zahlen.filter((_, i) => i % 2 === 0);
            const ys = zahlen.filter((_, i) => i % 2 === 1);
            return [v.id, Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
        })""")
        for kennung, x0, x1, y0, y1 in grenzen:
            self.assertGreaterEqual(x0, -0.5, kennung)
            self.assertLessEqual(x1, 200.5, kennung)
            self.assertGreaterEqual(y0, -0.5, kennung)
            self.assertLessEqual(y1, 680.5, kennung)

    def test_gespeichert_wird_was_der_pi_zurueckgibt(self):
        """Der Server prüft und schneidet zurecht. Was er antwortet, ist ab
        dann die Wahrheit — nicht die Arbeitskopie im Browser."""
        self.rechteck(.35, .60, .65, .70)
        self.pg.click('#geSpeichern')
        self.pg.wait_for_timeout(600)
        self.assertEqual(len(self.gespeichert), 1)
        geschickt = self.gespeichert[0]
        self.assertEqual(self.pg.evaluate('() => _geStand.raeume.length'),
                         len(geschickt['raeume']))
        self.assertTrue(self.pg.evaluate('() => document.getElementById("geSpeichern").disabled'),
                        'nach dem Speichern gibt es nichts mehr zu speichern')

    def test_das_geschickte_besteht_die_pruefung_des_pi(self):
        """Der eigentliche Test: was das Werkzeug baut, muss der Prüfer
        annehmen — und zwar DERSELBE, der am Boot prüft (sync/grundriss.py).
        Sonst merkt man es erst beim Laden."""
        self.rechteck(.35, .60, .65, .70)
        self.werkzeug('vieleck')
        for fx, fy in ((.35, .76), (.65, .76), (.60, .86), (.40, .86)):
            self.pg.mouse.click(*self.punkt(fx, fy))
            self.pg.wait_for_timeout(120)
        self.pg.keyboard.press('Enter')
        self.pg.wait_for_timeout(250)
        self.pg.click('button:has-text("Rumpfform")')
        self.pg.wait_for_timeout(400)
        self.pg.click('.ge-vorlage[data-vorlage="langkieler"]')
        self.pg.wait_for_timeout(400)
        self.pg.click('#geSpeichern')
        self.pg.wait_for_timeout(600)

        import main as _main
        geprueft = _main._grundriss_pruefen(self.gespeichert[0])
        self.assertEqual(len(geprueft['raeume']), len(self.gespeichert[0]['raeume']))
        self.assertTrue(geprueft['rumpf'])


    # ── Planvorlage ────────────────────────────────────────────────────────

    def _vorlage_hochladen(self, w=600, h=1600):
        pfad = pathlib.Path(tempfile.mkdtemp(prefix='mave-plan-')) / 'plan.png'
        pfad.write_bytes(_test_png(w, h))
        self.pg.set_input_files('#geVorlageBox input[type=file]', str(pfad))
        self.pg.wait_for_timeout(2000)
        return pfad

    def test_vorlage_wird_verkleinert_hochgeladen(self):
        """Verkleinert wird im Browser: Pillow gibt es auf dem Pi nicht, und
        ein Handyfoto hat acht Megapixel.

        Gemessen wird die KANTENLÄNGE, nicht die Dateigröße. Ein Bild aus
        lauter Rauschen — wie das Prüfbild hier — wird als JPEG größer als als
        PNG, und das wäre trotzdem kein Fehler: verkleinert ist es dann
        immer noch.
        """
        self._vorlage_hochladen(600, 1600)
        self.assertIsNotNone(self.vorlage, 'nichts hochgeladen')
        self.assertTrue(self.vorlage.startswith(b'\xff\xd8\xff'), 'kein JPEG')
        masse = self.pg.evaluate("""async (b64) => {
            const bild = new Image();
            await new Promise(ok => { bild.onload = ok;
                                      bild.src = 'data:image/jpeg;base64,' + b64; });
            return [bild.width, bild.height];
        }""", base64.b64encode(self.vorlage).decode())
        self.assertLessEqual(max(masse), 1400, f'nicht verkleinert: {masse}')
        # Und das Seitenverhältnis bleibt: 600 zu 1600.
        self.assertAlmostEqual(masse[0] / masse[1], 600 / 1600, places=2)

    def test_vorlage_erscheint_im_riss(self):
        self._vorlage_hochladen()
        self.assertTrue(self.pg.evaluate("() => !!document.querySelector('#geSvg image')"))
        bild = self.pg.evaluate('() => _geRiss.bild')
        self.assertGreater(bild['w'], 0)
        self.assertGreater(bild['h'], 0)

    def test_vorlage_passt_sich_in_die_flaeche_ein(self):
        """Sie soll beim ersten Mal ganz zu sehen sein und ihr Verhältnis
        behalten — von da aus wird geschoben."""
        self._vorlage_hochladen(600, 1600)
        b = self.pg.evaluate('() => _geRiss.bild')
        a = self.pg.evaluate('() => _geRiss.ansicht')
        self.assertAlmostEqual(b['w'] / b['h'], 600 / 1600, places=1)
        self.assertLessEqual(b['w'], a['w'] + 1)
        self.assertLessEqual(b['h'], a['h'] + 1)

    def test_regler_aendern_sichtbarkeit_und_groesse(self):
        self._vorlage_hochladen()
        vorher = self.pg.evaluate('() => _geRiss.bild')
        self.pg.evaluate('() => { geVorlageDeckkraft(90); geVorlageGroesse(100); }')
        self.pg.wait_for_timeout(200)
        nach = self.pg.evaluate('() => _geRiss.bild')
        self.assertAlmostEqual(nach['deckkraft'], .9)
        self.assertEqual(nach['w'], 100)
        # Die Mitte bleibt stehen, sonst wandert das Bild beim Regeln davon.
        self.assertAlmostEqual(vorher['x'] + vorher['w'] / 2, nach['x'] + nach['w'] / 2, delta=1)

    def test_vorlage_entfernen(self):
        self._vorlage_hochladen()
        self.pg.evaluate('() => geVorlageEntfernen()')
        self.pg.wait_for_timeout(600)
        self.assertIsNone(self.vorlage)
        self.assertFalse(self.pg.evaluate("() => !!document.querySelector('#geSvg image')"))
        self.assertIsNone(self.pg.evaluate('() => _geRiss.bild || null'))

    def test_die_platzierung_wird_mitgespeichert(self):
        self._vorlage_hochladen()
        self.pg.click('#geSpeichern')
        self.pg.wait_for_timeout(600)
        self.assertIn('bild', self.gespeichert[-1])
        import main as _main
        geprueft = _main._grundriss_pruefen(self.gespeichert[-1])
        self.assertEqual(set(geprueft['bild']), {'x', 'y', 'w', 'h', 'deckkraft'})


    def test_ans_boot_schickt_den_gespeicherten_stand(self):
        """Und nicht die Arbeitskopie — sonst schickte man dem Boot etwas,
        das man selbst noch nicht behalten wollte. Die Planvorlage bleibt hier:
        sie ist Werkzeug und nicht Riss."""
        self.rechteck(.35, .60, .65, .70)
        self.pg.click('#geSpeichern')
        self.pg.wait_for_timeout(600)
        self.pg.click('#geAnsBoot')
        self.pg.wait_for_timeout(600)
        self.assertEqual(len(self.ans_boot), 1)
        self.assertEqual(len(self.ans_boot[0]['raeume']), 1)
        self.assertNotIn('bild', self.ans_boot[0])
        self.assertNotIn('hat_vorlage', self.ans_boot[0])

    def test_die_datei_traegt_den_bootsnamen(self):
        self.rechteck(.35, .60, .65, .70)
        self.pg.fill('#geName', 'Mave')
        self.pg.click('#geSpeichern')
        self.pg.wait_for_timeout(600)
        with self.pg.expect_download() as ladung:
            self.pg.click('button:has-text("Datei")')
        self.assertEqual(ladung.value.suggested_filename, 'grundriss-mave.json')


if __name__ == '__main__':
    unittest.main()
