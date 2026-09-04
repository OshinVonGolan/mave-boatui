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
import hashlib
import json
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
                 'person': kt.get('person') or '', 'spitzname': kt.get('spitzname') or '',
                 'anzeigename': k.anzeigename(kt),
                 # Ein Konto ohne Hash wartet noch auf sein Passwort.
                 'eingeladen': bool(kt.get('einladung')),
                 'einladung_bis': (kt.get('einladung') or {}).get('bis'),
                 'gueltig_bis': kt.get('gueltig_bis'), 'angelegt': kt.get('angelegt')}
                for n, kt in sorted(self._konten.items())]

    # ── Konten ──────────────────────────────────────────────────────────────

    def anlegen(self, name: str, passwort: str, rolle: str,
                person: str = '', spitzname: str = '') -> dict:
        if rolle not in r.ROLLEN:
            raise k.KontoFehler(f'Unbekannte Rolle: {rolle!r}')
        konto = k.konto_anlegen(name, passwort, rolle, angelegt=time.time(),
                                person=person, spitzname=spitzname)
        with self._lock:
            if konto['name'] in self._konten:
                raise k.KontoFehler(f'Den Namen {konto["name"]!r} gibt es schon.')
            self._konten[konto['name']] = konto
            self._sichern_konten()
        log.info('Konto %r angelegt (%s)', konto['name'], rolle)
        return {'name': konto['name'], 'rolle': rolle,
                'anzeigename': k.anzeigename(konto)}

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

    def anmelden(self, name: str, passwort: str, *, kiosk: bool = False,
                 herkunft: str = '', geraet: str = '') -> tuple[str, dict]:
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
                # Woher und womit — damit sich im Logbuch sagen laesst, WER
                # gerade an Bord ist und mit welchem Geraet. Ohne diese beiden
                # Angaben waere eine Sitzung nur ein Name ohne Gesicht.
                'herkunft': (herkunft or '')[:45],
                'geraet': (geraet or '')[:60],
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
        # Der Zeitpunkt wandert im Speicher bei JEDEM Zugriff mit — sonst
        # zeigte die Anwesenheit einen bis zu einer Stunde alten Stand.
        # Geschrieben wird trotzdem hoechstens alle zehn Minuten: auf einer
        # SD-Karte ist jeder Schreibvorgang einer zu viel.
        jetzt = time.time()
        vorher = s.get('zuletzt', 0)
        s['zuletzt'] = jetzt
        if jetzt - vorher > 600:
            with self._lock:
                self._sichern_sitzungen()
        return dict(konto, _sitzung_kiosk=bool(s.get('kiosk')))

    def sitzungen(self) -> list[dict]:
        """Die offenen Sitzungen, fuer die Anwesenheitsanzeige.

        OHNE die Sitzungskennung — die ist das Geheimnis, mit dem man die
        Sitzung uebernehmen koennte. Sie hat in keiner Anzeige etwas zu suchen,
        auch nicht in einer, die nur der Eigner sieht.
        """
        jetzt = time.time()
        raus = []
        for si in self._sitzungen.values():
            zuletzt = si.get('zuletzt', 0)
            if not si.get('kiosk') and jetzt - zuletzt > SITZUNG_DAUER_S:
                continue                       # abgelaufen, wird beim naechsten Zugriff entfernt
            raus.append({
                'konto': si.get('konto'),
                'seit': si.get('seit'),
                'zuletzt': zuletzt,
                'kiosk': bool(si.get('kiosk')),
                'herkunft': si.get('herkunft') or '',
                'geraet': si.get('geraet') or '',
            })
        return sorted(raus, key=lambda x: x['zuletzt'], reverse=True)

    def konto_nach_name(self, name: str):
        """Ein Konto ohne Sitzung nachschlagen — fuer Aufrufe, die der Server
        ueber die Sync-Verbindung hereinreicht.

        Prueft dieselben Ausschluesse wie die Anmeldung: gesperrt oder
        abgelaufen heisst kein Konto. Sonst waere dies der Weg, auf dem ein
        entzogener Zugang doch noch wirkt.
        """
        konto = self._konten.get((name or '').strip())
        if not konto or konto.get('gesperrt'):
            return None
        if k.abgelaufen(konto, _jetzt_falls_verlaesslich()):
            return None
        return dict(konto)

    def einladen(self, name: str, rolle: str, person: str = '',
                 spitzname: str = '') -> tuple[str, dict]:
        """Ein Konto ohne Passwort anlegen und einen Einladungslink erzeugen.

        Der Sinn: ein Passwort, das jemand anders vergeben hat, wandert per
        Nachricht durch die Gegend und wird selten geaendert. Wer eingeladen
        wird, setzt es selbst — und niemand sonst kennt es je.

        Bis das geschehen ist, hat das Konto KEINEN Hash. Anmelden kann sich
        damit niemand: die Pruefung faellt auf den Blindwert zurueck und
        scheitert. Das gilt auch an Bord, wohin das Konto als Kopie geht.
        """
        if rolle not in r.ROLLEN:
            raise k.KontoFehler(f'Unbekannte Rolle: {rolle!r}')
        name = (name or '').strip()
        if not name or len(name) > 64:
            raise k.KontoFehler('Der Anmeldename fehlt oder ist zu lang.')
        klartext, kennung = k.einladung_erzeugen()
        jetzt = time.time()
        konto = {
            'name': name,
            'person': (person or '').strip()[:80],
            'spitzname': (spitzname or '').strip()[:40],
            'hash': None,
            'rolle': rolle,
            'gesperrt': False,
            'gueltig_bis': None,
            'angelegt': jetzt,
            'einladung': {'kennung': kennung, 'bis': jetzt + k.EINLADUNG_DAUER_S,
                          'von': jetzt},
        }
        with self._lock:
            if name in self._konten:
                raise k.KontoFehler(f'Den Namen {name!r} gibt es schon.')
            self._konten[name] = konto
            self._sichern_konten()
        log.info('Konto %r eingeladen (%s), Link gilt %d Tage',
                 name, rolle, k.EINLADUNG_DAUER_S // 86400)
        return klartext, {'name': name, 'rolle': rolle,
                          'anzeigename': k.anzeigename(konto)}

    def neu_einladen(self, name: str) -> str:
        """Einen neuen Einladungslink fuer ein BESTEHENDES Konto erzeugen.

        Zwei Faelle, derselbe Weg: die erste Einladung ist verfallen oder nie
        angekommen — oder jemand hat sein Passwort vergessen. Statt ihm ein
        neues zu diktieren (das er dann per Nachricht bekommt und nie aendert),
        setzt er es ueber denselben Link selbst.

        Das alte Passwort bleibt WEITER GUELTIG, bis der Link eingeloest wird.
        Sonst wuerde ein Link, der nie ankommt, jemanden aussperren — und der
        haeufigste Grund fuer eine Neueinladung ist ja gerade, dass der erste
        nicht angekommen ist.
        """
        with self._lock:
            konto = self._konten.get((name or '').strip())
            if not konto:
                raise k.KontoFehler(f'Kein Konto namens {name!r}.')
            if konto.get('gesperrt'):
                raise k.KontoFehler('Dieses Konto ist gesperrt — erst entsperren.')
            klartext, kennung = k.einladung_erzeugen()
            jetzt = time.time()
            konto['einladung'] = {'kennung': kennung, 'bis': jetzt + k.EINLADUNG_DAUER_S,
                                  'von': jetzt}
            self._sichern_konten()
        log.info('Neuer Einladungslink für %r', konto['name'])
        return klartext

    def einladung_pruefen(self, name: str, token: str) -> dict | None:
        """Zu welchem Konto ein Einladungslink gehoert — oder None.

        Zurueck kommt NUR, was die Einladungsseite anzeigen soll: Name und
        Rolle. Nichts ueber das Boot, nichts ueber andere Konten.
        """
        konto = self._konten.get((name or '').strip())
        if not konto or not k.einladung_gueltig(konto, token, time.time()):
            return None
        return {'name': konto['name'], 'anzeigename': k.anzeigename(konto),
                'rolle': konto.get('rolle'),
                'rolle_name': r.rolle(konto.get('rolle'))['name'],
                'handlungen': list(r.rolle(konto.get('rolle'))['handlungen'])}

    def einladung_einloesen(self, name: str, token: str, passwort: str) -> dict:
        """Das selbstgewaehlte Passwort setzen und die Einladung verbrauchen."""
        with self._lock:
            konto = self._konten.get((name or '').strip())
            if not konto or not k.einladung_gueltig(konto, token, time.time()):
                raise k.KontoFehler('Dieser Link gilt nicht mehr.')
            k.passwort_pruefen(passwort, konto['name'])
            konto['hash'] = k.hash_erzeugen(passwort)
            # Verbraucht: ein Link, der zweimal geht, ist ein Link, der
            # weitergegeben werden kann.
            konto.pop('einladung', None)
            self._sichern_konten()
        log.info('Einladung von %r eingelöst', konto['name'])
        return {'name': konto['name'], 'rolle': konto.get('rolle')}

    def offene_einladung(self, name: str) -> dict | None:
        konto = self._konten.get((name or '').strip())
        e = (konto or {}).get('einladung')
        return {'bis': e.get('bis')} if e else None

    def aendern(self, name: str, *, rolle=None, gesperrt=None,
                passwort=None, laeuft_ab=None, person=None, spitzname=None) -> dict:
        """Rolle, Sperre, Passwort oder Ablauf eines Kontos aendern.

        Wird ein Konto gesperrt oder sein Passwort gewechselt, verfallen SEINE
        Sitzungen sofort. Das ist der eigentliche Zweck des Sperrens: solange
        eine alte Sitzung weiterlaeuft, ist niemand ausgesperrt.
        """
        with self._lock:
            konto = self._konten.get((name or '').strip())
            if not konto:
                raise k.KontoFehler(f'Kein Konto namens {name!r}.')
            if rolle is not None:
                if rolle not in r.ROLLEN:
                    raise k.KontoFehler(f'Unbekannte Rolle: {rolle!r}')
                konto['rolle'] = rolle
            if gesperrt is not None:
                konto['gesperrt'] = bool(gesperrt)
            if laeuft_ab is not None:
                # Das Feld heisst im Kontenmodell `gueltig_bis` — `laeuft_ab`
                # war ein zweiter Name fuer dieselbe Sache, und die Pruefung
                # (k.abgelaufen) sah nur den einen. Ein befristeter Zugang lief
                # damit nie ab.
                konto['gueltig_bis'] = float(laeuft_ab) if laeuft_ab else None
            if person is not None:
                konto['person'] = str(person).strip()[:80]
            if spitzname is not None:
                konto['spitzname'] = str(spitzname).strip()[:40]
            if passwort:
                konto['hash'] = k.hash_erzeugen(passwort)
            if gesperrt or passwort:
                weg = [kn for kn, si in self._sitzungen.items()
                       if si.get('konto') == konto['name']]
                for kn in weg:
                    self._sitzungen.pop(kn, None)
                if weg:
                    self._sichern_sitzungen()
                    log.info('%d Sitzungen von %r beendet', len(weg), konto['name'])
            self._sichern_konten()
        log.info('Konto %r geändert', konto['name'])
        return {'name': konto['name'], 'rolle': konto.get('rolle'),
                'person': konto.get('person') or '',
                'spitzname': konto.get('spitzname') or '',
                'anzeigename': k.anzeigename(konto),
                'gesperrt': bool(konto.get('gesperrt')),
                'gueltig_bis': konto.get('gueltig_bis')}

    def passwort_selbst_aendern(self, name: str, altes: str, neues: str) -> None:
        """Sein eigenes Passwort aendern — mit Nachweis des alten.

        Der Nachweis ist der Punkt: eine Sitzung kann auf einem fremden,
        offenen Geraet liegen. Ohne das alte Passwort koennte jeder, der kurz
        an ein unbeaufsichtigtes Handy kommt, das Konto uebernehmen.

        Alle anderen Sitzungen enden dabei — wer sein Passwort aendert, tut das
        oft, WEIL es abhandengekommen ist.
        """
        konto = self._konten.get((name or '').strip())
        if not konto or not k.hash_pruefen(altes or '', konto.get('hash') or _BLIND):
            raise k.KontoFehler('Das bisherige Passwort stimmt nicht.')
        with self._lock:
            konto['hash'] = k.hash_erzeugen(neues)
            weg = [kn for kn, si in self._sitzungen.items() if si.get('konto') == konto['name']]
            for kn in weg:
                self._sitzungen.pop(kn, None)
            self._sichern_konten()
            self._sichern_sitzungen()
        log.info('%r hat sein Passwort selbst geändert, %d Sitzungen beendet',
                 konto['name'], len(weg))

    def sitzungen_beenden(self, name: str) -> int:
        """Alle Sitzungen eines Kontos beenden — fuer ein verlorenes Geraet.

        Bisher ging das nur ueber den Umweg eines Passwortwechsels. Der wirkt
        zwar, zwingt aber jemanden, sich ein neues auszudenken, obwohl das alte
        in Ordnung ist.
        """
        with self._lock:
            weg = [kn for kn, si in self._sitzungen.items()
                   if si.get('konto') == (name or '').strip()]
            for kn in weg:
                self._sitzungen.pop(kn, None)
            if weg:
                self._sichern_sitzungen()
        log.info('%d Sitzungen von %r beendet', len(weg), name)
        return len(weg)

    def einladung_zuruecknehmen(self, name: str) -> None:
        """Eine offene Einladung ungueltig machen, ohne das Konto zu loeschen.

        Bisher blieb nur, das ganze Konto zu entfernen — und damit auch seine
        Rolle und seinen Namen, obwohl man nur den Link zurueckziehen wollte.
        """
        with self._lock:
            konto = self._konten.get((name or '').strip())
            if not konto or not konto.get('einladung'):
                raise k.KontoFehler('Für dieses Konto ist keine Einladung offen.')
            konto.pop('einladung', None)
            self._sichern_konten()
        log.info('Einladung von %r zurückgenommen', name)

    def loeschen(self, name: str) -> None:
        with self._lock:
            konto = self._konten.pop((name or '').strip(), None)
            if not konto:
                raise k.KontoFehler(f'Kein Konto namens {name!r}.')
            for kn in [kn for kn, si in self._sitzungen.items()
                       if si.get('konto') == konto['name']]:
                self._sitzungen.pop(kn, None)
            self._sichern_konten()
            self._sichern_sitzungen()
        log.info('Konto %r gelöscht', konto['name'])

    def zum_verteilen(self) -> dict:
        """Die Kopie fuer den Pi, mitsamt einer Kennung ihres Standes.

        Der Passwort-Hash MUSS mit: sonst koennte an Bord ohne Internet
        niemand anmelden, und genau dafuer ist die Kopie da. Er ist ein
        scrypt-Hash — wer die Datei auf dem Pi liest, hat damit noch kein
        Passwort, sondern nur sehr viel Rechenarbeit vor sich.

        Die Kennung ist ein Hash ueber den Inhalt, kein Zaehler: sie stimmt
        auch dann noch, wenn eine Seite zwischendurch neu gestartet ist.
        """
        with self._lock:
            liste = [
                {'name': kt['name'], 'hash': kt.get('hash'), 'rolle': kt.get('rolle'),
                 'person': kt.get('person') or '', 'spitzname': kt.get('spitzname') or '',
                 'gesperrt': bool(kt.get('gesperrt')),
                 # Die Befristung MUSS mit: sonst laeuft ein Technikerzugang an
                 # Bord weiter, waehrend er auf dem Server laengst abgelaufen
                 # ist — und das Bordnetz ist genau der Ort, an dem er dann
                 # noch schalten koennte.
                 'gueltig_bis': kt.get('gueltig_bis'),
                 # Die Uebersteuerungen MUESSEN mit: sonst gilt an Bord wieder
                 # die blosse Rolle, und ein einzeln entzogenes Recht waere
                 # ueber das Bord-WLAN wieder da.
                 'handlungen': kt.get('handlungen'),
                 # Die Einladung selbst geht NICHT mit an Bord: eingeloest wird
                 # sie beim Server, und der Pi braucht sie nie. Was nicht
                 # mitfaehrt, kann unterwegs auch nicht abhandenkommen.
                 'oberflaechen': kt.get('oberflaechen')}
                for kt in sorted(self._konten.values(), key=lambda x: x['name'])
            ]
        roh = json.dumps(liste, sort_keys=True, ensure_ascii=False).encode()
        # Die offenen Sitzungen fahren mit. Der Grund ist handfest: die Anlage
        # hat drei Namen, und wer sich unter einem anmeldet, soll unter den
        # anderen angemeldet SEIN. Das Cookie gilt inzwischen fuer alle drei —
        # aber es nuetzt nichts, wenn die Gegenseite die Sitzung nicht kennt.
        #
        # Uebertragen wird nur die KENNUNG (der SHA-256), nie das Token selbst.
        # Wer die Uebertragung mitliest, kann damit keine Sitzung uebernehmen.
        with self._lock:
            sitzungen = {kn: dict(si) for kn, si in self._sitzungen.items()}
        return {'konten': liste, 'stand': hashlib.sha256(roh).hexdigest()[:16],
                'sitzungen': sitzungen}

    def sitzungen_uebernehmen(self, fremde: dict) -> int:
        """Sitzungen der Gegenseite aufnehmen, ohne die eigenen zu verlieren.

        Zusammengefuehrt, nicht ersetzt: an Bord kann sich jemand angemeldet
        haben, waehrend das Boot ohne Internet war. Diese Sitzung soll nicht
        verschwinden, nur weil der Server sie nicht kennt.

        Sitzungen zu Konten, die es nicht (mehr) gibt, fallen weg — sonst
        liesse sich ein geloeschtes Konto ueber eine alte Sitzung
        weiterbenutzen.
        """
        if not isinstance(fremde, dict):
            return 0
        dazu = 0
        with self._lock:
            for kennung, si in fremde.items():
                if not isinstance(si, dict) or si.get('konto') not in self._konten:
                    continue
                da = self._sitzungen.get(kennung)
                if da is None:
                    self._sitzungen[kennung] = dict(si)
                    dazu += 1
                elif si.get('zuletzt', 0) > da.get('zuletzt', 0):
                    da.update(si)
            if dazu:
                self._aufraeumen()
                self._sichern_sitzungen()
        return dazu

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
