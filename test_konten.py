"""Tests der Kontenverwaltung und der Zugangsregeln.

Kein Netz, keine Hardware: Kontenspeicher und Regelwerk sind reine Logik.
Aufruf:

    python3 -m unittest test_konten -v

Die Tests prüfen bewusst die Fälle, in denen ein Fehler jemanden aussperrt oder
jemanden hereinlässt — nicht den Normalweg, der ohnehin täglich läuft.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from konten_speicher import Konten
from sync import konten as k
from sync import rechte as r
from sync import zugang as zg


class Basis(unittest.TestCase):
    """Gemeinsamer Aufbau: ein Kontenspeicher in einem Wegwerf-Verzeichnis."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        pfad = Path(self._tmp.name)
        self.konten = Konten(pfad / 'konten.json', pfad / 'sitzungen.json')

    def tearDown(self):
        self._tmp.cleanup()


class Anmeldung(Basis):

    def test_leer_bis_zum_ersten_konto(self):
        self.assertTrue(self.konten.leer)
        self.konten.anlegen('eigner', 'ein gutes Passwort', 'eigner')
        self.assertFalse(self.konten.leer)

    def test_anmelden_und_sitzung(self):
        self.konten.anlegen('eigner', 'ein gutes Passwort', 'eigner')
        token, konto = self.konten.anmelden('eigner', 'ein gutes Passwort')
        self.assertTrue(token)
        self.assertEqual(konto['name'], 'eigner')
        self.assertEqual(self.konten.konto_zu_token(token)['name'], 'eigner')

    def test_falsches_passwort_gibt_keine_sitzung(self):
        self.konten.anlegen('eigner', 'ein gutes Passwort', 'eigner')
        with self.assertRaises(k.KontoFehler):
            self.konten.anmelden('eigner', 'falsch')

    def test_unbekannter_name_meldet_dasselbe_wie_falsches_passwort(self):
        """Sonst verrät die Meldung, welche Namen es gibt."""
        self.konten.anlegen('eigner', 'ein gutes Passwort', 'eigner')
        try:
            self.konten.anmelden('gibtesnicht', 'egal')
            self.fail('hätte scheitern müssen')
        except k.KontoFehler as e:
            unbekannt = str(e)
        try:
            self.konten.anmelden('eigner', 'falsch')
            self.fail('hätte scheitern müssen')
        except k.KontoFehler as e:
            falsch = str(e)
        self.assertEqual(unbekannt, falsch)

    def test_abmelden_macht_die_sitzung_ungueltig(self):
        self.konten.anlegen('eigner', 'ein gutes Passwort', 'eigner')
        token, _ = self.konten.anmelden('eigner', 'ein gutes Passwort')
        self.konten.abmelden(token)
        self.assertIsNone(self.konten.konto_zu_token(token))

    def test_erfundenes_token_gilt_nicht(self):
        self.konten.anlegen('eigner', 'ein gutes Passwort', 'eigner')
        self.assertIsNone(self.konten.konto_zu_token('a' * 43))
        self.assertIsNone(self.konten.konto_zu_token(''))


class Sperren(Basis):
    """Der wichtigste Teil: ein entzogener Zugang muss sofort greifen."""

    def setUp(self):
        super().setUp()
        self.konten.anlegen('eigner', 'ein gutes Passwort', 'eigner')
        self.konten.anlegen('crew', 'auch ein Passwort', 'crew')

    def test_sperren_beendet_laufende_sitzung(self):
        token, _ = self.konten.anmelden('crew', 'auch ein Passwort')
        self.assertIsNotNone(self.konten.konto_zu_token(token))
        self.konten.aendern('crew', gesperrt=True)
        self.assertIsNone(self.konten.konto_zu_token(token),
                          'Eine gesperrte Person darf nicht weiterarbeiten')

    def test_gesperrt_kann_sich_nicht_neu_anmelden(self):
        self.konten.aendern('crew', gesperrt=True)
        with self.assertRaises(k.KontoFehler):
            self.konten.anmelden('crew', 'auch ein Passwort')

    def test_passwortwechsel_beendet_alte_sitzungen(self):
        """Wer sein Passwort ändert, tut das oft, WEIL es abhanden kam."""
        token, _ = self.konten.anmelden('crew', 'auch ein Passwort')
        self.konten.aendern('crew', passwort='ein neues Passwort')
        self.assertIsNone(self.konten.konto_zu_token(token))
        neu, _ = self.konten.anmelden('crew', 'ein neues Passwort')
        self.assertIsNotNone(self.konten.konto_zu_token(neu))

    def test_loeschen_beendet_sitzung(self):
        token, _ = self.konten.anmelden('crew', 'auch ein Passwort')
        self.konten.loeschen('crew')
        self.assertIsNone(self.konten.konto_zu_token(token))

    def test_sperren_beruehrt_andere_konten_nicht(self):
        eigner_token, _ = self.konten.anmelden('eigner', 'ein gutes Passwort')
        self.konten.aendern('crew', gesperrt=True)
        self.assertIsNotNone(self.konten.konto_zu_token(eigner_token))


