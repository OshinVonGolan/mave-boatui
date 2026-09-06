"""Wer darf was — an einer Stelle, nicht an 48.

Die App hat 48 Endpunkte. Sie einzeln abzusichern hiesse, an 48 Stellen an
dieselbe Regel zu denken, und beim 49. wird sie vergessen. Deshalb entscheidet
eine Middleware, und zwar nach einer Tabelle, die man an einem Stueck lesen
kann.

## Die Schonfrist bei der Erstinbetriebnahme

Solange KEIN Konto angelegt ist, bleibt alles offen. Das klingt falsch, ist
aber die einzige Reihenfolge, die funktioniert: Auf dem Pi liegt heute keine
Kontendatei. Wuerde die Pflicht sofort greifen, waere die Anlage nach dem
Update unbedienbar — niemand koennte sich anmelden, und niemand koennte ein
Konto anlegen, weil auch DAS eine Anmeldung braeuchte.

Die Oberflaeche zeigt in diesem Zustand einen deutlichen Hinweis. Sobald das
erste Konto steht, gilt die Pflicht ohne Ausnahme.

## Warum Rechte und nicht nur "angemeldet"

Ein Gast darf zusehen, aber nicht schalten; der Kiosk am Kartentisch darf
schalten, aber keine Konten verwalten. Das steht in sync/rechte.py und gilt auf
beiden Seiten — hier wird es nur angewandt.
"""
from __future__ import annotations

import logging
import re

from sync import rechte as r

# Name des Sitzungscookies. Steht hier und nicht zweimal: liefen die beiden
# Seiten hier auseinander, waere man auf einer von beiden staendig abgemeldet,
# ohne dass ein Fehler sichtbar wuerde.
SITZUNG_COOKIE = 'mave_sitzung'


def geraet_aus_ua(ua: str) -> str:
    """Aus der Browserkennung eine lesbare Geraetebezeichnung machen.

    Absichtlich grob. Es geht um die Frage "welches Geraet ist das?" an einem
    Boot mit einer Handvoll Geraeten — nicht um Statistik. Eine vollstaendige
    Auswertung waere viel Code fuer eine Angabe, die ohnehin nur der Eigner
    liest, und die Kennungen luegen bekanntlich.
    """
    u = (ua or '')
    if not u:
        return 'unbekannt'
    system = ('iPhone' if 'iPhone' in u else
              'iPad' if 'iPad' in u else
              'Android' if 'Android' in u else
              'Mac' if 'Macintosh' in u or 'Mac OS' in u else
              'Windows' if 'Windows' in u else
              'Linux' if 'Linux' in u else '')
    browser = ('Firefox' if 'Firefox/' in u else
               'Edge' if 'Edg/' in u else
               'Chrome' if 'Chrome/' in u or 'CriOS' in u else
               'Safari' if 'Safari/' in u else '')
    if system and browser:
        return f'{browser} auf {system}'
    return system or browser or 'unbekannt'


def herkunft_erlaubt(origin: str, gastgeber: str) -> bool:
    """Ob ein WebSocket-Handschlag von einer zulaessigen Seite kommt.

    Ein Browser schickt beim Handschlag die Cookies mit, aber die uebliche
    Bremse gegen fremde Seiten (SameSite) greift hier nicht verlaesslich. Ohne
    diese Pruefung kann JEDE Webseite, die jemand an Bord aufruft, im
    Hintergrund eine Verbindung zum Boot oeffnen und mitlesen — die Anmeldung
    des Nutzers erledigt das fuer sie.

    Solange die Seite von derselben Stelle kommt wie der Kanal, ist alles gut.
    Fehlt der Kopf ganz, stammt die Anfrage nicht aus einem Browser (Skript,
    Werkzeug, die Werkstatt-App) — das ist erlaubt, denn dort gibt es keine
    fremde Seite, die jemanden hereinlegen koennte, und das Token muss ohnehin
    stimmen.
    """
    if not origin:
        return True
    try:
        from urllib.parse import urlparse
        o = urlparse(origin)
    except Exception:
        return False
    if not o.hostname:
        return False
    # Gastgeber kann "192.168.1.103:8080" sein — der Port spielt keine Rolle,
    # entscheidend ist der Name.
    eigener = (gastgeber or '').split(':')[0].strip().lower()
    return o.hostname.lower() == eigener


def keks_bereich(gastgeber: str) -> str | None:
    """Fuer welche Adressen das Sitzungscookie gelten soll.

    Die Anlage hat drei Namen unter einer gemeinsamen Wurzel:
    mave.…, pi.mave.… und logbuch.… . Ohne diese Angabe gilt ein Cookie nur
    fuer den Namen, unter dem es gesetzt wurde — und man muss sich beim Wechsel
    zwischen Bordansicht und Logbuch jedes Mal neu anmelden. Genau das ist
    passiert: in der Bordansicht war der Eigner angemeldet, im Logbuch ein
    Gast, und die Abweisung sah aus wie ein Rechtefehler.

    Zurueck kommt None, wenn der Gastgeber keine solche Adresse ist (eine
    IP, "localhost", ein anderer Name). Eine Domain auf eine IP zu setzen wird
    vom Browser ohnehin verworfen — dann lieber gar nicht erst.
    """
    h = (gastgeber or '').split(':')[0].strip().lower()
    if not h or h == 'localhost':
        return None
    if all(t.isdigit() for t in h.split('.')):      # IPv4
        return None
    teile = h.split('.')
    if len(teile) < 2:
        return None
    # Die gemeinsame Wurzel ist der Name mit seiner Endung — also
    # "circuit-sailor.com" fuer alle drei. Weiter hinauf zu gehen waere falsch
    # und wuerde vom Browser abgelehnt.
    return '.' + '.'.join(teile[-2:])


