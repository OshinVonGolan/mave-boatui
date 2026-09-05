"""Wetter: Orte, Modelle, Modellvergleich.

Die Kachel zeigte bis hierher zwei fest verdrahtete Punkte — Lübeck und die
Lübecker Bucht. Das ist die falsche Antwort auf die Frage vor dem Ablegen:
die lautet selten "wie wird es hier", sondern "wie wird es dort, wo ich
hinwill". Deshalb bis zu fünf Orte, die eigene Position dazu, und die Wahl des
Rechenmodells.

Geprüft wird ohne Netz: jeder Abruf nach draußen geht durch `_http_json`, und
genau die eine Stelle wird ersetzt. Ein Test, der Open-Meteo braucht, prüft
sonst irgendwann das Wetter statt den Quelltext.

Aufruf:

    ./venv/bin/python -m unittest test_wetter -v
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

import asyncio                                 # noqa: E402

from fastapi import HTTPException              # noqa: E402

import main                                    # noqa: E402


# ── Antworten, wie Open-Meteo sie schickt ──────────────────────────────────
# Klein gehalten, aber in der Form echt: zwei Tage, vier Stunden. Die Löcher
# darin sind Absicht — fehlende Werte sind der Normalfall, nicht die Ausnahme.

TAGE = {'daily': {
    'time': ['2026-09-05', '2026-09-06'],
    'weathercode': [3, 95],
    'temperature_2m_max': [18.1, 20.4], 'temperature_2m_min': [14.1, 12.2],
    'precipitation_sum': [3.1, 0.0], 'precipitation_probability_max': [100, 0],
    'windspeed_10m_max': [15.2, 11.0], 'windgusts_10m_max': [31.3, 19.4],
    'winddirection_10m_dominant': [272, 200],
    'sunrise': ['2026-09-05T06:32', '2026-09-06T06:34'],
    'sunset': ['2026-09-05T19:58', '2026-09-06T19:56'],
}}
STUNDEN = {'hourly': {
    'time': ['2026-09-05T00:00', '2026-09-05T01:00', '2026-09-05T02:00', '2026-09-05T03:00'],
    'temperature_2m': [15.0, 14.8, 14.6, None],
    'weathercode': [3, 3, 61, 61],
    'precipitation': [0.0, 0.2, 1.4, 0.0],
    'windspeed_10m': [10.0, 12.0, 14.0, 13.0],
    'windgusts_10m': [19.0, 21.0, 26.0, 24.0],
    'winddirection_10m': [270, 275, 280, 285],
    'pressure_msl': [1019.0, 1019.6, 1020.2, 1020.8],
}}
SEE_TAGE = {'daily': {
    'time': ['2026-09-05', '2026-09-06'],
    'wave_height_max': [0.6, 0.3], 'wave_direction_dominant': [261, 250],
    'wave_period_max': [3.15, 2.9],
}}
SEE_STUNDEN = {'hourly': {
    'time': ['2026-09-05T00:00', '2026-09-05T01:00', '2026-09-05T02:00', '2026-09-05T03:00'],
    'wave_height': [0.3, 0.35, 0.4, 0.4],
    'wave_direction': [261, 262, 263, 264],
    'wave_period': [3.1, 3.1, 3.2, 3.2],
}}


def _antwort_fuer(url: str) -> dict:
    """Die passende Vorlage zur Adresse — wie der echte Dienst es täte."""
    if 'marine-api' in url:
        return SEE_TAGE if 'daily=' in url else SEE_STUNDEN
    return TAGE if 'daily=' in url else STUNDEN


class WetterAbruf(unittest.TestCase):
    """Was aus den Rohdaten wird."""

    def holen(self, lat=53.9585, lon=10.8752, modell='auto', antwort=_antwort_fuer):
        with mock.patch.object(main, '_http_json', side_effect=antwort) as ruf:
            d = main._fetch_weather(lat, lon, modell)
        self.gerufen = [c.args[0] for c in ruf.call_args_list]
        return d

    def test_tage_und_stunden_kommen_beide(self):
        """Die Tage beantworten "wird das Wochenende was", die Stunden "wann
        heute". Das sind zwei Fragen, und die Seite stellt beide."""
        d = self.holen()
        self.assertEqual(len(d['tage']), 2)
        self.assertEqual(len(d['stunden']), 4)

    def test_seegang_liegt_am_tag_und_an_der_stunde(self):
        d = self.holen()
        self.assertAlmostEqual(d['tage'][0]['wave'], 0.6)
        self.assertAlmostEqual(d['tage'][0]['wave_periode'], 3.15)
        self.assertAlmostEqual(d['stunden'][2]['welle'], 0.4)

    def test_gewitter_wird_als_sturm_markiert(self):
        """95, 96 und 99 sind Gewitter. Ein Symbol allein reicht dafür nicht —
        die Kachel färbt den Tag."""
        d = self.holen()
        self.assertFalse(d['tage'][0]['storm'])
        self.assertTrue(d['tage'][1]['storm'])

    def test_fehlende_stundenwerte_werden_zu_none_und_nicht_zu_null(self):
        """`None` heißt "weiß ich nicht", `0` heißt "kein Wind". Die Linie im
        Diagramm setzt bei None aus statt auf den Boden zu fallen."""
        self.assertIsNone(self.holen()['stunden'][3]['temp'])

    def test_ort_an_land_bekommt_einfach_keinen_seegang(self):
        """Die Marine-API antwortet für einen Punkt an Land mit einem Fehler.
        Das ist kein Fehler der Anwendung — der Rest muss trotzdem kommen."""
        def ohne_see(url):
            if 'marine-api' in url:
                raise OSError('kein Gitterpunkt')
            return _antwort_fuer(url)
        d = self.holen(antwort=ohne_see)
        self.assertEqual(len(d['tage']), 2)
        self.assertIsNone(d['tage'][0]['wave'])
        self.assertIsNone(d['stunden'][0]['welle'])

    def test_modell_geht_als_parameter_mit(self):
        self.holen(modell='icon')
        self.assertTrue(all('models=icon_seamless' in u for u in self.gerufen
                            if 'marine-api' not in u))

    def test_automatisch_schickt_kein_modell(self):
        """`auto` heißt: Open-Meteo wählt je Ort. Ein leerer models-Parameter
        wäre etwas anderes als keiner."""
        self.holen(modell='auto')
        self.assertTrue(all('models=' not in u for u in self.gerufen))

    def test_knoten_und_nicht_kilometer(self):
        """Auf einem Boot ist die Einheit kn. Ohne den Parameter liefert
        Open-Meteo km/h — und 15 sähe genauso plausibel aus wie 15 Knoten."""
        self.holen()
        self.assertTrue(all('windspeed_unit=kn' in u for u in self.gerufen
                            if 'marine-api' not in u))


