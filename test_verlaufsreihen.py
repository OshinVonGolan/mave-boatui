"""Die Reihen hinter den Graphen der Statusleiste.

Sie kommen fertig verdichtet vom Pi: 60 Stützstellen über 24 Stunden, feste
Zeit-Eimer. Der Unterschied zwischen „kein Wert" und „null" entscheidet
darüber, wie die Kurve aussieht — und genau daran ist es einmal
auseinandergelaufen.

Aufruf:

    ./venv/bin/python -m unittest test_verlaufsreihen -v
"""
import os
import time
import unittest

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

import main                                    # noqa: E402


class Ladequellen(unittest.TestCase):
    """Eine Quelle, die nichts meldet, lädt nicht — also null Watt.

    Das Landstromgerät meldet sich erst, seit das Kabel dranhängt; die Stunden
    davor standen im Verlauf leer. Die Kurve lief deshalb nach links aus (die
    Anlaufblende greift, wenn eine Reihe erst mitten im Fenster beginnt),
    während das Feld „Laden" daneben durchging — dessen Summe gibt es, weil
    Solar durchgehend 0,0 meldet. Zwei Bilder derselben Sache (Eignermeldung).
    """

    def setUp(self):
        self._alt_grob = list(main.history_grob)
        self._alt_fein = list(main.history)
        main.history_grob.clear()
        main.history.clear()
        main._spark_cache['data'] = None
        # Genau ein Punkt je Eimer, in dessen Mitte. Bei anderem Abstand
        # blieben Eimer leer, und die Prüfung träfe eine andere Aussage.
        jetzt = time.time()
        von = jetzt - 86400
        breite = 86400 / 60
        for i in range(60):
            e = {'ts': von + i * breite + breite / 2,
                 'voltage': 13.2, 'soc': 70.0, 'solar1': 0.0}
            if i >= 48:                          # Landstrom seit knapp 5 h
                e['charger'] = 700.0
            main.history_grob.append(e)

    def tearDown(self):
        main.history_grob.clear(); main.history_grob.extend(self._alt_grob)
        main.history.clear(); main.history.extend(self._alt_fein)
        main._spark_cache['data'] = None

    def reihen(self):
        return main._spark_bauen()['serien']

    def test_der_lader_faengt_nicht_mitten_im_fenster_an(self):
        r = self.reihen()['charger']
        self.assertTrue(all(v is not None for v in r),
                        f'noch Lücken: {r[:6]}')

    def test_ohne_meldung_steht_null_und_nicht_der_letzte_wert(self):
        r = self.reihen()['charger']
        self.assertEqual(r[0], 0.0)
        self.assertGreater(r[-1], 100)

    def test_laden_und_lader_beginnen_gleich(self):
        """Sie zeigen dasselbe, solange nur der Lader liefert — dann dürfen sie
        auch nicht verschieden aussehen."""
        r = self.reihen()
        anfang = lambda x: next(i for i, v in enumerate(x) if v is not None)  # noqa: E731
        self.assertEqual(anfang(r['charger']), anfang(r['laden']))

    def test_ein_ganz_leerer_eimer_bleibt_leer(self):
        """Dann lief der Pi nicht. „Null Watt" wäre eine Behauptung über eine
        Zeit, von der wir nichts wissen."""
        main.history_grob.clear()
        jetzt = time.time()
        von = jetzt - 86400
        breite = 86400 / 60
        for i in range(50, 60):                 # nur die letzten vier Stunden
            main.history_grob.append({'ts': von + i * breite + breite / 2,
                                      'voltage': 13.2, 'charger': 700.0})
        main._spark_cache['data'] = None
        r = self.reihen()['charger']
        self.assertIsNone(r[0], 'leere Zeit wurde mit null aufgefüllt')
        self.assertIsNotNone(r[-1])

    def test_ein_tank_wird_nicht_aufgefuellt(self):
        """Nur Ladequellen. Ein Tank hat einen Stand, auch wenn ihn gerade
        niemand misst."""
        r = self.reihen()
        self.assertTrue(all(v is None for v in r['tank1']),
                        'der Tank wurde mit null erfunden')


if __name__ == '__main__':
    unittest.main()