def token_aus(request) -> str:
    """Die Sitzung aus einer Anfrage holen — Cookie zuerst, dann Kopfzeile.

    Das Cookie ist der Hauptweg: es geht bei jedem Aufruf von selbst mit, auch
    beim Laden des Bundles und beim WebSocket-Handschlag, wo ein Browser keine
    eigenen Koepfe setzen kann. Der Bearer-Kopf bleibt fuer Werkzeuge und
    Skripte.
    """
    keks = request.cookies.get(SITZUNG_COOKIE)
    if keks:
        return keks
    kopf = request.headers.get('authorization', '')
    return kopf[7:].strip() if kopf.lower().startswith('bearer ') else ''


log = logging.getLogger(__name__)

# Immer offen. Kurz halten und jede Zeile begruenden.
OFFEN = (
    '/api/login',            # sonst kaeme niemand herein
    '/api/system/version',   # die PWA muss erkennen, mit wem sie spricht
    '/api/jserror',          # Fehlermeldungen entstehen VOR der Anmeldung
    '/api/zugang',           # sagt, ob ueberhaupt ein Konto existiert
    '/api/stand',            # sagt nur, ob die Oberflaeche veraltet ist
    '/api/einladung',        # wer eingeladen ist, hat noch kein Passwort
)

# Welches Recht ein Pfad verlangt. Erster Treffer gewinnt, deshalb steht das
# Besondere vor dem Allgemeinen.
REGELN: tuple[tuple[str, str, str], ...] = (
    # (Methode oder '*', Muster, Recht)
    ('*',    r'^/api/konten',                r.VERWALTEN),
    # Das eigene Konto: angemeldet sein genuegt. Wer hier mehr verlangte,
    # koennte sein Passwort nicht aendern, ohne den Eigner zu fragen.
    ('POST', r'^/api/mein/',                 r.LESEN),
    ('POST', r'^/api/system/update$',        r.FERNWARTEN),
    # Zurueckgehen ist ein Eingriff in den laufenden Code, keine Bedienung.
    # Ohne diese Zeile fiele es unter die Vorgabe "Schalten" — und die hat
    # jedes Crewmitglied.
    ('POST', r'^/api/system/zurueck$',       r.FERNWARTEN),
    ('POST', r'^/api/system/time-sync$',     r.FERNWARTEN),
    ('POST', r'^/api/sync/',                 r.EINSTELLEN),
    # Der Mitschnitt enthaelt Fehlermeldungen mitsamt Pfaden und Innereien.
    # Lesen reicht dafuer nicht — das hat jeder Gast.
    ('GET',  r'^/api/debug/',                r.FERNWARTEN),
    # Auch LESEND: die Einstellungen zeigen, wie die Anlage aufgebaut ist —
    # Grenzwerte, Geraete, Netzzugaenge. Ein Gast soll die Werte sehen, nicht
    # die Anlage. Vorher fiel das GET unter die Vorgabe "Lesen", und die hat
    # jeder.
    ('*',    r'^/api/settings',              r.EINSTELLEN),
    # Wer im Netz haengt, geht einen Gast nichts an: die Geraeteuebersicht
    # nennt jedes Geraet im Bord-WLAN mit Namen und Adresse. Das ist eine
    # Aussage ueber ANWESENHEIT von Menschen, nicht ueber das Boot.
    ('*',    r'^/api/network',               r.EINSTELLEN),
    ('GET',  r'^/api/devices$',              r.EINSTELLEN),
    ('*',    r'^/api/devices/',              r.EINSTELLEN),
    ('*',    r'^/api/pgn/',                  r.EINSTELLEN),
    ('*',    r'^/api/alarms/rules',          r.EINSTELLEN),
    ('PUT',  r'^/api/(wartung|stauplan)$',   r.EINSTELLEN),
    # Gaeste-WLAN: LESEN genuegt zum Anzeigen, EINSTELLEN zum Aendern.
    #
    # Das Passwort geht beim Lesen mit hinaus, und das ist Absicht: es ist das
    # GAESTEnetz, es steht im QR-Code, und der haengt an der Wand im Salon.
    # Wer ein Konto auf diesem Boot hat, ist an Bord. Etwas strenger zu tun,
    # als die Sache ist, waere hier nur Theater.
    ('PUT',  r'^/api/wlan$',                 r.EINSTELLEN),
    # Der Grundriss ist keine Bedienung, sondern der Aufbau des Bootes: Raeume,
    # Namen, Umriss. An ihm haengen Stauplan und Geraeteseite — wer ihn
    # umzeichnet, aendert, wo fuer ALLE die Dinge liegen. Deshalb dieselbe
    # Schwelle wie fuer die uebrigen Einstellungen und nicht die Vorgabe fuer
    # schreibende Aufrufe (Schalten), unter die er sonst fiele.
    #
    # Gezeichnet wird er im Logbuch auf dem Server; hierher kommt er als
    # fertige Datei. Auch dafuer gilt: einstellen, nicht schalten.
    ('PUT',  r'^/api/grundriss$',            r.EINSTELLEN),
    # Das Planungswerkzeug samt Planvorlage liegt unter /api/logbuch/ — dort
    # prueft der Server selbst.
    # Wartungsplan und Bootsaufgaben gehen einen Gast nichts an. Sie sagen,
    # was an dieser Anlage kaputt ist, was ansteht und was sie gekostet hat —
    # das ist eine Aussage ueber das BOOT und seinen Zustand, nicht ueber die
    # Werte, die gerade anliegen. Ein Gast sieht den Ladestand, nicht die
    # Mängelliste.
    #
    # Die Schwelle ist SCHALTEN und nicht EINSTELLEN: die Crew soll beides
    # sehen und abhaken koennen, sie faehrt das Boot. Nur der Gast bleibt
    # draussen — er ist der einzige, der nicht schalten darf.
    ('*',    r'^/api/wartung',                r.SCHALTEN),
    ('*',    r'^/api/monday',                 r.SCHALTEN),
    # Und der Uplink zur selben Schwelle wie die Geraeteliste daneben: er nennt
    # Anbieter, Signal, Adressen — wie die Anlage ans Netz kommt, nicht wie es
    # dem Boot geht. Beides steht jetzt auf einer Seite, also gilt beides gleich.
    ('*',    r'^/api/connectivity',           r.EINSTELLEN),
    ('PUT',  r'^/api/devices/registry$',     r.EINSTELLEN),
    ('PATCH', r'^/api/lights/preset/',       r.EINSTELLEN),   # Preset AENDERN
    # Sich fuer Meldungen anmelden darf jeder, der die Werte sehen darf. Die
    # Meldung selbst sagt nichts, was er nicht ohnehin sehen koennte — und
    # unter die Vorgabe fuer POST (schalten) zu fallen hiesse, dass ein Gast
    # ueber einen Alarm nicht benachrichtigt werden kann, den er auf dem Schirm
    # sehen darf.
    ('*',    r'^/api/push/',                  r.LESEN),
    # Alles andere Schreibende ist Bedienen. Das ist die VORGABE fuer neue
    # Endpunkte, und sie ist bewusst gewaehlt: die meisten schreibenden Aufrufe
    # dieser Anlage sind Bedienung. Wer einen Endpunkt baut, der mehr tut —
    # Konten, Fernwartung, Einstellungen —, traegt ihn oben ein. Diese Datei
    # ist die Stelle, an der man das nachliest.
    ('POST', r'^/api/',                      r.SCHALTEN),
    ('PUT',  r'^/api/',                      r.SCHALTEN),
    ('PATCH', r'^/api/',                     r.SCHALTEN),
    ('DELETE', r'^/api/',                    r.SCHALTEN),
    # Und alles Lesende ist Lesen.
    ('GET',  r'^/api/',                      r.LESEN),
)

