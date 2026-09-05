"""Tests des gemeinsamen Fundaments.

Kein Boot, kein Server, kein Netz: alles hier sind reine Funktionen. Aufruf:

    python3 -m unittest test_sync -v
"""
import unittest
from pathlib import Path

import sync_client

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


class Nachliefern(unittest.IsolatedAsyncioTestCase):
    """Der Verlauf muss FORTLAUFEND hinausgehen, nicht nur beim Verbinden.

    Der Fehler, der das hier nötig macht: `_nachliefern` holte einmal auf und
    kehrte zurück. Solange die Verbindung hielt, ging danach keine einzige
    Verlaufszeile mehr hinaus — an Bord entstand jede Minute eine, beim Server
    kam nichts an. Ein Boot, das eine Woche durchgehend online ist, lieferte
    eine Woche lang keinen Verlauf, und im Logbuch sah es aus, als sei nichts
    gemessen worden.
    """

    def _client(self, vorrat):
        import sync_client

        def holen(ab, grenze):
            return [e for e in vorrat if e['n'] >= ab][:grenze]

        c = sync_client.SyncClient(
            adresse='ws://beispiel/sync', token='x', geraet='pruef', version='0',
            zustand_holen=lambda: {}, verlauf_holen=holen,
            verlauf_stand=lambda: (vorrat[-1]['n'] if vorrat else 0),
            conn_status=lambda: {})
        self.gesendet = []

        async def senden(nachricht):
            self.gesendet.append(nachricht)

        c._senden = senden
        return c

    async def test_holt_auf_und_bleibt_dann_stehen(self):
        """Erst aufholen — und danach NICHT zurückkehren, sondern warten."""
        import asyncio
        import sync_client
        vorrat = [{'n': i, 'ts': 1700000000 + i * 60, 'soc': 50.0} for i in range(1, 6)]
        c = self._client(vorrat)
        # Kurze Ruhe, damit der Test nicht zwanzig Sekunden dauert.
        alt = sync_client._NACHLIEFERN_RUHE_S
        sync_client._NACHLIEFERN_RUHE_S = 0.02
        try:
            aufgabe = asyncio.create_task(c._nachliefern(1))
            await asyncio.sleep(0.1)
            self.assertFalse(aufgabe.done(),
                             'Die Schleife darf nicht zurückkehren — sonst geht '
                             'nach dem Aufholen nie wieder etwas hinaus.')
            self.assertEqual(len(self.gesendet), 1)
            self.assertEqual(len(self.gesendet[0]['daten']), 5)

            # Jetzt entsteht an Bord eine neue Zeile. Sie MUSS von selbst gehen.
            # Etwas mehr Geduld: nach jedem Bündel legt die Schleife bewusst
            # eine halbe Sekunde Pause ein, damit das Nachliefern über
            # Mobilfunk den laufenden Betrieb nicht verdrängt.
            vorrat.append({'n': 6, 'ts': 1700000360, 'soc': 51.0})
            await asyncio.sleep(0.8)
            self.assertEqual(len(self.gesendet), 2,
                             'Eine neue Verlaufszeile muss ohne Neuverbinden hinausgehen.')
            self.assertEqual(self.gesendet[1]['daten'][0]['folge'], 6)
        finally:
            sync_client._NACHLIEFERN_RUHE_S = alt
            aufgabe.cancel()

    async def test_ohne_neues_geht_nichts_hinaus(self):
        """Nachsehen kostet nichts — senden schon. Ohne neue Zeile bleibt die
        Leitung still."""
        import asyncio
        import sync_client
        c = self._client([])
        alt = sync_client._NACHLIEFERN_RUHE_S
        sync_client._NACHLIEFERN_RUHE_S = 0.02
        try:
            aufgabe = asyncio.create_task(c._nachliefern(1))
            await asyncio.sleep(0.15)
            self.assertEqual(self.gesendet, [])
        finally:
            sync_client._NACHLIEFERN_RUHE_S = alt
            aufgabe.cancel()


