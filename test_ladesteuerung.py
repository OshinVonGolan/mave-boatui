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
from datetime import date, datetime, timedelta
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
        """Regler im Halten, seit `stunden` eingependelt, MIT Landstrom."""
        r = self._regler(settings=settings if settings is not None else dict(self.AN))
        r.update_soc(85, 0.0, None, True)          # Wechsel ins Halten
        r._halte_seit = time.monotonic() - stunden * 3600
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
        # aber unter dem Ziel. Nach oben nur mit Landstrom.
        self.assertTrue(r.update_soc(78, 0.0, None, True))
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
        r.update_soc(70, 0.0, None, True)          # unter Ziel minus Hysterese
        self.assertGreater(r._state['hold_learned_v'], start)

    def test_ohne_landstrom_wird_nichts_gelernt(self):
        """Faellt der Ladezustand, weil gar nicht geladen werden konnte, kann
        die Haltespannung nichts dafuer.

        Diese Probe lief frueher am Fehler vorbei: sie setzte den Merker
        _halte_geladen von Hand auf False und durchlief den Setzpfad deshalb
        nie. Der Merker wurde in Wahrheit gesetzt, sobald der Strom KLEIN war —
        was bei einer entladenden Bank mit Grundlast immer zutrifft. Jetzt wird
        der ganze Weg ohne Landstrom durchlaufen.
        """
        r = self._regler(settings=dict(self.AN))
        r.update_soc(85, -1.5, None, False)        # Halten beginnt, kein Landstrom
        r._halte_seit = time.monotonic() - 10 * 3600
        r.update_soc(78, -1.5, None, False)        # im Band, unter dem Ziel
        r.update_soc(70, -1.5, None, False)        # aus dem Band gefallen
        self.assertIsNone(r._state['hold_learned_v'])

    def test_ohne_landstrom_geht_es_trotzdem_nach_unten(self):
        # Weniger Spannung kann nie schaden — die Sperre gilt nur nach oben.
        r = self._regler(settings=dict(self.AN))
        r.update_soc(90, 0.0, None, False)
        r._halte_seit = time.monotonic() - 10 * 3600
        self.assertTrue(r.update_soc(90, 0.0, None, False))
        self.assertLess(r._state['hold_learned_v'],
                        cc._DEFAULT_SETTINGS['harbor']['hold_voltage'])

    def test_fremder_ladestrom_regelt_nicht_nach(self):
        """Der Shunt-Strom der Bank ist nicht der Strom unserer Lader.

        Laeuft die Maschine oder der nicht geregelte Orion, steht dort
        stundenlang ein grosser positiver Strom. Als "unser Lader drueckt"
        gelesen, faehrt er die Haltespannung binnen ein bis zwei Motortagen an
        die Untergrenze.
        """
        r = self._regler(settings=dict(self.AN))
        r.update_soc(85, 0.0, None, True)
        r._laedt_seit = time.monotonic() - 3600
        # 40 A am Shunt, aber unsere Lader schicken nichts.
        self.assertFalse(r.update_soc(85, 40.0, None, True, None, None, 0.0))
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


class BalanceStartetVonSelbst(Basis):
    """Drei Bedingungen, alle notwendig: eingeschaltet, faellig, Landstrom.

    Geprueft wird der Modus, nicht der Rueckgabewert von update_soc: der sagt
    nur "neue Sollwerte noetig", und bei 90 % schaltet auch die Hafen-Hysterese
    ins Halten.
    """

    AN = {'balance': {'auto': True}}

    def test_aus_wenn_nicht_eingeschaltet(self):
        r = self._regler()
        r.update_soc(90, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'harbor')

    def test_faellig_und_landstrom_startet(self):
        # last_balance fehlt = noch nie balanciert = faellig.
        r = self._regler(settings=dict(self.AN))
        r.update_soc(90, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'balance')
        self.assertEqual(r._state['balance_zurueck'], 'harbor')

    def test_ohne_landstrom_wird_nicht_gestartet(self):
        # Der Lauf laesst die Bank zuerst leerlaufen. Ohne Steckdose hiesse das,
        # sie zu entladen ohne sie danach fuellen zu koennen.
        r = self._regler(settings=dict(self.AN))
        r.update_soc(90, 0.0, 5.0, False)
        self.assertEqual(r._state['mode'], 'harbor')

    def test_unbekannter_landstrom_startet_nicht(self):
        r = self._regler(settings=dict(self.AN))
        r.update_soc(90, 0.0, 5.0, None)
        self.assertEqual(r._state['mode'], 'harbor')

    def test_noch_nicht_faellig_startet_nicht(self):
        r = self._regler({'last_balance': date.today().isoformat()}, dict(self.AN))
        r.update_soc(90, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'harbor')

    def test_nach_einem_abbruch_gilt_eine_sperre(self):
        # Sonst wirft der automatische Start den abgebrochenen Lauf sofort
        # wieder an — eine Schleife aus Abbrechen und Neustarten.
        r = self._regler(settings=dict(self.AN))
        r.update_soc(90, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'balance')
        r._balance_mono = time.monotonic() - 100 * 3600
        r.update_soc(70, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'harbor')
        self.assertIsNotNone(r._state['balance_sperre'])
        r.update_soc(90, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'harbor')

    def test_nach_der_sperre_geht_es_wieder(self):
        r = self._regler({'balance_sperre': (datetime.now() - timedelta(hours=48)).isoformat()},
                         dict(self.AN))
        r.update_soc(90, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'balance')


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


