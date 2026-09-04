"""Das Protokoll zwischen Pi und Server.

Eine ausgehende WebSocket-Verbindung, vom Pi aufgebaut (er haengt hinter
Mobilfunk-NAT, eingehend geht nicht). Darueber laeuft alles in beide
Richtungen, in einem einheitlichen Umschlag:

    {"typ": "...", "folge": 4711, "zeit": {...}, "daten": {...}}

`folge` tragen nur Nachrichten, die lueckenlos ankommen muessen (Verlauf,
Ereignisse). Der Server nennt beim Verbinden seinen Stand, der Pi schickt ab
dort weiter — damit ist Nachliefern nach einem Funkloch eine Zeile Logik und
keine Zustandsmaschine.

Warum kein MQTT: FastAPI und websockets sind im Haus, es geht um EIN Boot, und
ein Broker waere ein dritter Dienst, der ausfallen kann.

Alles hier ist reine Rechnung. Wer sendet und wer empfaengt, steht in
sync_client.py (Pi) und im Server.
"""
from __future__ import annotations

from . import zeit as _zeit

# Fassung des Protokolls. Der Server lehnt eine Verbindung ab, deren Hauptzahl
# er nicht kennt — das ist billiger als ein halb verstandenes Paket.
FASSUNG = 1

# ── Nachrichtentypen ────────────────────────────────────────────────────────
# Pi -> Server
HALLO    = 'hallo'      # einmal nach dem Verbinden: wer bin ich, wo stehe ich
ZUSTAND  = 'zustand'    # der Live-Stand, gedrosselt (wie /api/status)
VERLAUF  = 'verlauf'    # ein Buendel Verlaufseintraege, mit Folgenummern
EREIGNIS = 'ereignis'   # Alarm, Stoerung, Systemereignis — sofort
QUITTUNG = 'quittung'   # Ergebnis eines Befehls
SITZUNG  = 'sitzung'     # eine neue Anmeldung an Bord, damit der Server sie kennt
PUSH     = 'push'        # ein Geraet moechte benachrichtigt werden — der Server sendet

# Server -> Pi
STAND    = 'stand'      # "ich habe Verlauf bis Folge N", dazu Kontenrevision
BEFEHL   = 'befehl'     # ein Schaltbefehl, den der Pi lokal ausfuehrt
KONTEN   = 'konten'     # die Kontenkopie, damit der Pi ohne Internet anmelden kann

# Beide Richtungen
PING     = 'ping'
PONG     = 'pong'

_VOM_PI      = frozenset({HALLO, ZUSTAND, VERLAUF, EREIGNIS, QUITTUNG, SITZUNG, PUSH, PING, PONG})
_VOM_SERVER  = frozenset({STAND, BEFEHL, KONTEN, PING, PONG})
_MIT_FOLGE   = frozenset({VERLAUF, EREIGNIS})

# Groessengrenze je Nachricht. Ein Verlaufsbuendel mit 500 Minutenmitteln liegt
# bei etwa 60 kB; 1 MB laesst Luft und haelt trotzdem einen Fehler oder einen
# Angriff davon ab, dem Server den Speicher zu fuellen.
MAX_BYTES = 1024 * 1024


class ProtokollFehler(ValueError):
    """Unbrauchbare Nachricht. Die Meldung geht ins Protokoll, nicht nach aussen."""


# ── Schnueren ───────────────────────────────────────────────────────────────

def umschlag(typ: str, daten=None, *, folge: int | None = None,
             wand=None, mono=None, gestellt: bool = False) -> dict:
    """Baut eine Nachricht. Die Zeitangaben sind drei, nicht eine — warum,
    steht in sync/zeit.py."""
    nachricht: dict = {'typ': typ, 'daten': daten if daten is not None else {}}
    if folge is not None:
        nachricht['folge'] = int(folge)
    if mono is not None or wand is not None:
        nachricht['zeit'] = _zeit.stempel(wand, mono, gestellt)
    return nachricht


