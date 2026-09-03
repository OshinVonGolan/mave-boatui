"""Konten und Sitzungen auf dem Pi.

Der Server ist die Wahrheit; hier liegt die Kopie, damit die Anmeldung an Bord
auch ohne Internet funktioniert. Solange es noch keinen Server gibt, ist diese
Datei die einzige Quelle — die Anlage ist damit von Anfang an bedienbar und
muss nicht auf die Wolke warten.

Zwei Dateien, beide Laufzeitzustand und in .gitignore:

    konten.json      die Konten (mit Passworthashes), vom Server gespiegelt
    sitzungen.json   die offenen Sitzungen

Sitzungen liegen absichtlich auf der Platte und nicht nur im Speicher: der
Dienst startet sich nach jedem Update selbst neu (Restart=always), und niemand
will sich nach jedem Update an Bord neu anmelden — schon gar nicht am
Touchscreen am Kartentisch.
"""
from __future__ import annotations

import logging
import threading
import time

from jsonio import read_json, write_json
from sync import konten as k
from sync import rechte as r

log = logging.getLogger(__name__)

# Wie lange eine Sitzung ohne Nutzung gilt. Vier Wochen: an Bord soll man sich
# nicht staendig anmelden muessen, und die Anlage haengt in einem Netz, das dem
# Eigner gehoert.
SITZUNG_DAUER_S = 28 * 86400
# Eine Geraetesitzung (Kiosk am Kartentisch) laeuft nicht ab. Sie wird
# zurueckgezogen, nicht vergessen.
KIOSK_DAUER_S = None


