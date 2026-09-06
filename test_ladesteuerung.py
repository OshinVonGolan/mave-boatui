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
    """Halten heisst: Haltespannung schreiben, Lader bleibt an.

    Frueher wurde am Ziel-SOC der Lader abgeschaltet. Das startet bei jedem
    Wiedereinschalten den Ladezyklus neu (der Lader steht danach auf Bulk), und
    ein ausgefallener Pi haette die Bank dauerhaft von der Ladung abgeschnitten.
    Ueber die Spannung gehalten hoert der Lader von selbst auf zu druecken.
    """

    def test_ueber_ziel_wird_auf_haltespannung_gestellt(self):
        r = self._regler({'mode': None})     # der kaputte Stand vom Boot
        r.update_soc(85)
        hold = cc._DEFAULT_SETTINGS['harbor']['hold_voltage']
        for d in r.device_setpoints():
            self.assertIs(d['on'], True, f"{d['label']} soll an bleiben")
            self.assertAlmostEqual(d['absorption_v'], hold, places=3)
            self.assertAlmostEqual(d['float_v'],      hold, places=3)

    def test_halten_per_abschalten_bleibt_waehlbar(self):
        r = self._regler(settings={'harbor': {'hold_mode': 'aus'}})
        r.update_soc(85)
        for d in r.device_setpoints():
            self.assertIs(d['on'], False, f"{d['label']} haette abschalten muessen")

    def test_unbekannte_halteart_faellt_auf_spannung_zurueck(self):
        r = self._regler(settings={'harbor': {'hold_mode': 'quatsch'}})
        r.update_soc(85)
        self.assertTrue(all(d['on'] is True for d in r.device_setpoints()))

    def test_unter_ziel_wird_geladen(self):
        r = self._regler()
        r.update_soc(60)
        lade = cc._DEFAULT_SETTINGS['harbor']['absorption_v']
        for d in r.device_setpoints():
            self.assertIs(d['on'], True)
        werte = {d['id']: d['absorption_v'] for d in r.device_setpoints()}
        self.assertAlmostEqual(werte['mppt'], lade, places=3)

    def test_hysterese_haelt_bis_drei_punkte_darunter(self):
        r = self._regler()
        hold = cc._DEFAULT_SETTINGS['harbor']['hold_voltage']
        r.update_soc(85)                     # Halten beginnt
        r.update_soc(78)                     # Ziel 80 minus 3 = 77 → noch halten
        self.assertTrue(all(abs(d['absorption_v'] - hold) < 1e-6
                            for d in r.device_setpoints()))
        r.update_soc(76)                     # jetzt darunter
        self.assertTrue(all(d['absorption_v'] > hold for d in r.device_setpoints()))

    def test_solar_hat_beim_laden_vorrang(self):
        # Nicht-Solar bekommt Absorption minus Offset, damit zuerst die Sonne
        # zum Zuge kommt.
        r = self._regler()
        r.update_soc(60)
        nach = {d['id']: d['absorption_v'] for d in r.device_setpoints()}
        self.assertGreater(nach['mppt'], nach['ip43'])
        self.assertAlmostEqual(nach['mppt'] - nach['ip43'],
                               cc._DEFAULT_SETTINGS['solar_priority_offset_v'], places=3)

    def test_beim_halten_kein_solar_versatz(self):
        # Mit Versatz laege der Landlader unter der Ruhespannung und waere
        # faktisch aus — dann waere das Halten ueber die Spannung sinnlos.
        r = self._regler()
        r.update_soc(85)
        nach = {d['id']: d['absorption_v'] for d in r.device_setpoints()}
        self.assertAlmostEqual(nach['mppt'], nach['ip43'], places=3)

    def test_erhaltung_nie_ueber_absorption(self):
        r = self._regler(settings={'harbor': {'absorption_v': 13.4, 'float_v': 13.9}})
        r.update_soc(60)
        for d in r.device_setpoints():
            self.assertLessEqual(d['float_v'], d['absorption_v'])


class NurBeimUmschaltenSchreiben(Basis):
    """Die Sollwert-Register liegen im Flash des Laders.

    Frueher loeste jede SOC-Aenderung ab 0,5 % ein Schreiben aus. Beim Laden
    einer 900-Ah-Bank sind das etliche Schreibvorgaenge je Stunde.
    """

    def test_kein_schreiben_ohne_zustandswechsel(self):
        r = self._regler()
        self.assertTrue(r.update_soc(50))        # erstes Mal: Zustand wird gesetzt
        for soc in (52, 55, 60, 66, 70, 75):
            self.assertFalse(r.update_soc(soc),
                             f'SOC {soc} haette nichts schreiben duerfen')

    def test_schreiben_beim_wechsel_in_das_halten(self):
        r = self._regler()
        r.update_soc(50)
        self.assertTrue(r.update_soc(81))
        self.assertFalse(r.update_soc(83))

    def test_schreiben_beim_wechsel_zurueck(self):
        r = self._regler()
        r.update_soc(85)
        self.assertFalse(r.update_soc(78))       # im Hystereseband
        self.assertTrue(r.update_soc(70))


class NachsetzenBeiAbweichung(Basis):
    """Der Lader meldet seine Sollwerte zurueck (PGN 130914).

    Weicht die Rueckmeldung ab, hat ihn etwas anderes verstellt — die App, ein
    Werksreset, ein Stromausfall. Genau dann wird nachgesetzt, statt periodisch
    zu wiederholen und dabei nur das Flash abzunutzen.
    """

    def _ip43(self, regler):
        return next(d for d in regler.device_setpoints() if d['instance'] == 1)

    def test_gleiche_werte_loesen_nichts_aus(self):
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        r.update_actual_setpoints(soll['absorption_v'], soll['float_v'])
        self.assertFalse(r.update_soc(61))

    def test_abweichung_setzt_einmal_nach(self):
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        r.update_actual_setpoints(soll['absorption_v'] + 0.4, soll['float_v'])
        self.assertTrue(r.update_soc(61))        # einmal nachsetzen
        self.assertFalse(r.update_soc(62))       # danach wieder Ruhe

    def test_kleine_abweichung_bleibt_folgenlos(self):
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        r.update_actual_setpoints(soll['absorption_v'] + 0.01, soll['float_v'])
        self.assertFalse(r.update_soc(61))

    def test_ohne_bekannten_soc_wird_nicht_geprueft(self):
        # Sonst schreibt jeder Neustart einmal ins Flash: ohne SOC nimmt
        # device_setpoints() 'Laden' an, ein haltender Lader saehe verstellt aus.
        r = self._regler()
        r.update_actual_setpoints(11.0, 11.0)
        self.assertFalse(r._nachsetzen)


if __name__ == '__main__':
    unittest.main()
