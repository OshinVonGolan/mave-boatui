"""Tests der Geraete-Aggregation.

Kein CAN, kein Router, kein Pi: alles hier sind reine Funktionen, denen die
Momentaufnahmen von Hand gereicht werden. Aufruf:

    python3 -m unittest test_geraete -v
"""
import unittest

import geraete


def bus(pgn, src, age, *, name='', instance=None, interval=None, hexname=''):
    return {'pgn': pgn, 'src': src, 'instance': instance, 'device_name': name,
            'name_hex': hexname, 'description': f'PGN {pgn}', 'count': 10,
            'interval_ms': interval, 'age_s': age}


class BusIndex(unittest.TestCase):

    def test_fasst_pgns_je_geraet_zusammen(self):
        idx = geraete.n2k_index([
            bus(127508, 1, 1.0, name='VE.Direct Bridge'),
            bus(130900, 1, 4.0),
            bus(127507, 0, 2.0, name='VE.Direct NMEA2K GW'),
        ])
        self.assertEqual(sorted(idx), [0, 1])
        self.assertEqual(len(idx[1]['pgns']), 2)
        self.assertEqual(idx[1]['age_s'], 1.0)          # juengster Frame zaehlt
        self.assertEqual(idx[1]['name'], 'VE.Direct Bridge')

    def test_status_nach_alter(self):
        idx = geraete.n2k_index([bus(1, 1, 2.0), bus(2, 2, 60.0), bus(3, 3, 900.0)])
        self.assertEqual(idx[1]['status'], 'online')
        self.assertEqual(idx[2]['status'], 'traege')
        self.assertEqual(idx[3]['status'], 'offline')

    def test_name_aus_presets_wenn_keine_produktinfo(self):
        idx = geraete.n2k_index([bus(1, 35, 1.0)], {'35': 'B&G Chartplotter'})
        self.assertEqual(idx[35]['name'], 'B&G Chartplotter')


class Zuordnung(unittest.TestCase):

    def test_name_hex_schlaegt_adresse(self):
        # Die Adresse hat sich nach einem Neustart geaendert (0 → 7), das
        # 64-Bit-NAME nicht. Genau dafuer ist es der Schluessel.
        netz = [bus(127507, 7, 1.0, name='VE.Direct NMEA2K GW', hexname='0xAABB')]
        reg = [{'id': 'gw', 'name': 'Gateway', 'kategorie': 'n2k', 'netz': 'n2k-bord',
                'match': {'typ': 'n2k', 'name_hex': '0xaabb', 'src': 0}}]
        g = geraete.aggregiere(reg, netz)['geraete'][0]
        self.assertEqual(g['status'], 'online')
        self.assertEqual(g['live']['zuordnung'], 'name')
        self.assertEqual(g['live']['src'], 7)

    def test_adresse_als_letzter_notnagel(self):
        netz = [bus(127507, 0, 1.0)]
        reg = [{'id': 'gw', 'name': 'Gateway', 'kategorie': 'n2k',
                'match': {'typ': 'n2k', 'src': 0}}]
        g = geraete.aggregiere(reg, netz)['geraete'][0]
        self.assertEqual(g['live']['zuordnung'], 'adresse')

    def test_geraet_hinter_gateway_haengt_an_seiner_pgn(self):
        netz = [bus(130912, 0, 2.0, name='VE.Direct NMEA2K GW', interval=1000),
                bus(127507, 0, 2.0)]
        reg = [
            {'id': 'mppt', 'name': 'MPPT', 'kategorie': 'energie',
             'match': {'typ': 'n2k', 'src': 0, 'pgn': 130912}},
            {'id': 'orion', 'name': 'Orion', 'kategorie': 'energie',
             'match': {'typ': 'n2k', 'src': 0, 'pgn': 130913}},
        ]
        mppt, orion = geraete.aggregiere(reg, netz)['geraete']
        self.assertEqual(mppt['status'], 'online')
        # Das Traegergeraet sendet, diese eine PGN aber nicht — das Geraet
        # dahinter ist weg, nicht das Gateway.
        self.assertEqual(orion['status'], 'offline')
        self.assertEqual(orion['kennzahlen'][0]['l'], 'über')

    def test_bus_leer_heisst_unbekannt_nicht_offline(self):
        reg = [{'id': 'gw', 'name': 'Gateway', 'kategorie': 'n2k',
                'match': {'typ': 'n2k', 'src': 0}}]
        g = geraete.aggregiere(reg, [])['geraete'][0]
        self.assertEqual(g['status'], 'unbekannt')


