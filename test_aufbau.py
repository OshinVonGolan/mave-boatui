"""Die Aufbauseite darf nicht still veralten.

Sie nennt Takte — 200 ms hier, 25 Sekunden dort — und jede Zahl nennt dazu die
Stelle im Quelltext, an der sie steht. Dieser Test liest genau diese Stellen und
vergleicht. Aendert jemand einen Takt, faellt der Test um, und nicht ein Leser
in einem halben Jahr auf eine Zahl herein, die es nicht mehr gibt.

Eine Nachschlageseite, die still veraltet, ist schlimmer als keine: man glaubt
ihr. Aufruf:

    python3 -m unittest test_aufbau -v
"""
import re
import unittest
from pathlib import Path

WURZEL = Path(__file__).parent
LOGBUCH = (WURZEL / 'static' / 'js' / 'diagnose.js').read_text()


def _takt_tabelle() -> dict[str, list[dict]]:
    """AUFBAU_TAKTE aus diagnose.js lesen.

    Erst die Gruppen abgrenzen, dann je Gruppe die Eintraege — nicht beides in
    einem Ausdruck. Ein Ausdruck fuer beides sortierte die Eintraege der letzten
    Gruppe der ersten zu, und der Test verglich fleissig das Falsche.
    """
    anfang = LOGBUCH.index('const AUFBAU_TAKTE')
    block = LOGBUCH[anfang:LOGBUCH.index('/**\n * Die drei Namen', anfang)]
    marken = [(m.group(1), m.start()) for m in re.finditer(r'^  (\w+): \[', block, re.M)]
    raus: dict[str, list[dict]] = {}
    for i, (name, ab) in enumerate(marken):
        bis = marken[i + 1][1] if i + 1 < len(marken) else len(block)
        # Erst die geschweiften Bloecke, dann die Felder darin. Ein einziger
        # Ausdruck fuer beides scheiterte an der Reihenfolge: `neben` steht mal
        # vor, mal hinter `quelle`, und ein Eintrag ohne `quelle` fiel ganz
        # heraus — der Test verglich dann weniger, als dastand, und bestand.
        raus[name] = []
        for roh in re.findall(r'\{[^{}]*\}', block[ab:bis], re.S):
            feld = lambda n: (re.search(rf"{n}: '([^']*)'", roh) or [None, None])[1]
            raus[name].append({'was': feld('was'), 'wert': feld('wert'),
                               'quelle': feld('quelle')})
    return raus


def _ms(wert: str) -> float:
    """"200 ms", "25 s", "5 min" in Millisekunden."""
    zahl, _, einheit = wert.partition(' ')
    return float(zahl) * {'ms': 1, 's': 1000, 'min': 60000}[einheit]


