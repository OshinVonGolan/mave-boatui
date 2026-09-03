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
from fastapi.responses import JSONResponse

from server.befehle import (DURCHLEITEN, KeinBoot, Vermittlung,
                            Zeitueberschreitung, gesperrt)
from server.speicher import Speicher
from sync import protokoll as p
from sync import rechte as r
from sync import zeit as sz

log = logging.getLogger('mave-server')

DATEN = Path(os.environ.get('MAVE_DATEN', '/daten'))
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
    return sitzung


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
