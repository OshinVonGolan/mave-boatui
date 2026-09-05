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
import math
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
from server.push import PushDienst, KeinPush
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
            elif typ == p.PUSH:
                # Ein Geraet hat sich im Bordnetz beim Pi angemeldet. Senden
                # kann nur der Server — hier landet es also.
                d = n['daten'] or {}
                try:
                    if d.get('abmelden'):
                        push.abmelden((d.get('abo') or {}).get('endpoint', ''))
                    else:
                        push.anmelden(d.get('abo') or {}, str(d.get('konto') or ''),
                                      str(d.get('geraet') or ''))
                    log.info('Push-Abo vom Boot uebernommen (%s)', d.get('konto'))
                except Exception as e:
                    log.warning('Push-Abo vom Boot abgewiesen: %s', e)
            elif typ == p.VERLAUF:
                _verlauf(n, wand, mono, gestellt)
            elif typ == p.EREIGNIS:
                d = n['daten'] or {}
                speicher.ereignis_anhaengen(
                    n['folge'], d.get('art', 'unbekannt'), d,
                    _uhrbuch.aufloesen(wand, mono, gestellt))
                # Ein Alarm ist der Grund, warum es Push ueberhaupt gibt.
                # Nebenlaeufig: der Versand geht ueber fremde Server und darf
                # den Datenstrom vom Boot nicht aufhalten.
                if d.get('art') == 'alarm':
                    asyncio.create_task(_push_bei_alarm(d))
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
        # Nur aufraeumen, wenn DIESE Verbindung noch die aktuelle ist.
        #
        # Reisst die Leitung ab und der Pi ist sofort wieder da — bei Mobilfunk
        # der Normalfall —, laeuft dieser Block NACH dem hallo der neuen
        # Verbindung. Bedingungslos ausgefuehrt erklaerte er sie fuer tot: das
        # Logbuch zeigte "Das Boot ist nicht verbunden" ueber Werten, die zwei
        # Sekunden alt waren, und die Fernwartung nahm keine Befehle mehr an.
        # Erst der naechste echte Abbruch haette das geheilt.
        _vermittlung.getrennt(ws)
        # Nur der Vergleich, kein Sonderfall fuer `sitzung is None`: eine
        # Verbindung, die vor dem hallo stirbt, darf den Zustand einer anderen
        # nicht loeschen. Steht ohnehin nichts da, ist der Vergleich wahr und
        # das Zuruecksetzen ein Nichts.
        if _verbindung['sitzung'] == sitzung:
            _verbindung.update(sitzung=None, seit=None)
            # Den offenen Oberflaechen sagen, dass ab jetzt nur noch die Kopie
            # gilt. Ohne das zeigen sie weiter Live-Werte und bieten Schalter
            # an, die niemand mehr entgegennimmt.
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
    await ws.send_json(p.stand(speicher.verlauf_stand(), verteilung['stand'],
                               push.oeffentlicher_schluessel))
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


# ── Push ────────────────────────────────────────────────────────────────────
# Die Meldung, wenn die Anwendung zu ist. Einzelheiten in server/push.py —
# vor allem die eine, die man kennen muss: das Senden laeuft ueber den
# Push-Dienst des Browserherstellers und braucht deshalb Internet auf BEIDEN
# Seiten. Im Bordnetz ohne Uplink kommt nichts an, obwohl der Pi im selben
# Raum steht. Dafuer gibt es daneben den Ton bei offener Anwendung.
push = PushDienst(DATEN / 'push_vapid.json', speicher)


@app.get('/api/push/schluessel')
def push_schluessel(k: dict = Depends(braucht(r.LESEN))) -> JSONResponse:
    """Der oeffentliche Schluessel. Das Geraet braucht ihn, um sich anzumelden."""
    return JSONResponse({'bereit': push.bereit, 'schluessel': push.oeffentlicher_schluessel,
                         'grund': push.grund})


@app.post('/api/push/anmelden')
async def push_anmelden(daten: dict, request: Request,
                        k: dict = Depends(braucht(r.LESEN))) -> JSONResponse:
    """Ein Geraet traegt sich ein.

    Nur LESEN noetig: wer die Werte sehen darf, darf auch benachrichtigt
    werden. Die Meldung selbst enthaelt nichts, was ueber das hinausgeht.
    """
    try:
        push.anmelden(daten.get('abo') or daten, k.get('name', ''),
                      zg.geraet_aus_ua(request.headers.get('user-agent', '')))
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from None
    return JSONResponse({'ok': True})


