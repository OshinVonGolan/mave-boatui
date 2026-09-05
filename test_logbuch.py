"""Tests des Logbuch-Dashboards — Alarmfaltung, Geraetenamen, Wartungsstand.

Kein Netz, kein Boot, kein Container: die drei Stellen mit echter Logik werden
einzeln gerechnet. Aufruf:

    python3 -m unittest test_logbuch -v
"""
import datetime
import tempfile
import unittest
from pathlib import Path

import geraete
from server.speicher import Speicher

JETZT = 1788000000.0


class AlarmeFalten(unittest.TestCase):
    """Aus dem Ereignisstrom wird die Liste hinter dem Vorhang.

    Der Strom sagt "Alarm da", "quittiert", "wieder weg" — der Vorhang muss
    daraus je Vorfall EINE Zeile machen und dabei zwei Dinge auseinanderhalten,
    die leicht zusammenfallen: gesehen und vorbei.
    """

    def setUp(self):
        self.ort = Path(tempfile.mkdtemp()) / 'mave.db'
        self.s = Speicher(self.ort)
        # Der Server haelt seinen Speicher als Modulvariable; fuer den Test
        # wird sie getauscht und danach zurueckgegeben.
        import server.app as app
        self.app = app
        self.vorher = app.speicher
        app.speicher = self.s

    def tearDown(self):
        self.app.speicher = self.vorher
        self.s.schliessen()

    def _e(self, folge, art, kennung, **rest):
        self.s.ereignis_anhaengen(folge, art, {'art': art, 'kennung': kennung, **rest},
                                  JETZT + folge)

    def test_ein_alarm_wird_eine_zeile(self):
        self._e(1, 'alarm', 'batt_low', name='Ladestand niedrig',
                wert=21.4, schwelle=25, schwere='critical', zeit=JETZT)
        (a,) = self.app._alarme_seit(0)
        self.assertEqual(a['name'], 'Ladestand niedrig')
        self.assertEqual(a['schwere'], 'critical')
        self.assertFalse(a['quittiert'])
        self.assertFalse(a['weg'])
        self.assertEqual(a['male'], 1)

    def test_quittiert_und_weg_sind_zwei_aussagen(self):
        # Genau das ist der Grund fuer den Vorhang: ein Alarm, der von selbst
        # wieder ging, ohne dass ihn je jemand gesehen hat, ist etwas anderes
        # als einer, den jemand zur Kenntnis genommen hat.
        self._e(1, 'alarm', 'a', name='A', schwere='warning', zeit=JETZT)
        self._e(2, 'alarm_weg', 'a')
        self._e(3, 'alarm', 'b', name='B', schwere='warning', zeit=JETZT)
        self._e(4, 'alarm_quittiert', 'b')
        nach = {x['kennung']: x for x in self.app._alarme_seit(0)}
        self.assertEqual((nach['a']['quittiert'], nach['a']['weg']), (False, True))
        self.assertEqual((nach['b']['quittiert'], nach['b']['weg']), (True, False))

    def test_mehrfach_gekommen_wird_gezaehlt_nicht_gedoppelt(self):
        self._e(1, 'alarm', 'a', name='A', schwere='warning', zeit=JETZT)
        self._e(2, 'alarm_weg', 'a')
        self._e(3, 'alarm', 'a', name='A', schwere='warning', zeit=JETZT + 300)
        (a,) = self.app._alarme_seit(0)
        self.assertEqual(a['male'], 2)
        # Nach dem erneuten Auftreten steht er wieder an und ist nicht
        # quittiert — sonst zeigte der Vorhang einen offenen Alarm als erledigt.
        self.assertFalse(a['weg'])
        self.assertFalse(a['quittiert'])
        self.assertEqual(a['zeit'], JETZT + 300)

    def test_quittierung_ohne_auftreten_wird_uebergangen(self):
        # Der Alarm selbst liegt VOR dem Merker — er stand beim letzten Blick
        # schon in der Liste. Seine Quittierung ist kein neuer Vorfall.
        self._e(1, 'alarm', 'alt', name='Alt', schwere='warning', zeit=JETZT)
        self._e(2, 'alarm_quittiert', 'alt')
        self.assertEqual(self.app._alarme_seit(1), [])

    def test_schwere_zuerst_dann_das_juengste(self):
        self._e(1, 'alarm', 'i', name='I', schwere='info', zeit=JETZT)
        self._e(2, 'alarm', 'w1', name='W1', schwere='warning', zeit=JETZT + 10)
        self._e(3, 'alarm', 'c', name='C', schwere='critical', zeit=JETZT)
        self._e(4, 'alarm', 'w2', name='W2', schwere='warning', zeit=JETZT + 99)
        self.assertEqual([a['kennung'] for a in self.app._alarme_seit(0)],
                         ['c', 'w2', 'w1', 'i'])

    def test_ereignisse_ohne_kennung_fallen_weg(self):
        # Ein Ereignis ohne Kennung laesst sich keinem Vorfall zuordnen. Es als
        # eigene Zeile zu zeigen hiesse, einen Alarm zu erfinden.
        self._e(1, 'alarm', None, name='Namenlos')
        self.assertEqual(self.app._alarme_seit(0), [])