class Ereignisnummern(unittest.TestCase):
    """Ereignisse brauchen eine Nummer, die es nur einmal gibt.

    Der Server legt sie mit `folge` als Primärschlüssel und INSERT OR IGNORE
    ab. Die Betriebsart-Meldung schickte immer 0 — in der Datenbank stand
    deshalb genau EIN solches Ereignis, jeder weitere Wechsel fiel still weg.
    Ein Fehler ist dabei nie aufgetaucht: INSERT OR IGNORE schweigt.
    """

    def test_nummern_wiederholen_sich_nicht(self):
        import sync_client
        gesehen = {sync_client._ereignis_folge() for _ in range(500)}
        self.assertEqual(len(gesehen), 500)

    def test_nummern_steigen(self):
        import sync_client
        a = sync_client._ereignis_folge()
        b = sync_client._ereignis_folge()
        self.assertGreater(b, a)

    def test_nummer_ist_kein_nullwert(self):
        """Genau der alte Fehler: eine feste 0 für jedes Ereignis."""
        import sync_client
        self.assertGreater(sync_client._ereignis_folge(), 0)


class PushProtokoll(unittest.TestCase):
    """Der Schlüssel fährt im Handschlag mit, das Abo geht vom Boot zum Server.

    Warum überhaupt: senden kann nur der Server (ein Push-Abo zeigt auf den
    Dienst des Browserherstellers). Meldet sich ein Gerät im Bordnetz an,
    landet die Anmeldung aber beim Pi — der muss sie weiterreichen und braucht
    dafür den öffentlichen Schlüssel, den er nicht selbst hat.
    """

    def test_stand_traegt_den_schluessel(self):
        n = p.stand(42, 'abc', 'BGYoHYyB')
        self.assertEqual(n['daten']['push_schluessel'], 'BGYoHYyB')
        self.assertEqual(n['daten']['verlauf_bis'], 42)

    def test_stand_ohne_schluessel_bleibt_gueltig(self):
        """Ohne eingerichtetes Push darf der Handschlag nicht scheitern."""
        n = p.stand(1)
        self.assertEqual(n['daten']['push_schluessel'], '')

    def test_abo_geht_nur_vom_pi_zum_server(self):
        n = p.push_abo({'endpoint': 'https://x/y', 'keys': {}}, 'joshy', 'Tablet')
        self.assertEqual(n['typ'], p.PUSH)
        self.assertEqual(n['daten']['konto'], 'joshy')
        self.assertFalse(n['daten']['abmelden'])
        # Richtung: der Pi schickt, der Server empfaengt.
        p.pruefe(n, vom_pi=True)
        with self.assertRaises(p.ProtokollFehler):
            p.pruefe(n, vom_pi=False)

    def test_abmelden_faehrt_als_gleiche_nachricht(self):
        n = p.push_abo({'endpoint': 'https://x/y'}, 'joshy', abmelden=True)
        self.assertTrue(n['daten']['abmelden'])


