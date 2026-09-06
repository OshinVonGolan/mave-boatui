"""Der KI-Zugang im Logbuch — EIN Zugang für alle Funktionen.

Er läuft über das Abo des Eigners. Geprüft wird ohne Netz: der einzige Weg
nach draußen ist `_post`, und genau die Stelle wird ersetzt.

Aufruf:

    ./venv/bin/python -m unittest test_ki -v
"""
import asyncio
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

os.environ.setdefault('MAVE_PASSWORT', 'x' * 24)
os.environ.setdefault('MAVE_GERAET_TOKEN', 'y' * 24)

from server import ki as kimodul                 # noqa: E402


def antwort(daten, code=200):
    return (code, json.dumps(daten))


class Anmeldung(unittest.TestCase):

    def setUp(self):
        self.verz = tempfile.mkdtemp(prefix='mave-ki-')
        self.ki = kimodul.Ki(Path(self.verz) / 'ki.json')

    def tearDown(self):
        shutil.rmtree(self.verz, ignore_errors=True)

    def test_die_adresse_traegt_alles_was_claude_ai_verlangt(self):
        """`code=true` fehlte einmal, und claude.ai antwortete nur mit
        „Invalid request format" — daran sucht man lange."""
        u = urlparse(self.ki.anmeldung_starten()['url'])
        q = parse_qs(u.query)
        self.assertEqual(u.netloc, 'claude.ai')
        self.assertEqual(q['code'], ['true'])
        self.assertEqual(q['response_type'], ['code'])
        self.assertEqual(q['code_challenge_method'], ['S256'])
        self.assertTrue(q['code_challenge'][0])
        self.assertTrue(q['state'][0])
        self.assertIn('user:inference', q['scope'][0])

    def test_jede_anmeldung_bekommt_einen_neuen_pruefwert(self):
        a = parse_qs(urlparse(self.ki.anmeldung_starten()['url']).query)
        b = parse_qs(urlparse(self.ki.anmeldung_starten()['url']).query)
        self.assertNotEqual(a['code_challenge'], b['code_challenge'])
        self.assertNotEqual(a['state'], b['state'])

    def test_ohne_start_kein_abschluss(self):
        with self.assertRaises(kimodul.KiFehler):
            asyncio.run(self.ki.anmeldung_abschliessen('abc#def'))

    def test_leerer_code(self):
        self.ki.anmeldung_starten()
        with self.assertRaises(kimodul.KiFehler):
            asyncio.run(self.ki.anmeldung_abschliessen('   '))

    def test_der_code_wird_an_seinem_gatter_geteilt(self):
        """Claude zeigt ihn als `code#state`. Beides muss getrennt mitgehen."""
        self.ki.anmeldung_starten()
        with mock.patch.object(kimodul, '_post',
                               return_value=antwort({'access_token': 'a', 'refresh_token': 'r',
                                                     'expires_in': 3600})) as post:
            asyncio.run(self.ki.anmeldung_abschliessen('DERCODE#DERSTATE'))
        nutzlast = post.call_args.args[1]
        self.assertEqual(nutzlast['code'], 'DERCODE')
        self.assertEqual(nutzlast['state'], 'DERSTATE')
        self.assertTrue(nutzlast['code_verifier'])

    def test_an_den_token_endpunkt_geht_KEINE_browserkennung(self):
        """console.anthropic.com antwortet darauf mit 429 — und dann sieht es
        aus, als wäre das Abo am Limit."""
        self.ki.anmeldung_starten()
        with mock.patch.object(kimodul, '_post',
                               return_value=antwort({'access_token': 'a', 'expires_in': 60})) as post:
            asyncio.run(self.ki.anmeldung_abschliessen('a#b'))
        kopf = post.call_args.args[2]
        self.assertNotIn('User-Agent', kopf)

    def test_der_zugang_liegt_nur_fuer_den_eigenen_nutzer_lesbar(self):
        """Es ist der Schlüssel zum Konto des Eigners."""
        self.ki.anmeldung_starten()
        with mock.patch.object(kimodul, '_post',
                               return_value=antwort({'access_token': 'a', 'expires_in': 60})):
            asyncio.run(self.ki.anmeldung_abschliessen('a#b'))
        rechte = stat.S_IMODE(self.ki.datei.stat().st_mode)
        self.assertEqual(rechte, 0o600, oct(rechte))

    def test_antwort_ohne_zugang_wird_nicht_abgelegt(self):
        self.ki.anmeldung_starten()
        with mock.patch.object(kimodul, '_post', return_value=antwort({'fehler': 'nix'})):
            with self.assertRaises(kimodul.KiFehler):
                asyncio.run(self.ki.anmeldung_abschliessen('a#b'))
        self.assertFalse(self.ki.datei.exists())

    def test_abmelden_raeumt_auf(self):
        self.ki.datei.write_text('{"access_token": "a"}')
        self.assertTrue(self.ki.zustand()['verbunden'])
        self.ki.abmelden()
        self.assertFalse(self.ki.zustand()['verbunden'])
        self.ki.abmelden()          # zweimal darf nicht wehtun