def hallo(geraet: str, fassung: int, version: str, verlauf_folge: int,
          betriebsart: str, *, wand=None, mono=None, gestellt: bool = False) -> dict:
    """Die erste Nachricht des Pi. Sie sagt dem Server alles, was er braucht,
    um den Rest zu deuten: welche Fassung, welcher Stand, welche Betriebsart."""
    return umschlag(HALLO, {
        'geraet': geraet,
        'fassung': fassung,
        'version': version,
        'verlauf_folge': int(verlauf_folge),
        'betriebsart': betriebsart,
    }, wand=wand, mono=mono, gestellt=gestellt)


def stand(verlauf_bis: int, konten_stand: str = '', push_schluessel: str = '') -> dict:
    """Die Antwort des Servers: bis wohin er den Verlauf hat.

    `konten_stand` ist eine Kennung des Kontenbestands, kein Zaehler — ein
    Hash ueber den Inhalt. Ein Zaehler muesste dauerhaft mitgefuehrt werden und
    liefe nach einem Neustart der falschen Seite aus dem Tritt; die Kennung
    stimmt immer, weil sie aus den Daten selbst folgt. Der Pi vergleicht sie
    mit seiner eigenen und fordert nur bei Abweichung eine neue Kopie an.

    Der `push_schluessel` faehrt mit, weil das Boot ihn braucht und nicht
    selbst hat: meldet sich ein Geraet im Bordnetz fuer Benachrichtigungen an,
    fragt es den Pi nach dem oeffentlichen Schluessel — den kennt aber nur der
    Server, der auch sendet. Ein eigener Abruf dafuer waere ein zweites Rohr
    fuer eine Zeichenkette, die ohnehin bei jedem Verbinden vorbeikommt.
    """
    return umschlag(STAND, {'verlauf_bis': int(verlauf_bis),
                            'konten_stand': str(konten_stand or ''),
                            'push_schluessel': str(push_schluessel or '')})


def sitzung(kennung: str, daten: dict, beendet: bool = False) -> dict:
    """Eine an Bord entstandene Anmeldung, damit der Server sie auch kennt.

    Uebertragen wird nur die KENNUNG (der SHA-256 des Tokens), nie das Token
    selbst — wer die Verbindung mitliest, kann damit keine Sitzung uebernehmen.

    Mit `beendet` traegt dieselbe Nachricht das Gegenteil: eine Abmeldung an
    Bord. Ohne das bliebe die Sitzung auf dem Server bestehen, und wer sich an
    Bord abmeldet, waere im Logbuch weiter drin — eine Abmeldung, die nicht
    abmeldet, ist schlimmer als gar keine.
    """
    return umschlag(SITZUNG, {'kennung': kennung, 'sitzung': daten,
                              'beendet': bool(beendet)})


def push_abo(abo: dict, konto: str, geraet: str = '', abmelden: bool = False) -> dict:
    """Ein Geraet im Bordnetz moechte benachrichtigt werden.

    Es hat sich beim PI angemeldet — der ist im Bordnetz die Adresse, unter der
    die Anwendung laeuft. Senden kann aber nur der Server: ein Push-Abo zeigt
    auf den Dienst des Browserherstellers, und dorthin kommt man nur mit
    Internet. Der Pi reicht das Abo deshalb weiter, so wie er es mit
    Anmeldungen auch tut.
    """
    return umschlag(PUSH, {'abo': abo, 'konto': konto, 'geraet': geraet,
                           'abmelden': bool(abmelden)})


def konten(daten: dict) -> dict:
    """Die Kontenkopie fuer den Pi.

    Enthaelt die Passwort-Hashes: ohne sie koennte an Bord ohne Internet
    niemand anmelden, und genau dafuer ist die Kopie da.
    """
    return umschlag(KONTEN, daten)


# ── Aufschnueren ────────────────────────────────────────────────────────────

