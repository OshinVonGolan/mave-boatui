"""Wasserstand: waehlbare Pegel statt eines fest verdrahteten.

Travemünde stand im Quelltext — Adresse, Pegelnullpunkt und die BSH-Kurve
gleich mit. Das beantwortet die Frage nicht, die man abends stellt: komme ich
im ZIELhafen noch über die Schwelle.

Zwei Dinge machen das schwieriger als beim Wetter. Erstens hat jeder Pegel
seinen eigenen Nullpunkt — 527 cm in Warnemünde und 527 cm in Travemünde sind
verschiedene Wasserstände, und ohne Umrechnung wären die Zahlen zweier Pegel
nicht vergleichbar. Zweitens gibt es eine Vorhersagekurve nur beim BSH und nur
für eine Handvoll Ostseepegel.

Geprüft wird ohne Netz: alles nach draußen geht durch `_http_json`.

Aufruf:

    ./venv/bin/python -m unittest test_pegel -v
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

import asyncio                                 # noqa: E402
import urllib.error                            # noqa: E402

from fastapi import HTTPException              # noqa: E402

import main                                    # noqa: E402

TRAVE = 'c7383149-1f77-430d-8bef-c5667be3846b'
WARNE = '220ff4c6-83da-4a1b-9c13-dfee5a2a8798'


def _messungen(werte, minuten=10):
    """Eine Messreihe, die JETZT endet — sonst greift der 30-Minuten-Vergleich."""
    jetzt = datetime.now(timezone.utc)
    n = len(werte)
    return [{'timestamp': (jetzt - timedelta(minutes=(n - 1 - i) * minuten)).isoformat(),
             'value': v} for i, v in enumerate(werte)]


class Pegelnullpunkt(unittest.TestCase):
    """Ohne ihn ist ein Pegelstand keine Höhe, sondern eine Hausnummer."""

    def setUp(self):
        main._gz_cache.clear()

    def test_kommt_vom_pegel_und_nicht_aus_dem_quelltext(self):
        with mock.patch.object(main, '_http_json',
                               return_value={'gaugeZero': {'value': -4.979}}):
            self.assertAlmostEqual(main._pegel_nullpunkt(WARNE), -4.979)

    def test_wird_nur_einmal_geholt(self):
        """Er ändert sich alle paar Jahrzehnte — Travemünde zuletzt 2019."""
        with mock.patch.object(main, '_http_json',
                               return_value={'gaugeZero': {'value': -5.025}}) as ruf:
            main._pegel_nullpunkt(TRAVE)
            main._pegel_nullpunkt(TRAVE)
        self.assertEqual(ruf.call_count, 1)

    def test_ohne_antwort_der_rueckfall(self):
        """Lieber der Wert des Heimatpegels als gar keine Höhe."""
        with mock.patch.object(main, '_http_json', side_effect=OSError('weg')):
            self.assertAlmostEqual(main._pegel_nullpunkt('unbekannt'), main._WL_PNP_M)


class BshDateiname(unittest.TestCase):
    """Das BSH schreibt Titelschreibweise ohne Umlaute."""

    def test_umlaute_werden_umschrieben(self):
        self.assertEqual(main._bsh_dateiname('TRAVEMÜNDE'), 'Travemuende')
        self.assertEqual(main._bsh_dateiname('WARNEMÜNDE'), 'Warnemuende')

    def test_bindestriche_bleiben_bindestriche(self):
        self.assertEqual(main._bsh_dateiname('KIEL-HOLTENAU'), 'Kiel-Holtenau')

    def test_leerzeichen_werden_zu_bindestrichen(self):
        self.assertEqual(main._bsh_dateiname('GREIFSWALD WIECK'), 'Greifswald-Wieck')

    def test_die_adresse_ist_verpackt(self):
        self.assertNotIn(' ', main._bsh_url('TIMMENDORF POEL'))


class Wasserstand(unittest.TestCase):
    """Was aus der Messreihe wird."""

    def setUp(self):
        main._gz_cache.clear()
        main._gz_cache[TRAVE] = -5.025          # kein Netzabruf dafuer

    def holen(self, werte):
        with mock.patch.object(main, '_http_json', return_value=_messungen(werte)):
            return main._fetch_waterlevel(TRAVE, 'Travemünde')

    def test_umrechnung_auf_nhn(self):
        """514 cm über Pegelnull bei −5,025 m Nullpunkt sind +11 cm NHN."""
        self.assertEqual(self.holen([510, 512, 514])['current_nhn_cm'], 11)

    def test_der_pegelnullpunkt_steht_dabei(self):
        """Sonst ist auf der Seite nicht nachzuvollziehen, worauf sich die
        Zahl bezieht — und zwei Pegel sähen gleich aus."""
        self.assertAlmostEqual(self.holen([500])['station']['pnp_m'], -5.025)

    def test_trend_steigend_fallend_gleich(self):
        # Zehn Minuten je Schritt: vier Schritte zurueck sind 40 Minuten,
        # der Vergleich greift also.
        self.assertEqual(self.holen([500, 502, 504, 508, 512])['trend'], 'rising')
        self.assertEqual(self.holen([512, 508, 504, 502, 500])['trend'], 'falling')
        self.assertEqual(self.holen([500, 500, 501, 500, 500])['trend'], 'stable')

    def test_leere_reihe_gibt_leeres_ergebnis_statt_fehler(self):
        with mock.patch.object(main, '_http_json', return_value=[]):
            self.assertEqual(main._fetch_waterlevel(TRAVE, 'Travemünde'), {})

    def test_reihe_wird_ausgeduennt(self):
        """Ein Tag Minutenwerte sind 1440 Punkte fuer eine Grafik, die 600
        Pixel breit ist."""
        d = self.holen(list(range(500, 500 + 600)))
        self.assertLessEqual(len(d['measurements']), 130)

    def test_der_name_faehrt_mit(self):
        self.assertEqual(self.holen([500])['station']['name'], 'Travemünde')


class VorhersageNurWoEsSieGibt(unittest.TestCase):

    def setUp(self):
        main._wl_cache.clear()
        main._bsh_cache.clear()
        main._gz_cache.clear()
        main._gz_cache[TRAVE] = -5.025
        self.verz = tempfile.mkdtemp(prefix='mave-pegel-')
        self.datei = Path(self.verz) / 'presets.json'
        self.datei.write_text(json.dumps({'pegel': {'stationen': [
            {'name': 'Travemünde', 'uuid': TRAVE}]}}))
        self._alt = main.PRESETS_FILE
        main.PRESETS_FILE = self.datei

    def tearDown(self):
        main.PRESETS_FILE = self._alt
        for c in (main._wl_cache, main._bsh_cache, main._gz_cache):
            c.clear()
        shutil.rmtree(self.verz, ignore_errors=True)

    def abrufen(self, bsh):
        with mock.patch.object(main, '_http_json', return_value=_messungen([510, 514])), \
             mock.patch.object(main, '_fetch_bsh_forecast', **bsh) as ruf:
            d = asyncio.run(main.get_waterlevel())
        self.bsh_rufe = ruf.call_count
        return d

    def test_ohne_kurve_kein_bild(self):
        """Sonst steht auf der Seite ein leerer Rahmen mit kaputtem Verweis."""
        d = self.abrufen({'side_effect': urllib.error.HTTPError(
            'u', 404, 'Not Found', {}, None)})
        self.assertNotIn('forecast_img', d)
        self.assertNotIn('forecast_min_nhn_cm', d)

    def test_mit_kurve_aber_ohne_auswertung_trotzdem_das_bild(self):
        """Auf dem Pi ist genau das der Normalfall: Pillow und numpy sind dort
        nicht installiert. Das Bild anzusehen braucht aber nur einen Browser."""
        d = self.abrufen({'return_value': {}})
        self.assertIn('forecast_img', d)
        self.assertNotIn('forecast_min_nhn_cm', d)

    def test_mit_auswertung_kommt_der_tiefste_wert_mit(self):
        d = self.abrufen({'return_value': {'min_nhn_cm': -75}})
        self.assertEqual(d['forecast_min_nhn_cm'], -75)
        self.assertTrue(d['forecast_alarm'])

    def test_kein_alarm_ueber_der_schwelle(self):
        self.assertFalse(self.abrufen({'return_value': {'min_nhn_cm': -20}})['forecast_alarm'])

    def test_der_fehlschlag_wird_mitgespeichert(self):
        """Ohne das liefe die Anwendung alle fuenf Minuten in denselben 404 —
        fuer jeden Pegel, fuer den es keine Kurve gibt, also fast alle."""
        fehler = urllib.error.HTTPError('u', 404, 'Not Found', {}, None)
        with mock.patch.object(main, '_http_json', return_value=_messungen([510])), \
             mock.patch.object(main, '_fetch_bsh_forecast', side_effect=fehler) as ruf:
            asyncio.run(main.get_waterlevel())
            main._wl_cache.clear()              # nur den Wasserstand vergessen
            asyncio.run(main.get_waterlevel())
        self.assertEqual(ruf.call_count, 1)


class Pegelverwaltung(unittest.TestCase):

    def setUp(self):
        main._wl_cache.clear()
        self.verz = tempfile.mkdtemp(prefix='mave-pegelv-')
        self.datei = Path(self.verz) / 'presets.json'
        self.datei.write_text('{}')
        self._alt = main.PRESETS_FILE
        main.PRESETS_FILE = self.datei

    def tearDown(self):
        main.PRESETS_FILE = self._alt
        main._wl_cache.clear()
        shutil.rmtree(self.verz, ignore_errors=True)

    def setzen(self, pegel):
        return asyncio.run(main.update_settings({'pegel': pegel}))

    def test_ohne_pflege_der_heimatpegel(self):
        """Eine leere Kachel waere die schlechtere Antwort."""
        liste = main._pegel_liste()
        self.assertEqual(len(liste), 1)
        self.assertEqual(liste[0]['name'], 'Travemünde')
        self.assertFalse(asyncio.run(main.get_pegel_orte())['gepflegt'])

    def test_gepflegte_liste_ersetzt_die_vorgabe(self):
        self.setzen({'stationen': [{'name': 'Warnemünde', 'uuid': WARNE}]})
        antwort = asyncio.run(main.get_pegel_orte())
        self.assertTrue(antwort['gepflegt'])
        self.assertEqual([p['name'] for p in antwort['stationen']], ['Warnemünde'])

    def test_reihenfolge_bleibt(self):
        namen = ['A', 'B', 'C']
        self.setzen({'stationen': [{'name': n, 'uuid': TRAVE} for n in namen]})
        self.assertEqual([p['name'] for p in main._pegel_liste()], namen)

    def test_hoechstens_fuenf(self):
        with self.assertRaises(HTTPException):
            self.setzen({'stationen': [{'name': str(i), 'uuid': TRAVE} for i in range(6)]})

    def test_kennung_muss_wie_eine_kennung_aussehen(self):
        """Sie geht in eine URL. Ohne diese Pruefung liesse sich darueber ein
        beliebiger Pfad anhaengen."""
        for boese in ('../../etc/passwd', 'a b', 'http://anderswo/', 'x' * 65, ''):
            with self.assertRaises(HTTPException, msg=boese):
                self.setzen({'stationen': [{'name': 'x', 'uuid': boese}]})

    def test_name_ist_pflicht(self):
        with self.assertRaises(HTTPException):
            self.setzen({'stationen': [{'name': '  ', 'uuid': TRAVE}]})

    def test_zusaetzliche_felder_werden_abgelehnt(self):
        with self.assertRaises(HTTPException):
            self.setzen({'stationen': [{'name': 'x', 'uuid': TRAVE, 'pnp_m': 0}]})

    def test_fremder_pegel_wird_nicht_abgerufen(self):
        """Sonst waere ueber diese Adresse jeder Pegel der Welt abrufbar, und
        der Zwischenspeicher waechst mit jeder Anfrage."""
        self.setzen({'stationen': [{'name': 'Travemünde', 'uuid': TRAVE}]})
        with self.assertRaises(HTTPException) as e:
            asyncio.run(main.get_waterlevel(WARNE))
        self.assertEqual(e.exception.status_code, 400)

    def test_je_pegel_ein_eigener_zwischenspeicher(self):
        self.setzen({'stationen': [{'name': 'Travemünde', 'uuid': TRAVE},
                                   {'name': 'Warnemünde', 'uuid': WARNE}]})
        main._gz_cache[TRAVE] = main._gz_cache[WARNE] = -5.0
        with mock.patch.object(main, '_http_json', return_value=_messungen([510])), \
             mock.patch.object(main, '_fetch_bsh_forecast', return_value=None):
            asyncio.run(main.get_waterlevel(TRAVE))
            asyncio.run(main.get_waterlevel(WARNE))
            self.assertEqual(len(main._wl_cache), 2)

    def test_kaputte_datei_wirft_nicht(self):
        self.datei.write_text(json.dumps({'pegel': 'nein'}))
        self.assertEqual(main._pegel_liste()[0]['name'], 'Travemünde')


class Pegelsuche(unittest.TestCase):

    def test_zu_kurz_geht_gar_nicht_erst_los(self):
        with mock.patch.object(main, '_http_json') as ruf:
            self.assertEqual(asyncio.run(main.get_pegel_suche('T'))['treffer'], [])
            ruf.assert_not_called()

    def test_gewaesser_kommt_dazu(self):
        """„Neustadt" gibt es an mehreren Gewässern."""
        roh = [{'uuid': TRAVE, 'longname': 'TRAVEMÜNDE',
                'water': {'longname': 'TRAVE'}}]
        with mock.patch.object(main, '_http_json', return_value=roh):
            t = asyncio.run(main.get_pegel_suche('trave'))['treffer'][0]
        self.assertEqual(t['name'], 'Travemünde')
        self.assertEqual(t['zusatz'], 'Trave')

    def test_treffer_ohne_kennung_fallen_raus(self):
        """Ohne Kennung liesse sich der Pegel nicht abrufen — er waere ein
        Eintrag, der beim Antippen nichts tut."""
        roh = [{'longname': 'IRGENDWO'}, {'uuid': TRAVE, 'longname': 'TRAVEMÜNDE'}]
        with mock.patch.object(main, '_http_json', return_value=roh):
            self.assertEqual(len(asyncio.run(main.get_pegel_suche('trave'))['treffer']), 1)

    def test_stoerung_wird_gemeldet(self):
        with mock.patch.object(main, '_http_json', side_effect=OSError('weg')):
            with self.assertRaises(HTTPException) as e:
                asyncio.run(main.get_pegel_suche('kiel'))
        self.assertEqual(e.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()
