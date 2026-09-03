"""Tests der Serverdatenhaltung.

Kein Netz, keine Verbindung: eine SQLite-Datei im Temp-Verzeichnis. Aufruf:

    python3 -m unittest test_server_speicher -v
"""
import tempfile
import time
import unittest
from pathlib import Path

from server.speicher import Speicher
from sync import zeit as z

JETZT = 1788000000.0


class Basis(unittest.TestCase):
    def setUp(self):
        self.ort = Path(tempfile.mkdtemp()) / 'mave.db'
        self.s = Speicher(self.ort)

    def tearDown(self):
        self.s.schliessen()


class ZustandUndAlter(Basis):

    def test_alter_gehoert_zum_ergebnis(self):
        # Ein Wert von vor drei Tagen darf nicht aussehen wie live — deshalb
        # liefert der Speicher das Alter mit, nicht daneben.
        self.s.zustand_setzen({'battery': {'soc': 82}}, JETZT)
        z_ = self.s.zustand()
        self.assertEqual(z_['daten']['battery']['soc'], 82)
        self.assertEqual(z_['bordzeit'], JETZT)
        self.assertLess(z_['alter_s'], 2)

    def test_leerer_speicher_liefert_nichts_statt_zu_luegen(self):
        self.assertIsNone(self.s.zustand())

    def test_zustand_wird_ersetzt_nicht_gesammelt(self):
        self.s.zustand_setzen({'a': 1}, JETZT)
        self.s.zustand_setzen({'a': 2}, JETZT + 10)
        self.assertEqual(self.s.zustand()['daten'], {'a': 2})


class VerlaufUndParken(Basis):

    def test_stand_ist_die_zahl_die_der_pi_braucht(self):
        self.assertEqual(self.s.verlauf_stand(), 0)
        self.s.verlauf_anhaengen([{'folge': 1, 'zeit': JETZT, 'daten': {'v': 12.6}},
                                  {'folge': 2, 'zeit': JETZT + 60, 'daten': {'v': 12.5}}])
        self.assertEqual(self.s.verlauf_stand(), 2)

    def test_geparkte_zaehlen_zum_stand(self):
        # Sonst schickt der Pi sie ein zweites Mal.
        self.s.verlauf_anhaengen([{'folge': 7, 'mono': 30.0, 'daten': {'v': 12.4}}])
        self.assertEqual(self.s.verlauf_stand(), 7)
        self.assertEqual(self.s.geparkt_anzahl(), 1)

    def test_eintrag_ohne_zeit_wird_geparkt_nicht_geraten(self):
        erg = self.s.verlauf_anhaengen([
            {'folge': 1, 'zeit': JETZT, 'daten': {'v': 1}},
            {'folge': 2, 'mono': 30.0, 'daten': {'v': 2}},
        ])
        self.assertEqual(erg, {'verlauf': 1, 'geparkt': 1})
        self.assertEqual(len(self.s.verlauf()), 1)

    def test_geparkte_wandern_nach_dem_ntp_abgleich_in_den_verlauf(self):
        # Der ganze Zweck des Parkens: Stromausfall, Pi schreibt ohne Uhr,
        # zwei Minuten spaeter kommt NTP — dann ist der Bezug bekannt.
        self.s.verlauf_anhaengen([{'folge': 1, 'mono': 30.0, 'daten': {'v': 12.4}},
                                  {'folge': 2, 'mono': 90.0, 'daten': {'v': 12.3}}])
        self.assertEqual(self.s.geparkt_anzahl(), 2)

        buch = z.Uhrbuch()
        buch.merke(1000.0, 30.0, False)      # Uhr stand noch falsch
        buch.merke(JETZT, 150.0, True)       # NTP bei mono=150
        umgezogen = self.s.geparkte_aufloesen(buch)

        self.assertEqual(umgezogen, 2)
        self.assertEqual(self.s.geparkt_anzahl(), 0)
        v = self.s.verlauf()
        self.assertAlmostEqual(v[0]['zeit'], JETZT - 120.0, places=1)
        self.assertAlmostEqual(v[1]['zeit'], JETZT - 60.0, places=1)

    def test_ohne_referenz_bleiben_sie_liegen(self):
        self.s.verlauf_anhaengen([{'folge': 1, 'mono': 30.0, 'daten': {'v': 1}}])
        self.assertEqual(self.s.geparkte_aufloesen(z.Uhrbuch()), 0)
        self.assertEqual(self.s.geparkt_anzahl(), 1)

    def test_derselbe_eintrag_zweimal_bleibt_einer(self):
        # Nach einem Abriss schickt der Pi lieber einmal zu viel.
        e = [{'folge': 1, 'zeit': JETZT, 'daten': {'v': 1}}]
        self.s.verlauf_anhaengen(e)
        self.s.verlauf_anhaengen(e)
        self.assertEqual(len(self.s.verlauf()), 1)