@app.post('/api/push/abmelden')
async def push_abmelden(daten: dict,
                        k: dict = Depends(braucht(r.LESEN))) -> JSONResponse:
    endpunkt = (daten or {}).get('endpunkt') or (daten.get('abo') or {}).get('endpoint')
    if endpunkt:
        push.abmelden(endpunkt)
    return JSONResponse({'ok': True})


@app.post('/api/push/probe')
async def push_probe(k: dict = Depends(braucht(r.LESEN))) -> JSONResponse:
    """Eine Probemeldung an die eigenen Geraete — damit man es einmal gesehen
    hat, bevor es ernst wird."""
    try:
        return JSONResponse(push.senden('Probe', 'So sieht eine Meldung von Mave aus.',
                                        kennung='mave-probe'))
    except KeinPush as e:
        raise HTTPException(503, detail=str(e)) from None


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


# Wie weit sich das Boot bewegt haben muss, bevor die Spur einen neuen Punkt
# setzt. Zwanzig Meter: darunter ist es das Rauschen des Empfaengers und das
# Schwojen an der Leine, darueber ist es Fahrt.
_SPUR_ABSTAND_M = 20.0

# Hier stand einmal zusaetzlich "und alle zwei Stunden einer, auch wenn sich
# nichts ruehrt". Der Gedanke dahinter war richtig — sonst liesse sich
# "lag da" nicht von "nichts aufgeschrieben" unterscheiden —, die Umsetzung
# war es nicht: drei Wochen am Steg ergaben dreihundertsechzig Punkte auf
# demselben Fleck.
#
# Dieselbe Auskunft ohne den Stapel: ein Punkt merkt sich, wie lange das Boot
# an ihm lag (`bis`), und eine echte LUECKE im Aufschrieb beginnt einen neuen
# Abschnitt. Die Karte zieht dann keine Linie darueber — sie waere gelogen,
# denn wo das Boot dazwischen war, weiss niemand.
_SPUR_LUECKE_S = 30 * 60


def _meter(lat1, lon1, lat2, lon2) -> float:
    """Abstand zweier Punkte in Metern.

    Aequirektangulaere Naeherung statt Haversine: auf den paar Kilometern, um
    die es hier geht, liegt sie im Zentimeterbereich daneben und kostet einen
    Bruchteil der Rechenzeit. Auf einem Bordrechner zaehlt das.
    """
    lat_m = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon2 - lon1) * math.cos(lat_m)
    dy = math.radians(lat2 - lat1)
    return math.hypot(dx, dy) * 6371000.0


# Mehr Punkte muss keine Karte zeichnen. Ein Boot, das drei Wochen am Anker
# schwojt, bewegt sich echt — jede dieser Bewegungen einzeln zu uebertragen
# nuetzt aber niemandem, und auf einem Telefon wird die Linie zaeh.
_SPUR_MAX_PUNKTE = 2500


def _spur_ausduennen(roh, abstand_m, luecke_s=None):
    """Aus Rohpunkten (zeit, lat, lon) eine Spur ohne Stapel machen.

    Zwei Regeln, mehr nicht:

    Ein neuer Punkt entsteht, wenn sich das Boot um `abstand_m` bewegt hat.
    Liegt es still, waechst stattdessen das `bis` des letzten Punktes — daraus
    wird die Liegezeit. Drei Wochen am Steg ergeben so EINEN Punkt und nicht
    dreihundertsechzig uebereinander.

    Und ein neuer Punkt entsteht nach einer LUECKE im Aufschrieb. Zwischen zwei
    Messzeilen liegt sonst eine Minute; war es eine halbe Stunde, hat der
    Bordrechner geschwiegen. Wo das Boot in dieser Zeit war, weiss niemand — der
    Punkt traegt deshalb `neu`, und die Karte zieht keine Linie darueber.
    """
    if luecke_s is None:
        luecke_s = _SPUR_LUECKE_S
    raus, letzter, vorige_zeit = [], None, None
    for t, lat, lon in roh:
        luecke = vorige_zeit is not None and (t - vorige_zeit) >= luecke_s
        vorige_zeit = t
        bewegt = letzter is None or \
            _meter(letzter['lat'], letzter['lon'], lat, lon) >= abstand_m
        if bewegt or luecke:
            letzter = {'t': round(t, 1), 'bis': round(t, 1),
                       'lat': lat, 'lon': lon, 'neu': bool(luecke)}
            raus.append(letzter)
        else:
            letzter['bis'] = round(t, 1)
    return raus


