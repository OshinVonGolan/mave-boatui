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


def hafenprofil(absorption, float_v, **hafen):
    """Einstellungen mit einem eigenen Hafen-Profil (Nummer 2)."""
    profile = [dict(pr) for pr in cc._DEFAULT_SETTINGS['profile']]
    profile[1] = {**profile[1], 'absorption_v': absorption, 'float_v': float_v}
    return {'profile': profile, 'harbor': {'profile_id': 2, **hafen}}


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

    def test_halten_ueber_ein_anderes_profil(self):
        # Die von Hand gepflegte Fassung: kein eigener Spannungswert, sondern
        # eines der fuenf Profile.
        profile = [dict(pr) for pr in cc._DEFAULT_SETTINGS['profile']]
        profile[2] = {**profile[2], 'absorption_v': 13.35, 'float_v': 13.25}
        r = self._regler(settings={'profile': profile,
                                   'harbor': {'hold_mode': 'profil', 'hold_profile_id': 3}})
        r.update_soc(85)
        for d in r.device_setpoints():
            self.assertIs(d['on'], True)
            self.assertAlmostEqual(d['absorption_v'], 13.35, places=3)
            self.assertAlmostEqual(d['float_v'],      13.25, places=3)

    def test_beim_halteprofil_wird_nichts_selbst_ermittelt(self):
        # Ein von Hand gewaehltes Profil soll nicht hinter dem Ruecken des
        # Eigners verschoben werden.
        r = self._regler(settings={'harbor': {'hold_mode': 'profil', 'hold_auto': True}})
        r.update_soc(85, 0.0)
        r._halte_seit    = time.monotonic() - 10 * 3600
        r._halte_geladen = True
        self.assertFalse(r.update_soc(85, 0.0))
        self.assertIsNone(r._state['hold_learned_v'])

    def test_unbekannte_halteart_faellt_auf_spannung_zurueck(self):
        r = self._regler(settings={'harbor': {'hold_mode': 'quatsch'}})
        r.update_soc(85)
        self.assertTrue(all(d['on'] is True for d in r.device_setpoints()))

    def test_unter_ziel_wird_geladen(self):
        r = self._regler()
        r.update_soc(60)
        lade = cc._DEFAULT_SETTINGS['profile'][1]['absorption_v']
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
        r = self._regler(settings=hafenprofil(13.4, 13.9))
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
        r.update_settings({'profile': [{**cc._DEFAULT_SETTINGS['profile'][1],
                                        'absorption_v': 14.0}]})
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
        r = self._haltend(hafenprofil(13.25, 13.2, hold_auto=True, hold_voltage=13.24))
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
        r = self._haltend(hafenprofil(13.4, 13.2, hold_auto=True, hold_min_v=14.0))
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


class Ladeprofile(Basis):
    """Fünf benannte Profile; Hafen und Vollladung verweisen darauf."""

    def test_hafen_nimmt_sein_profil(self):
        r = self._regler(settings=hafenprofil(13.9, 13.35))
        r.update_soc(60)
        werte = {d['id']: d for d in r.device_setpoints()}
        self.assertAlmostEqual(werte['mppt']['absorption_v'], 13.9,  places=3)
        self.assertAlmostEqual(werte['mppt']['float_v'],      13.35, places=3)

    def test_vollladung_nimmt_ihr_profil(self):
        profile = [dict(pr) for pr in cc._DEFAULT_SETTINGS['profile']]
        profile[2] = {**profile[2], 'absorption_v': 14.55, 'float_v': 13.6}
        r = self._regler({'mode': 'full'}, {'profile': profile, 'full': {'profile_id': 3}})
        for d in r.device_setpoints():
            self.assertAlmostEqual(d['absorption_v'], 14.55, places=3)
            self.assertAlmostEqual(d['float_v'],      13.6,  places=3)

    def test_andere_profilnummer_andere_spannung(self):
        r = self._regler(settings={'harbor': {'profile_id': 1}})
        r.update_soc(60)
        werte = {d['id']: d['absorption_v'] for d in r.device_setpoints()}
        self.assertAlmostEqual(werte['mppt'],
                               cc._DEFAULT_SETTINGS['profile'][0]['absorption_v'], places=3)

    def test_unbekannte_nummer_faellt_auf_das_erste_profil(self):
        # Nie None: der Aufrufer rechnet mit Spannungen, und ein Zahlendreher in
        # den Einstellungen darf die Ladung nicht anhalten.
        r = self._regler(settings={'harbor': {'profile_id': 99}})
        r.update_soc(60)
        werte = {d['id']: d['absorption_v'] for d in r.device_setpoints()}
        self.assertAlmostEqual(werte['mppt'],
                               cc._DEFAULT_SETTINGS['profile'][0]['absorption_v'], places=3)

    def test_erhaltung_ueber_absorption_wird_gekappt(self):
        r = self._regler(settings=hafenprofil(13.4, 13.9))
        self.assertEqual(r._profil_spannungen(2), (13.4, 13.4))

    def test_halteband_deckelt_auf_das_profil(self):
        r = self._regler(settings=hafenprofil(13.3, 13.2))
        self.assertLessEqual(r._halte_grenzen(14.0), 13.3)