class GeraetenamenAusDemRouter(unittest.TestCase):
    """Adresse der Sitzung → Name des Geraets."""

    def test_wlan_schlaegt_lease(self):
        # Die WLAN-Liste ist eine Aussage ueber jetzt; eine Lease kann auf ein
        # Geraet zeigen, das laengst von Bord ist.
        namen = geraete.namen_nach_ip({'router': {
            'dhcp_leases': [{'ip': '192.168.1.196', 'mac': 'AA:BB:CC:DD:EE:FF',
                             'hostname': 'alter-name'}],
            'wifi_clients': [{'ip': '192.168.1.196', 'mac': 'aa:bb:cc:dd:ee:ff',
                              'hostname': 'Tab-S7-FE', 'signal': -52, 'band': '5G'}],
        }})
        self.assertEqual(namen['192.168.1.196']['name'], 'Tab-S7-FE')
        self.assertEqual(namen['192.168.1.196']['quelle'], 'wlan')
        self.assertEqual(namen['192.168.1.196']['signal'], -52)

    def test_leerer_wlan_name_ueberschreibt_den_der_lease_nicht(self):
        namen = geraete.namen_nach_ip({'router': {
            'dhcp_leases': [{'ip': '10.0.0.5', 'mac': '11:22:33:44:55:66',
                             'hostname': 'Drucker'}],
            'wifi_clients': [{'ip': '10.0.0.5', 'mac': '11:22:33:44:55:66',
                              'hostname': '', 'signal': -70}],
        }})
        self.assertEqual(namen['10.0.0.5']['name'], 'Drucker')

    def test_geraet_am_kabel_kommt_aus_der_lease(self):
        namen = geraete.namen_nach_ip({'router': {
            'dhcp_leases': [{'ip': '10.0.0.9', 'mac': 'de:ad:be:ef:00:01',
                             'hostname': 'Plotter'}],
            'wifi_clients': [],
        }})
        self.assertEqual(namen['10.0.0.9']['name'], 'Plotter')
        self.assertEqual(namen['10.0.0.9']['quelle'], 'lease')

    def test_ohne_router_leere_zuordnung(self):
        # Kein Router heisst kein Name — und keine Ausnahme. Die
        # Anwesenheitsliste soll trotzdem stehen.
        self.assertEqual(geraete.namen_nach_ip(None), {})
        self.assertEqual(geraete.namen_nach_ip({}), {})
        self.assertEqual(geraete.namen_nach_ip({'router': {}}), {})

    def test_eintraege_ohne_adresse_werden_uebergangen(self):
        namen = geraete.namen_nach_ip({'router': {
            'dhcp_leases': [{'ip': '', 'mac': 'aa:aa:aa:aa:aa:aa', 'hostname': 'x'}],
            'wifi_clients': [{'mac': 'bb:bb:bb:bb:bb:bb', 'hostname': 'y'}],
        }})
        self.assertEqual(namen, {})