@app.get('/api/verlauf/spur')
def verlauf_spur(tage: int = Query(30, ge=1, le=365),
                 k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))) -> JSONResponse:
    """Die gefahrene Spur der letzten Tage, ausgeduennt.

    Warum nicht ueber /api/verlauf/reihen: das dampft in Eimer GLEICHER BREITE
    ein. Fuer einen Messwert ist das richtig, fuer einen Weg nicht — dreissig
    Tage auf 800 Punkte heisst ein Punkt je 54 Minuten, und ein Hafenmanoever
    verschwindet darin vollstaendig, waehrend drei Wochen am Steg 700 Punkte
    auf demselben Fleck bekommen.

    Hier wird stattdessen nach ZURUECKGELEGTEM WEG ausgeduennt: ein neuer Punkt,
    sobald sich das Boot weiter als eine Bootslaenge bewegt hat. Liegt es still,
    entsteht alle zwei Stunden einer — damit man sieht, dass es dalag, und nicht
    bloss, dass nichts aufgeschrieben wurde.

    Die Position kommt aus dem GNSS des Routers. Fehlt sie in einer Zeile, gab
    es keinen Fix; dann fehlt der Punkt, und das ist die ehrliche Auskunft.
    """
    jetzt = time.time()
    seit = jetzt - tage * 86400
    # Grenze grosszuegig: dreissig Tage sind rund 43.000 Minutenzeilen.
    zeilen = speicher.verlauf(seit=seit, grenze=tage * 1500 + 2000)

    # Erst die brauchbaren Zeilen heraussuchen, dann ausduennen. Getrennt,
    # damit das Ausduennen mit groesserem Abstand wiederholt werden kann, ohne
    # die Datenbank noch einmal zu fragen.
    roh = []
    for z in zeilen:
        lat, lon = z.get('lat'), z.get('lon')
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if isinstance(lat, bool) or isinstance(lon, bool):
            continue
        roh.append((z.get('zeit') or 0.0, lat, lon))

    # Verdoppeln, bis es passt. Ein schwojendes Boot bewegt sich wirklich —
    # aber ab einer gewissen Zahl beschreibt jeder weitere Punkt dieselbe
    # Sache noch einmal.
    abstand = _SPUR_ABSTAND_M
    punkte = _spur_ausduennen(roh, abstand)
    while len(punkte) > _SPUR_MAX_PUNKTE and abstand < 5000:
        abstand *= 2
        punkte = _spur_ausduennen(roh, abstand)

    mit_position = len(roh)
    return JSONResponse({
        'punkte': punkte,
        'zeilen_gesamt': len(zeilen),
        'zeilen_mit_position': mit_position,
        'von': seit, 'bis': jetzt,
        'abstand_m': abstand, 'luecke_s': _SPUR_LUECKE_S,
    })


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


# ── Alarme seit dem letzten Blick ───────────────────────────────────────────
# Die Frage, mit der man unterwegs ins Logbuch schaut, ist nicht "was ist
# gerade", sondern "was war, waehrend ich nicht hingesehen habe". Ein Alarm,
# der um drei Uhr nachts kam und um vier von selbst wieder ging, taucht in
# keinem Zustand mehr auf — im Ereignisstrom steht er.
#
# Der Merker liegt JE KONTO. Zwei Leute schauen unabhaengig voneinander ins
# Logbuch; haette der Marker nur einen Stand, wuerde der erste dem zweiten die
# Meldung wegnehmen.

_ALARM_ARTEN = ('alarm', 'alarm_quittiert', 'alarm_weg')