class AlteStaendeUmstellen(Basis):
    """Vor dem Umbau trugen harbor und full ihre Spannungen selbst.

    Diese Werte sind vom Eigner eingestellt. Ohne Umstellung wuerde _deep_merge
    sie stumm verwerfen, weil es nur bekannte Schluessel behaelt.
    """

    ALT = {'harbor': {'absorption_v': 13.75, 'float_v': 13.25, 'target_soc': 85},
           'full':   {'absorption_v': 14.55, 'float_v': 13.45}}

    def test_alte_spannungen_landen_in_den_profilen(self):
        r = self._regler(settings=dict(self.ALT))
        self.assertAlmostEqual(r._profil(1)['absorption_v'], 14.55, places=3)
        self.assertAlmostEqual(r._profil(1)['float_v'],      13.45, places=3)
        self.assertAlmostEqual(r._profil(2)['absorption_v'], 13.75, places=3)
        self.assertAlmostEqual(r._profil(2)['float_v'],      13.25, places=3)

    def test_die_modi_verweisen_danach_darauf(self):
        r = self._regler(settings=dict(self.ALT))
        self.assertEqual(r._settings['harbor']['profile_id'], 2)
        self.assertEqual(r._settings['full']['profile_id'],   1)

    def test_andere_einstellungen_bleiben(self):
        r = self._regler(settings=dict(self.ALT))
        self.assertEqual(r._settings['harbor']['target_soc'], 85)

    def test_neuer_stand_wird_nicht_noch_einmal_umgestellt(self):
        profile = [dict(pr) for pr in cc._DEFAULT_SETTINGS['profile']]
        profile[0] = {**profile[0], 'absorption_v': 14.1}
        r = self._regler(settings={'profile': profile,
                                   'harbor': {'absorption_v': 99.0}})
        self.assertAlmostEqual(r._profil(1)['absorption_v'], 14.1, places=3)


class KaputteProfillisteWirdRepariert(Basis):

    def test_immer_fuenf_mit_den_nummern_eins_bis_fuenf(self):
        for kaputt in (None, [], 'Profile', 42, [{'kein': 'profil'}], [{'id': 'zwei'}]):
            r = self._regler(settings={'profile': kaputt})
            self.assertEqual([pr['id'] for pr in r._settings['profile']], [1, 2, 3, 4, 5])

    def test_gueltige_eintraege_bleiben_erhalten(self):
        r = self._regler(settings={'profile': [{'id': 3, 'name': ' Winter ',
                                                'absorption_v': 14.1, 'float_v': 13.4}]})
        pr = r._profil(3)
        self.assertEqual(pr['name'], 'Winter')
        self.assertAlmostEqual(pr['absorption_v'], 14.1, places=3)
        # Die uebrigen kommen aus der Vorgabe.
        self.assertEqual(r._profil(1)['name'], cc._DEFAULT_SETTINGS['profile'][0]['name'])

    def test_leerer_name_faellt_auf_die_vorgabe_zurueck(self):
        r = self._regler(settings={'profile': [{'id': 2, 'name': '   '}]})
        self.assertEqual(r._profil(2)['name'], cc._DEFAULT_SETTINGS['profile'][1]['name'])

    def test_langer_name_wird_gekuerzt(self):
        r = self._regler(settings={'profile': [{'id': 2, 'name': 'x' * 200}]})
        self.assertLessEqual(len(r._profil(2)['name']), cc._PROFIL_NAME_MAX)