class VerlaufswerteFuersLogbuch(Basis):
    """Was von der Ladesteuerung in den Verlauf geht.

    Der Verlauf traegt ausschliesslich Zahlen — gemittelt, verdichtet und
    gezeichnet wird nur, was sich rechnen laesst. Ein versehentlich
    mitgegebener String oder ein bool faellt in der Minutenmittelung
    stillschweigend heraus, und im Logbuch fehlt dann eine Kurve, ohne dass
    irgendwo ein Fehler auftaucht. Deshalb prueft der erste Test die FORM.
    """

    def test_alles_sind_zahlen(self):
        for modus in ('harbor', 'full'):
            for feld, wert in self._regler({'mode': modus}).verlaufswerte().items():
                self.assertIsInstance(wert, (int, float), feld)
                self.assertNotIsInstance(wert, bool, feld)

    def test_der_modus_steht_als_zahl_darin(self):
        for modus, zahl in (('harbor', 1), ('full', 2)):
            self.assertEqual(self._regler({'mode': modus}).verlaufswerte()['ld_modus'], zahl)

    def test_je_eingeschaltetem_geraet_zwei_spannungen(self):
        w = self._regler({'mode': 'full'}).verlaufswerte()
        # ip43 und mppt sind an, orion ist aus — ein ausgeschaltetes Geraet
        # bekommt keine Sollwerte und darf deshalb auch keine Kurve haben.
        self.assertEqual({f for f in w if f.startswith(('ld_abs_', 'ld_flt_'))},
                         {'ld_abs_ip43', 'ld_flt_ip43', 'ld_abs_mppt', 'ld_flt_mppt'})

    def test_hafen_felder_gibt_es_nur_im_hafen(self):
        # Ziel-SOC, Halten und Solar-Vorrang gelten allein im Hafen-Modus. In
        # der Vollladung eine Linie zu zeichnen hiesse, etwas zu behaupten, was
        # gerade nicht gilt — die Kurve hat dort eine Luecke, und das stimmt.
        w = self._regler({'mode': 'full'}).verlaufswerte()
        for feld in ('ld_ziel_soc', 'ld_halten', 'ld_an', 'ld_vorrang'):
            self.assertNotIn(feld, w)

    def test_beim_laden_wirkt_der_solar_vorrang(self):
        r = self._regler({'mode': 'harbor'},
                         hafenprofil(13.8, 13.3, target_soc=80, soc_hysteresis_pct=3))
        r.update_soc(50)
        w = r.verlaufswerte()
        self.assertEqual((w['ld_halten'], w['ld_an'], w['ld_vorrang']), (0, 1, 1))
        # Und er steht nicht nur als Eins da, sondern ist an den Sollwerten
        # abzulesen: der Landlader liegt um den Offset tiefer als das Solar.
        self.assertLess(w['ld_abs_ip43'], w['ld_abs_mppt'])

    def test_am_ziel_faellt_der_vorrang_weg(self):
        r = self._regler({'mode': 'harbor'},
                         hafenprofil(13.8, 13.3, target_soc=80, soc_hysteresis_pct=3))
        r.update_soc(85)
        w = r.verlaufswerte()
        self.assertEqual((w['ld_halten'], w['ld_vorrang']), (1, 0))
        # Beim Halten gilt fuer alle derselbe Wert — sonst laege der Landlader
        # unter der Ruhespannung und waere faktisch aus.
        self.assertEqual(w['ld_abs_ip43'], w['ld_abs_mppt'])

    def test_ohne_vorrang_keine_eins(self):
        r = self._regler({'mode': 'harbor'},
                         {**hafenprofil(13.8, 13.3, target_soc=80),
                          'solar_priority_offset_v': 0.0})
        r.update_soc(50)
        self.assertEqual(r.verlaufswerte()['ld_vorrang'], 0)

    def test_das_ziel_steht_im_verlauf(self):
        r = self._regler({'mode': 'harbor'}, hafenprofil(13.8, 13.3, target_soc=75))
        self.assertEqual(r.verlaufswerte()['ld_ziel_soc'], 75.0)

    def test_rueckmeldung_nur_wenn_gelesen(self):
        # Ohne Rueckmeldung des Laders fehlt sie — eine Null hiesse 'null Volt'.
        r = self._regler({'mode': 'harbor'})
        self.assertNotIn('ld_ist_abs', r.verlaufswerte())
        with mock.patch.object(cc, 'write_json'):
            r.update_actual_setpoints(14.2, 13.5)
        w = r.verlaufswerte()
        self.assertEqual((w['ld_ist_abs'], w['ld_ist_flt']), (14.2, 13.5))

    def test_feldnamen_bleiben_harmlos(self):
        # Die Geraete-Kennungen kommen aus den Einstellungen und koennen alles
        # enthalten, was durch JSON passt. Im Verlauf werden daraus
        # Spaltennamen — in CSV-Export und Graphen.
        self.assertEqual(cc._feldname('Smart IP43!'), 'smartip4')
        self.assertEqual(cc._feldname(''), 'lader')
        self.assertEqual(cc._feldname('../etc'), 'etc')