def _alarme_seit(folge: int) -> list[dict]:
    """Die Ereignisse ab `folge` zu einer Liste von Alarmen falten.

    Vorwaerts durch den Strom: erst das Auftreten, dann was daraus wurde.
    Rueckwaerts liesse sich "spaeter quittiert" nicht von "vorher quittiert"
    unterscheiden.

    Quittierungen und Aufloesungen zu Alarmen, deren Auftreten VOR dem Merker
    liegt, werden uebergangen. Sie sind kein neuer Vorfall — der Alarm selbst
    stand schon beim letzten Blick in der Liste.
    """
    nach_kennung: dict[str, dict] = {}
    for e in speicher.ereignisse_ab(folge, _ALARM_ARTEN, grenze=300):
        d = e.get('daten') or {}
        kennung = str(d.get('kennung') or '')
        if not kennung:
            continue
        if e['art'] == 'alarm':
            # Derselbe Alarm kann in der Zeitspanne mehrfach gekommen und
            # gegangen sein. Gezaehlt wird, wie oft — angezeigt wird einer.
            vorhanden = nach_kennung.get(kennung)
            if vorhanden:
                vorhanden['male'] += 1
                vorhanden['zeit'] = d.get('zeit') or e.get('zeit')
                vorhanden['quittiert'] = False
                vorhanden['weg'] = False
            else:
                nach_kennung[kennung] = {
                    'kennung': kennung,
                    'name': d.get('name') or kennung,
                    'schluessel': d.get('schluessel'),
                    'wert': d.get('wert'),
                    'schwelle': d.get('schwelle'),
                    'schwere': d.get('schwere') or 'warning',
                    'zeit': d.get('zeit') or e.get('zeit'),
                    'folge': e['folge'],
                    'quittiert': False,
                    'weg': False,
                    'male': 1,
                }
        elif kennung in nach_kennung:
            if e['art'] == 'alarm_quittiert':
                nach_kennung[kennung]['quittiert'] = True
            else:
                nach_kennung[kennung]['weg'] = True
                nach_kennung[kennung]['weg_zeit'] = e.get('zeit')
    # Schwer zuerst, danach das Juengste oben: wer schnell schaut, liest genau
    # die Zeile, auf die es ankommt.
    rang = {'critical': 0, 'warning': 1, 'info': 2}
    return sorted(nach_kennung.values(),
                  key=lambda a: (rang.get(a['schwere'], 3), -(a['zeit'] or 0)))


@app.get('/api/logbuch/alarme')
def logbuch_alarme(k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))) -> JSONResponse:
    konto_name = str(k.get('name') or '')
    stand = speicher.ereignis_stand()
    seit = speicher.merker('alarme_gesehen', konto=konto_name, vorgabe=None)
    # Beim ALLERERSTEN Besuch nicht die ganze Vergangenheit aufblaettern: der
    # Vorhang soll melden, was seit dem letzten Blick war, und nicht bei der
    # Einfuehrung mit dreihundert alten Alarmen aufschlagen. Ohne Merker gilt
    # deshalb der jetzige Stand als gesehen.
    if seit is None:
        speicher.merker_setzen('alarme_gesehen', stand, konto=konto_name)
        return JSONResponse({'stand': stand, 'seit': stand, 'alarme': [], 'erstbesuch': True})
    return JSONResponse({'stand': stand, 'seit': int(seit),
                         'alarme': _alarme_seit(int(seit)), 'erstbesuch': False})


class AlarmeGesehen(BaseModel):
    stand: int = 0


@app.post('/api/logbuch/alarme/gesehen')
def logbuch_alarme_gesehen(body: AlarmeGesehen,
                           k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))) -> JSONResponse:
    """Bis hierhin gesehen.

    Der Stand kommt aus der Anzeige und nicht aus der Datenbank: zwischen dem
    Aufbau des Vorhangs und dem Klick kann ein neuer Alarm eingetroffen sein,
    und den haette der Betrachter dann nie zu Gesicht bekommen.
    """
    stand = max(0, min(int(body.stand or 0), speicher.ereignis_stand()))
    speicher.merker_setzen('alarme_gesehen', stand, konto=str(k.get('name') or ''))
    return JSONResponse({'ok': True, 'stand': stand})


# ── Einstellungen des Logbuchs ──────────────────────────────────────────────
# Bootweit und nicht je Konto: welches Wettermodell fuer dieses Revier taugt,
# ist eine Erkenntnis ueber die Gegend und keine Geschmacksfrage.

