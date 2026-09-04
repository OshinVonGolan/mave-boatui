"""Alarme: die Toleranzschwelle, damit ein Alarm nicht flattert.

Anlass: Der Wassertank steht bei rund 20 %, die Regel loest bei 20 aus.
Quittieren half nichts — der Alarm loeste sich beim naechsten Messwert auf und
feuerte beim uebernaechsten wieder. Ein Alarm, den man nicht wegbekommt, wird
ignoriert; und ignorierte Alarme machen alle anderen wertlos.

Aufruf:

    ./venv/bin/python -m unittest test_alarme -v
"""
import unittest

from alarm_engine import AlarmEngine, _evaluate, _hysterese


class Toleranz(unittest.TestCase):

    TANK = {'op': '<', 'threshold': 20, 'hysterese': 4}

    def test_loest_an_der_schwelle_aus(self):
        self.assertTrue(_evaluate(self.TANK, 19.8, aktiv=False))
        self.assertFalse(_evaluate(self.TANK, 20.1, aktiv=False))

    def test_steht_der_alarm_braucht_es_echte_erholung(self):
        """Der Kern: knapp ueber der Schwelle bleibt der Alarm stehen."""
        for wert in (20.1, 21, 23, 23.9):
            with self.subTest(wert=wert):
                self.assertTrue(_evaluate(self.TANK, wert, aktiv=True),
                                f'{wert} % darf den Alarm noch nicht aufheben')
        self.assertFalse(_evaluate(self.TANK, 24.5, aktiv=True))

    def test_das_gleiche_nach_oben(self):
        regel = {'op': '>', 'threshold': 45, 'hysterese': 2}
        self.assertTrue(_evaluate(regel, 45.5, aktiv=False))
        self.assertTrue(_evaluate(regel, 44, aktiv=True))     # noch nicht erholt
        self.assertFalse(_evaluate(regel, 42.5, aktiv=True))  # jetzt schon

    def test_bereich_engt_sich_beidseitig_ein(self):
        regel = {'op': 'range', 'min': 11.8, 'max': 14.6, 'hysterese': 0.2}
        self.assertTrue(_evaluate(regel, 11.7, aktiv=False))
        self.assertFalse(_evaluate(regel, 11.9, aktiv=False))
        # Steht der Alarm, reicht 11.9 nicht mehr
        self.assertTrue(_evaluate(regel, 11.9, aktiv=True))
        self.assertFalse(_evaluate(regel, 12.1, aktiv=True))

    def test_flag_regeln_bekommen_keine_toleranz(self):
        """Ein Flag flattert nicht — es steht oder es steht nicht. Der Anteil
        von der Schwelle ergibt bei `> 0` von selbst null."""
        self.assertEqual(_hysterese({'op': '>', 'threshold': 0}), 0.0)
        regel = {'op': '>', 'threshold': 0}
        self.assertTrue(_evaluate(regel, 1, aktiv=True))
        self.assertFalse(_evaluate(regel, 0, aktiv=True))

    def test_ohne_angabe_fuenf_prozent_der_schwelle(self):
        self.assertAlmostEqual(_hysterese({'op': '<', 'threshold': 20}), 1.0)
        self.assertAlmostEqual(_hysterese({'op': '>', 'threshold': 60}), 3.0)


class AmLaufendenAlarm(unittest.TestCase):
    """Dasselbe durch die ganze Engine, nicht nur durch die Regelfunktion."""

    def setUp(self):
        self.e = AlarmEngine()
        # Nur die eine Regel prüfen — die übrigen haben keine Daten und
        # werden ohnehin übersprungen.
        for k, r in self.e._rules.items():
            r['enabled'] = (k == 'tank1_low')

    def _stand(self, prozent):
        self.e.check({'tanks': {'tank1': prozent}})
        return [a for a in self.e.get_alarms() if not a['resolved']]

    def test_flattert_nicht_mehr(self):
        """Der gemeldete Fall: Wert wackelt um die Schwelle."""
        self.assertEqual(len(self._stand(25)), 0)
        self.assertEqual(len(self._stand(19.5)), 1, 'muss auslösen')
        # Jetzt das Wackeln — jeder dieser Werte hätte vorher aufgelöst
        for wert in (20.2, 19.8, 21.0, 20.5, 22.9):
            with self.subTest(wert=wert):
                self.assertEqual(len(self._stand(wert)), 1,
                                 f'bei {wert} % darf der Alarm nicht verschwinden')
        # Und ohne die Hysterese hätte es hier fünf Alarme gegeben
        self.assertEqual(len(self.e.get_alarms()), 1)

    def test_echte_erholung_loest_ihn_auf(self):
        self._stand(19.5)
        self.assertEqual(len(self._stand(30)), 0)

    def test_quittieren_haelt_nach_dem_wackeln(self):
        """Der eigentliche Ärger: quittiert, und beim nächsten Messwert ist ein
        NEUER unquittierter Alarm da."""
        self._stand(19.5)
        alarm = self.e.get_alarms()[0]
        self.e.acknowledge(alarm['id'])
        self.assertEqual(self.e.unack_count, 0)
        for wert in (20.3, 19.9, 21.5):
            self._stand(wert)
        self.assertEqual(self.e.unack_count, 0,
                         'Nach dem Quittieren darf kein neuer Alarm entstehen, '
                         'solange sich der Wert nicht wirklich erholt hat.')


if __name__ == '__main__':
    unittest.main()