class WetterEndpunkt(unittest.TestCase):
    """Der Zwischenspeicher und was passiert, wenn der Dienst schweigt."""

    def setUp(self):
        main._wx_cache.clear()
        self.verz = tempfile.mkdtemp(prefix='mave-wetter-')
        self.datei = Path(self.verz) / 'presets.json'
        self.datei.write_text('{}')
        self._alt = main.PRESETS_FILE
        main.PRESETS_FILE = self.datei

    def tearDown(self):
        main.PRESETS_FILE = self._alt
        main._wx_cache.clear()
        shutil.rmtree(self.verz, ignore_errors=True)

    def test_zweiter_abruf_geht_nicht_noch_einmal_ins_netz(self):
        with mock.patch.object(main, '_http_json', side_effect=_antwort_fuer) as ruf:
            asyncio.run(main.get_weather(54.0, 11.0))
            zahl = ruf.call_count
            asyncio.run(main.get_weather(54.0, 11.0))
            self.assertEqual(ruf.call_count, zahl)

    def test_anderer_ort_ist_ein_anderer_eintrag(self):
        with mock.patch.object(main, '_http_json', side_effect=_antwort_fuer) as ruf:
            asyncio.run(main.get_weather(54.0, 11.0))
            zahl = ruf.call_count
            asyncio.run(main.get_weather(55.0, 12.0))
            self.assertGreater(ruf.call_count, zahl)

    def test_anderes_modell_ist_ein_anderer_eintrag(self):
        """Sonst zeigte der Wechsel auf ICON die Zahlen von ECMWF."""
        with mock.patch.object(main, '_http_json', side_effect=_antwort_fuer) as ruf:
            asyncio.run(main.get_weather(54.0, 11.0, 'icon'))
            zahl = ruf.call_count
            asyncio.run(main.get_weather(54.0, 11.0, 'ecmwf'))
            self.assertGreater(ruf.call_count, zahl)

    def test_bei_stoerung_bleibt_der_alte_stand_stehen(self):
        """Wetter ändert sich langsamer als die Erreichbarkeit eines fremden
        Dienstes. Eine halbe Stunde alt ist besser als eine Fehlermeldung."""
        with mock.patch.object(main, '_http_json', side_effect=_antwort_fuer):
            asyncio.run(main.get_weather(54.0, 11.0))
        main._wx_cache[list(main._wx_cache)[0]]['ts'] = 0      # kuenstlich alt
        with mock.patch.object(main, '_http_json', side_effect=OSError('kein Netz')):
            d = asyncio.run(main.get_weather(54.0, 11.0))
        self.assertEqual(len(d['tage']), 2)

    def test_ohne_alten_stand_ein_ehrlicher_fehler(self):
        with mock.patch.object(main, '_http_json', side_effect=OSError('kein Netz')):
            with self.assertRaises(HTTPException) as e:
                asyncio.run(main.get_weather(54.0, 11.0))
        self.assertEqual(e.exception.status_code, 503)

    def test_unbekanntes_modell_wird_abgelehnt(self):
        with self.assertRaises(HTTPException) as e:
            asyncio.run(main.get_weather(54.0, 11.0, 'wunschdenken'))
        self.assertEqual(e.exception.status_code, 400)

    def test_ohne_ort_nimmt_er_den_ersten_favoriten(self):
        self.datei.write_text(json.dumps({'wetter': {'orte': [
            {'name': 'Fehmarnsund', 'lat': 54.4139, 'lon': 11.1058}]}}))
        with mock.patch.object(main, '_http_json', side_effect=_antwort_fuer) as ruf:
            asyncio.run(main.get_weather())
        self.assertTrue(any('latitude=54.4139' in c.args[0] for c in ruf.call_args_list))

    def test_das_gepflegte_modell_gilt_ohne_angabe(self):
        """Die Wahl gehört zum Boot. Wer sie in den Einstellungen setzt, soll
        sie nicht bei jedem Abruf mitschicken müssen."""
        self.datei.write_text(json.dumps({'wetter': {'modell': 'ukmo'}}))
        with mock.patch.object(main, '_http_json', side_effect=_antwort_fuer) as ruf:
            d = asyncio.run(main.get_weather(54.0, 11.0))
        self.assertEqual(d['modell'], 'ukmo')
        self.assertTrue(any('models=ukmo_seamless' in c.args[0] for c in ruf.call_args_list))