class HerkunftHinterDemWeiterreicher(unittest.TestCase):
    """Von welcher Adresse eine Sitzung wirklich kommt.

    Seit nginx auf dem Pi die Verschluesselung uebernimmt, steht in
    `request.client.host` bei jeder HTTPS-Sitzung 127.0.0.1 — und damit trifft
    der Abgleich gegen die Geraeteliste des Routers nie.
    """

    class _Anfrage:
        def __init__(self, direkt, kopfzeilen=None):
            self.client = type('C', (), {'host': direkt})() if direkt else None
            self.headers = kopfzeilen or {}

    def _herkunft(self, direkt, kopfzeilen=None):
        import main
        return main._herkunft_vom_client(self._Anfrage(direkt, kopfzeilen))

    def test_direkte_verbindung_bleibt_wie_sie_ist(self):
        self.assertEqual(self._herkunft('192.168.1.196'), '192.168.1.196')

    def test_hinter_dem_weiterreicher_zaehlt_der_weitergereichte_wert(self):
        self.assertEqual(
            self._herkunft('127.0.0.1', {'x-forwarded-for': '192.168.1.196'}),
            '192.168.1.196')

    def test_aus_einer_kette_der_erste(self):
        self.assertEqual(
            self._herkunft('::1', {'x-forwarded-for': '192.168.1.196, 10.0.0.1'}),
            '192.168.1.196')

    def test_x_real_ip_als_zweiter_weg(self):
        self.assertEqual(self._herkunft('127.0.0.1', {'x-real-ip': '192.168.1.155'}),
                         '192.168.1.155')

    def test_fremder_kopf_wird_nicht_geglaubt(self):
        # Der Gegenueber ist NICHT der eigene Rechner. Wuerde der Kopf hier
        # zaehlen, koennte sich jeder im Bordnetz eine beliebige Herkunft geben.
        self.assertEqual(
            self._herkunft('192.168.1.9', {'x-forwarded-for': '192.168.1.196'}),
            '192.168.1.9')

    def test_ohne_kopf_bleibt_die_loopback_adresse_stehen(self):
        # Lieber ehrlich 127.0.0.1 als eine erfundene Adresse: dann sieht man
        # in der Liste, dass der Weiterreicher nichts durchgibt.
        self.assertEqual(self._herkunft('127.0.0.1'), '127.0.0.1')

    def test_ohne_gegenueber_leer(self):
        self.assertEqual(self._herkunft(None), '')


class Wartungsstand(unittest.TestCase):
    """Wie viele Aufgaben ueberfaellig oder bald faellig sind.

    Gerechnet wird wie in der Bordansicht (`getWartungStatus`), damit nicht
    zwei Stellen unterschiedlich zaehlen.
    """

    def _stand(self, plan, frist=7):
        import main
        echt = main.read_json
        main.read_json = lambda pfad, vorgabe: (
            plan if 'wartung' in str(pfad) else {'wartung': {'due_soon_days': frist}})
        try:
            return main._wartung_stand()
        finally:
            main.read_json = echt

    def _tage_her(self, n):
        return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()

    def test_nie_erledigt_ist_ueberfaellig(self):
        stand = self._stand([{'tasks': [{'interval_days': 30, 'last_done': None}]}])
        self.assertEqual(stand['ueberfaellig'], 1)
        self.assertEqual(stand['gesamt'], 1)

    def test_ohne_intervall_zaehlt_nirgends_mit(self):
        # interval_days 0 heisst "von Hand gefuehrt" — das hat keine
        # Faelligkeit und darf die Ampel nicht rot faerben.
        stand = self._stand([{'tasks': [{'interval_days': 0, 'last_done': None}]}])
        self.assertEqual((stand['ueberfaellig'], stand['bald'], stand['gesamt']), (0, 0, 0))

    def test_bald_faellig_haengt_an_der_frist(self):
        plan = [{'tasks': [{'interval_days': 30, 'last_done': self._tage_her(25)}]}]
        self.assertEqual(self._stand(plan, frist=7)['bald'], 1)     # in 5 Tagen faellig
        self.assertEqual(self._stand(plan, frist=3)['bald'], 0)     # noch nicht bald

    def test_laengst_vorbei_ist_ueberfaellig(self):
        stand = self._stand([{'tasks': [{'interval_days': 30,
                                         'last_done': self._tage_her(90)}]}])
        self.assertEqual(stand['ueberfaellig'], 1)
        self.assertEqual(stand['bald'], 0)

    def test_kaputtes_datum_wirft_nicht(self):
        # Ein unlesbares Datum darf den ganzen Zustandsversand nicht mitnehmen.
        stand = self._stand([{'tasks': [{'interval_days': 30, 'last_done': 'demnaechst'}]}])
        self.assertEqual((stand['ueberfaellig'], stand['bald']), (0, 0))

    def test_leerer_plan(self):
        stand = self._stand([])
        self.assertEqual((stand['ueberfaellig'], stand['bald'], stand['gesamt']), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