class Wiederverbinden(unittest.TestCase):
    """Wie lange der Pi wartet, bevor er es noch einmal versucht.

    Die Schleife ist kurz, die Falle darin war teuer: eine Verbindung endet im
    Betrieb IMMER mit einer Ausnahme (die Gegenseite geht weg, `websockets`
    wirft). Setzte nur ein Ende ohne Ausnahme die Wartezeit zurueck, verdoppelte
    sie sich ueber die Lebensdauer des Dienstes hinweg bis auf fuenf Minuten —
    und nach jedem Neustart des Servers stand im Logbuch minutenlang "Boot nicht
    verbunden", obwohl der Pi da war und nur wartete.

    Geprueft wird die Regel selbst, nicht die Schleife: sie nachzubauen hiesse,
    den Test gegen eine Kopie laufen zu lassen.
    """

    def _warte_danach(self, warte, dauer_s, mit_ausnahme=True):
        """Ein Durchlauf der Regel aus `laufen()`."""
        stand = not mit_ausnahme
        if stand or dauer_s >= sync_client._GUTE_SITZUNG_S:
            warte = sync_client._WARTE_START_S
        return min(warte * 2, sync_client._WARTE_MAX_S)

    def test_eine_stehende_sitzung_setzt_zurueck(self):
        # Acht Minuten gestanden, dann riss der Server sie ab: das ist keine
        # gescheiterte Verbindung.
        self.assertEqual(self._warte_danach(sync_client._WARTE_MAX_S, 480),
                         sync_client._WARTE_START_S * 2)

    def test_sofortiges_scheitern_verdoppelt_weiter(self):
        # Gar keine Verbindung zustande gekommen — dann ist Zurueckhaltung
        # richtig, sonst haemmert der Pi gegen einen toten Server.
        self.assertEqual(self._warte_danach(20, 0.4), 40)

    def test_die_obergrenze_haelt(self):
        self.assertEqual(self._warte_danach(sync_client._WARTE_MAX_S, 0.4),
                         sync_client._WARTE_MAX_S)

    def test_ordentliches_ende_setzt_auch_zurueck(self):
        self.assertEqual(self._warte_danach(200, 0.1, mit_ausnahme=False),
                         sync_client._WARTE_START_S * 2)

    def test_die_schwelle_liegt_unter_einer_ueblichen_sitzung(self):
        # Eine Minute ist erreicht, sobald Handschlag, hallo und der erste
        # Zustand durch sind — auch in der gedrosselten Betriebsart (60 s).
        self.assertLessEqual(sync_client._GUTE_SITZUNG_S, 60)
        self.assertGreater(sync_client._GUTE_SITZUNG_S, sync_client._WARTE_START_S)

    def test_die_regel_steht_wirklich_so_im_code(self):
        # Der Test rechnet die Regel nach; er muss deshalb belegen, dass der
        # Code sie auch anwendet. Sonst prueft er nur sich selbst.
        quelle = Path(sync_client.__file__).read_text()
        self.assertIn('if stand or time.monotonic() - begonnen >= _GUTE_SITZUNG_S:', quelle)
        self.assertIn('warte = _WARTE_START_S', quelle)


if __name__ == '__main__':
    unittest.main()


