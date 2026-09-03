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

import json
import logging
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from server.befehle import (DURCHLEITEN, KeinBoot, Vermittlung,
                            Zeitueberschreitung, gesperrt)
from server.speicher import Speicher
from sync import protokoll as p
from sync import rechte as r
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
_uhrbuch = sz.Uhrbuch()
_vermittlung = Vermittlung()
_verbindung: dict = {'sitzung': None, 'seit': None, 'geraet': None, 'betriebsart': None}


@app.on_event('startup')
def _start() -> None:
    global speicher
    DATEN.mkdir(parents=True, exist_ok=True)
    speicher = Speicher(DATEN / 'mave.db')
    if not GERAET_TOKEN:
        log.error('MAVE_GERAET_TOKEN ist nicht gesetzt — das Boot kann sich nicht anmelden.')
    if not UEBERGANG_PASSWORT:
        log.error('MAVE_PASSWORT ist nicht gesetzt — die Leseseite bleibt zu.')
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
    quelle = _herkunft(request)
    jetzt = time.time()
    versuche = [t for t in _FEHLVERSUCHE.get(quelle, []) if jetzt - t < _SPERRE_S]
    if len(versuche) >= _MAX_VERSUCHE:
        _FEHLVERSUCHE[quelle] = versuche
        raise HTTPException(429, detail='Zu viele Fehlversuche. Bitte später erneut.')

    kopf = request.headers.get('authorization', '')
    gegeben = kopf[7:].strip() if kopf.lower().startswith('bearer ') else ''
    if not UEBERGANG_PASSWORT or not gegeben or \
            not secrets.compare_digest(gegeben, UEBERGANG_PASSWORT):
        versuche.append(jetzt)
        _FEHLVERSUCHE[quelle] = versuche
        # Kein WWW-Authenticate: der Browser soll keinen eigenen Dialog
        # aufmachen, die Anmeldung gehoert in die Oberflaeche.
        raise HTTPException(401, detail='Anmeldung nötig')
    _FEHLVERSUCHE.pop(quelle, None)
    return {'rolle': 'eigner', 'name': 'übergang'}


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
    await ws.send_json(p.stand(speicher.verlauf_stand()))
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
            ergebnis = await _vermittlung.senden(methode, ziel, rumpf)
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
def startseite() -> HTMLResponse:
    seite = STATISCH / 'index.html'
    if not seite.exists():
        raise HTTPException(500, detail='Oberfläche fehlt im Abbild')
    return HTMLResponse(seite.read_text(encoding='utf-8'),
                        headers={'Cache-Control': 'no-cache'})


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
        ergebnis = await _vermittlung.senden('GET', pfad, None, frist=_DURCHREICHEN_FRIST_S)
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
    # Der WebSocket kommt nicht durch die HTTP-Anmeldung — Browser koennen bei
    # einer WebSocket-Verbindung keine eigenen Koepfe setzen. Geschuetzt ist er
    # dadurch, dass Caddy die Anmeldung schon beim Laden der Seite verlangt hat
    # und der Browser das Cookie mitschickt. Mit der eigenen Anmeldung wird das
    # hier durch eine Sitzungspruefung ersetzt.
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