_WETTERMODELLE = [
    {'kennung': 'best_match',      'name': 'Automatisch',
     'neben': 'Open-Meteo waehlt je Ort das passendste Modell'},
    {'kennung': 'icon_seamless',   'name': 'DWD ICON',
     'neben': 'Deutscher Wetterdienst — fein aufgeloest über Nord- und Ostsee'},
    {'kennung': 'ecmwf_ifs025',    'name': 'ECMWF IFS',
     'neben': 'Europäisches Zentrum — global stark, gröber im Detail'},
    {'kennung': 'knmi_seamless',   'name': 'KNMI Harmonie',
     'neben': 'Niederlande — Nordsee und südliche Ostsee'},
    {'kennung': 'meteofrance_seamless', 'name': 'Météo-France AROME',
     'neben': 'Frankreich, Ärmelkanal und Biskaya'},
    {'kennung': 'ukmo_seamless',   'name': 'UK Met Office',
     'neben': 'Britische Inseln und Nordsee'},
    {'kennung': 'gfs_seamless',    'name': 'NOAA GFS',
     'neben': 'USA — global, als Gegenprobe'},
]
_MODELL_KENNUNGEN = {m['kennung'] for m in _WETTERMODELLE}
_MODELL_VORGABE = 'icon_seamless'


@app.get('/api/logbuch/einstellungen')
def logbuch_einstellungen(k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))) -> JSONResponse:
    return JSONResponse({
        'wetter_modell': speicher.merker('wetter_modell', vorgabe=_MODELL_VORGABE),
        'wetter_modelle': _WETTERMODELLE,
    })


class LogbuchEinstellungen(BaseModel):
    wetter_modell: str | None = None


@app.post('/api/logbuch/einstellungen')
def logbuch_einstellungen_setzen(body: LogbuchEinstellungen,
                                 k: dict = Depends(braucht(r.EINSTELLEN))) -> JSONResponse:
    if body.wetter_modell is not None:
        if body.wetter_modell not in _MODELL_KENNUNGEN:
            raise HTTPException(400, detail='Unbekanntes Wettermodell')
        speicher.merker_setzen('wetter_modell', body.wetter_modell)
        _wetter_zwischenspeicher.clear()
    return JSONResponse({'ok': True,
                         'wetter_modell': speicher.merker('wetter_modell',
                                                          vorgabe=_MODELL_VORGABE)})


# ── Wetter am Liegeplatz ────────────────────────────────────────────────────
# Vom Server geholt und nicht vom Boot: der Pi haengt am teuersten Datenweg,
# den dieses System hat (Mobilfunk oder Starlink), und das Wetter interessiert
# ohnehin nur den, der gerade ins Logbuch schaut. Es waere die falsche Seite.
#
# Open-Meteo braucht keinen Schluessel und erlaubt die freie Nutzung. Die
# Modellwahl geht mit: ueber der Ostsee liegt ICON deutlich naeher an der
# Wirklichkeit als ein globales Modell, und wer das Revier kennt, soll das
# einstellen koennen.

_WETTER_URL = 'https://api.open-meteo.com/v1/forecast'
_WETTER_FELDER = ('temperature_2m,apparent_temperature,relative_humidity_2m,'
                  'precipitation,weather_code,cloud_cover,pressure_msl,'
                  'wind_speed_10m,wind_direction_10m,wind_gusts_10m')
# Zehn Minuten. Ein Wettermodell rechnet stuendlich; oefter zu fragen holt
# dieselbe Zahl noch einmal und belastet einen fremden, freien Dienst.
_WETTER_FRIST_S = 600
_wetter_zwischenspeicher: dict = {}

# WMO-Schluessel in Worte. Die Tabelle ist genormt; die Uebersetzung ist es
# nicht — "Nieselregen" statt "leichter Niederschlag geringer Intensitaet".
_WMO = {
    0: 'klar', 1: 'überwiegend klar', 2: 'teils bewölkt', 3: 'bedeckt',
    45: 'Nebel', 48: 'gefrierender Nebel',
    51: 'leichter Niesel', 53: 'Niesel', 55: 'starker Niesel',
    56: 'gefrierender Niesel', 57: 'gefrierender Niesel',
    61: 'leichter Regen', 63: 'Regen', 65: 'starker Regen',
    66: 'gefrierender Regen', 67: 'gefrierender Regen',
    71: 'leichter Schneefall', 73: 'Schneefall', 75: 'starker Schneefall',
    77: 'Schneegriesel',
    80: 'Regenschauer', 81: 'Regenschauer', 82: 'kräftige Schauer',
    85: 'Schneeschauer', 86: 'starke Schneeschauer',
    95: 'Gewitter', 96: 'Gewitter mit Hagel', 99: 'schweres Gewitter mit Hagel',
}