class Kopie(Basis):
    """Die Kontenkopie zum Pi — der Weg, auf dem ein Entzug an Bord ankommt."""

    def setUp(self):
        super().setUp()
        self.konten.anlegen('eigner', 'ein gutes Passwort', 'eigner')
        self.konten.anlegen('crew', 'auch ein Passwort', 'crew')

    def test_kopie_enthaelt_hashes(self):
        """Ohne Hash könnte an Bord ohne Internet niemand anmelden."""
        v = self.konten.zum_verteilen()
        self.assertEqual(len(v['konten']), 2)
        for konto in v['konten']:
            self.assertTrue(konto['hash'], 'Der Hash muss mit')
            self.assertNotIn('passwort', konto, 'Klartext darf NIE mitgehen')

    def test_stand_aendert_sich_bei_aenderung(self):
        vorher = self.konten.zum_verteilen()['stand']
        self.konten.aendern('crew', gesperrt=True)
        self.assertNotEqual(vorher, self.konten.zum_verteilen()['stand'])

    def test_stand_bleibt_bei_unveraendertem_bestand(self):
        self.assertEqual(self.konten.zum_verteilen()['stand'],
                         self.konten.zum_verteilen()['stand'])

    def test_uebernahme_auf_dem_pi(self):
        """Der Pi übernimmt die Kopie und kann damit anmelden."""
        v = self.konten.zum_verteilen()
        tmp = tempfile.TemporaryDirectory()
        pfad = Path(tmp.name)
        pi = Konten(pfad / 'konten.json', pfad / 'sitzungen.json')
        self.assertTrue(pi.leer)
        pi.ersetzen({konto['name']: konto for konto in v['konten']})
        self.assertFalse(pi.leer)
        token, konto = pi.anmelden('crew', 'auch ein Passwort')
        self.assertEqual(konto['rolle'], 'crew')
        self.assertEqual(pi.zum_verteilen()['stand'], v['stand'],
                         'Beide Seiten müssen denselben Stand errechnen')
        tmp.cleanup()

    def test_entzug_wirkt_auf_dem_pi(self):
        """Der Fall, auf den es ankommt: von Bord verwiesen, WLAN bleibt."""
        tmp = tempfile.TemporaryDirectory()
        pfad = Path(tmp.name)
        pi = Konten(pfad / 'konten.json', pfad / 'sitzungen.json')
        pi.ersetzen({c['name']: c for c in self.konten.zum_verteilen()['konten']})
        token, _ = pi.anmelden('crew', 'auch ein Passwort')
        self.assertIsNotNone(pi.konto_zu_token(token))

        # Auf dem Server gesperrt, Kopie kommt an Bord an
        self.konten.aendern('crew', gesperrt=True)
        pi.ersetzen({c['name']: c for c in self.konten.zum_verteilen()['konten']})
        self.assertIsNone(pi.konto_zu_token(token),
                          'Die Sperre muss die Bordsitzung beenden')
        tmp.cleanup()

    def test_geloeschtes_konto_verliert_bordsitzung(self):
        tmp = tempfile.TemporaryDirectory()
        pfad = Path(tmp.name)
        pi = Konten(pfad / 'konten.json', pfad / 'sitzungen.json')
        pi.ersetzen({c['name']: c for c in self.konten.zum_verteilen()['konten']})
        token, _ = pi.anmelden('crew', 'auch ein Passwort')
        self.konten.loeschen('crew')
        pi.ersetzen({c['name']: c for c in self.konten.zum_verteilen()['konten']})
        self.assertIsNone(pi.konto_zu_token(token))
        tmp.cleanup()


