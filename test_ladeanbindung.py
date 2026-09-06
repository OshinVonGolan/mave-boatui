"""Die Anbindung der Ladesteuerung an den CAN-Bus.

Alle Proben hier gehen auf Defekte zurueck, die eine adversariale Pruefung am
06.09.2026 gefunden hat — jeder davon haette am Boot echten Schaden angerichtet,
und keiner wurde von den vorhandenen Tests erfasst.

Aufruf:

    ./venv/bin/python -m unittest test_ladeanbindung -v
"""
import os
import unittest

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

import main                                    # noqa: E402


class Betriebsart(unittest.TestCase):
    """0x0200 kennt 1 = an und 4 = aus. Eine 0 kennt es nicht.

    Hier stand zum Abschalten eine 0. Der Lader quittiert die mit Fehlerstatus,
    und genau diese Antwort verwirft der Auswerter im Gateway — es wurde nicht
    einmal geloggt, dass das Abschalten nie ankam. Die Oberflaeche zeigte
    "Lader aus", waehrend die Bank weiter auf Ladespannung hing.
    """

    def test_werte_sind_die_am_geraet_gemessenen(self):
        self.assertEqual(main._MODUS_AN, 1)
        self.assertEqual(main._MODUS_AUS, 4)

    def test_abschalten_schickt_vier(self):
        gesendet = []
        echt = main.can_if.send_charger_register
        main.can_if.send_charger_register = (
            lambda reg, val, inst=1, size=2: gesendet.append((reg, val, inst, size)) or True)
        try:
            main._apply_charger_setpoints([
                {'instance': 1, 'label': 'IP43', 'on': False,
                 'absorption_v': 13.5, 'float_v': 13.3, 'max_a': None}])
        finally:
            main.can_if.send_charger_register = echt
        modus = [g for g in gesendet if g[0] == main._REG_DEVICE_MODE]
        self.assertEqual([g[1] for g in modus], [main._MODUS_AUS])


class Zellspreizung(unittest.TestCase):
    """Ohne belastbare Zellwerte gibt es keine Spreizung.

    CanState.to_dict() setzt die Werte einer ausgefallenen Quelle bewusst NICHT
    auf None, sondern laesst nur '_age_s' wachsen. Ohne Fristpruefung stuende
    hier nach einem BMS-Ausfall weiter die letzte, typischerweise winzige
    Spreizung — und der Balance-Lauf haette sie als "Zellen gleich" gelesen und
    die Spannung ohne lebende Zellueberwachung hochgefahren.
    """

    def _bms(self, alter, spannungen):
        return {'bms': {'_age_s': alter,
                        'cells': [{'voltage': v} for v in spannungen]}}

    def test_frische_werte_ergeben_die_spreizung(self):
        self.assertAlmostEqual(
            main._zellspreizung_mv(self._bms(2.0, [3.31, 3.32, 3.30, 3.31])),
            20.0, places=1)

    def test_alte_werte_zaehlen_nicht(self):
        self.assertIsNone(main._zellspreizung_mv(
            self._bms(600.0, [3.31, 3.32, 3.30, 3.31])))

    def test_ohne_altersangabe_zaehlt_nicht(self):
        self.assertIsNone(main._zellspreizung_mv(
            {'bms': {'cells': [{'voltage': 3.3}, {'voltage': 3.31}]}}))

    def test_eine_stumme_zelle_macht_die_spreizung_unbrauchbar(self):
        # Ausgerechnet die driftende koennte die fehlende sein.
        d = self._bms(2.0, [3.31, 3.32, 3.30])
        d['bms']['cells'].append({'voltage': None})
        self.assertIsNone(main._zellspreizung_mv(d))


class LaderStrom(unittest.TestCase):
    """Der Strom UNSERER Lader, nicht der Shunt-Strom der Bank.

    Der Shunt enthaelt auch Lichtmaschine und Verbraucher. Als "unser Lader
    drueckt" gelesen, faehrt er die Haltespannung beim Motoren an die
    Untergrenze.
    """

    def test_summiert_die_frischen_gruppen(self):
        d = {'charger': {'_age_s': 1.0, 'dc_current': 12.0},
             'solar':   {'_age_s': 2.0, 'dc_current': 4.5},
             'orion':   {'_age_s': 3.0, 'dc_current': 0.0}}
        self.assertAlmostEqual(main._lader_strom_a(d), 16.5, places=2)

    def test_alte_gruppen_zaehlen_nicht(self):
        d = {'charger': {'_age_s': 900.0, 'dc_current': 40.0},
             'solar':   {'_age_s': 1.0,   'dc_current': 2.0}}
        self.assertAlmostEqual(main._lader_strom_a(d), 2.0, places=2)

    def test_ohne_jede_frische_gruppe_unbekannt(self):
        # Unbekannt darf keinen Regeleingriff ausloesen.
        self.assertIsNone(main._lader_strom_a(
            {'charger': {'_age_s': 900.0, 'dc_current': 40.0}}))
        self.assertIsNone(main._lader_strom_a({}))


class Strombegrenzung(unittest.TestCase):
    """Der Merker darf nur zaehlen, was auch abgegangen ist.

    Sonst gilt ein Strom als gesetzt, den der Lader nie gesehen hat — und weil
    der Merker den naechsten Versuch unterdrueckt, bliebe er es fuer immer.
    """

    def setUp(self):
        main._strom_geschrieben.clear()
        self.echt = main.can_if.send_charger_register

    def tearDown(self):
        main.can_if.send_charger_register = self.echt
        main._strom_geschrieben.clear()

    def test_erfolgreicher_versuch_wird_gemerkt(self):
        main.can_if.send_charger_register = lambda *a, **k: True
        main._strom_setzen({'instance': 1, 'label': 'IP43', 'max_a': 10.0})
        self.assertEqual(main._strom_geschrieben.get(1), 100)

    def test_gescheiterter_versuch_wird_nicht_gemerkt(self):
        main.can_if.send_charger_register = lambda *a, **k: False
        main._strom_setzen({'instance': 1, 'label': 'IP43', 'max_a': 10.0})
        self.assertNotIn(1, main._strom_geschrieben)

    def test_ohne_bus_wird_nicht_gemerkt(self):
        # send_charger_register gibt bei fehlendem Bus None zurueck.
        main.can_if.send_charger_register = lambda *a, **k: None
        main._strom_setzen({'instance': 1, 'label': 'IP43', 'max_a': 10.0})
        self.assertNotIn(1, main._strom_geschrieben)

    def test_nach_einem_fehlschlag_wird_erneut_versucht(self):
        versuche = []
        main.can_if.send_charger_register = lambda *a, **k: (versuche.append(a) or False)
        for _ in range(3):
            main._strom_setzen({'instance': 1, 'label': 'IP43', 'max_a': 10.0})
        self.assertEqual(len(versuche), 3)


if __name__ == '__main__':
    unittest.main()