def _wetter_wert(jetzt: dict, feld: str):
    """Einen Wert aus dem `current`-Block holen.

    Open-Meteo haengt den Modellnamen an die Feldnamen an, sobald mehr als ein
    Modell abgefragt wird. Wir fragen genau eines ab, dann bleiben die Namen
    schlicht — aber sich darauf zu verlassen hiesse, dass eine spaetere
    Erweiterung auf zwei Modelle die Anzeige lautlos leert.
    """
    if feld in jetzt:
        return jetzt[feld]
    for name, wert in jetzt.items():
        if name.startswith(feld + '_'):
            return wert
    return None


def _wetter_holen(lat: float, lon: float, modell: str) -> dict:
    import urllib.parse
    import urllib.request
    frage = {
        'latitude': f'{lat:.4f}', 'longitude': f'{lon:.4f}',
        'current': _WETTER_FELDER,
        'wind_speed_unit': 'kn',      # an Bord wird in Knoten gesprochen
        'timezone': 'auto',
        'forecast_days': '1',
    }
    if modell and modell != 'best_match':
        frage['models'] = modell
    url = _WETTER_URL + '?' + urllib.parse.urlencode(frage)
    anfrage = urllib.request.Request(url, headers={'User-Agent': 'mave-logbuch'})
    with urllib.request.urlopen(anfrage, timeout=12) as antwort:
        roh = json.loads(antwort.read().decode('utf-8'))
    jetzt = roh.get('current') or {}
    code = _wetter_wert(jetzt, 'weather_code')
    return {
        'temperatur': _wetter_wert(jetzt, 'temperature_2m'),
        'gefuehlt': _wetter_wert(jetzt, 'apparent_temperature'),
        'feuchte': _wetter_wert(jetzt, 'relative_humidity_2m'),
        'niederschlag': _wetter_wert(jetzt, 'precipitation'),
        'bewoelkung': _wetter_wert(jetzt, 'cloud_cover'),
        'druck': _wetter_wert(jetzt, 'pressure_msl'),
        'wind_kn': _wetter_wert(jetzt, 'wind_speed_10m'),
        'boe_kn': _wetter_wert(jetzt, 'wind_gusts_10m'),
        'wind_grad': _wetter_wert(jetzt, 'wind_direction_10m'),
        'code': code,
        'text': _WMO.get(int(code), 'unbekannt') if isinstance(code, (int, float)) else None,
        'gemessen': jetzt.get('time'),
        'hoehe_m': roh.get('elevation'),
        'modell': modell,
        'geholt': time.time(),
    }


