"""Tests der Hafen-Ladesteuerung.

Am Boot vorgefunden: in `charger_state.json` stand `mode: null`. Beim
Zusammenfuehren mit den Vorgaben schlaegt ein gespeichertes null den
Vorgabewert, und `if mode == 'harbor'` ist dann falsch — womit der gesamte
Zweig mit SOC-Ziel, Halten und Hysterese uebersprungen wird. Der Lader haette
stumm 13,8/13,3 V bekommen, ohne jede Begrenzung bei 80 %.

Aufruf:  python3 -m unittest test_ladesteuerung -v
"""
import json
import time
import unittest
from datetime import datetime, timedelta
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

    def test_zwei_hundertstel_sind_eine_abweichung(self):
        """Am Boot vorgefunden: gewollt 13,50 V, im Lader standen 13,48 V.

        Die Schwelle lag bei 0,05 V, die 0,02 V galten also als "passt schon".
        Da zugleich kein Wechsel zwischen Laden und Halten stattfand, wurde nie
        geschrieben — der Lader stand tagelang auf einem Wert, den niemand
        gesetzt hatte. Die Register rechnen in 0,01 V; zwei Stufen sind eine
        echte Abweichung.
        """
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        r.update_actual_setpoints(soll['absorption_v'] - 0.02, soll['float_v'])
        self.assertTrue(r.update_soc(61))

    def test_rundung_feiner_als_eine_stufe_bleibt_folgenlos(self):
        # Der Weg zum Lader rundet auf 0,01 V. Ist der eingestellte Wert feiner,
        # darf die Rundung kein dauerndes Nachsetzen ausloesen.
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        r.update_actual_setpoints(soll['absorption_v'] + 0.003, soll['float_v'])
        self.assertFalse(r.update_soc(61))

    def test_geaenderte_einstellung_wird_hingeschickt(self):
        # Sonst bliebe eine neue Absorptionsspannung im Lader stehen, bis
        # zufaellig zwischen Laden und Halten gewechselt wird.
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        r.update_actual_setpoints(soll['absorption_v'], soll['float_v'])
        self.assertFalse(r.update_soc(61))
        r.update_settings({'harbor': {'absorption_v': 14.0}})
        self.assertTrue(r.update_soc(62))

    def test_hartnaeckige_abweichung_wird_irgendwann_aufgegeben(self):
        """Uebernimmt der Lader den Wert nie, darf nicht ewig geschrieben werden.

        Bei einer Rueckmeldung alle fuenf Minuten waeren das ueber hundert
        Schreibvorgaenge am Tag in ein Flash, das dafuer nicht gedacht ist.
        """
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        falsch = soll['absorption_v'] - 0.5
        versuche = 0
        for _ in range(12):
            r._nachsetz_zeit = None            # Mindestabstand ueberspringen
            r.update_actual_setpoints(falsch, soll['float_v'])
            if r.update_soc(60 + versuche * 0.1):
                versuche += 1
        self.assertEqual(versuche, cc._NACHSETZ_MAX)

    def test_innerhalb_des_mindestabstands_kein_zweiter_versuch(self):
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        falsch = soll['absorption_v'] - 0.5
        r.update_actual_setpoints(falsch, soll['float_v'])
        self.assertTrue(r.update_soc(61))
        r.update_actual_setpoints(falsch, soll['float_v'])
        self.assertFalse(r.update_soc(62))

    def test_uebernommener_wert_loest_die_bremse(self):
        r = self._regler()
        r.update_soc(60)
        soll = self._ip43(r)
        r.update_actual_setpoints(soll['absorption_v'] - 0.5, soll['float_v'])
        r.update_soc(61)
        self.assertEqual(r._nachsetz_zaehler, 1)
        r.update_actual_setpoints(soll['absorption_v'], soll['float_v'])
        self.assertEqual(r._nachsetz_zaehler, 0)

    def test_ohne_bekannten_soc_wird_nicht_geprueft(self):
        # Sonst schreibt jeder Neustart einmal ins Flash: ohne SOC nimmt
        # device_setpoints() 'Laden' an, ein haltender Lader saehe verstellt aus.
        r = self._regler()
        r.update_actual_setpoints(11.0, 11.0)
        self.assertFalse(r._nachsetzen)


