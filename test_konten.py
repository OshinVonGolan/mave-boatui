"""Tests der Kontenverwaltung und der Zugangsregeln.

Kein Netz, keine Hardware: Kontenspeicher und Regelwerk sind reine Logik.
Aufruf:

    python3 -m unittest test_konten -v

Die Tests prüfen bewusst die Fälle, in denen ein Fehler jemanden aussperrt oder
jemanden hereinlässt — nicht den Normalweg, der ohnehin täglich läuft.
"""
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

    def test_unbekannte_methode_faellt_auf_das_strengste_recht(self):
        """Ein neuer Aufruf soll auffallen, indem er abgewiesen wird."""
        self.assertEqual(zg.recht_fuer('TRACE', '/api/irgendwas'), r.VERWALTEN)

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
