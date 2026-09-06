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


class Zellspannungen(unittest.TestCase):
    """Die einzelnen Zellen im Verlauf.

    Die Zelldifferenz sagt, WIE WEIT die Zellen auseinanderliegen. Erst diese
    Reihen sagen, WELCHE wegläuft und seit wann.
    """

    def _entry(self, bms):
        """Genau der Abschnitt aus `broadcast`, der die Zellen herausschreibt."""
        import main
        raus = {}
        for nr, zelle in enumerate((bms.get('cells') or [])[:main._ZELLEN_MAX], start=1):
            v = zelle.get('voltage') if isinstance(zelle, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                raus[f'zelle{nr}'] = round(v, 3)
        return raus

    def test_vier_zellen_wie_an_bord(self):
        # Die Werte stammen aus der Serverkopie des laufenden Bootes.
        w = self._entry({'cell_count': 4, 'cells': [
            {'voltage': 3.275, 'temp': 18.2}, {'voltage': 3.275, 'temp': 16.5},
            {'voltage': 3.275, 'temp': 18.2}, {'voltage': 3.25, 'temp': 17.3}]})
        self.assertEqual(w, {'zelle1': 3.275, 'zelle2': 3.275,
                             'zelle3': 3.275, 'zelle4': 3.25})

    def test_millivolt_bleiben_erhalten(self):
        # Auf zwei Stellen gerundet waeren 3,275 und 3,25 beide 3,28 bzw 3,25 —
        # und eine Abweichung von 25 mV waere nicht mehr zu sehen. Genau darum
        # geht es hier aber.
        w = self._entry({'cells': [{'voltage': 3.2751}, {'voltage': 3.2499}]})
        self.assertEqual(w, {'zelle1': 3.275, 'zelle2': 3.25})

    def test_ohne_bms_nichts(self):
        for leer in ({}, {'cells': None}, {'cells': []}):
            self.assertEqual(self._entry(leer), {})

    def test_einzelne_zelle_ohne_wert_faellt_weg(self):
        # Das BMS meldet 0xFFFF als "kein Wert"; der Parser macht daraus None.
        # Eine Null im Verlauf waere hier eine erfundene Messung.
        w = self._entry({'cells': [{'voltage': 3.3}, {'voltage': None},
                                   {'voltage': 3.29}]})
        self.assertEqual(w, {'zelle1': 3.3, 'zelle3': 3.29})

    def test_sechzehn_zellen_gehen_auch(self):
        w = self._entry({'cells': [{'voltage': 3.3} for _ in range(16)]})
        self.assertEqual(len(w), 16)
        self.assertIn('zelle16', w)

    def test_mehr_als_sechzehn_werden_gedeckelt(self):
        # Ein BMS mit kaputtem Zaehler soll nicht hunderte Felder je Zeile
        # erzeugen — der Verlauf geht ueber Mobilfunk.
        w = self._entry({'cells': [{'voltage': 3.3} for _ in range(400)]})
        self.assertEqual(len(w), 16)


class RouterWerte(unittest.TestCase):
    """Die Zahlen, mit denen sich der Router-Absturz aufklaeren laesst.

    Der Sinn der Auswahl ist, dass jede eine Erklaerung von einer anderen
    TRENNT. Die Tests pruefen deshalb auch, dass nichts Ueberfluessiges
    mitkommt — eine Datenhalde beantwortet am Ende gar nichts.
    """

    def setUp(self):
        import main
        self.main = main
        main._rt_letzte_lauf = None      # der Neustart-Vergleich ist zustandsbehaftet

    def _werte(self, netz):
        return self.main._router_werte(netz)

    def _voll(self, lauf=2589, **abweichung):
        """Der Status SO, wie ihn `_loop` zusammensetzt — mit Ebene und Zeitstempel.

        Die erste Fassung dieser Tests baute ihn ohne die Ebene 'router', weil
        `_fetch_router()` den inneren Teil zurueckgibt. Damit bestanden sie,
        waehrend im Verlauf nichts ankam: der Test glaubte demselben Irrtum wie
        der Code. Ein Testaufbau, der die Form der echten Daten nicht
        nachstellt, prueft nur die eigene Annahme.
        """
        import time
        innen = {'gesundheit': {'uptime_s': lauf, 'ram_prozent': 55.31,
                                'cpu_prozent': 18.9},
                 'radios': [{'band': '2.4GHz', 'up': True, 'kanal': 11},
                            {'band': '5GHz', 'up': True, 'kanal': 157}]}
        innen.update(abweichung)
        return {'router': innen, 'starlink': {}, 'ts': time.time()}

    def test_die_sechs_und_nichts_sonst(self):
        self.assertEqual(set(self._werte(self._voll())),
                         {'rt_an', 'rt_neu', 'rt_ram', 'rt_cpu', 'wl24', 'wl5'})

    def test_ein_neustart_wird_erkannt(self):
        # Eine Laufzeit kann nur steigen. Faellt sie, lag ein Neustart dazwischen.
        self.assertEqual(self._werte(self._voll(lauf=3000))['rt_neu'], 0)
        self.assertEqual(self._werte(self._voll(lauf=3020))['rt_neu'], 0)
        self.assertEqual(self._werte(self._voll(lauf=12))['rt_neu'], 1)
        # Und danach ist wieder Ruhe, nicht dauerhaft Alarm.
        self.assertEqual(self._werte(self._voll(lauf=32))['rt_neu'], 0)

    def test_der_erste_wert_ist_kein_neustart(self):
        # Ohne Vorgaenger laesst sich nichts vergleichen — dann eine Eins zu
        # schreiben hiesse, beim Start des Bordrechners einen Router-Neustart
        # zu erfinden.
        self.assertEqual(self._werte(self._voll(lauf=99))['rt_neu'], 0)

    def test_alter_stand_gilt_nicht_als_messung(self):
        # Der Kern: der Poller BEHAELT seinen letzten Stand, wenn eine Abfrage
        # scheitert. Ohne Frischepruefung wuerde ausgerechnet waehrend eines
        # Ausfalls "alles gut" weitergeschrieben.
        import time
        alt = self._voll()
        alt['ts'] = time.time() - 300
        self.assertEqual(self._werte(alt), {'rt_an': 0})

    def test_ohne_zeitstempel_kein_vertrauen(self):
        for kaputt in (None, {}, {'router': {'gesundheit': {'uptime_s': 5}}},
                       {'ts': 'gestern'}, {'ts': True}):
            self.assertEqual(self._werte(kaputt), {'rt_an': 0})

    def test_nach_einem_ausfall_kein_falscher_neustart(self):
        # Waehrend des Ausfalls wird der Vergleichswert verworfen. Sonst
        # meldete der erste Wert danach einen Neustart, den es nicht gab — der
        # Router kann in der Zwischenzeit ja durchgelaufen sein.
        import time
        self._werte(self._voll(lauf=5000))
        weg = self._voll(); weg['ts'] = time.time() - 300
        self._werte(weg)
        self.assertEqual(self._werte(self._voll(lauf=5300))['rt_neu'], 0)

    def test_baender_getrennt(self):
        # Der entscheidende Fall: 2,4 GHz weg, 5 GHz laeuft weiter.
        w = self._werte(self._voll(radios=[{'band': '2.4GHz', 'up': False},
                                           {'band': '5GHz', 'up': True}]))
        self.assertEqual((w['wl24'], w['wl5']), (0, 1))

    def test_teilausfall_liefert_was_da_ist(self):
        w = self._werte(self._voll(gesundheit=None,
                                   radios=[{'band': '2.4GHz', 'up': False}]))
        self.assertEqual(w, {'rt_an': 1, 'wl24': 0})

    def test_unfug_wird_nicht_uebernommen(self):
        # Der Router liefert seine Zahlen als Zeichenketten, wenn ihm danach
        # ist. Eine Zeichenkette zaehlt im Reihen-Endpunkt nicht als Messwert —
        # dann lieber gar nicht erst hineinschreiben.
        w = self._werte(self._voll(gesundheit={'uptime_s': 'viel',
                                               'ram_prozent': None,
                                               'cpu_prozent': True}))
        self.assertEqual(w, {'rt_an': 1, 'wl24': 1, 'wl5': 1})

    def test_unbekanntes_band_wird_uebergangen(self):
        w = self._werte(self._voll(gesundheit=None,
                                   radios=[{'band': '6GHz', 'up': True}]))
        self.assertEqual(w, {'rt_an': 1})


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