def pruefe(nachricht, *, vom_pi: bool) -> dict:
    """Prueft eine empfangene Nachricht und gibt sie zurueck.

    `vom_pi` sagt, aus welcher Richtung sie kommt: der Server darf keinen
    Befehl von einem Boot annehmen und der Pi keinen Zustand vom Server. Das
    ist keine Formsache — die Gegenstelle des Servers haengt im Internet.
    """
    if not isinstance(nachricht, dict):
        raise ProtokollFehler('Objekt erwartet.')
    typ = nachricht.get('typ')
    if not isinstance(typ, str):
        raise ProtokollFehler('typ fehlt.')
    erlaubt = _VOM_PI if vom_pi else _VOM_SERVER
    if typ not in erlaubt:
        raise ProtokollFehler(f'Typ {typ!r} ist aus dieser Richtung nicht erlaubt.')

    daten = nachricht.get('daten', {})
    if not isinstance(daten, dict) and not isinstance(daten, list):
        raise ProtokollFehler('daten muss Objekt oder Liste sein.')

    if typ in _MIT_FOLGE:
        folge = nachricht.get('folge')
        if not isinstance(folge, int) or isinstance(folge, bool) or folge < 0:
            raise ProtokollFehler(f'{typ} braucht eine Folgenummer ab 0.')

    z = nachricht.get('zeit')
    if z is not None:
        if not isinstance(z, dict):
            raise ProtokollFehler('zeit muss ein Objekt sein.')
        if z.get('mono') is not None and not _zahl(z.get('mono')):
            raise ProtokollFehler('zeit.mono muss eine Zahl sein.')
        if z.get('wand') is not None and not _zahl(z.get('wand')):
            raise ProtokollFehler('zeit.wand muss eine Zahl sein.')
    return nachricht


def zeitangaben(nachricht) -> tuple:
    """Die drei Zeitangaben einer Nachricht, mit Vorgaben."""
    z = (nachricht or {}).get('zeit') or {}
    return z.get('wand'), z.get('mono'), bool(z.get('gestellt'))


def fehlt_dazwischen(habe: int, kommt: int) -> bool:
    """Ob zwischen dem eigenen Stand und der naechsten Folgenummer eine Luecke
    liegt. Lueckenlos heisst: kommt == habe + 1."""
    return int(kommt) > int(habe) + 1


# ── Betriebsarten ───────────────────────────────────────────────────────────
# Im Hafen haengt das Boot ueber Starlink am Netz, dort ist Volumen kein Thema.
# Unterwegs laeuft es ueber Mobilfunk, dort schon. Umgeschaltet wird anhand des
# Uplinks, den connectivity.py ohnehin kennt (router.active_type).

VOLL       = 'voll'
GEDROSSELT = 'gedrosselt'

BETRIEBSARTEN = {
    VOLL:       {'zustand_s': 10, 'verlauf': 'fein',    'name': 'voll'},
    GEDROSSELT: {'zustand_s': 60, 'verlauf': 'minuten', 'name': 'gedrosselt'},
}

# Stellungen des Handschalters in den Einstellungen.
SCHALTER = ('auto', VOLL, GEDROSSELT)


def betriebsart(conn_status, schalter: str = 'auto') -> str:
    """Welche Betriebsart gilt.

    Der Handschalter gewinnt immer — wer im Hafen an einer teuren Marina-SIM
    haengt, soll drosseln koennen, obwohl der Router 'wifi' meldet.

    Automatisch: nur Mobilfunk drosselt. Ist der Uplink unbekannt (Router
    antwortet nicht), wird ebenfalls gedrosselt: im Zweifel sparsam, denn der
    haeufigste Grund fuer einen stummen Router ist eine schlechte Verbindung.
    """
    if schalter in (VOLL, GEDROSSELT):
        return schalter
    art = ((conn_status or {}).get('router') or {}).get('active_type')
    if not isinstance(art, str) or not art:
        return GEDROSSELT
    return GEDROSSELT if art == 'mobile' else VOLL


def takt(art: str) -> dict:
    """Die Zahlen zur Betriebsart. Unbekannte Angabe wird gedrosselt."""
    return BETRIEBSARTEN.get(art, BETRIEBSARTEN[GEDROSSELT])


def _zahl(wert) -> bool:
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)
