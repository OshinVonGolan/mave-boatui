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
