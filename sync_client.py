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
# Ab wann eine Sitzung als gelungen gilt — auch wenn sie mit einer Ausnahme
# endete. Eine Minute reicht als Beleg: der Handschlag steht, das hallo ist
# durch, Daten sind geflossen. Was danach abreisst, liegt an der Leitung oder
# an der Gegenseite und nicht daran, dass wir zu frueh wiederkommen.
_GUTE_SITZUNG_S = 60

# Wie lange ein lokal ausgefuehrter Befehl dauern darf. Laenger heisst: etwas
# ist kaputt, und der Server soll das erfahren statt zu warten.
_BEFEHL_FRIST_S = 12.0

# Wie viele Verlaufseintraege auf einmal nachgeliefert werden. Ueber Mobilfunk
# ist ein grosses Paket teurer als mehrere kleine, und ein Abbruch mittendrin
# kostet dann weniger.
_BUENDEL = 200


# Wie lange gewartet wird, wenn gerade nichts nachzuliefern ist. Eine
# Verlaufszeile entsteht je Minute — zwanzig Sekunden sind prompt genug.
_NACHLIEFERN_RUHE_S = 20

# Dasselbe fuer die Heizung. Ihre Saetze kommen vom Hub und entstehen ebenfalls
# je Minute; hier wird seltener nachgesehen, weil jedes Nachsehen eine HTTP-
# Abfrage AN DEN HUB ist und der ein ESP32 mit einem Kern ist (nicht oefter als
# 1 Hz, sagt seine Anleitung — eine Abfrage je Minute ist weit darunter).
_HEIZUNG_RUHE_S = 60

# Wie viele Heizungssaetze auf einmal gehen. Ein Satz ist klein (eine Handvoll
# Zahlen je Raum); dreihundert bleiben deutlich unter der Nachrichtengrenze und
# machen aus einem nachgeholten Monat trotzdem keine Dauerlast.
_HEIZUNG_BUENDEL = 300

# Ereignisse brauchen eine Nummer, die es nur einmal gibt.
#
# Der Server legt sie mit `folge` als PRIMAERSCHLUESSEL und INSERT OR IGNORE ab:
# eine Nummer, die schon dasteht, faellt still weg. Die Betriebsart-Meldung
# schickte immer 0 — in der Datenbank steht deshalb genau EIN solches Ereignis,
# jeder weitere Wechsel ist verlorengegangen, ohne dass irgendwo ein Fehler
# aufgetaucht waere.
#
# Millisekunden seit 1970: monoton, ohne Zustand, ueberlebt einen Neustart. Der
# Vergleich mit der zuletzt vergebenen Nummer faengt den Fall ab, dass zwei
# Ereignisse in dieselbe Millisekunde fallen.
_letzte_ereignis_folge = 0


def _ereignis_folge() -> int:
    global _letzte_ereignis_folge
    n = max(int(time.time() * 1000), _letzte_ereignis_folge + 1)
    _letzte_ereignis_folge = n
    return n


