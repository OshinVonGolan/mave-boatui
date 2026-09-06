"""Der Heizungsverlauf im Logbuch — vom Hub, nicht mitgeschrieben.

Die Heizung fuehrt ihren Verlauf selbst: der Stoker-Hub legt ihn minuetlich 24
Stunden, viertelstuendlich 30 Tage, stuendlich 45 Tage und taeglich 13 Monate
ab. Ein zweiter Mitschnitt an Bord koennte davon nichts besser machen, nur
aelter. Was fehlte, war der WEG nach draussen: an den Hub kommt allein das
Bordnetz heran, der Server im Internet nicht.

Geprueft werden die drei Stationen dieses Weges:

    Hub-Antwort  ->  Saetze          (heating.verlaufssaetze)
    Saetze       ->  Leitung         (sync_client._heizung_nachliefern)
    Leitung      ->  Server, Graph   (speicher + /api/verlauf/reihen)

Aufruf:  python3 -m unittest test_heizungsverlauf -v
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import heating
from server.speicher import Speicher

JETZT = 1788000000.0


def antwort(*zeilen, spalten=None):
    """Eine Antwort des Hubs, so wie sie ueber /api/history kommt."""
    return {'columns': spalten or ['t', 'r0.temp', 'r0.target', 'r0.flow', 'r0.fan',
                                   'heater.state', 'heater.power', 'heater.flow', 'flags'],
            'rows': list(zeilen)}


class SaetzeVomHub(unittest.TestCase):
    """Aus `columns` und `rows` werden flache Zeilen aus Zahlen."""

    def _saetze(self, roh, **kw):
        hz = heating.StokerClient(Path(tempfile.mkdtemp()) / 'heizung.json')
        with mock.patch.object(hz, 'verlauf', return_value=roh):
            return hz.verlaufssaetze(JETZT - 3600, JETZT, 'minute', **kw)

    def test_eine_zeile_wird_ein_satz(self):
        (s,) = self._saetze(antwort([JETZT, 19.4, 21.0, 34.2, 60, 2, 75, 48.5, 0]))
        self.assertEqual(s['zeit'], JETZT)
        self.assertEqual(s['daten'], {
            'hz_r0_ist': 19.4, 'hz_r0_soll': 21.0, 'hz_r0_vor': 34.2,
            'hz_r0_luft': 60.0, 'hz_zustand': 2.0, 'hz_leistung': 75.0,
            'hz_vorlauf': 48.5, 'hz_stoerung': 0})

    def test_unsichere_zeit_wird_verworfen(self):
        # Der Hub hat keine gepufferte Uhr und sagt selbst, wenn er die Zeit
        # nicht kennt (Bit 0x01). Ein solcher Satz laege irgendwo auf der
        # Zeitachse — und das ist schlimmer als eine Luecke.
        self.assertEqual(self._saetze(antwort([JETZT, 19.4, 21.0, 34.2, 60, 2, 75, 48.5, 1])), [])
        self.assertEqual(self._saetze(antwort([0, 19.4, 21.0, 34.2, 60, 2, 75, 48.5, 0])), [])

    def test_stoerung_wird_gemerkt(self):
        # Bit 0x08: im Zeitraum lag eine Stoerung an. In einem verdichteten
        # Satz ist das etwas anderes als der Zustand am Ende des Zeitraums.
        (s,) = self._saetze(antwort([JETZT, 19.4, 21.0, 34.2, 60, 2, 75, 48.5, 0x08]))
        self.assertEqual(s['daten']['hz_stoerung'], 1)

    def test_leere_raumplaetze_fallen_weg(self):
        # Der Hub fuehrt zehn Raumplaetze, ob es sie gibt oder nicht. Die
        # Geblaesedrehzahl eines nicht vorhandenen Raums ist eine ECHTE Null
        # und saehe in jeder Kurve aus wie eine Messung.
        (s,) = self._saetze(antwort([JETZT, None, None, None, 0, 0, 0, None, 0]))
        self.assertNotIn('hz_r0_luft', s['daten'])
        self.assertNotIn('hz_r0_ist', s['daten'])

    def test_null_heisst_unbekannt_und_nicht_null_grad(self):
        (s,) = self._saetze(antwort([JETZT, 19.4, None, None, 40, 0, 0, None, 0]))
        self.assertEqual(s['daten']['hz_r0_ist'], 19.4)
        for feld in ('hz_r0_soll', 'hz_r0_vor', 'hz_vorlauf'):
            self.assertNotIn(feld, s['daten'])

    def test_mehrere_raeume_bleiben_getrennt(self):
        spalten = ['t', 'r0.temp', 'r0.target', 'r0.flow', 'r0.fan',
                   'r1.temp', 'r1.target', 'r1.flow', 'r1.fan',
                   'heater.state', 'heater.power', 'heater.flow', 'flags']
        (s,) = self._saetze(antwort(
            [JETZT, 19.4, 21.0, 34.0, 60, 17.1, 18.0, 30.0, 20, 2, 75, 48.5, 0],
            spalten=spalten))
        self.assertEqual(s['daten']['hz_r0_ist'], 19.4)
        self.assertEqual(s['daten']['hz_r1_ist'], 17.1)

    def test_kaputte_antwort_wirft_nicht(self):
        # Der Hub ist Beiwerk. Antwortet er Unsinn, darf davon nichts anderes
        # stehenbleiben.
        for kaputt in (None, {}, {'columns': [], 'rows': None},
                       {'columns': ['t'], 'rows': [[1, 2, 3]]},
                       {'columns': ['t'], 'rows': ['keine Liste']}):
            self.assertEqual(self._saetze(kaputt), [])


class Aufloesung(unittest.TestCase):
    """Welche Ebene des Hubs eine Luecke ueberhaupt noch enthaelt.

    Sie folgt dem ALTER und nicht der Laenge: wer eine drei Tage alte Luecke
    minuetlich anfragt, bekommt nichts — nicht weil nichts da waere, sondern
    weil er in der falschen Ebene sucht.
    """

    def test_frisch_ist_minuetlich(self):
        self.assertEqual(heating.StokerClient.aufloesung_fuer(600), 'minute')

    def test_aelter_wird_grober(self):
        self.assertEqual(heating.StokerClient.aufloesung_fuer(3 * 86400), 'quarter')
        self.assertEqual(heating.StokerClient.aufloesung_fuer(40 * 86400), 'hour')
        self.assertEqual(heating.StokerClient.aufloesung_fuer(300 * 86400), 'day')

    def test_jenseits_von_allem_bleibt_die_tagesebene(self):
        self.assertEqual(heating.StokerClient.aufloesung_fuer(10 * 365 * 86400), 'day')

    def test_ein_abruf_bleibt_ein_haeppchen(self):
        # Hinter dem Hub sitzt ein ESP32 mit EINEM Kern fuer alles. Ein
        # nachgeholter Monat darf nicht in einer einzigen Antwort kommen —
        # er schickte sie, waehrend er regelt.
        hz = heating.StokerClient(Path(tempfile.mkdtemp()) / 'heizung.json')
        with mock.patch.object(hz, 'verlaufssaetze', return_value=[]) as gefragt:
            hz.verlauf_nachschub(JETZT - 20 * 86400, JETZT)
            von, bis, aufloesung = gefragt.call_args[0]
            self.assertEqual(aufloesung, 'quarter')
            self.assertAlmostEqual(bis - von, 5 * 86400, delta=1)

    def test_kurze_luecken_werden_am_stueck_geholt(self):
        hz = heating.StokerClient(Path(tempfile.mkdtemp()) / 'heizung.json')
        with mock.patch.object(hz, 'verlaufssaetze', return_value=[]) as gefragt:
            hz.verlauf_nachschub(JETZT - 600, JETZT)
            von, bis, aufloesung = gefragt.call_args[0]
            self.assertEqual(aufloesung, 'minute')
            self.assertAlmostEqual(bis - von, 600, delta=1)


class ServerSpeicher(unittest.TestCase):
    """Der Schluessel ist die ZEIT des Hubs und keine Folgenummer.

    Die Saetze entstehen dort und nicht hier. Derselbe Zeitraum darf deshalb
    zweimal ankommen, ohne sich zu verdoppeln — nach einem Verbindungsabriss
    ist genau das der Normalfall.
    """

    def setUp(self):
        self.s = Speicher(Path(tempfile.mkdtemp()) / 'mave.db')

    def tearDown(self):
        self.s.schliessen()

    def _satz(self, t, ist=19.0):
        return {'zeit': JETZT + t, 'daten': {'hz_r0_ist': ist}}

    def test_ohne_saetze_ist_der_stand_null(self):
        self.assertEqual(self.s.heizung_stand(), 0.0)

    def test_stand_ist_der_juengste_satz(self):
        self.s.heizung_anhaengen([self._satz(0), self._satz(120)], 'minute')
        self.assertEqual(self.s.heizung_stand(), JETZT + 120)

    def test_dasselbe_zweimal_verdoppelt_nichts(self):
        self.s.heizung_anhaengen([self._satz(0)], 'minute')
        self.s.heizung_anhaengen([self._satz(0, ist=20.0)], 'minute')
        self.assertEqual(len(self.s.heizung()), 1)

    def test_unbrauchbares_faellt_weg(self):
        self.s.heizung_anhaengen([
            {'zeit': None, 'daten': {'hz_r0_ist': 1}},
            {'zeit': 0, 'daten': {'hz_r0_ist': 1}},
            {'zeit': JETZT, 'daten': {}},
            {'zeit': JETZT, 'daten': 'kein Objekt'},
        ], 'minute')
        self.assertEqual(self.s.heizung(), [])

    def test_zeitraum_wird_beachtet(self):
        for i in range(5):
            self.s.heizung_anhaengen([self._satz(i * 60)], 'minute')
        gefunden = self.s.heizung(seit=JETZT + 60, bis=JETZT + 180)
        self.assertEqual([g['zeit'] for g in gefunden],
                         [JETZT + 60, JETZT + 120, JETZT + 180])


class ReihenMischen(unittest.TestCase):
    """Heizung und Boot liegen auf DERSELBEN Zeitachse.

    Sie kommen aus zwei Quellen und auf zwei Takten — fuer die Anzeige ist das
    gleichgueltig: eingedampft wird nach Zeit. Erst dadurch laesst sich sehen,
    dass der Verbrauch stieg, WAEHREND die Heizung lief. Genau dafuer sieht man
    in ein Logbuch.
    """

    def setUp(self):
        import server.app as app
        self.app = app
        self.s = Speicher(Path(tempfile.mkdtemp()) / 'mave.db')
        self.vorher = app.speicher
        app.speicher = self.s

    def tearDown(self):
        self.app.speicher = self.vorher
        self.s.schliessen()

    def _reihen(self, von, bis, punkte=10):
        antwort = self.app.verlauf_reihen(von=von, bis=bis, punkte=punkte, k={})
        import json
        return json.loads(antwort.body)

    def test_beide_quellen_stehen_in_denselben_punkten(self):
        self.s.verlauf_anhaengen([{'folge': 1, 'zeit': JETZT + 30,
                                   'daten': {'soc': 82.0}}])
        self.s.heizung_anhaengen([{'zeit': JETZT + 40,
                                   'daten': {'hz_r0_ist': 19.4}}], 'minute')
        d = self._reihen(JETZT, JETZT + 60, punkte=1)
        self.assertEqual(sorted(d['felder']), ['hz_r0_ist', 'soc'])
        self.assertEqual(d['punkte'][0]['hz_r0_ist'][0], 19.4)
        self.assertEqual(d['punkte'][0]['soc'][0], 82.0)
        self.assertEqual(d['roh_anzahl'], 2)

    def test_heizung_allein_reicht_fuer_eine_kurve(self):
        # Nach einem Ausfall des Pi kann der Hub Saetze fuer einen Zeitraum
        # haben, fuer den es sonst nichts gibt. Ein 'nichts vorhanden' waere
        # dann falsch.
        self.s.heizung_anhaengen([{'zeit': JETZT + 10,
                                   'daten': {'hz_zustand': 2}}], 'quarter')
        d = self._reihen(JETZT, JETZT + 60)
        self.assertEqual(d['felder'], ['hz_zustand'])

    def test_ohne_alles_bleibt_es_leer(self):
        d = self._reihen(JETZT, JETZT + 60)
        self.assertEqual(d['punkte'], [])
        self.assertEqual(d['heizung_raeume'], {})

    def test_raumnamen_kommen_mit(self):
        self.app._heizung({'daten': {'saetze': [{'zeit': JETZT, 'daten': {'hz_r0_ist': 19.0}}],
                                     'aufloesung': 'minute',
                                     'raeume': {'0': 'Salon'}}})
        d = self._reihen(JETZT - 60, JETZT + 60)
        self.assertEqual(d['heizung_raeume'], {'0': 'Salon'})
        # Ohne die Namen staende im Logbuch 'Raum 0' — richtig, aber nutzlos.
        self.assertEqual(self.s.heizung_stand(), JETZT)


class ClientSchleife(unittest.IsolatedAsyncioTestCase):
    """Die Strecke Hub -> Server, und was sie NICHT tut.

    Wo angesetzt wird, sagt der Server. Der Pi fuehrt dafuer keinen eigenen
    Merker — genau deshalb schliesst sich eine Luecke von selbst: war er zwei
    Tage aus, nennt der Server seinen alten Stand, und der Hub hat die zwei
    Tage noch.
    """

    def _client(self, saetze_je_aufruf):
        import sync_client
        self.aufrufe = []

        def holen(von, bis):
            self.aufrufe.append((von, bis))
            return saetze_je_aufruf.pop(0) if saetze_je_aufruf else {}

        c = sync_client.SyncClient(
            adresse='ws://beispiel/sync', token='x', geraet='pruef', version='0',
            zustand_holen=lambda: {}, verlauf_holen=lambda ab, g: [],
            verlauf_stand=lambda: 0, conn_status=lambda: {},
            heizung_saetze=holen, heizung_raeume=lambda: {'0': 'Salon'})
        self.gesendet = []

        async def senden(nachricht):
            self.gesendet.append(nachricht)

        c._senden = senden
        return c

    async def _laufen(self, c, ab, dauer=0.15):
        import asyncio
        import sync_client
        alt = sync_client._HEIZUNG_RUHE_S
        sync_client._HEIZUNG_RUHE_S = 0.02
        aufgabe = asyncio.create_task(c._heizung_nachliefern(ab))
        try:
            await asyncio.sleep(dauer)
        finally:
            sync_client._HEIZUNG_RUHE_S = alt
            aufgabe.cancel()
        return aufgabe

    async def test_saetze_gehen_hinaus_und_die_namen_mit(self):
        c = self._client([{'aufloesung': 'minute',
                           'saetze': [{'zeit': JETZT, 'daten': {'hz_r0_ist': 19.0}}]}])
        await self._laufen(c, JETZT - 60)
        self.assertEqual(len(self.gesendet), 1)
        d = self.gesendet[0]['daten']
        self.assertEqual(self.gesendet[0]['typ'], 'heizung')
        self.assertEqual(d['aufloesung'], 'minute')
        self.assertEqual(d['raeume'], {'0': 'Salon'})
        self.assertEqual(d['saetze'][0]['zeit'], JETZT)

    async def test_ohne_neue_saetze_bleibt_die_leitung_still(self):
        c = self._client([])
        await self._laufen(c, JETZT - 60)
        self.assertEqual(self.gesendet, [])

    async def test_es_wird_ab_dem_stand_des_servers_geholt(self):
        c = self._client([])
        await self._laufen(c, JETZT - 3600, dauer=0.05)
        self.assertEqual(round(self.aufrufe[0][0]), round(JETZT - 3600 + 1))

    async def test_ohne_stand_wird_nicht_die_ganze_ablage_geleert(self):
        # Der erste Lauf holt einen Tag — die Minutenebene des Hubs. Alles auf
        # einmal zu holen hiesse, ueber Mobilfunk 13 Monate zu ziehen.
        c = self._client([])
        await self._laufen(c, 0, dauer=0.05)
        von, bis = self.aufrufe[0]
        self.assertAlmostEqual(bis - von, 24 * 3600, delta=2)

    async def test_ohne_heizung_laeuft_die_schleife_gar_nicht(self):
        import sync_client
        c = sync_client.SyncClient(
            adresse='ws://beispiel/sync', token='x', geraet='pruef', version='0',
            zustand_holen=lambda: {}, verlauf_holen=lambda ab, g: [],
            verlauf_stand=lambda: 0, conn_status=lambda: {})
        self.gesendet = []
        c._senden = lambda n: self.gesendet.append(n)
        await c._heizung_nachliefern(0)          # kehrt sofort zurueck
        self.assertEqual(self.gesendet, [])

    async def test_derselbe_satz_noch_einmal_geht_nicht_hinaus(self):
        # Der Hub rundet `from` womoeglich auf den Beginn seines Rasters ab und
        # liefert denselben Satz erneut. Ohne Bremse liefe die Schleife heiss
        # und schickte im Sekundentakt dasselbe ueber Mobilfunk.
        satz = {'aufloesung': 'minute', 'saetze': [{'zeit': JETZT, 'daten': {'hz_r0_ist': 19.0}}]}
        c = self._client([dict(satz), dict(satz), dict(satz)])
        await self._laufen(c, JETZT)
        self.assertEqual(self.gesendet, [])

    async def test_ein_stummer_hub_haelt_nichts_auf(self):
        # Ist der Hub aus, wirft der Griff. Die Verbindung zum Server darf
        # davon nichts merken.
        import sync_client
        c = self._client([])

        def krachen(von, bis):
            raise OSError('Hub antwortet nicht')

        c._heizung_saetze = krachen
        aufgabe = await self._laufen(c, JETZT - 60)
        self.assertEqual(self.gesendet, [])
        self.assertTrue(aufgabe.cancelled() or not aufgabe.done())


if __name__ == '__main__':
    unittest.main()