class Erneuerung(unittest.TestCase):

    def setUp(self):
        self.verz = tempfile.mkdtemp(prefix='mave-ki2-')
        self.ki = kimodul.Ki(Path(self.verz) / 'ki.json')

    def tearDown(self):
        shutil.rmtree(self.verz, ignore_errors=True)

    def ablegen(self, **felder):
        self.ki.datei.write_text(json.dumps({'access_token': 'alt', 'refresh_token': 'r',
                                             'gueltig_bis': time.time() + 3600, **felder}))

    def test_ein_frischer_zugang_wird_einfach_benutzt(self):
        self.ablegen()
        with mock.patch.object(kimodul, '_post') as post:
            self.assertEqual(asyncio.run(self.ki._zugang()), 'alt')
            post.assert_not_called()

    def test_ein_abgelaufener_wird_erneuert(self):
        self.ablegen(gueltig_bis=time.time() - 10)
        with mock.patch.object(kimodul, '_post',
                               return_value=antwort({'access_token': 'neu', 'expires_in': 3600})) as post:
            self.assertEqual(asyncio.run(self.ki._zugang()), 'neu')
        self.assertEqual(post.call_args.args[1]['grant_type'], 'refresh_token')

    def test_kurz_vor_ablauf_auch(self):
        """Ein Aufruf, der mitten im Fluss 401 bekommt, ist schwerer zu deuten
        als eine Erneuerung, die zu früh kam."""
        self.ablegen(gueltig_bis=time.time() + 30)
        with mock.patch.object(kimodul, '_post',
                               return_value=antwort({'access_token': 'neu', 'expires_in': 3600})):
            self.assertEqual(asyncio.run(self.ki._zugang()), 'neu')

    def test_der_alte_erneuerungsschluessel_gilt_weiter(self):
        """Kommt keiner mit, wäre die Anmeldung sonst nach einer Stunde weg."""
        self.ablegen(gueltig_bis=0)
        with mock.patch.object(kimodul, '_post',
                               return_value=antwort({'access_token': 'neu', 'expires_in': 60})):
            asyncio.run(self.ki._zugang())
        self.assertEqual(json.loads(self.ki.datei.read_text())['refresh_token'], 'r')

    def test_ohne_erneuerungsschluessel_ein_klarer_hinweis(self):
        self.ki.datei.write_text(json.dumps({'access_token': 'a', 'gueltig_bis': 0}))
        with self.assertRaises(kimodul.KiFehler) as e:
            asyncio.run(self.ki._zugang())
        self.assertIn('neu anmelden', str(e.exception))

    def test_gar_kein_zugang(self):
        with self.assertRaises(kimodul.KiFehler) as e:
            asyncio.run(self.ki._zugang())
        self.assertIn('Einstellungen', str(e.exception))


class Fragen(unittest.TestCase):

    def setUp(self):
        self.verz = tempfile.mkdtemp(prefix='mave-ki3-')
        self.ki = kimodul.Ki(Path(self.verz) / 'ki.json')
        self.ki.datei.write_text(json.dumps({'access_token': 'tok', 'refresh_token': 'r',
                                             'gueltig_bis': time.time() + 3600}))

    def tearDown(self):
        shutil.rmtree(self.verz, ignore_errors=True)

    def fragen(self, rueck, **kw):
        with mock.patch.object(kimodul, '_post', return_value=rueck) as post:
            try:
                return asyncio.run(self.ki.frage('Was ist das?', **kw)), post
            finally:
                self.post = post

    def test_die_antwort_kommt_als_text(self):
        text, _ = self.fragen(antwort({'content': [{'type': 'text', 'text': ' Hallo '}]}))
        self.assertEqual(text, 'Hallo')

    def test_das_bild_geht_VOR_dem_text(self):
        """Sonst liest das Modell die Frage, bevor es weiß, worum es geht."""
        self.fragen(antwort({'content': [{'type': 'text', 'text': 'ok'}]}),
                    bilder=[('image/jpeg', b'\xff\xd8\xffdaten')])
        inhalt = self.post.call_args.args[1]['messages'][0]['content']
        self.assertEqual(inhalt[0]['type'], 'image')
        self.assertEqual(inhalt[-1]['type'], 'text')
        self.assertEqual(inhalt[0]['source']['media_type'], 'image/jpeg')

    def test_an_die_api_geht_sehr_wohl_eine_kennung(self):
        """Anders als der Token-Endpunkt WILL api.anthropic.com eine sehen."""
        self.fragen(antwort({'content': [{'type': 'text', 'text': 'ok'}]}))
        kopf = self.post.call_args.args[2]
        self.assertIn('User-Agent', kopf)
        self.assertEqual(kopf['Authorization'], 'Bearer tok')
        self.assertTrue(kopf['anthropic-beta'])

    def test_ein_unbekanntes_modell_faellt_auf_die_vorgabe_zurueck(self):
        self.fragen(antwort({'content': [{'type': 'text', 'text': 'ok'}]}), modell='gpt-9')
        self.assertEqual(self.post.call_args.args[1]['model'], kimodul.MODELL_VORGABE)

    def test_jeder_fehler_bekommt_seinen_eigenen_satz(self):
        for code, wort in ((401, 'neu anmelden'), (429, 'Limit'), (500, 'gescheitert')):
            with self.assertRaises(kimodul.KiFehler) as e:
                self.fragen((code, '{}'))
            self.assertIn(wort, str(e.exception), f'bei {code}')

    def test_eine_leere_antwort_ist_ein_fehler(self):
        with self.assertRaises(kimodul.KiFehler):
            self.fragen(antwort({'content': []}))

    def test_unlesbare_antwort(self):
        with self.assertRaises(kimodul.KiFehler):
            self.fragen((200, 'kein json'))


if __name__ == '__main__':
    unittest.main()