class Rechte(unittest.TestCase):
    """Rollen und Rechte — geprüft an den Fällen, die der Eigner genannt hat."""

    def test_crew_darf_die_diagnose_nicht_oeffnen(self):
        crew = {'name': 'x', 'rolle': 'crew'}
        self.assertTrue(r.darf_oberflaeche(crew, r.PWA))
        self.assertFalse(r.darf_oberflaeche(crew, r.DIAGNOSE),
                         'Ausdrücklicher Eigner-Wunsch vom 03.09.2026')

    def test_crew_darf_schalten_aber_nicht_verwalten(self):
        crew = {'name': 'x', 'rolle': 'crew'}
        self.assertTrue(r.darf(crew, r.SCHALTEN))
        self.assertFalse(r.darf(crew, r.VERWALTEN))
        self.assertFalse(r.darf(crew, r.FERNWARTEN))

    def test_gast_darf_nur_sehen(self):
        gast = {'name': 'x', 'rolle': 'gast'}
        self.assertTrue(r.darf(gast, r.LESEN))
        for verboten in (r.SCHALTEN, r.EINSTELLEN, r.VERWALTEN, r.FERNWARTEN):
            self.assertFalse(r.darf(gast, verboten), verboten)

    def test_gesperrtes_konto_darf_nichts(self):
        tot = {'name': 'x', 'rolle': 'eigner', 'gesperrt': True}
        for handlung in r.HANDLUNGEN:
            self.assertFalse(r.darf(tot, handlung), handlung)
        for flaeche in r.OBERFLAECHEN:
            self.assertFalse(r.darf_oberflaeche(tot, flaeche), flaeche)

    def test_abgelaufener_technikerzugang_darf_nichts(self):
        alt = {'name': 'x', 'rolle': 'techniker', 'abgelaufen': True}
        self.assertFalse(r.darf(alt, r.LESEN))

    def test_einzelrecht_uebersteuert_die_rolle(self):
        """Rollen sind die Vorgabe, kein Korsett."""
        crew_plus = {'name': 'x', 'rolle': 'crew', 'handlungen': ['lesen', 'schalten', 'einstellen']}
        self.assertTrue(r.darf(crew_plus, r.EINSTELLEN))
        crew_minus = {'name': 'y', 'rolle': 'crew', 'handlungen': ['lesen']}
        self.assertFalse(r.darf(crew_minus, r.SCHALTEN))

    def test_kein_konto_darf_nichts(self):
        for handlung in r.HANDLUNGEN:
            self.assertFalse(r.darf(None, handlung), handlung)


