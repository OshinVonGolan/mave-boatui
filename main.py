"""Mave Boat Monitor — FastAPI Backend."""
import asyncio
import functools
import json
import logging
import math
import os
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import zlib
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from alarm_engine import AlarmEngine
from can_reader import BoatState, CanInterface
from charge_control import ChargeController
from connectivity import ConnectivityMonitor
from daily_stats import _MAX_DAYS as DAILY_STATS_MAX_DAYS   # Aufbewahrung im Tracker
import geraete
from heating import StokerClient, StokerFehler
from history_store import HistoryStore
from jsonio import read_json, write_json

def _git_semver() -> str:
    """Returns semver tag (e.g. '1.5.3') if HEAD is exactly on a tag, else ''."""
    try:
        r = subprocess.run(
            ['git', 'describe', '--tags', '--exact-match', 'HEAD'],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip().lstrip('v') if r.returncode == 0 else ''
    except Exception:
        return ''

def _git_hash() -> str:
    """Returns short commit hash, e.g. 'e1354d6'."""
    try:
        r = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        return ''

VERSION  = _git_semver() or '1.53.1'
GIT_HASH = _git_hash()

# Hintergrund-Cache: lesbare Remote-Version + ob ein Update verfügbar ist.
# Wird periodisch in einem Thread aktualisiert, damit der Endpunkt nie blockiert.
_remote_ver = {'ts': 0.0, 'version': '', 'hash': '', 'up_to_date': None}

def _parse_version_fallback(text: str) -> str:
    """Liest die lesbare Version aus  VERSION = _git_semver() or 'x.y.z'  ."""
    m = re.search(r"_git_semver\(\)\s*or\s*'([^']+)'", text)
    return m.group(1) if m else ''

def _refresh_remote_version() -> bool:
    """Gleicht den Remote-Stand ab. Liefert True, wenn das geklappt hat."""
    try:
        fetch = subprocess.run(['git', 'fetch', '--quiet'], cwd=Path(__file__).parent, timeout=30)
        h = subprocess.run(['git', 'rev-parse', '--short', '@{u}'],
                           cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        rhash = h.stdout.strip() if h.returncode == 0 else ''
        show = subprocess.run(['git', 'show', '@{u}:main.py'],
                              cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        rver = _parse_version_fallback(show.stdout) if show.returncode == 0 else ''
        _remote_ver.update(ts=time.time(), version=rver, hash=rhash,
                           up_to_date=((rhash == GIT_HASH) if rhash else None))
        return fetch.returncode == 0 and bool(rhash)
    except Exception as e:
        logging.getLogger(__name__).debug('Remote-Version-Check: %s', e)
        return False

# 30 Minuten statt 5: ein `git fetch` bedeutet auf dem Pi Zero Prozessstart,
# Zugriff auf die SD-Karte, DNS und einen TLS-Handshake — auf ARMv6 ohne
# Krypto-Beschleunigung teuer, und das auf dem einzigen Kern. Ohne Netz laeuft
# jeder Versuch stur in seine Timeouts (bis zu 50 s), deshalb faellt die
# Wartezeit bei Fehlschlag jeweils aufs Doppelte zurueck, hoechstens 2 Stunden.
# 30 min waren zu traege: nach einem Push dauerte es gefuehlt ewig, bis die App
# ueberhaupt anbot zu aktualisieren. Der teure Teil ist nicht die Haeufigkeit,
# sondern ein haengender git fetch ohne Netz — dagegen hilft der Rueckfall unten,
# nicht ein langes Grundintervall.
_VERSION_CHECK_INTERVAL = 300    # Normalfall: alle 5 min
_VERSION_CHECK_MAX      = 7200   # Rueckfall offline: hoechstens alle 2 h

def _remote_version_loop():
    wartezeit = _VERSION_CHECK_INTERVAL
    while True:
        if _refresh_remote_version():
            wartezeit = _VERSION_CHECK_INTERVAL
        else:
            wartezeit = min(wartezeit * 2, _VERSION_CHECK_MAX)
            logging.getLogger(__name__).debug(
                'Remote-Version nicht erreichbar — naechster Versuch in %d min',
                wartezeit // 60)
        time.sleep(wartezeit)


async def _run_blocking(fn, *args, **kwargs):
    """Führt eine blockierende Funktion im Thread-Pool aus.

    Auf dem Pi Zero (ein Kern) legt jede blockierende Zeile im Event-Loop den
    ganzen Server lahm — Datei-Schreibvorgänge (fsync auf SD-Karte),
    Unterprozesse und HTTP-Aufrufe gehören deshalb konsequent hier hinein.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger(__name__)

BASE_DIR      = Path(__file__).parent
PRESETS_FILE  = BASE_DIR / 'presets.json'
STATIC_DIR    = BASE_DIR / 'static'
STAUPLAN_FILE = BASE_DIR / 'stauplan.json'
HEIZUNG_FILE  = BASE_DIR / 'heizung.json'
WARTUNG_FILE  = BASE_DIR / 'wartung.json'
DEVICES_FILE  = BASE_DIR / 'devices.json'
DEVICES_VORLAGE = BASE_DIR / 'devices.example.json'
HISTORY_FILE  = BASE_DIR / 'history.ndjson'
HISTORY_GROB_FILE = BASE_DIR / 'history_min.ndjson'

state       = BoatState()
can_if      = CanInterface(channel='can0', state=state,
                           stats_path=BASE_DIR / 'daily_stats.json')
alarms      = AlarmEngine()
charge_ctrl = ChargeController()
can_if._charger_config_cb = charge_ctrl.update_actual_setpoints


def _alarm_alert(key: str, active: bool):
    """Kritischer Alarm → PGN 126983 Alert auf den Bus (NMEA-2000-Buzzer)."""
    alert_id = (zlib.crc32(key.encode()) & 0xFFFF) or 1   # stabile 16-Bit-ID pro Regel
    can_if.send_alert(alert_id, active, priority=0)

alarms.set_alert_callback(_alarm_alert)

_CONN_FILE = BASE_DIR / 'connectivity.json'
if _CONN_FILE.exists():
    _conn_cfg  = read_json(_CONN_FILE, {})
    conn_mon   = ConnectivityMonitor(
        router_host  = _conn_cfg.get('router_host',   'https://192.168.1.1'),
        router_user  = _conn_cfg.get('router_user',   'admin'),
        router_pass  = _conn_cfg.get('router_pass',   ''),
        starlink_host= _conn_cfg.get('starlink_host', '192.168.100.1:9200'),
    )
else:
    conn_mon = None
    log.warning('connectivity.json nicht gefunden — Connectivity-Monitor deaktiviert')

_MONDAY_FILE = BASE_DIR / 'monday.json'
if _MONDAY_FILE.exists():
    _monday_cfg = read_json(_MONDAY_FILE, {})
else:
    _monday_cfg = {}
    log.warning('monday.json nicht gefunden — Monday-Integration deaktiviert')

def _apply_presets_config():
    """Übernimmt die Batterie-Instanzen aus presets.json in den CAN-Reader.

    Fehlt die Datei oder ist sie beschädigt, laufen wir mit den Vorgabewerten
    weiter — ein kaputtes presets.json darf den Dienst nicht am Start hindern.
    """
    data = read_json(PRESETS_FILE, {}) or {}
    batt = data.get('batteries') or {}
    try:
        service = int(batt.get('service_instance', 0))
        starter = int(batt.get('starter_instance', 1))
    except (TypeError, ValueError):
        log.warning('presets.json: unbrauchbare Batterie-Instanzen — nutze 0/1')
        service, starter = 0, 1
    can_if.set_battery_instances(service=service, starter=starter)

_apply_presets_config()

ws_clients: set['_WsClient'] = set()
history: deque[dict] = deque(maxlen=10800)   # 10800 × 5 s ≈ 15 h

# Zweiter, GROBER Verlauf: Minutenmittel statt 5-Sekunden-Werte.
#
# Der feine Puffer deckt rund 15 Stunden ab. Die Zeitknoepfe bieten aber
# 24 Stunden und 7 Tage an — bei 7 Tagen waren damit neun Zehntel des
# Diagramms leer. Sieben Tage in 5-Sekunden-Aufloesung waeren 120.960
# Eintraege und auf einem Pi Zero nicht vertretbar; als Minutenmittel sind es
# 10.080 — also genau so viele wie der bestehende Puffer und damit derselbe
# Speicherbedarf.
history_grob: deque[dict] = deque(maxlen=10080)   # 10080 × 60 s = 7 Tage
_hist_last_ts: float = 0.0
# Die 5-s-Drossel MUSS auf der monotonen Uhr laufen. Der Pi hat keine RTC:
# steht die Wanduhr nach einem Stromausfall hinter dem letzten gespeicherten
# Zeitstempel, wuerde eine Wanduhr-Drossel gar nichts mehr aufzeichnen, bis
# NTP aufgeholt hat. Der Wanduhr-Wert bleibt nur der Anzeige-Zeitstempel.
_hist_last_mono: float = 0.0

# Der Verlauf überlebt jetzt den Neustart: NDJSON-Datei, gepuffert im eigenen
# Thread geschrieben (SD-Karte), beim Start wird das Zeitfenster nachgeladen.
# Heizung: der Pi fragt den Stoker-Hub zentral ab und haelt den Zustand vor.
# Die Geraetedoku begrenzt auf 1 Hz und vier WebSockets im GANZEN Netz —
# jedes Handy einzeln pollen zu lassen waere schnell darueber.
heizung = StokerClient(HEIZUNG_FILE)

hist_store = HistoryStore(HISTORY_FILE, retention_s=16 * 3600,
                          max_entries=history.maxlen or 10800)

# Der grobe Verlauf haelt sieben Tage vor. Eine Zeile je Minute sind 1440 am
# Tag, gut 10.000 in der Woche und rund 1,5 MB — verglichen mit dem feinen
# Verlauf (17.280 Zeilen am Tag) ist das fuer die SD-Karte nichts.
grob_store = HistoryStore(HISTORY_GROB_FILE, retention_s=7 * 86400,
                          max_entries=history_grob.maxlen or 10080,
                          rotate_age_s=8 * 86400)


_REG_DEVICE_MODE = 0x0200   # DeviceMode: 0 = aus, 1 = ein


def _apply_charger_setpoints(setpoints: list):
    """Sendet Spannung + DeviceMode an alle aktivierten Ladegeräte.
    Im Hafen-Modus Halten (on=False) → DeviceMode=0 zuerst senden, keine Spannungsänderung.
    Im Hafen-Modus Laden (on=True) → DeviceMode=1 dann Spannungs-Setpoints.
    Vollladung/Balance (kein 'on' Feld) → nur Spannungen.
    """
    for dev in setpoints:
        inst = dev['instance']
        on_flag = dev.get('on')   # None = kein Toggle (Vollladung/Balance), True/False = Hafen
        if on_flag is False:
            # Lader ausschalten (SOC ≥ Ziel im Hafen-Modus); DeviceMode ist un8 → size=1
            can_if.send_charger_register(_REG_DEVICE_MODE, 0, inst, size=1)
            log.info("Lader aus → Inst %d (%s)", inst, dev['label'])
        else:
            # Einschalten: immer bei True (Hafen laden) und bei None (Vollladung/Balance),
            # da der Lader nach einem Hafen-Halt evtl. noch aus sein könnte
            can_if.send_charger_register(_REG_DEVICE_MODE, 1, inst, size=1)
            can_if.send_charger_setpoints(dev['absorption_v'], dev['float_v'], inst)
            log.info("Lader %s Inst %d: %.2f/%.2f V (on=%s)",
                     dev['label'], inst, dev['absorption_v'], dev['float_v'], on_flag)


# ── Minutenmittel fuer den groben Verlauf ─────────────────────────────────
# Gemittelt wird ueber die 5-Sekunden-Eintraege einer Minute. Ein Mittelwert
# statt einer Stichprobe, damit kurze Spitzen (Anlaufstrom) die Wochenansicht
# nicht verfaelschen, aber auch nicht spurlos verschwinden.
_grob_eimer: dict = {}
_grob_minute: int = -1


def _grob_sammeln(entry: dict) -> None:
    """Sammelt einen Feinwert; bei Minutenwechsel wandert das Mittel in den
    groben Verlauf. Rein rechnerisch, kein Datei- oder Netzzugriff."""
    global _grob_eimer, _grob_minute
    ts = entry.get('ts')
    if ts is None:
        return
    minute = int(ts // 60)
    if _grob_minute < 0:
        _grob_minute = minute
    if minute != _grob_minute:
        # Zeitstempel in die Mitte der Minute, wie es _decimate_history auch
        # fuer seine Buckets macht.
        mittel = {'ts': _grob_minute * 60 + 30}
        for k, (summe, anzahl) in _grob_eimer.items():
            if anzahl:
                mittel[k] = round(summe / anzahl, 4)
        if len(mittel) > 1:
            history_grob.append(mittel)
            grob_store.append(mittel)
        _grob_eimer = {}
        _grob_minute = minute
    for k, v in entry.items():
        if k == 'ts' or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        eintrag = _grob_eimer.setdefault(k, [0.0, 0])
        eintrag[0] += v
        eintrag[1] += 1


async def broadcast(data: dict):
    global _hist_last_ts, _hist_last_mono
    # Die Heizung haengt an einem eigenen Poller und war deshalb bisher gar
    # nicht Teil der Alarmpruefung — Regeln auf heizung.* konnten nie greifen.
    check_data = {**data,
                  '_network_age': can_if.time_since_last_message(),
                  'heizung': heizung.snapshot()}
    alarms.check(check_data)
    # Hafen-SOC-Regelung: bei Zustandswechsel sofort neue Setpoints senden
    soc = data.get('battery', {}).get('soc')
    if charge_ctrl.update_soc(soc):
        _apply_charger_setpoints(charge_ctrl.device_setpoints())
    batt = data.get('battery', {})
    now = time.time()
    entry: dict = {'ts': now}
    for key in ('soc', 'voltage', 'current'):
        v = batt.get(key)
        if v is not None:
            entry[key] = v
    # solar2/solar3/wind sind VORBEREITUNG für Hardware, die noch nicht verbaut
    # ist. Sie kosten nichts: fehlt die Quelle im State, liefert .get() None und
    # es wird nichts geschrieben. NICHT entfernen — sie gehören zum Ausbauplan.
    for src_key, field in (('solar', 'solar1'), ('alternator', 'alternator'),
                            ('solar2', 'solar2'), ('solar3', 'solar3'),
                            ('charger', 'charger'), ('wind', 'wind')):
        p = data.get(src_key, {}).get('power')
        if p is not None:
            entry[field] = p
    # Tankstaende gehoeren in den Verlauf: sie aendern sich langsam, und genau
    # deshalb ist die 24-Stunden-Kurve aussagekraeftig (Verbrauch je Tag).
    # Vorher lag im Verlauf ausschliesslich die Batterie.
    tanks = data.get('tanks') or {}
    for tk in ('tank1', 'tank2'):
        v = tanks.get(tk)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            entry[tk] = v
    bms = data.get('bms', {})
    for bms_key in ('current_charge', 'current_discharge'):
        v = bms.get(bms_key)
        if v is not None:
            entry[bms_key] = v
    # Zelldifferenz gehoert in den Server-Verlauf, nicht nur in den Browser.
    # Vorher rechnete sie ausschliesslich charts.js aus den Live-Daten — die
    # aus der Datei geladenen aelteren Punkte hatten das Feld deshalb nie und
    # der Anfang der Kurve fehlte, waehrend alle anderen Serien vollstaendig
    # waren.
    hoch, tief = bms.get('highest_cell_v'), bms.get('lowest_cell_v')
    if hoch is not None and tief is not None:
        entry['zelldiff'] = round(hoch - tief, 4)
    mono = time.monotonic()
    if len(entry) > 1 and mono - _hist_last_mono >= 5.0:
        history.append(entry)
        hist_store.append(entry)      # gepuffert, eigener Thread — blockiert nicht
        _grob_sammeln(entry)          # zusaetzlich ins Minutenmittel
        _hist_last_ts  = now
        _hist_last_mono = mono
    payload = {**data, 'alarms': alarms.get_alarms(), 'unack_alarms': alarms.unack_count, 'version': VERSION}
    # Nur einreihen — der Sender-Task jedes Clients schickt selbststaendig.
    # Kein await auf einen einzelnen Client mehr, also auch kein Einfrieren.
    for client in list(ws_clients):
        client.send(payload)


can_if.on_change(broadcast)



async def _charger_poll_loop():
    """Fragt alle 5 Minuten die aktuellen Setpoints vom IP43 ab (via Teensy PGN 130914)."""
    await asyncio.sleep(30)   # erster Poll nach 30 s (Teensy braucht Zeit zum Hochfahren)
    while True:
        can_if.send_charger_config_request()
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_event_loop()
    can_if.set_loop(loop)
    t = threading.Thread(target=can_if.run, daemon=True, name='can-reader')
    t.start()
    log.info("CAN-Reader gestartet")
    if conn_mon:
        conn_mon.start()
        log.info("Connectivity-Monitor gestartet")
    threading.Thread(target=_remote_version_loop, daemon=True, name='version-check').start()
    asyncio.create_task(_charger_poll_loop())

    # Verlauf von der Platte in die Deque zurückholen, damit die Graphen nach
    # einem Neustart nicht bei null anfangen. Das Lesen läuft im Executor —
    # sonst steht der Server beim Hochfahren mehrere Sekunden.
    try:
        geladen = await loop.run_in_executor(None, hist_store.load)
        history.extend(geladen)
        # _hist_last_mono bewusst NICHT vorbelegen: die Drossel laeuft auf der
        # monotonen Uhr, die bei jedem Start bei 0 beginnt. Aufzeichnung soll
        # sofort wieder anlaufen, unabhaengig davon, was in der Datei steht.
        log.info("Verlauf geladen: %d Einträge", len(geladen))
    except Exception as e:
        log.warning("Verlauf konnte nicht geladen werden: %s", e)
    # Der grobe Verlauf ebenso — ohne ihn braeuchte die Wochenansicht nach
    # jedem Neustart sieben Tage, bis sie wieder etwas zeigt.
    try:
        grob = await loop.run_in_executor(None, grob_store.load)
        history_grob.extend(grob)
        log.info("Grober Verlauf geladen: %d Einträge", len(grob))
    except Exception as e:
        log.warning("Grober Verlauf konnte nicht geladen werden: %s", e)
    hist_store.start()
    grob_store.start()
    heizung.start()
    log.info("Heizungs-Anbindung gestartet")

    yield
    can_if.stop()
    heizung.stop()
    hist_store.close()
    grob_store.close()
    log.info("CAN-Reader gestoppt")


class _NoCacheStatic(StaticFiles):
    """StaticFiles, das Revalidierung erzwingt (no-cache) — so erscheinen
    JS/CSS-Updates nach git pull sofort, liefern aber 304 wenn unverändert."""
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers['Cache-Control'] = 'no-cache'
        return resp


app = FastAPI(title='Mave Boat Monitor', lifespan=lifespan)
# compresslevel=6 statt der Vorgabe 9: auf ARMv6 deutlich billiger,
# die Antworten werden dabei nur wenige Prozent groesser.
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
app.mount('/static', _NoCacheStatic(directory=STATIC_DIR), name='static')

# JS files in dependency order — concatenated into one request on /js-bundle.js
_JS_FILES = [
    'icons.js',
    'core.js', 'battery.js', 'tanks.js', 'lights.js', 'charts.js',
    'alarms.js', 'settings.js', 'connectivity.js', 'ws.js', 'lightdetail.js',
    'wartung.js', 'stauplan.js', 'monday.js', 'flow.js', 'display.js',
    'waterlevel.js', 'weather.js', 'verlauf.js', 'heizung.js',
    'orte.js', 'topologie.js', 'geraete.js', 'init.js',
]
_js_bundle: dict = {'data': b'', 'etag': '', 'mtime': 0.0}


@app.get('/js-bundle.js', include_in_schema=False)
async def js_bundle(req: Request):
    js_dir = STATIC_DIR / 'js'
    latest = max((js_dir / f).stat().st_mtime for f in _JS_FILES if (js_dir / f).exists())
    if _js_bundle['mtime'] < latest:
        parts = []
        for f in _JS_FILES:
            p = js_dir / f
            if p.exists():
                parts.append(p.read_text(encoding='utf-8'))
        _js_bundle['data'] = ('\n;// ---\n'.join(parts)).encode()
        _js_bundle['mtime'] = latest
        _js_bundle['etag'] = f'"{int(latest)}"'
    if req.headers.get('if-none-match') == _js_bundle['etag']:
        return Response(status_code=304)
    return Response(
        content=_js_bundle['data'],
        media_type='application/javascript',
        headers={'Cache-Control': 'no-cache', 'ETag': _js_bundle['etag']},
    )


_index_cache: dict = {'data': b'', 'etag': '', 'mtime': 0.0}


@app.get('/', include_in_schema=False)
async def root(req: Request):
    """Startseite.

    Vorher mit no-store ausgeliefert: die ~100 KB gingen bei JEDEM Seitenaufbau
    neu über das Boots-WLAN und wurden jedes Mal neu gezippt. Jetzt no-cache mit
    ETag — der Browser fragt weiterhin jedes Mal nach (Updates kommen also
    sofort an), bekommt bei unveränderter Datei aber ein leeres 304 zurück.
    Gleiches Muster wie /js-bundle.js.
    """
    pfad = STATIC_DIR / 'index.html'
    mtime = pfad.stat().st_mtime
    if _index_cache['mtime'] < mtime:
        _index_cache['data']  = pfad.read_bytes()
        _index_cache['mtime'] = mtime
        _index_cache['etag']  = f'"{int(mtime)}-{len(_index_cache["data"])}"'
    if req.headers.get('if-none-match') == _index_cache['etag']:
        return Response(status_code=304)
    return Response(
        content=_index_cache['data'],
        media_type='text/html; charset=utf-8',
        headers={'Cache-Control': 'no-cache', 'ETag': _index_cache['etag']},
    )



class _WsClient:
    """Ein WebSocket-Client mit eigener kleiner Sende-Queue.

    Vorher sendete broadcast() sequenziell mit `await ws.send_json(...)` an jeden
    Client. Ist der TCP-Schreibpuffer eines schwach angebundenen Geraets voll
    (Handy am anderen Ende des Boots), blockiert dieses await — und weil der Pi
    Zero W genau EINEN Event-Loop hat, stand damit der komplette Server: keine
    weiteren Broadcasts, keine HTTP-Antworten, keine Alarmpruefung.

    Jetzt bekommt jeder Client eine Queue der Laenge 2 und einen eigenen
    Sender-Task. Laeuft die Queue voll, wird der AELTESTE Payload verworfen —
    bei Live-Telemetrie ist der neueste Wert der einzig interessante, und ein
    langsames Geraet darf die anderen nicht ausbremsen.
    """

    __slots__ = ('ws', 'queue', 'task', 'verworfen')

    def __init__(self, ws: WebSocket):
        self.ws        = ws
        self.queue     = asyncio.Queue(maxsize=2)
        self.task      = None
        self.verworfen = 0

    def start(self) -> None:
        self.task = asyncio.create_task(self._sender())

    def send(self, payload: dict) -> None:
        """Nimmt einen Payload entgegen, ohne je zu blockieren."""
        while True:
            try:
                self.queue.put_nowait(payload)
                return
            except asyncio.QueueFull:
                try:
                    self.queue.get_nowait()      # aeltesten wegwerfen
                    self.verworfen += 1
                except asyncio.QueueEmpty:
                    return

    async def _sender(self) -> None:
        try:
            while True:
                payload = await self.queue.get()
                await self.ws.send_json(payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("WebSocket Sendefehler: %s", e)
        finally:
            ws_clients.discard(self)

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.task


@app.websocket('/ws')
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    client = _WsClient(ws)
    client.start()
    ws_clients.add(client)
    log.info("WebSocket verbunden (%d aktiv)", len(ws_clients))
    try:
        client.send({**state.to_dict(), 'version': VERSION})
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                client.send({'ping': True})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.debug("WebSocket Fehler: %s", e)
    finally:
        ws_clients.discard(client)
        await client.stop()
        if client.verworfen:
            log.info("WebSocket getrennt (%d aktiv, %d Payloads verworfen)",
                     len(ws_clients), client.verworfen)
        else:
            log.info("WebSocket getrennt (%d aktiv)", len(ws_clients))


# ── Pruefung von Request-Bodys ───────────────────────────────────────────────
# Die Endpunkte nehmen rohe dicts entgegen und griffen frueher ungeprueft zu:
# `len(values)` auf einer Zahl, `int(v)` auf 'abc' — beides endete als HTTP 500
# ("Interner Fehler"), obwohl schlicht der Body falsch war. Diese Helfer machen
# daraus eine 400 mit einer Meldung, die sagt, was erwartet wird.

def _zahl(wert, lo: float, hi: float, name: str) -> float:
    """Prueft einen Zahlenwert aus dem Body und gibt ihn zurueck.

    true/false sind in Python zwar ints, aber hier nie gemeint. NaN und
    Unendlich muessen ebenfalls raus: json.loads laesst beide als Literal durch.
    """
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        raise HTTPException(400, detail=f'{name}: Zahl erwartet, '
                                        f'{type(wert).__name__} bekommen')
    if not (lo <= wert <= hi):
        raise HTTPException(400, detail=f'{name}: {wert} liegt ausserhalb von {lo} bis {hi}')
    return wert


def _text(wert, max_laenge: int, name: str) -> str:
    if not isinstance(wert, str):
        raise HTTPException(400, detail=f'{name}: Text erwartet, '
                                        f'{type(wert).__name__} bekommen')
    return wert[:max_laenge]


def _unbekannt_ablehnen(daten: dict, erlaubt, pfad: str) -> None:
    """Lehnt unbekannte Schluessel mit 400 ab.

    Bewusst ABLEHNEN und nicht stillschweigend verwerfen oder loeschen: was
    bereits in presets.json steht, bleibt unangetastet — der Aufrufer erfaehrt
    nur, dass sein Schluessel hier nicht hingehoert. Sonst waechst die Datei
    bei jedem Tippfehler um einen Eintrag, den nie wieder jemand entfernt.
    """
    fremd = sorted(str(k) for k in daten if k not in erlaubt)
    if fremd:
        raise HTTPException(400, detail=f'{pfad}: unbekannte Schluessel: {", ".join(fremd)}')


_LICHT_KANAELE = 9

def _kanalwerte(werte, name: str = 'values') -> list[int]:
    """Prueft eine Helligkeitsliste und gibt genau 9 ganze Werte 0..255 zurueck."""
    if not isinstance(werte, list):
        raise HTTPException(400, detail=f'{name}: Liste erwartet, '
                                        f'{type(werte).__name__} bekommen')
    if len(werte) != _LICHT_KANAELE:
        raise HTTPException(400, detail=f'{name}: {_LICHT_KANAELE} Werte erforderlich, '
                                        f'{len(werte)} bekommen')
    return [int(_zahl(v, 0, 255, f'{name}[{i}]')) for i, v in enumerate(werte)]


@app.get('/api/presets')
async def get_presets():
    return read_json(PRESETS_FILE, {})


@app.post('/api/lights/preset/{preset_id}')
async def apply_preset(preset_id: int):
    data    = read_json(PRESETS_FILE, {})
    presets = data.get('presets', [])
    if not (0 <= preset_id < len(presets)):
        raise HTTPException(404, detail='Preset nicht gefunden')
    preset = presets[preset_id]
    if preset.get('values') is None:
        raise HTTPException(400, detail='Preset nicht konfiguriert')
    # Auch die Werte aus der Datei pruefen: eine von Hand verkorkste
    # presets.json darf keinen 500er ausloesen, sondern eine klare Meldung.
    try:
        werte = _kanalwerte(preset['values'], f'Preset {preset_id}')
    except HTTPException:
        raise HTTPException(400, detail=f'Preset {preset_id} enthaelt unbrauchbare Werte')
    can_if.send_brightness(werte)
    log.info("Preset %d '%s' aktiviert", preset_id, preset.get('name', ''))
    return {'ok': True, 'preset': preset.get('name', '')}


@app.post('/api/lights/channels')
async def set_channels(body: dict):
    values = _kanalwerte(body.get('values'))
    can_if.send_brightness(values)
    state.lights['channels'] = values
    return {'ok': True}


@app.patch('/api/lights/preset/{preset_id}')
async def update_preset(preset_id: int, body: dict):
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Objekt erwartet')
    # 'icon' gehoert dazu: presetIcon() in lights.js liest p.icon zuerst und
    # faellt nur ersatzweise auf die Emoji-Zuordnung zurueck. Das Feld ist als
    # Nachfolger vorgesehen und darf hier nicht abgelehnt werden.
    _unbekannt_ablehnen(body, ('name', 'emoji', 'icon', 'values'), 'preset')
    data    = read_json(PRESETS_FILE, {})
    presets = data.get('presets', [])
    if not (0 <= preset_id < len(presets)):
        raise HTTPException(404, detail='Preset nicht gefunden')
    if 'name' in body:
        presets[preset_id]['name'] = _text(body['name'], 32, 'name')
    if 'emoji' in body:
        presets[preset_id]['emoji'] = _text(body['emoji'], 4, 'emoji')
    if 'icon' in body:
        presets[preset_id]['icon'] = _text(body['icon'], 32, 'icon')
    if 'values' in body:
        # Frueher wurde eine Liste mit falscher Laenge stillschweigend
        # verworfen — der Nutzer sah "Gespeichert", gespeichert war nichts.
        presets[preset_id]['values'] = _kanalwerte(body['values'])
    # Schreiben in den Thread-Pool: fsync auf der SD-Karte dauert auf dem Pi
    # Zero laenge genug, um im Event-Loop den ganzen Server anzuhalten.
    await _run_blocking(write_json, PRESETS_FILE, data)
    log.info("Preset %d aktualisiert: '%s'", preset_id, presets[preset_id].get('name', ''))
    return data


def _decimate_history(entries: list[dict], max_points: int) -> list[dict]:
    """Dünnt einen Verlauf auf höchstens max_points Punkte aus.

    Je Zeitfenster (Bucket) wird der Mittelwert jedes Feldes gebildet, der
    Zeitstempel ist die Bucket-Mitte. Felder, die in einem Bucket gar nicht
    vorkommen, fehlen auch im Ergebnis — so reißt keine Serie ab, nur weil
    der erste Eintrag eines Buckets sie zufällig nicht hatte.
    """
    n = len(entries)
    if max_points <= 0 or n <= max_points:
        return entries

    schritt = n / max_points
    out: list[dict] = []
    for i in range(max_points):
        a = int(i * schritt)
        b = int((i + 1) * schritt)
        if b <= a:
            b = a + 1
        bucket = entries[a:min(b, n)]
        if not bucket:
            continue
        summen: dict[str, float] = {}
        anzahl: dict[str, int] = {}
        for e in bucket:
            for k, v in e.items():
                if k == 'ts' or not isinstance(v, (int, float)):
                    continue
                summen[k] = summen.get(k, 0.0) + v
                anzahl[k] = anzahl.get(k, 0) + 1
        punkt: dict = {'ts': round((bucket[0]['ts'] + bucket[-1]['ts']) / 2, 1)}
        for k, sm in summen.items():
            punkt[k] = round(sm / anzahl[k], 3)
        out.append(punkt)
    return out


@app.get('/api/history')
async def get_history(range: int | None = None, max_points: int = 1500):
    """Verlauf für die Graphen.

    Vorher gab dieser Endpunkt stumpf alle 10.800 Einträge zurück. Ohne
    response_model schickt FastAPI das durch den jsonable_encoder und danach
    durch gzip Level 9 — alles synchron im einzigen Event-Loop. Am Gerät
    gemessen: 11,1 s bis zum ersten Byte, und in dieser Zeit steht der GESAMTE
    Server (auch /api/status brauchte dann 8,2 s statt 0,1 s).

    Jetzt: nach Zeitfenster filtern, serverseitig ausdünnen, am Encoder vorbei
    serialisieren und die Serialisierung bei großen Antworten in den Executor
    verlagern.

    server_now geht mit, weil der Pi keine Echtzeituhr hat: der Client rechnet
    daraus einen Offset und filtert gegen die Server-Zeit statt gegen die
    Uhr des Telefons.
    """
    jetzt = time.time()

    # Welcher Puffer? Der feine deckt rund 15 Stunden in 5-Sekunden-Schritten
    # ab, der grobe sieben Tage in Minutenmitteln. Fuer kurze Fenster ist der
    # feine besser, fuer lange deckt nur der grobe das ganze Fenster ab.
    # Entschieden wird nicht nach einer festen Grenze, sondern danach, wer
    # tatsaechlich weiter zurueckreicht — nach einem Neustart mit leerer
    # Wochendatei bleibt es so beim feinen Verlauf.
    def _reicht_bis(d):
        return jetzt - d[0].get('ts', jetzt) if d else 0.0

    if range and range > 6 * 3600 and _reicht_bis(history_grob) > _reicht_bis(history):
        eintraege = list(history_grob)
    else:
        eintraege = list(history)

    if range is not None and range > 0:
        grenze = jetzt - range
        eintraege = [e for e in eintraege if e.get('ts', 0) >= grenze]

    max_points = max(1, min(max_points, 5000))
    eintraege = _decimate_history(eintraege, max_points)

    payload = {'server_now': jetzt, 'entries': eintraege}

    def _dump() -> str:
        return json.dumps(payload, separators=(',', ':'))

    # Kleine Antworten direkt, große im Executor — der Schwellwert liegt so,
    # dass der Normalfall keinen Thread-Wechsel kostet.
    if len(eintraege) > 400:
        loop = asyncio.get_running_loop()
        body = await loop.run_in_executor(None, _dump)
    else:
        body = _dump()

    return Response(content=body, media_type='application/json')


@app.get('/api/status')
async def get_status():
    return state.to_dict()


@app.get('/api/daily-stats')
async def get_daily_stats(days: int = 7):
    # Der Tracker haelt nur DAILY_STATS_MAX_DAYS Tage vor — mehr anzufordern
    # lieferte vorher stillschweigend leere Tage.
    return can_if.get_daily_stats(max(1, min(days, DAILY_STATS_MAX_DAYS)))


@app.get('/api/network')
async def get_network():
    return can_if.get_network_stats()


# Hoechstens so viele VERSCHIEDENE Fehler im RAM-Log; Wiederholungen zaehlen
# im jeweiligen Eintrag hoch. Ein Fehler in einer Render-Schleife feuert sonst
# im Sekundentakt, schiebt binnen einer Minute jeden anderen Fehler aus der
# Liste und flutet nebenbei das Journal auf der SD-Karte.
_JS_ERRORS_MAX  = 50
# Obergrenze des Request-Bodys. 2 KB waren zu knapp: ein Chromium-Stack mit
# zehn Rahmen und langen /static/js/...-Adressen kommt leicht auf ueber 2 KB,
# und weil der Melder einen zu grossen Body verwirft, gingen ausgerechnet die
# Fehler mit dem brauchbarsten Stack spurlos verloren. Der gespeicherte Eintrag
# bleibt durch die Feldgrenzen unten trotzdem bei rund 1,7 KB.
_JS_ERROR_BYTES = 16 * 1024
_js_errors: list[dict] = []

def _js_error_key(eintrag: dict):
    """Kennzeichen eines Fehlers: gleiche Meldung an gleicher Stelle."""
    return (eintrag.get('msg'), eintrag.get('src'), eintrag.get('line'))

def _js_zeilennr(wert):
    """Zeilen-/Spaltennummer aus dem Body — alles Unbrauchbare wird zu None.

    Ohne diese Umwandlung landete hier alles, was das Feld enthielt (auch eine
    verschachtelte Struktur), unveraendert im RAM-Log.
    """
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    return int(wert) if math.isfinite(wert) else None

@app.post('/api/jserror')
async def post_jserror(request: Request):
    """Empfängt JS-Fehler vom Frontend und speichert sie im RAM-Log.

    Gleiche Meldungen werden zusammengefasst: der Eintrag bekommt 'count'
    sowie 'first_ts'/'last_ts'. Ins Log geht nur das erste Auftreten.
    """
    try:
        entry = await _json_body(request, _JS_ERROR_BYTES)
        if not isinstance(entry, dict):
            entry = {'msg': str(entry)[:500]}
        # Auf die Felder begrenzen, die core.js schickt (msg/src/line/col/
        # stack/ts) und jedes davon kappen: der Fehlermelder ist ungeschuetzt
        # erreichbar, das RAM-Log darf davon nicht beliebig gross werden.
        entry = {'msg':   str(entry.get('msg', '?'))[:500],
                 'src':   str(entry.get('src', '?'))[:200],
                 'line':  _js_zeilennr(entry.get('line')),
                 'col':   _js_zeilennr(entry.get('col')),
                 'stack': str(entry.get('stack', ''))[:1000] or None,
                 'ts':    str(entry.get('ts', ''))[:40] or None}
        jetzt    = time.time()
        kennung  = _js_error_key(entry)
        for bekannt in _js_errors:
            if _js_error_key(bekannt) == kennung:
                bekannt['count']   = bekannt.get('count', 1) + 1
                bekannt['last_ts'] = jetzt
                break
        else:
            entry.update(count=1, first_ts=jetzt, last_ts=jetzt)
            _js_errors.append(entry)
            if len(_js_errors) > _JS_ERRORS_MAX:
                _js_errors.pop(0)
            logging.error('JS-FEHLER: %s @ %s:%s',
                          entry['msg'], entry['src'], entry['line'])
    except Exception:
        pass
    return {'ok': True}

@app.get('/api/jserrors')
async def get_jserrors():
    """Gibt die gesammelten JS-Fehler zurück."""
    return _js_errors

@app.get('/api/debug/bms')
async def debug_bms():
    """Roh-Bytes der letzten BMS/VE.Direct-Frames + geparste Werte (Debug)."""
    from nmea2000 import parse_bms_pack, parse_bms_cells, parse_ve_direct_ext
    raw = can_if.get_raw_frames()
    out = {'raw': raw, 'parsed': {}}
    for pgn, parser in ((130901, parse_bms_pack), (130902, parse_bms_cells),
                        (130910, parse_ve_direct_ext)):
        if pgn in raw:
            try:
                out['parsed'][pgn] = parser(bytes.fromhex(raw[pgn]['hex']))
            except Exception as e:
                out['parsed'][pgn] = {'error': str(e)}
    out['state'] = {'bms': state.to_dict().get('bms'),
                    'inverter': state.to_dict().get('inverter'),
                    'charger': state.to_dict().get('charger'),
                    'orion': state.to_dict().get('orion')}
    return out


@app.get('/api/alarms')
async def get_alarms():
    return alarms.get_alarms()

@app.post('/api/alarms/ack-all')
async def ack_all_alarms():
    alarms.acknowledge_all()
    return {'ok': True}

@app.post('/api/alarms/{alarm_id}/ack')
async def ack_alarm(alarm_id: str):
    if not alarms.acknowledge(alarm_id):
        raise HTTPException(404)
    return {'ok': True}

@app.delete('/api/alarms/{alarm_id}')
async def delete_alarm(alarm_id: str):
    if not alarms.delete(alarm_id):
        raise HTTPException(404)
    return {'ok': True}

@app.get('/api/connectivity')
async def get_connectivity():
    if not conn_mon:
        raise HTTPException(503, detail='Connectivity-Monitor nicht konfiguriert')
    return conn_mon.get_status()


# Hier lag POST /api/connectivity/starlink/sleep. Auf Anweisung des Eigners
# entfernt: es geht nichts Steuerndes mehr an die Starlink-Antenne, weil der
# Verdacht besteht, dass die Befehle ihr schaden. Der Status wird weiter
# gelesen (connectivity.py, dish_get_status) und in /api/connectivity geliefert.


@app.get('/api/alarms/rules')
async def get_alarm_rules():
    return alarms.get_rules()

@app.patch('/api/alarms/rules')
async def update_alarm_rules(body: dict):
    return alarms.update_rules(body)


@app.post('/api/system/time-sync')
async def system_time_sync():
    can_if.send_time(time.time())
    return {'ok': True}


@app.get('/api/system/version')
async def system_version():
    return {
        'version':        VERSION,
        'git':            GIT_HASH,
        'remote_version': _remote_ver['version'],
        'remote_git':     _remote_ver['hash'],
        'up_to_date':     _remote_ver['up_to_date'],
    }


def _lauf(cmd: list[str], timeout: float):
    """subprocess.run mit festen Vorgaben — laeuft immer im Executor, nie im Loop."""
    return subprocess.run(cmd, cwd=BASE_DIR, capture_output=True,
                          text=True, timeout=timeout)


@app.post('/api/system/update')
async def system_update():
    loop = asyncio.get_running_loop()
    before = _git_hash()
    # git pull dauert ueber Mobilfunk gut und gerne 10-30 s. Synchron im
    # Event-Loop stand solange der komplette Server.
    result = await loop.run_in_executor(None, _lauf, ['git', 'pull'], 30)
    if result.returncode != 0:
        # stderr NICHT an den Aufrufer geben: die Remote-URL enthaelt das
        # GitHub-Token, und git schreibt sie bei Fehlern mit in die Meldung.
        log.error("git pull fehlgeschlagen: %s", result.stderr.strip())
        raise HTTPException(500, detail='Aktualisierung fehlgeschlagen — Details im Log.')
    changed = 'Already up to date.' not in result.stdout
    after = _git_hash()
    log.info("git pull: %s", result.stdout.strip())
    changelog = []
    if changed:
        try:
            cl = await loop.run_in_executor(
                None, _lauf,
                ['git', 'log', 'ORIG_HEAD..HEAD', '--no-merges',
                 '--pretty=format:ENTRY%n%s%n%b'], 10)
            for block in cl.stdout.split('ENTRY\n'):
                block = block.strip()
                if not block:
                    continue
                lines = block.splitlines()
                title = lines[0].strip()
                items = [l.strip() for l in lines[1:]
                         if l.strip() and not l.strip().startswith('Co-Authored')]
                if title:
                    changelog.append({'title': title, 'items': items})
        except Exception:
            pass
        asyncio.get_event_loop().call_later(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
    return {'ok': True, 'changed': changed, 'output': result.stdout.strip(),
            'version_before': before, 'version_after': after, 'changelog': changelog}


@app.get('/api/pgn/{pgn}/{src}')
async def get_pgn_detail(pgn: int, src: int, instance: int | None = None):
    from nmea2000 import parse_pgn_fields, PGN_NAMES
    frame = can_if.get_raw_frame(pgn, src, instance)
    if not frame:
        raise HTTPException(404, detail=f'Keine Daten für PGN {pgn} von Adresse {src}')
    payload = bytes.fromhex(frame['hex'])
    return {
        'pgn':    pgn,
        'src':    src,
        'name':   PGN_NAMES.get(pgn, f'PGN {pgn}'),
        'hex':    frame['hex'],
        'len':    frame['len'],
        'fields': parse_pgn_fields(pgn, payload),
    }


@app.post('/api/inverter/mode')
async def set_inverter_mode(body: dict):
    mode = body.get('mode')
    if mode not in (2, 4, 5):
        raise HTTPException(400, detail='mode muss 2 (An), 4 (Aus) oder 5 (Eco) sein')
    can_if.send_inverter_mode(int(mode))
    return {'ok': True, 'mode': mode}


@app.get('/api/monday/board')
async def get_monday_board():
    token    = _monday_cfg.get('api_token', '')
    board_id = str(_monday_cfg.get('board_id', ''))
    if not token or not board_id or token.startswith('DEIN_'):
        raise HTTPException(503, detail='Monday nicht konfiguriert')
    from monday import get_board
    try:
        return await get_board(token, board_id)
    except Exception as e:
        raise HTTPException(502, detail=str(e))


@app.post('/api/monday/item')
async def create_monday_item(body: dict):
    token    = _monday_cfg.get('api_token', '')
    board_id = str(_monday_cfg.get('board_id', ''))
    if not token or not board_id or token.startswith('DEIN_'):
        raise HTTPException(503, detail='Monday nicht konfiguriert')
    group_id = body.get('group_id', '').strip()
    name     = body.get('name',     '').strip()
    if not group_id or not name:
        raise HTTPException(400, detail='group_id und name erforderlich')
    column_values = body.get('column_values') or None
    from monday import create_item
    try:
        return await create_item(token, board_id, group_id, name, column_values)
    except Exception as e:
        raise HTTPException(502, detail=str(e))


@app.patch('/api/monday/item/{item_id}/status')
async def set_monday_status(item_id: str, body: dict):
    token    = _monday_cfg.get('api_token', '')
    board_id = str(_monday_cfg.get('board_id', ''))
    if not token or not board_id or token.startswith('DEIN_'):
        raise HTTPException(503, detail='Monday nicht konfiguriert')
    column_id = body.get('column_id', '')
    label     = body.get('label', '')
    if not column_id or not label:
        raise HTTPException(400, detail='column_id und label erforderlich')
    from monday import set_status
    try:
        await set_status(token, board_id, item_id, column_id, label)
        return {'ok': True}
    except Exception as e:
        raise HTTPException(502, detail=str(e))


@app.get('/api/debug/router-clients')
async def debug_router_clients():
    """Probiert RutOS-7-Pfade (enden auf /status) für verbundene Geräte."""
    if not conn_mon:
        raise HTTPException(503, detail='Connectivity-Monitor nicht konfiguriert')
    candidates = [
        '/api/dhcp/leases/ipv4/status',
        '/api/dhcp/leases/status',
        '/api/wireless/devices/status',
        '/api/wireless/interfaces/status',
        '/api/wireless/stations/status',
        '/api/hosts/status',
        '/api/network/clients/status',
    ]
    results = {}
    hdrs = conn_mon._token_headers()
    for path in candidates:
        try:
            results[path] = {'ok': True, 'data': conn_mon._http(conn_mon._router_host + path, headers=hdrs)}
        except Exception as e:
            results[path] = {'ok': False, 'error': str(e)}
    return results


# 1 MB reicht fuer Wartungsplan und Stauplan um Groessenordnungen. Ohne
# Obergrenze laege ein 50-MB-Body erst komplett im RAM eines Rechners mit
# 512 MB, bevor ihn ueberhaupt jemand ablehnen koennte.
_MAX_BODY_BYTES = 1024 * 1024

async def _json_body(request: Request, grenze: int = _MAX_BODY_BYTES):
    """Liest den Request-Body mit Obergrenze und gibt das geparste JSON zurueck.

    Der Body wird stueckweise gelesen und beim Ueberschreiten sofort mit 413
    abgebrochen, statt ihn erst ganz einzusammeln. Ungueltiges JSON ergibt 400
    statt eines ungefangenen JSONDecodeError (bisher HTTP 500).
    """
    zu_gross = HTTPException(413, detail=f'Daten zu gross (hoechstens '
                                         f'{grenze // 1024} KB)')
    angekuendigt = request.headers.get('content-length')
    if angekuendigt is not None:
        try:
            if int(angekuendigt) > grenze:
                raise zu_gross
        except ValueError:
            pass          # unbrauchbarer Header — die Stueckpruefung faengt es
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > grenze:
            raise zu_gross
    try:
        return json.loads(roh)
    except ValueError:
        raise HTTPException(400, detail='Ungueltiges JSON') from None


@app.get('/api/wartung')
async def get_wartung():
    return read_json(WARTUNG_FILE, [])

@app.put('/api/wartung')
async def save_wartung(request: Request):
    body = await _json_body(request)
    await _run_blocking(write_json, WARTUNG_FILE, body)
    return body


@app.get('/api/stauplan')
async def get_stauplan():
    return read_json(STAUPLAN_FILE, [])

@app.put('/api/stauplan')
async def save_stauplan(request: Request):
    body = await _json_body(request)
    await _run_blocking(write_json, STAUPLAN_FILE, body)
    return body


# Bekannte Schluessel je Abschnitt. Unbekannte werden mit 400 abgelehnt, damit
# presets.json nicht bei jedem Tippfehler um einen Eintrag waechst — geloescht
# wird dabei nichts, was schon in der Datei steht.
_SETTINGS_ABSCHNITTE = ('tanks', 'devices', 'batteries', 'wartung')
_TANK_FELDER         = ('name', 'capacity_l', 'color')
_BATTERIE_FELDER     = ('service_instance', 'starter_instance',
                        'primary_source', 'capacity_ah')

@app.patch('/api/settings')
async def update_settings(body: dict):
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Objekt erwartet')
    _unbekannt_ablehnen(body, _SETTINGS_ABSCHNITTE, 'settings')
    data = read_json(PRESETS_FILE, {})

    if 'tanks' in body:
        if not isinstance(body['tanks'], dict):
            raise HTTPException(400, detail='tanks: Objekt erwartet')
        # Bekannt ist, was der CAN-Zustand kennt (state.tanks) ODER was schon
        # in presets.json steht — Letzteres, damit ein bereits gepflegter
        # Eintrag nie abgelehnt wird. Ohne den ersten Teil scheiterte eine
        # frische Installation, die noch gar keine presets.json hat.
        erlaubte_tanks = set(state.tanks) | set(data.get('tanks') or {})
        for key, val in body['tanks'].items():
            if key not in erlaubte_tanks:
                raise HTTPException(400, detail=f'tanks: unbekannter Tank {key}')
            if not isinstance(val, dict):
                raise HTTPException(400, detail=f'tanks.{key}: Objekt erwartet')
            _unbekannt_ablehnen(val, _TANK_FELDER, f'tanks.{key}')
            geprueft = {}
            if 'name' in val:
                geprueft['name'] = _text(val['name'], 32, f'tanks.{key}.name')
            if 'capacity_l' in val:
                geprueft['capacity_l'] = _zahl(val['capacity_l'], 0, 100000,
                                               f'tanks.{key}.capacity_l')
            if 'color' in val:
                geprueft['color'] = _text(val['color'], 32, f'tanks.{key}.color')
            ziel = data.setdefault('tanks', {}).setdefault(key, {})
            if not isinstance(ziel, dict):
                raise HTTPException(400, detail=f'tanks.{key}: Eintrag in presets.json '
                                                f'ist kein Objekt')
            ziel.update(geprueft)

    if 'devices' in body:
        # Die Schluessel sind CAN-Quelladressen (0..255), keine feste Liste —
        # ein noch nicht verbautes Geraet bekommt hier spaeter einfach seine
        # Adresse. Begrenzt wird deshalb der Wertebereich, nicht die Auswahl.
        if not isinstance(body['devices'], dict):
            raise HTTPException(400, detail='devices: Objekt erwartet')
        geraete = {}
        for key, val in body['devices'].items():
            try:
                adresse = int(key)
            except (TypeError, ValueError):
                raise HTTPException(400, detail=f'devices: {key!r} ist keine CAN-Adresse') from None
            if not 0 <= adresse <= 255:
                raise HTTPException(400, detail=f'devices: Adresse {adresse} liegt '
                                                f'ausserhalb von 0 bis 255')
            geraete[str(adresse)] = _text(val, 64, f'devices.{key}')
        data.setdefault('devices', {}).update(geraete)

    if 'batteries' in body:
        if not isinstance(body['batteries'], dict):
            raise HTTPException(400, detail='batteries: Objekt erwartet')
        _unbekannt_ablehnen(body['batteries'], _BATTERIE_FELDER, 'batteries')
        b = dict(body['batteries'])
        for feld in ('service_instance', 'starter_instance'):
            if feld in b:
                b[feld] = int(_zahl(b[feld], 0, 255, f'batteries.{feld}'))
        if 'primary_source' in b:
            b['primary_source'] = _text(b['primary_source'], 32, 'batteries.primary_source')
        # leeres Eingabefeld = keine Kapazitaet hinterlegt; die Oberflaeche
        # schickt dafuer null, das MUSS durchgehen.
        if b.get('capacity_ah') is not None:
            b['capacity_ah'] = _zahl(b['capacity_ah'], 0, 100000, 'batteries.capacity_ah')
        data.setdefault('batteries', {}).update(b)

    if 'wartung' in body:
        w = body['wartung']
        if not isinstance(w, dict):
            raise HTTPException(400, detail='wartung: Objekt erwartet')
        _unbekannt_ablehnen(w, ('due_soon_days',), 'wartung')
        if 'due_soon_days' in w:
            tage = int(_zahl(w['due_soon_days'], 1, 14, 'wartung.due_soon_days'))
            data.setdefault('wartung', {})['due_soon_days'] = tage

    await _run_blocking(write_json, PRESETS_FILE, data)
    # ERST schreiben, DANN uebernehmen: _apply_presets_config() liest
    # presets.json neu ein. Vor dem Schreiben aufgerufen las es den alten Stand,
    # geaenderte Batterie-Instanzen wurden also bis zum Neustart ignoriert.
    if 'batteries' in body:
        _apply_presets_config()
    return data


# ── Ladesteuerung ────────────────────────────────────────────────────────────

@app.get('/api/charger')
async def get_charger():
    return charge_ctrl.status()


@app.post('/api/charger/mode')
async def set_charger_mode(body: dict):
    mode = body.get('mode', '')
    try:
        status = charge_ctrl.set_mode(mode)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    _apply_charger_setpoints(charge_ctrl.device_setpoints())
    return status


# Zulaessige Bereiche fuer Ladeparameter. Diese Werte gehen ueber NMEA 2000 an
# die Victron-Geraete — ein Zahlendreher hier laedt eine LiFePO4-Bank kaputt.
# Grenzen sind bewusst weit genug fuer 12-V- UND 24-V-Systeme.
_LADE_GRENZEN: dict[str, tuple[float, float]] = {
    'absorption_v':            (10.0, 60.0),
    'float_v':                 (10.0, 60.0),
    'hold_voltage':            (10.0, 60.0),
    'off_voltage':             ( 0.0, 60.0),
    'target_soc':              ( 0.0, 100.0),
    'soc_ramp_pct':            ( 0.0, 100.0),
    'soc_hysteresis_pct':      ( 0.0, 50.0),
    'balance_target_soc':      ( 0.0, 100.0),
    'balance_interval_days':   ( 1.0, 365.0),
    'balance_min_hours':       ( 0.0, 48.0),
    'balance_max_hours':       ( 0.1, 72.0),
    'balance_end_current_a':   ( 0.0, 200.0),
    'solar_priority_offset_v': ( 0.0, 5.0),
}


def _pruefe_ladewerte(patch: dict, pfad: str = '') -> None:
    """Prueft alle Zahlenwerte rekursiv gegen _LADE_GRENZEN.

    Vorher ging der Body ungeprueft durch einen deep_merge auf die Platte und
    von dort an die Ladegeraete. Ein Tippfehler (144 statt 14.4) waere
    unbemerkt bis in die Zellen durchgeschlagen.
    """
    for schluessel, wert in patch.items():
        voll = f'{pfad}.{schluessel}' if pfad else schluessel
        if isinstance(wert, dict):
            _pruefe_ladewerte(wert, voll)
            continue
        if schluessel not in _LADE_GRENZEN:
            continue
        if isinstance(wert, bool) or not isinstance(wert, (int, float)):
            raise HTTPException(400, detail=f'{voll}: Zahl erwartet, {type(wert).__name__} bekommen')
        lo, hi = _LADE_GRENZEN[schluessel]
        if not (lo <= wert <= hi):
            raise HTTPException(400, detail=f'{voll}: {wert} liegt ausserhalb von {lo} bis {hi}')


@app.patch('/api/charger/settings')
async def update_charger_settings(body: dict):
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Objekt erwartet')
    _pruefe_ladewerte(body)
    # Absorption darf nicht unter Float liegen — das ergibt kein sinnvolles Ladeprofil.
    for profil in ('harbor', 'full', 'balance'):
        p = body.get(profil)
        if isinstance(p, dict):
            a, f = p.get('absorption_v'), p.get('float_v')
            if isinstance(a, (int, float)) and isinstance(f, (int, float)) and a < f:
                raise HTTPException(400, detail=f'{profil}: absorption_v ({a}) darf nicht '
                                                f'unter float_v ({f}) liegen')
    return charge_ctrl.update_settings(body)


@app.post('/api/charger/poll')
async def poll_charger():
    """Sofortiger ISO-Request für PGN 130914 → liest aktuelle Setpoints vom IP43."""
    can_if.send_charger_config_request()
    return {'ok': True}


# ── Wasserstand Travemünde (pegelonline.wsv.de + BSH-Prognose) ───────────────

_wl_cache: dict  = {'data': None, 'ts': 0.0}
_bsh_cache: dict = {'data': None, 'ts': 0.0}

_WL_URL = ('https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations/'
           'TRAVEM%C3%BCNDE/W/measurements.json?start=P1DT0H&includeCurrentMeasurement=true')
_BSH_URL = 'https://www2.bsh.de/aktdat/wvd/ostsee/modellkurve/WVD_Travemuende.png'
_WL_PNP_M = -5.025          # Pegelnullpunkt Travemünde in m über NHN (Stand 2019-11-01)
_WL_ALARM_NHN_CM = -60      # Alarm wenn Prognose-Minimum unter diesem Wert


def _parse_bsh_forecast(img_bytes: bytes) -> dict | None:
    """Parst das BSH-Prognose-PNG und gibt den minimalen NHN-Prognosewert zurück."""
    try:
        import io
        import numpy as np
        from PIL import Image as PILImage
    except ImportError:
        log.debug("PIL/numpy fehlt — BSH-Prognose-Parsing nicht verfügbar")
        return None
    img = PILImage.open(io.BytesIO(img_bytes)).convert('RGB')
    arr = np.array(img)
    w, h = img.size
    # Horizontale Grid-Linien finden (grau, >300 solcher Pixel in chart-Breite)
    gm = ((arr[:,:,0] > 210) & (arr[:,:,0] < 235) &
          (arr[:,:,1] > 210) & (arr[:,:,1] < 235) &
          (arr[:,:,2] > 210) & (arr[:,:,2] < 235))
    raw_ys: list[int] = []
    for y in range(50, h - 50):
        if np.sum(gm[y, 200:w-30]) > 300:
            if not raw_ys or y - raw_ys[-1] > 5:
                raw_ys.append(y)
    if len(raw_ys) < 4:
        return None
    # Nur regelmäßig verteilte Linien behalten (Median-Abstand)
    sps = [raw_ys[i+1] - raw_ys[i] for i in range(len(raw_ys)-1)]
    med = sorted(sps)[len(sps)//2]
    if med < 30:
        return None
    grid_ys: list[int] = []
    for i, y in enumerate(raw_ys):
        if i < len(raw_ys) - 1 and abs(raw_ys[i+1] - y - med) / med < 0.3:
            grid_ys.append(y)
        elif i > 0 and abs(y - raw_ys[i-1] - med) / med < 0.3 and y not in grid_ys:
            grid_ys.append(y)
    if len(grid_ys) < 3:
        return None
    # Linien-Typ: negatives Label wenn linkster dunkler Pixel < x=45 (Minus-Zeichen)
    n_nonneg = 0
    for y_g in grid_ys:
        region = arr[max(0, y_g-18):y_g+18, 40:75, 0]
        x_min_local = next((x for x in range(region.shape[1])
                            if np.any(region[:, x] < 100)), None)
        if x_min_local is None or (40 + x_min_local) >= 45:
            n_nonneg += 1
    top_value = (n_nonneg - 1) * 10   # cm NHN an oberster Grid-Linie
    # Farbige Prognose-Pixel (gesättigte Farbe, nicht weiß/schwarz)
    sat = np.max(arr, axis=2).astype(int) - np.min(arr, axis=2).astype(int)
    colored = (sat > 40) & (arr[:,:,0] > 30) & (arr[:,:,0] < 250)
    ys_c = np.where(colored)[0]
    if len(ys_c) == 0:
        return None
    max_y_c = int(ys_c.max())
    min_nhn = round(top_value - (max_y_c - grid_ys[0]) * (10 / med))
    return {'min_nhn_cm': min_nhn}


def _fetch_bsh_forecast() -> dict | None:
    with urllib.request.urlopen(_BSH_URL, timeout=15) as r:
        return _parse_bsh_forecast(r.read())


def _fetch_waterlevel() -> dict:
    with urllib.request.urlopen(_WL_URL, timeout=10) as r:
        measurements = json.loads(r.read())
    if not measurements:
        return {}
    current = measurements[-1]['value']
    nhn_cm  = round(current / 100 * 100 + _WL_PNP_M * 100)   # cm über NHN
    now_ts  = time.time()
    past_v  = None
    for m in reversed(measurements):
        dt = datetime.fromisoformat(m['timestamp'])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.timestamp() <= now_ts - 1800:
            past_v = m['value']
            break
    delta  = round(current - past_v, 1) if past_v is not None else None
    trend  = ('rising' if delta and delta > 2 else
              'falling' if delta and delta < -2 else 'stable')
    step   = max(1, len(measurements) // 120)
    chart  = [{'ts': m['timestamp'], 'v': m['value']} for m in measurements[::step]]
    return {
        'current_cm':     current,
        'current_nhn_cm': nhn_cm,
        'trend':          trend,
        'delta_cm':       delta,
        'measurements':   chart,
        'forecast_img':   _BSH_URL,
    }


# ── Kurzverlaeufe fuer die Statusleiste ──────────────────────────────────────
# Die Felder der Statusleiste zeigen im Hintergrund den Verlauf der letzten
# 24 Stunden. Dafuer NICHT /api/history nehmen: das liefert bis zu 1500 Punkte
# mit allen Feldern und ist fuer einen fingernagelgrossen Graphen masslos.
# Hier kommen 60 Stuetzstellen je Reihe heraus — das genuegt fuer einen
# Streifen von rund 200 px Breite und kostet den Pi so gut wie nichts.
_SPARK_FENSTER_S = 86400
_SPARK_PUNKTE    = 60
_spark_cache: dict = {'ts': 0.0, 'data': None}


def _spark_bauen() -> dict:
    """Verlaufsreihen fuer die Statusleiste aus dem groben Puffer verdichten."""
    jetzt = time.time()
    von   = jetzt - _SPARK_FENSTER_S

    # Der grobe Puffer (Minutenmittel, 7 Tage) deckt 24 Stunden sicher ab. Nur
    # wenn er nach einem Neustart noch nicht weit genug zurueckreicht, hilft
    # der feine weiter.
    def _reicht(d):
        return jetzt - d[0].get('ts', jetzt) if d else 0.0
    quelle = history_grob if _reicht(history_grob) >= _reicht(history) else history
    punkte = [e for e in list(quelle) if e.get('ts', 0) >= von]

    # Feste Zeit-Eimer statt gleicher Punktzahl je Eimer: eine Luecke im
    # Verlauf (Pi war aus) soll als Luecke sichtbar bleiben und die Kurve nicht
    # stauchen.
    breite = _SPARK_FENSTER_S / _SPARK_PUNKTE
    eimer: list[dict] = [{} for _ in range(_SPARK_PUNKTE)]
    for e in punkte:
        i = int((e.get('ts', von) - von) / breite)
        if not 0 <= i < _SPARK_PUNKTE:
            continue
        for k, v in e.items():
            if k == 'ts' or not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            summe, anzahl = eimer[i].get(k, (0.0, 0))
            eimer[i][k] = (summe + v, anzahl + 1)

    def _reihe(feld: str) -> list:
        aus = []
        for b in eimer:
            summe, anzahl = b.get(feld, (0.0, 0))
            aus.append(round(summe / anzahl, 2) if anzahl else None)
        return aus

    def _ladeleistung() -> list:
        """Summe der Ladequellen, wie es die Statusleiste auch anzeigt."""
        teile = [_reihe(f) for f in ('charger', 'solar1', 'alternator')]
        aus = []
        for i in range(_SPARK_PUNKTE):
            werte = [t[i] for t in teile if t[i] is not None]
            aus.append(round(sum(werte), 1) if werte else None)
        return aus

    serien = {'soc': _reihe('soc'), 'laden': _ladeleistung(),
              'tank1': _reihe('tank1'), 'tank2': _reihe('tank2')}

    # Pegel kommt nicht aus unserem Verlauf, sondern fertig von pegelonline.
    # Nur aus dem Zwischenspeicher lesen — hier keinen Abruf ausloesen, sonst
    # haengt die Statusleiste an einem fremden Dienst.
    wl = _wl_cache.get('data') or {}
    messungen = wl.get('measurements') or []
    if messungen:
        pegel: list = [None] * _SPARK_PUNKTE
        eimer_p: list = [(0.0, 0)] * _SPARK_PUNKTE
        for m in messungen:
            try:
                # _fetch_waterlevel legt die Reihe als {'ts': ..., 'v': ...} ab,
                # NICHT als {'timestamp': ...} wie die Rohantwort von
                # pegelonline. Mit dem falschen Schluessel lief jeder Punkt in
                # den Ausnahmefall und die Reihe blieb still leer.
                dt = datetime.fromisoformat(m['ts'])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                i = int((dt.timestamp() - von) / breite)
            except Exception:
                continue
            if not 0 <= i < _SPARK_PUNKTE or not isinstance(m.get('v'), (int, float)):
                continue
            su, an = eimer_p[i]
            eimer_p[i] = (su + m['v'], an + 1)
        for i, (su, an) in enumerate(eimer_p):
            pegel[i] = round(su / an, 1) if an else None
        serien['pegel'] = pegel

    return {'von': round(von), 'bis': round(jetzt),
            'punkte': _SPARK_PUNKTE, 'serien': serien}


@app.get('/api/statusleiste/verlauf')
async def get_statusleiste_verlauf():
    """Kurzverlaeufe fuer die Hintergrundgraphen der Statusleiste.

    Eine Minute Zwischenspeicher: die Reihen sind Minutenmittel ueber 24
    Stunden, oefter neu zu rechnen aendert das Bild nicht und der Pi hat nur
    einen Kern. Mehrere offene Browser teilen sich damit eine Berechnung.
    """
    jetzt = time.time()
    if _spark_cache['data'] and jetzt - _spark_cache['ts'] < 60:
        return _spark_cache['data']
    daten = await asyncio.get_running_loop().run_in_executor(None, _spark_bauen)
    _spark_cache['data'] = daten
    _spark_cache['ts']   = jetzt
    return daten


@app.get('/api/waterlevel')
async def get_waterlevel():
    now = time.time()
    if _wl_cache['data'] and now - _wl_cache['ts'] < 300:
        return _wl_cache['data']
    loop = asyncio.get_event_loop()
    try:
        wl_data = await loop.run_in_executor(None, _fetch_waterlevel)
    except Exception as e:
        log.warning('Wasserstand-Fetch fehlgeschlagen: %s', e)
        if _wl_cache['data']:
            return _wl_cache['data']
        raise HTTPException(503, detail='Wasserstand nicht verfügbar')
    # BSH-Prognose: eigener Cache mit 30-Minuten TTL (Bild aktualisiert ~1x/h)
    bsh = _bsh_cache['data']
    if bsh is None or now - _bsh_cache['ts'] >= 1800:
        try:
            bsh = await loop.run_in_executor(None, _fetch_bsh_forecast)
            _bsh_cache['data'] = bsh
            _bsh_cache['ts']   = now
        except Exception as e:
            log.warning('BSH-Prognose-Fetch fehlgeschlagen: %s', e)
    if isinstance(bsh, dict) and 'min_nhn_cm' in bsh:
        wl_data['forecast_min_nhn_cm'] = bsh['min_nhn_cm']
        wl_data['forecast_alarm']      = bsh['min_nhn_cm'] < _WL_ALARM_NHN_CM
    _wl_cache['data'] = wl_data
    _wl_cache['ts']   = now
    return wl_data


# ── Display-Steuerung (fester Kiosk-Bildschirm) ──────────────────────────────
# Vorbereitet für einen später angeschlossenen Display. Power über `vcgencmd
# display_power`, Helligkeit über /sys/class/backlight (falls vorhanden).
# Der State-Endpoint meldet ehrlich, was die Hardware/Rechte erlauben — die UI
# deaktiviert Regler, die (noch) nicht funktionieren. Kein Crash ohne Display.
from glob import glob as _glob


def _backlight_dir():
    dirs = sorted(_glob('/sys/class/backlight/*'))
    return dirs[0] if dirs else None


def _display_state() -> dict:
    bl = _backlight_dir()
    brightness, bright_avail = None, False
    if bl:
        try:
            cur = int((Path(bl) / 'brightness').read_text().strip())
            mx  = int((Path(bl) / 'max_brightness').read_text().strip())
            brightness   = round(cur / mx * 100) if mx else None
            bright_avail = os.access(str(Path(bl) / 'brightness'), os.W_OK)
        except Exception:
            pass
    power, power_avail = None, False
    try:
        r = subprocess.run(['vcgencmd', 'display_power', '-1'],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and '=' in r.stdout:
            power       = r.stdout.strip().split('=')[-1] == '1'
            power_avail = True
    except Exception:
        pass
    return {'power': power, 'power_available': power_avail,
            'brightness': brightness, 'brightness_available': bright_avail}


@app.get('/api/display/state')
async def display_state():
    return _display_state()


@app.post('/api/display/power')
async def display_power(body: dict):
    on = bool(body.get('on', True))
    try:
        # vcgencmd braucht bis zu 5 s — synchron wuerde das den ganzen Server anhalten.
        r = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(['vcgencmd', 'display_power', '1' if on else '0'],
                                   capture_output=True, text=True, timeout=5))
        if r.returncode != 0:
            raise HTTPException(503, detail=f'Display-Steuerung nicht verfügbar: '
                                            f'{(r.stderr or r.stdout).strip()}')
    except FileNotFoundError:
        raise HTTPException(503, detail='vcgencmd nicht gefunden')
    return {'ok': True, 'power': on}


@app.post('/api/display/brightness')
async def display_brightness(body: dict):
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Objekt erwartet')
    # frueher: int('abc') → ValueError → HTTP 500 statt einer klaren 400
    val = int(_zahl(body.get('value', 50), 0, 100, 'value'))
    bl  = _backlight_dir()
    if not bl:
        raise HTTPException(503, detail='Kein Backlight-Gerät (Display ohne Helligkeitssteuerung)')
    try:
        mx  = int((Path(bl) / 'max_brightness').read_text().strip())
        raw = max(1, round(val / 100 * mx))   # nie ganz aus über die Helligkeit
        (Path(bl) / 'brightness').write_text(str(raw))
    except PermissionError:
        raise HTTPException(503, detail='Keine Schreibrechte auf Backlight (udev-Regel nötig)')
    except Exception as e:
        raise HTTPException(503, detail=f'Helligkeit fehlgeschlagen: {e}')
    return {'ok': True, 'brightness': val}


# ── Wetter (Lübeck + Lübecker Bucht, 3-Tage-Trend) ───────────────────────────
# Quelle: Open-Meteo (kostenlos, kein API-Key). DMI wäre möglich, braucht aber
# eine Schlüssel-Registrierung — daher Open-Meteo. Struktur ist quellen-agnostisch,
# ein späterer Wechsel auf DMI betrifft nur _fetch_weather().
_WX_LAND = (53.87, 10.69)   # Lübeck (Stadt)
_WX_SEA  = (54.10, 11.00)   # Lübecker Bucht (außen)
_WX_STORM_CODES = {95, 96, 99}
_wx_cache = {'data': None, 'ts': 0.0}


def _http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'mave-boatui'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _fetch_weather() -> dict:
    base = 'https://api.open-meteo.com/v1/forecast'
    land = _http_json(f'{base}?latitude={_WX_LAND[0]}&longitude={_WX_LAND[1]}'
                      '&daily=weathercode,temperature_2m_max,temperature_2m_min,'
                      'precipitation_sum,precipitation_probability_max'
                      '&timezone=Europe%2FBerlin&forecast_days=3')
    sea = _http_json(f'{base}?latitude={_WX_SEA[0]}&longitude={_WX_SEA[1]}'
                     '&daily=weathercode,windspeed_10m_max,windgusts_10m_max,'
                     'winddirection_10m_dominant'
                     '&timezone=Europe%2FBerlin&forecast_days=3&windspeed_unit=kn')
    try:
        marine = _http_json('https://marine-api.open-meteo.com/v1/marine'
                            f'?latitude={_WX_SEA[0]}&longitude={_WX_SEA[1]}'
                            '&daily=wave_height_max,wave_direction_dominant'
                            '&timezone=Europe%2FBerlin&forecast_days=3')
    except Exception:
        marine = {}

    ld, sd, md = land.get('daily', {}), sea.get('daily', {}), marine.get('daily', {})
    dates = ld.get('time', [])

    def g(d, k, i):
        a = d.get(k)
        return a[i] if isinstance(a, list) and i < len(a) else None

    land_days, sea_days = [], []
    for i, date in enumerate(dates):
        wmo = g(ld, 'weathercode', i)
        land_days.append({
            'date': date, 'wmo': wmo,
            'tmax': g(ld, 'temperature_2m_max', i), 'tmin': g(ld, 'temperature_2m_min', i),
            'precip': g(ld, 'precipitation_sum', i), 'pop': g(ld, 'precipitation_probability_max', i),
            'storm': wmo in _WX_STORM_CODES,
        })
        swmo = g(sd, 'weathercode', i)
        sea_days.append({
            'date': date,
            'wind': g(sd, 'windspeed_10m_max', i), 'gust': g(sd, 'windgusts_10m_max', i),
            'dir': g(sd, 'winddirection_10m_dominant', i), 'wave': g(md, 'wave_height_max', i),
            'storm': swmo in _WX_STORM_CODES,
        })
    return {'updated': time.time(), 'source': 'open-meteo',
            'land': {'name': 'Lübeck', 'days': land_days},
            'sea':  {'name': 'Lübecker Bucht', 'days': sea_days}}


@app.get('/api/weather')
async def get_weather():
    now = time.time()
    if _wx_cache['data'] and now - _wx_cache['ts'] < 1800:
        return _wx_cache['data']
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, _fetch_weather)
    except Exception as e:
        log.warning('Wetter-Fetch fehlgeschlagen: %s', e)
        if _wx_cache['data']:
            return _wx_cache['data']
        raise HTTPException(503, detail='Wetter nicht verfügbar')
    _wx_cache['data'] = data
    _wx_cache['ts'] = now
    return data


# ── Heizung (Stoker) ────────────────────────────────────────────────────────
# Der Pi ist der einzige, der mit dem Hub spricht. Lesen kommt aus dem
# Zwischenspeicher des Pollers, Schreiben wird durchgereicht — die Antwort der
# Heizung IST bereits der neue Zustand, ein zweiter Abruf entfaellt.


async def _heiz_ruf(fn, *args):
    """Schaltbefehl im Executor — urllib blockiert sonst den Event-Loop."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, fn, *args)
    except StokerFehler as e:
        raise HTTPException(e.status, detail=e.as_dict()['error']) from None


@app.get('/api/heizung')
async def heizung_state():
    """Zustand der Heizung. Antwortet immer, auch wenn das Gerät fehlt."""
    return heizung.snapshot()


@app.get('/api/heizung/settings')
async def heizung_settings():
    return heizung.settings()


@app.patch('/api/heizung/settings')
async def heizung_settings_patch(body: dict):
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Objekt erwartet')
    return heizung.update_settings(body)


@app.post('/api/heizung/probe')
async def heizung_probe(body: dict | None = None):
    """Prüft eine Adresse per GET /api/info — für den Einstellungsdialog."""
    host = (body or {}).get('host')
    info = await _heiz_ruf(heizung.probe, host)
    # Die Doku sagt: ist role nicht hub, hat man einen Raumknoten erwischt.
    if info.get('role') != 'hub':
        raise HTTPException(503, detail={
            'code': 'wrong_role',
            'message': f"Das ist kein Heizungsknoten (role: {info.get('role')})."})
    return info


@app.post('/api/heizung/room/{room_id}')
async def heizung_room(room_id: int, body: dict):
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Objekt erwartet')
    return await _heiz_ruf(heizung.set_room, room_id, body)


@app.post('/api/heizung/heater')
async def heizung_heater(body: dict):
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Objekt erwartet')
    return await _heiz_ruf(heizung.set_heater, body)


@app.post('/api/heizung/preset/{index}')
async def heizung_preset(index: str):
    """index ist 0..3 oder 'none'. Abwählen ist ausdrücklich 'none', nicht 4."""
    return await _heiz_ruf(heizung.set_preset, index)


# ── Geraeteuebersicht ───────────────────────────────────────────────────────
# Zwei Haelften: die gepflegte Liste in devices.json und die Quellen, die
# ohnehin laufen. Zusammengesetzt wird in geraete.py — hier steht nur die
# Verdrahtung.

# Die Registry aendert sich selten, wird aber bei jedem Aufruf der Seite
# gebraucht. Ohne diesen Zwischenspeicher laege bei jedem Abruf ein Lesezugriff
# auf der SD-Karte — auf dem Pi Zero teurer, als es aussieht.
_devices_cache: dict = {'reg_mtime': 0.0, 'registry': [],
                        'pre_mtime': 0.0, 'namen': {}}


def _devices_lesen() -> tuple[list, dict]:
    """Registry und die Namensliste aus presets.json — nur bei Aenderung neu.

    Laeuft im Thread-Pool (Dateizugriff), nie direkt im Event-Loop.
    """
    # devices.json ist Laufzeitdatei: sie wird ueber die Oberflaeche gepflegt und
    # gehoert deshalb NICHT ins Repo (sonst scheitert am Pi jeder `git pull`).
    # Damit ein frisches Geraet trotzdem nicht mit leerer Liste dasteht, wird
    # sie beim ersten Mal aus der mitgelieferten Vorlage angelegt. Danach fasst
    # sie niemand mehr an — eine vorhandene Datei wird nie ueberschrieben.
    if not DEVICES_FILE.exists() and DEVICES_VORLAGE.exists():
        vorlage = read_json(DEVICES_VORLAGE, [])
        if vorlage:
            write_json(DEVICES_FILE, vorlage)
            log.info("devices.json aus Vorlage angelegt: %d Geräte", len(vorlage))
    for datei, schluessel_zeit, schluessel_wert, vorgabe in (
            (DEVICES_FILE, 'reg_mtime', 'registry', []),
            (PRESETS_FILE, 'pre_mtime', 'namen', {})):
        try:
            mtime = datei.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != _devices_cache[schluessel_zeit]:
            daten = read_json(datei, vorgabe)
            if datei is PRESETS_FILE:
                daten = (daten or {}).get('devices') or {}
            _devices_cache[schluessel_wert] = daten if daten is not None else vorgabe
            _devices_cache[schluessel_zeit] = mtime
    return _devices_cache['registry'], _devices_cache['namen']


@app.get('/api/devices')
async def get_devices():
    """Ein Aufruf, eine fertige Geraeteliste samt Zustand."""
    registry, namen = await _run_blocking(_devices_lesen)
    return geraete.aggregiere(
        registry,
        netzwerk=can_if.get_network_stats(),
        presets_devices=namen,
        stoker_snapshot=heizung.snapshot(),
        conn_status=conn_mon.get_status() if conn_mon else None,
        eigener_host=socket.gethostname(),
    )


@app.get('/api/devices/registry')
async def get_devices_registry():
    return await _run_blocking(read_json, DEVICES_FILE, [])


@app.put('/api/devices/registry')
async def save_devices_registry(request: Request):
    body = await _json_body(request)
    try:
        sauber = geraete.pruefe_registry(body)
    except geraete.RegistryFehler as e:
        # Der Text ist fuer den Bediener geschrieben und geht wortwoertlich raus.
        raise HTTPException(400, detail=str(e)) from None
    await _run_blocking(write_json, DEVICES_FILE, sauber)
    _devices_cache['reg_mtime'] = 0.0        # Zwischenspeicher verwerfen
    return sauber
