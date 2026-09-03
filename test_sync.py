"""Tests des gemeinsamen Fundaments.

Kein Boot, kein Server, kein Netz: alles hier sind reine Funktionen. Aufruf:

    python3 -m unittest test_sync -v
"""
import unittest

from sync import protokoll as p
from sync import zeit as z

# Eine echte Zeit als Bezug (irgendwann 2026) und der Rueckfall eines Pi ohne
# gestellte Uhr.
JETZT = 1788000000.0
OHNE_UHR = 1000.0          # was die Uhr nach dem Hochlauf sagt: Unsinn


class Zeitrechnung(unittest.TestCase):

    def test_gestellte_uhr_gilt_direkt(self):
        b = z.Uhrbuch()
        b.merke(JETZT, 500.0, True)
        self.assertEqual(b.aufloesen(JETZT, 500.0, True), JETZT)

    def test_ohne_uhr_und_ohne_referenz_gibt_es_keine_zeit(self):
        # Das ist ein GUELTIGES Ergebnis: der Eintrag wird geparkt, nicht
        # geraten. Wuerde hier die Wanduhr gelten, landete er im Jahr 1970.
        b = z.Uhrbuch()
        self.assertIsNone(b.aufloesen(OHNE_UHR, 30.0, False))

    def test_nachtraegliches_einordnen_nach_dem_ntp_abgleich(self):
        # Der Fall, um den es geht: Stromausfall, der Pi laeuft ohne Uhr los und
        # schreibt Verlauf. Zwei Minuten spaeter kommt Internet, NTP stellt die
        # Uhr — ab da lassen sich die frueheren Eintraege zurueckrechnen.
        b = z.Uhrbuch()
        b.merke(OHNE_UHR, 30.0, False)          # Eintrag bei mono=30, Uhr falsch
        b.merke(JETZT, 150.0, True)             # NTP bei mono=150
        gerechnet = b.aufloesen(OHNE_UHR, 30.0, False)
        self.assertIsNotNone(gerechnet)
        # 120 s vor dem Abgleich entstanden
        self.assertAlmostEqual(gerechnet, JETZT - 120.0, places=3)

    def test_neustart_verwirft_die_referenz(self):
        # Der monotone Zaehler faellt zurueck: neue Laufzeit. Die alte Referenz
        # wuerde ab hier um die gesamte vorige Laufzeit falsch rechnen.
        b = z.Uhrbuch()
        b.merke(JETZT, 5000.0, True)
        self.assertTrue(b.hat_referenz)
        b.merke(OHNE_UHR, 12.0, False)          # Neustart
        self.assertFalse(b.hat_referenz)
        self.assertIsNone(b.aufloesen(OHNE_UHR, 20.0, False))

    def test_kleine_ruecksprunge_sind_kein_neustart(self):
        b = z.Uhrbuch()
        b.merke(JETZT, 500.0, True)
        b.merke(JETZT, 499.5, True)             # Rundung zwischen Threads
        self.assertTrue(b.hat_referenz)

    def test_juengster_abgleich_gewinnt(self):
        # NTP korrigiert nach; die letzte Korrektur ist die genaueste.
        b = z.Uhrbuch()
        b.merke(JETZT, 100.0, True)
        b.merke(JETZT + 300.5, 400.0, True)     # Uhr wurde um 0,5 s nachgezogen
        gerechnet = b.aufloesen(OHNE_UHR, 200.0, False)
        self.assertAlmostEqual(gerechnet, JETZT + 300.5 - 200.0, places=3)

    def test_unsinnige_wandzeit_gilt_auch_als_gestellt_nicht(self):
        # gestellt=True aber Zeit aus 1970: die Angabe widerspricht sich, und
        # die Zahl ist das Unglaubwuerdigere.
        b = z.Uhrbuch()
        self.assertIsNone(b.aufloesen(1000.0, 50.0, True))

    def test_stempel_rundet_und_haelt_none_aus(self):
        s = z.stempel(JETZT + 0.123456, 12.98765, True)
        self.assertEqual(s['wand'], round(JETZT + 0.123456, 3))
        self.assertEqual(s['mono'], 12.988)
        self.assertTrue(s['gestellt'])
        self.assertEqual(z.stempel(None, None, False),
                         {'wand': None, 'mono': None, 'gestellt': False})