class Konten:
    def __init__(self, konten_datei, sitzungen_datei):
        self._konten_datei = konten_datei
        self._sitz_datei = sitzungen_datei
        self._lock = threading.Lock()
        self._konten: dict = {}
        self._sitzungen: dict = {}
        self._laden()

    # ── Laden und Sichern ───────────────────────────────────────────────────

    def _laden(self) -> None:
        rohe = read_json(self._konten_datei, {}) or {}
        self._konten = rohe if isinstance(rohe, dict) else {}
        rohs = read_json(self._sitz_datei, {}) or {}
        self._sitzungen = rohs if isinstance(rohs, dict) else {}
        self._aufraeumen()

    def _sichern_konten(self) -> None:
        write_json(self._konten_datei, self._konten)

    def _sichern_sitzungen(self) -> None:
        write_json(self._sitz_datei, self._sitzungen)

    # ── Zustand ─────────────────────────────────────────────────────────────

    @property
    def leer(self) -> bool:
        """Ob noch gar kein Konto angelegt ist.

        Das ist der Zustand bei der Erstinbetriebnahme, und er hat Folgen:
        solange er gilt, bleibt die Anlage im Bordnetz offen. Sonst waere sie
        nach einem Update unbedienbar, bevor jemand ein Konto anlegen konnte.
        """
        return not self._konten

    def liste(self) -> list[dict]:
        """Alle Konten OHNE Hashes — fuer die Verwaltungsansicht."""
        return [{'name': n, 'rolle': kt.get('rolle'), 'gesperrt': bool(kt.get('gesperrt')),
                 'gueltig_bis': kt.get('gueltig_bis'), 'angelegt': kt.get('angelegt')}
                for n, kt in sorted(self._konten.items())]

    # ── Konten ──────────────────────────────────────────────────────────────

    def anlegen(self, name: str, passwort: str, rolle: str) -> dict:
        if rolle not in r.ROLLEN:
            raise k.KontoFehler(f'Unbekannte Rolle: {rolle!r}')
        konto = k.konto_anlegen(name, passwort, rolle, angelegt=time.time())
        with self._lock:
            if konto['name'] in self._konten:
                raise k.KontoFehler(f'Den Namen {konto["name"]!r} gibt es schon.')
            self._konten[konto['name']] = konto
            self._sichern_konten()
        log.info('Konto %r angelegt (%s)', konto['name'], rolle)
        return {'name': konto['name'], 'rolle': rolle}

    def ersetzen(self, konten: dict) -> int:
        """Die Kopie vom Server uebernehmen.

        Ersetzt vollstaendig: der Server ist die Wahrheit. Sitzungen von
        Konten, die es nicht mehr gibt, werden dabei ungueltig — anders liesse
        sich ein gesperrtes Konto an Bord nicht aussperren.
        """
        with self._lock:
            self._konten = {n: kt for n, kt in (konten or {}).items() if isinstance(kt, dict)}
            self._sichern_konten()
            weg = [kn for kn, s in self._sitzungen.items()
                   if s.get('konto') not in self._konten]
            for kn in weg:
                del self._sitzungen[kn]
            if weg:
                self._sichern_sitzungen()
        log.info('Kontenkopie vom Server uebernommen: %d Konten, %d Sitzungen verworfen',
                 len(self._konten), len(weg))
        return len(self._konten)

    # ── Anmelden ────────────────────────────────────────────────────────────

    def anmelden(self, name: str, passwort: str, *, kiosk: bool = False) -> tuple[str, dict]:
        """Gibt (Sitzungstoken, Konto) zurueck oder wirft KontoFehler.

        Die Meldung ist bei falschem Namen und falschem Passwort DIESELBE —
        sonst verraet sie, welche Namen es gibt.
        """
        konto = self._konten.get((name or '').strip())
        # Auch bei unbekanntem Namen rechnen, damit die Antwortzeit nichts
        # verraet. Der Hash ist ein fester Blindwert.
        gespeichert = (konto or {}).get('hash') or _BLIND
        passt = k.hash_pruefen(passwort or '', gespeichert)
        if not konto or not passt:
            raise k.KontoFehler('Name oder Passwort stimmt nicht.')
        if konto.get('gesperrt'):
            raise k.KontoFehler('Dieses Konto ist gesperrt.')
        if k.abgelaufen(konto, _jetzt_falls_verlaesslich()):
            raise k.KontoFehler('Dieser Zugang ist abgelaufen.')

        # Bei erfolgreicher Anmeldung liegt das Passwort im Klartext vor — der
        # einzige Moment, in dem sich ein schwacher Hash still erneuern laesst.
        if k.sollte_erneuert_werden(gespeichert):
            with self._lock:
                konto['hash'] = k.hash_erzeugen(passwort)
                self._sichern_konten()
            log.info('Passworthash von %r auf heutige Kosten gehoben', konto['name'])

        klartext, kennung = k.sitzung_erzeugen()
        with self._lock:
            self._sitzungen[kennung] = {
                'konto': konto['name'],
                'seit': time.time(),
                'zuletzt': time.time(),
                'kiosk': bool(kiosk),
            }
            self._aufraeumen()
            self._sichern_sitzungen()
        return klartext, konto

    def abmelden(self, token: str) -> None:
        kennung = k.sitzung_kennung(token or '')
        with self._lock:
            if self._sitzungen.pop(kennung, None) is not None:
                self._sichern_sitzungen()

    def konto_zu_token(self, token: str) -> dict | None:
        """Das Konto zu einer Sitzung, oder None.

        Nebenwirkung: die Sitzung wird als benutzt vermerkt. Geschrieben wird
        dabei hoechstens einmal je Stunde — sonst faende auf der SD-Karte bei
        jeder Anfrage ein Schreibvorgang statt.
        """
        if not token:
            return None
        kennung = k.sitzung_kennung(token)
        s = self._sitzungen.get(kennung)
        if not s:
            return None
        if not s.get('kiosk') and time.time() - s.get('zuletzt', 0) > SITZUNG_DAUER_S:
            with self._lock:
                self._sitzungen.pop(kennung, None)
                self._sichern_sitzungen()
            return None
        konto = self._konten.get(s.get('konto'))
        if not konto or konto.get('gesperrt'):
            return None
        if k.abgelaufen(konto, _jetzt_falls_verlaesslich()):
            return None
        if time.time() - s.get('zuletzt', 0) > 3600:
            with self._lock:
                s['zuletzt'] = time.time()
                self._sichern_sitzungen()
        return dict(konto, _sitzung_kiosk=bool(s.get('kiosk')))

    def _aufraeumen(self) -> None:
        jetzt = time.time()
        weg = [kn for kn, s in self._sitzungen.items()
               if not s.get('kiosk') and jetzt - s.get('zuletzt', 0) > SITZUNG_DAUER_S]
        for kn in weg:
            del self._sitzungen[kn]


# Ein fester, gueltiger Hash fuer Anmeldeversuche mit unbekanntem Namen. So
# kostet ein falscher Name genauso viel Zeit wie ein falsches Passwort, und die
# Antwortzeit verraet nicht, welche Namen es gibt.
_BLIND = k.hash_erzeugen('kein-konto-mit-diesem-namen-vorhanden')


def _jetzt_falls_verlaesslich() -> float | None:
    """Die Uhrzeit, sofern sie ueberhaupt stimmen kann.

    Vor 2024 kann kein Eintrag dieser Anlage liegen — dann laeuft der Pi ohne
    gestellte Uhr, und befristete Konten duerfen NICHT ablaufen.
    """
    jetzt = time.time()
    return jetzt if jetzt >= 1704067200.0 else None
