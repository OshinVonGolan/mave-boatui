"""Tests der Fassungsnummer.

Die Nummer kommt aus Git. Das ist bequem und hat eine Falle, in die dieses
Projekt bereits einmal getreten ist: `git describe` nimmt ohne Filter den
zuletzt gesetzten Tag — auch eine Sicherungsmarke wie `geraeteseite-vor-umbau`.
Aus 1.60.4 wurde damit ".4".

Geprueft wird gegen ein echtes, wegwerfbares Git-Verzeichnis. Ein Nachbau der
Aufrufe waere wertlos: der Fehler steckte genau darin, was `git describe`
wirklich zurueckgibt. Aufruf:

    python3 -m unittest test_fassung -v
"""
import subprocess
import tempfile
import unittest
from pathlib import Path


def _git(ort, *args):
    return subprocess.run(['git', *args], cwd=ort, capture_output=True,
                          text=True, timeout=10).stdout.strip()


class FassungAusGit(unittest.TestCase):

    def setUp(self):
        self.ort = Path(tempfile.mkdtemp())
        _git(self.ort, 'init', '-q', '-b', 'master')
        _git(self.ort, 'config', 'user.email', 'test@example.invalid')
        _git(self.ort, 'config', 'user.name', 'Test')
        # Die Funktionen rechnen relativ zu ihrer eigenen Datei — also muss dort
        # eine liegen.
        (self.ort / 'main.py').write_text('# Platzhalter\n')
        self._commit('erster')
        self.f, self.semver = self._laden()

    def _commit(self, text):
        p = self.ort / 'main.py'
        p.write_text(p.read_text() + f'# {text}\n')
        _git(self.ort, 'add', 'main.py')
        _git(self.ort, 'commit', '-q', '-m', text)

    def _laden(self):
        """Die echten Funktionen aus main.py, mit dem Testverzeichnis als Heimat."""
        quelle = Path(__file__).with_name('main.py').read_text()
        teil = quelle[quelle.index('_FASSUNGS_TAGS ='):quelle.index('VERSION  = _fassung()')]
        ns = {'subprocess': subprocess, 'Path': Path,
              '__file__': str(self.ort / 'main.py')}
        exec(compile(teil, 'fassung', 'exec'), ns)
        return ns['_fassung'], ns['_git_semver']

    def test_genau_auf_dem_tag_gilt_der_tag(self):
        _git(self.ort, 'tag', 'v2.0.0')
        self.assertEqual(self.f(), '2.0.0')

    def test_danach_wird_weitergezaehlt(self):
        _git(self.ort, 'tag', 'v2.0.0')
        self._commit('zweiter')
        self._commit('dritter')
        self.assertEqual(self.f(), '2.0.2')

    def test_sicherungsmarke_zaehlt_nicht_als_fassung(self):
        # Der Fehler, um den es geht: die Marke ist juenger als der
        # Fassungs-Tag. Ohne Filter gewinnt sie — und weil in ihr kein Punkt
        # steht, kam ".1" heraus.
        _git(self.ort, 'tag', 'v1.60.0')
        self._commit('umbau')
        _git(self.ort, 'tag', 'geraeteseite-vor-umbau')
        self.assertEqual(self.f(), '1.60.1')

    def test_marke_genau_auf_dem_kopf_gilt_auch_nicht(self):
        _git(self.ort, 'tag', 'v1.60.0')
        self._commit('umbau')
        _git(self.ort, 'tag', 'vor-dem-umbau')
        self.assertEqual(self.semver(), '')      # kein exakter FASSUNGS-Tag
        self.assertEqual(self.f(), '1.60.1')

    def test_ohne_jeden_tag_der_notfallwert(self):
        self.assertEqual(self.f(), '1.60.0')

    def test_fuer_die_gegenstelle_lieber_nichts_als_geraten(self):
        # Eine erfundene Fassung der Gegenstelle waere schlimmer als eine
        # leere: danach entscheidet jemand, ob er aktualisiert.
        self.assertEqual(self.f(notfalls=''), '')
        self.assertEqual(self.f('gibtesnicht', notfalls=''), '')

    def test_ein_anderer_stand_wird_getrennt_gerechnet(self):
        _git(self.ort, 'tag', 'v3.1.0')
        self._commit('zweiter')
        stand = _git(self.ort, 'rev-parse', 'HEAD')
        self._commit('dritter')
        self.assertEqual(self.f(), '3.1.2')
        self.assertEqual(self.f(stand), '3.1.1')

    def test_tag_ohne_punkt_ergibt_keine_halbe_nummer(self):
        # Selbst wenn jemand einen Tag `v9` setzt: lieber der Notfallwert als
        # ".7" — eine Zeichenkette, die wie eine Fassung aussieht und keine ist.
        _git(self.ort, 'tag', 'v9')
        self._commit('zweiter')
        self.assertEqual(self.f(), '1.60.0')


if __name__ == '__main__':
    unittest.main()