class Modellvergleich(unittest.TestCase):
    """Fünf Modelle in EINEM Abruf — auf dem Pi ist das der Unterschied."""

    ROH = {'hourly': {
        'time': ['2026-09-05T00:00', '2026-09-05T01:00'],
        'windspeed_10m_icon_seamless': [18.0, 17.9],
        'windgusts_10m_icon_seamless': [24.3, 22.7],
        'windspeed_10m_ecmwf_ifs025': [13.1, 13.6],
        'windgusts_10m_ecmwf_ifs025': [None, None],
        'windspeed_10m_gfs_seamless': [18.9, 19.6],
        'windgusts_10m_gfs_seamless': [31.3, 31.9],
        'windspeed_10m_arpege_seamless': [None, None],
        'windgusts_10m_arpege_seamless': [None, None],
        'windspeed_10m_ukmo_seamless': [15.4, 13.0],
        'windgusts_10m_ukmo_seamless': [21.6, 18.1],
    }}

    def setUp(self):
        main._wx_vergleich_cache.clear()

    def holen(self):
        with mock.patch.object(main, '_http_json', return_value=self.ROH) as ruf:
            d = main._fetch_wetter_vergleich(54.0, 11.0)
        self.rufe = ruf.call_count
        return d

    def test_ein_einziger_abruf(self):
        """Fünf einzelne Abrufe wären fünfmal Verbindungsaufbau auf einem
        Rechner, der einen Kern hat."""
        self.holen()
        self.assertEqual(self.rufe, 1)

    def test_modell_ohne_werte_faellt_raus(self):
        """Eine leere Linie im Diagramm sieht aus wie Flaute."""
        namen = [m['kennung'] for m in self.holen()['modelle']]
        self.assertNotIn('arpege', namen)
        self.assertEqual(namen, ['icon', 'ecmwf', 'gfs', 'ukmo'])

    def test_modell_ohne_boeen_bleibt_drin_ohne_boeen(self):
        """ECMWF rechnet keine Böen. Der Wind daraus ist trotzdem brauchbar —
        eine Liste voller Nullen wäre es nicht."""
        ecmwf = [m for m in self.holen()['modelle'] if m['kennung'] == 'ecmwf'][0]
        self.assertEqual(ecmwf['boe'], [])
        self.assertEqual(ecmwf['wind'], [13.1, 13.6])

    def test_zeiten_kommen_mit(self):
        self.assertEqual(len(self.holen()['zeiten']), 2)

    def test_auch_hier_ein_zwischenspeicher(self):
        with mock.patch.object(main, '_http_json', return_value=self.ROH) as ruf:
            asyncio.run(main.get_wetter_vergleich(54.0, 11.0))
            asyncio.run(main.get_wetter_vergleich(54.0, 11.0))
        self.assertEqual(ruf.call_count, 1)


