"""Der Server neben dem Boot.

Er tut drei Dinge und sonst nichts:

  1. Er nimmt die ausgehende Verbindung des Pi an (der haengt hinter
     Mobilfunk-NAT, eingehend geht nicht) und legt ab, was hereinkommt.
  2. Er liefert der PWA dieselben Pfade wie der Pi — /api/status, /api/history —
     aber aus seiner Kopie und IMMER mit Altersangabe.
  3. Er stellt spaeter das Diagnosewerkzeug bereit. Dessen Aufbau ist noch
     offen; hier steht nur das Tor davor.

Was er ausdruecklich NICHT tut: rechnen, regeln, entscheiden. Der Server ist
Zusatz, nie Voraussetzung (KONZEPT-SERVER.md). Faellt er aus, merkt das Boot
davon nichts.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import secrets
import time
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from server.befehle import (DURCHLEITEN, KeinBoot, Vermittlung,
                            Zeitueberschreitung, gesperrt)
from pydantic import BaseModel

from konten_speicher import Konten
from sync.konten import KontoFehler
from konten_speicher import SITZUNG_DAUER_S
from server.speicher import Speicher
from sync import protokoll as p
from sync import rechte as r
from sync import zugang as zg
from sync import zeit as sz

log = logging.getLogger('mave-server')

DATEN = Path(os.environ.get('MAVE_DATEN', '/daten'))
# Die Oberflaeche kommt aus DEMSELBEN Verzeichnis wie auf dem Pi. Nicht
# kopiert, nicht nachgebaut: dieselben Dateien, damit es nicht zwei Staende
# gibt, die auseinanderlaufen.
STATISCH = Path(os.environ.get('MAVE_STATISCH', '/app/static'))
# Das Token des Bootes. Ohne gesetztes Token nimmt der Server KEINE Verbindung
# an — ein offener Sammelpunkt im Internet waere schlimmer als keiner.
GERAET_TOKEN = os.environ.get('MAVE_GERAET_TOKEN', '')
# Uebergangszugang fuer die Leseseite, bis die Kontenverwaltung steht. Auch hier
# gilt: ohne gesetztes Passwort ist der Zugang zu, nicht offen.
UEBERGANG_PASSWORT = os.environ.get('MAVE_PASSWORT', '')
# Die Heizung aus der Ferne schalten ist standardmaessig GESPERRT — sie
# verbrennt Diesel in einem Boot, in dem niemand steht. Bewusst hier und nicht
# als Haekchen in der Oberflaeche: wer es einschaltet, soll es einmal bewusst
# tun und es in der Serverkonfiguration wiederfinden.
FERN_HEIZUNG = os.environ.get('MAVE_FERN_HEIZUNG', '').strip().lower() in ('ja', 'yes', '1')

app = FastAPI(title='Mave Server', docs_url=None, redoc_url=None, openapi_url=None)

speicher: Speicher | None = None
konten: Konten | None = None
_uhrbuch = sz.Uhrbuch()
_vermittlung = Vermittlung()
_verbindung: dict = {'sitzung': None, 'seit': None, 'geraet': None, 'betriebsart': None}


@app.on_event('startup')
def _start() -> None:
    global speicher
    DATEN.mkdir(parents=True, exist_ok=True)
    speicher = Speicher(DATEN / 'mave.db')
    global konten
    konten = Konten(DATEN / 'konten.json', DATEN / 'sitzungen.json')
    if not GERAET_TOKEN:
        log.error('MAVE_GERAET_TOKEN ist nicht gesetzt — das Boot kann sich nicht anmelden.')
    if konten.leer:
        if UEBERGANG_PASSWORT:
            log.warning('Noch kein Konto angelegt. Bis dahin gilt MAVE_PASSWORT als '
                        'Notzugang — damit das erste Konto angelegt werden kann.')
        else:
            log.error('Weder ein Konto noch MAVE_PASSWORT — niemand kommt herein.')
    else:
        log.info('%d Konten geladen', len(konten.liste()))
    log.info('Server bereit, Daten in %s', DATEN)


@app.on_event('shutdown')
def _stop() -> None:
    if speicher:
        speicher.schliessen()


# ── Zugang ──────────────────────────────────────────────────────────────────
# Uebergangsloesung, bis die Kontenverwaltung steht: ein Passwort, eine Rolle.
# Der Punkt ist, dass es zu KEINEM Zeitpunkt einen offenen Server im Internet
# gibt — das verbietet das Konzept ausdruecklich.

# Fehlversuche je Herkunft. Kein voller Ratenbegrenzer, sondern das Minimum,
# das dauerndes Probieren teuer macht: nach zehn Fehlschlaegen ist zehn Minuten
# Ruhe. Bei einem lang gewuerfelten Token waere das unnoetig, bei einem
# selbstgewaehlten Passwort nicht — und welches gesetzt wird, entscheidet nicht
# dieser Code.
_FEHLVERSUCHE: dict[str, list] = {}
_MAX_VERSUCHE = 10
_SPERRE_S = 600


def _herkunft(request: Request) -> str:
    # Hinter Caddy steht die echte Adresse im Kopf. Der Kopf ist nur
    # vertrauenswuerdig, WEIL zwischen Internet und Anwendung ausschliesslich
    # Caddy sitzt — der Container ist nicht selbst veroeffentlicht.
    weiter = request.headers.get('x-forwarded-for', '')
    if weiter:
        return weiter.split(',')[0].strip()
    return request.client.host if request.client else 'unbekannt'


def konto(request: Request) -> dict:
    """Wer da ist — oder 401.

    Solange KEIN Konto angelegt ist, gilt das Uebergangspasswort als Notzugang.
    Das ist kein offener Server: ohne dieses Passwort kommt auch dann niemand
    herein. Es ist nur der Weg, auf dem das erste Konto entsteht — sonst gaebe
    es die Henne-Ei-Lage, dass man ein Konto braucht, um ein Konto anzulegen.
    """
    token = zg.token_aus(request)
    k = konten.konto_zu_token(token) if (konten and token) else None
    if k:
        return k

    if konten and konten.leer and UEBERGANG_PASSWORT and token and \
            secrets.compare_digest(token, UEBERGANG_PASSWORT):
        # Notzugang bei der Erstinbetriebnahme. Traegt absichtlich einen
        # sprechenden Namen: taucht er spaeter irgendwo in einem Protokoll auf,
        # ist sofort klar, dass noch kein richtiges Konto besteht.
        return {'name': 'ersteinrichtung', 'rolle': 'eigner'}

    # Kein WWW-Authenticate: der Browser soll keinen eigenen Dialog aufmachen,
    # die Anmeldung gehoert in die Oberflaeche. (Genau dieser Dialog war es,
    # der Chrome die PWA-Installation verweigern liess.)
    raise HTTPException(401, detail='Anmeldung nötig')


def braucht_oberflaeche(welche: str):
    """Tor vor einer Oberflaeche.

    Das Ausblenden in der Anzeige ist Bequemlichkeit; hier faellt die
    Entscheidung. Wer die Adresse kennt, aber das Recht nicht hat, bekommt eine
    Abweisung und keine Seite.
    """
    def pruefen(k: dict = Depends(konto)) -> dict:
        if not r.darf_oberflaeche(k, welche):
            raise HTTPException(403, detail='Für diese Ansicht fehlt die Berechtigung')
        return k
    return pruefen


def braucht(handlung: str):
    def pruefen(k: dict = Depends(konto)) -> dict:
        if not r.darf(k, handlung):
            raise HTTPException(403, detail='Dafür fehlt die Berechtigung')
        return k
    return pruefen


# ── Die Verbindung zum Boot ─────────────────────────────────────────────────

@app.websocket('/sync')
async def sync(ws: WebSocket) -> None:
    """Der Pi verbindet sich hierher und bleibt.

    Das Token kommt im Kopf, nicht in der Adresse: Adressen landen in
    Protokollen, Koepfe nicht.
    """
    kopf = ws.headers.get('authorization', '')
    gegeben = kopf[7:].strip() if kopf.lower().startswith('bearer ') else ''
    if not GERAET_TOKEN or not gegeben or not secrets.compare_digest(gegeben, GERAET_TOKEN):
        await ws.close(code=4401)
        return
    await ws.accept()
    _vermittlung.verbunden(ws)

    sitzung = None
    try:
        while True:
            text = await ws.receive_text()
            # Groesse VOR dem Parsen pruefen: json.loads eines 500-MB-Textes
            # belegt den Speicher, bevor irgendeine Pruefung greift. Das Token
            # schuetzt davor nicht — es koennte gestohlen sein.
            if len(text) > p.MAX_BYTES:
                log.warning('Paket vom Boot zu gross (%d Bytes) — verworfen', len(text))
                continue
            try:
                n = p.pruefe(json.loads(text), vom_pi=True)
            except p.ProtokollFehler as e:      # zuerst: Unterklasse von ValueError
                log.warning('Paket vom Boot abgewiesen: %s', e)
                continue
            except ValueError as e:
                log.warning('Unlesbares Paket vom Boot: %s', e)
                continue

            typ = n['typ']
            wand, mono, gestellt = p.zeitangaben(n)
            # Buch fuehren, BEVOR etwas gedeutet wird: der Zeitbezug entscheidet,
            # ob ein Eintrag eine Zeitachse hat.
            hatte_referenz = _uhrbuch.hat_referenz
            _uhrbuch.merke(wand, mono, gestellt)

            if typ == p.HALLO:
                sitzung = await _hallo(ws, n)
            elif sitzung is None:
                # Alles andere braucht ein hallo davor, sonst weiss der Server
                # nicht, zu welcher Laufzeit die Zeitangaben gehoeren.
                log.warning('Paket %s ohne hallo — verworfen', typ)
                continue
            elif typ == p.ZUSTAND:
                speicher.zustand_setzen(n['daten'], _uhrbuch.aufloesen(wand, mono, gestellt))
                # Und weiter an alle offenen Oberflaechen — sonst zeigen sie
                # einmal Daten und danach nie wieder etwas Neues.
                weiter = dict(n['daten'] or {})
                weiter['quelle'] = 'server'
                weiter['alter_s'] = 0
                weiter['boot_verbunden'] = True
                await _an_zuschauer(weiter)
            elif typ == p.SITZUNG:
                # Eine Anmeldung, die an Bord entstanden ist. Uebernommen wird
                # sie nur, wenn das Konto hier bekannt ist — der Server bleibt
                # die Wahrheit darueber, WER es gibt.
                d = n['daten'] or {}
                if konten and d.get('kennung'):
                    if d.get('beendet'):
                        # Eine Abmeldung an Bord. Sie MUSS auch hier wirken,
                        # sonst bleibt das Logbuch offen, nachdem man sich am
                        # Kartentisch abgemeldet hat.
                        konten.sitzungen_uebernehmen(
                            {}, {d['kennung']: time.time()})
                    else:
                        konten.sitzungen_uebernehmen(
                            {d['kennung']: d.get('sitzung') or {}})
            elif typ == p.VERLAUF:
                _verlauf(n, wand, mono, gestellt)
            elif typ == p.EREIGNIS:
                d = n['daten'] or {}
                speicher.ereignis_anhaengen(
                    n['folge'], d.get('art', 'unbekannt'), d,
                    _uhrbuch.aufloesen(wand, mono, gestellt))
                # Ein Wechsel der Betriebsart ist nicht nur ein Ereignis fuers
                # Protokoll, sondern aendert den ANGEZEIGTEN Zustand: der
                # Bediener soll sehen, warum seltener Daten kommen. Ohne diese
                # Zeile stuende hier fuer immer die Art aus dem hallo.
                if d.get('art') == 'betriebsart' and d.get('betriebsart'):
                    _verbindung['betriebsart'] = d['betriebsart']
                    log.info('Boot meldet Betriebsart %s (Takt %ss)',
                             d['betriebsart'], d.get('takt_s'))
            elif typ == p.QUITTUNG:
                _vermittlung.quittung(n['daten'] or {})
            elif typ == p.PING:
                await ws.send_json(p.umschlag(p.PONG))

            if sitzung is not None:
                speicher.lebenszeichen(sitzung)
            # Sobald die Uhr des Bootes zum ersten Mal steht, koennen geparkte
            # Eintraege eingeordnet werden — das ist der Moment dafuer.
            if _uhrbuch.hat_referenz and not hatte_referenz:
                umgezogen = speicher.geparkte_aufloesen(_uhrbuch)
                if umgezogen:
                    log.info('%d geparkte Verlaufseintraege eingeordnet', umgezogen)

    except WebSocketDisconnect:
        log.info('Boot hat die Verbindung beendet')
    except Exception:
        log.exception('Verbindung zum Boot abgebrochen')
    finally:
        _vermittlung.getrennt()
        _verbindung.update(sitzung=None, seit=None)
        # Den offenen Oberflaechen sagen, dass ab jetzt nur noch die Kopie
        # gilt. Ohne das zeigen sie weiter Live-Werte und bieten Schalter an,
        # die niemand mehr entgegennimmt.
        await _an_zuschauer(_stand_fuer_zuschauer())


async def _hallo(ws: WebSocket, n: dict) -> int:
    d = n['daten'] or {}
    if d.get('fassung') != p.FASSUNG:
        # Lieber abweisen als halb verstehen.
        log.error('Fassung %r passt nicht zu %d', d.get('fassung'), p.FASSUNG)
        await ws.close(code=4400)
        raise WebSocketDisconnect(4400)
    sitzung = speicher.sitzung_beginnen(str(d.get('geraet') or 'unbekannt'), d.get('start') or {})
    _verbindung.update(sitzung=sitzung, seit=time.time(),
                       geraet=d.get('geraet'), betriebsart=d.get('betriebsart'))
    # Der Server nennt seinen Stand, der Pi schickt ab dort weiter. Das ist die
    # ganze Nachliefer-Logik.
    verteilung = konten.zum_verteilen() if konten else {'konten': [], 'stand': ''}
    await ws.send_json(p.stand(speicher.verlauf_stand(), verteilung['stand']))
    # Die Kontenkopie geht gleich mit. Sie ist klein (ein paar Zeilen je Konto)
    # und der Pi braucht sie, BEVOR sich jemand an Bord anmelden will — sie
    # erst auf Nachfrage zu schicken hiesse, die erste Anmeldung im Bordnetz
    # scheitern zu lassen.
    # Immer, nicht nur bei geaenderten Konten. Der Stand ist ein Hash ueber die
    # KONTEN — Sitzungen und Widerrufe aendern ihn nicht. Haetten wir hier
    # weiter darauf verglichen, kaeme eine Anmeldung, die waehrend einer
    # Trennung entstanden ist, nie an Bord an. Die Kopie ist ein paar Zeilen je
    # Konto gross; das einmal je Verbindung zu schicken kostet nichts.
    await ws.send_json(p.konten(verteilung))
    log.info('Kontenkopie ans Boot (%d Konten, %d Sitzungen, Stand %s)',
             len(verteilung['konten']), len(verteilung.get('sitzungen') or {}),
             verteilung['stand'] or '—')
    log.info('Boot %s angemeldet, Verlauf ab %d, Betriebsart %s',
             d.get('geraet'), speicher.verlauf_stand() + 1, d.get('betriebsart'))
    # Und den Oberflaechen, dass wieder Leben da ist — sie sperren sonst
    # weiter, bis zufaellig der naechste Zustand eintrifft.
    await _an_zuschauer(_stand_fuer_zuschauer())
    return sitzung


def _stand_fuer_zuschauer() -> dict:
    """Der zuletzt bekannte Zustand, ausdruecklich als Server-Kopie markiert.

    `boot_verbunden` ist das Feld, an dem die Oberflaeche entscheidet, ob sie
    noch schalten laesst — es muss deshalb bei JEDER Aenderung mitgehen, nicht
    nur wenn gerade zufaellig neue Messwerte kommen.
    """
    z = speicher.zustand()
    daten = dict(z['daten']) if z else {}
    daten['quelle'] = 'server'
    daten['alter_s'] = z['alter_s'] if z else None
    daten['boot_verbunden'] = _verbindung['sitzung'] is not None
    return daten


def _verlauf(n: dict, wand, mono, gestellt) -> None:
    """Ein Buendel Verlaufseintraege ablegen.

    Jeder Eintrag traegt seine eigene Zeit — nicht die des Umschlags. Sonst
    bekaeme ein nachgeliefertes Buendel aus dem Funkloch die Zeit des Moments,
    in dem es endlich durchkam.
    """
    eintraege = []
    for e in (n['daten'] or []):
        if not isinstance(e, dict):
            continue
        e_wand = e.get('wand', e.get('zeit'))
        e_mono = e.get('mono')
        e_gestellt = bool(e.get('gestellt', gestellt))
        aufgeloest = _uhrbuch.aufloesen(e_wand, e_mono, e_gestellt)
        eintraege.append({'folge': e.get('folge'), 'zeit': aufgeloest,
                          'mono': e_mono, 'daten': e.get('daten', e)})
    if eintraege:
        erg = speicher.verlauf_anhaengen(eintraege)
        if erg['geparkt']:
            log.info('%d Eintraege geparkt (Boot hatte noch keine gestellte Uhr)',
                     erg['geparkt'])


# ── Was die PWA sieht ───────────────────────────────────────────────────────
# Dieselben Pfade wie auf dem Pi, damit dieselbe Oberflaeche laeuft. Der
# Unterschied steckt im Feld `quelle`: die PWA soll wissen, dass sie eine Kopie
# ansieht, und wie alt die ist.

@app.get('/api/status')
def status(k: dict = Depends(braucht(r.LESEN))) -> JSONResponse:
    z = speicher.zustand()
    if not z:
        return JSONResponse({'quelle': 'server', 'alter_s': None,
                             'hinweis': 'Vom Boot ist noch kein Zustand angekommen.'})
    daten = dict(z['daten'])
    daten['quelle'] = 'server'
    daten['alter_s'] = z['alter_s']
    daten['bordzeit'] = z['bordzeit']
    daten['boot_verbunden'] = _verbindung['sitzung'] is not None
    return JSONResponse(daten)


@app.get('/api/history')
def history(stunden: float = Query(24, gt=0, le=24 * 90),
            k: dict = Depends(braucht(r.LESEN))) -> JSONResponse:
    seit = time.time() - stunden * 3600
    return JSONResponse({
        'quelle': 'server',
        'server_now': time.time(),
        'eintraege': speicher.verlauf(seit=seit),
        'geparkt': speicher.geparkt_anzahl(),
    })


# ── Anmeldung und Konten ────────────────────────────────────────────────────
# Der Server ist die Wahrheit ueber Konten (KONZEPT-SERVER.md, Kapitel 3). Der
# Pi bekommt eine Kopie, damit an Bord auch ohne Internet angemeldet werden
# kann — geschickt wird sie ueber dieselbe Verbindung, die schon steht.

class Anmeldung(BaseModel):
    name: str
    passwort: str
    kiosk: bool = False


class NeuesKonto(BaseModel):
    name: str
    rolle: str
    passwort: str = ''
    person: str = ''
    spitzname: str = ''
    # Statt eines vorgegebenen Passworts einen Einladungslink erzeugen. Der
    # Eingeladene setzt es selbst — ein Passwort, das jemand anders vergeben
    # hat, wandert per Nachricht durch die Gegend und wird selten geaendert.
    einladen: bool = False


class EinladungEinloesen(BaseModel):
    name: str
    token: str
    passwort: str


class KontoAenderung(BaseModel):
    rolle: str | None = None
    gesperrt: bool | None = None
    passwort: str | None = None
    laeuft_ab: float | None = None
    person: str | None = None
    spitzname: str | None = None


def _sitzung_setzen(antwort: JSONResponse, request: Request, token: str) -> None:
    """Das Sitzungscookie so streng setzen, wie die Verbindung es zulaesst.

    `secure` haengt am Schema und ist keine Nachlaessigkeit: ueber HTTPS wird
    das Cookie als sicher markiert, im Bordnetz ueber HTTP koennte der Browser
    es dann gar nicht erst speichern — die Anmeldung wuerde dort still
    scheitern. Sobald der Pi TLS spricht, gilt auch dort die strenge Fassung.
    """
    # Das alte, nur fuer DIESEN Namen gueltige Cookie loeschen. Sonst hat der
    # Browser zwei mit demselben Namen — eines fuer "logbuch.circuit-sailor.com"
    # und eines fuer ".circuit-sailor.com" — und schickt bei jeder Anfrage
    # beide. Welches der Server dann sieht, ist Glueckssache, und das falsche
    # gehoert zur alten, laengst beendeten Sitzung.
    antwort.delete_cookie(zg.SITZUNG_COOKIE, path='/')
    antwort.set_cookie(
        zg.SITZUNG_COOKIE, token,
        max_age=int(SITZUNG_DAUER_S), httponly=True,
        secure=(request.url.scheme == 'https'), samesite='lax', path='/',
        # Gilt fuer alle drei Namen der Anlage. Ohne das muesste man sich beim
        # Wechsel zwischen Bordansicht und Logbuch jedes Mal neu anmelden.
        domain=zg.keks_bereich(request.headers.get('host', '')),
    )


@app.post('/api/login')
async def login(daten: Anmeldung, request: Request) -> JSONResponse:
    """Anmelden. Die einzige Stelle, an der ein Passwort geprueft wird.

    Sie ist deshalb auch die einzige, die einen Ratenbegrenzer braucht: eine
    Sitzung ist ein lang gewuerfeltes Zufallswort und laesst sich nicht raten,
    ein selbstgewaehltes Passwort schon.
    """
    quelle = _herkunft(request)
    jetzt = time.time()
    versuche = [t for t in _FEHLVERSUCHE.get(quelle, []) if jetzt - t < _SPERRE_S]
    if len(versuche) >= _MAX_VERSUCHE:
        _FEHLVERSUCHE[quelle] = versuche
        raise HTTPException(429, detail='Zu viele Fehlversuche. Bitte später erneut.')

    try:
        # Im Threadpool, nicht hier: das Passwort wird mit scrypt geprueft, und
        # das dauert absichtlich lange. Direkt in der Schleife stuende in dieser
        # Zeit der ganze Server — auch der Datenstrom vom Boot.
        token, k = await run_in_threadpool(
            konten.anmelden,
            daten.name, daten.passwort, kiosk=daten.kiosk,
            herkunft=_herkunft(request),
            geraet=zg.geraet_aus_ua(request.headers.get('user-agent', '')))
    except KontoFehler as e:
        versuche.append(jetzt)
        _FEHLVERSUCHE[quelle] = versuche
        log.warning('Anmeldung gescheitert für %r von %s', daten.name, quelle)
        # Die Meldung des Speichers ist absichtlich unspezifisch (sie
        # unterscheidet nicht zwischen falschem Namen und falschem Passwort).
        raise HTTPException(401, detail=str(e)) from None

    _FEHLVERSUCHE.pop(quelle, None)
    log.info('%r angemeldet (%s)', k['name'], k.get('rolle'))
    # Die frische Sitzung sofort ans Boot. Das Cookie gilt fuer alle drei Namen
    # der Anlage, aber im Bordnetz beantwortet der PI die Bordansicht — und der
    # kannte diese Sitzung bisher erst beim naechsten Verbindungsaufbau. Wer
    # sich also im Logbuch anmeldete und dann zur Bordansicht wechselte, stand
    # wieder vor "nicht angemeldet". Genau das war der Fehler.
    await _konten_zum_boot()
    antwort = JSONResponse(r.uebersicht(k))
    _sitzung_setzen(antwort, request, token)
    return antwort


@app.post('/api/logout')
async def logout(request: Request) -> JSONResponse:
    konten.abmelden(zg.token_aus(request))
    # Der Widerruf muss ans Boot, sonst gilt die Sitzung dort weiter.
    await _konten_zum_boot()
    antwort = JSONResponse({'ok': True})
    antwort.delete_cookie(zg.SITZUNG_COOKIE, path='/',
                          domain=zg.keks_bereich(request.headers.get('host', '')))
    return antwort


@app.get('/api/zugang')
def zugang(request: Request) -> JSONResponse:
    """Was die Oberflaeche wissen muss, BEVOR sie irgendetwas anderes fragt.

    Offen zugaenglich, und das mit Absicht: die Anmeldeseite muss sich zeigen
    koennen. Verraten wird dabei nichts ueber das Boot — nur, ob ueberhaupt
    schon ein Konto besteht und wer gerade angemeldet ist.
    """
    token = zg.token_aus(request)
    k = konten.konto_zu_token(token) if (konten and token) else None
    return JSONResponse({
        'angemeldet': bool(k),
        'ersteinrichtung': bool(konten and konten.leer),
        'konto': r.uebersicht(k) if k else None,
    })


@app.get('/api/anwesend')
async def anwesend(k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))) -> JSONResponse:
    """Wer gerade angemeldet ist — und ob an Bord oder von auswaerts.

    Die Unterscheidung faellt nicht ueber eine IP-Adresse, sondern darueber, WO
    die Sitzung liegt: im Bordnetz zeigt mave.circuit-sailor.com auf den Pi,
    wer dort eine Sitzung hat, sitzt also im Bordnetz. Wer nur hier eine hat,
    ist woanders. Das ist zuverlaessiger als jede Adressauswertung — und es
    faellt ganz nebenbei ab, ohne dass irgendwo mitgeschrieben werden muesste,
    wer sich wo aufhaelt.
    """
    hier = konten.sitzungen() if konten else []
    an_bord, boot_erreichbar = [], False
    if _verbindung['sitzung'] is not None:
        try:
            ergebnis = await _vermittlung.senden('GET', '/api/anwesend', None,
                                                 frist=_DURCHREICHEN_FRIST_S,
                                                 konto=k.get('name', ''))
            an_bord = ((ergebnis or {}).get('antwort') or {}).get('sitzungen') or []
            boot_erreichbar = True
        except (KeinBoot, Zeitueberschreitung):
            pass
    return JSONResponse({
        'an_bord': an_bord,
        'ueber_server': hier,
        'boot_erreichbar': boot_erreichbar,
    })


@app.get('/api/einladung')
def einladung_pruefen(name: str = Query(''), token: str = Query(''),
                      request: Request = None) -> JSONResponse:
    """Ob ein Einladungslink gilt — und zu wem er gehoert.

    Ohne Anmeldung erreichbar, denn genau das ist der Zweck: der Eingeladene
    hat noch kein Passwort. Zurueck kommt nur, was die Einladungsseite anzeigen
    soll — nichts ueber das Boot, nichts ueber andere Konten.

    Ratenbegrenzt wie die Anmeldung: der Link ist ein Geheimnis, und was ein
    Geheimnis ist, laesst sich probieren.
    """
    quelle = _herkunft(request) if request else 'unbekannt'
    jetzt = time.time()
    versuche = [t for t in _FEHLVERSUCHE.get(quelle, []) if jetzt - t < _SPERRE_S]
    if len(versuche) >= _MAX_VERSUCHE:
        raise HTTPException(429, detail='Zu viele Versuche. Bitte später erneut.')

    k = konten.einladung_pruefen(name, token) if konten else None
    if not k:
        versuche.append(jetzt)
        _FEHLVERSUCHE[quelle] = versuche
        raise HTTPException(404, detail='Dieser Link gilt nicht mehr.')
    return JSONResponse(k)


@app.post('/api/einladung')
async def einladung_einloesen(daten: EinladungEinloesen, request: Request) -> JSONResponse:
    """Das selbstgewaehlte Passwort setzen und sich gleich anmelden.

    Gleich anmelden, weil alles andere unfreundlich waere: wer sein Passwort
    eben eingegeben hat, soll es nicht sofort noch einmal eintippen.
    """
    quelle = _herkunft(request)
    jetzt = time.time()
    versuche = [t for t in _FEHLVERSUCHE.get(quelle, []) if jetzt - t < _SPERRE_S]
    if len(versuche) >= _MAX_VERSUCHE:
        raise HTTPException(429, detail='Zu viele Versuche. Bitte später erneut.')
    try:
        konten.einladung_einloesen(daten.name, daten.token, daten.passwort)
    except KontoFehler as e:
        versuche.append(jetzt)
        _FEHLVERSUCHE[quelle] = versuche
        raise HTTPException(400, detail=str(e)) from None

    _FEHLVERSUCHE.pop(quelle, None)
    await _konten_zum_boot()
    token, k = konten.anmelden(
        daten.name, daten.passwort,
        herkunft=_herkunft(request),
        geraet=zg.geraet_aus_ua(request.headers.get('user-agent', '')))
    antwort = JSONResponse(r.uebersicht(k))
    _sitzung_setzen(antwort, request, token)
    return antwort


@app.get('/einladung', include_in_schema=False)
async def einladungsseite():
    return FileResponse(STATISCH / 'einladung.html', media_type='text/html',
                        headers={'Cache-Control': 'no-cache'})


@app.get('/api/konten')
def konten_liste(k: dict = Depends(braucht(r.VERWALTEN))) -> JSONResponse:
    return JSONResponse({'konten': konten.liste(), 'rollen': [
        {'schluessel': s, 'name': v['name'],
         'oberflaechen': list(v['oberflaechen']), 'handlungen': list(v['handlungen']),
         'befristet': bool(v.get('befristet'))}
        for s, v in r.ROLLEN.items()]})


@app.post('/api/konten')
async def konto_anlegen(neu: NeuesKonto, k: dict = Depends(braucht(r.VERWALTEN))) -> JSONResponse:
    try:
        if neu.einladen:
            token, angelegt = konten.einladen(neu.name, neu.rolle,
                                              person=neu.person, spitzname=neu.spitzname)
            # Der Link wird EINMAL zurueckgegeben und nirgends gespeichert —
            # in der Kontendatei liegt nur sein Hash. Wer ihn verliert, muss
            # neu einladen; das ist der Preis dafuer, dass ein Blick in die
            # Datei keine gueltigen Links liefert.
            angelegt['einladungslink'] = f'/einladung#{neu.name}:{token}'
        else:
            if not neu.passwort:
                raise HTTPException(400, detail='Ohne Passwort oder Einladung geht es nicht.')
            angelegt = konten.anlegen(neu.name, neu.passwort, neu.rolle,
                                      person=neu.person, spitzname=neu.spitzname)
    except KontoFehler as e:
        raise HTTPException(400, detail=str(e)) from None
    await _konten_zum_boot()
    return JSONResponse(angelegt, status_code=201)


@app.patch('/api/konten/{name}')
async def konto_aendern(name: str, aenderung: KontoAenderung,
                        k: dict = Depends(braucht(r.VERWALTEN))) -> JSONResponse:
    # Sich selbst zu sperren oder herabzustufen ist der klassische Weg, sich
    # auszusperren. Der Server laesst es nicht zu — es gibt hier niemanden, der
    # es wieder geradeziehen koennte.
    if name == k.get('name') and (aenderung.gesperrt or aenderung.rolle):
        # Namen und Passwort darf man am eigenen Konto sehr wohl aendern —
        # gesperrt wird nur die Selbstentmachtung.
        raise HTTPException(400, detail='Die eigene Rolle und Sperre lassen sich nicht ändern.')
    try:
        stand = konten.aendern(name, rolle=aenderung.rolle, gesperrt=aenderung.gesperrt,
                               passwort=aenderung.passwort, laeuft_ab=aenderung.laeuft_ab,
                               person=aenderung.person, spitzname=aenderung.spitzname)
    except KontoFehler as e:
        raise HTTPException(400, detail=str(e)) from None
    await _konten_zum_boot()
    return JSONResponse(stand)


class PasswortWechsel(BaseModel):
    altes: str
    neues: str


@app.post('/api/mein/passwort')
def eigenes_passwort(daten: PasswortWechsel, request: Request,
                     k: dict = Depends(konto)) -> JSONResponse:
    """Sein eigenes Passwort aendern — jeder, fuer sich, ohne Verwalter.

    Das alte muss mit: eine Sitzung kann auf einem fremden, offenen Geraet
    liegen. Ohne diesen Nachweis koennte jeder, der kurz an ein
    unbeaufsichtigtes Handy kommt, das Konto uebernehmen.
    """
    if len(daten.neues or '') < 8:
        raise HTTPException(400, detail='Das neue Passwort braucht mindestens 8 Zeichen.')
    try:
        konten.passwort_selbst_aendern(k['name'], daten.altes, daten.neues)
    except KontoFehler as e:
        raise HTTPException(400, detail=str(e)) from None
    # Alle Sitzungen sind beendet — auch die eigene. Gleich wieder anmelden,
    # sonst steht man nach dem Wechsel vor der Anmeldemaske.
    token, konto_neu = konten.anmelden(
        k['name'], daten.neues,
        herkunft=_herkunft(request),
        geraet=zg.geraet_aus_ua(request.headers.get('user-agent', '')))
    antwort = JSONResponse({'ok': True})
    _sitzung_setzen(antwort, request, token)
    return antwort


@app.post('/api/konten/{name}/abmelden')
def konto_abmelden(name: str, k: dict = Depends(braucht(r.VERWALTEN))) -> JSONResponse:
    """Alle Sitzungen eines Kontos beenden — fuer ein verlorenes Geraet.

    Ohne das bliebe nur der Umweg ueber einen Passwortwechsel. Der wirkt zwar,
    zwingt aber jemanden, sich ein neues auszudenken, obwohl das alte in
    Ordnung ist.
    """
    return JSONResponse({'beendet': konten.sitzungen_beenden(name)})


@app.post('/api/konten/{name}/einladung')
async def einladung_erneuern(name: str,
                             k: dict = Depends(braucht(r.VERWALTEN))) -> JSONResponse:
    """Einen neuen Einladungslink erzeugen — auch fuer ein vergessenes Passwort.

    Statt jemandem ein neues Passwort zu diktieren, das er per Nachricht
    bekommt und nie aendert, setzt er es ueber denselben Weg selbst. Das alte
    bleibt gueltig, bis der Link eingeloest wird: ein Link, der nie ankommt,
    darf niemanden aussperren.
    """
    try:
        token = konten.neu_einladen(name)
    except KontoFehler as e:
        raise HTTPException(400, detail=str(e)) from None
    await _konten_zum_boot()
    return JSONResponse({'name': name, 'einladungslink': f'/einladung#{name}:{token}'})


@app.delete('/api/konten/{name}/einladung')
async def einladung_zuruecknehmen(name: str,
                                  k: dict = Depends(braucht(r.VERWALTEN))) -> JSONResponse:
    """Eine offene Einladung ungueltig machen, ohne das Konto zu loeschen."""
    try:
        konten.einladung_zuruecknehmen(name)
    except KontoFehler as e:
        raise HTTPException(400, detail=str(e)) from None
    await _konten_zum_boot()
    return JSONResponse({'ok': True})


@app.delete('/api/konten/{name}')
async def konto_loeschen(name: str, k: dict = Depends(braucht(r.VERWALTEN))) -> JSONResponse:
    if name == k.get('name'):
        raise HTTPException(400, detail='Das eigene Konto lässt sich nicht löschen.')
    try:
        konten.loeschen(name)
    except KontoFehler as e:
        raise HTTPException(404, detail=str(e)) from None
    await _konten_zum_boot()
    return JSONResponse({'ok': True})


async def _konten_zum_boot() -> None:
    """Die Kontenkopie ans Boot schicken, wenn es gerade verbunden ist.

    Scheitert das, ist es kein Fehler der Aenderung: der Pi holt die Liste beim
    naechsten Verbindungsaufbau ohnehin. Nur waere er bis dahin auf altem
    Stand — deshalb wird es protokolliert.
    """
    if _verbindung['sitzung'] is None:
        return
    try:
        await _vermittlung.senden_ohne_antwort(p.konten(konten.zum_verteilen()))
    except Exception:
        log.warning('Kontenkopie konnte nicht ans Boot gehen — folgt beim nächsten Verbinden')


@app.get('/api/verbindung')
def verbindung(k: dict = Depends(braucht(r.LESEN))) -> JSONResponse:
    """Wie es dem Boot verbindungsseitig geht — und was in den Luecken war.

    Das ist die Antwort auf die Frage des Eigners: war das Boot offline, oder
    ist der Pi abgestuerzt?
    """
    return JSONResponse({
        'verbunden': _verbindung['sitzung'] is not None,
        'seit': _verbindung['seit'],
        'geraet': _verbindung['geraet'],
        'betriebsart': _verbindung['betriebsart'],
        'luecken': speicher.luecken(seit=time.time() - 30 * 86400),
    })


@app.get('/api/rechte')
def meine_rechte(k: dict = Depends(konto)) -> JSONResponse:
    """Was das angemeldete Konto darf. Die Oberflaeche blendet danach aus."""
    return JSONResponse(r.uebersicht(k))


# ── Messwerte fuers Logbuch ─────────────────────────────────────────────────

_REIHEN_MAX = 1500      # mehr Punkte kann kein Bildschirm zeigen


@app.get('/api/verlauf/reihen')
def verlauf_reihen(von: float = Query(0, ge=0), bis: float = Query(0, ge=0),
                   punkte: int = Query(600, ge=50, le=_REIHEN_MAX),
                   k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))) -> JSONResponse:
    """Zeitreihen fuer die Graphen, auf eine anzeigbare Menge eingedampft.

    Neunzig Tage sind rund 130.000 Minutenwerte. Die alle in den Browser zu
    schicken waere sinnlos — ein Bildschirm hat keine 130.000 Spalten — und auf
    einem Handy schlicht zu viel.

    Eingedampft wird in Eimer gleicher Breite, und je Eimer gehen MITTELWERT,
    KLEINSTER und GROESSTER Wert mit. Nur den Mittelwert zu nehmen waere
    bequemer, wuerde aber genau das verschlucken, weswegen man in ein
    Diagnosewerkzeug schaut: die kurze Spitze, den Einbruch, den Ausreisser.
    Der Graph zeichnet deshalb ein Band zwischen klein und gross und die
    Mittellinie hinein.
    """
    jetzt = time.time()
    bis = bis or jetzt
    von = von or (bis - 24 * 3600)
    if bis <= von:
        raise HTTPException(400, detail='Der Zeitraum ist leer.')
    if bis - von > 400 * 86400:
        raise HTTPException(400, detail='Mehr als 400 Tage gibt der Verlauf nicht her.')

    roh = speicher.verlauf(seit=von, grenze=200000)
    roh = [e for e in roh if e.get('zeit') and von <= e['zeit'] <= bis]
    if not roh:
        return JSONResponse({'von': von, 'bis': bis, 'punkte': [], 'felder': [],
                             'roh_anzahl': 0})

    breite = (bis - von) / punkte
    eimer: dict = {}
    felder: set = set()
    for e in roh:
        i = min(int((e['zeit'] - von) / breite), punkte - 1)
        fach = eimer.setdefault(i, {})
        for feld, wert in e.items():
            if feld in ('folge', 'zeit', 'mono') or not isinstance(wert, (int, float)) \
                    or isinstance(wert, bool):
                continue
            felder.add(feld)
            w = fach.setdefault(feld, [0.0, 0, None, None])   # summe, anzahl, min, max
            w[0] += wert
            w[1] += 1
            w[2] = wert if w[2] is None else min(w[2], wert)
            w[3] = wert if w[3] is None else max(w[3], wert)

    punkte_raus = []
    for i in sorted(eimer):
        p_ = {'t': round(von + (i + 0.5) * breite, 1)}
        for feld, (summe, anzahl, klein, gross) in eimer[i].items():
            if not anzahl:
                continue
            p_[feld] = [round(summe / anzahl, 4), round(klein, 4), round(gross, 4)]
        punkte_raus.append(p_)

    return JSONResponse({
        'von': von, 'bis': bis,
        'felder': sorted(felder),
        'punkte': punkte_raus,
        'roh_anzahl': len(roh),
        'eimer_s': round(breite, 1),
    })


@app.get('/api/verlauf/export')
def verlauf_export(von: float = Query(0, ge=0), bis: float = Query(0, ge=0),
                   k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))):
    """Der Verlauf als CSV — ungefiltert, ungerundet, alle Messwerte.

    Bewusst NICHT eingedampft: fuer die Anzeige ist das richtig, fuer einen
    Export waere es Datenverlust. Wer exportiert, will die Zahlen selbst
    auswerten und nicht das, was gerade auf einen Bildschirm passte.

    Semikolon als Trenner und Komma als Dezimalzeichen: so oeffnet eine
    deutsche Tabellenkalkulation die Datei ohne Nachfragen.
    """
    import csv
    import io
    jetzt = time.time()
    bis = bis or jetzt
    von = von or (bis - 7 * 86400)
    roh = [e for e in speicher.verlauf(seit=von, grenze=400000)
           if e.get('zeit') and von <= e['zeit'] <= bis]

    felder = sorted({f for e in roh for f, w in e.items()
                     if f not in ('folge', 'zeit', 'mono')
                     and isinstance(w, (int, float)) and not isinstance(w, bool)})
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=';', lineterminator='\n')
    schreiber.writerow(['Zeit', 'Zeitstempel'] + felder)
    for e in roh:
        zeile = [datetime.datetime.fromtimestamp(e['zeit']).strftime('%Y-%m-%d %H:%M:%S'),
                 f"{e['zeit']:.0f}"]
        for f in felder:
            w = e.get(f)
            zeile.append(f'{w}'.replace('.', ',') if isinstance(w, (int, float)) else '')
        schreiber.writerow(zeile)

    name = 'mave-verlauf-{}.csv'.format(
        datetime.datetime.fromtimestamp(bis).strftime('%Y%m%d-%H%M'))
    return Response(
        content=puffer.getvalue().encode('utf-8-sig'),   # BOM, sonst verhunzt Excel Umlaute
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{name}"'})


# ── Das Diagnosewerkzeug ────────────────────────────────────────────────────
# Aufbau noch offen (wird gesondert besprochen). Hier steht nur das Tor: es
# braucht das Oberflaechenrecht `diagnose`, das die Crew nicht hat.

@app.get('/api/diagnose/uebersicht')
def diagnose(k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))) -> JSONResponse:
    return JSONResponse({
        'verlauf_stand': speicher.verlauf_stand(),
        'geparkt': speicher.geparkt_anzahl(),
        'sitzungen': speicher.sitzungen(seit=time.time() - 30 * 86400),
        'ereignisse': speicher.ereignisse(grenze=50),
    })


@app.get('/api/stand')
def oberflaechen_stand() -> JSONResponse:
    """Wie auf dem Pi: womit die Oberflaeche ausgeliefert wuerde."""
    return JSONResponse({'stand': _abbild_stand()})


@app.get('/api/system/version')
def version() -> JSONResponse:
    """Ohne Anmeldung erreichbar, damit die PWA erkennen kann, mit WEM sie
    spricht — Pi oder Server. Mehr sagt die Antwort nicht.

    Ob das Boot gerade verbunden ist, stand hier zuerst mit drin. Das ist eine
    Aussage darueber, ob jemand an Bord ist beziehungsweise das Boot Strom hat,
    und sie gehoert hinter die Anmeldung (/api/verbindung)."""
    return JSONResponse({'rolle': 'server', 'fassung': p.FASSUNG})


# ── Schalten aus der Ferne ──────────────────────────────────────────────────
# Die PWA ruft dieselben Pfade auf wie am Boot — deshalb bietet der Server sie
# an und leitet sie durch die offene Verbindung weiter. Es gibt KEINEN
# allgemeinen Durchleiter: was nicht in DURCHLEITEN steht, gibt es hier nicht.

def _durchleiter(methode: str, pfad: str, recht: str):
    async def weiterleiten(request: Request, k: dict = Depends(braucht(recht))):
        if gesperrt(pfad, FERN_HEIZUNG):
            raise HTTPException(403, detail=(
                'Dieser Befehl ist aus der Ferne gesperrt. '
                'Er lässt sich in der Serverkonfiguration freigeben.'))
        roh = await request.body()
        if len(roh) > 256 * 1024:
            raise HTTPException(413, detail='Daten zu groß')
        try:
            rumpf = json.loads(roh) if roh else None
        except ValueError:
            raise HTTPException(400, detail='Ungültiges JSON') from None

        # Pfadteile einsetzen — der Pi prueft sie selbst, hier werden sie nur
        # eingesetzt. Deshalb keine Validierung, aber auch keine Auswertung.
        ziel = pfad
        for name, wert in (request.path_params or {}).items():
            ziel = ziel.replace('{' + name + '}', str(wert))

        try:
            ergebnis = await _vermittlung.senden(methode, ziel, rumpf,
                                                 konto=k.get("name", ""))
        except KeinBoot as e:
            # 409 und nicht 503: der Server ist in Ordnung, das Boot ist weg.
            raise HTTPException(409, detail=str(e)) from None
        except Zeitueberschreitung as e:
            raise HTTPException(504, detail=str(e)) from None

        if not ergebnis.get('ok'):
            raise HTTPException(int(ergebnis.get('status') or 502),
                                detail=ergebnis.get('fehler') or 'Das Boot hat abgelehnt.')
        return JSONResponse(ergebnis.get('antwort') or {})

    weiterleiten.__name__ = f'weiterleiten_{methode.lower()}_{pfad.strip("/").replace("/", "_").replace("{", "").replace("}", "")}'
    return weiterleiten


for _methode, _pfad, _recht in DURCHLEITEN:
    app.add_api_route(_pfad, _durchleiter(_methode, _pfad, _recht),
                      methods=[_methode], include_in_schema=False)


# ── Die Oberflaeche ─────────────────────────────────────────────────────────
# Der Server liefert DIESELBE PWA aus wie der Pi. Sie spricht ohnehin nur
# /api/* und /ws, und beides gibt es hier — deshalb laeuft sie unveraendert.

# Reihenfolge wie in main.py auf dem Pi. Sie steht hier ein zweites Mal, und
# das ist die einzige Doppelung im ganzen Aufbau: eine gemeinsame Liste haette
# bedeutet, dass der Server den Pi-Code importiert, und der bringt CAN-Bus und
# alles Uebrige mit.
from js_bundle_liste import JS_FILES as _JS_FILES
_bundle: dict = {'data': b'', 'etag': '', 'mtime': 0.0}


@app.get('/diagnose', include_in_schema=False)
async def diagnose_seite():
    """Das Logbuch — Diagnose und Fernwartung.

    Die Seite selbst wird ohne Pruefung ausgeliefert, ihre Daten nicht. Das ist
    Absicht: sie enthaelt nur Aufbau und Beschriftung, kein Wort ueber das
    Boot. Wer sie ohne das Recht `diagnose` oeffnet, sieht eine Abweisung und
    bekommt von den Endpunkten nichts — die pruefen einzeln.

    Andersherum waere es unbequem ohne Gewinn: eine geschuetzte HTML-Seite
    koennte die Anmeldemaske nicht zeigen, die sie selbst mitbringt.
    """
    return FileResponse(STATISCH / 'diagnose.html', media_type='text/html',
                        headers={'Cache-Control': 'no-cache'})


@app.get('/js-diagnose.js', include_in_schema=False)
async def diagnose_js():
    return FileResponse(STATISCH / 'js' / 'diagnose.js',
                        media_type='application/javascript',
                        headers={'Cache-Control': 'no-cache'})


@app.get('/sw.js', include_in_schema=False)
async def service_worker():
    """Muss von der Wurzel kommen, sonst deckt sein Geltungsbereich die Seite
    nicht ab und die Anwendung bleibt fuer Chrome nicht installierbar."""
    return FileResponse(STATISCH / 'sw.js', media_type='application/javascript',
                        headers={'Cache-Control': 'no-cache'})


@app.get('/js-bundle.js', include_in_schema=False)
def js_bundle(req: Request) -> Response:
    js = STATISCH / 'js'
    vorhanden = [js / f for f in _JS_FILES if (js / f).exists()]
    if not vorhanden:
        raise HTTPException(500, detail='Oberfläche fehlt im Abbild')
    neueste = max(p.stat().st_mtime for p in vorhanden)
    if _bundle['mtime'] < neueste:
        _bundle['data'] = ('\n;// ---\n'.join(
            p.read_text(encoding='utf-8') for p in vorhanden)).encode()
        _bundle['mtime'] = neueste
        _bundle['etag'] = f'"{int(neueste)}"'
    if req.headers.get('if-none-match') == _bundle['etag']:
        return Response(status_code=304)
    return Response(_bundle['data'], media_type='application/javascript',
                    headers={'Cache-Control': 'no-cache', 'ETag': _bundle['etag']})


@app.get('/', include_in_schema=False)
def startseite(request: Request) -> HTMLResponse:
    """Welche Oberflaeche an der Wurzel steht, haengt am Namen.

    Unter `logbuch.…` erwartet niemand die Bordansicht — der Name sagt, was
    kommen soll. Umgekehrt liefert `mave.…` immer die Bordansicht, auch auf dem
    Server; unterwegs ist genau das die Seite, die man aufmacht.

    Das Logbuch bleibt zusaetzlich unter /diagnose erreichbar, damit ein alter
    Verweis nicht ins Leere geht.
    """
    gastgeber = (request.headers.get('host') or '').split(':')[0].lower()
    name = 'diagnose.html' if gastgeber.startswith('logbuch.') else 'index.html'
    seite = STATISCH / name
    if not seite.exists():
        raise HTTPException(500, detail='Oberfläche fehlt im Abbild')
    # Denselben Platzhalter wie auf dem Pi fuellen. Welcher Stand hier steht,
    # sagt der Server aus SEINEM Abbild — die Oberflaeche kommt schliesslich
    # von ihm.
    roh = seite.read_text(encoding='utf-8').replace('__STAND__', _abbild_stand())
    return HTMLResponse(roh, headers={'Cache-Control': 'no-cache'})


def _abbild_stand() -> str:
    """Eine Kennung des ausgelieferten Standes.

    Im Container gibt es kein git. Genommen wird deshalb die Aenderungszeit des
    Bundles — sie aendert sich bei jedem Neubau und reicht voellig: es geht nur
    darum, ZU ERKENNEN, dass sich etwas geaendert hat.
    """
    try:
        js = STATISCH / 'js' / 'init.js'
        return f'abbild-{int(js.stat().st_mtime)}'
    except OSError:
        return 'unbekannt'


if STATISCH.exists():
    app.mount('/static', StaticFiles(directory=str(STATISCH)), name='static')


# ── Lesende Anfragen ans Boot durchreichen ──────────────────────────────────
# Die Oberflaeche fragt weit mehr ab als Zustand und Verlauf: Alarme, Heizung,
# Geraete, Tanks, Wartung, Verbindung, Tageswerte. Diese Daten liegen auf dem
# Pi, nicht hier — und sie alle ueber den Sync zu spiegeln hiesse, jede
# kuenftige Erweiterung an ZWEI Stellen zu pflegen.
#
# Deshalb: Was der Server selbst weiss, beantwortet er aus seiner Kopie (das
# ist das Wichtige — Zustand und Verlauf sind auch da, wenn das Boot schweigt).
# Alles andere Lesende geht durch dieselbe Verbindung ans Boot.
#
# Ein GET veraendert nichts, deshalb braucht es hier keine Weissliste wie bei
# den Befehlen — aber sehr wohl das Leserecht und eine kurze Frist: haengt das
# Boot, soll die Oberflaeche eine Antwort bekommen und nicht warten.

_DURCHREICHEN_FRIST_S = 8.0


@app.get('/api/{rest:path}', include_in_schema=False)
async def lesend_durchreichen(rest: str, request: Request,
                              k: dict = Depends(braucht(r.LESEN))):
    pfad = '/api/' + rest
    if request.url.query:
        pfad += '?' + request.url.query
    try:
        ergebnis = await _vermittlung.senden('GET', pfad, None, frist=_DURCHREICHEN_FRIST_S,
                                                  konto=k.get('name', ''))
    except KeinBoot:
        # 409 und nicht 503: der Server ist in Ordnung, das Boot ist weg. Die
        # Oberflaeche kann daraus einen ehrlichen Hinweis machen, statt einen
        # Fehler anzuzeigen.
        raise HTTPException(409, detail='Das Boot ist nicht verbunden.') from None
    except Zeitueberschreitung:
        raise HTTPException(504, detail='Das Boot hat nicht geantwortet.') from None
    if not ergebnis.get('ok'):
        raise HTTPException(int(ergebnis.get('status') or 502),
                            detail=ergebnis.get('fehler') or 'Das Boot hat abgelehnt.')
    return JSONResponse(ergebnis.get('antwort') if ergebnis.get('antwort') is not None else {})


# ── Die Oberflaeche mit Live-Daten versorgen ────────────────────────────────
# Die PWA haelt einen WebSocket offen und erwartet darueber neue Zustaende. Auf
# dem Pi ist das die Verbindung zum Bus; hier ist es die Weitergabe dessen, was
# vom Boot hereinkommt. Ohne diesen Endpunkt zeigt die Oberflaeche einmal Daten
# und danach nie wieder etwas Neues.

_zuschauer: set = set()


async def _an_zuschauer(zustand: dict) -> None:
    """Einen neuen Zustand an alle offenen Oberflaechen geben."""
    if not _zuschauer:
        return
    tot = []
    for ws in list(_zuschauer):
        try:
            await ws.send_json(zustand)
        except Exception:
            tot.append(ws)
    for ws in tot:
        _zuschauer.discard(ws)


@app.websocket('/ws')
async def oberflaeche_ws(ws: WebSocket) -> None:
    # Der Live-Kanal traegt den vollstaendigen Zustand des Bootes. Er MUSS
    # geprueft werden, und zwar hier: eine HTTP-Middleware sieht einen
    # WebSocket-Handschlag nicht.
    #
    # Das Sitzungscookie geht beim Handschlag von selbst mit — anders als ein
    # Authorization-Kopf, den ein Browser bei WebSockets nicht setzen kann.
    # Genau deshalb liegt die Sitzung im Cookie und nicht im Kopf.
    #
    # Diese Pruefung fehlte, solange eine Basic-Anmeldung davor stand: die war
    # der eigentliche Waechter, ohne dass es hier jemandem auffiel. Mit ihrem
    # Wegfall stand der Kanal offen im Internet und lieferte Ladestand,
    # Tankfuellungen und Standortdaten an jeden, der die Adresse kannte.
    if not zg.herkunft_erlaubt(ws.headers.get('origin', ''),
                               ws.headers.get('host', '')):
        log.warning('Live-Kanal von fremder Herkunft abgewiesen: %r',
                    ws.headers.get('origin'))
        await ws.close(code=4403)
        return
    token = ws.cookies.get(zg.SITZUNG_COOKIE) or ''
    k = konten.konto_zu_token(token) if (konten and token) else None
    if not r.darf(k, r.LESEN):
        # 4401 statt eines HTTP-Codes: nach dem Handschlag gibt es nur noch
        # WebSocket-Schliesscodes. Die Oberflaeche deutet ihn als "anmelden".
        await ws.close(code=4401)
        return
    await ws.accept()
    _zuschauer.add(ws)
    try:
        z = speicher.zustand()
        if z:
            daten = dict(z['daten'])
            daten['quelle'] = 'server'
            daten['alter_s'] = z['alter_s']
            await ws.send_json(daten)
        while True:
            # Die Oberflaeche schickt nichts ausser gelegentlichen Lebenszeichen.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _zuschauer.discard(ws)