class SyncClient:
    """Haelt die Verbindung zum Server und bedient sie."""

    def __init__(self, *, adresse: str, token: str, geraet: str, version: str,
                 zustand_holen, verlauf_holen, verlauf_stand, conn_status,
                 schalter=lambda: 'auto', lokal_ausfuehren=None,
                 heizung_saetze=None, heizung_raeume=None,
                 konten_stand=lambda: '', konten_uebernehmen=None,
                 intern_token: str = '',
                 eigener_port: int = 8080, markenpfad='sync_start.json'):
        self._adresse = (adresse or '').strip()
        # Die Kontenkopie kommt vom Server; der Client kennt die Kontenhaltung
        # nicht, sondern nur diese beiden Griffe.
        self._konten_stand = konten_stand
        self._konten_uebernehmen = konten_uebernehmen
        self._token = (token or '').strip()
        self._geraet = geraet
        self._version = version
        # Alles, was der Client von der App braucht, kommt als Funktion herein.
        # So haengt er nicht an main.py und ist ohne Boot testbar.
        self._zustand_holen = zustand_holen
        self._verlauf_holen = verlauf_holen
        self._verlauf_stand = verlauf_stand
        self._conn_status = conn_status
        # Die Heizung fuehrt ihren Verlauf selbst — im Hub. Der Pi holt ihn nur
        # ab und reicht ihn weiter; ohne diese beiden Griffe passiert das
        # schlicht nicht, und alles andere laeuft weiter.
        self._heizung_saetze = heizung_saetze
        self._heizung_raeume = heizung_raeume or (lambda: {})
        self._schalter = schalter
        self._lokal = lokal_ausfuehren or (
            lambda m, pf, r, konto='': lokal_ueber_http(
                m, pf, r, port=eigener_port, konto=konto, intern=intern_token))
        self._marke = Startmarke(markenpfad)
        self._befund: dict = {}
        self._ws = None
        self._laeuft = False
        self._letzter_zustand = 0.0
        self._gemeldete_art: str | None = None
        self._start_mono = time.monotonic()
        self._push_schluessel = ''
        self.zustand_gesendet = 0
        self.verlauf_gesendet = 0
        self.heizung_gesendet = 0
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
            begonnen = time.monotonic()
            try:
                await self._sitzung()
                stand = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                stand = False
                log.info('Serververbindung nicht moeglich (%s) — neuer Versuch in %d s',
                         e, warte)
            # Eine Sitzung, die STAND, setzt die Wartezeit zurueck — auch wenn
            # sie mit einer Ausnahme endete. Genau so endet die normale: die
            # Gegenseite geht weg, und `websockets` wirft.
            #
            # Vorher setzte nur ein Ende OHNE Ausnahme zurueck, und das kommt im
            # Betrieb praktisch nie vor. Die Wartezeit verdoppelte sich deshalb
            # ueber die Lebensdauer des Dienstes hinweg bis auf fuenf Minuten —
            # und nach einem Neustart des Servers stand im Logbuch minutenlang
            # "Boot nicht verbunden", obwohl der Pi die ganze Zeit da war und
            # nur brav wartete. Gemessen: nach einem Neustart dauerte es dreiein-
            # halb Minuten, bis sich das Boot wieder meldete.
            if stand or time.monotonic() - begonnen >= _GUTE_SITZUNG_S:
                warte = _WARTE_START_S
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
                    'betriebsart': self._art(), 'start': self._befund,
                    'konten_stand': self._konten_stand()}})

            antwort = p.pruefe(json.loads(await ws.recv()), vom_pi=False)
            if antwort['typ'] != p.STAND:
                raise RuntimeError(f'Erwartet wurde der Stand, kam: {antwort["typ"]}')
            # Der oeffentliche Push-Schluessel kommt im Handschlag mit. Das Boot
            # braucht ihn, um ein Geraet im Bordnetz anmelden zu koennen; er
            # gehoert dem Server, der auch sendet.
            self._push_schluessel = str((antwort['daten'] or {}).get('push_schluessel') or '')
            ab = int((antwort['daten'] or {}).get('verlauf_bis', 0)) + 1
            heiz_ab = float((antwort['daten'] or {}).get('heizung_bis', 0) or 0.0)
            log.info('Mit dem Server verbunden. Verlauf ab %d, Betriebsart %s', ab, self._art())

            # Nachliefern und laufender Betrieb nebeneinander: das Nachliefern
            # kann dauern, der Zustand soll trotzdem aktuell bleiben.
            await asyncio.gather(
                self._nachliefern(ab),
                self._heizung_nachliefern(heiz_ab),
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
            elif n['typ'] == p.KONTEN:
                await self._konten(n['daten'] or {})
            elif n['typ'] == p.PING:
                await self._senden(p.umschlag(p.PONG))

    async def _konten(self, daten: dict) -> None:
        """Die Kontenkopie vom Server uebernehmen.

        Sie ersetzt den lokalen Bestand vollstaendig — der Server ist die
        Wahrheit. Anders liesse sich ein dort gesperrtes Konto an Bord nicht
        aussperren, und das waere die gefaehrlichere Haelfte: wer von Bord
        verwiesen wurde, soll nicht ueber das Bord-WLAN weiterschalten.
        """
        if not self._konten_uebernehmen:
            return
        try:
            anzahl = await asyncio.to_thread(self._konten_uebernehmen, daten)
            log.info('Kontenkopie übernommen: %s Konten, Stand %s',
                     anzahl, daten.get('stand') or '—')
        except Exception as e:
            log.warning('Kontenkopie nicht übernommen: %s', e)

    async def _befehl(self, b: dict) -> None:
        """Einen Befehl lokal ausfuehren und quittieren.

        Ausgefuehrt wird gegen die EIGENE API — dieselbe Pruefung, dieselben
        Regeln wie bei einem Griff an Bord. Der Server bekommt keine Sonderrechte.
        """
        kennung = b.get('kennung')
        try:
            status, antwort = await asyncio.wait_for(
                asyncio.to_thread(self._lokal, b.get('methode', 'POST'),
                                  b.get('pfad', ''), b.get('rumpf'),
                                  b.get('konto') or ''),
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
                }, folge=_ereignis_folge(), **self._jetzt()))
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
        """Verlauf ab der genannten Folgenummer schicken — und weiter schicken.

        Der Server nennt beim Verbinden seinen Stand, hier wird ab dort
        weitergeschickt. Das war frueher die ganze Logik: aufholen und fertig.

        Damit stand der Verlauf auf dem Server still, SOLANGE DIE VERBINDUNG
        HIELT. Jede Minute entsteht an Bord eine neue Zeile, aber keine ging
        hinaus; erst der naechste Verbindungsaufbau holte sie nach. Ein Boot,
        das eine Woche durchgehend online ist, lieferte eine Woche lang keinen
        Verlauf — und im Logbuch sah es aus, als sei nichts gemessen worden.
        Aufgefallen ist es, als die Position dazukam und die Spur einfach nicht
        wuchs.

        Jetzt bleibt die Schleife stehen und sieht in Ruhe nach, ob etwas Neues
        da ist. Ist nichts da, geht auch nichts hinaus — das Nachsehen selbst
        kostet nur einen Griff in den lokalen Speicher, kein Byte auf der
        Leitung.
        """
        while True:
            roh = await asyncio.to_thread(self._verlauf_holen, ab, _BUENDEL)
            eintraege = [e for e in (_verlaufspaket(r) for r in (roh or []))
                         if e is not None]
            if not eintraege:
                # Nichts Neues. Eine Zeile entsteht je Minute; alle zwanzig
                # Sekunden nachzusehen ist prompt genug und belastet nichts.
                await asyncio.sleep(_NACHLIEFERN_RUHE_S)
                continue
            hoechste = max(int(e['folge']) for e in eintraege)
            await self._senden(p.umschlag(p.VERLAUF, eintraege, folge=hoechste,
                                          **self._jetzt()))
            self.verlauf_gesendet += len(eintraege)
            ab = hoechste + 1
            # Luft lassen: ueber Mobilfunk soll das Nachliefern den laufenden
            # Betrieb nicht verdraengen.
            await asyncio.sleep(0.5)

    async def _heizung_nachliefern(self, ab: float) -> None:
        """Den Heizungsverlauf vom Hub holen und weiterreichen.

        Der Pi schreibt die Heizung NICHT selbst mit — der Hub fuehrt sie
        ohnehin, und zwar tiefer und laenger, als der Pi es je taete. Was ihm
        fehlt, ist der Weg nach draussen: an den Hub kommt nur das Bordnetz
        heran. Genau diese Strecke ist diese Schleife, und mehr ist sie nicht.

        Wo angesetzt wird, sagt der SERVER (`heizung_bis` im Handschlag). Damit
        braucht der Pi keinen eigenen Merker, und der Fall, der sonst Muehe
        macht, loest sich von selbst: war der Pi zwei Tage aus, nennt der Server
        seinen alten Stand, der Hub hat die zwei Tage noch, und sie werden
        nachgereicht. Ein eigener Mitschnitt an Bord haette in dieser Zeit
        nichts gehabt.

        Welche Aufloesung dabei zu holen ist, entscheidet der Griff selbst
        (`heizung_saetze`, an Bord die Heizung): die Minutenebene des Hubs
        reicht 24 Stunden zurueck, danach gibt es nur noch Viertelstunden,
        Stunden, Tage. Hier steht nur die Strecke — was der Hub kann, weiss der
        Hub.
        """
        if self._heizung_saetze is None:
            return                       # keine Heizung eingerichtet
        while True:
            jetzt = time.time()
            # Ohne bekannten Stand nicht die ganze Ablage des Hubs leerraeumen:
            # der erste Lauf holt einen Tag: das ist die Minutenebene, sie
            # kostet wenig und ist sofort etwas wert. Aelteres kann jederzeit
            # nachgeholt werden, indem der Stand zurueckgesetzt wird.
            von = ab + 1 if ab > 0 else jetzt - 24 * 3600
            if von >= jetzt:
                await asyncio.sleep(_HEIZUNG_RUHE_S)
                continue
            try:
                nachschub = await asyncio.to_thread(self._heizung_saetze, von, jetzt) or {}
            except Exception as e:
                # Der Hub ist Beiwerk: ist er aus oder gerade beschaeftigt,
                # wartet die Schleife und versucht es wieder. Die Verbindung
                # zum Server haengt nicht davon ab.
                log.debug('Heizungsverlauf nicht abrufbar: %s', e)
                nachschub = {}
            saetze = nachschub.get('saetze') or []
            aufloesung = str(nachschub.get('aufloesung') or '')
            if not saetze:
                await asyncio.sleep(_HEIZUNG_RUHE_S)
                continue
            buendel = saetze[:_HEIZUNG_BUENDEL]
            juengster = max(float(s['zeit']) for s in buendel)
            if juengster <= ab:
                # Nichts Neues, obwohl etwas kam. Das passiert, wenn der Hub
                # `from` auf den Beginn seines Rasters abrundet und denselben
                # Satz noch einmal liefert. Ohne diese Bremse liefe die
                # Schleife heiss und schickte im Sekundentakt dasselbe.
                await asyncio.sleep(_HEIZUNG_RUHE_S)
                continue
            await self._senden(p.heizung(buendel, aufloesung, self._raeume()))
            self.heizung_gesendet += len(buendel)
            ab = juengster
            # Luft lassen — dasselbe wie beim Verlauf des Bootes: ein
            # nachgeholter Monat darf den laufenden Betrieb nicht verdraengen.
            await asyncio.sleep(0.5)

    def _raeume(self) -> dict:
        """Die Raumnamen fuers Logbuch. Fehlen sie, wird dort nummeriert."""
        try:
            return self._heizung_raeume() or {}
        except Exception:
            return {}

    async def sitzung_melden(self, kennung: str, daten: dict,
                             beendet: bool = False) -> None:
        """Eine an Bord entstandene Anmeldung zum Server tragen.

        Ohne Verbindung passiert nichts, und das ist richtig: die Sitzung gilt
        an Bord trotzdem, und beim naechsten Verbinden gleicht sich beides ab.
        """
        if self._ws is None:
            return
        try:
            await self._senden(p.sitzung(kennung, daten, beendet))
        except Exception as e:
            log.debug('Sitzung nicht gemeldet: %s', e)

    @property
    def push_schluessel(self) -> str:
        """Der oeffentliche Schluessel des Servers, aus dem Handschlag."""
        return self._push_schluessel

    async def push_melden(self, abo: dict, konto: str, geraet: str = '',
                          abmelden: bool = False) -> None:
        """Ein an Bord entstandenes Push-Abo zum Server tragen.

        Ohne Verbindung passiert nichts, und dann ist das Abo auch nutzlos: der
        Server sendet, nicht das Boot. Wer sich im Bordnetz ohne Internet
        anmeldet, bekommt genau deshalb eine ehrliche Rueckmeldung statt einer
        stillen Ablage.
        """
        if self._ws is None:
            raise RuntimeError('Kein Server erreichbar — ohne ihn kann niemand senden.')
        await self._senden(p.push_abo(abo, konto, geraet, abmelden))

    async def ereignis(self, art: str, daten: dict, folge: int | None = None) -> None:
        """Alarme und Stoerungen. Gehen in JEDER Betriebsart sofort hinaus —
        sie sind der Grund, warum das System nach draussen spricht.

        Ohne Verbindung passiert nichts. Das ist hier kein Mangel: der Alarm
        steht an Bord trotzdem und wird dort auch angezeigt. Was fehlt, ist die
        Meldung nach draussen — und die kann ohne Leitung niemand geben.
        """
        if self._ws is None:
            return
        await self._senden(p.umschlag(p.EREIGNIS, {'art': art, **daten},
                                      folge=folge if folge is not None else _ereignis_folge(),
                                      **self._jetzt()))

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