class Ortsverwaltung(unittest.TestCase):
    """Was in den Einstellungen ankommen darf.

    Die Namen gehen als HTML in die Oberfläche und die Koordinaten in eine
    URL — beides Gründe, hier nichts durchzulassen, was nicht geprüft ist.
    """

    def setUp(self):
        self.verz = tempfile.mkdtemp(prefix='mave-orte-')
        self.datei = Path(self.verz) / 'presets.json'
        self.datei.write_text('{}')
        self._alt = main.PRESETS_FILE
        main.PRESETS_FILE = self.datei

    def tearDown(self):
        main.PRESETS_FILE = self._alt
        shutil.rmtree(self.verz, ignore_errors=True)

    def setzen(self, wetter):
        return asyncio.run(main.update_settings({'wetter': wetter}))

    def gespeichert(self):
        return json.loads(self.datei.read_text()).get('wetter', {})

    def test_orte_werden_gespeichert(self):
        self.setzen({'orte': [{'name': 'Travemünde', 'lat': 53.9585, 'lon': 10.8752}]})
        self.assertEqual(self.gespeichert()['orte'],
                         [{'name': 'Travemünde', 'lat': 53.9585, 'lon': 10.8752}])

    def test_reihenfolge_bleibt(self):
        """Sie ist die Reihenfolge, in der die Kachel durchschaltet — also
        eine Angabe und kein Zufall."""
        namen = ['A', 'B', 'C']
        self.setzen({'orte': [{'name': n, 'lat': 54.0, 'lon': 11.0} for n in namen]})
        self.assertEqual([o['name'] for o in self.gespeichert()['orte']], namen)

    def test_hoechstens_fuenf(self):
        with self.assertRaises(HTTPException) as e:
            self.setzen({'orte': [{'name': str(i), 'lat': 54.0, 'lon': 11.0}
                                  for i in range(6)]})
        self.assertEqual(e.exception.status_code, 400)

    def test_ort_ohne_namen_wird_abgelehnt(self):
        """Ein Schalter ohne Beschriftung wäre nicht zu treffen."""
        with self.assertRaises(HTTPException):
            self.setzen({'orte': [{'name': '  ', 'lat': 54.0, 'lon': 11.0}]})

    def test_koordinaten_ausserhalb_der_erde(self):
        for schlecht in ({'lat': 91, 'lon': 0}, {'lat': 0, 'lon': 181}):
            with self.assertRaises(HTTPException):
                self.setzen({'orte': [{'name': 'x', **schlecht}]})

    def test_zusaetzliche_felder_werden_abgelehnt(self):
        """Sonst landet irgendwann etwas in presets.json, das niemand liest,
        und irgendwann liest es doch jemand."""
        with self.assertRaises(HTTPException):
            self.setzen({'orte': [{'name': 'x', 'lat': 1, 'lon': 2, 'skript': '<svg>'}]})

    def test_unbekanntes_modell_wird_abgelehnt(self):
        with self.assertRaises(HTTPException):
            self.setzen({'modell': 'blick-aus-dem-fenster'})

    def test_modell_und_orte_stehen_nebeneinander(self):
        """Das eine zu setzen darf das andere nicht löschen — die Oberfläche
        schickt sie getrennt."""
        self.setzen({'orte': [{'name': 'A', 'lat': 54.0, 'lon': 11.0}]})
        self.setzen({'modell': 'icon'})
        gespeichert = self.gespeichert()
        self.assertEqual(gespeichert['modell'], 'icon')
        self.assertEqual(len(gespeichert['orte']), 1)

    def test_endpunkt_liefert_orte_modell_und_auswahl(self):
        self.setzen({'orte': [{'name': 'A', 'lat': 54.0, 'lon': 11.0}], 'modell': 'gfs'})
        antwort = asyncio.run(main.get_wetter_orte())
        self.assertEqual(antwort['modell'], 'gfs')
        self.assertEqual(len(antwort['orte']), 1)
        self.assertIn('icon', antwort['modelle'])

    def test_ohne_pflege_faellt_es_auf_auto_zurueck(self):
        self.assertEqual(main._wetter_modell(), 'auto')
        self.assertEqual(main._wetter_orte(), [])

    def test_kaputte_datei_wirft_nicht(self):
        """presets.json kann von Hand bearbeitet worden sein."""
        self.datei.write_text(json.dumps({'wetter': 'nein'}))
        self.assertEqual(main._wetter_orte(), [])
        self.assertEqual(main._wetter_modell(), 'auto')