class Rechte(unittest.TestCase):
    """Zwei Arten von Rechten, getrennt: welche Oberflaeche, welche Handlung."""

    def test_crew_nutzt_die_pwa_aber_nicht_die_diagnose(self):
        # Der Wunsch des Eigners in einem Test.
        from sync import rechte as r
        crew = {'rolle': 'crew'}
        self.assertTrue(r.darf_oberflaeche(crew, r.PWA))
        self.assertFalse(r.darf_oberflaeche(crew, r.DIAGNOSE))
        self.assertTrue(r.darf(crew, r.SCHALTEN))       # bedienen darf sie
        self.assertFalse(r.darf(crew, r.FERNWARTEN))

    def test_oberflaeche_und_handlung_haengen_nicht_zusammen(self):
        from sync import rechte as r
        # Der Kiosk darf einstellen, aber keine Diagnose oeffnen.
        kiosk = {'rolle': 'kiosk'}
        self.assertTrue(r.darf(kiosk, r.EINSTELLEN))
        self.assertFalse(r.darf_oberflaeche(kiosk, r.DIAGNOSE))
        # Und er darf keine Konten verwalten, obwohl er an Bord haengt.
        self.assertFalse(r.darf(kiosk, r.VERWALTEN))

    def test_gesperrt_nimmt_alles(self):
        from sync import rechte as r
        eigner = {'rolle': 'eigner', 'gesperrt': True}
        self.assertFalse(r.darf(eigner, r.LESEN))
        self.assertFalse(r.darf_oberflaeche(eigner, r.PWA))

    def test_abgelaufen_nimmt_alles(self):
        from sync import rechte as r
        # Die Frist prueft der Aufrufer, nicht dieses Paket: es hat keine Uhr,
        # und auf dem Pi ist "welche Zeit ist es" nach einem Stromausfall eine
        # echte Frage.
        techniker = {'rolle': 'techniker', 'abgelaufen': True}
        self.assertFalse(r.darf(techniker, r.LESEN))
        self.assertTrue(r.ROLLEN['techniker']['befristet'])

    def test_unbekannte_rolle_wird_gast_nicht_eigner(self):
        from sync import rechte as r
        # Ein Konto MIT unbrauchbarer Rollenangabe faellt auf Gast zurueck:
        # sehen ja, alles andere nein. Im Zweifel das geringste Recht.
        for unsinn in ({'name': 'x', 'rolle': 'kapitaen'},
                       {'name': 'x', 'rolle': ''},
                       {'name': 'x', 'rolle': None}):
            with self.subTest(unsinn=unsinn):
                self.assertTrue(r.darf(unsinn, r.LESEN))
                self.assertFalse(r.darf(unsinn, r.SCHALTEN))
                self.assertFalse(r.darf_oberflaeche(unsinn, r.DIAGNOSE))

    def test_gar_kein_konto_faellt_nicht_auf_gast(self):
        """Frueher bekam auch `None` Gastrechte — also Lesezugriff.

        Das war der Unterschied zwischen "unbekannte Rolle" und "niemand", und
        er ging verloren, weil beides denselben Weg nahm. Ein Unangemeldeter
        durfte damit lesen, sobald irgendwo eine Pruefung fehlte. Seit
        03.09.2026 heisst kein Konto ausdruecklich kein Recht.
        """
        from sync import rechte as r
        for nichts in (None, {}):
            with self.subTest(nichts=nichts):
                for handlung in r.HANDLUNGEN:
                    self.assertFalse(r.darf(nichts, handlung), handlung)
                for flaeche in r.OBERFLAECHEN:
                    self.assertFalse(r.darf_oberflaeche(nichts, flaeche), flaeche)

    def test_konto_kann_die_rolle_uebersteuern(self):
        from sync import rechte as r
        # Ein Crewmitglied, das auch in die Diagnose darf — ohne dafuer eine
        # neue Rolle erfinden zu muessen.
        besonders = {'rolle': 'crew', 'oberflaechen': ['pwa', 'diagnose']}
        self.assertTrue(r.darf_oberflaeche(besonders, r.DIAGNOSE))
        self.assertFalse(r.darf(besonders, r.FERNWARTEN))   # Handlungen bleiben Crew

        knapp = {'rolle': 'eigner', 'handlungen': ['lesen']}
        self.assertFalse(r.darf(knapp, r.SCHALTEN))
        self.assertTrue(r.darf_oberflaeche(knapp, r.DIAGNOSE))

    def test_uebersicht_taugt_fuer_die_oberflaeche(self):
        from sync import rechte as r
        u = r.uebersicht({'rolle': 'crew'})
        self.assertEqual(u['rolle_name'], 'Crew')
        self.assertEqual(u['oberflaechen'], ['pwa'])
        self.assertIn('schalten', u['handlungen'])
        self.assertNotIn('verwalten', u['handlungen'])