class Stoker(unittest.TestCase):

    SNAP = {
        'configured': True, 'reachable': True, 'age_s': 1.0, 'error': None,
        'info': {'firmware': '1.0.0', 'uptimeS': 7200,
                 'wifi': {'ip': '192.168.1.48', 'ssid': 'SY_Mave', 'rssi': -62}},
        'state': {'rooms': [
            {'id': 0, 'nodeId': 1, 'name': 'Bugkabine', 'conn': 'online',
             'lastSeenS': 3, 'rssi': -52, 'fault': 'none', 'roomTemp': 19.8},
            {'id': 1, 'nodeId': 2, 'name': 'Salon', 'conn': 'offline',
             'lastSeenS': 800, 'rssi': None, 'fault': 'none', 'roomTemp': None},
        ]},
    }

    def test_hub_und_raeume(self):
        reg = [
            {'id': 'hub', 'name': 'Hub', 'kategorie': 'heizung',
             'match': {'typ': 'stoker', 'rolle': 'hub'}},
            {'id': 'r1', 'name': 'Knoten Bugkabine', 'kategorie': 'heizung',
             'match': {'typ': 'stoker', 'roomName': 'Bugkabine'}},
            {'id': 'r2', 'name': 'Knoten Salon', 'kategorie': 'heizung',
             'match': {'typ': 'stoker', 'roomName': 'Salon'}},
        ]
        hub, r1, r2 = geraete.aggregiere(reg, stoker_snapshot=self.SNAP)['geraete']
        self.assertEqual(hub['status'], 'online')
        self.assertIn({'l': 'IP', 'v': '192.168.1.48'}, hub['kennzahlen'])
        self.assertEqual(r1['status'], 'online')
        self.assertEqual(r2['status'], 'offline')

    def test_ohne_hub_sind_raumknoten_unbekannt(self):
        # Der Hub ist die einzige Quelle ueber die Knoten. Faellt er aus, ist
        # ueber die Knoten nichts bekannt — sie sind nicht automatisch tot.
        snap = dict(self.SNAP, reachable=False)
        reg = [{'id': 'r1', 'name': 'Knoten', 'kategorie': 'heizung',
                'match': {'typ': 'stoker', 'roomName': 'Bugkabine'}}]
        g = geraete.aggregiere(reg, stoker_snapshot=snap)['geraete'][0]
        self.assertEqual(g['status'], 'unbekannt')

    def test_heizung_nicht_eingerichtet(self):
        reg = [{'id': 'hub', 'name': 'Hub', 'kategorie': 'heizung',
                'match': {'typ': 'stoker', 'rolle': 'hub'}}]
        g = geraete.aggregiere(reg, stoker_snapshot={'configured': False})['geraete'][0]
        self.assertEqual(g['status'], 'unbekannt')


class Lan(unittest.TestCase):

    CONN = {'router': {'active_type': 'wired', 'wan_ip': '10.0.0.2',
                       'wifi_client_count': 1,
                       'wifi_clients': [{'hostname': 'stoker-bf38', 'mac': 'AA-BB-CC-DD-EE-FF',
                                         'ip': '192.168.1.48', 'signal': -62,
                                         'band': '2.4', 'ssid': 'SY_Mave'}]}}

    def test_treffer_ueber_mac_unabhaengig_von_schreibweise(self):
        reg = [{'id': 'x', 'name': 'Stoker', 'kategorie': 'netzwerk',
                'match': {'typ': 'lan', 'mac': 'aa:bb:cc:dd:ee:ff'}}]
        g = geraete.aggregiere(reg, conn_status=self.CONN)['geraete'][0]
        self.assertEqual(g['status'], 'online')
        self.assertIn({'l': 'IP', 'v': '192.168.1.48'}, g['kennzahlen'])

    def test_ohne_routerdaten_unbekannt(self):
        reg = [{'id': 'x', 'name': 'Stoker', 'kategorie': 'netzwerk',
                'match': {'typ': 'lan', 'mac': 'aa:bb:cc:dd:ee:ff'}}]
        g = geraete.aggregiere(reg, conn_status={})['geraete'][0]
        self.assertEqual(g['status'], 'unbekannt')

    def test_nicht_gesehen_ist_offline(self):
        reg = [{'id': 'x', 'name': 'Fremd', 'kategorie': 'netzwerk',
                'match': {'typ': 'lan', 'mac': '11:22:33:44:55:66'}}]
        g = geraete.aggregiere(reg, conn_status=self.CONN)['geraete'][0]
        self.assertEqual(g['status'], 'offline')


