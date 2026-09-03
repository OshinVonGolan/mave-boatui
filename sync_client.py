"""Die Verbindung des Bootes zum Server.

Laeuft auf dem Pi, als Aufgabe in der vorhandenen Event-Loop. Der Pi haengt
hinter Mobilfunk-NAT, also baut ER die Verbindung auf und haelt sie. Ueber
denselben Kanal kommen Befehle zurueck (ein WebSocket ist bidirektional) —
deshalb braucht der Pi keinen offenen Port, und genau deshalb ist er von aussen
nicht erreichbar.

Drei Regeln, die aus der Umgebung folgen:

  * **Nichts Blockierendes.** Ein Kern, ein uvicorn-Arbeiter (CLAUDE.md,
    Regel 3). Jede Datei- und HTTP-Arbeit laeuft ueber to_thread.
  * **Sparsam ueber Mobilfunk.** Der Takt haengt am Uplink, den
    connectivity.py ohnehin kennt. Alarme gehen trotzdem sofort hinaus.
  * **Der Server ist Zusatz, nie Voraussetzung.** Faellt er aus, faellt hier
    nichts aus. Der Client protokolliert und versucht es spaeter wieder.

Ohne gesetzte Adresse und Token tut dieses Modul GAR NICHTS — es ist dann, als
gaebe es keinen Server. Das ist der Auslieferungszustand.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import urllib.request

from sync import protokoll as p
from sync import zeit as sz
from sync.startmarke import Startmarke

log = logging.getLogger(__name__)

# Wiederverbinden: erst zuegig, dann immer traeger. Ohne Obergrenze wuerde ein
# tagelanger Ausfall den Pi in ein stundenlanges Warten laufen lassen; ohne
# Streuung faellt das Boot nach einem Serverneustart im Gleichtakt mit allen
# anderen wieder ein (hier nur eines, aber die Gewohnheit ist richtig).
_WARTE_START_S = 5
_WARTE_MAX_S = 300

# Wie lange ein lokal ausgefuehrter Befehl dauern darf. Laenger heisst: etwas
# ist kaputt, und der Server soll das erfahren statt zu warten.
_BEFEHL_FRIST_S = 12.0

# Wie viele Verlaufseintraege auf einmal nachgeliefert werden. Ueber Mobilfunk
# ist ein grosses Paket teurer als mehrere kleine, und ein Abbruch mittendrin
# kostet dann weniger.
_BUENDEL = 200


class SyncClient:
    """Haelt die Verbindung zum Server und bedient sie."""

    def __init__(self, *, adresse: str, token: str, geraet: str, version: str,
                 zustand_holen, verlauf_holen, verlauf_stand, conn_status,
                 schalter=lambda: 'auto', lokal_ausfuehren=None,
                 eigener_port: int = 8080, markenpfad='sync_start.json'):
        self._adresse = (adresse or '').strip()
        self._token = (token or '').strip()
        self._geraet = geraet
        self._version = version
        # Alles, was der Client von der App braucht, kommt als Funktion herein.
        # So haengt er nicht an main.py und ist ohne Boot testbar.
        self._zustand_holen = zustand_holen
        self._verlauf_holen = verlauf_holen
        self._verlauf_stand = verlauf_stand
        self._conn_status = conn_status
        self._schalter = schalter
        self._lokal = lokal_ausfuehren or (
            lambda m, pf, r: lokal_ueber_http(m, pf, r, port=eigener_port))
        self._marke = Startmarke(markenpfad)
        self._befund: dict = {}
        self._ws = None
        self._laeuft = False
        self._letzter_zustand = 0.0
        self._gemeldete_art: str | None = None
        self._start_mono = time.monotonic()
        self.zustand_gesendet = 0
        self.verlauf_gesendet = 0
        self.befehle_ausgefuehrt = 0

    # ── Leben ───────────────────────────────────────────────────────────────

    @property
    def eingerichtet(self) -> bool:
        return bool(self._adresse and self._token)

    @property
    def verbunden(self) -> bool:
        return self._ws is not None

    def start_vermerken(self, *, wand=None, gestellt: bool = False) -> dict:
        """Beim Hochlauf aufrufen — auch ohne Server.

        Die Startmarke muss unabhaengig davon gefuehrt werden, ob eine
        Verbindung besteht: sonst waere nach einem Stromausfall ohne Netz nicht
        mehr feststellbar, dass es einer war.
        """
        self._befund = self._marke.start(wand=wand, gestellt=gestellt)
        if self._befund.get('letztes_ende') == 'abbruch':
            log.warning('Voriger Lauf wurde nicht geordnet beendet — Stromausfall oder Absturz')
        return self._befund

    def geordnet_beenden(self) -> None:
        self._laeuft = False
        self._marke.geordnet_beenden()

    async def laufen(self) -> None:
        """Endlosschleife mit Wiederverbinden. Als Aufgabe starten."""
        if not self.eingerichtet:
            log.info('Kein Server eingerichtet — die Verbindung bleibt aus.')
            return
        self._laeuft = True
        warte = _WARTE_START_S
        while self._laeuft:
            try:
                await self._sitzung()
                warte = _WARTE_START_S          # eine gute Sitzung setzt zurueck
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.info('Serververbindung nicht moeglich (%s) — neuer Versuch in %d s',
                         e, warte)
            self._ws = None
            if not self._laeuft:
                break
            await asyncio.sleep(warte + random.uniform(0, warte * 0.2))
            warte = min(warte * 2, _WARTE_MAX_S)

    # ── Eine Sitzung ────────────────────────────────────────────────────────

    async def _sitzung(self) -> None:
        import websockets                      # erst hier: fehlt es, laeuft der Rest weiter
        async with websockets.connect(
                self._adresse,
                additional_headers={'Authorization': f'Bearer {self._token}'},
                open_timeout=20, ping_interval=30, ping_timeout=20,
                max_size=p.MAX_BYTES) as ws:
            self._ws = ws
            self._gemeldete_art = self._art()   # steht im hallo, nicht doppelt melden
            await self._senden(p.hallo(
                self._geraet, p.FASSUNG, self._version,
                await asyncio.to_thread(self._verlauf_stand),
                self._art(), **self._jetzt()) | {'daten': {
                    'geraet': self._geraet, 'fassung': p.FASSUNG, 'version': self._version,
                    'verlauf_folge': await asyncio.to_thread(self._verlauf_stand),
                    'betriebsart': self._art(), 'start': self._befund}})

            antwort = p.pruefe(json.loads(await ws.recv()), vom_pi=False)
            if antwort['typ'] != p.STAND:
                raise RuntimeError(f'Erwartet wurde der Stand, kam: {antwort["typ"]}')
            ab = int((antwort['daten'] or {}).get('verlauf_bis', 0)) + 1
            log.info('Mit dem Server verbunden. Verlauf ab %d, Betriebsart %s', ab, self._art())

            # Nachliefern und laufender Betrieb nebeneinander: das Nachliefern
            # kann dauern, der Zustand soll trotzdem aktuell bleiben.
            await asyncio.gather(
                self._nachliefern(ab),
                self._zustand_schleife(),
                self._empfangen(ws),
            )

    async def _empfangen(self, ws) -> None:
        while True:
            roh = json.loads(await ws.recv())
            try:
                n = p.pruefe(roh, vom_pi=False)
            except p.ProtokollFehler as e:
                log.warning('Nachricht vom Server abgewiesen: %s', e)
                continue
            if n['typ'] == p.BEFEHL:
                # Nebenlaeufig ausfuehren: ein langsamer Befehl darf den
                # Empfang nicht anhalten.
                asyncio.create_task(self._befehl(n['daten'] or {}))
            elif n['typ'] == p.PING:
                await self._senden(p.umschlag(p.PONG))

    async def _befehl(self, b: dict) -> None:
        """Einen Befehl lokal ausfuehren und quittieren.

        Ausgefuehrt wird gegen die EIGENE API — dieselbe Pruefung, dieselben
        Regeln wie bei einem Griff an Bord. Der Server bekommt keine Sonderrechte.
        """
        kennung = b.get('kennung')
        try:
            status, antwort = await asyncio.wait_for(
                asyncio.to_thread(self._lokal, b.get('methode', 'POST'),
                                  b.get('pfad', ''), b.get('rumpf')),
                timeout=_BEFEHL_FRIST_S)
            self.befehle_ausgefuehrt += 1
            await self._senden(p.umschlag(p.QUITTUNG, {
                'kennung': kennung, 'ok': 200 <= status < 300,
                'status': status, 'antwort': antwort,
                'fehler': None if 200 <= status < 300 else (antwort or {}).get('detail'),
            }))
        except Exception as e:
            log.warning('Befehl %s fehlgeschlagen: %s', b.get('pfad'), e)
            await self._senden(p.umschlag(p.QUITTUNG, {
                'kennung': kennung, 'ok': False, 'status': 502, 'fehler': str(e)}))

    # ── Senden ──────────────────────────────────────────────────────────────

    async def _zustand_schleife(self) -> None:
        while True:
            art = self._art()
            # Wechselt der Uplink, aendert sich der Takt — und der Server soll
            # wissen, warum seltener Daten kommen. Im hallo steht die Art nur
            # EINMAL; ohne diese Meldung bliebe sie dort fuer immer stehen.
            if art != self._gemeldete_art:
                self._gemeldete_art = art
                await self._senden(p.umschlag(p.EREIGNIS, {
                    'art': 'betriebsart', 'betriebsart': art,
                    'takt_s': p.takt(art)['zustand_s'],
                }, folge=0, **self._jetzt()))
                log.info('Betriebsart jetzt %s (Takt %d s)', art, p.takt(art)['zustand_s'])
            takt = p.takt(art)['zustand_s']
            jetzt = time.monotonic()
            if jetzt - self._letzter_zustand >= takt:
                zustand = await asyncio.to_thread(self._zustand_holen)
                if zustand is not None:
                    await self._senden(p.umschlag(p.ZUSTAND, zustand, **self._jetzt()))
                    self.zustand_gesendet += 1
                self._letzter_zustand = jetzt
            await asyncio.sleep(1)

    async def _nachliefern(self, ab: int) -> None:
        """Verlauf ab der genannten Folgenummer schicken, in Buendeln.

        Der Server nennt seinen Stand, hier wird ab dort weitergeschickt — das
        ist die ganze Logik. Kein Zustandsabgleich, keine Zustandsmaschine.
        """
        while True:
            eintraege = await asyncio.to_thread(self._verlauf_holen, ab, _BUENDEL)
            if not eintraege:
                return
            hoechste = max(int(e['folge']) for e in eintraege)
            await self._senden(p.umschlag(p.VERLAUF, eintraege, folge=hoechste,
                                          **self._jetzt()))
            self.verlauf_gesendet += len(eintraege)
            ab = hoechste + 1
            # Luft lassen: ueber Mobilfunk soll das Nachliefern den laufenden
            # Betrieb nicht verdraengen.
            await asyncio.sleep(0.5)

    async def ereignis(self, art: str, daten: dict, folge: int) -> None:
        """Alarme und Stoerungen. Gehen in JEDER Betriebsart sofort hinaus —
        sie sind der Grund, warum das System nach draussen spricht."""
        if self._ws is None:
            return
        await self._senden(p.umschlag(p.EREIGNIS, {'art': art, **daten},
                                      folge=folge, **self._jetzt()))

    async def _senden(self, nachricht: dict) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps(nachricht, ensure_ascii=False))

    # ── Kleinkram ───────────────────────────────────────────────────────────

    def _art(self) -> str:
        try:
            return p.betriebsart(self._conn_status(), self._schalter())
        except Exception:
            return p.GEDROSSELT          # im Zweifel sparsam

    def _jetzt(self) -> dict:
        """Die drei Zeitangaben. Ob die Uhr steht, entscheidet ihr Wert:
        vor 2024 kann kein Eintrag dieser Anlage liegen."""
        wand = time.time()
        return {'wand': wand, 'mono': time.monotonic(),
                'gestellt': wand >= 1704067200.0}


def lokal_ueber_http(methode: str, pfad: str, rumpf, port: int = 8080) -> tuple:
    """Einen Befehl gegen die eigene API ausfuehren.

    Ueber HTTP an 127.0.0.1 und nicht durch einen direkten Funktionsaufruf:
    so durchlaeuft der Befehl dieselbe Pruefung wie ein Griff an Bord, und es
    gibt keine zweite Stelle, an der Regeln gepflegt werden muessten. Der
    Umweg kostet auf dem Pi einen lokalen Aufruf ohne TLS.
    """
    ziel = f'http://127.0.0.1:{port}{pfad}'
    # Bei GET keinen Rumpf mitschicken: ein GET mit Koerper ist unsauber, und
    # manche Bibliotheken machen daraus stillschweigend ein POST.
    daten = None if methode.upper() == 'GET' else (
        json.dumps(rumpf).encode() if rumpf is not None else b'{}')
    req = urllib.request.Request(ziel, data=daten, method=methode)
    if daten is not None:
        req.add_header('Content-Type', 'application/json')
    # Kennzeichnet den Aufruf als aus der Ferne kommend. Die App kann damit
    # spaeter unterscheiden, was aus dem Bordnetz und was von aussen kam.
    req.add_header('X-Herkunft', 'server')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            roh = r.read()
            return r.status, (json.loads(roh) if roh else {})
    except urllib.error.HTTPError as e:
        roh = e.read()
        try:
            return e.code, json.loads(roh) if roh else {}
        except ValueError:
            return e.code, {'detail': roh.decode('utf-8', 'replace')[:200]}