class LueckenDeutung(Basis):
    """Die Frage des Eigners: war das Boot offline, oder ist der Pi abgestuerzt?"""

    def _sitzung(self, ab, bis, **befund):
        # Sitzungen mit gesetzten Zeiten anlegen, ohne echte Uhr abzuwarten
        sid = self.s.sitzung_beginnen('mave-pi', befund)
        with self.s._lock:
            self.s._db.execute('UPDATE sitzung SET ab = ?, bis = ? WHERE id = ?',
                               (ab, bis, sid))
            self.s._db.commit()
        return sid

    def test_funkloch_der_pi_lief_durch(self):
        self._sitzung(JETZT, JETZT + 3600, letztes_ende='erststart')
        # Kein Neustart gemeldet: gleiche Laufzeit, nur die Verbindung fehlte.
        self._sitzung(JETZT + 9000, JETZT + 12000, letztes_ende='sauber',
                      rechner_neu=False, nur_dienst=False)
        (l,) = self.s.luecken()
        self.assertEqual(l['art'], 'funkloch')
        self.assertEqual(l['dauer_s'], 5400.0)
        self.assertNotIn('aus_bis', l)

    def test_stromausfall_wird_als_solcher_benannt(self):
        self._sitzung(JETZT, JETZT + 3600, letztes_ende='erststart')
        # Der Rechner ist neu und das letzte Ende war unsauber: Strom weg.
        self._sitzung(JETZT + 9000, JETZT + 9600, letztes_ende='abbruch',
                      rechner_neu=True, rechner_start_wand=JETZT + 8940)
        (l,) = self.s.luecken()
        self.assertEqual(l['art'], 'stromlos')
        self.assertIn('Stromausfall', l['grund'])
        # Die Luecke laesst sich teilen: bis zum Systemstart war er AUS,
        # danach lief er nur ohne Verbindung.
        self.assertEqual(l['aus_bis'], JETZT + 8940)

    def test_geordneter_neustart_ist_kein_absturz(self):
        self._sitzung(JETZT, JETZT + 3600, letztes_ende='erststart')
        self._sitzung(JETZT + 3700, JETZT + 4000, letztes_ende='sauber',
                      rechner_neu=True, rechner_start_wand=JETZT + 3650)
        (l,) = self.s.luecken()
        self.assertEqual(l['art'], 'neustart')

    def test_dienstneustart_wird_vom_rechnerneustart_unterschieden(self):
        # Update per Aktualisieren-Knopf: der Dienst beendet sich selbst,
        # systemd startet ihn neu. Der Rechner lief durch.
        self._sitzung(JETZT, JETZT + 3600, letztes_ende='erststart')
        self._sitzung(JETZT + 3620, JETZT + 4000, letztes_ende='abbruch',
                      rechner_neu=False, nur_dienst=True)
        (l,) = self.s.luecken()
        self.assertEqual(l['art'], 'dienst')

    def test_nahtlose_uebergaenge_sind_keine_luecke(self):
        self._sitzung(JETZT, JETZT + 100, letztes_ende='erststart')
        self._sitzung(JETZT + 100.5, JETZT + 200, letztes_ende='sauber')
        self.assertEqual(self.s.luecken(), [])

    def test_lebenszeichen_verschiebt_das_ende(self):
        sid = self.s.sitzung_beginnen('mave-pi', {'letztes_ende': 'sauber'})
        vorher = self.s.sitzungen()[0]['bis']
        time.sleep(0.01)
        self.s.lebenszeichen(sid)
        self.assertGreater(self.s.sitzungen()[0]['bis'], vorher)

    def test_erster_start_wird_nicht_als_neustart_geraten(self):
        # Im Feld aufgefallen: der allererste Start nach dem Einbau meldete
        # "Rechner wurde neu gestartet". Das war geraten — es gab schlicht
        # keine Startmarke, mit der sich vergleichen liess.
        self._sitzung(JETZT, JETZT + 100, letztes_ende='sauber')
        self._sitzung(JETZT + 200, JETZT + 300, letztes_ende='erststart', rechner_neu=True)
        (l,) = self.s.luecken()
        self.assertEqual(l['art'], 'erststart')
        self.assertIn('nicht Buch', l['grund'])


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