class GelernteKennlinie(Basis):
    """Bei welcher Spannung sich welcher Ladezustand einpendelt.

    Bei jedem Einpendeln faellt ein Messpunkt an. Frueher wurde er einmal mit
    dem Ziel verglichen und weggeworfen; jetzt sammelt er sich in einer Tabelle
    ueber den Ladezustand, aus der sich fuer JEDES Ziel die Spannung ablesen
    laesst.
    """

    def _punkt(self, r, soc, v, temp=None, mal=1):
        """Einen Messpunkt aufnehmen, ohne auf Ruhezeit und Abstand zu warten."""
        for _ in range(mal):
            r._ruhig_seit = time.monotonic() - 3600
            r._state['kenn_letzte'] = None
            r._kenn_beobachten(soc, v, 0.0, temp)

    def _kurve(self, r, punkte):
        for soc, v in punkte:
            self._punkt(r, soc, v, mal=cc._KENN_MIN_N)
        return r

    # ── Aufnehmen ──────────────────────────────────────────────────────────

    def test_ein_einzelner_ruhiger_messwert_zaehlt_nicht(self):
        # Der Strom geht beim Vorzeichenwechsel durch null — ein Punkt aus
        # diesem Moment saehe wie Ruhe aus, waehrend die Bank umschlaegt.
        r = self._regler()
        r._kenn_beobachten(80, 13.28, 0.0, None)
        self.assertEqual(r._state['kennpunkte'], [])

    def test_nach_der_ruhezeit_wird_aufgenommen(self):
        r = self._regler()
        self._punkt(r, 80, 13.28)
        self.assertEqual(len(r._state['kennpunkte']), 1)
        self.assertEqual(r._state['kennpunkte'][0]['soc'], 80)
        self.assertAlmostEqual(r._state['kennpunkte'][0]['v'], 13.28, places=3)

    def test_strom_beendet_die_ruhe(self):
        r = self._regler()
        r._ruhig_seit = time.monotonic() - 3600
        r._kenn_beobachten(80, 13.28, 25.0, None)     # laedt gerade
        self.assertIsNone(r._ruhig_seit)
        self.assertEqual(r._state['kennpunkte'], [])

    def test_ladezustand_faellt_in_faecher(self):
        r = self._regler()
        self._punkt(r, 78, 13.28)
        self._punkt(r, 81, 13.30)
        # 78 und 81 runden beide auf das Fach 80.
        self.assertEqual([p['soc'] for p in r._state['kennpunkte']], [80])
        self.assertEqual(r._state['kennpunkte'][0]['n'], 2)

    def test_der_mittelwert_folgt_nach(self):
        r = self._regler()
        self._punkt(r, 80, 13.20)
        self._punkt(r, 80, 13.40)
        v = r._state['kennpunkte'][0]['v']
        self.assertGreater(v, 13.20)
        self.assertLess(v, 13.40)

    def test_abstand_verhindert_eine_flut(self):
        # Die Tabelle landet auf der SD-Karte; eine stille Nacht darf sie nicht
        # hundertmal anfassen.
        r = self._regler()
        self._punkt(r, 80, 13.28)
        r._ruhig_seit = time.monotonic() - 3600
        r._kenn_beobachten(80, 13.28, 0.0, None)      # sofort danach
        self.assertEqual(r._state['kennpunkte'][0]['n'], 1)

    # ── Ablesen ────────────────────────────────────────────────────────────

    def test_zwischen_zwei_faechern_wird_interpoliert(self):
        r = self._kurve(self._regler(), [(70, 13.20), (90, 13.40)])
        self.assertAlmostEqual(r._kenn_spannung(80), 13.30, places=3)

    def test_ausserhalb_des_gemessenen_bereichs_kein_wert(self):
        # Eine verlaengerte Gerade waere geraten, und geraten wird hier nicht.
        r = self._kurve(self._regler(), [(70, 13.20), (90, 13.40)])
        self.assertIsNone(r._kenn_spannung(50))
        self.assertIsNone(r._kenn_spannung(100))

    def test_zu_wenige_beobachtungen_zaehlen_nicht(self):
        r = self._regler()
        self._punkt(r, 70, 13.20, mal=cc._KENN_MIN_N - 1)
        self._punkt(r, 90, 13.40, mal=cc._KENN_MIN_N - 1)
        self.assertIsNone(r._kenn_spannung(80))

    # ── Wirkung auf die Regelung ───────────────────────────────────────────

    def test_haltespannung_kommt_aus_der_kennlinie(self):
        r = self._kurve(self._regler(settings={'harbor': {'hold_auto': True}}),
                        [(70, 13.20), (90, 13.40)])
        self.assertAlmostEqual(r._haltespannung(), 13.30, places=3)
        self.assertEqual(r._haltespannung_quelle(), 'kennlinie')

    def test_anderes_ziel_sofort_andere_spannung(self):
        # Das ist der eigentliche Gewinn: ein geaendertes Ziel muss nicht neu
        # erlaufen werden.
        r = self._kurve(self._regler(settings={'harbor': {'hold_auto': True}}),
                        [(70, 13.20), (90, 13.40)])
        self.assertAlmostEqual(r._haltespannung(), 13.30, places=3)
        r.update_settings({'harbor': {'target_soc': 75}})
        self.assertAlmostEqual(r._haltespannung(), 13.25, places=3)

    def test_ohne_kennlinie_gilt_die_eingestellte_spannung(self):
        r = self._regler(settings={'harbor': {'hold_auto': True}})
        self.assertAlmostEqual(r._haltespannung(),
                               cc._DEFAULT_SETTINGS['harbor']['hold_voltage'], places=3)
        self.assertEqual(r._haltespannung_quelle(), 'manuell')

    def test_mit_kennlinie_wird_nicht_mehr_geschrittelt(self):
        # Sie korrigiert sich ueber neue Beobachtungen von selbst; jeder Schritt
        # waere ein zusaetzlicher Schreibvorgang ins Flash des Laders.
        r = self._kurve(self._regler(settings={'harbor': {'hold_auto': True}}),
                        [(70, 13.20), (90, 13.40)])
        r.update_soc(85, 0.0)
        r._halte_seit = time.monotonic() - 10 * 3600
        self.assertFalse(r.update_soc(85, 0.0))
        self.assertIsNone(r._state['hold_learned_v'])

    # ── Schrittweite und Bremsen ───────────────────────────────────────────

    def _haltend(self, settings=None):
        r = self._regler(settings=settings or {'harbor': {'hold_auto': True}})
        r.update_soc(85, 0.0)
        r._halte_seit    = time.monotonic() - 10 * 3600
        r._halte_geladen = True
        return r

    def test_grosser_fehler_grosser_schritt(self):
        klein = self._haltend()
        klein.update_soc(82, 0.0)
        gross = self._haltend()
        gross.update_soc(99, 0.0)
        start = cc._DEFAULT_SETTINGS['harbor']['hold_voltage']
        self.assertGreater(start - gross._state['hold_learned_v'],
                           start - klein._state['hold_learned_v'])

    def test_schrittweite_bleibt_gedeckelt(self):
        r = self._haltend()
        r.update_soc(100, 0.0)
        start = cc._DEFAULT_SETTINGS['harbor']['hold_voltage']
        self.assertLessEqual(start - r._state['hold_learned_v'],
                             cc._DEFAULT_SETTINGS['harbor']['hold_step_max_v'] + 1e-9)

    def test_hoechstens_so_viele_schritte_am_tag(self):
        # Jede Aenderung ist ein Schreibvorgang in das Flash des Ladegeraets.
        r = self._haltend()
        schritte = 0
        for _ in range(30):
            r._state['hold_learn_last'] = None
            r._halte_seit = time.monotonic() - 10 * 3600
            if r.update_soc(85, 0.0):
                schritte += 1
        self.assertEqual(schritte, cc._DEFAULT_SETTINGS['harbor']['hold_max_pro_tag'])

    def test_druckender_lader_wird_schnell_nachgeregelt(self):
        # Das schnelle Signal: der Strom UNSERER Lader sagt in Minuten, was der
        # Ladezustand erst nach Stunden zeigt.
        r = self._regler(settings={'harbor': {'hold_auto': True}})
        r.update_soc(85, 0.0, None, True)
        r._laedt_seit = time.monotonic() - 3600
        self.assertTrue(r.update_soc(85, 0.0, None, True, None, None, 6.0))
        self.assertLess(r._state['hold_learned_v'],
                        cc._DEFAULT_SETTINGS['harbor']['hold_voltage'])

    def test_kurzes_druecken_reicht_nicht(self):
        r = self._regler(settings={'harbor': {'hold_auto': True}})
        r.update_soc(85, 0.0, None, True)
        self.assertFalse(r.update_soc(85, 0.0, None, True, None, None, 6.0))
        self.assertIsNone(r._state['hold_learned_v'])

    # ── Zuruecksetzen ──────────────────────────────────────────────────────

    def test_zuruecksetzen_raeumt_alles_weg(self):
        r = self._kurve(self._regler(settings={'harbor': {'hold_auto': True}}),
                        [(70, 13.20), (90, 13.40)])
        r._state['hold_learned_v'] = 13.11
        r.kennlinie_zuruecksetzen()
        self.assertEqual(r._state['kennpunkte'], [])
        self.assertIsNone(r._state['hold_learned_v'])
        self.assertIsNone(r._state['kenn_letzte'])
        # Von Hand Eingestelltes bleibt.
        self.assertAlmostEqual(r._settings['harbor']['hold_voltage'],
                               cc._DEFAULT_SETTINGS['harbor']['hold_voltage'], places=3)
        self.assertEqual(r._haltespannung_quelle(), 'manuell')