class Umschlaege(unittest.TestCase):

    def test_hallo_traegt_den_eigenen_stand(self):
        n = p.hallo('mave-pi', p.FASSUNG, '1.57.0', 4711, p.VOLL,
                    wand=JETZT, mono=12.0, gestellt=True)
        self.assertEqual(n['typ'], p.HALLO)
        self.assertEqual(n['daten']['verlauf_folge'], 4711)
        self.assertEqual(n['zeit']['gestellt'], True)
        p.pruefe(n, vom_pi=True)

    def test_richtung_wird_durchgesetzt(self):
        # Der Server darf keinen Befehl von einem Boot annehmen: seine
        # Gegenstelle haengt im Internet.
        befehl = p.umschlag(p.BEFEHL, {'pfad': '/api/lights/channels'})
        with self.assertRaises(p.ProtokollFehler):
            p.pruefe(befehl, vom_pi=True)
        p.pruefe(befehl, vom_pi=False)          # vom Server ist er richtig

        zustand = p.umschlag(p.ZUSTAND, {'battery': {}})
        with self.assertRaises(p.ProtokollFehler):
            p.pruefe(zustand, vom_pi=False)

    def test_verlauf_ohne_folgenummer_wird_abgewiesen(self):
        with self.assertRaises(p.ProtokollFehler):
            p.pruefe({'typ': p.VERLAUF, 'daten': []}, vom_pi=True)
        with self.assertRaises(p.ProtokollFehler):
            p.pruefe({'typ': p.VERLAUF, 'folge': -1, 'daten': []}, vom_pi=True)
        p.pruefe({'typ': p.VERLAUF, 'folge': 0, 'daten': []}, vom_pi=True)

    def test_muell_wird_abgewiesen(self):
        for unsinn in (None, 'text', 42, [], {}, {'typ': 7},
                       {'typ': p.ZUSTAND, 'daten': 'text'},
                       {'typ': p.ZUSTAND, 'zeit': 'jetzt'},
                       {'typ': p.ZUSTAND, 'zeit': {'mono': 'gleich'}}):
            with self.subTest(unsinn=unsinn):
                with self.assertRaises(p.ProtokollFehler):
                    p.pruefe(unsinn, vom_pi=True)

    def test_luecke_erkennen(self):
        self.assertFalse(p.fehlt_dazwischen(10, 11))   # lueckenlos
        self.assertTrue(p.fehlt_dazwischen(10, 12))    # 11 fehlt
        self.assertFalse(p.fehlt_dazwischen(10, 10))   # Wiederholung, keine Luecke


class Betriebsarten(unittest.TestCase):

    def test_nur_mobilfunk_drosselt(self):
        self.assertEqual(p.betriebsart({'router': {'active_type': 'mobile'}}), p.GEDROSSELT)
        self.assertEqual(p.betriebsart({'router': {'active_type': 'wired'}}), p.VOLL)
        self.assertEqual(p.betriebsart({'router': {'active_type': 'wifi'}}), p.VOLL)

    def test_unbekannter_uplink_drosselt(self):
        # Der haeufigste Grund fuer einen stummen Router ist eine schlechte
        # Verbindung — dann ist sparsam die richtige Annahme.
        for leer in (None, {}, {'router': None}, {'router': {}},
                     {'router': {'active_type': ''}}):
            with self.subTest(leer=leer):
                self.assertEqual(p.betriebsart(leer), p.GEDROSSELT)

    def test_handschalter_gewinnt_immer(self):
        voll = {'router': {'active_type': 'mobile'}}
        self.assertEqual(p.betriebsart(voll, 'voll'), p.VOLL)
        kabel = {'router': {'active_type': 'wired'}}
        self.assertEqual(p.betriebsart(kabel, 'gedrosselt'), p.GEDROSSELT)
        self.assertEqual(p.betriebsart(kabel, 'auto'), p.VOLL)

    def test_takt_faellt_auf_sparsam_zurueck(self):
        self.assertEqual(p.takt(p.VOLL)['zustand_s'], 10)
        self.assertEqual(p.takt(p.GEDROSSELT)['zustand_s'], 60)
        self.assertEqual(p.takt('quatsch'), p.takt(p.GEDROSSELT))

    def test_alle_schalterstellungen_ergeben_eine_gueltige_art(self):
        for stellung in p.SCHALTER:
            with self.subTest(stellung=stellung):
                art = p.betriebsart({'router': {'active_type': 'wired'}}, stellung)
                self.assertIn(art, p.BETRIEBSARTEN)


if __name__ == '__main__':
    unittest.main()