class OhneMelder(unittest.TestCase):

    def test_zweites_n2k_netz_ist_kein_ausfall(self):
        reg = [{'id': 'itc5', 'name': 'ITC-5', 'kategorie': 'navigation', 'netz': 'n2k-nav'},
               {'id': 'ap', 'name': 'Autopilot', 'kategorie': 'navigation', 'netz': 'seatalk'},
               {'id': 'fl', 'name': 'Feuerlöscher', 'kategorie': 'sicherheit', 'netz': 'keins'}]
        itc, ap, fl = geraete.aggregiere(reg)['geraete']
        self.assertEqual(itc['status'], 'fremdnetz')
        self.assertEqual(ap['status'], 'fremdnetz')
        self.assertEqual(fl['status'], 'stumm')

    def test_kachelfarbe_ignoriert_stumm_und_fremdnetz(self):
        reg = [{'id': 'a', 'name': 'A', 'kategorie': 'navigation', 'netz': 'n2k-nav'},
               {'id': 'b', 'name': 'B', 'kategorie': 'navigation', 'netz': 'keins'}]
        kat = geraete.aggregiere(reg)['kategorien'][0]
        self.assertEqual(kat['status'], 'fremdnetz')
        self.assertEqual(kat['problem'], 0)

        reg.append({'id': 'c', 'name': 'C', 'kategorie': 'navigation',
                    'match': {'typ': 'n2k', 'src': 9}})
        # Das fremde Busgeraet (src 8) landet in seiner eigenen Kachel, nicht
        # in der Navigation — deshalb hier gezielt die Navigationskachel holen.
        kacheln = {k['key']: k for k in geraete.aggregiere(reg, [bus(1, 8, 1.0)])['kategorien']}
        self.assertEqual(kacheln['navigation']['status'], 'offline')
        self.assertEqual(kacheln['navigation']['problem'], 1)
        self.assertEqual(kacheln['n2k']['status'], 'online')


class Unbekannte(unittest.TestCase):

    def test_geraet_am_bus_ohne_eintrag_wird_gezeigt(self):
        erg = geraete.aggregiere([], [bus(0, 4, 2.0)])
        g = erg['geraete'][0]
        self.assertFalse(g['gepflegt'])
        self.assertEqual(g['vorschlag']['src'], 4)
        self.assertEqual(g['kategorie'], 'n2k')

    def test_gepflegtes_geraet_erscheint_nicht_doppelt(self):
        reg = [{'id': 'gw', 'name': 'Gateway', 'kategorie': 'n2k',
                'match': {'typ': 'n2k', 'src': 0}}]
        erg = geraete.aggregiere(reg, [bus(127507, 0, 1.0)])
        self.assertEqual(len(erg['geraete']), 1)

    def test_wlan_client_ohne_eintrag_wird_gezeigt(self):
        erg = geraete.aggregiere([], conn_status=Lan.CONN)
        namen = [g['name'] for g in erg['geraete'] if not g['gepflegt']]
        self.assertIn('stoker-bf38', namen)