class Zugangsregeln(unittest.TestCase):
    """Welcher Aufruf welches Recht verlangt. Die Tabelle ist die Sicherung."""

    def test_lesen_ist_lesen(self):
        self.assertEqual(zg.recht_fuer('GET', '/api/status'), r.LESEN)

    def test_schalten_ist_der_vorgabefall(self):
        self.assertEqual(zg.recht_fuer('POST', '/api/lights/channels'), r.SCHALTEN)
        self.assertEqual(zg.recht_fuer('POST', '/api/heizung/heater'), r.SCHALTEN)

    def test_konten_brauchen_verwalten(self):
        for methode in ('GET', 'POST', 'PATCH', 'DELETE'):
            self.assertEqual(zg.recht_fuer(methode, '/api/konten'), r.VERWALTEN, methode)
        self.assertEqual(zg.recht_fuer('DELETE', '/api/konten/crew'), r.VERWALTEN)

    def test_update_braucht_fernwarten(self):
        self.assertEqual(zg.recht_fuer('POST', '/api/system/update'), r.FERNWARTEN)

    def test_einstellungen_brauchen_einstellen(self):
        self.assertEqual(zg.recht_fuer('POST', '/api/settings'), r.EINSTELLEN)
        self.assertEqual(zg.recht_fuer('POST', '/api/alarms/rules'), r.EINSTELLEN)

    def test_anmeldung_und_oberflaeche_sind_offen(self):
        for pfad in ('/api/login', '/api/zugang', '/api/system/version', '/api/jserror'):
            self.assertIsNone(zg.recht_fuer('POST', pfad), pfad)
        self.assertIsNone(zg.recht_fuer('GET', '/'))
        self.assertIsNone(zg.recht_fuer('GET', '/js-bundle.js'))
        self.assertIsNone(zg.recht_fuer('GET', '/static/css/style.css'))

    def test_zurueckgehen_braucht_fernwartung(self):
        """Ein Eingriff in den laufenden Code, keine Bedienung. Ohne eigene
        Regel fiele er unter die Vorgabe 'Schalten' — die hat jede Crew."""
        self.assertEqual(zg.recht_fuer('POST', '/api/system/zurueck'), r.FERNWARTEN)
        crew = {'name': 'x', 'rolle': 'crew'}
        erlaubt, code, _ = zg.pruefen(crew, 'POST', '/api/system/zurueck', schonfrist=False)
        self.assertFalse(erlaubt)
        self.assertEqual(code, 403)

    def test_unbekannte_methode_faellt_auf_das_strengste_recht(self):
        """Ein neuer Aufruf soll auffallen, indem er abgewiesen wird."""
        self.assertEqual(zg.recht_fuer('TRACE', '/api/irgendwas'), r.VERWALTEN)

    def test_gast_sieht_die_werte_aber_nicht_die_anlage(self):
        """Ein Gast soll den Ladestand sehen — nicht die Einstellungen und
        nicht, wer sonst im Bord-WLAN hängt. Die Geräteübersicht ist eine
        Aussage über die Anwesenheit von Menschen, nicht über das Boot."""
        gast = {'name': 'g', 'rolle': 'gast'}
        for pfad in ('/api/status', '/api/tanks', '/api/heizung', '/api/history'):
            erlaubt, _, _ = zg.pruefen(gast, 'GET', pfad, schonfrist=False)
            self.assertTrue(erlaubt, pfad)
        for pfad in ('/api/settings', '/api/network', '/api/devices',
                     '/api/devices/registry', '/api/pgn/127508/1'):
            erlaubt, code, _ = zg.pruefen(gast, 'GET', pfad, schonfrist=False)
            self.assertFalse(erlaubt, pfad)
            self.assertEqual(code, 403, pfad)

    def test_crew_darf_die_anlage_auch_nicht_einstellen(self):
        crew = {'name': 'c', 'rolle': 'crew'}
        erlaubt, _, _ = zg.pruefen(crew, 'GET', '/api/settings', schonfrist=False)
        self.assertFalse(erlaubt)
        # Bedienen aber schon
        erlaubt, _, _ = zg.pruefen(crew, 'POST', '/api/lights/channels', schonfrist=False)
        self.assertTrue(erlaubt)

    def test_schonfrist_oeffnet_alles(self):
        erlaubt, code, _ = zg.pruefen(None, 'POST', '/api/lights/channels', schonfrist=True)
        self.assertTrue(erlaubt)

    def test_ohne_schonfrist_ist_401_nicht_403(self):
        """Die Oberfläche braucht den Unterschied: anmelden oder abweisen."""
        erlaubt, code, _ = zg.pruefen(None, 'GET', '/api/status', schonfrist=False)
        self.assertFalse(erlaubt)
        self.assertEqual(code, 401)

        gast = {'name': 'x', 'rolle': 'gast'}
        erlaubt, code, _ = zg.pruefen(gast, 'POST', '/api/lights/channels', schonfrist=False)
        self.assertFalse(erlaubt)
        self.assertEqual(code, 403)


