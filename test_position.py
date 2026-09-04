"""Position: was der Router meldet und was daraus eine Spur wird.

Die Position ist die einzige Angabe an Bord, die von aussen kommt und nicht
vom NMEA-2000-Bus. Sie hat zwei Eigenheiten, die man falsch machen kann:

  1. RutOS liefert alles als Zeichenkette — auch Zahlen.
  2. RutOS antwortet auch OHNE Fix mit einer Position: der zuletzt bekannten.
     Eine alte Position, die aussieht wie eine aktuelle, ist auf einem Boot
     schlimmer als gar keine.

Aufruf:

    ./venv/bin/python -m unittest test_position -v
"""
import unittest

from connectivity import _gps_lesen


class WasDerRouterMeldet(unittest.TestCase):

    GUT = {
        'fix_status': '1', 'satellites': '8', 'accuracy': '0.6',
        'latitude': '53.896098', 'longitude': '10.769512',
        'altitude': '-0.2', 'utc_timestamp': '1788518745',
        'speed': '0', 'angle': '0', 'timestamp': '1788522345',
    }

    def test_zeichenketten_werden_zahlen(self):
        p = _gps_lesen(dict(self.GUT))
        self.assertIsInstance(p['lat'], float)
        self.assertIsInstance(p['lon'], float)
        self.assertIsInstance(p['satelliten'], int)
        self.assertAlmostEqual(p['lat'], 53.896098)
        self.assertAlmostEqual(p['lon'], 10.769512)
        self.assertEqual(p['satelliten'], 8)
        self.assertAlmostEqual(p['hoehe_m'], -0.2)

    def test_ohne_fix_kommt_nichts(self):
        """Der wichtigste Fall. Der Router liefert dann die zuletzt bekannte
        Position — sie wäre eine Lüge über den jetzigen Ort."""
        for fix in ('0', '', 'none', None):
            with self.subTest(fix=fix):
                roh = dict(self.GUT, fix_status=fix)
                self.assertIsNone(_gps_lesen(roh))

    def test_nullinsel_gilt_nicht(self):
        """0/0 liegt im Atlantik vor Ghana und heisst in der Praxis
        'kein Wert'."""
        self.assertIsNone(_gps_lesen(dict(self.GUT, latitude='0', longitude='0')))

    def test_unsinnige_werte_fallen_durch(self):
        for lat, lon in (('91', '10'), ('53', '181'), ('abc', '10'), ('', '')):
            with self.subTest(lat=lat, lon=lon):
                self.assertIsNone(_gps_lesen(dict(self.GUT, latitude=lat, longitude=lon)))

    def test_nur_die_utc_zeit_wird_genommen(self):
        """Der Router führt zwei Zeitstempel, und `timestamp` steht in seiner
        eigenen, hier falsch gestellten Zeitzone."""
        p = _gps_lesen(dict(self.GUT))
        self.assertEqual(p['zeit'], 1788518745.0)

    def test_kurs_und_fahrt_gehen_nicht_mit(self):
        """Ausdrücklicher Wunsch des Eigners: Position, keine Navigation."""
        p = _gps_lesen(dict(self.GUT))
        for feld in ('speed', 'angle', 'sog', 'cog', 'kurs', 'fahrt'):
            self.assertNotIn(feld, p)

    def test_sechs_stellen_bleiben_erhalten(self):
        """Vier Stellen wären rund elf Meter — am Liegeplatz sichtbar."""
        p = _gps_lesen(dict(self.GUT, latitude='53.8960983', longitude='10.7695127'))
        self.assertEqual(p['lat'], 53.896098)
        self.assertEqual(p['lon'], 10.769513)


class Ausduennen(unittest.TestCase):
    """Die Spur: nach zurückgelegtem Weg, nicht nach Zeit."""

    def setUp(self):
        import sys, os
        os.environ.setdefault('MAVE_GERAET_TOKEN', 'x' * 20)
        os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)
        sys.path.insert(0, '.')
        from server.app import _meter
        self.meter = _meter

    def test_abstand_stimmt(self):
        # Ein Bogenminute Breite ist definitionsgemäss eine Seemeile.
        d = self.meter(53.0, 10.0, 53.0 + 1 / 60, 10.0)
        self.assertAlmostEqual(d, 1852, delta=6)

    def test_abstand_in_laengsrichtung_wird_kleiner(self):
        """Auf 54° Nord ist ein Längengrad nur noch gut halb so breit."""
        nord = self.meter(54.0, 10.0, 54.0, 10.1)
        aequator = self.meter(0.0, 10.0, 0.0, 10.1)
        self.assertLess(nord, aequator * 0.62)
        self.assertGreater(nord, aequator * 0.55)

    def test_dieselbe_stelle_ist_null(self):
        self.assertAlmostEqual(self.meter(53.896098, 10.769512,
                                          53.896098, 10.769512), 0.0, places=6)


