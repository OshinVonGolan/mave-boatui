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
)

# Welches Recht ein Pfad verlangt. Erster Treffer gewinnt, deshalb steht das
# Besondere vor dem Allgemeinen.
REGELN: tuple[tuple[str, str, str], ...] = (
    # (Methode oder '*', Muster, Recht)
    ('*',    r'^/api/konten',                r.VERWALTEN),
    ('POST', r'^/api/system/update$',        r.FERNWARTEN),
    ('POST', r'^/api/system/time-sync$',     r.FERNWARTEN),
    ('POST', r'^/api/sync/',                 r.EINSTELLEN),
    ('*',    r'^/api/settings',              r.EINSTELLEN),
    ('*',    r'^/api/alarms/rules',          r.EINSTELLEN),
    ('PUT',  r'^/api/(wartung|stauplan)$',   r.EINSTELLEN),
    ('PUT',  r'^/api/devices/registry$',     r.EINSTELLEN),
    ('PATCH', r'^/api/lights/preset/',       r.EINSTELLEN),   # Preset AENDERN
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