class Ortssuche(unittest.TestCase):

    def test_zu_kurz_geht_gar_nicht_erst_los(self):
        """Ein Buchstabe liefert Unsinn und kostet einen Abruf."""
        with mock.patch.object(main, '_http_json') as ruf:
            self.assertEqual(asyncio.run(main.get_wetter_suche('L'))['treffer'], [])
            ruf.assert_not_called()

    def test_treffer_bekommen_land_und_region_dazu(self):
        """"Neustadt" gibt es reichlich. Aus der nackten Liste wäre nicht zu
        erkennen, welches gemeint ist."""
        roh = {'results': [{'name': 'Neustadt', 'admin1': 'Schleswig-Holstein',
                            'country': 'Deutschland', 'latitude': 54.1, 'longitude': 10.8}]}
        with mock.patch.object(main, '_http_json', return_value=roh):
            t = asyncio.run(main.get_wetter_suche('Neustadt'))['treffer'][0]
        self.assertEqual(t['zusatz'], 'Schleswig-Holstein, Deutschland')
        self.assertEqual(t['lat'], 54.1)

    def test_umlaute_und_leerzeichen_werden_verpackt(self):
        with mock.patch.object(main, '_http_json', return_value={}) as ruf:
            asyncio.run(main.get_wetter_suche('Sankt Peter-Ording'))
        url = ruf.call_args.args[0]
        self.assertIn('Sankt%20Peter-Ording', url)
        self.assertNotIn(' ', url)

    def test_stoerung_wird_gemeldet_und_nicht_verschluckt(self):
        with mock.patch.object(main, '_http_json', side_effect=OSError('weg')):
            with self.assertRaises(HTTPException) as e:
                asyncio.run(main.get_wetter_suche('Kiel'))
        self.assertEqual(e.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()