class Passwoerter(unittest.TestCase):
    """Das Hashen selbst — mit den Grenzen, die ein Pi Zero setzt."""

    def test_hash_und_pruefung(self):
        h = k.hash_erzeugen('ein Passwort', n_exp=10)     # klein, damit der Test flott ist
        self.assertTrue(k.hash_pruefen('ein Passwort', h))
        self.assertFalse(k.hash_pruefen('anderes', h))

    def test_gleiche_passwoerter_ergeben_verschiedene_hashes(self):
        """Sonst verrät die Datei, wer dasselbe Passwort hat."""
        a = k.hash_erzeugen('gleiches Wort', n_exp=10)
        b = k.hash_erzeugen('gleiches Wort', n_exp=10)
        self.assertNotEqual(a, b)

    def test_kaputter_hash_laesst_niemanden_herein(self):
        for murks in ('', 'x', 'scrypt$kaputt', '$$$$'):
            self.assertFalse(k.hash_pruefen('egal', murks), repr(murks))

    def test_zu_teure_kosten_werden_abgewiesen(self):
        """Ein Pi Zero hat 427 MB. Ein Hash, der 268 MB will, tötet ihn."""
        with self.assertRaises(Exception):
            k.hash_erzeugen('acht Zeichen', n_exp=20)

    def test_schwacher_hash_wird_zur_erneuerung_vorgemerkt(self):
        alt = k.hash_erzeugen('acht Zeichen', n_exp=10)
        self.assertTrue(k.sollte_erneuert_werden(alt))
        heute = k.hash_erzeugen('acht Zeichen')
        self.assertFalse(k.sollte_erneuert_werden(heute))

    def test_sitzungstoken_wird_nur_als_kennung_gespeichert(self):
        """Wer die Sitzungsdatei liest, darf damit nichts anfangen können."""
        klartext, kennung = k.sitzung_erzeugen()
        self.assertNotEqual(klartext, kennung)
        self.assertNotIn(klartext, kennung)
        self.assertTrue(k.sitzung_gleich(klartext, kennung))
        self.assertFalse(k.sitzung_gleich('anderes', kennung))


if __name__ == '__main__':
    unittest.main(verbosity=2)


class Herkunft(unittest.TestCase):
    """Der Schutz des Live-Kanals gegen fremde Seiten.

    Ein Browser schickt beim WebSocket-Handschlag die Cookies mit, und die
    übliche Bremse (SameSite) greift dort nicht verlässlich. Ohne diese Prüfung
    kann jede Webseite, die jemand an Bord aufruft, im Hintergrund eine
    Verbindung zum Boot öffnen — die Anmeldung des Nutzers erledigt das für sie.
    """

    def test_gleiche_herkunft_ist_erlaubt(self):
        self.assertTrue(zg.herkunft_erlaubt(
            'https://mave.circuit-sailor.com', 'mave.circuit-sailor.com'))
        self.assertTrue(zg.herkunft_erlaubt(
            'http://192.168.1.103:8080', '192.168.1.103:8080'))

    def test_port_spielt_keine_rolle(self):
        """Hinter nginx kommt der Kanal auf 443 an, die Seite kam von 8080."""
        self.assertTrue(zg.herkunft_erlaubt(
            'https://pi.mave.circuit-sailor.com', 'pi.mave.circuit-sailor.com:443'))

    def test_fremde_seite_wird_abgewiesen(self):
        for boese in ('https://beispiel.de', 'http://boese.example',
                      'https://mave.circuit-sailor.com.boese.de',
                      'https://xmave.circuit-sailor.com'):
            with self.subTest(boese=boese):
                self.assertFalse(zg.herkunft_erlaubt(boese, 'mave.circuit-sailor.com'))

    def test_ohne_herkunft_erlaubt(self):
        """Kein Origin heißt: kein Browser. Skripte und die Werkstatt-App
        haben keine fremde Seite, die jemanden hereinlegen könnte — und das
        Token muss ohnehin stimmen."""
        self.assertTrue(zg.herkunft_erlaubt('', 'mave.circuit-sailor.com'))

    def test_unsinn_wird_abgewiesen(self):
        for murks in ('kein-url', 'null', 'file://', '://'):
            with self.subTest(murks=murks):
                self.assertFalse(zg.herkunft_erlaubt(murks, 'mave.circuit-sailor.com'))


