"""Tests der Hafen-Ladesteuerung.

Am Boot vorgefunden: in `charger_state.json` stand `mode: null`. Beim
Zusammenfuehren mit den Vorgaben schlaegt ein gespeichertes null den
Vorgabewert, und `if mode == 'harbor'` ist dann falsch — womit der gesamte
Zweig mit SOC-Ziel, Halten und Hysterese uebersprungen wird. Der Lader haette
stumm 13,8/13,3 V bekommen, ohne jede Begrenzung bei 80 %.

Aufruf:  python3 -m unittest test_ladesteuerung -v
"""
import json
import unittest
from unittest import mock

import charge_control as cc


class Basis(unittest.TestCase):
    def _regler(self, state=None, settings=None):
        datei = {'state': state or {}, 'settings': settings or {}}
        with mock.patch.object(cc, 'read_json', return_value=datei), \
             mock.patch.object(cc, 'write_json'):
            return cc.ChargeController()


class ModusFaelltZurueck(Basis):

    def test_null_faellt_auf_hafen_zurueck(self):
        # Genau der vorgefundene Stand.
        self.assertEqual(self._regler({'mode': None})._state['mode'], 'harbor')

    def test_unsinn_faellt_auch_zurueck(self):
        for kaputt in ('Hafen', '', 42, [], {'a': 1}):
            self.assertEqual(self._regler({'mode': kaputt})._state['mode'], 'harbor')

    def test_gueltige_modi_bleiben(self):
        for m in ('harbor', 'full', 'balance'):
            self.assertEqual(self._regler({'mode': m})._state['mode'], m)


class HaltenBeiZiel(Basis):

    def test_ueber_ziel_wird_gehalten(self):
        r = self._regler({'mode': None})     # der kaputte Stand vom Boot
        r.update_soc(85)
        for d in r.device_setpoints():
            self.assertIs(d['on'], False, f"{d['label']} haette halten muessen")

    def test_unter_ziel_wird_geladen(self):
        r = self._regler()
        r.update_soc(60)
        for d in r.device_setpoints():
            self.assertIs(d['on'], True)

    def test_hysterese_haelt_bis_drei_punkte_darunter(self):
        r = self._regler()
        r.update_soc(85)                     # Halten beginnt
        r.update_soc(78)                     # Ziel 80 minus 3 = 77 → noch halten
        self.assertTrue(all(d['on'] is False for d in r.device_setpoints()))
        r.update_soc(76)                     # jetzt darunter
        self.assertTrue(all(d['on'] is True for d in r.device_setpoints()))

    def test_solar_hat_beim_laden_vorrang(self):
        # Nicht-Solar bekommt Absorption minus Offset, damit zuerst die Sonne
        # zum Zuge kommt.
        r = self._regler()
        r.update_soc(60)
        nach = {d['id']: d['absorption_v'] for d in r.device_setpoints()}
        self.assertGreater(nach['mppt'], nach['ip43'])
        self.assertAlmostEqual(nach['mppt'] - nach['ip43'],
                               cc._DEFAULT_SETTINGS['solar_priority_offset_v'], places=3)


if __name__ == '__main__':
    unittest.main()