def _verlaufspaket(e: dict):
    """Einen Verlaufseintrag in die Form bringen, die der Server erwartet.

    Der Verlauf an Bord ist eine flache Zeile Messwerte mit Zeitstempel und
    Folgenummer — was die Anzeige braucht. Der Server braucht etwas anderes:
    Folge, Zeit und die Messwerte als eigenes Feld, damit er jeden Eintrag
    einzeln zeitlich einordnen kann.

    Diese Umsetzung fehlte. Der Pi schickte seine Rohzeilen, der Server las
    darin `folge` und `wand` — beides gab es nicht, also legte er Eintraege
    ohne Nummer und ohne Zeit ab. Bemerkt hat es niemand, weil auf keiner
    Seite ein Fehler entstand: es kam nur nie etwas an.

    `gestellt` wird an der Plausibilitaet des Zeitstempels entschieden. Der Pi
    hat keine gepufferte Uhr; lief er ohne Netz hoch, stehen dort Zeiten aus
    1970. Der Server kann solche Eintraege parken und spaeter einordnen — aber
    nur, wenn er sie als unsicher erkennt.
    """
    if not isinstance(e, dict):
        return None
    folge, ts = e.get('n'), e.get('ts')
    if not isinstance(folge, int) or isinstance(folge, bool) or ts is None:
        return None
    daten = {k: v for k, v in e.items() if k not in ('n', 'ts')}
    return {
        'folge': int(folge),
        'wand': float(ts),
        # 2020 als Grenze: alles davor kann keine echte Bordzeit sein.
        'gestellt': float(ts) > 1577836800.0,
        'daten': daten,
    }


def lokal_ueber_http(methode: str, pfad: str, rumpf, port: int = 8080,
                     konto: str = '', intern: str = '') -> tuple:
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
    # Wer auf dem Server gefragt hat. Ohne diese Angabe kaeme der Aufruf ohne
    # Anmeldung an der eigenen Tuer an und wuerde abgewiesen — der Server
    # reicht durch, aber die Sitzung des Nutzers liegt bei ihm, nicht hier.
    # Das Geheimnis weist den Aufruf als den eigenen Prozess aus; ueber die
    # RECHTE entscheidet danach der Pi anhand seiner eigenen Kontenkopie.
    if intern:
        req.add_header('X-Mave-Intern', intern)
    if konto:
        req.add_header('X-Mave-Konto', konto)
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