class AusDerPruefungHervorgegangen(Basis):
    """Proben zu Defekten, die eine adversariale Pruefung am 06.09.2026 fand.

    Jeder davon war ein Ablauf, der am Boot echten Schaden angerichtet haette,
    und keiner wurde von den vorhandenen Tests erfasst.
    """

    def test_balance_uhren_werden_beim_laden_neu_gestellt(self):
        """Der Pi hat keine Echtzeituhr.

        Startet er mitten im Lauf ohne Netz neu und steht seine Uhr HINTER dem
        gespeicherten Beginn, wurde die Laufzeit auf 0 geklemmt — dauerhaft,
        weil im laufenden Modus nie wieder eine monotone Uhr gesetzt wurde. Der
        Sicherheitsdeckel war damit fuer immer wirkungslos und die Bank haette
        unbegrenzt auf Konstantspannung gehangen.
        """
        zukunft = (datetime.now() + timedelta(hours=5)).isoformat()
        r = self._regler({'mode': 'balance', 'balance_phase': 'laden',
                          'balance_start': zukunft, 'balance_spannung': 14.4})
        self.assertIsNotNone(r._balance_mono)
        self.assertIsNotNone(r._phase_mono)
        # Und der Deckel greift wieder, gerechnet ab dem Neustart.
        r._balance_mono = time.monotonic() - 100 * 3600
        r.update_soc(95, 3.0, 2.0, True)
        self.assertNotEqual(r._state['mode'], 'balance')

    def test_haltephase_laeuft_auch_nach_neustart_ab(self):
        zukunft = (datetime.now() + timedelta(hours=5)).isoformat()
        r = self._regler({'mode': 'balance', 'balance_phase': 'halten',
                          'balance_start': zukunft, 'balance_phase_seit': zukunft,
                          'balance_zurueck': 'harbor'})
        r._phase_mono = time.monotonic() - 5 * 3600
        r.update_soc(100, 1.0, 2.0, True)
        self.assertEqual(r._state['mode'], 'harbor')

    def test_von_hand_verlassener_lauf_startet_nicht_sofort_neu(self):
        # Ohne Sperre wirft der automatische Start den gerade verlassenen Lauf
        # binnen Sekunden wieder an — er waere nicht verlassbar.
        r = self._regler(settings={'balance': {'auto': True}})
        r.update_soc(90, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'balance')
        r.set_mode('harbor')
        self.assertIsNotNone(r._state['balance_sperre'])
        r.update_soc(90, 0.0, 5.0, True)
        self.assertEqual(r._state['mode'], 'harbor')

    def test_kaputter_einstellungsabschnitt_legt_nichts_lahm(self):
        """Wo die Vorgabe ein Objekt ist, muss auch der gespeicherte Wert eines sein.

        Ein "harbor": "kaputt" in der Zustandsdatei haette die ganze
        Ladesteuerung lahmgelegt — jeder Zugriff der Form
        settings.get('harbor', {}).get(...) faellt auf einer Zeichenkette um,
        und weil der Wert gespeichert wird, nach jedem Neustart wieder.
        """
        for kaputt in ('kaputt', 42, [1, 2], None):
            r = self._regler(settings={'harbor': kaputt})
            self.assertIsInstance(r._settings['harbor'], dict)
            r.update_soc(85, 0.0, None, True)          # muss einfach laufen
            self.assertTrue(r.device_setpoints())

    def test_halbe_registerstufe_loest_kein_nachsetzen_aus(self):
        """13,055 V wird im Lader zu 13,06 V — das ist keine Abweichung.

        Verglichen wurde frueher mit einer Gleitkomma-Toleranz von genau einer
        halben Stufe, und dort faellt der Abstand auf die falsche Seite. Die
        Folge war nicht nur ueberfluessiges Schreiben ins Flash, sondern ein
        dauerhaft totes Sicherheitsnetz: nach drei Versuchen gibt der Regler
        auf und meldet eine spaetere ECHTE Verstellung nicht mehr.
        """
        profile = [dict(pr) for pr in cc._DEFAULT_SETTINGS['profile']]
        profile[1] = {**profile[1], 'absorption_v': 13.355, 'float_v': 13.055}
        r = self._regler(settings={'profile': profile,
                                   'harbor': {'profile_id': 2, 'target_soc': 0}})
        r.update_soc(60, 0.0, None, True)
        soll = next(d for d in r.device_setpoints() if d['instance'] == 1)
        # Was der Lader nach der Rundung auf 0,01 V zurueckmeldet.
        ist_abs = cc._register_stufe(soll['absorption_v']) / 100.0
        ist_flt = cc._register_stufe(soll['float_v']) / 100.0
        r.update_actual_setpoints(ist_abs, ist_flt)
        self.assertFalse(r._nachsetzen)

    def test_echte_verstellung_wird_weiter_erkannt(self):
        r = self._regler()
        r.update_soc(60, 0.0, None, True)
        soll = next(d for d in r.device_setpoints() if d['instance'] == 1)
        r.update_actual_setpoints(soll['absorption_v'] - 0.2, soll['float_v'])
        self.assertTrue(r._nachsetzen)

    def test_solar_versatz_gilt_nicht_als_fremde_verstellung(self):
        """Zurueckgelesen wird der IP43, und der bekommt beim Laden den
        Solar-Versatz abgezogen. Vorher stand waehrend des ganz normalen
        Hafen-Ladens dauerhaft "Extern geaendert" in der Oberflaeche.
        """
        r = self._regler()
        r.update_soc(60, 0.0, None, True)
        soll = next(d for d in r.device_setpoints() if d['instance'] == 1)
        r.update_actual_setpoints(soll['absorption_v'], soll['float_v'])
        self.assertEqual(r.status()['preset_match'], 'harbor')


if __name__ == '__main__':
    unittest.main()