class HaltespannungSelbstErmitteln(Basis):
    """Kein Modell, eine langsame Rueckkopplung.

    Gemessen wird der Ladezustand, den die Bank bei der aktuellen
    Haltespannung annimmt; die Spannung wird um einen Schritt nachgezogen.
    Ausgeschaltet, bis jemand sie einschaltet — eine Regelung, die von sich aus
    an Ladespannungen dreht, darf nicht die Vorgabe sein.
    """

    AN = {'harbor': {'hold_auto': True}}

    def _haltend(self, settings=None, stunden=4.0):
        """Regler im Halten, seit `stunden` eingependelt."""
        r = self._regler(settings=settings if settings is not None else dict(self.AN))
        r.update_soc(85, 0.0)                      # Wechsel ins Halten
        r._halte_seit    = time.monotonic() - stunden * 3600
        r._halte_geladen = True
        return r

    def test_aus_wenn_nicht_eingeschaltet(self):
        r = self._regler()
        r.update_soc(85, 0.0)
        r._halte_seit = time.monotonic() - 10 * 3600
        r.update_soc(85, 0.0)
        self.assertIsNone(r._state['hold_learned_v'])
        self.assertAlmostEqual(r._haltespannung(),
                               cc._DEFAULT_SETTINGS['harbor']['hold_voltage'], places=3)

    def test_zeichenkette_zaehlt_nicht_als_ja(self):
        # 'false' waere in Python wahr — das darf die Ermittlung nicht starten.
        r = self._haltend({'harbor': {'hold_auto': 'false'}})
        r.update_soc(85, 0.0)
        self.assertIsNone(r._state['hold_learned_v'])

    def test_ueber_dem_ziel_geht_die_spannung_runter(self):
        r = self._haltend()
        start = r._haltespannung()
        self.assertTrue(r.update_soc(85, 0.0))
        self.assertLess(r._state['hold_learned_v'], start)

    def test_unter_dem_ziel_geht_die_spannung_hoch(self):
        r = self._haltend()
        start = r._haltespannung()
        # 78 liegt im Hystereseband (Ziel 80, Hysterese 3) → weiter halten,
        # aber unter dem Ziel.
        self.assertTrue(r.update_soc(78, 0.0))
        self.assertGreater(r._state['hold_learned_v'], start)

    def test_im_totband_passiert_nichts(self):
        r = self._haltend()
        self.assertFalse(r.update_soc(80.5, 0.0))
        self.assertIsNone(r._state['hold_learned_v'])

    def test_noch_nicht_lange_genug_gehalten(self):
        r = self._haltend(stunden=0.5)
        self.assertFalse(r.update_soc(85, 0.0))
        self.assertIsNone(r._state['hold_learned_v'])

    def test_nicht_eingependelt_wird_nicht_gemessen(self):
        # Fliesst noch Strom, sagt die Spannung nichts ueber den Ladezustand.
        r = self._haltend()
        self.assertFalse(r.update_soc(85, 12.0))
        self.assertIsNone(r._state['hold_learned_v'])

    def test_ohne_strom_wird_nicht_gemessen(self):
        r = self._haltend()
        self.assertFalse(r.update_soc(85, None))
        self.assertIsNone(r._state['hold_learned_v'])

    def test_aus_dem_band_gefallen_hebt_die_spannung(self):
        r = self._haltend()
        start = r._haltespannung()
        r.update_soc(70, 0.0)                      # unter Ziel minus Hysterese
        self.assertGreater(r._state['hold_learned_v'], start)

    def test_ohne_landstrom_wird_nichts_gelernt(self):
        # Faellt der Ladezustand, weil gar nicht geladen werden konnte, kann
        # die Haltespannung nichts dafuer.
        r = self._haltend()
        r._halte_geladen = False
        r.update_soc(70, -18.0)
        self.assertIsNone(r._state['hold_learned_v'])

    def test_hoechstens_ein_schritt_je_abstand(self):
        r = self._haltend()
        self.assertTrue(r.update_soc(85, 0.0))
        erster = r._state['hold_learned_v']
        r._halte_seit = time.monotonic() - 10 * 3600
        self.assertFalse(r.update_soc(85, 0.0))
        self.assertEqual(r._state['hold_learned_v'], erster)

    def test_nach_dem_abstand_geht_der_naechste_schritt(self):
        r = self._haltend()
        r.update_soc(85, 0.0)
        erster = r._state['hold_learned_v']
        r._state['hold_learn_last'] = (datetime.now() - timedelta(hours=48)).isoformat()
        r._halte_seit = time.monotonic() - 10 * 3600
        self.assertTrue(r.update_soc(85, 0.0))
        self.assertLess(r._state['hold_learned_v'], erster)

    def test_nie_ueber_die_absorptionsspannung(self):
        r = self._haltend({'harbor': {'hold_auto': True, 'absorption_v': 13.25,
                                      'float_v': 13.2, 'hold_voltage': 13.24}})
        for _ in range(20):
            r._state['hold_learn_last'] = None
            r._halte_seit = time.monotonic() - 10 * 3600
            r.update_soc(78, 0.0)
        self.assertLessEqual(r._haltespannung(), 13.25)

    def test_nie_unter_die_untergrenze(self):
        r = self._haltend({'harbor': {'hold_auto': True, 'hold_min_v': 13.1}})
        for _ in range(20):
            r._state['hold_learn_last'] = None
            r._halte_seit = time.monotonic() - 10 * 3600
            r.update_soc(95, 0.0)
        self.assertGreaterEqual(r._haltespannung(), 13.1)

    def test_untergrenze_ueber_obergrenze_gewinnt_die_obergrenze(self):
        # Kaeme aus dem Einstellungs-Endpunkt eine Untergrenze ueber der
        # Absorption, liesse sich die Obergrenze sonst umgehen.
        r = self._haltend({'harbor': {'hold_auto': True, 'absorption_v': 13.4,
                                      'float_v': 13.2, 'hold_min_v': 14.0}})
        self.assertLessEqual(r._haltespannung(), 13.4)

    def test_bei_abgeschaltetem_lader_wird_nicht_gelernt(self):
        r = self._haltend({'harbor': {'hold_auto': True, 'hold_mode': 'aus'}})
        self.assertFalse(r.update_soc(85, 0.0))
        self.assertIsNone(r._state['hold_learned_v'])

    def test_ermittelte_spannung_landet_im_profil(self):
        r = self._haltend()
        r.update_soc(85, 0.0)
        gelernt = r._state['hold_learned_v']
        for d in r.device_setpoints():
            self.assertAlmostEqual(d['absorption_v'], gelernt, places=3)
            self.assertAlmostEqual(d['float_v'],      gelernt, places=3)
        self.assertAlmostEqual(r.status()['hold_voltage_eff'], gelernt, places=3)


if __name__ == '__main__':
    unittest.main()