class BalanceLauf(Basis):
    """Drei Phasen: entladen, langsam laden mit steigender Spannung, halten."""

    def _lauf(self, settings=None, von='harbor'):
        r = self._regler({'mode': von}, settings)
        r.set_mode('balance')
        return r

    def _ip43(self, r):
        return next(d for d in r.device_setpoints() if d['instance'] == 1)

    # ── Start ──────────────────────────────────────────────────────────────

    def test_start_merkt_sich_woher(self):
        for von in ('harbor', 'full'):
            r = self._lauf(von=von)
            self.assertEqual(r._state['balance_zurueck'], von)
            self.assertEqual(r._state['balance_phase'], 'entladen')

    def test_in_der_entladephase_sind_alle_lader_aus(self):
        r = self._lauf()
        self.assertTrue(all(d['on'] is False for d in r.device_setpoints()))

    def test_ueber_dem_startwert_wird_weiter_gewartet(self):
        r = self._lauf()
        self.assertFalse(r.update_soc(90, 0.0, 5.0, True))
        self.assertEqual(r._state['balance_phase'], 'entladen')

    def test_startwert_erreicht_beginnt_die_ladephase(self):
        r = self._lauf()
        self.assertTrue(r.update_soc(60, 0.0, 5.0, True))
        self.assertEqual(r._state['balance_phase'], 'laden')
        self.assertAlmostEqual(r._state['balance_spannung'],
                               cc._DEFAULT_SETTINGS['balance']['start_v'], places=3)

    def test_entladen_braucht_keinen_landstrom(self):
        # Die Lader sind ohnehin aus — ohne Landstrom faellt der Ladezustand
        # sogar schneller.
        r = self._lauf()
        self.assertTrue(r.update_soc(55, -8.0, 5.0, False))
        self.assertEqual(r._state['balance_phase'], 'laden')

    # ── Ladephase ──────────────────────────────────────────────────────────

    def _laden(self, settings=None):
        r = self._lauf(settings)
        r.update_soc(60, 0.0, 5.0, True)
        return r

    def test_ladephase_faehrt_konstantspannung_mit_kleinem_strom(self):
        r = self._laden()
        d = self._ip43(r)
        self.assertIs(d['on'], True)
        self.assertAlmostEqual(d['absorption_v'], d['float_v'], places=3)
        self.assertAlmostEqual(d['max_a'], cc._DEFAULT_SETTINGS['balance']['strom_a'], places=1)

    def test_gleiche_zellen_heben_die_spannung(self):
        r = self._laden()
        vorher = r._state['balance_spannung']
        r._schritt_mono = time.monotonic() - 3600
        self.assertTrue(r.update_soc(70, 5.0, 5.0, True))
        self.assertAlmostEqual(r._state['balance_spannung'],
                               vorher + cc._DEFAULT_SETTINGS['balance']['schritt_v'], places=3)

    def test_ungleiche_zellen_heben_nicht(self):
        r = self._laden()
        vorher = r._state['balance_spannung']
        r._schritt_mono = time.monotonic() - 3600
        self.assertFalse(r.update_soc(70, 5.0, 80.0, True))
        self.assertEqual(r._state['balance_spannung'], vorher)

    def test_ohne_zellwerte_wird_nicht_gehoben(self):
        # Der ganze Zweck ist, dem BMS Zeit zum Ausgleichen zu geben. Blind zu
        # steigern waere genau das Gegenteil.
        r = self._laden()
        vorher = r._state['balance_spannung']
        r._schritt_mono = time.monotonic() - 3600
        self.assertFalse(r.update_soc(70, 5.0, None, True))
        self.assertEqual(r._state['balance_spannung'], vorher)

    def test_mindestabstand_zwischen_zwei_schritten(self):
        r = self._laden()
        r._schritt_mono = time.monotonic() - 3600
        self.assertTrue(r.update_soc(70, 5.0, 5.0, True))
        self.assertFalse(r.update_soc(71, 5.0, 5.0, True))

    def test_spannung_bleibt_unter_der_obergrenze(self):
        r = self._laden()
        for _ in range(60):
            r._schritt_mono = time.monotonic() - 3600
            r.update_soc(70, 5.0, 5.0, True)
        self.assertLessEqual(r._state['balance_spannung'],
                             cc._DEFAULT_SETTINGS['balance']['max_v'])

    def test_ohne_landstrom_geht_es_nicht_weiter(self):
        r = self._laden()
        vorher = r._state['balance_spannung']
        r._schritt_mono = time.monotonic() - 3600
        self.assertFalse(r.update_soc(70, 0.0, 5.0, False))
        self.assertEqual(r._state['balance_spannung'], vorher)
        self.assertEqual(r._state['balance_phase'], 'laden')

    def test_ladestrom_nie_ueber_dem_geraetestrom(self):
        # Der Balance-Strom ist eine Begrenzung, keine Anhebung: der MPPT kann
        # 15 A, ein Balance-Strom von 40 A darf daraus keine 40 machen.
        r = self._laden({'balance': {'strom_a': 40.0}})
        werte = {d['id']: d['max_a'] for d in r.device_setpoints()}
        self.assertAlmostEqual(werte['mppt'], 15.0, places=1)

    # ── Halten und Abschluss ───────────────────────────────────────────────

    def test_ziel_erreicht_geht_ins_halten(self):
        r = self._laden()
        r.update_soc(100, 2.0, 5.0, True)
        self.assertEqual(r._state['balance_phase'], 'halten')

    def test_nach_der_haltezeit_zurueck_woher_es_kam(self):
        r = self._lauf(von='full')
        r.update_soc(60, 0.0, 5.0, True)
        r.update_soc(100, 2.0, 5.0, True)
        r._phase_mono = time.monotonic() - 3 * 3600
        self.assertTrue(r.update_soc(100, 1.0, 5.0, True))
        self.assertEqual(r._state['mode'], 'full')
        self.assertIsNotNone(r._state['last_balance'])
        self.assertIsNone(r._state['balance_phase'])

    def test_danach_bekommen_die_lader_ihren_strom_zurueck(self):
        r = self._lauf()
        r.update_soc(60, 0.0, 5.0, True)
        r.update_soc(100, 2.0, 5.0, True)
        r._phase_mono = time.monotonic() - 3 * 3600
        r.update_soc(100, 1.0, 5.0, True)
        werte = {d['id']: d['max_a'] for d in r.device_setpoints()}
        self.assertAlmostEqual(werte['ip43'], 50.0, places=1)
        self.assertAlmostEqual(werte['mppt'], 15.0, places=1)

    def test_deckel_bricht_ab_ohne_das_datum_zu_setzen(self):
        # Sonst gaelte ein abgebrochener Lauf als durchbalancierte Bank, und der
        # naechste faellige waere einen Monat spaeter.
        r = self._lauf()
        r._balance_mono = time.monotonic() - 100 * 3600
        self.assertTrue(r.update_soc(70, 0.0, 5.0, True))
        self.assertEqual(r._state['mode'], 'harbor')
        self.assertIsNone(r._state['last_balance'])

    def test_verlassen_des_modus_raeumt_auf(self):
        r = self._lauf()
        r.update_soc(60, 0.0, 5.0, True)
        r.set_mode('harbor')
        self.assertIsNone(r._state['balance_phase'])
        self.assertIsNone(r._state['balance_spannung'])
        self.assertIsNone(r._state['balance_zurueck'])


if __name__ == '__main__':
    unittest.main()