_VORBEREITET = tuple((m, re.compile(muster), recht) for m, muster, recht in REGELN)


def recht_fuer(methode: str, pfad: str) -> str | None:
    """Welches Recht dieser Aufruf verlangt, oder None wenn keines noetig ist."""
    if pfad in OFFEN or not pfad.startswith('/api/'):
        return None            # statische Dateien und die offene Liste
    for m, muster, recht in _VORBEREITET:
        if (m == '*' or m == methode) and muster.search(pfad):
            return recht
    # Hierher kommt nur, was mit /api/ beginnt und keine der ueblichen
    # Methoden benutzt (HEAD, OPTIONS, exotisches). Dann das strengste Recht:
    # ein unbekannter Aufruf soll auffallen, indem er abgewiesen wird, statt
    # unbemerkt offenzustehen.
    return r.VERWALTEN


def pruefen(konto, methode: str, pfad: str, *, schonfrist: bool) -> tuple[bool, int, str]:
    """(erlaubt, Statuscode, Meldung).

    Der Statuscode unterscheidet zwei Faelle, die oft verwechselt werden:
    401 heisst "melde dich an", 403 heisst "du bist angemeldet, darfst aber
    nicht". Die Oberflaeche braucht den Unterschied — beim ersten zeigt sie das
    Anmeldefenster, beim zweiten waere das eine Zumutung.
    """
    recht = recht_fuer(methode, pfad)
    if recht is None:
        return True, 200, ''
    if schonfrist:
        return True, 200, ''
    if not konto:
        return False, 401, 'Bitte anmelden.'
    if not r.darf(konto, recht):
        return False, 403, 'Dafür fehlt die Berechtigung.'
    return True, 200, ''
