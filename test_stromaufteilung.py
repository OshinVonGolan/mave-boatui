"""Zufluss, Verbrauch und Bilanz — und dass sie zusammenpassen.

Drei Zahlen stehen im Logbuch nebeneinander: die Bilanz kommt vom Shunt, die
Aufteilung in Zufluss und Verbrauch vom BMS. Wenn die beiden sich nicht einig
sind, muss die Anzeige trotzdem stimmen — sonst stehen dort drei Zahlen, von
denen zwei nicht zur dritten passen, und das ist schlechter als eine.

Frueher wurde erst ab 5 A Abweichung korrigiert (und erst unter 3 A wieder
aufgehoert). Im Normalbetrieb ging die Aufteilung damit um bis zu fuenf Ampere
an der Bilanz vorbei. Aufruf:

    python3 -m unittest test_stromaufteilung -v
"""
import types
import unittest

import can_reader


class Stromaufteilung(unittest.TestCase):

    def _korrigiert(self, shunt, zufluss, verbrauch):
        """Die echte Funktion, nur ohne CAN-Bus darunter."""
        stub = types.SimpleNamespace(
            state=types.SimpleNamespace(battery={'current': shunt}),
            _BMS_FACTOR_MIN=can_reader.CanInterface._BMS_FACTOR_MIN,
            _BMS_FACTOR_MAX=can_reader.CanInterface._BMS_FACTOR_MAX)
        p = can_reader.CanInterface._correct_bms_currents(
            stub, {'current_charge': zufluss, 'current_discharge': verbrauch})
        return p.get('current_charge', zufluss), p.get('current_discharge', verbrauch)

    def _passt(self, shunt, zufluss, verbrauch):
        zu, ve = self._korrigiert(shunt, zufluss, verbrauch)
        self.assertAlmostEqual(zu - ve, shunt, places=1,
                               msg=f'Zufluss {zu} − Verbrauch {ve} ≠ Bilanz {shunt}')
        return zu, ve

    def test_kleine_abweichung_wird_jetzt_auch_korrigiert(self):
        # Genau der Fall, der frueher durchrutschte: 2 A daneben, unter der
        # alten Schwelle von 5 A. Die Anzeige zeigte 12 herein bei einer Bilanz
        # von 10.
        self._passt(10.0, 12.0, 0.0)

    def test_verhaeltnis_bleibt_erhalten(self):
        # Das BMS weiss, WIE der Strom sich aufteilt; der Shunt weiss, WIE VIEL
        # es insgesamt ist. Beides soll erhalten bleiben.
        zu, ve = self._passt(10.0, 8.0, 1.0)
        # Nicht auf die dritte Stelle genau: die Stroeme werden auf zwei
        # Nachkommastellen gerundet (11,43 / 1,43 ergibt 7,993 statt 8,0). Ein
        # Prozent Toleranz prueft, worum es geht — das Verhaeltnis bleibt —,
        # ohne an der Rundung zu scheitern.
        self.assertAlmostEqual(zu / ve, 8.0, delta=8.0 * 0.01)

    def test_vorzeichenkonflikt_folgt_dem_shunt(self):
        # BMS behauptet Laden, der Shunt sagt Entladen. Der Shunt gewinnt.
        zu, ve = self._passt(-5.0, 6.0, 0.0)
        self.assertEqual((zu, ve), (0.0, 5.0))

    def test_bms_meldet_nichts(self):
        zu, ve = self._passt(7.0, 0.0, 0.0)
        self.assertEqual((zu, ve), (7.0, 0.0))

    def test_grosse_abweichung(self):
        self._passt(-20.0, 0.0, 8.0)

    def test_auch_im_rauschen_passt_es(self):
        # Ausdrueckliche Vorgabe des Eigners: keine Abweichung, auch nicht bei
        # ruhendem Boot. Frueher blieb hier eine Aufteilung stehen, die nicht
        # zur Bilanz passte.
        self._passt(0.2, 0.1, 0.0)
        self._passt(-0.3, 0.2, 0.1)
        self._passt(0.0, 0.1, 0.1)

    def test_ohne_shunt_bleibt_alles_wie_es_war(self):
        # Kein Shunt am Bus heisst: es gibt nichts, woran man korrigieren
        # koennte. Dann lieber die Rohwerte als geratene.
        stub = types.SimpleNamespace(
            state=types.SimpleNamespace(battery={'current': None}),
            _BMS_FACTOR_MIN=0.2, _BMS_FACTOR_MAX=5.0)
        p = can_reader.CanInterface._correct_bms_currents(
            stub, {'current_charge': 3.0, 'current_discharge': 1.0})
        self.assertEqual((p['current_charge'], p['current_discharge']), (3.0, 1.0))

    def test_stroeme_bleiben_betraege(self):
        # Lade- und Entladestrom sind per Definition Betraege. Ein negativer
        # Wert daraus liefe in Alarmregeln und Verlauf ein.
        for shunt, zu_, ve_ in ((10.0, 12.0, 0.0), (-5.0, 6.0, 0.0), (-20.0, 0.0, 8.0)):
            zu, ve = self._korrigiert(shunt, zu_, ve_)
            self.assertGreaterEqual(zu, 0.0)
            self.assertGreaterEqual(ve, 0.0)

    def test_aufteilung_folgt_einem_neuen_shuntwert(self):
        """Der eigentliche Fall aus dem Betrieb.

        Korrigiert wurde bisher nur beim Eintreffen eines BMS-Rahmens, gegen
        den Shunt von genau diesem Augenblick. Der Shunt hat aber seinen
        eigenen Takt. Am laufenden Boot gemessen: Bilanz -7,5 A, Aufteilung
        0,0 / 6,7 — 0,8 A daneben, obwohl die Korrektur "immer" laeuft.
        """
        import types
        stub = types.SimpleNamespace(
            state=types.SimpleNamespace(battery={'current': -6.7},
                                        bms={'current_charge': 0.0,
                                             'current_discharge': 6.7}),
            _BMS_FACTOR_MIN=can_reader.CanInterface._BMS_FACTOR_MIN,
            _BMS_FACTOR_MAX=can_reader.CanInterface._BMS_FACTOR_MAX)
        stub._correct_bms_currents = types.MethodType(
            can_reader.CanInterface._correct_bms_currents, stub)
        angleichen = types.MethodType(can_reader.CanInterface._angleichen, stub)

        # Der Shunt wandert weiter, ohne dass ein BMS-Rahmen kommt.
        stub.state.battery['current'] = -7.5
        self.assertTrue(angleichen(), 'die Aufteilung haette sich aendern muessen')
        zu = stub.state.bms['current_charge']
        ve = stub.state.bms['current_discharge']
        self.assertAlmostEqual(zu - ve, -7.5, places=1)

    def test_angleichen_ohne_bms_tut_nichts(self):
        import types
        stub = types.SimpleNamespace(
            state=types.SimpleNamespace(battery={'current': 5.0},
                                        bms={'current_charge': None,
                                             'current_discharge': None}),
            _BMS_FACTOR_MIN=0.2, _BMS_FACTOR_MAX=5.0)
        self.assertFalse(types.MethodType(can_reader.CanInterface._angleichen, stub)())

    def test_die_alten_schwellen_gibt_es_nicht_mehr(self):
        # Sonst schleicht sich die Zweiteilung wieder ein.
        self.assertFalse(hasattr(can_reader.CanInterface, '_BMS_CORR_ON'))
        self.assertFalse(hasattr(can_reader.CanInterface, '_BMS_CORR_OFF'))


if __name__ == '__main__':
    unittest.main()