class Einladungen(Basis):
    """Ein Konto, das auf sein Passwort wartet.

    Der Sinn: ein Passwort, das jemand anders vergeben hat, wandert per
    Nachricht durch die Gegend und wird selten geändert. Wer eingeladen wird,
    setzt es selbst.
    """

    def test_eingeladenes_konto_kann_sich_nicht_anmelden(self):
        """Der wichtigste Fall: bis das Passwort gesetzt ist, kommt niemand
        herein — auch nicht mit einem leeren Passwort."""
        self.konten.einladen('crew', 'crew')
        for versuch in ('', 'irgendwas', None):
            with self.subTest(versuch=versuch):
                with self.assertRaises(k.KontoFehler):
                    self.konten.anmelden('crew', versuch)

    def test_einladung_einloesen_und_anmelden(self):
        token, _ = self.konten.einladen('crew', 'crew')
        self.assertIsNotNone(self.konten.einladung_pruefen('crew', token))
        self.konten.einladung_einloesen('crew', token, 'ein selbst gewähltes')
        sitzung, konto = self.konten.anmelden('crew', 'ein selbst gewähltes')
        self.assertEqual(konto['rolle'], 'crew')

    def test_link_gilt_nur_einmal(self):
        """Ein Link, der zweimal geht, ist ein Link, der weitergegeben werden
        kann."""
        token, _ = self.konten.einladen('crew', 'crew')
        self.konten.einladung_einloesen('crew', token, 'erstes Passwort')
        self.assertIsNone(self.konten.einladung_pruefen('crew', token))
        with self.assertRaises(k.KontoFehler):
            self.konten.einladung_einloesen('crew', token, 'zweites Passwort')
        # Und das erste Passwort gilt weiterhin
        self.assertIsNotNone(self.konten.anmelden('crew', 'erstes Passwort'))

    def test_falscher_token_gilt_nicht(self):
        self.konten.einladen('crew', 'crew')
        for falsch in ('', 'a' * 43, 'x'):
            with self.subTest(falsch=falsch):
                self.assertIsNone(self.konten.einladung_pruefen('crew', falsch))

    def test_abgelaufene_einladung_gilt_nicht(self):
        token, _ = self.konten.einladen('crew', 'crew')
        # Ablauf vorziehen, statt sieben Tage zu warten
        self.konten._konten['crew']['einladung']['bis'] = time.time() - 1
        self.assertIsNone(self.konten.einladung_pruefen('crew', token))
        with self.assertRaises(k.KontoFehler):
            self.konten.einladung_einloesen('crew', token, 'zu spät gekommen')

    def test_token_steht_nicht_im_klartext_in_der_datei(self):
        """Wer die Kontendatei liest, darf damit kein Konto übernehmen können."""
        token, _ = self.konten.einladen('crew', 'crew')
        roh = self.konten._konten_datei.read_text()
        self.assertNotIn(token, roh)

    def test_einladung_faehrt_nicht_mit_ans_boot(self):
        """Eingelöst wird beim Server. Was nicht mitfährt, kann unterwegs auch
        nicht abhandenkommen."""
        self.konten.einladen('crew', 'crew')
        v = self.konten.zum_verteilen()
        for c in v['konten']:
            self.assertNotIn('einladung', c)

    def test_neu_einladen_laesst_das_alte_passwort_gelten(self):
        """Ein Link, der nie ankommt, darf niemanden aussperren — und genau
        das ist der häufigste Grund für eine Neueinladung."""
        self.konten.anlegen('vergesslich', 'Das alte Passwort 1', 'crew')
        token = self.konten.neu_einladen('vergesslich')
        self.assertIsNotNone(self.konten.anmelden('vergesslich', 'Das alte Passwort 1'))
        # Erst das Einlösen ersetzt es
        self.konten.einladung_einloesen('vergesslich', token, 'Das neue Passwort 2')
        with self.assertRaises(k.KontoFehler):
            self.konten.anmelden('vergesslich', 'Das alte Passwort 1')
        self.assertIsNotNone(self.konten.anmelden('vergesslich', 'Das neue Passwort 2'))

    def test_gesperrtes_konto_bekommt_keinen_link(self):
        self.konten.anlegen('gesperrt', 'Ein Passwort 12', 'crew')
        self.konten.aendern('gesperrt', gesperrt=True)
        with self.assertRaises(k.KontoFehler):
            self.konten.neu_einladen('gesperrt')

    def test_schwaches_passwort_wird_beim_einloesen_abgewiesen(self):
        self.konten.einladen('neu', 'crew')
        token = self.konten.neu_einladen('neu')
        with self.assertRaises(k.KontoFehler):
            self.konten.einladung_einloesen('neu', token, 'kurz')

    def test_offene_einladung_ist_in_der_liste_sichtbar(self):
        self.konten.einladen('crew', 'crew')
        (c,) = [x for x in self.konten.liste() if x['name'] == 'crew']
        self.assertTrue(c['eingeladen'])
        self.assertIsNotNone(c['einladung_bis'])


