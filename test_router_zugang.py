"""Der Zugang zum Router — und was passiert, wenn er nicht mehr gilt.

Am 05.09.2026 vorgefunden: der Router hatte einen GPS-Fix, das Logbuch meldete
„kein Fix vom Router". Im Mitschnitt des Bordrechners stand alle 20 Sekunden
„HTTP Error 401: Unauthorized".

Die Ursache war nicht das GPS. `_token_headers` erneuerte den Zugang nur nach
ALTER (50 Minuten). Wies der Router ihn vorher ab — RutOS begrenzt die Zahl
gleichzeitiger Sitzungen, und wer sich am Router anmeldet, wirft die aelteste
heraus —, lief die Anwendung fuenfzig Minuten lang immer wieder in dasselbe
401. Weil schon der ERSTE Aufruf scheiterte, wurde der ganze Verbindungszustand
nicht mehr aktualisiert; die Position war nur das, was zuerst auffiel.

Aufruf:

    ./venv/bin/python -m unittest test_router_zugang -v
"""
import json
import unittest
import urllib.error

from connectivity import ConnectivityMonitor, _gps_lesen


class FalscherRouter:
    """Ein Router, der Zugaenge vergibt und sie wieder verwirft."""

    def __init__(self, verwirft_nach: int = 0):
        self.zugang = None
        self.anmeldungen = 0
        self.abrufe = []
        self._verwirft_nach = verwirft_nach     # so viele Abrufe gelten, dann 401
        self._seit_anmeldung = 0

    def http(self, url, data=None, headers=None):
        if url.endswith('/api/login'):
            self.anmeldungen += 1
            self.zugang = f'zugang-{self.anmeldungen}'
            self._seit_anmeldung = 0
            return {'data': {'token': self.zugang}}
        self.abrufe.append(url)
        mit = (headers or {}).get('Authorization', '')
        if mit != f'Bearer {self.zugang}':
            raise urllib.error.HTTPError(url, 401, 'Unauthorized', {}, None)
        if self._verwirft_nach and self._seit_anmeldung >= self._verwirft_nach:
            self.zugang = None                  # der Router hat sie herausgeworfen
            raise urllib.error.HTTPError(url, 401, 'Unauthorized', {}, None)
        self._seit_anmeldung += 1
        return {'data': {'ok': True}}


def bauen(router: FalscherRouter) -> ConnectivityMonitor:
    m = ConnectivityMonitor('https://192.168.1.1', 'admin', 'geheim')
    m._http = router.http
    return m


class ZugangWirdAbgewiesen(unittest.TestCase):

    def test_im_normalfall_eine_anmeldung(self):
        r = FalscherRouter()
        m = bauen(r)
        for _ in range(3):
            m._router_holen('/api/gps/position/status')
        self.assertEqual(r.anmeldungen, 1, 'meldet sich unnoetig oft neu an')

    def test_nach_einer_abweisung_neu_anmelden(self):
        """Der eigentliche Fehler: vorher blieb es beim toten Zugang."""
        r = FalscherRouter(verwirft_nach=1)
        m = bauen(r)
        m._router_holen('/api/gps/position/status')          # gilt noch
        antwort = m._router_holen('/api/gps/position/status')  # wird abgewiesen
        self.assertEqual(antwort, {'data': {'ok': True}}, 'Abruf hat sich nicht erholt')
        self.assertEqual(r.anmeldungen, 2)

    def test_nur_einmal_wiederholen(self):
        """Bleibt es beim 401, ist es kein Zugangsproblem mehr — dann darf die
        Anwendung den Router nicht in einer Schleife beschaeftigen."""
        r = FalscherRouter(verwirft_nach=0)

        def immer_abweisen(url, data=None, headers=None):
            if url.endswith('/api/login'):
                return r.http(url, data, headers)
            r.abrufe.append(url)
            raise urllib.error.HTTPError(url, 401, 'Unauthorized', {}, None)

        m = bauen(r)
        m._http = immer_abweisen
        with self.assertRaises(urllib.error.HTTPError):
            m._router_holen('/api/gps/position/status')
        self.assertEqual(len(r.abrufe), 2, 'genau ein Wiederholungsversuch')

    def test_andere_fehler_gehen_durch(self):
        """Ein 500 ist kein Zugangsproblem. Sich dafuer neu anzumelden waere
        Aberglaube — und verdeckte den echten Fehler."""
        r = FalscherRouter()
        m = bauen(r)
        m._router_holen('/api/x')                      # einmal anmelden
        vorher = r.anmeldungen

        def fuenfhundert(url, data=None, headers=None):
            raise urllib.error.HTTPError(url, 500, 'Server Error', {}, None)

        m._http = fuenfhundert
        with self.assertRaises(urllib.error.HTTPError) as f:
            m._router_holen('/api/x')
        self.assertEqual(f.exception.code, 500)
        self.assertEqual(r.anmeldungen, vorher)

    def test_alle_wege_gehen_ueber_die_zweite_chance(self):
        """Sonst faellt derselbe Fehler beim naechsten Endpunkt wieder an."""
        import inspect
        quelle = inspect.getsource(ConnectivityMonitor._fetch_router)
        self.assertNotIn('self._http(', quelle,
                         'ein Router-Abruf geht am Wiederanmelden vorbei')
        self.assertGreaterEqual(quelle.count('self._router_holen('), 5)


class PositionLesen(unittest.TestCase):
    """Der Parser war unschuldig — das bleibt hier festgehalten."""

    ECHT = {'fix_status': '1', 'timestamp': '1788627253', 'utc_timestamp': '1788623653',
            'satellites': '9', 'angle': '0', 'speed': '0', 'longitude': '10.769501',
            'latitude': '53.896100', 'altitude': '-1.8', 'accuracy': '0.8'}

    def test_echte_antwort_des_routers(self):
        """Wortlaut vom RUTX50 am 05.09.2026, direkt abgefragt."""
        p = _gps_lesen(self.ECHT)
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p['lat'], 53.8961, places=6)
        self.assertAlmostEqual(p['lon'], 10.769501, places=6)
        self.assertEqual(p['satelliten'], 9)
        self.assertEqual(p['zeit'], 1788623653.0)

    def test_ohne_fix_nichts(self):
        self.assertIsNone(_gps_lesen({**self.ECHT, 'fix_status': '0'}))


if __name__ == '__main__':
    unittest.main()
