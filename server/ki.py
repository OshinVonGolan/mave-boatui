"""KI im Logbuch — EIN Zugang, den alle Funktionen benutzen.

Bewusst kein Anhaengsel einer einzelnen Funktion. Wer etwas dazubaut — einen
Bericht aus dem Mitschnitt, eine Erklaerung zu einem Alarm, eine
Zusammenfassung der Woche — ruft `frage()` und muss sich um Anmeldung,
Erneuerung und Fehler nicht mehr kuemmern. Stand heute nutzt ihn noch keine
Funktion; er ist die Grundlage, nicht das Gebaeude.

**Warum das Abo und kein Konsolen-Schluessel.** Eignerentscheidung: es laeuft
auf seinem eigenen Zugang. Angemeldet wird einmal ueber denselben Weg wie die
Claude-Code-Anmeldung (OAuth mit PKCE gegen claude.ai). Fuer ein Produkt, das
FREMDE Boote bedient, waere das der falsche Weg — dafuer gibt es Schluessel aus
der Konsole.

**Warum direkt an die Messages-API und nicht ueber die CLI.** Der Assistent
(`/home/joshy/assistant`) nimmt die Claude-Code-CLI, weil er einen Agenten mit
Werkzeugen braucht. Hier geht es um einzelne Fragen — "sieh dir dieses Bild an
und gib mir JSON". Dafuer genuegt ein Aufruf. Das spart Node und die CLI im
Abbild (rund das Doppelte an Groesse) und ein beschreibbares HOME in einem
Container, der bewusst `read_only` laeuft.

**Und deshalb auch urllib und nicht httpx.** Das Serverabbild ist absichtlich
klein — FastAPI, uvicorn, pywebpush, sonst nichts (siehe server/Dockerfile).
Eine neue Abhaengigkeit fuer zwei POST-Aufrufe waere hier nicht angemessen.
Beide Aufrufe blockieren und laufen deshalb in einem Faden.

**Zwei Fallen, beide teuer bezahlt** (siehe assistant/backend/agent.py):

  * An `console.anthropic.com` (Token-Tausch) darf KEIN Browser-User-Agent
    gehen — sonst 429. An `api.anthropic.com` muss einer mit.
  * Beim Aufruf von claude.ai muss `code=true` in der Adresse stehen, sonst
    "Invalid request format"; und die Parameter werden mit `+` fuer Leerzeichen
    kodiert (Standard-urlencode), nicht mit `%20`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import jsonio

log = logging.getLogger(__name__)

# Dieselben Endpunkte und derselbe Client wie die Claude-Code-Anmeldung.
_CLIENT = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
_AUTHORIZE = 'https://claude.ai/oauth/authorize'
_TOKEN = 'https://console.anthropic.com/v1/oauth/token'
_REDIRECT = 'https://console.anthropic.com/oauth/code/callback'
_SCOPE = 'org:create_api_key user:profile user:inference'

_API = 'https://api.anthropic.com/v1/messages'
_API_VERSION = '2023-06-01'
_API_BETA = 'oauth-2025-04-20'
_UA = 'mave-logbuch/1.0'

# Was zur Auswahl steht. Bewusst kurz: mehr Auswahl heisst hier nur mehr
# Gelegenheiten, das Falsche zu waehlen.
MODELLE = {
    'claude-sonnet-5': 'Sonnet 5 — schnell, fuer die meisten Aufgaben',
    'claude-opus-5': 'Opus 5 — gruendlicher, dafuer langsamer',
}
MODELL_VORGABE = 'claude-sonnet-5'

# Etwas Vorlauf vor dem Ablauf: ein Aufruf, der mitten im Fluss 401 bekommt,
# ist schwerer zu deuten als eine Erneuerung, die zu frueh kam.
_VORLAUF_S = 120


class KiFehler(Exception):
    """Etwas ging schief, und der Text ist fuer den Eigner gedacht."""


class Ki:
    """Der Zugang. Eine Instanz je Server, die Datei ist der Zustand."""

    def __init__(self, datei: Path):
        self.datei = datei
        self._offen: dict = {}          # laufende Anmeldung: verifier + state

    # ── Anmeldung ──────────────────────────────────────────────────────────

    def anmeldung_starten(self) -> dict:
        verifier = _b64url(os.urandom(32))
        pruefwert = _b64url(hashlib.sha256(verifier.encode('ascii')).digest())
        zustand = _b64url(os.urandom(24))
        self._offen = {'verifier': verifier, 'state': zustand, 'ts': time.time()}
        adresse = _AUTHORIZE + '?' + urlencode({
            # Ohne `code=true` lehnt claude.ai mit "Invalid request format" ab:
            # erst damit gibt es den Weg ueber einen Code zum Abtippen.
            'code': 'true',
            'client_id': _CLIENT, 'response_type': 'code',
            'redirect_uri': _REDIRECT, 'scope': _SCOPE,
            'code_challenge': pruefwert, 'code_challenge_method': 'S256',
            'state': zustand,
        })
        return {'url': adresse}

    async def anmeldung_abschliessen(self, eingabe: str) -> None:
        if not self._offen:
            raise KiFehler('Die Anmeldung wurde nicht begonnen — bitte den Knopf '
                           'noch einmal drücken.')
        roh = (eingabe or '').strip()
        if not roh:
            raise KiFehler('Kein Code eingegeben.')
        # Der Code kommt in der Form `code#state` aus der Adresszeile.
        code, _, zustand = roh.partition('#')
        nutzlast = {
            'grant_type': 'authorization_code', 'code': code,
            'state': zustand or self._offen.get('state', ''),
            'client_id': _CLIENT, 'redirect_uri': _REDIRECT,
            'code_verifier': self._offen.get('verifier', ''),
        }
        antwort = await self._token_holen(nutzlast)
        self._offen = {}
        self._ablegen(antwort)

    def abmelden(self) -> None:
        self.datei.unlink(missing_ok=True)

    def zustand(self) -> dict:
        d = self._gelesen()
        return {
            'verbunden': bool(d.get('refresh_token') or d.get('access_token')),
            'gueltig_bis': d.get('gueltig_bis'),
            'modelle': MODELLE,
        }

    # ── Fragen ─────────────────────────────────────────────────────────────

    async def frage(self, text: str, *, bilder: list[tuple[str, bytes]] | None = None,
                    modell: str | None = None, max_tokens: int = 4000,
                    system: str | None = None) -> str:
        """Eine Frage stellen und die Antwort als Text bekommen.

        `bilder` sind Paare aus Medientyp und Rohdaten — sie gehen VOR dem Text
        in die Nachricht, weil das Modell so zuerst sieht, worum es geht.
        """
        zugang = await self._zugang()
        inhalt: list[dict] = []
        for typ, daten in (bilder or []):
            inhalt.append({'type': 'image', 'source': {
                'type': 'base64', 'media_type': typ,
                'data': base64.b64encode(daten).decode('ascii')}})
        inhalt.append({'type': 'text', 'text': text})

        koerper: dict = {
            'model': modell if modell in MODELLE else MODELL_VORGABE,
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': inhalt}],
        }
        if system:
            koerper['system'] = system

        kopf = {
            'Authorization': f'Bearer {zugang}',
            'anthropic-version': _API_VERSION,
            'anthropic-beta': _API_BETA,
            # api.anthropic.com WILL eine Kennung sehen — anders als der
            # Token-Endpunkt, der bei einer Browser-Kennung mit 429 abweist.
            'User-Agent': _UA,
            'Content-Type': 'application/json',
        }
        code, roh = await asyncio.to_thread(_post, _API, koerper, kopf, 180)
        if code == 401:
            raise KiFehler('Der Zugang wurde abgelehnt. Bitte in den '
                           'Einstellungen neu anmelden.')
        if code == 429:
            raise KiFehler('Das Abo ist gerade am Limit. Später noch einmal.')
        if code >= 400:
            log.warning('KI-Aufruf fehlgeschlagen (%s): %s', code, roh[:300])
            raise KiFehler(f'Die Anfrage ist gescheitert ({code}).')

        try:
            antwort_json = json.loads(roh)
        except ValueError:
            raise KiFehler('Die Antwort war nicht zu lesen.') from None
        teile = [t.get('text', '') for t in (antwort_json.get('content') or [])
                 if t.get('type') == 'text']
        antwort = ''.join(teile).strip()
        if not antwort:
            raise KiFehler('Die Antwort war leer.')
        return antwort

    # ── Innereien ──────────────────────────────────────────────────────────

    def _gelesen(self) -> dict:
        return jsonio.read_json(self.datei, {}) or {}

    def _ablegen(self, antwort: dict) -> None:
        d = {
            'access_token': antwort.get('access_token', ''),
            'refresh_token': antwort.get('refresh_token', ''),
            'gueltig_bis': time.time() + float(antwort.get('expires_in') or 3600),
        }
        if not d['access_token']:
            raise KiFehler('Die Antwort enthielt keinen Zugang.')
        jsonio.write_json(self.datei, d)
        # Es ist der Schluessel zum Konto des Eigners — er geht niemanden sonst
        # etwas an, auch nicht andere Nutzer auf demselben Rechner.
        try:
            self.datei.chmod(0o600)
        except OSError:
            pass

    async def _zugang(self) -> str:
        d = self._gelesen()
        if not d:
            raise KiFehler('Kein KI-Zugang hinterlegt. Einstellungen › KI-Zugang.')
        if d.get('access_token') and time.time() < (d.get('gueltig_bis') or 0) - _VORLAUF_S:
            return d['access_token']
        if not d.get('refresh_token'):
            raise KiFehler('Der Zugang ist abgelaufen. Bitte neu anmelden.')
        antwort = await self._token_holen({
            'grant_type': 'refresh_token',
            'refresh_token': d['refresh_token'],
            'client_id': _CLIENT,
        })
        # Kommt kein neuer refresh_token mit, gilt der alte weiter.
        antwort.setdefault('refresh_token', d['refresh_token'])
        self._ablegen(antwort)
        return antwort['access_token']

    async def _token_holen(self, nutzlast: dict) -> dict:
        # KEIN User-Agent: console.anthropic.com antwortet auf eine
        # Browser-Kennung mit 429, und dann sieht es aus, als waere das Abo am
        # Limit.
        kopf = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        try:
            code, roh = await asyncio.to_thread(_post, _TOKEN, nutzlast, kopf, 30)
        except OSError as e:
            raise KiFehler(f'Anthropic war nicht erreichbar: {e}') from None
        if code not in (200, 201):
            log.warning('Token-Tausch fehlgeschlagen (%s): %s', code, roh[:300])
            raise KiFehler(f'Der Token-Tausch ist gescheitert ({code}). '
                           f'Stimmt der Code?')
        try:
            return json.loads(roh)
        except ValueError:
            raise KiFehler('Die Antwort war nicht zu lesen.') from None


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode('ascii').rstrip('=')


def _post(adresse: str, koerper: dict, kopf: dict, frist: float) -> tuple[int, str]:
    """POST mit JSON. Gibt (Status, Text) zurueck — auch bei 4xx und 5xx.

    urllib wirft bei Fehlercodes, und die Antwort steht dann in der Ausnahme.
    Genau die wird hier gebraucht: Anthropic schreibt in den Rumpf, WAS nicht
    stimmt, und das gehoert ins Protokoll.
    """
    anfrage = urllib.request.Request(
        adresse, data=json.dumps(koerper).encode(), headers=kopf, method='POST')
    try:
        with urllib.request.urlopen(anfrage, timeout=frist) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