class Befristung(Basis):
    """Befristete Zugänge — der Techniker ist der einzige Fremde im System.

    Der Fehler, den das hier absichert: das Feld hieß an einer Stelle
    `laeuft_ab` und an der anderen `gueltig_bis`. Die Ablaufprüfung las nur das
    eine, gesetzt wurde das andere — ein befristeter Zugang wäre nie abgelaufen.
    """

    def setUp(self):
        super().setUp()
        self.konten.anlegen('techniker', 'ein Werkstattpasswort', 'techniker')

    def test_befristung_wird_gesetzt_und_gelesen(self):
        bis = time.time() + 3600
        self.konten.aendern('techniker', laeuft_ab=bis)
        konto = self.konten._konten['techniker']
        self.assertAlmostEqual(konto['gueltig_bis'], bis, places=1)
        self.assertFalse(k.abgelaufen(konto, time.time()))

    def test_abgelaufener_zugang_kommt_nicht_mehr_herein(self):
        token, _ = self.konten.anmelden('techniker', 'ein Werkstattpasswort')
        self.konten.aendern('techniker', laeuft_ab=time.time() - 1)
        self.assertIsNone(self.konten.konto_zu_token(token),
                          'Eine laufende Sitzung muss mit dem Ablauf enden')
        with self.assertRaises(k.KontoFehler):
            self.konten.anmelden('techniker', 'ein Werkstattpasswort')

    def test_befristung_faehrt_ans_boot_mit(self):
        """Sonst läuft der Zugang an Bord weiter — dort, wo er schalten kann."""
        bis = time.time() + 3600
        self.konten.aendern('techniker', laeuft_ab=bis)
        v = self.konten.zum_verteilen()
        (t,) = [c for c in v['konten'] if c['name'] == 'techniker']
        self.assertAlmostEqual(t['gueltig_bis'], bis, places=1)

    def test_befristung_laesst_sich_wieder_aufheben(self):
        self.konten.aendern('techniker', laeuft_ab=time.time() + 60)
        self.konten.aendern('techniker', laeuft_ab=0)
        self.assertIsNone(self.konten._konten['techniker']['gueltig_bis'])


class Selbstbedienung(Basis):
    """Was jemand mit dem EIGENEN Konto tun darf."""

    def setUp(self):
        super().setUp()
        self.konten.anlegen('crew', 'das alte Passwort', 'crew')

    def test_eigenes_passwort_aendern(self):
        self.konten.passwort_selbst_aendern('crew', 'das alte Passwort', 'das neue Passwort')
        with self.assertRaises(k.KontoFehler):
            self.konten.anmelden('crew', 'das alte Passwort')
        self.assertIsNotNone(self.konten.anmelden('crew', 'das neue Passwort'))

    def test_ohne_das_alte_passwort_geht_es_nicht(self):
        """Eine Sitzung kann auf einem fremden offenen Gerät liegen."""
        with self.assertRaises(k.KontoFehler):
            self.konten.passwort_selbst_aendern('crew', 'geraten', 'das neue Passwort')
        self.assertIsNotNone(self.konten.anmelden('crew', 'das alte Passwort'))

    def test_aendern_beendet_alle_sitzungen(self):
        token, _ = self.konten.anmelden('crew', 'das alte Passwort')
        self.konten.passwort_selbst_aendern('crew', 'das alte Passwort', 'das neue Passwort')
        self.assertIsNone(self.konten.konto_zu_token(token))

    def test_sitzungen_beenden_ohne_passwortwechsel(self):
        a, _ = self.konten.anmelden('crew', 'das alte Passwort')
        b, _ = self.konten.anmelden('crew', 'das alte Passwort')
        self.assertEqual(self.konten.sitzungen_beenden('crew'), 2)
        self.assertIsNone(self.konten.konto_zu_token(a))
        self.assertIsNone(self.konten.konto_zu_token(b))
        # Das Passwort gilt weiter — man muss sich kein neues ausdenken
        self.assertIsNotNone(self.konten.anmelden('crew', 'das alte Passwort'))

    def test_einladung_zuruecknehmen_laesst_das_konto_stehen(self):
        self.konten.einladen('gast', 'gast')
        self.konten.einladung_zuruecknehmen('gast')
        self.assertIn('gast', self.konten._konten)
        self.assertIsNone(self.konten.offene_einladung('gast'))