class Passwoerter(unittest.TestCase):
    """Hashing mit Parametern, die zum Pi passen — gemessen, nicht geraten."""

    def test_erzeugen_und_pruefen(self):
        from sync import konten as k
        h = k.hash_erzeugen('einpasswort123')
        self.assertTrue(k.hash_pruefen('einpasswort123', h))
        self.assertFalse(k.hash_pruefen('einpasswort124', h))

    def test_zweimal_dasselbe_passwort_ergibt_zwei_hashes(self):
        from sync import konten as k
        a, b = k.hash_erzeugen('einpasswort123'), k.hash_erzeugen('einpasswort123')
        self.assertNotEqual(a, b)                  # eigenes Salz je Hash
        self.assertTrue(k.hash_pruefen('einpasswort123', a))
        self.assertTrue(k.hash_pruefen('einpasswort123', b))

    def test_parameter_stehen_im_hash(self):
        # Damit sich die Kosten spaeter erhoehen lassen, ohne alte Passwoerter
        # zu entwerten.
        from sync import konten as k
        felder = k.hash_erzeugen('einpasswort123').split('$')
        self.assertEqual(felder[0], 'scrypt')
        self.assertEqual(int(felder[1]), k.SCRYPT_N_EXP)

    def test_alter_hash_bleibt_gueltig_wird_aber_als_schwach_erkannt(self):
        from sync import konten as k
        schwach = k.hash_erzeugen('einpasswort123', n_exp=k.SCRYPT_N_EXP - 2)
        self.assertTrue(k.hash_pruefen('einpasswort123', schwach))
        self.assertTrue(k.sollte_erneuert_werden(schwach))
        self.assertFalse(k.sollte_erneuert_werden(k.hash_erzeugen('einpasswort123')))

    def test_praeparierter_hash_wird_abgelehnt_statt_gerechnet(self):
        # Der eigentliche Schutz: 128*n*r*p waeren hier 268 MB. Auf einem Pi
        # mit 427 MB waere das ein Abschuss, kein Rechenvorgang.
        from sync import konten as k
        import time
        t = time.perf_counter()
        self.assertFalse(k.hash_pruefen('x', 'scrypt$16$16$4$AAAA$AAAA'))
        self.assertLess(time.perf_counter() - t, 0.05)

    def test_kaputter_hash_stoert_den_dienst_nicht(self):
        from sync import konten as k
        for muell in ('', 'quatsch', 'scrypt$1$2$3', 'scrypt$a$b$c$d$e',
                      'bcrypt$13$8$1$AA$BB', None, 42):
            with self.subTest(muell=muell):
                self.assertFalse(k.hash_pruefen('x', muell))

    def test_zu_kurzes_passwort_wird_abgelehnt(self):
        from sync import konten as k
        with self.assertRaises(k.KontoFehler):
            k.hash_erzeugen('kurz')

    def test_sehr_langes_passwort_wird_abgelehnt(self):
        # Ohne Grenze koennte die Laenge selbst zur Last werden.
        from sync import konten as k
        with self.assertRaises(k.KontoFehler):
            k.hash_erzeugen('a' * 2000)


class Sitzungen(unittest.TestCase):

    def test_gespeichert_wird_nur_die_kennung(self):
        # Wer die Kontendatei liest, soll damit keine Sitzung uebernehmen
        # koennen.
        from sync import konten as k
        klartext, kennung = k.sitzung_erzeugen()
        self.assertNotIn(klartext, kennung)
        self.assertTrue(k.sitzung_gleich(klartext, kennung))
        self.assertFalse(k.sitzung_gleich('anderes', kennung))

    def test_jede_sitzung_ist_neu(self):
        from sync import konten as k
        a, _ = k.sitzung_erzeugen()
        b, _ = k.sitzung_erzeugen()
        self.assertNotEqual(a, b)
        self.assertGreater(len(a), 30)


class Befristung(unittest.TestCase):

    def test_ohne_frist_laeuft_nichts_ab(self):
        from sync import konten as k
        self.assertFalse(k.abgelaufen({'gueltig_bis': None}, 1788000000.0))

    def test_abgelaufen_wird_erkannt(self):
        from sync import konten as k
        self.assertTrue(k.abgelaufen({'gueltig_bis': 1787000000.0}, 1788000000.0))
        self.assertFalse(k.abgelaufen({'gueltig_bis': 1789000000.0}, 1788000000.0))

    def test_ohne_verlaessliche_uhr_sperrt_niemand_aus(self):
        # Auf dem Pi ist "welche Zeit ist es" nach einem Stromausfall eine echte
        # Frage. Eine falsche Uhr darf niemanden aussperren, der berechtigt ist.
        from sync import konten as k
        self.assertFalse(k.abgelaufen({'gueltig_bis': 1787000000.0}, None))


