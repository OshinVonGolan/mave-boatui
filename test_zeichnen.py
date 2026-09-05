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
                         ['Batterie', '78', '%'])
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
class HaltenOeffnetDetail(Pruefstand):
    """Zwei Sekunden halten führt zur Detailseite.

    Seit die Felder beim Tippen durchschalten, war der kurze Weg dorthin weg.
    Dieselbe Geste wie beim Relais in der Lichtkachel — eine Bedienung, die man
    einmal lernt, soll überall dasselbe bedeuten.
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

    def halten(self, ms):
        self.pg.mouse.move(self.x, self.y)
        self.pg.mouse.down()
        self.pg.wait_for_timeout(ms)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(400)

    def test_kurzer_tipp_schaltet_nur_weiter(self):
        self.pg.mouse.click(self.x, self.y)
        self.pg.wait_for_timeout(300)
        self.assertEqual(self.label(), 'Starter')
        self.assertFalse(self.offen(), 'ein Tipp soll keine Seite öffnen')

    def test_halten_oeffnet(self):
        self.halten(2400)
        self.assertTrue(self.offen())

    def test_halten_schaltet_nicht_zusaetzlich_weiter(self):
        """Beides zugleich wäre Unsinn: man landet auf der Detailseite und
        hätte nebenbei die Auswahl darunter verstellt."""
        vorher = self.label()
        self.halten(2400)
        self.assertEqual(self.label(), vorher)

    def test_zu_kurz_oeffnet_nicht(self):
        self.halten(900)
        self.assertFalse(self.offen())

    def test_nur_felder_mit_seite(self):
        """Für die Tanks gibt es keine Detailseite — dort darf das Halten auch
        nichts vortäuschen."""
        self.assertIsNone(self.pg.evaluate(
            "() => document.getElementById('sbTankItem').dataset.detail ?? null"))


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

    def test_ohne_eintrag_gilt_die_vorgabe(self):
        self.assertEqual(self.pg.evaluate("() => chName(2)"), 'Salon')

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

    def test_relais_erst_nach_zwei_sekunden(self):
        """Ohne die Wartezeit schaltete es jedes Mal mit, wenn jemand die
        Detailseite öffnen wollte."""
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
        self.pg.wait_for_timeout(2400)
        self.pg.mouse.up()
        self.pg.wait_for_timeout(400)
        self.assertNotEqual(self.pg.evaluate("() => _wideCh[8]"), vorher)
        self.assertTrue(self.pg.evaluate(
            "() => document.getElementById('lightOverlay').classList.contains('hidden')"),
            'nach dem Halten soll nicht auch noch die Seite aufgehen')


if __name__ == '__main__':
    unittest.main()