class TakteStimmenMitDemCode(unittest.TestCase):

    def setUp(self):
        self.tabelle = _takt_tabelle()

    def test_die_tabelle_wurde_ueberhaupt_gelesen(self):
        # Ohne diese Pruefung wuerde ein kaputter Ausdruck oben alle folgenden
        # Tests still bestehen lassen: leere Liste, nichts zu vergleichen.
        self.assertEqual(set(self.tabelle), {'bordnetz', 'server', 'logbuch'})
        mit_quelle = [e for g in self.tabelle.values() for e in g if e['quelle']]
        self.assertGreaterEqual(len(mit_quelle), 15)

    def test_jede_zahl_steht_so_auch_im_quelltext(self):
        for gruppe, eintraege in self.tabelle.items():
            for e in eintraege:
                if not e['quelle']:
                    continue
                with self.subTest(gruppe=gruppe, was=e['was'], quelle=e['quelle']):
                    self.assertEqual(_ms(e['wert']), self._aus_quelle(e['quelle']),
                                     f"{e['was']}: Seite sagt {e['wert']}")

    def _aus_quelle(self, quelle: str) -> float:
        datei, _, symbol = quelle.rpartition(':')
        text = (WURZEL / datei).read_text()
        if datei.endswith('.py'):
            return self._aus_python(text, symbol)
        return self._aus_js(text, symbol)

    def _aus_python(self, text: str, symbol: str) -> float:
        if symbol.startswith('BETRIEBSARTEN.'):
            art = symbol.split('.', 1)[1]
            # Die Zeile der Betriebsart, davon 'zustand_s'.
            zeile = re.search(rf"^\s*{art.upper()}:\s*\{{([^}}]*)\}}", text, re.M | re.I)
            self.assertIsNotNone(zeile, f'Betriebsart {art} nicht gefunden')
            wert = re.search(r"'zustand_s':\s*([0-9.]+)", zeile.group(1))
            return float(wert.group(1)) * 1000
        wert = re.search(rf"^{re.escape(symbol)}\s*=\s*([0-9.]+)", text, re.M)
        self.assertIsNotNone(wert, f'{symbol} nicht gefunden')
        # Sekundenkonstanten heissen im Projekt durchgaengig ..._S.
        return float(wert.group(1)) * (1000 if symbol.endswith('_S') else 1)

    def _aus_js(self, text: str, symbol: str) -> float:
        """Den Takt hinter einem Namen aus einer JS-Datei holen.

        Drei Schreibweisen kommen vor, und alle drei sind gewollt: eine
        benannte Konstante, ein createPoller mit Zahl, ein createPoller mit
        einer Konstanten als Argument. Die dritte ist der Grund fuer die
        Aufloesung unten — `createPoller(fetchDailyStats, DAILY_POLL_MS)` haette
        sonst eine leere Zahl ergeben und der Test waere am eigenen Ausdruck
        gescheitert statt am Code.
        """
        roh = self._rohwert(text, symbol)
        self.assertIsNotNone(roh, f'{symbol} nicht gefunden')
        return self._rechnen(text, roh, symbol)

    def _rohwert(self, text: str, symbol: str):
        # Die Aufrufe ZUERST: ein Poller wird ebenfalls als `const` angelegt
        # (`const _wxPoller = createPoller(...)`), und das schlichte Muster
        # haette den ganzen Aufruf als Wert zurueckgegeben.
        for muster in (rf"{re.escape(symbol)}\s*=\s*createPoller\([^,]*,\s*([^,)]+)",
                       rf"{re.escape(symbol)}\s*=\s*setInterval\([^,]*,\s*([^,)]+)",
                       rf"^const\s+{re.escape(symbol)}\s*=\s*([^;/\n]+)"):
            t = re.search(muster, text, re.M)
            if t:
                return t.group(1).strip()
        return None

    def _rechnen(self, text: str, ausdruck: str, symbol: str) -> float:
        """Zahlen und Mal-Zeichen ausrechnen, Namen vorher aufloesen."""
        ausdruck = ausdruck.strip()
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', ausdruck):
            weiter = self._rohwert(text, ausdruck)
            self.assertIsNotNone(weiter, f'{ausdruck} (aus {symbol}) nicht gefunden')
            return self._rechnen(text, weiter, ausdruck)
        self.assertRegex(ausdruck, r'^[0-9 *]+$',
                         f'{symbol}: mit "{ausdruck}" kann dieser Test nicht rechnen')
        zahl = 1.0
        for teil in ausdruck.split('*'):
            zahl *= float(teil.strip())
        # Sekundenkonstanten heissen im Projekt durchgaengig ..._S.
        return zahl * 1000 if symbol.endswith('_S') else zahl


class SeiteBleibtVollstaendig(unittest.TestCase):
    """Was die Seite behauptet, muss es geben."""

    def test_genannte_dateien_existieren(self):
        for gruppe in _takt_tabelle().values():
            for e in gruppe:
                if not e['quelle']:
                    continue
                datei = e['quelle'].rpartition(':')[0]
                with self.subTest(datei=datei):
                    self.assertTrue((WURZEL / datei).is_file(), f'{datei} fehlt')

    def test_die_wandfassung_gibt_es_wirklich(self):
        # Die Seite erklaert den Wandmonitor unter /wand mit eigenem Manifest.
        # Beides muss dastehen, sonst erklaert sie etwas Erfundenes.
        self.assertIn("@app.get('/wand'", (WURZEL / 'main.py').read_text())
        self.assertTrue((WURZEL / 'static' / 'manifest-wand.json').is_file())

    def test_die_seite_haengt_in_der_schiene(self):
        html = (WURZEL / 'static' / 'diagnose.html').read_text()
        self.assertIn('data-seite="aufbau"', html)
        self.assertIn('id="aufbau"', html)


if __name__ == '__main__':
    unittest.main()