class RegistryPruefung(unittest.TestCase):

    def test_gute_registry(self):
        geraete.pruefe_registry([{'id': 'a', 'name': 'A', 'kategorie': 'energie',
                                  'netz': 'lan', 'match': {'typ': 'lan', 'mac': 'x'}}])

    def test_fehler(self):
        faelle = [
            ({}, 'Liste erwartet'),
            ([{'name': 'ohne id'}], 'id fehlt'),
            ([{'id': 'a'}, {'id': 'a'}], 'doppelt'),
            ([{'id': 'a', 'quatsch': 1}], 'unbekanntes Feld'),
            ([{'id': 'a', 'kategorie': 'quark'}], 'gibt es nicht'),
            ([{'id': 'a', 'netz': 'quark'}], 'gibt es nicht'),
            ([{'id': 'a', 'match': {'typ': 'quark'}}], 'gibt es nicht'),
            ([{'id': 'a', 'match': {'typ': 'lan', 'ip': '1.2.3.4'}}], 'kennt'),
        ]
        for daten, teil in faelle:
            with self.subTest(daten=daten):
                with self.assertRaises(geraete.RegistryFehler) as ctx:
                    geraete.pruefe_registry(daten)
                self.assertIn(teil, str(ctx.exception))


class EchteRegistry(unittest.TestCase):
    """Die ausgelieferte Vorlage muss die eigene Pruefung bestehen.

    Geprueft wird devices.example.json — die Datei aus dem Repo. Die laufende
    devices.json steht nur auf dem Geraet und wird dort gepflegt.
    """

    def test_devices_vorlage(self):
        import json
        import pathlib
        pfad = pathlib.Path(__file__).parent / 'devices.example.json'
        daten = json.loads(pfad.read_text(encoding='utf-8'))
        geraete.pruefe_registry(daten)
        erg = geraete.aggregiere(daten)
        self.assertEqual(len(erg['geraete']), len(daten))
        # Ohne jede Live-Quelle darf nur der Pi selbst "online" sein — er
        # beantwortet die Anfrage ja gerade. Alles andere waere geraten.
        online = {g['id'] for g in erg['geraete'] if g['status'] == 'online'}
        self.assertEqual(online, {'pi-mave-control'})
        # Verweise auf Elterngeraete muessen ins Leere zeigen duerfen — aber
        # nicht in dieser Datei.
        ids = {g['id'] for g in daten}
        for g in daten:
            if g.get('verbunden_an'):
                self.assertIn(g['verbunden_an'], ids, g['id'])


if __name__ == '__main__':
    unittest.main()


class KeineDoppelten(unittest.TestCase):
    """Dieselbe Kiste darf nicht zweimal in der Liste stehen."""

    CONN = {'router': {'wifi_client_count': 2, 'wifi_clients': [
        {'hostname': 'stoker-bf38', 'mac': '3c:61:05:bf:38:aa', 'ip': '192.168.1.48',
         'signal': -62, 'band': '2.4', 'ssid': 'SY_Mave'},
        {'hostname': 'mave-control', 'mac': 'b8:27:eb:01:02:03', 'ip': '192.168.1.20',
         'signal': -55, 'band': '2.4', 'ssid': 'SY_Mave'}]}}

    SNAP = {'configured': True, 'reachable': True, 'age_s': 1.0,
            'info': {'firmware': '1.0.0', 'wifi': {'ip': '192.168.1.48', 'rssi': -62}},
            'state': {'rooms': []}}

    def test_ip_des_hubs_erscheint_nicht_zusaetzlich_als_wlan_client(self):
        reg = [{'id': 'hub', 'name': 'Stoker Hub', 'kategorie': 'heizung',
                'match': {'typ': 'stoker', 'rolle': 'hub'}}]
        erg = geraete.aggregiere(reg, stoker_snapshot=self.SNAP, conn_status=self.CONN)
        namen = [g['name'] for g in erg['geraete']]
        self.assertEqual(namen.count('stoker-bf38'), 0)
        self.assertIn('Stoker Hub', namen)

    def test_der_pi_erkennt_sich_am_eigenen_rechnernamen(self):
        erg = geraete.aggregiere([], conn_status=self.CONN, eigener_host='mave-control')
        self.assertNotIn('mave-control', [g['name'] for g in erg['geraete']])
        # ohne den Hinweis waere er ein Fremder im eigenen Netz
        erg = geraete.aggregiere([], conn_status=self.CONN)
        self.assertIn('mave-control', [g['name'] for g in erg['geraete']])


