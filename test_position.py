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


if __name__ == '__main__':
    unittest.main()