class Startmarke(unittest.TestCase):
    """Woran der Pi erkennt, was beim letzten Mal geschah.

    Die Unterscheidung, um die es geht: ein geordneter Neustart (Update) sieht
    von aussen genauso aus wie ein Stromausfall — der Dienst ist weg und kommt
    wieder. Nur die Marke auf der Platte kennt den Unterschied.
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.pfad = __import__('pathlib').Path(self._tmp.name) / 'marke.json'

    def tearDown(self):
        self._tmp.cleanup()

    def _marke(self):
        from sync.startmarke import Startmarke as M
        return M(self.pfad)

    def test_ohne_datei_ist_es_der_erste_start(self):
        b = self._marke().start()
        self.assertEqual(b['letztes_ende'], 'erststart')

    def test_geordnetes_ende_wird_als_sauber_erkannt(self):
        """Frueher loeschte das geordnete Ende die Marke — dann war es von
        "noch nie gelaufen" nicht zu unterscheiden, und jedes Update meldete
        sich als Erstinbetriebnahme."""
        m = self._marke()
        m.start()
        m.geordnet_beenden()
        b = self._marke().start()
        self.assertEqual(b['letztes_ende'], 'sauber')
        self.assertTrue(b['nur_dienst'], 'derselbe Rechner, nur der Dienst war weg')

    def test_liegengebliebene_marke_heisst_abbruch(self):
        m = self._marke()
        m.start()
        # kein geordnet_beenden(): so sieht ein Stromausfall aus
        b = self._marke().start()
        self.assertEqual(b['letztes_ende'], 'abbruch')

    def test_jeder_lauf_bekommt_eine_eigene_kennung(self):
        a = self._marke().start()
        b = self._marke().start()
        self.assertTrue(a['lauf_id'])
        self.assertNotEqual(a['lauf_id'], b['lauf_id'])

    def test_unlesbare_marke_stuerzt_nicht_ab(self):
        self.pfad.write_text('kein JSON {{{')
        b = self._marke().start()
        self.assertEqual(b['letztes_ende'], 'unbekannt')


class Verlaufsumsetzung(unittest.TestCase):
    """Die Umsetzung, die zwischen Bord und Server fehlte.

    An Bord ist ein Verlaufseintrag eine flache Zeile Messwerte; der Server
    erwartet Folge, Zeit und die Messwerte als eigenes Feld. Ohne die
    Umsetzung schickte der Pi seine Rohzeilen, und der Server las darin
    `folge` und `wand` — beides gab es nicht. Es entstand nirgends ein Fehler,
    es kam nur nie etwas an.
    """

    def _um(self, e):
        from sync_client import _verlaufspaket
        return _verlaufspaket(e)

    def test_felder_werden_richtig_umgesetzt(self):
        p = self._um({'ts': 1788470000.0, 'n': 42, 'soc': 96.6, 'voltage': 13.2})
        self.assertEqual(p['folge'], 42)
        self.assertEqual(p['wand'], 1788470000.0)
        self.assertEqual(p['daten'], {'soc': 96.6, 'voltage': 13.2})
        self.assertNotIn('n', p['daten'], 'die Nummer gehört nicht zu den Messwerten')
        self.assertNotIn('ts', p['daten'])

    def test_uhr_ohne_bezug_wird_als_unsicher_gemeldet(self):
        """Ein Pi ohne gepufferte Uhr startet in 1970. Der Server kann solche
        Einträge parken und später einordnen — aber nur, wenn er sie erkennt."""
        p = self._um({'ts': 1200.0, 'n': 5, 'soc': 50})
        self.assertFalse(p['gestellt'])
        p2 = self._um({'ts': 1788470000.0, 'n': 6, 'soc': 50})
        self.assertTrue(p2['gestellt'])

    def test_unbrauchbare_eintraege_fallen_weg(self):
        for murks in ({'ts': 1788470000.0}, {'n': 5}, {'n': 1476.5, 'ts': 1788470000.0},
                      {'n': True, 'ts': 1788470000.0}, 'kein dict', None):
            with self.subTest(murks=murks):
                self.assertIsNone(self._um(murks))