@app.get('/api/logbuch/wetter')
async def logbuch_wetter(lat: float = Query(...), lon: float = Query(...),
                         k: dict = Depends(braucht_oberflaeche(r.DIAGNOSE))) -> JSONResponse:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, detail='Unbrauchbare Position')
    modell = speicher.merker('wetter_modell', vorgabe=_MODELL_VORGABE)
    if modell not in _MODELL_KENNUNGEN:
        modell = _MODELL_VORGABE
    # Auf zwei Nachkommastellen gerundet: das sind gut zwei Kilometer, und
    # innerhalb davon rechnet kein Modell etwas anderes. Ohne das Runden waere
    # jede Positionsmeldung des GNSS ein neuer Abruf.
    schluessel = (round(lat, 2), round(lon, 2), modell)
    treffer = _wetter_zwischenspeicher.get(schluessel)
    if treffer and time.time() - treffer['geholt'] < _WETTER_FRIST_S:
        return JSONResponse(treffer)
    try:
        daten = await run_in_threadpool(_wetter_holen, lat, lon, modell)
    except Exception as e:
        log.warning('Wetter nicht abrufbar: %s', e)
        # Lieber ein alter Wert mit ehrlichem Alter als gar keiner: das Wetter
        # aendert sich langsamer als die Verbindung zu einem fremden Dienst.
        if treffer:
            return JSONResponse({**treffer, 'veraltet': True})
        raise HTTPException(502, detail='Wetterdienst nicht erreichbar') from None
    _wetter_zwischenspeicher.clear()      # eine Position, ein Eintrag
    _wetter_zwischenspeicher[schluessel] = daten
    return JSONResponse(daten)


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
from js_bundle_liste import JS_FILES as _JS_FILES, DIAGNOSE_FILES as _DIAGNOSE_FILES
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
    js = STATISCH / 'js'
    text = '\n;// ---\n'.join(
        (js / f).read_text(encoding='utf-8') for f in _DIAGNOSE_FILES if (js / f).exists())
    return Response(text, media_type='application/javascript',
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


@app.get('/wand', include_in_schema=False)
def wandseite() -> HTMLResponse:
    """Dieselbe Seite, ein anderes Manifest.

    Sperrt sich das Tablet, ist der ueber `requestFullscreen()` geholte Vollbild
    danach weg — und die Seite darf ihn nicht selbst zurueckholen: das gewaehrt
    nur ein Fingergriff. Eine Anwendung, die als `display: fullscreen`
    INSTALLIERT ist, startet dagegen immer ohne Browserleiste, auch nach dem
    Entsperren.

    Deshalb ein eigenes Manifest statt einer Aenderung am bestehenden: das
    Telefon in der Hosentasche soll weiter `standalone` bleiben. Die
    Unterscheidung macht Chrome an der `id` — beide lassen sich nebeneinander
    installieren. Wer das Wandtablet so haben will, ruft einmal /wand auf und
    installiert von dort.
    """
    seite = STATISCH / 'index.html'
    if not seite.exists():
        raise HTTPException(500, detail='Oberfläche fehlt im Abbild')
    roh = (seite.read_text(encoding='utf-8')
           .replace('__STAND__', _abbild_stand())
           .replace('href="/static/manifest.json"',
                    'href="/static/manifest-wand.json"'))
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


async def _push_bei_alarm(d: dict) -> None:
    """Einen Alarm als Benachrichtigung hinausschicken.

    Im Threadpool: pywebpush spricht HTTP mit dem Push-Dienst, und das ist
    blockierend. Direkt in der Schleife stuende in dieser Zeit der ganze
    Server — auch der Datenstrom vom Boot.
    """
    kritisch = d.get('schwere') == 'critical'
    name = d.get('name') or 'Alarm'
    wert = d.get('wert')
    text = name + (f' — {wert}' if wert is not None else '')
    try:
        ergebnis = await run_in_threadpool(
            push.senden,
            'Alarm an Bord' if kritisch else 'Hinweis an Bord', text,
            kennung='mave-alarm-' + str(d.get('kennung') or 'x'),
            dringend=kritisch)
        log.info('Alarm gepusht: %s', ergebnis)
    except KeinPush as e:
        log.info('Alarm nicht gepusht: %s', e)
    except Exception as e:
        log.warning('Alarm nicht gepusht: %s', e)


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
        # Der erste Frame ging bisher von Hand zusammengebaut hinaus — mit
        # `quelle` und `alter_s`, aber OHNE `boot_verbunden`. Genau daran macht
        # die Oberflaeche fest, ob sie noch Live-Werte zeigt: sie deutet ein
        # fehlendes Feld als "live", weil ein weitergereichter Frame nur dann
        # entsteht, wenn das Boot gerade sendet.
        #
        # Fuer DIESEN Frame gilt das nicht. Er wird beim Verbinden verschickt,
        # ob das Boot dranhaengt oder nicht. Die Folge war die verschwindende
        # Warnung: die Seite holte per /api/status korrekt "nicht verbunden",
        # zeigte die gelbe Wolke — und der erste Frame nahm sie eine Sekunde
        # spaeter wieder weg. Danach sahen alte Werte aus wie frische.
        #
        # `_stand_fuer_zuschauer()` gibt es genau dafuer; im Docstring steht die
        # Regel, an die sich diese Stelle nicht gehalten hat.
        if speicher.zustand():
            await ws.send_json(_stand_fuer_zuschauer())
        while True:
            # Die Oberflaeche schickt nichts ausser gelegentlichen Lebenszeichen.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _zuschauer.discard(ws)