class SitzungenTeilen(Basis):
    """Die Anlage hat drei Namen. Wer sich unter einem anmeldet, soll unter den
    anderen angemeldet SEIN.

    Der Fehler, den das behebt: In der Bordansicht war der Eigner angemeldet,
    im Logbuch ein Gast — zwei Adressen, zwei Cookie-Speicher, zwei getrennte
    Sitzungslisten. Die Abweisung sah aus wie ein Rechtefehler.
    """

    def setUp(self):
        super().setUp()
        self.konten.anlegen('eigner', 'ein gutes Passwort 1', 'eigner')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pfad = Path(tmp.name)
        self.pi = Konten(pfad / 'konten.json', pfad / 'sitzungen.json')
        self.pi.ersetzen({c['name']: c for c in self.konten.zum_verteilen()['konten']})

    def test_sitzung_vom_server_gilt_auch_an_bord(self):
        token, _ = self.konten.anmelden('eigner', 'ein gutes Passwort 1')
        self.assertIsNone(self.pi.konto_zu_token(token), 'vor dem Abgleich noch nicht')
        v = self.konten.zum_verteilen()
        self.pi.sitzungen_uebernehmen(v['sitzungen'])
        self.assertIsNotNone(self.pi.konto_zu_token(token),
                             'nach dem Abgleich muss dieselbe Sitzung an Bord gelten')

    def test_sitzung_von_bord_gilt_auch_beim_server(self):
        token, _ = self.pi.anmelden('eigner', 'ein gutes Passwort 1')
        kennung = k.sitzung_kennung(token)
        self.konten.sitzungen_uebernehmen({kennung: {'konto': 'eigner',
                                                     'seit': time.time(), 'zuletzt': time.time()}})
        self.assertIsNotNone(self.konten.konto_zu_token(token))

    def test_uebernehmen_verwirft_die_eigenen_nicht(self):
        """An Bord kann sich jemand angemeldet haben, während das Boot ohne
        Internet war. Diese Sitzung darf nicht verschwinden."""
        bord, _ = self.pi.anmelden('eigner', 'ein gutes Passwort 1')
        server, _ = self.konten.anmelden('eigner', 'ein gutes Passwort 1')
        self.pi.sitzungen_uebernehmen(self.konten.zum_verteilen()['sitzungen'])
        self.assertIsNotNone(self.pi.konto_zu_token(bord), 'die eigene bleibt')
        self.assertIsNotNone(self.pi.konto_zu_token(server), 'die fremde kommt dazu')

    def test_sitzung_zu_unbekanntem_konto_wird_verworfen(self):
        """Sonst ließe sich ein gelöschtes Konto über eine alte Sitzung
        weiterbenutzen."""
        dazu = self.pi.sitzungen_uebernehmen({'irgendeine': {'konto': 'gibtesnicht',
                                                            'zuletzt': time.time()}})
        self.assertEqual(dazu, 0)

    def test_das_token_selbst_faehrt_nie_mit(self):
        """Übertragen wird nur die Kennung — wer mitliest, kann damit keine
        Sitzung übernehmen."""
        token, _ = self.konten.anmelden('eigner', 'ein gutes Passwort 1')
        roh = json.dumps(self.konten.zum_verteilen())
        self.assertNotIn(token, roh)
