"""Der Stand, der in die ausgelieferte Seite eingesetzt wird.

Anlass: Die Seite trug wochenlang den Balken "Diese Seite läuft auf einem
älteren Stand" — auch direkt nach dem Neuladen. Der Grund war kein Fehler
im Browser, sondern einer im Zwischenspeicher: er hing allein an der
Änderungszeit von index.html, während in die Seite eine Commit-Kennung
eingesetzt wird, die sich bei JEDEM Commit ändert. Ein Commit, der nur CSS
oder nur JavaScript anfasst, ließ die Datei unberührt — und die Seite trug
danach für immer die alte Kennung, während /api/stand die neue meldete.

Aufruf:

    ./venv/bin/python -m unittest test_stand -v
"""
import os
import unittest

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

import asyncio                                 # noqa: E402

import main                                    # noqa: E402


class FalscheAnfrage:
    """Das Wenigste, was die Route braucht.

    Kein TestClient: der zieht httpx nach, und dieses Projekt kommt bisher
    ohne aus. Die Route liest genau ein Feld — den Kopfzeilen-Vergleich.
    """

    def __init__(self, etag: str = ''):
        self.headers = {'if-none-match': etag} if etag else {}


def stand_der_seite(text: str) -> str:
    marke = 'name="mave-stand" content="'
    i = text.index(marke) + len(marke)
    return text[i:text.index('"', i)]


class StandInDerSeite(unittest.TestCase):

    def setUp(self):
        self._echt = main._git_hash
        main._index_cache.update({'data': b'', 'etag': '', 'mtime': 0.0, 'stand': ''})

    def tearDown(self):
        main._git_hash = self._echt

    def _hole(self, hash_wert, etag=''):
        main._git_hash = lambda: hash_wert
        antwort = asyncio.run(main.root(FalscheAnfrage(etag)))
        antwort.text = (antwort.body or b'').decode('utf-8')
        return antwort

    def test_stand_steht_in_der_seite(self):
        a = self._hole('aaaaaaa')
        self.assertEqual(stand_der_seite(a.text), 'aaaaaaa')

    def test_neuer_commit_ohne_aenderung_an_index_html(self):
        """Der eigentliche Fehler: index.html bleibt gleich, der Commit nicht.

        Genau das passiert bei jedem Commit, der nur CSS, nur JavaScript oder
        nur ein Bild anfasst — und das ist die Mehrzahl.
        """
        a = self._hole('aaaaaaa')
        self.assertEqual(stand_der_seite(a.text), 'aaaaaaa')
        b = self._hole('bbbbbbb')
        self.assertEqual(stand_der_seite(b.text), 'bbbbbbb',
                         'Der Stand in der Seite muss dem neuen Commit folgen, '
                         'auch wenn index.html unverändert bleibt.')

    def test_die_kennung_wandert_mit(self):
        """Sonst bekommt ein Browser mit der alten Kennung ein leeres 304
        zurück und behält die alte Seite — der Balken bliebe trotz Neuladen."""
        a = self._hole('aaaaaaa')
        b = self._hole('bbbbbbb')
        self.assertNotEqual(a.headers['etag'], b.headers['etag'])

    def test_unveraendert_gibt_304(self):
        """Die Ersparnis darf nicht verlorengehen: gleicher Stand, gleiche
        Datei — dann soll der Browser nichts noch einmal herunterladen."""
        a = self._hole('aaaaaaa')
        b = self._hole('aaaaaaa', etag=a.headers['etag'])
        self.assertEqual(b.status_code, 304)

    def test_api_stand_und_seite_stimmen_ueberein(self):
        """Die beiden Werte, deren Abweichung den Balken auslöst."""
        seite = self._hole('ccccccc')
        api = asyncio.run(main.oberflaechen_stand())
        self.assertEqual(stand_der_seite(seite.text), api['stand'])


if __name__ == '__main__':
    unittest.main()