class SpurOhneStapel(unittest.TestCase):
    """Der ausdrückliche Wunsch des Eigners: keine hundert Punkte auf demselben
    Fleck, wenn das Boot sich nicht bewegt hat.

    Vorher setzte die Spur zusätzlich alle zwei Stunden einen Punkt, damit sich
    "lag da" von "nichts aufgeschrieben" unterscheiden liess. Der Gedanke war
    richtig, die Umsetzung nicht: drei Wochen am Steg ergaben dreihundertsechzig
    Punkte übereinander. Dieselbe Auskunft trägt jetzt die Liegezeit am Punkt
    (`bis`), und eine echte Lücke beginnt einen neuen Abschnitt (`neu`).
    """

    def setUp(self):
        import os, sys
        os.environ.setdefault('MAVE_GERAET_TOKEN', 'x' * 20)
        os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)
        sys.path.insert(0, '.')
        from server.app import _spur_ausduennen, _SPUR_ABSTAND_M
        self.duennen = _spur_ausduennen
        self.abstand = _SPUR_ABSTAND_M

    def test_am_steg_bleibt_es_bei_einem_punkt(self):
        """Sieben Tage im Minutentakt am selben Fleck, mit dem Rauschen eines
        Empfängers (rund zwei Meter). Das ist der Alltag dieses Bootes."""
        import random
        rnd = random.Random(7)
        t0 = 1788000000
        roh = [(t0 + i * 60,
                53.896098 + (rnd.random() - .5) * 0.000036,
                10.769512 + (rnd.random() - .5) * 0.000060)
               for i in range(7 * 24 * 60)]
        punkte = self.duennen(roh, self.abstand)
        self.assertEqual(len(punkte), 1,
                         f'Ein Boot, das liegt, ergibt einen Punkt — nicht {len(punkte)}.')
        # Und die Liegezeit steht dran, statt in Punkten gezählt zu werden.
        self.assertAlmostEqual(punkte[0]['bis'] - punkte[0]['t'],
                               (7 * 24 * 60 - 1) * 60, delta=1)

    def test_bewegung_ergibt_punkte(self):
        """Eine Fahrt muss sichtbar bleiben."""
        t0 = 1788000000
        # Rund 55 m je Schritt nach Norden (0.0005° Breite)
        roh = [(t0 + i * 60, 53.896098 + i * 0.0005, 10.769512) for i in range(20)]
        punkte = self.duennen(roh, self.abstand)
        self.assertEqual(len(punkte), 20)

    def test_unter_der_schwelle_entsteht_nichts_neues(self):
        """Zehn Meter sind keine Bewegung — zwanzig schon."""
        t0 = 1788000000
        # 0.00009° Breite sind rund 10 m
        roh = [(t0 + i * 60, 53.896098 + i * 0.00009, 10.769512) for i in range(3)]
        self.assertEqual(len(self.duennen(roh, 20.0)), 2)   # nach zwei Schritten 20 m
        self.assertEqual(len(self.duennen(roh, 50.0)), 1)

    def test_luecke_beginnt_einen_neuen_abschnitt(self):
        """Schweigt der Bordrechner, darf die Karte keine Linie darüber ziehen —
        wo das Boot dazwischen war, weiss niemand."""
        t0 = 1788000000
        roh = [(t0, 53.896098, 10.769512),
               (t0 + 60, 53.896098, 10.769512),
               # zwei Stunden Stille, danach derselbe Fleck
               (t0 + 7200, 53.896098, 10.769512),
               (t0 + 7260, 53.896098, 10.769512)]
        punkte = self.duennen(roh, self.abstand)
        self.assertEqual(len(punkte), 2)
        self.assertFalse(punkte[0]['neu'])
        self.assertTrue(punkte[1]['neu'],
                        'Nach einer Lücke muss ein neuer Abschnitt beginnen.')

    def test_kurze_stille_ist_keine_luecke(self):
        """Ein paar ausgefallene Minuten sind Betrieb, kein Loch."""
        t0 = 1788000000
        roh = [(t0, 53.896098, 10.769512), (t0 + 300, 53.896098, 10.769512)]
        punkte = self.duennen(roh, self.abstand)
        self.assertEqual(len(punkte), 1)

    def test_leere_eingabe(self):
        self.assertEqual(self.duennen([], self.abstand), [])


if __name__ == '__main__':
    unittest.main()