class DhcpLeases(unittest.TestCase):
    """Eine Lease ist etwas anderes als eine bestehende Verbindung."""

    FUNK = {'hostname': 'stoker-bf38', 'mac': 'aa:bb:cc:dd:ee:ff', 'ip': '192.168.1.48',
            'signal': -62, 'band': '2.4', 'ssid': 'SY_Mave'}
    LEASE_FUNK = {'hostname': 'stoker-bf38', 'mac': 'AA:BB:CC:DD:EE:FF',
                  'ip': '192.168.1.48', 'interface': 'lan'}
    LEASE_KABEL = {'hostname': 'plotter', 'mac': '11:22:33:44:55:66',
                   'ip': '192.168.1.60', 'interface': 'lan'}

    def conn(self, funk=True, leases=True):
        return {'router': {
            'wifi_clients': [self.FUNK] if funk else [],
            'wifi_client_count': 1 if funk else 0,
            'dhcp_leases': ([self.LEASE_FUNK, self.LEASE_KABEL] if leases else []),
            'dhcp_ok': leases,
        }}

    def test_nur_lease_heisst_nicht_online(self):
        # Das Kabelgeraet steht in keiner WLAN-Liste — es kann dort gar nicht
        # stehen. "online" waere geraten, "offline" waere falsch.
        reg = [{'id': 'p', 'name': 'Plotter', 'kategorie': 'navigation',
                'match': {'typ': 'lan', 'mac': '11:22:33:44:55:66'}}]
        g = geraete.aggregiere(reg, conn_status=self.conn())['geraete'][0]
        self.assertEqual(g['status'], 'unbekannt')
        self.assertIn({'l': 'IP', 'v': '192.168.1.60'}, g['kennzahlen'])
        self.assertTrue(any('nicht bestätigt' in k['v'] for k in g['kennzahlen']))

    def test_funk_schlaegt_lease(self):
        reg = [{'id': 's', 'name': 'Stoker', 'kategorie': 'netzwerk',
                'match': {'typ': 'lan', 'mac': 'aa:bb:cc:dd:ee:ff'}}]
        g = geraete.aggregiere(reg, conn_status=self.conn())['geraete'][0]
        self.assertEqual(g['status'], 'online')
        self.assertIn({'l': 'WLAN', 'v': '-62 dBm'}, g['kennzahlen'])

    def test_weder_funk_noch_lease_ist_offline(self):
        reg = [{'id': 'x', 'name': 'Fremd', 'kategorie': 'netzwerk',
                'match': {'typ': 'lan', 'mac': '99:99:99:99:99:99'}}]
        g = geraete.aggregiere(reg, conn_status=self.conn())['geraete'][0]
        self.assertEqual(g['status'], 'offline')

    def test_geraet_aus_lease_wird_gefunden_aber_nicht_als_online(self):
        erg = geraete.aggregiere([], conn_status=self.conn())
        nach_name = {g['name']: g for g in erg['geraete']}
        self.assertEqual(nach_name['stoker-bf38']['status'], 'online')
        self.assertEqual(nach_name['plotter']['status'], 'unbekannt')

    def test_kein_doppelter_eintrag_bei_funk_und_lease(self):
        erg = geraete.aggregiere([], conn_status=self.conn())
        namen = [g['name'] for g in erg['geraete']]
        self.assertEqual(namen.count('stoker-bf38'), 1)
        self.assertEqual(erg['quellen']['lan'], {'verfuegbar': True, 'clients': 1, 'adressen': 2})

    def test_leases_allein_reichen_als_quelle(self):
        erg = geraete.aggregiere([], conn_status=self.conn(funk=False))
        self.assertTrue(erg['quellen']['lan']['verfuegbar'])
        self.assertEqual({g['name'] for g in erg['geraete']}, {'stoker-bf38', 'plotter'})
        self.assertTrue(all(g['status'] == 'unbekannt' for g in erg['geraete']))
