"""Mave Boat Monitor — FastAPI Backend."""
import asyncio
import functools
import json
import gzip
import logging
import math
import os
import re
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import zlib
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from alarm_engine import AlarmEngine
from can_reader import BoatState, CanInterface
from charge_control import ChargeController
from connectivity import ConnectivityMonitor
from daily_stats import _MAX_DAYS as DAILY_STATS_MAX_DAYS   # Aufbewahrung im Tracker
import geraete
from debug_log import DebugLog, RingHandler
import konten_speicher
from konten_speicher import Konten
import sync_client
from sync import zugang as zg
from sync import rechte as rechte_modul
from heating import StokerClient, StokerFehler
from history_store import HistoryStore
from jsonio import read_json, write_json

# Nur Tags, die auch eine Fassung BEZEICHNEN. Im Verzeichnis stehen daneben
# Sicherungsmarken wie `geraeteseite-vor-umbau` — Ruecksprungpunkte vor einem
# groesseren Umbau, und die gehoeren dorthin. `git describe` nimmt aber ohne
# Filter den zuletzt gesetzten Tag, ganz gleich wie er heisst: aus v1.60.0 plus
# vier Aenderungen wurde so ploetzlich ".4", weil in
# `geraeteseite-vor-umbau` kein Punkt steht, von dem sich eine Hauptnummer
# abtrennen liesse. Der Filter loest das an der Wurzel, statt Marken zu
# verbieten, die ihren Zweck haben.
_FASSUNGS_TAGS = 'v[0-9]*'


def _git_semver(ref: str = 'HEAD') -> str:
    """Returns semver tag (e.g. '1.5.3') if the ref is exactly on a tag, else ''."""
    try:
        r = subprocess.run(
            ['git', 'describe', '--tags', '--exact-match', '--match', _FASSUNGS_TAGS, ref],
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

def _fassung(ref: str = 'HEAD', notfalls: str = '1.60.0') -> str:
    """Die laufende Fassung eines Standes, fortlaufend gezaehlt.

    Steht der Stand genau auf einem Fassungs-Tag, gilt der Tag. Sonst wird ab
    dem letzten weitergezaehlt: 1.60.0 + 42 Aenderungen = 1.60.42. Vorher hiess
    alles nach einem Tag gleich, und "welche Fassung laeuft da eigentlich" war
    nicht zu beantworten.

    Mit `ref` laesst sich dieselbe Rechnung auf die Gegenstelle anwenden. Das
    ist der Grund fuer den Parameter: die Fernfassung wurde bisher aus dem
    QUELLTEXT der Gegenstelle gelesen, mit einem Muster fuer die alte Schreibart
    `VERSION = _git_semver() or '1.59.0'`. Seit die Fassung aus Git kommt, gibt
    es diese Zeile nicht mehr — das Muster traf ins Leere und die Fernfassung
    blieb leer. Zweimal dasselbe auf zwei Wegen auszurechnen war der Fehler.

    `notfalls` ist die Antwort, wenn Git nichts hergibt. Fuer den eigenen Stand
    ist eine plausible Zahl besser als nichts: die Anwendung laeuft ja. Fuer die
    Gegenstelle waere sie eine Luege — dort steht dann lieber gar nichts als
    eine Fassung, die niemand geprueft hat.
    """
    genau = _git_semver(ref)
    if genau:
        return genau
    try:
        tag = subprocess.run(['git', 'describe', '--tags', '--abbrev=0',
                              '--match', _FASSUNGS_TAGS, ref],
                             cwd=Path(__file__).parent, capture_output=True,
                             text=True, timeout=5).stdout.strip()
        seit = subprocess.run(['git', 'rev-list', '--count', f'{tag}..{ref}'],
                              cwd=Path(__file__).parent, capture_output=True,
                              text=True, timeout=5).stdout.strip()
        haupt, _, _ = tag.lstrip('v').rpartition('.')
        if haupt and seit.isdigit():
            return f'{haupt}.{seit}'
    except Exception:
        pass
    return notfalls


VERSION  = _fassung()
GIT_HASH = _git_hash()

# Hintergrund-Cache: lesbare Remote-Version + ob ein Update verfügbar ist.
# Wird periodisch in einem Thread aktualisiert, damit der Endpunkt nie blockiert.
_remote_ver = {'ts': 0.0, 'version': '', 'hash': '', 'up_to_date': None}

def _refresh_remote_version() -> bool:
    """Gleicht den Remote-Stand ab. Liefert True, wenn das geklappt hat."""
    try:
        fetch = subprocess.run(['git', 'fetch', '--quiet'], cwd=Path(__file__).parent, timeout=30)
        h = subprocess.run(['git', 'rev-parse', '--short', '@{u}'],
                           cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        rhash = h.stdout.strip() if h.returncode == 0 else ''
        # Dieselbe Rechnung wie fuer den eigenen Stand, nur auf den Stand der
        # Gegenstelle angewandt. Frueher wurde dafuer deren main.py geholt und
        # eine Zeichenkette herausgesucht — ein zweiter Weg zum selben Ergebnis,
        # und der eine ueberlebte die Umstellung auf Git-Fassungen nicht.
        rver = _fassung('@{u}', notfalls='') if rhash else ''
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
GRUNDRISS_FILE = BASE_DIR / 'grundriss.json'
GRUNDRISS_VORLAGE = BASE_DIR / 'grundriss.example.json'
HEIZUNG_FILE  = BASE_DIR / 'heizung.json'
WARTUNG_FILE  = BASE_DIR / 'wartung.json'
DEVICES_FILE  = BASE_DIR / 'devices.json'
DEVICES_VORLAGE = BASE_DIR / 'devices.example.json'
SYNC_FILE     = BASE_DIR / 'sync.json'
KONTEN_FILE   = BASE_DIR / 'konten.json'
DEBUG_FILE    = BASE_DIR / 'debug_log.json'
SITZUNGEN_FILE = BASE_DIR / 'sitzungen.json'
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
# Konten an Bord: die Wahrheit liegt beim Server, hier steht eine Kopie. Damit
# funktioniert die Anmeldung auch ohne Internet — an Bord der Normalfall, nicht
# die Ausnahme. Solange die Kopie leer ist, gilt die Schonfrist aus
# sync/zugang.py und alles bleibt offen; das ist vertretbar, weil der Pi nur im
# Bordnetz haengt. Sobald das erste Konto ankommt, greift die Pflicht.
konten = Konten(KONTEN_FILE, SITZUNGEN_FILE)

# Mitschnitt der letzten 24 Stunden. Er haengt sich in das Protokoll ein und
# nimmt alles ab WARNUNG auf — dazu, was die Oberflaeche meldet. Der Anlass:
# ein Fehler, den der Eigner sieht, ist eine Stunde spaeter nicht mehr da, und
# dann bleibt nur Raten.
debug_log = DebugLog(DEBUG_FILE)
logging.getLogger().addHandler(RingHandler(debug_log))
debug_log.merken('info', 'bord', f'Bordrechner gestartet, Fassung {VERSION}')

hist_store = HistoryStore(HISTORY_FILE, retention_s=16 * 3600,
                          max_entries=history.maxlen or 10800)

# Der grobe Verlauf haelt sieben Tage vor. Eine Zeile je Minute sind 1440 am
# Tag, gut 10.000 in der Woche und rund 1,5 MB — verglichen mit dem feinen
# Verlauf (17.280 Zeilen am Tag) ist das fuer die SD-Karte nichts.
grob_store = HistoryStore(HISTORY_GROB_FILE, retention_s=7 * 86400,
                          max_entries=history_grob.maxlen or 10080,
                          rotate_age_s=8 * 86400)


# ── Verbindung zum Server ───────────────────────────────────────────────────
# Ohne sync.json passiert hier gar nichts: kein Verbindungsversuch, keine
# Meldung, kein Verbrauch. Das ist der Auslieferungszustand — der Server ist
# Zusatz, nie Voraussetzung (KONZEPT-SERVER.md).
#
# Zum Server geht der GROBE Verlauf (Minutenmittel). Zwei Gruende: er haelt
# sieben Tage vor statt sechzehn Stunden, das Nachliefern ueberlebt also auch
# einen laengeren Ausfall — und er ist ein Zwoelftel der Datenmenge, was ueber
# Mobilfunk zaehlt. Der feine Verlauf bleibt an Bord.

def _sync_konfig() -> dict:
    k = read_json(SYNC_FILE, {}) or {}
    return k if isinstance(k, dict) else {}


_sync_cfg = _sync_konfig()
# Ein Geheimnis, das nur in diesem Prozess existiert. Der Sync-Client fuehrt
# Befehle vom Server ueber HTTP gegen die eigene API aus (damit sie dieselbe
# Pruefung durchlaufen wie ein Griff an Bord) — dabei hat er aber kein
# Sitzungscookie. Mit diesem Kopf weist er sich als der eigene Prozess aus und
# nennt, WER auf dem Server gefragt hat.
#
# Er ist keine Vollmacht: der Pi schlaegt das genannte Konto in SEINER Kopie
# nach und wendet dessen Rechte an. Sagt der Server "crew", gilt Crew, nicht
# mehr. Neu bei jedem Start, steht nirgends auf der Platte.
_INTERN = secrets.token_urlsafe(32)


def _zustand_fuer_server() -> dict:
    """Was zum Server hochgeht — der Bordzustand plus die Heizung.

    Die Heizung haengt an einem eigenen Poller und war deshalb nicht Teil des
    Zustands. Fuer die Bordansicht ist das gleichgueltig (dort holt sie sich
    ihre Kachel selbst), fuer den Server aber nicht: was nicht mitgeht, hat er
    nicht, und was er nicht hat, kann er nicht zeigen, wenn das Boot schweigt.

    Und genau danach fragt man aus der Ferne im Winter zuerst: laeuft die
    Heizung noch, wie kalt ist es drinnen. Der Schnappschuss ist ein paar
    hundert Byte — auch ueber Mobilfunk kein Argument.
    """
    d = state.to_dict()
    # Die Position. Sie kommt nicht vom Bus, sondern vom GNSS des Routers, und
    # gehoert deshalb nicht in BoatState — aber sehr wohl in das, was der
    # Server bekommt: gezeigt wird sie ausschliesslich im Logbuch, und das
    # laeuft dort.
    try:
        pos = ((conn_mon.get_status() if conn_mon else {})
               .get('router') or {}).get('gps')
        if pos:
            # Dasselbe Alterfeld wie bei den Bus-Gruppen, damit die Anzeige
            # nicht zwei Wege kennen muss. Gemessen ab dem Fix des Empfaengers,
            # nicht ab dem Abruf: der Router antwortet auch mit einem alten Fix,
            # und der Abruf saehe dann taufrisch aus.
            zeit = pos.get('zeit')
            pos = dict(pos)
            pos['_age_s'] = round(max(0.0, time.time() - zeit), 1) \
                if isinstance(zeit, (int, float)) and zeit > 1704067200 else None
        d['position'] = pos
    except Exception as e:
        log.warning('Position geht nicht mit zum Server: %s', e)
    try:
        d['heizung'] = heizung.snapshot()
    except Exception as e:
        # Sichtbar und nicht auf debug: geht der Schnappschuss still verloren,
        # fehlt die Heizung in der Serverkopie, und niemand weiss warum.
        log.warning('Heizungszustand geht nicht mit zum Server: %s', e)
    try:
        d['wartung'] = _wartung_stand()
    except Exception as e:
        log.warning('Wartungsstand geht nicht mit zum Server: %s', e)
    return d


def _wartung_stand() -> dict:
    """Wie viele Wartungsaufgaben ueberfaellig oder bald faellig sind.

    Nur die Zaehlung, nicht der Plan. Der Plan waere ueber den Fernabruf zu
    haben — aber genau dann nicht, wenn man ihn braucht: wer von unterwegs ins
    Logbuch schaut, tut das ueblicherweise, WEIL das Boot allein liegt, und ein
    Abruf ueber die Vermittlung setzt voraus, dass es antwortet. Drei Zahlen
    kosten nichts und stehen auch dann noch da, wenn seit Tagen Funkstille ist.

    Gerechnet wird wie in der Bordansicht (`getWartungStatus`), damit nicht
    zwei Stellen unterschiedlich zaehlen: kein Intervall heisst "von Hand" und
    zaehlt nirgends mit, nie erledigt heisst ueberfaellig.
    """
    plan = read_json(WARTUNG_FILE, [])
    frist = int(((read_json(PRESETS_FILE, {}) or {}).get('wartung') or {})
                .get('due_soon_days', 7))
    heute = date.today()
    ueberfaellig = bald = gesamt = 0
    for kategorie in (plan if isinstance(plan, list) else []):
        for aufgabe in (kategorie.get('tasks') or []):
            tage = aufgabe.get('interval_days')
            if not isinstance(tage, int) or tage <= 0:
                continue                       # von Hand gefuehrt, hat keine Faelligkeit
            gesamt += 1
            zuletzt = aufgabe.get('last_done')
            if not zuletzt:
                ueberfaellig += 1
                continue
            try:
                faellig = date.fromisoformat(str(zuletzt)[:10]) + timedelta(days=tage)
            except ValueError:
                continue
            rest = (faellig - heute).days
            if rest < 0:
                ueberfaellig += 1
            elif rest <= frist:
                bald += 1
    return {'ueberfaellig': ueberfaellig, 'bald': bald, 'gesamt': gesamt,
            'frist_tage': frist}


def _nur_ort() -> dict | None:
    """Breite und Laenge fuer den Nachtmodus — mehr nicht.

    Bewusst nur diese zwei Zahlen: die Bordansicht ZEIGT die Position nicht,
    sie rechnet damit. Satellitenzahl, Guete und Hoehe braucht sie dafuer nicht,
    und was nicht mitfaehrt, kann auch nicht versehentlich irgendwo auftauchen.
    """
    try:
        pos = ((conn_mon.get_status() if conn_mon else {}).get('router') or {}).get('gps')
    except Exception:
        return None
    if not pos or not isinstance(pos.get('lat'), (int, float)):
        return None
    return {'lat': pos['lat'], 'lon': pos['lon']}


def _konten_stand() -> str:
    return konten.zum_verteilen()['stand']


def _konten_uebernehmen(daten: dict) -> int:
    anzahl = konten.ersetzen({k['name']: k for k in (daten.get('konten') or [])})
    # Und die offenen Sitzungen des Servers dazu: die Anlage hat drei Namen,
    # und wer sich unter einem anmeldet, soll unter den anderen angemeldet
    # SEIN. Das Cookie gilt fuer alle drei — es nuetzt aber nichts, wenn diese
    # Seite die Sitzung nicht kennt.
    dazu = konten.sitzungen_uebernehmen(daten.get('sitzungen') or {},
                                        daten.get('widerrufe') or {})
    if dazu:
        log.info('%d Sitzungen vom Server übernommen', dazu)
    return anzahl


sync = sync_client.SyncClient(
    adresse=_sync_cfg.get('adresse', ''),
    token=_sync_cfg.get('token', ''),
    geraet=_sync_cfg.get('geraet', 'mave-pi'),
    version=VERSION,
    zustand_holen=_zustand_fuer_server,
    verlauf_holen=lambda ab, grenze: grob_store.ab_folge(ab, grenze),
    verlauf_stand=grob_store.hoechste_folge,
    conn_status=(conn_mon.get_status if conn_mon else (lambda: None)),
    schalter=lambda: _sync_konfig().get('schalter', 'auto'),
    konten_stand=_konten_stand,
    konten_uebernehmen=_konten_uebernehmen,
    intern_token=_INTERN,
    # Der Port, unter dem die App selbst laeuft — dorthin schickt der Client
    # die Befehle, die vom Server kommen. Nicht fest verdrahtet, weil er im
    # Test ein anderer ist als im Betrieb.
    eigener_port=int(_sync_cfg.get('eigener_port', 8080)),
    markenpfad=BASE_DIR / 'sync_start.json',
)


_REG_DEVICE_MODE = 0x0200   # DeviceMode: 0 = aus, 1 = ein
_REG_MAX_STROM   = 0xEDF0   # maximaler Ladestrom, 0,1 A


def _zellspreizung_mv(data: dict) -> float | None:
    """Abstand zwischen hoechster und niedrigster Zelle in Millivolt.

    None, wenn keine oder unvollstaendige Zellwerte vorliegen — der Balance-Lauf
    hebt dann bewusst nicht weiter, statt blind zu steigern.
    """
    zellen = (data.get('bms') or {}).get('cells')
    if not isinstance(zellen, list) or not zellen:
        return None
    werte = [z.get('voltage') for z in zellen
             if isinstance(z, dict) and isinstance(z.get('voltage'), (int, float))]
    if len(werte) < 2:
        return None
    return round((max(werte) - min(werte)) * 1000.0, 1)


def _landstrom_da(data: dict) -> bool | None:
    """Haengt das Boot am Landstrom?

    Der Smart IP43 wird aus dem Netz gespeist: ohne Landstrom ist er stromlos
    und meldet sich gar nicht. Ein per DeviceMode abgeschalteter Lader redet
    dagegen weiter — genau diese Unterscheidung braucht der Balance-Lauf, der
    seine Lader in der Entladephase selbst abschaltet.

    None heisst "unbekannt" (noch keine Daten) und wird wie "da" behandelt:
    lieber weiterlaufen als einen Lauf wegen eines fehlenden Feldes anhalten.
    """
    lader = data.get('charger')
    if not isinstance(lader, dict) or 'active' not in lader:
        return None
    return bool(lader.get('active'))


# Zuletzt geschriebener maximaler Ladestrom je Geraeteinstanz. Das Register
# liegt im Flash des Laders, ein unveraenderter Wert wird deshalb nicht erneut
# geschrieben. Er aendert sich nur beim Wechsel in den Balance-Lauf und zurueck.
_strom_geschrieben: dict[int, int] = {}


def _strom_setzen(dev: dict):
    """Schreibt den maximalen Ladestrom eines Geraets, wenn er sich geaendert hat."""
    a = dev.get('max_a')
    if isinstance(a, bool) or not isinstance(a, (int, float)) or a <= 0:
        return
    roh  = int(round(a * 10))          # das Register rechnet in 0,1 A
    inst = dev['instance']
    if _strom_geschrieben.get(inst) == roh:
        return
    can_if.send_charger_register(_REG_MAX_STROM, roh, inst, size=2)
    _strom_geschrieben[inst] = roh
    log.info("Lader %s Inst %d: max. Ladestrom %.1f A", dev.get('label', inst), inst, a)


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
        _strom_setzen(dev)


# ── Minutenmittel fuer den groben Verlauf ─────────────────────────────────
# Gemittelt wird ueber die 5-Sekunden-Eintraege einer Minute. Ein Mittelwert
# statt einer Stichprobe, damit kurze Spitzen (Anlaufstrom) die Wochenansicht
# nicht verfaelschen, aber auch nicht spurlos verschwinden.
_grob_eimer: dict = {}
_grob_minute: int = -1


# Mehr Zellen als das meldet kein Akku, den man auf ein Boot stellt (sechzehn
# in Reihe sind 48 V). Die Grenze schuetzt den Verlauf davor, dass ein BMS mit
# kaputtem Zaehler hunderte Felder je Zeile erzeugt.
_ZELLEN_MAX = 16


# Welche Alarme schon nach draussen gemeldet sind, und ob sie beim letzten Mal
# quittiert waren. Nur das — die Alarme selbst haelt die Engine.
_alarm_gemeldet: dict = {}


def _alarme_melden() -> None:
    """Neue und aufgeloeste Alarme zum Server tragen.

    Der Kanal dafuer war seit jeher da (`sync.ereignis`, im Code beschrieben
    als "sie sind der Grund, warum das System nach draussen spricht") — und
    wurde von NIRGENDWO aufgerufen. Alarme erreichten den Server also nie: im
    Logbuch fehlten sie, und eine Benachrichtigung haette es nie geben koennen.

    Gemeldet wird dreierlei: das Auftreten, das Quittieren und das
    Verschwinden. Ein Alarm, der nur kommt und nie geht, steht sonst fuer immer
    in der Chronik — und ohne das Quittieren koennte auswaerts niemand
    unterscheiden, ob ein Alarm von jemandem GESEHEN wurde oder ob er nur
    stundenlang unbemerkt lief. Genau diese Unterscheidung ist der Grund, aus
    dem man von unterwegs ueberhaupt ins Logbuch schaut.

    Das Quittieren wird hier abgeleitet und nicht an den Endpunkten gemeldet:
    quittiert wird an mehreren Stellen (einzeln, alle auf einmal, kuenftig
    vielleicht aus einer Benachrichtigung heraus), und jede davon muesste sonst
    daran denken. Ein Vergleich gegen den letzten Stand denkt immer daran.
    """
    global _alarm_gemeldet
    offen = {a['id']: a for a in alarms.get_alarms() if not a.get('resolved')}
    neu = [a for kennung, a in offen.items() if kennung not in _alarm_gemeldet]
    quittiert = [a for kennung, a in offen.items()
                 if kennung in _alarm_gemeldet and a.get('acknowledged')
                 and not _alarm_gemeldet[kennung]]
    weg = set(_alarm_gemeldet) - set(offen)
    if not neu and not weg and not quittiert:
        return
    _alarm_gemeldet = {kennung: bool(a.get('acknowledged')) for kennung, a in offen.items()}
    for a in neu:
        _ereignis_absetzen('alarm', {
            'kennung': a['id'], 'name': a.get('name'), 'schluessel': a.get('key'),
            'wert': a.get('value'), 'schwelle': a.get('threshold'),
            'schwere': a.get('severity'), 'zeit': a.get('timestamp'),
        })
    for a in quittiert:
        _ereignis_absetzen('alarm_quittiert', {'kennung': a['id'], 'name': a.get('name')})
    for kennung in weg:
        _ereignis_absetzen('alarm_weg', {'kennung': kennung})


def _ereignis_absetzen(art: str, daten: dict) -> None:
    """Nebenlaeufig senden. Der Zustandsstrom darf nicht auf die Leitung warten;
    ohne Verbindung faellt es ohnehin still aus."""
    try:
        asyncio.create_task(sync.ereignis(art, daten))
    except Exception as e:
        log.debug('Ereignis %s nicht gesendet: %s', art, e)


# Ab wann der zwischengespeicherte Router-Stand nicht mehr als frisch gilt.
# Gepollt wird alle 20 s; nach zwei ausgefallenen Runden ist es keine
# Schwankung mehr, sondern eine Aussage.
_RT_FRIST_S = 50

# Die Laufzeit des Routers beim letzten frischen Abruf. Nur dafuer da, einen
# Neustart zu ERKENNEN — im Verlauf steht dann das Ergebnis und nicht die Zahl.
_rt_letzte_lauf: float | None = None


def _router_werte(netz: dict | None) -> dict:
    """Was der Router beisteuert — als Zahlen, die man zeichnen kann.

    Die Auswahl folgt einer Regel: jeder Wert muss eine Erklaerung von einer
    anderen TRENNEN. Alles andere waere Ballast, und die Zeilen gehen ueber
    Mobilfunk.

      rt_an    Antwortet er ueberhaupt? Null heisst: seit ueber einer Minute
               nicht mehr.
      rt_neu   Eins genau in dem Moment, in dem seine Laufzeit zurueckfaellt —
               also bei einem Neustart. Hier steht bewusst das ERGEBNIS und
               nicht die Laufzeit selbst: eine Kurve, die stundenlang steigt,
               ist als Bild wertlos, und die Frage lautet ohnehin "wie oft" und
               nicht "seit wann".
      wl24     Stirbt 2,4 GHz VOR dem Neustart oder mit ihm? Daran entscheidet
               sich die Ursache.
      wl5      Nur 2,4 GHz oder beide Baender? Trennt einen Fehler DIESES
               Radios von einem des ganzen Funkteils.
      rt_cpu   Last in Prozent (vier Kerne).
      rt_ram   Speicher in Prozent. Beide zusammen sind die Gegenprobe:
               klettert eins davon vor jedem Absturz gegen hundert, liegt es
               nicht am Radio.

    Die Frischepruefung ist keine Feinheit, sondern der Kern. Der Poller
    BEHAELT seinen letzten Stand, wenn eine Abfrage scheitert — ohne sie wuerde
    ausgerechnet waehrend eines Ausfalls die alte Laufzeit weitergeschrieben,
    und der Mitschnitt behauptete "alles gut" genau dann, wenn nichts gut ist.
    """
    global _rt_letzte_lauf
    status = netz or {}
    ts = status.get('ts')
    frisch = (isinstance(ts, (int, float)) and not isinstance(ts, bool)
              and time.time() - ts < _RT_FRIST_S)
    if not frisch:
        # Kein Kontakt. Genau das wird geschrieben und sonst nichts — jeder
        # weitere Wert waere von vorhin und saehe aus wie von jetzt.
        _rt_letzte_lauf = None
        return {'rt_an': 0}

    raus: dict = {'rt_an': 1}
    # Eine Ebene tiefer, unter 'router'. `_fetch_router()` liefert den inneren
    # Teil, aber im Betrieb kommt hier der ganze Status an — und der packt ihn
    # neben Starlink unter 'router'.
    r_ = status.get('router') or {}
    gesund = r_.get('gesundheit') or {}

    lauf = gesund.get('uptime_s')
    if isinstance(lauf, (int, float)) and not isinstance(lauf, bool):
        # Eine Laufzeit kann nur steigen. Faellt sie, lag ein Neustart
        # dazwischen. Die kleine Toleranz faengt Rundung ab, nicht mehr.
        raus['rt_neu'] = 1 if (_rt_letzte_lauf is not None
                               and lauf < _rt_letzte_lauf - 5) else 0
        _rt_letzte_lauf = lauf

    for feld, schluessel in (('rt_ram', 'ram_prozent'), ('rt_cpu', 'cpu_prozent')):
        wert = gesund.get(schluessel)
        if isinstance(wert, (int, float)) and not isinstance(wert, bool):
            raus[feld] = round(wert, 1)

    for r in (r_.get('radios') or []):
        feld = {'2.4GHz': 'wl24', '5GHz': 'wl5'}.get(r.get('band'))
        if feld:
            raus[feld] = 1 if r.get('up') else 0
    return raus


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
                # Vier Stellen sind fuer jeden Messwert reichlich — fuer eine
                # geografische Breite aber rund elf Meter. Eine Spur am
                # Liegeplatz saehe damit aus, als haette das Boot Sprünge
                # gemacht. Position deshalb mit sechs Stellen, das sind gut
                # zehn Zentimeter.
                stellen = 6 if k in ('lat', 'lon') else 4
                mittel[k] = round(summe / anzahl, stellen)
        if len(mittel) > 1:
            history_grob.append(mittel)
            grob_store.append(mittel)
        _grob_eimer = {}
        _grob_minute = minute
    for k, v in entry.items():
        # `n` ist die Folgenummer, keine Messgroesse. Sie wurde bisher
        # mitgemittelt und kam als Kommazahl heraus (1476.5) — der Server
        # verlangt aber ganze Zahlen und bekam deshalb NIE einen Verlauf
        # geliefert. Die Nummer vergibt allein history_store beim Anhaengen.
        if k in ('ts', 'n') or not isinstance(v, (int, float)) or isinstance(v, bool):
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
    _alarme_melden()
    # Hafen-SOC-Regelung: bei Zustandswechsel sofort neue Setpoints senden
    soc = data.get('battery', {}).get('soc')
    # Batteriestrom, Zellspreizung und Landstrom mitgeben: die Selbstermittlung
    # der Haltespannung misst nur bei eingependelter Bank, und der Balance-Lauf
    # hebt die Spannung nur, wenn die Zellen beieinanderliegen.
    if charge_ctrl.update_soc(soc,
                              data.get('battery', {}).get('current'),
                              _zellspreizung_mv(data),
                              _landstrom_da(data)):
        _apply_charger_setpoints(charge_ctrl.device_setpoints())
    batt = data.get('battery', {})
    now = time.time()
    entry: dict = {'ts': now}
    for key in ('soc', 'voltage', 'current'):
        v = batt.get(key)
        if v is not None:
            entry[key] = v
    # Die Starterbatterie. Sie haengt an einem einfachen Spannungseingang, es
    # gibt also weder Ladestand noch Strom — die Spannung ist alles, was man
    # ueber sie sagen kann, und genau deshalb ist ihr VERLAUF das Interessante:
    # ein Starter faellt nicht ploetzlich aus, er wird ueber Wochen schlechter.
    sv = batt.get('starter_voltage')
    if isinstance(sv, (int, float)) and not isinstance(sv, bool):
        entry['starter'] = sv
    # solar2/solar3/wind sind VORBEREITUNG für Hardware, die noch nicht verbaut
    # ist. Sie kosten nichts: fehlt die Quelle im State, liefert .get() None und
    # es wird nichts geschrieben. NICHT entfernen — sie gehören zum Ausbauplan.
    for src_key, field in (('solar', 'solar1'), ('alternator', 'alternator'),
                            ('solar2', 'solar2'), ('solar3', 'solar3'),
                            ('charger', 'charger'), ('orion', 'orion'),
                            ('wind', 'wind')):
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
    # Mittlere Raumtemperatur der Heizung. Der Schnappschuss liegt oben schon
    # fuer die Alarmpruefung vor — kein zweiter Abruf.
    hz_avg = check_data['heizung'].get('raum_temp_avg')
    if isinstance(hz_avg, (int, float)) and not isinstance(hz_avg, bool):
        entry['raumtemp'] = hz_avg
    bms = data.get('bms', {})
    for bms_key in ('current_charge', 'current_discharge'):
        v = bms.get(bms_key)
        if v is not None:
            entry[bms_key] = v
    # Die einzelnen Zellspannungen. Die Zelldifferenz darunter sagt, WIE WEIT
    # die Zellen auseinanderliegen; erst diese Reihen sagen, WELCHE Zelle
    # wegläuft und seit wann. Bei vier Zellen sind das vier Zahlen je Zeile —
    # das faellt neben den uebrigen zwanzig nicht ins Gewicht.
    #
    # Die Zahl der Zellen steht nicht fest: eine 12-V-Bank hat vier, eine
    # 48-V-Bank sechzehn. Geschrieben wird, was das BMS meldet; die Obergrenze
    # ist nur eine Bremse gegen ein BMS, das Unsinn erzaehlt.
    for nr, zelle in enumerate((bms.get('cells') or [])[:_ZELLEN_MAX], start=1):
        v = zelle.get('voltage') if isinstance(zelle, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            entry[f'zelle{nr}'] = round(v, 3)      # Millivolt, darum drei Stellen

    # Zelldifferenz gehoert in den Server-Verlauf, nicht nur in den Browser.
    # Vorher rechnete sie ausschliesslich charts.js aus den Live-Daten — die
    # aus der Datei geladenen aelteren Punkte hatten das Feld deshalb nie und
    # der Anfang der Kurve fehlte, waehrend alle anderen Serien vollstaendig
    # waren.
    hoch, tief = bms.get('highest_cell_v'), bms.get('lowest_cell_v')
    if hoch is not None and tief is not None:
        entry['zelldiff'] = round(hoch - tief, 4)

    # Die Verbindung nach draussen gehoert in den Verlauf. Sie hing bisher an
    # einem eigenen Poller und war nach fuenf Minuten vergessen — die Frage
    # "war das Netz gestern Abend schon so schlecht?" liess sich nicht
    # beantworten.
    netz = conn_mon.get_status() if conn_mon else None
    # Die Position in den Verlauf. Nur so entsteht eine Spur — und nur deshalb
    # steht sie hier: gezeigt wird sie ausschliesslich im Logbuch.
    #
    # Sie wandert durch die Minutenmittelung; ein Mittelwert aus sechzig
    # Positionen einer Minute ist genau das, was eine Spur braucht (er glaettet
    # das Rauschen der Empfaenger weg). Ohne Fix steht hier nichts, und dann
    # fehlt der Punkt in der Spur — richtig so, denn es gab keinen.
    gps = ((netz or {}).get('router') or {}).get('gps') or {}
    if isinstance(gps.get('lat'), (int, float)) and isinstance(gps.get('lon'), (int, float)):
        entry['lat'] = gps['lat']
        entry['lon'] = gps['lon']
    # Die Zahl der Satelliten gehoert eigenstaendig in den Verlauf. Sie
    # beantwortet eine andere Frage als die Position: die sagt, WO das Boot lag,
    # die Satelliten sagen, wie gut der Empfaenger sah — ob der Empfang ueber
    # Nacht wegbrach oder ob er die ganze Zeit knapp war.
    #
    # Nur MIT Fix, und das ist eine Einschraenkung, die man kennen muss:
    # `_gps_lesen` wirft die ganze Meldung weg, wenn kein Fix zustande kam
    # (bewusst — eine alte Position, die aussieht wie eine aktuelle, ist auf
    # einem Boot schlimmer als gar keine). Die Satellitenzahl faellt dabei mit
    # weg. Im Verlauf fehlen deshalb genau die Zeiten ohne Fix, statt dort eine
    # kleine Zahl zu zeigen. Das getrennt zu retten hiesse, den Rueckgabewert
    # von `_gps_lesen` zu aendern — und dann muessten alle Stellen, die "es gibt
    # eine Position" an dieser Meldung festmachen, gleich mit angefasst werden.
    sats = gps.get('satelliten')
    if isinstance(sats, int) and not isinstance(sats, bool):
        entry['sats'] = sats
    entry.update(_router_werte(netz))

    sl = (netz or {}).get('starlink') or {}
    ping = sl.get('ping_ms')
    if isinstance(ping, (int, float)) and not isinstance(ping, bool):
        entry['ping_ms'] = round(ping, 1)
    runter = sl.get('downlink_bps')
    if isinstance(runter, (int, float)) and not isinstance(runter, bool):
        entry['down_mbit'] = round(runter / 1e6, 2)
    mono = time.monotonic()
    if len(entry) > 1 and mono - _hist_last_mono >= 5.0:
        history.append(entry)
        hist_store.append(entry)      # gepuffert, eigener Thread — blockiert nicht
        _grob_sammeln(entry)          # zusaetzlich ins Minutenmittel
        _hist_last_ts  = now
        _hist_last_mono = mono
    # Die Position faehrt mit, wird aber NICHT angezeigt. Sie dient allein dem
    # Nachtmodus: er rechnet daraus den echten Sonnenuntergang fuer diesen Ort.
    # Ein starrer Zeitplan laege in der Ostsee im Juni um Stunden daneben.
    payload = {**data, 'alarms': alarms.get_alarms(), 'unack_alarms': alarms.unack_count,
               'position': _nur_ort(), 'version': VERSION}
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

    # Die Startmarke wird IMMER gefuehrt, auch ohne Server: sonst waere nach
    # einem Stromausfall ohne Netz nicht mehr feststellbar, dass es einer war.
    befund = await loop.run_in_executor(
        None, lambda: sync.start_vermerken(wand=time.time(), gestellt=time.time() > 1704067200))
    if befund.get('letztes_ende') == 'abbruch':
        log.warning('Voriger Lauf endete unsauber — Stromausfall oder Absturz')
    if sync.eingerichtet:
        asyncio.create_task(sync.laufen())
        log.info('Serververbindung wird aufgebaut')

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
    # Die Startmarke entfernen — sie ist das Zeichen fuer "unsauber beendet".
    # Bleibt sie liegen, meldet der Server beim naechsten Start einen
    # Stromausfall, den es nie gab. Das gehoert ans ENDE: erst wenn alles
    # weggeschrieben ist, war das Ende wirklich geordnet.
    sync.geordnet_beenden()
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

# ── Zugang ──────────────────────────────────────────────────────────────────
# Eine Middleware statt 48 einzelner Pruefungen. Die Regeln stehen in
# sync/zugang.py und gelten auf beiden Seiten — der Pi kann sich nicht darauf
# verlassen, dass der Server vorher gefragt wurde: im Bordnetz spricht die PWA
# direkt mit ihm.

@app.middleware('http')
async def _zugang_pruefen(request: Request, call_next):
    pfad = request.url.path
    if not pfad.startswith('/api/'):
        return await call_next(request)          # Oberflaeche und Dateien sind offen

    schonfrist = konten.leer
    k = None
    if not schonfrist:
        token = zg.token_aus(request)
        if token:
            k = konten.konto_zu_token(token)
        if k is None:
            k = _durchgereicht(request)

    erlaubt, code, meldung = zg.pruefen(k, request.method, pfad, schonfrist=schonfrist)
    if not erlaubt:
        return JSONResponse({'detail': meldung}, status_code=code)

    # Nachgelagerte Endpunkte duerfen wissen, wer da ist, ohne erneut zu suchen.
    request.state.konto = k
    return await call_next(request)


def _durchgereicht(request: Request):
    """Ein Aufruf, den der Server ueber die Sync-Verbindung hereingereicht hat.

    Zwei Bedingungen, beide notwendig: das prozessinterne Geheimnis (von aussen
    nicht zu erraten, es steht nirgends) und die Herkunft 127.0.0.1 — der
    Sync-Client ruft sich selbst auf. Erst dann wird der genannte Name in der
    eigenen Kontenkopie nachgeschlagen.

    Ein uebernommener Server koennte hier einen fremden Namen behaupten. Das
    ist hingenommen und keine neue Luecke: wer den Server hat, hat auch das
    Geraetetoken und damit die Verbindung selbst.
    """
    if not secrets.compare_digest(request.headers.get('x-mave-intern', ''), _INTERN):
        return None
    herkunft = request.client.host if request.client else ''
    if herkunft not in ('127.0.0.1', '::1'):
        log.warning('Interner Kopf von %s — abgewiesen', herkunft)
        return None
    name = (request.headers.get('x-mave-konto') or '').strip()
    if not name:
        return None
    return konten.konto_nach_name(name)


# Loopback-Adressen. Steht hier eine davon in `request.client.host`, sitzt der
# Aufrufer nicht wirklich dort — dann steht ein Gegenstueck auf demselben
# Rechner davor und reicht weiter.
_EIGENER_RECHNER = ('127.0.0.1', '::1', '::ffff:127.0.0.1')


def _herkunft_vom_client(request: Request) -> str:
    """Die Adresse, von der eine Sitzung wirklich kommt.

    Seit auf dem Pi nginx die Verschluesselung uebernimmt und an uvicorn
    weiterreicht, ist `request.client.host` fuer JEDE Sitzung ueber HTTPS
    127.0.0.1. Damit stand in der Anwesenheitsliste bei allen dasselbe, und der
    Abgleich gegen die Geraeteliste des Routers konnte gar nicht treffen — die
    kennt Bordadressen, keine Loopback-Adresse.

    Der weitergereichten Adresse wird NUR geglaubt, wenn der unmittelbare
    Gegenueber der eigene Rechner ist. Sonst duerfte sich jeder im Bordnetz mit
    einem selbstgeschriebenen Kopf eine beliebige Herkunft geben — und die
    Herkunft entscheidet mit darueber, wie eine Sitzung angezeigt wird.

    Reicht uvicorn die Adresse schon selbst durch (--proxy-headers ist seine
    Vorgabe), steht sie hier bereits richtig und dieser Weg greift nicht.
    """
    direkt = request.client.host if request.client else ''
    if direkt not in _EIGENER_RECHNER:
        return direkt
    # X-Forwarded-For kann eine Kette sein; der erste Eintrag ist der Ursprung.
    kette = request.headers.get('x-forwarded-for', '')
    erster = kette.split(',')[0].strip()
    return erster or (request.headers.get('x-real-ip', '') or '').strip() or direkt


@app.post('/api/login')
async def login(request: Request):
    """Anmelden — gegen die Kontenkopie, die vom Server kam.

    Der Pi prueft selbst und fragt nicht beim Server nach: an Bord ist kein
    Internet der Normalfall, und eine Anmeldung, die dann nicht geht, waere
    genau am falschen Ort kaputt.
    """
    daten = await request.json()
    try:
        token, k = konten.anmelden(
            str(daten.get('name', '')), str(daten.get('passwort', '')),
            kiosk=bool(daten.get('kiosk')),
            herkunft=_herkunft_vom_client(request),
            geraet=zg.geraet_aus_ua(request.headers.get('user-agent', '')))
    except Exception as e:
        log.warning('Anmeldung an Bord gescheitert für %r', daten.get('name'))
        return JSONResponse({'detail': str(e)}, status_code=401)
    # Dem Server melden, damit dieselbe Anmeldung auch dort gilt — sonst steht
    # man beim Wechsel ins Logbuch wieder vor der Anmeldemaske. Scheitert es
    # (kein Internet), ist das kein Fehler: die Sitzung gilt an Bord trotzdem,
    # und beim naechsten Verbinden gleicht sich beides ab.
    try:
        kennung = konten_speicher.k.sitzung_kennung(token)
        asyncio.create_task(sync.sitzung_melden(kennung, {
            'konto': k['name'], 'seit': time.time(), 'zuletzt': time.time(),
            'kiosk': bool(daten.get('kiosk')),
            'herkunft': _herkunft_vom_client(request),
            'geraet': zg.geraet_aus_ua(request.headers.get('user-agent', '')),
        }))
    except Exception as e:
        log.debug('Sitzung nicht gemeldet: %s', e)

    antwort = JSONResponse(rechte_modul.uebersicht(k))
    # Siehe server/app.py: das alte, nur fuer diesen Namen gueltige Cookie muss
    # weg, sonst schickt der Browser zwei und das falsche gewinnt.
    antwort.delete_cookie(zg.SITZUNG_COOKIE, path='/')
    antwort.set_cookie(zg.SITZUNG_COOKIE, token, max_age=int(konten_speicher.SITZUNG_DAUER_S),
                       httponly=True, secure=(request.url.scheme == 'https'),
                       samesite='lax', path='/',
                       # Gilt fuer alle drei Namen — siehe sync/zugang.py.
                       domain=zg.keks_bereich(request.headers.get('host', '')))
    return antwort


@app.post('/api/logout')
async def logout(request: Request):
    token = zg.token_aus(request)
    konten.abmelden(token)
    # Dem Server sagen, dass diese Sitzung weg ist. Sonst gilt sie dort
    # weiter — und beim naechsten Abgleich traegt er sie hierher zurueck.
    try:
        if token:
            asyncio.create_task(sync.sitzung_melden(
                konten_speicher.k.sitzung_kennung(token), {}, beendet=True))
    except Exception as e:
        log.debug('Abmeldung nicht gemeldet: %s', e)
    antwort = JSONResponse({'ok': True})
    antwort.delete_cookie(zg.SITZUNG_COOKIE, path='/',
                          domain=zg.keks_bereich(request.headers.get('host', '')))
    return antwort


@app.get('/api/anwesend')
async def anwesend():
    """Wer gerade am BOOT angemeldet ist — also an Bord.

    Der Unterschied zum Server ist die ganze Aussage: im Bordnetz zeigt
    mave.circuit-sailor.com auf den Pi, wer hier eine Sitzung hat, sitzt also
    im Bordnetz. Wer nur ueber den Server hereinkommt, ist woanders.

    Dazu, wo es geht, der NAME des Geraets aus der Router-Liste. Der Browser
    verraet nur seine Gattung ("Chrome auf Android"); der Router kennt den
    Namen, den das Geraet selbst angibt. Das ist der Unterschied zwischen
    "irgendein Android" und "das Tablet im Salon".

    Die Zuordnung passiert nur an Bord und nur hier: die IP einer Sitzung ist
    eine Adresse im BORDNETZ. Auf dem Server zeigt sie ins Internet und
    bedeutet dort nichts — dieselbe Verknuepfung waere dort schlicht falsch.
    """
    sitzungen = konten.sitzungen()
    try:
        namen = geraete.namen_nach_ip(conn_mon.get_status() if conn_mon else None)
    except Exception as e:                    # der Router ist kein Grund, die Liste zu verlieren
        log.debug('Gerätenamen nicht ermittelbar: %s', e)
        namen = {}
    for si in sitzungen:
        treffer = namen.get(si.get('herkunft') or '')
        if treffer and treffer.get('name'):
            si['geraet_name'] = treffer['name']
            si['geraet_quelle'] = treffer.get('quelle')
            if treffer.get('signal') is not None:
                si['geraet_signal'] = treffer['signal']
    return {'quelle': 'boot', 'sitzungen': sitzungen}


@app.get('/api/zugang')
async def zugang_stand(request: Request):
    """Was die Oberflaeche wissen muss, bevor sie irgendetwas anderes fragt.

    `offen` sagt, dass noch keine Kontenkopie da ist und deshalb alles
    zugaenglich bleibt. Die Oberflaeche zeigt das deutlich an — ein stiller
    offener Zustand waere schlimmer als ein sichtbarer.
    """
    token = zg.token_aus(request)
    k = konten.konto_zu_token(token) if token else None
    return {
        'angemeldet': bool(k),
        'offen': konten.leer,
        'quelle': 'boot',
        'konto': rechte_modul.uebersicht(k) if k else None,
    }

app.mount('/static', _NoCacheStatic(directory=STATIC_DIR), name='static')


@app.get('/sw.js', include_in_schema=False)
async def service_worker():
    """Der Service Worker muss von der Wurzel kommen.

    Unter /static/sw.js waere sein Geltungsbereich nur /static/ — die Seite
    selbst laege ausserhalb, und Chrome haette weiter keinen Grund, die
    Anwendung als installierbar zu betrachten.
    """
    return FileResponse(STATIC_DIR / 'sw.js', media_type='application/javascript',
                        headers={'Cache-Control': 'no-cache'})

# JS files in dependency order — concatenated into one request on /js-bundle.js
from js_bundle_liste import JS_FILES as _JS_FILES
_js_bundle: dict = {'data': b'', 'gz': b'', 'etag': '', 'mtime': 0.0}


def _bundle_frisch(js_dir: Path) -> None:
    """Baut das Buendel neu, sobald sich eine der Dateien geaendert hat.

    Gepackt wird EINMAL und mitgespeichert. Vorher lag das Packen bei nginx,
    das die 518 kB bei jedem kalten Abruf neu durch gzip schob — gemessen 0,54
    bis 0,65 s bis zum ersten Byte, auf dem einzigen Kern des Pi und ohne jede
    Beschleunigung im Prozessor. Das Ergebnis war dabei jedes Mal dasselbe.

    Stufe 6 statt der nginx-Vorgabe 1: die Arbeit faellt jetzt einmal je Update
    an, nicht je Abruf, also darf sie gruendlicher sein. Das spart nebenbei
    noch ein paar Kilobyte auf dem Weg durchs Bordnetz.
    """
    latest = max((js_dir / f).stat().st_mtime for f in _JS_FILES if (js_dir / f).exists())
    # `!=` statt `<`: siehe / weiter unten — nach einem Zuruecknehmen kann die
    # Datei aelter sein als die im Speicher.
    if _js_bundle['mtime'] == latest:
        return
    parts = []
    for f in _JS_FILES:
        p = js_dir / f
        if p.exists():
            parts.append(p.read_text(encoding='utf-8'))
    roh = ('\n;// ---\n'.join(parts)).encode()
    _js_bundle['data']    = roh
    _js_bundle['gz']      = gzip.compress(roh, 6)
    _js_bundle['mtime']   = latest
    _js_bundle['etag']    = f'"{int(latest)}"'
    log.info('JS-Buendel gebaut: %d kB roh, %d kB gepackt',
             len(roh) // 1024, len(_js_bundle['gz']) // 1024)


@app.get('/js-bundle.js', include_in_schema=False)
async def js_bundle(req: Request):
    _bundle_frisch(STATIC_DIR / 'js')
    return _ausliefern(req, _js_bundle['data'], _js_bundle['gz'],
                       _js_bundle['etag'], 'application/javascript')


def _ausliefern(req: Request, daten: bytes, gz: bytes, etag: str, typ: str) -> Response:
    """Fertige Bytes ausliefern, gepackt wenn der Browser das mag.

    Gepackt wird nicht hier, sondern einmal beim Bauen. Der Pi hat einen Kern
    ohne Beschleunigung im Prozessor; jedes Neu-Packen derselben Bytes ist
    Rechenzeit, die keiner anderen Aufgabe zur Verfuegung steht.
    """
    gepackt = bool(gz) and 'gzip' in (req.headers.get('accept-encoding') or '')
    marke = f'{etag[:-1]}-gz"' if gepackt else etag
    kopf = {'Cache-Control': 'no-cache', 'ETag': marke, 'Vary': 'Accept-Encoding'}
    if req.headers.get('if-none-match') == marke:
        return Response(status_code=304, headers=kopf)
    if gepackt:
        kopf['Content-Encoding'] = 'gzip'
    return Response(content=gz if gepackt else daten, media_type=typ, headers=kopf)


_index_cache: dict = {'data': b'', 'gz': b'', 'etag': '', 'mtime': 0.0, 'stand': ''}
_wand_cache:  dict = {'data': b'', 'gz': b'', 'etag': ''}

# Die Wandfassung unterscheidet sich in genau einem Zeichenzug.
_WAND_MANIFEST = ('href="/static/manifest.json"',
                  'href="/static/manifest-wand.json"')


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
    stand = _git_hash() or '?'
    # Der Stand MUSS mit in die Bedingung. Vorher hing der Zwischenspeicher
    # allein an der Aenderungszeit von index.html — und in die Seite wird eine
    # Commit-Kennung eingesetzt, die sich bei JEDEM Commit aendert. Ein Commit,
    # der index.html nicht anfasst (nur CSS, nur JavaScript, nur ein Bild),
    # lieferte danach fuer immer die alte Kennung aus, waehrend /api/stand die
    # neue meldete. Die Seite verglich beide, fand sie verschieden und zeigte
    # "Diese Seite laeuft auf einem aelteren Stand" — auch direkt nach dem
    # Neuladen, weil das Neuladen an der Ursache nichts aendert.
    #
    # `!=` statt `<`: nach einem Zuruecknehmen auf eine aeltere Fassung kann die
    # Datei aelter sein als die im Speicher, und mit `<` wuerde der Speicher nie
    # wieder anfassen.
    if _index_cache['mtime'] != mtime or _index_cache['stand'] != stand:
        roh = pfad.read_text(encoding='utf-8').replace('__STAND__', stand)
        _index_cache['data']  = roh.encode()
        _index_cache['gz']    = gzip.compress(_index_cache['data'], 6)
        _index_cache['mtime'] = mtime
        _index_cache['stand'] = stand
        # Der Stand gehoert in die Kennung: sonst bekommt ein Browser mit der
        # alten Kennung ein leeres 304 und behaelt die alte Seite.
        _index_cache['etag']  = f'"{int(mtime)}-{stand}-{len(_index_cache["data"])}"'
        # Die Wandfassung haengt an derselben Datei — mitbauen, statt sie beim
        # naechsten Abruf ein zweites Mal zu packen.
        wand = _index_cache['data'].decode('utf-8').replace(*_WAND_MANIFEST).encode()
        _wand_cache['data'] = wand
        _wand_cache['gz']   = gzip.compress(wand, 6)
        _wand_cache['etag'] = f'"wand-{_index_cache["etag"][1:-1]}"'
    return _ausliefern(req, _index_cache['data'], _index_cache['gz'],
                       _index_cache['etag'], 'text/html; charset=utf-8')


@app.get('/wand', include_in_schema=False)
async def wandseite(req: Request):
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
    await root(req)                       # fuellt beide Zwischenspeicher
    return _ausliefern(req, _wand_cache['data'], _wand_cache['gz'],
                       _wand_cache['etag'], 'text/html; charset=utf-8')



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
    # Der Live-Kanal traegt den ganzen Bordzustand und muss hier geprueft
    # werden: die Zugangs-Middleware sieht nur HTTP-Anfragen, keinen
    # WebSocket-Handschlag. Das Sitzungscookie geht beim Handschlag mit —
    # anders als ein Authorization-Kopf, den ein Browser dabei nicht setzen
    # kann. Genau deshalb liegt die Sitzung im Cookie.
    #
    # Solange keine Kontenkopie da ist, gilt dieselbe Schonfrist wie fuer die
    # uebrigen Aufrufe: eine Anlage, die sich nach einem Update selbst
    # aussperrt, waere schlimmer als eine offene im eigenen Bordnetz.
    # Die Herkunft gilt IMMER, auch in der Schonfrist: sie schuetzt nicht vor
    # Unbefugten, sondern davor, dass eine fremde Seite den Browser eines
    # Befugten benutzt. Das ist ohne Konten genauso moeglich wie mit.
    if not zg.herkunft_erlaubt(ws.headers.get('origin', ''),
                               ws.headers.get('host', '')):
        log.warning('Live-Kanal von fremder Herkunft abgewiesen: %r',
                    ws.headers.get('origin'))
        await ws.close(code=4403)
        return
    if not konten.leer:
        k = konten.konto_zu_token(ws.cookies.get(zg.SITZUNG_COOKIE) or '')
        if not rechte_modul.darf(k, rechte_modul.LESEN):
            await ws.close(code=4401)
            return
    await ws.accept()
    client = _WsClient(ws)
    client.start()
    ws_clients.add(client)
    log.info("WebSocket verbunden (%d aktiv)", len(ws_clients))
    try:
        client.send({**state.to_dict(), 'version': VERSION})
        while True:
            try:
                # Zehn Sekunden und nicht dreissig: die Oberflaeche erkennt am
                # AUSBLEIBEN dieser Nachricht, dass die Verbindung weg ist
                # (siehe _STILL_MS in ws.js). Mit dreissig haette sie erst nach
                # gut einer Minute etwas sagen koennen, ohne bei einem ruhigen
                # Boot falschen Alarm zu geben. Ein leerer Rahmen alle zehn
                # Sekunden je Zuschauer kostet nichts.
                await asyncio.wait_for(ws.receive_text(), timeout=10)
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
    if not can_if.send_brightness(werte):
        raise HTTPException(503, detail='Der CAN-Bus ist nicht verbunden — nichts geschaltet.')
    log.info("Preset %d '%s' aktiviert", preset_id, preset.get('name', ''))
    return {'ok': True, 'preset': preset.get('name', '')}


@app.post('/api/lights/channels')
async def set_channels(body: dict):
    values = _kanalwerte(body.get('values'))
    # Melden, ob es wirklich auf den Bus ging. Vorher kam immer {'ok': True} —
    # an Bord faellt das kaum auf, aus der Ferne bekaeme man eine Bestaetigung
    # fuer etwas, das nie passiert ist.
    if not can_if.send_brightness(values):
        raise HTTPException(503, detail='Der CAN-Bus ist nicht verbunden — nichts geschaltet.')
    state.lights['channels'] = values      # erst NACH dem Senden: sonst zeigt
    return {'ok': True}                    # die Anzeige einen Zustand, den es nicht gibt


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
        # Auch in den Mitschnitt: ein Fehler im Browser des Eigners taucht
        # sonst nirgends auf, wo man ihn spaeter noch findet.
        debug_log.merken('fehler', 'oberflaeche', str(entry.get('msg') or entry)[:400],
                         {'quelle': entry.get('src'), 'zeile': entry.get('line')})
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

@app.get('/api/debug/log')
async def debug_log_holen(stunden: float = Query(24, gt=0, le=24),
                          art: str = '', quelle: str = '',
                          suche: str = '', grenze: int = Query(400, ge=1, le=2000)):
    """Der Mitschnitt der letzten Stunden.

    Zum Nachsehen gedacht, nicht zum Zuschauen: wer live mitlesen will, nimmt
    journalctl auf dem Pi. Hierher kommt man, wenn etwas SCHON passiert ist.
    """
    debug_log.aufraeumen()
    return {
        'eintraege': debug_log.holen(seit=time.time() - stunden * 3600,
                                     art=art, quelle=quelle, suche=suche, grenze=grenze),
        'quellen': debug_log.quellen(),
        'gesamt': debug_log.anzahl(),
        'stunden': stunden,
    }


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
    gesendet = can_if.send_time(time.time())
    # Kein Fehler, wenn es nicht ging: die Bordzeit zu setzen ist eine
    # Bequemlichkeit, kein Schaltbefehl. Aber der Aufrufer soll es wissen,
    # statt zu glauben, die Uhr am Bus sei jetzt gestellt.
    return {'ok': bool(gesendet),
            'hinweis': None if gesendet else 'CAN-Bus nicht verbunden — Zeit nicht gesendet.'}


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
    # Nach einem Zurückgehen steht HEAD losgelöst, und dann scheitert `git
    # pull`. Erst zurück auf den Zweig — das ist zugleich der Weg, auf dem man
    # ein Zurückgehen wieder rückgängig macht.
    zweig = await loop.run_in_executor(
        None, _lauf, ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], 10)
    if zweig.stdout.strip() == 'HEAD':
        log.info('Losgelöster Stand — zurück auf master vor dem Aktualisieren')
        await loop.run_in_executor(None, _lauf, ['git', 'checkout', '--quiet', 'master'], 30)
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
        changelog = await loop.run_in_executor(None, _changelog, 'ORIG_HEAD..HEAD')
        asyncio.get_event_loop().call_later(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
    return {'ok': True, 'changed': changed, 'output': result.stdout.strip(),
            'version_before': before, 'version_after': after, 'changelog': changelog}


def _changelog(bereich: str, grenze: int = 40) -> list[dict]:
    """Das Änderungsverzeichnis für einen Commit-Bereich.

    Eine Commit-Nachricht hat ZWEI Leser mit verschiedenen Bedürfnissen: den
    Entwickler, der in einem Jahr wissen will, WARUM etwas so gebaut wurde, und
    den Eigner, der wissen will, WAS sich für ihn ändert. Beides in einen Text
    zu pressen macht ihn für beide schlecht — vorher landete der ganze
    Fliesstext im Änderungsverzeichnis.

    Deshalb die Trennung: Zeilen, die mit "* " beginnen, sind die kurze Fassung
    für den Eigner. Alles andere im Rumpf bleibt Begründung und wird hier nicht
    gezeigt. Fehlen solche Zeilen (ältere Commits), bleibt es bei der
    Überschrift — die ist ohnehin die Kurzfassung.
    """
    try:
        # %x00 als Trenner und --name-only fuer die Dateien: daraus faellt ab,
        # WELCHEN Teil eine Aenderung betrifft — Bordansicht oder Logbuch.
        erg = _lauf(['git', 'log', bereich, '--no-merges', f'-n{grenze}',
                     '--name-only',
                     '--pretty=format:EINTRAG%x00%H%x00%at%x00%s%x00%b%x00DATEIEN'], 15)
    except Exception:
        return []

    nummern = _versionsnummern(bereich, grenze)
    raus = []
    for block in erg.stdout.split('EINTRAG\x00'):
        if not block.strip():
            continue
        teile = block.split('\x00')
        if len(teile) < 4:
            continue
        hash_, zeit, titel = teile[0], teile[1], teile[2]
        # Alles ab Teil 3 wieder zusammensetzen: der Rumpf kann selbst
        # Nullbytes enthalten, und die Dateiliste haengt git NACH dem Format an
        # — nur teile[3] zu nehmen schnitt sie ab, und die Bereiche blieben
        # deshalb leer.
        rest = '\x00'.join(teile[3:]) if len(teile) > 3 else ''
        rumpf, _, dateien = rest.partition('DATEIEN')
        punkte = [z.strip()[2:].strip() for z in rumpf.splitlines()
                  if z.strip().startswith('* ')]
        raus.append({
            'hash': hash_.strip()[:10],
            'zeit': float(zeit) if zeit.strip().isdigit() else None,
            'titel': titel.strip(),
            'punkte': punkte,
            'bereiche': _bereiche(dateien),
            'version': nummern.get(hash_.strip()[:10]),
        })
    return raus


def _bereiche(dateien: str) -> list[str]:
    """Welchen Teil der Anlage eine Aenderung betrifft.

    Abgeleitet aus den geaenderten Dateien, nicht aus dem Text: der Text kann
    luegen, die Dateiliste nicht. Es geht um die Frage "muss ich hinsehen?" —
    wer nur die Bordansicht benutzt, interessiert sich nicht fuer eine
    Aenderung am Logbuch.
    """
    b = set()
    for zeile in (dateien or '').splitlines():
        d = zeile.strip()
        if not d:
            continue
        if 'diagnose' in d:
            b.add('Logbuch')
        elif d.startswith('server/'):
            b.add('Server')
        elif d.startswith('static/') or d == 'main.py':
            b.add('Bordansicht')
        elif d.startswith('sync/') or d in ('konten_speicher.py', 'debug_log.py'):
            b.add('Grundlagen')
    return sorted(b)


def _versionsnummern(bereich: str, grenze: int) -> dict:
    """Je Commit eine Versionsnummer, fortlaufend gezaehlt.

    Bisher trug nur ein Stand, der genau auf einem git-Tag lag, eine Nummer —
    alle anderen hiessen gleich. Gezaehlt wird deshalb ab dem letzten Tag: die
    dritte Stelle ist die Anzahl der Aenderungen seither. Das braucht keine
    Pflege, steigt monoton und benennt jeden Stand eindeutig.
    """
    try:
        letzter_tag = _lauf(['git', 'describe', '--tags', '--abbrev=0'], 8).stdout.strip()
        if not letzter_tag:
            return {}
        basis = letzter_tag.lstrip('v')
        haupt, _, _ = basis.rpartition('.')
        seit = _lauf(['git', 'rev-list', '--count', f'{letzter_tag}..HEAD'], 8).stdout.strip()
        gesamt = int(seit) if seit.isdigit() else 0
        # Die Reihenfolge von `git log` ist neueste zuerst und linear (ein
        # Zweig, ohne Zusammenfuehrungen) — der i-te Eintrag liegt also genau
        # i Aenderungen hinter dem neuesten.
        hashes = _lauf(['git', 'log', bereich, '--no-merges', f'-n{grenze}',
                        '--pretty=format:%H'], 10).stdout.split()
        raus = {}
        for i, h in enumerate(hashes):
            nr = gesamt - i
            if nr >= 0:
                raus[h[:10]] = f'{haupt}.{nr}'
        return raus
    except Exception:
        return {}


@app.post('/api/system/zurueck')
async def system_zurueck(body: dict):
    """Auf eine frühere Fassung zurückgehen.

    Erlaubt sind AUSSCHLIESSLICH die letzten acht Commits — nicht jeder
    beliebige Stand. Zwei Gründe: je weiter zurück, desto weniger passt die
    Fassung zu den Daten auf der Platte (Kontendatei, Verlaufsformat), und ein
    Feld, in das man einen beliebigen Commit schreiben kann, ist ein Feld, über
    das sich beliebiger Code starten lässt.

    Gearbeitet wird mit `checkout`, nicht mit `reset --hard`: der Zweig master
    bleibt dabei unangetastet, und "wieder aktuell machen" ist danach einfach
    das gewöhnliche Aktualisieren.
    """
    ziel = str((body or {}).get('hash') or '').strip()
    if not ziel or not all(c in '0123456789abcdef' for c in ziel.lower()):
        raise HTTPException(400, detail='Kein brauchbarer Stand angegeben.')

    loop = asyncio.get_running_loop()
    erlaubt = await loop.run_in_executor(None, _changelog, 'HEAD', 8)
    treffer = [e for e in erlaubt if e['hash'].startswith(ziel.lower()[:10])]
    if not treffer:
        raise HTTPException(
            400, detail='Dieser Stand liegt zu weit zurück. Möglich sind die letzten acht.')

    vorher = _git_hash()
    erg = await loop.run_in_executor(None, _lauf, ['git', 'checkout', '--quiet', ziel], 30)
    if erg.returncode != 0:
        log.error('Zurückgehen fehlgeschlagen: %s', erg.stderr.strip())
        raise HTTPException(500, detail='Zurückgehen fehlgeschlagen — Details im Log.')

    log.warning('Auf Stand %s zurückgegangen (vorher %s): %s',
                ziel[:10], vorher, treffer[0]['titel'])
    # Wie beim Aktualisieren: der Dienst beendet sich, systemd startet ihn neu.
    asyncio.get_event_loop().call_later(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
    return {'ok': True, 'vorher': vorher, 'jetzt': ziel[:10],
            'titel': treffer[0]['titel']}


# ── Push ────────────────────────────────────────────────────────────────────
# Im Bordnetz laeuft die Anwendung auf DIESEM Rechner, ein Geraet meldet sich
# also hier an. Senden kann nur der Server: ein Push-Abo zeigt auf den Dienst
# des Browserherstellers, und dorthin kommt man nur mit Internet. Der Pi nimmt
# das Abo entgegen und reicht es weiter — so wie er es mit Anmeldungen auch tut.

@app.get('/api/push/schluessel')
async def push_schluessel():
    """Der oeffentliche Schluessel des Servers, aus dem Handschlag."""
    schluessel = getattr(sync, 'push_schluessel', '') or ''
    return {'bereit': bool(schluessel), 'schluessel': schluessel,
            'grund': '' if schluessel else 'Der Server ist gerade nicht verbunden.'}


@app.post('/api/push/anmelden')
async def push_anmelden(daten: dict, request: Request):
    konto = getattr(request.state, 'konto', None) or {}
    try:
        await sync.push_melden(daten.get('abo') or daten, konto.get('name', ''),
                               zg.geraet_aus_ua(request.headers.get('user-agent', '')))
    except Exception as e:
        raise HTTPException(503, detail=str(e)) from None
    return {'ok': True}


@app.post('/api/push/abmelden')
async def push_abmelden(daten: dict, request: Request):
    konto = getattr(request.state, 'konto', None) or {}
    try:
        await sync.push_melden(daten.get('abo') or {}, konto.get('name', ''),
                               abmelden=True)
    except Exception as e:
        raise HTTPException(503, detail=str(e)) from None
    return {'ok': True}


@app.get('/api/stand')
async def oberflaechen_stand():
    """Mit welchem Stand die Oberflaeche gerade ausgeliefert wuerde.

    Winzig und ohne Anmeldung: die Seite fragt ihn im Takt und vergleicht ihn
    mit dem, der bei ihrer Auslieferung eingesetzt wurde. Weichen sie ab, laeuft
    im Browser eine alte Fassung — aus dem Zwischenspeicher oder aus einem Tab,
    der seit Tagen offen ist. Verraten wird dabei nichts: eine Commit-Kennung
    sagt nichts ueber das Boot.
    """
    return {'stand': _git_hash() or '?'}


@app.get('/api/system/versionen')
async def system_versionen():
    """Was installiert ist, was bereitliegt, und wohin sich zurückgehen lässt.

    `bereit` ist das Änderungsverzeichnis der Fassung, die noch NICHT
    eingespielt ist — damit man vor dem Aktualisieren sieht, was kommt. Genau
    dafür wird vorher `git fetch` gemacht, ohne etwas zu verändern.
    """
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _lauf, ['git', 'fetch', '--quiet'], 25)
    except Exception as e:
        log.warning('git fetch fehlgeschlagen: %s', e)
    # 'HEAD', nicht 'HEAD -n8': der Bereich ist EIN Argument fuer git, die
    # Anzahl hat ihren eigenen Schalter. Zusammengeschrieben versteht git
    # beides nicht und liefert stillschweigend nichts.
    jetzt = await loop.run_in_executor(None, _changelog, 'HEAD', 8)
    bereit = await loop.run_in_executor(None, _changelog, 'HEAD..origin/master')
    return {
        'installiert': _git_hash(),
        'version': VERSION,
        # Die jüngsten acht: mehr braucht niemand, und je weiter zurück, desto
        # weniger passt die Fassung zu den Daten auf der Platte.
        'verlauf': jetzt,
        'bereit': bereit,
    }


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
    if not can_if.send_inverter_mode(int(mode)):
        raise HTTPException(503, detail='Der CAN-Bus ist nicht verbunden — nichts geschaltet.')
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


# Was der Router ueber seine eigene Gesundheit sagt. Die Pfade sind KANDIDATEN
# und keine Zusicherung: RutOS benennt seine Endpunkte zwischen Fassungen um,
# und was auf einem RUTX50 antwortet, muss es auf dem naechsten nicht. Deshalb
# wird probiert und berichtet, was zurueckkommt — statt eine Liste zu pflegen,
# die auf dem Papier stimmt und am Geraet nicht.
_ROUTER_DIAGNOSE_PFADE = (
    # Laufzeit und Neustarts. Die wichtigste Zahl ueberhaupt: eine Laufzeit,
    # die kleiner ist als der Abstand zur letzten Abfrage, IST ein Neustart.
    '/api/system/device/status',
    '/api/system/device/usage/status',
    '/api/system/status',
    '/api/system/reboot/status',
    # Speicher und Waerme — die beiden ueblichen Gruende, aus denen ein kleines
    # Geraet von selbst neu startet.
    '/api/system/device/memory/status',
    '/api/system/device/temperature/status',
    '/api/system/device/fw/status',
    # Das Protokoll. Hier steht, WARUM er neu gestartet ist, falls er es sagt.
    '/api/system/events/status',
    '/api/system/logs/status',
    '/api/system/log/status',
    # Funk: die Radios (2,4 und 5 GHz) und die darauf gehosteten Netze. Genau
    # hier faellt auf, wenn nach einem Neustart ein Netz nicht wiederkommt.
    '/api/wireless/devices/status',
    '/api/wireless/interfaces/status',
    '/api/wireless/access_points/config',
    '/api/wireless/devices/config',
)


@app.get('/api/debug/router-diagnose')
async def debug_router_diagnose(kurz: bool = True):
    """Was der Router ueber sich selbst hergibt — zum Nachsehen, warum er faellt.

    Ein reiner Leseaufruf; er stellt nichts um. Gedacht fuer die Frage des
    Eigners: der RUTX50 startet staendig neu, und danach fehlt ein WLAN.

    `kurz=false` liefert die vollen Antworten. In der Vorgabe werden lange
    Nutzlasten (Protokolle) gekuerzt — sonst kommt ein Megabyte JSON durch die
    Mobilfunkleitung, und lesen kann man es dann immer noch nicht.
    """
    if not conn_mon:
        raise HTTPException(503, detail='Connectivity-Monitor nicht konfiguriert')
    hdrs = conn_mon._token_headers()
    raus: dict = {'zeit': time.time(), 'pfade': {}}
    for pfad in _ROUTER_DIAGNOSE_PFADE:
        try:
            antwort = conn_mon._http(conn_mon._router_host + pfad, headers=hdrs)
            daten = antwort.get('data') if isinstance(antwort, dict) else antwort
            if kurz:
                daten = _diagnose_kuerzen(daten)
            raus['pfade'][pfad] = {'ok': True, 'data': daten}
        except Exception as e:
            # Ein 404 ist hier KEIN Fehler, sondern das Ergebnis: diesen Pfad
            # gibt es auf dieser Fassung nicht.
            raus['pfade'][pfad] = {'ok': False, 'fehler': str(e)}
    return raus


def _diagnose_kuerzen(daten, tiefe: int = 0):
    """Lange Listen und Texte stutzen, Struktur behalten.

    Es geht darum zu sehen, WELCHE Felder es gibt und wie sie aussehen. Die
    letzten Eintraege eines Protokolls sind dabei die interessanten — bei einem
    Absturz steht das Entscheidende am Ende.
    """
    if isinstance(daten, dict):
        return {k: _diagnose_kuerzen(v, tiefe + 1) for k, v in daten.items()}
    if isinstance(daten, list):
        if len(daten) > 25:
            return ([f'… {len(daten) - 25} weitere davor …']
                    + [_diagnose_kuerzen(x, tiefe + 1) for x in daten[-25:]])
        return [_diagnose_kuerzen(x, tiefe + 1) for x in daten]
    if isinstance(daten, str) and len(daten) > 400:
        return daten[:400] + f' … (+{len(daten) - 400} Zeichen)'
    return daten


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


# ── Grundriss ────────────────────────────────────────────────────────────────
# Der Grundriss des Bootes: Rumpfumriss, Raeume, und was sonst noch gezeichnet
# ist. Er stand bis hierher als 250 Zeilen SVG in index.html, dazu ein zweites
# Mal als Tabelle in orte.js und ein drittes Mal in stauplan.js — drei Kopien
# desselben Bootes, von denen jede fuer sich altern konnte.
#
# Jetzt ist er eine Datei. Das ist die Voraussetzung dafuer, ihn ueberhaupt
# bearbeiten zu koennen: was im Programmtext steht, kann niemand einzeichnen.
#
# WARUM HIER SO GENAU GEPRUEFT WIRD: der Inhalt wird im Browser zu SVG. Wer
# schreiben darf, koennte sonst Zeichenketten unterbringen, die im Dokument
# etwas anderes tun als zeichnen. Deshalb kommt nichts durch, was nicht in
# dieses Raster passt: bekannte Formen, Zahlen in Grenzen, Farben nach Muster,
# Pfaddaten nur aus den Zeichen, die eine Pfadangabe braucht. Texte werden im
# Browser ueber textContent gesetzt, nie ueber innerHTML.

_GR_FORMEN = {
    'rect':    ('x', 'y', 'w', 'h', 'rx', 'ry'),
    'line':    ('x1', 'y1', 'x2', 'y2'),
    'circle':  ('cx', 'cy', 'r'),
    'ellipse': ('cx', 'cy', 'rx', 'ry'),
    'path':    (),
    'text':    ('x', 'y', 'fs'),
}
_GR_ZAHL_FELDER = {'x', 'y', 'w', 'h', 'rx', 'ry', 'x1', 'y1', 'x2', 'y2',
                   'cx', 'cy', 'r', 'fs', 'sw'}
_GR_TEXT_FELDER = {'fill', 'stroke', 'anker', 'fw', 'ls', 'strich', 's', 'd', 'ff'}
_GR_FARBE   = re.compile(r'^(#[0-9a-fA-F]{6}|none|transparent|schraffur)$')
_GR_PFAD    = re.compile(r'^[MmLlHhVvCcSsQqTtAaZz0-9\s,.+-]{1,4000}$')
_GR_EINFACH = re.compile(r'^[\w .,%-]{0,32}$')       # Anker, Strichmuster, ...
# Schriftfamilien sind Listen und damit laenger — dieselben Zeichen, mehr davon.
_GR_SCHRIFT = re.compile(r'^[\w .,\'"-]{0,64}$')


def _gr_zahl(wert, name: str) -> float:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        raise HTTPException(400, detail=f'{name}: Zahl erwartet')
    # Grosszuegig, aber begrenzt: die Zeichenflaeche ist ein paar hundert
    # Einheiten gross, alles darueber ist ein Tippfehler oder Absicht.
    if not -10000 <= wert <= 10000:
        raise HTTPException(400, detail=f'{name}: {wert} liegt ausserhalb von -10000 bis 10000')
    return round(float(wert), 3)


def _gr_farbe(wert, name: str) -> str:
    wert = _text(wert, 32, name)
    if not _GR_FARBE.match(wert):
        raise HTTPException(400, detail=f'{name}: {wert!r} ist keine erlaubte Farbe')
    return wert


def _gr_form(roh, name: str) -> dict:
    if not isinstance(roh, dict):
        raise HTTPException(400, detail=f'{name}: Objekt erwartet')
    t = _text(roh.get('t', ''), 12, f'{name}.t')
    if t not in _GR_FORMEN:
        raise HTTPException(400, detail=f'{name}.t: {t!r} ist keine bekannte Form')
    aus = {'t': t}
    for feld, wert in roh.items():
        if feld == 't':
            continue
        if feld in _GR_ZAHL_FELDER:
            aus[feld] = _gr_zahl(wert, f'{name}.{feld}')
        elif feld in ('fill', 'stroke'):
            aus[feld] = _gr_farbe(wert, f'{name}.{feld}')
        elif feld == 'd':
            d = _text(wert, 4000, f'{name}.d')
            if not _GR_PFAD.match(d):
                raise HTTPException(400, detail=f'{name}.d: unerlaubte Zeichen in der Pfadangabe')
            aus['d'] = d
        elif feld == 's':
            aus['s'] = _text(wert, 120, f'{name}.s')
        elif feld == 'frei':
            # Liegt die Form ausserhalb des Rumpfumrisses? Dann wird sie nicht
            # beschnitten — die Beschriftungen "Bug" und "Heck" stehen neben
            # dem Boot, nicht darin.
            aus['frei'] = bool(wert)
        elif feld in _GR_TEXT_FELDER:
            # Schriftfamilien sind laenger als die uebrigen Angaben.
            v = _text(wert, 64 if feld == 'ff' else 32, f'{name}.{feld}')
            if not (_GR_SCHRIFT if feld == 'ff' else _GR_EINFACH).match(v):
                raise HTTPException(400, detail=f'{name}.{feld}: unerlaubte Zeichen')
            aus[feld] = v
        else:
            raise HTTPException(400, detail=f'{name}: unbekanntes Feld {feld!r}')
    return aus


def _gr_raum(roh, name: str) -> dict:
    if not isinstance(roh, dict):
        raise HTTPException(400, detail=f'{name}: Objekt erwartet')
    kennung = _text(roh.get('id', ''), 40, f'{name}.id').strip()
    if not re.match(r'^[a-z0-9][a-z0-9_-]{0,39}$', kennung):
        raise HTTPException(400, detail=f'{name}.id: nur Kleinbuchstaben, Ziffern, - und _')
    form = roh.get('form') or {}
    if not isinstance(form, dict):
        raise HTTPException(400, detail=f'{name}.form: Objekt erwartet')
    art = _text(form.get('t', ''), 12, f'{name}.form.t')
    if art == 'rechteck':
        geprueft = {'t': 'rechteck'}
        for feld in ('x', 'y', 'w', 'h'):
            geprueft[feld] = _gr_zahl(form.get(feld), f'{name}.form.{feld}')
    elif art == 'vieleck':
        punkte = form.get('punkte')
        if not isinstance(punkte, list) or not 3 <= len(punkte) <= 200:
            raise HTTPException(400, detail=f'{name}.form.punkte: 3 bis 200 Punkte erwartet')
        geprueft = {'t': 'vieleck', 'punkte': [
            [_gr_zahl((pt or [None, None])[0], f'{name}.form.punkte[{i}].x'),
             _gr_zahl((pt or [None, None])[1], f'{name}.form.punkte[{i}].y')]
            for i, pt in enumerate(punkte)]}
    else:
        raise HTTPException(400, detail=f'{name}.form.t: rechteck oder vieleck erwartet')
    return {
        'id': kennung,
        'name': _text(roh.get('name', ''), 60, f'{name}.name').strip() or kennung,
        'farbe': _gr_farbe(roh.get('farbe', '#94a3b8'), f'{name}.farbe'),
        'form': geprueft,
    }


def _grundriss_pruefen(body) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(400, detail='Grundriss: Objekt erwartet')
    ansicht = body.get('ansicht') or {}
    if not isinstance(ansicht, dict):
        raise HTTPException(400, detail='ansicht: Objekt erwartet')
    rumpf = _text(body.get('rumpf', ''), 4000, 'rumpf')
    if rumpf and not _GR_PFAD.match(rumpf):
        raise HTTPException(400, detail='rumpf: unerlaubte Zeichen in der Pfadangabe')
    raeume = body.get('raeume') or []
    if not isinstance(raeume, list) or len(raeume) > 200:
        raise HTTPException(400, detail='raeume: Liste mit hoechstens 200 Eintraegen erwartet')
    hintergrund = body.get('hintergrund') or []
    if not isinstance(hintergrund, list) or len(hintergrund) > 2000:
        raise HTTPException(400, detail='hintergrund: Liste mit hoechstens 2000 Formen erwartet')
    geprueft_raeume = [_gr_raum(r, f'raeume[{i}]') for i, r in enumerate(raeume)]
    kennungen = [r['id'] for r in geprueft_raeume]
    doppelt = {k for k in kennungen if kennungen.count(k) > 1}
    if doppelt:
        raise HTTPException(400, detail=f'raeume: Kennung mehrfach vergeben: {sorted(doppelt)}')
    geprueft = {
        'name':  _text(body.get('name', ''), 60, 'name').strip(),
        'loa_m': _gr_zahl(body.get('loa_m', 0) or 0, 'loa_m'),
        'breite_m': _gr_zahl(body.get('breite_m', 0) or 0, 'breite_m'),
        'ansicht': {'w': _gr_zahl(ansicht.get('w', 200), 'ansicht.w'),
                    'h': _gr_zahl(ansicht.get('h', 680), 'ansicht.h')},
        'rumpf': rumpf,
        'hintergrund': [_gr_form(f, f'hintergrund[{i}]') for i, f in enumerate(hintergrund)],
        'raeume': geprueft_raeume,
    }
    # Wo die Planvorlage liegt und wie stark sie durchscheint. Nur Zahlen — das
    # BILD selbst steht nicht hier drin, sondern in einer eigenen Datei; ein
    # halbes Megabyte Base64 in der Antwort holt sich sonst jede Seite mit,
    # die nur die Raumnamen braucht.
    bild = body.get('bild')
    if isinstance(bild, dict):
        geprueft['bild'] = {
            'x': _gr_zahl(bild.get('x', 0) or 0, 'bild.x'),
            'y': _gr_zahl(bild.get('y', 0) or 0, 'bild.y'),
            'w': _gr_zahl(bild.get('w', 0) or 0, 'bild.w'),
            'h': _gr_zahl(bild.get('h', 0) or 0, 'bild.h'),
            'deckkraft': min(1.0, max(0.0, _gr_zahl(
                bild.get('deckkraft', .5), 'bild.deckkraft'))),
        }
    return geprueft


@app.get('/api/grundriss')
async def get_grundriss():
    """Der Grundriss — oder die mitgelieferte Vorlage, solange keiner geladen ist.

    GEZEICHNET wird er nicht hier, sondern im Logbuch auf dem Server (siehe
    KONZEPT-GRUNDRISS.md). Am Boot kommt er als Datei an, ueber die
    Einstellungen oder ueber den Durchleiter des Servers.

    Wie devices.json: die echte Datei ist Laufzeitdatei und gehoert nicht ins
    Repo (sonst scheitert am Pi jeder `git pull`). Damit ein frisches Geraet
    trotzdem nicht mit einer leeren Flaeche dasteht, liegt eine Vorlage daneben.
    """
    daten = read_json(GRUNDRISS_FILE, {})
    if not daten:
        daten = read_json(GRUNDRISS_VORLAGE, {})
    return daten


@app.put('/api/grundriss')
async def save_grundriss(request: Request):
    geprueft = _grundriss_pruefen(await _json_body(request))
    await _run_blocking(write_json, GRUNDRISS_FILE, geprueft)
    return geprueft


# Bekannte Schluessel je Abschnitt. Unbekannte werden mit 400 abgelehnt, damit
# presets.json nicht bei jedem Tippfehler um einen Eintrag waechst — geloescht
# wird dabei nichts, was schon in der Datei steht.
_SETTINGS_ABSCHNITTE = ('tanks', 'devices', 'batteries', 'wartung', 'lights',
                        'wetter', 'pegel')
_TANK_FELDER         = ('name', 'capacity_l', 'color')
_LICHT_FELDER        = ('name',)
_WETTER_FELDER       = ('orte', 'modell')
_WETTER_ORT_FELDER   = ('name', 'lat', 'lon')
_WETTER_ORTE_MAX     = 5
_PEGEL_FELDER        = ('stationen',)
_PEGEL_ST_FELDER     = ('name', 'uuid')
_PEGEL_MAX           = 5
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

    if 'lights' in body:
        # Namen der Lichtkreise. Sie standen bis hierher fest im Skript
        # (CH_NAMES, _WIDE_LABELS) — an zwei Stellen, in zwei Laengen, und beide
        # nur durch ein Update zu aendern. Ein Kanalname ist aber eine Angabe
        # ueber DIESES Boot, kein Programmtext.
        #
        # Schluessel ist die Kanalnummer 0..8 als Zeichenkette: acht
        # PWM-Kanaele und das Relais. Ein leerer Name loescht den Eintrag —
        # dann greift wieder die Vorgabe in der Oberflaeche.
        if not isinstance(body['lights'], dict):
            raise HTTPException(400, detail='lights: Objekt erwartet')
        for key, val in body['lights'].items():
            try:
                kanal = int(key)
            except (TypeError, ValueError):
                raise HTTPException(400, detail=f'lights: {key!r} ist keine '
                                                f'Kanalnummer') from None
            if not 0 <= kanal <= 8:
                raise HTTPException(400, detail=f'lights: Kanal {kanal} liegt '
                                                f'ausserhalb von 0 bis 8')
            if not isinstance(val, dict):
                raise HTTPException(400, detail=f'lights.{key}: Objekt erwartet')
            _unbekannt_ablehnen(val, _LICHT_FELDER, f'lights.{key}')
            ziel = data.setdefault('lights', {})
            name = _text(val.get('name', ''), 32, f'lights.{key}.name').strip()
            if name:
                ziel.setdefault(str(kanal), {})['name'] = name
            else:
                ziel.pop(str(kanal), None)

    if 'wetter' in body:
        # Bis zu fuenf Orte, dazu das Rechenmodell. Warum eine Liste und keine
        # einzelne Angabe: die Frage vor dem Ablegen ist selten "wie wird es
        # hier", sondern "wie wird es DORT, wo ich hinwill" — und zwischen
        # Liegeplatz, Ziel und der Ecke dazwischen schaltet man hin und her.
        #
        # Die aktuelle Position steht NICHT in dieser Liste. Sie kommt vom
        # Router und ist keine Einstellung; die Oberflaeche haengt sie beim
        # Durchschalten von selbst an.
        if not isinstance(body['wetter'], dict):
            raise HTTPException(400, detail='wetter: Objekt erwartet')
        _unbekannt_ablehnen(body['wetter'], _WETTER_FELDER, 'wetter')
        ziel = data.setdefault('wetter', {})
        if not isinstance(ziel, dict):
            ziel = data['wetter'] = {}

        if 'modell' in body['wetter']:
            modell = _text(body['wetter']['modell'], 24, 'wetter.modell').strip()
            if modell not in WX_MODELLE:
                raise HTTPException(400, detail=f'wetter.modell: unbekanntes '
                                                f'Modell {modell!r}')
            ziel['modell'] = modell

        if 'orte' in body['wetter']:
            roh = body['wetter']['orte']
            if not isinstance(roh, list):
                raise HTTPException(400, detail='wetter.orte: Liste erwartet')
            if len(roh) > _WETTER_ORTE_MAX:
                raise HTTPException(400, detail=f'wetter.orte: hoechstens '
                                                f'{_WETTER_ORTE_MAX} Orte')
            orte = []
            for i, eintrag in enumerate(roh):
                if not isinstance(eintrag, dict):
                    raise HTTPException(400, detail=f'wetter.orte[{i}]: Objekt erwartet')
                _unbekannt_ablehnen(eintrag, _WETTER_ORT_FELDER, f'wetter.orte[{i}]')
                name = _text(eintrag.get('name', ''), 40, f'wetter.orte[{i}].name').strip()
                if not name:
                    raise HTTPException(400, detail=f'wetter.orte[{i}].name: fehlt')
                orte.append({
                    'name': name,
                    'lat': round(_zahl(eintrag.get('lat'), -90, 90, f'wetter.orte[{i}].lat'), 4),
                    'lon': round(_zahl(eintrag.get('lon'), -180, 180, f'wetter.orte[{i}].lon'), 4),
                })
            ziel['orte'] = orte

    if 'pegel' in body:
        # Dieselbe Ueberlegung wie beim Wetter: der Heimatpegel beantwortet
        # nicht, ob man im Zielhafen noch ueber die Schwelle kommt. Anders als
        # beim Wetter ist der Pegel aber KEINE Koordinate, sondern eine
        # Messstelle mit eigener Kennung — die Zahl allein saehe an der
        # Nachbarmole schon anders aus.
        if not isinstance(body['pegel'], dict):
            raise HTTPException(400, detail='pegel: Objekt erwartet')
        _unbekannt_ablehnen(body['pegel'], _PEGEL_FELDER, 'pegel')
        if 'stationen' in body['pegel']:
            roh = body['pegel']['stationen']
            if not isinstance(roh, list):
                raise HTTPException(400, detail='pegel.stationen: Liste erwartet')
            if len(roh) > _PEGEL_MAX:
                raise HTTPException(400, detail=f'pegel.stationen: hoechstens '
                                                f'{_PEGEL_MAX} Pegel')
            stationen = []
            for i, eintrag in enumerate(roh):
                if not isinstance(eintrag, dict):
                    raise HTTPException(400, detail=f'pegel.stationen[{i}]: Objekt erwartet')
                _unbekannt_ablehnen(eintrag, _PEGEL_ST_FELDER, f'pegel.stationen[{i}]')
                name = _text(eintrag.get('name', ''), 48,
                             f'pegel.stationen[{i}].name').strip()
                uuid = _text(eintrag.get('uuid', ''), 64,
                             f'pegel.stationen[{i}].uuid').strip()
                if not name or not uuid:
                    raise HTTPException(400, detail=f'pegel.stationen[{i}]: '
                                                    f'Name und Kennung noetig')
                # Die Kennung geht in eine URL. Nur das Format, das
                # pegelonline vergibt — sonst laesst sich darueber ein
                # beliebiger Pfad anhaengen.
                if not re.fullmatch(r'[0-9a-fA-F-]{8,64}', uuid):
                    raise HTTPException(400, detail=f'pegel.stationen[{i}].uuid: '
                                                    f'keine Pegel-Kennung')
                stationen.append({'name': name, 'uuid': uuid})
            ziel = data.setdefault('pegel', {})
            if not isinstance(ziel, dict):
                ziel = data['pegel'] = {}
            ziel['stationen'] = stationen

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
    'target_soc':              ( 0.0, 100.0),
    'soc_hysteresis_pct':      ( 0.0, 50.0),
    # Selbstermittlung der Haltespannung
    'hold_min_v':              (10.0, 60.0),
    'hold_step_v':             ( 0.001, 1.0),
    'hold_settle_h':           ( 0.1, 48.0),
    'hold_quiet_a':            ( 0.0, 200.0),
    'hold_interval_h':         ( 0.1, 720.0),
    'balance_interval_days':   ( 1.0, 365.0),
    # Balance-Lauf
    'start_soc':               ( 0.0, 100.0),
    'ziel_soc':                ( 0.0, 100.0),
    'strom_a':                 ( 0.5, 200.0),
    'start_v':                 (10.0, 60.0),
    'max_v':                   (10.0, 60.0),
    'schritt_v':               ( 0.005, 1.0),
    'zelldiff_mv':             ( 1.0, 500.0),
    'schritt_min':             ( 1.0, 600.0),
    'halten_h':                ( 0.1, 48.0),
    'max_h':                   ( 1.0, 168.0),
    'max_current_a':           ( 0.5, 200.0),
    'solar_priority_offset_v': ( 0.0, 5.0),
    'profile_id':              ( 1.0, 5.0),
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
        # Auch in Listen absteigen: die Ladeprofile stehen als Liste von
        # Objekten im Body. Ohne das waeren genau ihre Spannungen ungeprueft —
        # also die Werte, die am Ende im Lader landen.
        if isinstance(wert, list):
            for i, eintrag in enumerate(wert):
                if isinstance(eintrag, dict):
                    _pruefe_ladewerte(eintrag, f'{voll}[{i}]')
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
    # hold_auto steuert, ob der Regler selbst an den Ladespannungen dreht —
    # das muss ein echter Wahrheitswert sein. Ein Text waere in Python wahr,
    # und ein versehentliches "false" haette die Selbstermittlung eingeschaltet.
    for profil in ('harbor',):
        p = body.get(profil)
        if isinstance(p, dict) and 'hold_auto' in p and not isinstance(p['hold_auto'], bool):
            raise HTTPException(400, detail=f'{profil}.hold_auto: true oder false erwartet')
    # Absorption darf nicht unter Float liegen — das ergibt kein sinnvolles Ladeprofil.
    for profil in ('harbor', 'full', 'balance'):
        p = body.get(profil)
        if isinstance(p, dict):
            a, f = p.get('absorption_v'), p.get('float_v')
            if isinstance(a, (int, float)) and isinstance(f, (int, float)) and a < f:
                raise HTTPException(400, detail=f'{profil}: absorption_v ({a}) darf nicht '
                                                f'unter float_v ({f}) liegen')
    # Dasselbe fuer die Ladeprofile. Der Regler kappt die Erhaltung zwar ohnehin
    # auf die Absorption, aber eine stumme Korrektur ist die schlechtere Antwort
    # als eine klare Fehlermeldung an den, der es gerade eintraegt.
    liste = body.get('profile')
    if isinstance(liste, list):
        for i, pr in enumerate(liste):
            if not isinstance(pr, dict):
                continue
            a, f = pr.get('absorption_v'), pr.get('float_v')
            if isinstance(a, (int, float)) and isinstance(f, (int, float)) and a < f:
                raise HTTPException(400, detail=f'profile[{i}]: absorption_v ({a}) darf '
                                                f'nicht unter float_v ({f}) liegen')
    return charge_ctrl.update_settings(body)


@app.post('/api/charger/poll')
async def poll_charger():
    """Sofortiger ISO-Request für PGN 130914 → liest aktuelle Setpoints vom IP43."""
    can_if.send_charger_config_request()
    return {'ok': True}


# ── Wasserstand Travemünde (pegelonline.wsv.de + BSH-Prognose) ───────────────

# Je Pegel ein Eintrag — wer zwischen seinen Pegeln durchschaltet, soll nicht
# bei jedem Wechsel warten.
_wl_cache: dict  = {}
_bsh_cache: dict = {}
# Der Pegelnullpunkt aendert sich alle paar Jahrzehnte (Travemuende zuletzt
# 2019). Einmal je Lauf holen genuegt.
_gz_cache: dict  = {}

_PEGEL_BASIS = 'https://www.pegelonline.wsv.de/webservices/rest-api/v2'
_BSH_BASIS   = 'https://www2.bsh.de/aktdat/wvd/ostsee/modellkurve'
# Vorgabe, solange nichts gepflegt ist. Der Heimatpegel dieses Bootes.
_PEGEL_VORGABE = {'name': 'Travemünde',
                  'uuid': 'c7383149-1f77-430d-8bef-c5667be3846b'}
_WL_PNP_M = -5.025          # Rueckfall: Pegelnullpunkt Travemünde in m über NHN
_WL_ALARM_NHN_CM = -60      # Alarm wenn Prognose-Minimum unter diesem Wert


def _bsh_dateiname(name: str) -> str:
    """Aus dem Pegelnamen den Namen der BSH-Grafik.

    Das BSH schreibt seine Dateien in Titelschreibweise ohne Umlaute:
    TRAVEMÜNDE -> Travemuende, WARNEMÜNDE -> Warnemuende. Geprueft an allen
    29 Ostseepegeln von pegelonline — die drei, fuer die es ueberhaupt eine
    Kurve gibt, treffen damit.
    """
    t = (name or '').strip().lower()
    for a, b in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        t = t.replace(a, b)
    return '-'.join(w.capitalize() for w in t.replace('-', ' ').split())


def _bsh_url(name: str) -> str:
    return f'{_BSH_BASIS}/WVD_{urllib.parse.quote(_bsh_dateiname(name))}.png'


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


def _fetch_bsh_forecast(name: str) -> dict:
    """Die Vorhersagekurve des BSH — wenn es fuer diesen Pegel eine gibt.

    Es gibt sie fuer sehr wenige: von 29 Ostseepegeln bei pegelonline hat das
    BSH heute drei (Travemuende, Warnemuende, Koserow) plus Ueckermuende. Statt
    eine Liste zu pflegen, die still veraltet, wird der Name probiert — 404
    heisst dann schlicht "keine Vorhersage", und kommt eine dazu, erscheint sie
    von selbst.

    Wirft, wenn es keine gibt. Gibt `{}` zurueck, wenn es sie gibt, der
    tiefste Wert daraus aber nicht zu lesen war: das BILD ist dann trotzdem da
    und wird gezeigt — dafuer braucht es kein Pillow, nur einen Browser.
    Genau dieser Fall ist auf dem Pi der Normalfall (Pillow und numpy sind
    dort nicht installiert, siehe requirements.txt).
    """
    with urllib.request.urlopen(
            urllib.request.Request(_bsh_url(name), headers={'User-Agent': 'mave-boatui'}),
            timeout=15) as r:
        roh = r.read()
    try:
        return _parse_bsh_forecast(roh) or {}
    except Exception as e:
        log.warning('BSH-Kurve %s nicht auswertbar: %s', name, e)
        return {}


def _pegel_nullpunkt(uuid: str) -> float:
    """Wo die Null dieses Pegels liegt, in Metern ueber NHN.

    Ohne diese Zahl ist ein Pegelstand keine Hoehe, sondern eine Hausnummer:
    527 cm in Warnemuende und 527 cm in Travemuende sind verschiedene
    Wasserstaende. Sie stand hier fest fuer Travemuende — mit waehlbaren Pegeln
    geht das nicht mehr.
    """
    if uuid in _gz_cache:
        return _gz_cache[uuid]
    wert = _WL_PNP_M
    try:
        w = _http_json(f'{_PEGEL_BASIS}/stations/{urllib.parse.quote(uuid)}/W.json')
        gz = (w or {}).get('gaugeZero') or {}
        if isinstance(gz.get('value'), (int, float)):
            wert = float(gz['value'])
    except Exception as e:
        log.warning('Pegelnullpunkt %s nicht lesbar: %s', uuid, e)
    _gz_cache[uuid] = wert
    return wert


def _fetch_waterlevel(uuid: str, name: str) -> dict:
    pnp = _pegel_nullpunkt(uuid)
    # Ueber _http_json und nicht mit einem eigenen urlopen: dann gibt es EINE
    # Stelle, an der dieses Programm nach draussen geht — pruefbar, ohne das
    # halbe Netz nachzubauen.
    measurements = _http_json(
        f'{_PEGEL_BASIS}/stations/{urllib.parse.quote(uuid)}/W/measurements.json'
        '?start=P1DT0H&includeCurrentMeasurement=true', timeout=10)
    if not measurements:
        return {}
    current = measurements[-1]['value']
    nhn_cm  = round(current / 100 * 100 + pnp * 100)         # cm über NHN
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
        'station':        {'name': name, 'uuid': uuid, 'pnp_m': pnp},
        'forecast_img':   _bsh_url(name),
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


def _heizung_reihen(von: float, breite: float) -> dict:
    """Heizungsreihen fuer die Statusleiste — geholt, nicht mitgeschrieben.

    Der Hub fuehrt seinen Verlauf selbst und tiefer, als wir es je wuerden
    (siehe StokerClient.verlauf). Hier wird er nur auf dasselbe Raster gelegt
    wie die uebrigen Reihen, damit die Leiste eine Sprache spricht.

    Viertelstundenstufe und nicht Minutenstufe: die Leiste hat 60 Stuetzstellen
    auf 24 Stunden, also einen Punkt je 24 Minuten. Minutenwerte waeren 1440
    Zeilen, die der Pi holen, lesen und wieder wegwerfen muesste.

    Faellt der Hub aus, fehlen die Reihen — und die Felder zeigen ihre Zahlen
    ohne Hintergrund. Das ist richtig so: eine Kurve zu zeichnen, deren Quelle
    gerade schweigt, waere eine Behauptung.
    """
    antwort = heizung.verlauf(von, von + _SPARK_FENSTER_S)
    if not antwort:
        return {}
    spalten = antwort.get('columns') or []
    zeilen  = antwort.get('rows') or []
    if not spalten or not zeilen:
        return {}

    # Welche Spalte wohin gehoert. Die Raumnummer in `r<N>.temp` ist die id des
    # Raums, unter der ihn auch /api/state fuehrt.
    ziele: dict[str, str] = {'heater.flow': 'vorlauf', 'heater.power': 'heizleistung'}
    for name in spalten:
        if name.startswith('r') and name.endswith('.temp'):
            ziele[name] = 'raum' + name[1:-5]

    eimer: dict[str, list] = {z: [(0.0, 0)] * _SPARK_PUNKTE for z in ziele.values()}
    for zeile in zeilen:
        if not zeile:
            continue
        i = int((zeile[0] - von) / breite)
        if not 0 <= i < _SPARK_PUNKTE:
            continue
        for spalte, wert in zip(spalten, zeile):
            ziel = ziele.get(spalte)
            if ziel is None or not isinstance(wert, (int, float)) or isinstance(wert, bool):
                continue
            summe, anzahl = eimer[ziel][i]
            eimer[ziel][i] = (summe + wert, anzahl + 1)

    aus = {}
    for ziel, werte in eimer.items():
        reihe = [round(su / an, 2) if an else None for su, an in werte]
        # Eine Reihe ohne einen einzigen Wert ist keine Reihe. Sie wegzulassen
        # heisst: das Feld zeigt keinen Hintergrund, statt einer leeren Flaeche.
        if any(v is not None for v in reihe):
            aus[ziel] = reihe
    return aus


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

    def _reihe_quelle(feld: str) -> list:
        """Wie `_reihe`, aber eine fehlende LADEQUELLE zaehlt als 0 W.

        Ein Ladegeraet, das nichts meldet, laedt nicht — bei einer Quelle ist
        "kein Wert" also tatsaechlich null und nicht "unbekannt". Bei einem
        Tank waere dieselbe Annahme falsch: der hat einen Stand, auch wenn ihn
        gerade niemand misst. Deshalb nur hier.

        Der Anlass: das Landstromgeraet meldet sich erst, seit das Kabel
        dranhaengt — im Verlauf standen die Stunden davor leer. Die Kurve lief
        deshalb nach links aus (die Anlaufblende in display.js greift, wenn
        eine Reihe erst mitten im Fenster beginnt), waehrend das Feld "Laden"
        daneben durchging: dessen Summe gibt es, weil Solar durchgehend 0,0
        meldet. Zwei Bilder derselben Sache (Eignermeldung).

        Ein Eimer OHNE jeden Wert bleibt leer. Dann lief der Pi nicht, und
        "null Watt" waere eine Behauptung ueber eine Zeit, von der wir nichts
        wissen.
        """
        aus = []
        for b in eimer:
            summe, anzahl = b.get(feld, (0.0, 0))
            aus.append(round(summe / anzahl, 2) if anzahl else (0.0 if b else None))
        return aus

    def _ladeleistung() -> list:
        """Summe der Ladequellen, wie es die Statusleiste auch anzeigt."""
        teile = [_reihe_quelle(f) for f in ('charger', 'solar1', 'alternator')]
        aus = []
        for i in range(_SPARK_PUNKTE):
            werte = [t[i] for t in teile if t[i] is not None]
            aus.append(round(sum(werte), 1) if werte else None)
        return aus

    serien = {'soc': _reihe('soc'), 'starter': _reihe('starter'),
              'laden': _ladeleistung(),
              'tank1': _reihe('tank1'), 'tank2': _reihe('tank2'),
              'raumtemp': _reihe('raumtemp'),
              # Die Verbindung nach draussen. Ein hoher Ausschlag heisst hier
              # SCHLECHT — bei der Latenz ist wenig gut. Das ist beim Ablesen
              # richtig herum: was hochschlaegt, ist ein Problem.
              'ping': _reihe('ping_ms'), 'down': _reihe('down_mbit'),
              # Je Quelle einzeln, damit das Laden-Feld durchschalten kann.
              'charger': _reihe_quelle('charger'), 'solar1': _reihe_quelle('solar1'),
              'orion': _reihe_quelle('orion')}
    # Heizung und Raeume kommen aus dem Hub, nicht aus unserem Puffer.
    serien.update(_heizung_reihen(von, breite))

    # Geladene Amperestunden der letzten 24 Stunden, je Quelle und gesamt.
    # Aus Leistung und Spannung DESSELBEN Punktes: Ah = Summe(P/U) * dt.
    # Die Punkte des groben Puffers sind Minutenmittel, dt ist also 1/60 h.
    # Fehlt die Spannung, wird der Punkt uebersprungen statt mit einer
    # angenommenen Spannung gerechnet — geraten waere hier schlimmer als eine
    # etwas zu kleine Summe.
    def _amperestunden(felder) -> float:
        ah = 0.0
        for e in punkte:
            u = e.get('voltage')
            if not isinstance(u, (int, float)) or isinstance(u, bool) or u <= 1:
                continue
            for f in felder:
                p_w = e.get(f)
                if isinstance(p_w, (int, float)) and not isinstance(p_w, bool) and p_w > 0:
                    ah += (p_w / u) / 60.0
        return round(ah, 1)

    def _wattstunden(felder) -> float:
        """Wh braucht keine Spannung — die Leistung steht ja schon da."""
        wh = 0.0
        for e in punkte:
            for f in felder:
                p_w = e.get(f)
                if isinstance(p_w, (int, float)) and not isinstance(p_w, bool) and p_w > 0:
                    wh += p_w / 60.0
        return round(wh, 1)

    ah24 = {'charger': _amperestunden(('charger',)),
            'solar':   _amperestunden(('solar1',)),
            'orion':   _amperestunden(('orion',))}
    ah24['gesamt'] = round(sum(ah24.values()), 1)
    wh24 = {'charger': _wattstunden(('charger',)),
            'solar':   _wattstunden(('solar1',)),
            'orion':   _wattstunden(('orion',))}
    wh24['gesamt'] = round(sum(wh24.values()), 1)

    # Pegel steht seit v1.54.0 nicht mehr in der Statusleiste (dort ist jetzt
    # die Heizung). Die Reihe bleibt trotzdem: die Wasserstandsseite gibt es
    # weiter, und der Wert kostet nichts — er kommt fertig aus dem
    # Zwischenspeicher von pegelonline. Von hier wird KEIN Abruf ausgeloest,
    # sonst haengt die Statusleiste an einem fremden Dienst.
    erster = (_pegel_liste() or [{}])[0].get('uuid')
    wl = (_wl_cache.get(erster) or {}).get('data') or {}
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
            'punkte': _SPARK_PUNKTE, 'serien': serien,
            'ah24': ah24, 'wh24': wh24}


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


def _pegel_liste() -> list:
    """Die gepflegten Pegel — oder der Heimatpegel, solange keiner gepflegt ist."""
    roh = (read_json(PRESETS_FILE, {}) or {}).get('pegel') or {}
    stationen = roh.get('stationen') if isinstance(roh, dict) else None
    if isinstance(stationen, list) and stationen:
        return stationen
    return [dict(_PEGEL_VORGABE)]


def _pegel_finden(uuid: str | None) -> dict:
    """Der gefragte Pegel, sonst der erste. Eine unbekannte Kennung wird NICHT
    einfach abgerufen: sonst waere jeder Fremdpegel der Welt ueber diese
    Adresse abrufbar, und der Zwischenspeicher waechst mit jeder Anfrage."""
    liste = _pegel_liste()
    if not uuid:
        return liste[0]
    for p in liste:
        if p.get('uuid') == uuid:
            return p
    raise HTTPException(400, detail='Unbekannter Pegel')


# ── Gaeste-WLAN: ein QR-Code zum Scannen ────────────────────────────────────
#
# Wer an Bord kommt, fragt als Erstes nach dem WLAN. Statt ein Passwort
# vorzulesen, zeigt das Wandtablet einen QR-Code; iOS-Kamera und Android bieten
# daraufhin von selbst „Netzwerk beitreten" an.
#
# **Das gehoert das GAESTENETZ und nicht das Bordnetz.** Wer vor dem Tablet
# steht, hat damit das Passwort — das ist der Sinn der Sache. Es darf also
# kein Netz sein, aus dem man an den Pi, die Router-Oberflaeche oder das
# CAN-Gateway kommt. Das einzurichten ist Sache des Routers, nicht dieser App;
# hier stehen nur Name und Passwort.
#
# Von Hand eingetragen und NICHT aus dem Router gelesen: der RutOS-Zugang hat
# ein Sitzungslimit, und ein 401 von dort hat schon einmal den ganzen
# Verbindungszustand eingefroren. Ein Gaestepasswort aendert sich zweimal im
# Jahr — das ist den Preis nicht wert.

WLAN_FILE = BASE_DIR / 'wlan.json'
_WLAN_ARTEN = ('WPA', 'WEP', 'nopass')


def _wlan_lesen() -> dict:
    d = read_json(WLAN_FILE, {}) or {}
    art = d.get('art')
    return {'ssid': str(d.get('ssid') or '')[:64],
            'passwort': str(d.get('passwort') or '')[:128],
            'art': art if art in _WLAN_ARTEN else 'WPA',
            'versteckt': bool(d.get('versteckt'))}


def _wlan_feld(text: str) -> str:
    """Ein Feld fuer die WIFI-Zeichenkette maskieren.

    Sonderzeichen muessen mit Backslash geschuetzt werden, und der Backslash
    zuerst — sonst maskiert man die eigenen Maskierungen gleich mit.
    """
    for zeichen in ('\\', ';', ',', ':', '"'):
        text = text.replace(zeichen, '\\' + zeichen)
    return text


def _wlan_wert(text: str) -> str:
    """Wie `_wlan_feld`, aber schuetzt zusaetzlich vor der Hex-Falle.

    Sieht ein Wert aus wie eine Hexzahl (`0a1b2c…`), liest ein Telefon ihn als
    ROHEN Schluessel statt als Text und tritt dem Netz nicht bei. Anfuehrungs-
    zeichen erzwingen die Lesart als Text. Sie stehen AUSSEN und werden selbst
    nicht maskiert.
    """
    hex_verdacht = bool(text) and all(c in '0123456789abcdefABCDEF' for c in text)
    return f'"{_wlan_feld(text)}"' if hex_verdacht else _wlan_feld(text)


def wlan_kette(w: dict) -> str:
    """Die Zeichenkette, die ein Telefon als WLAN-Angebot versteht."""
    teile = [f"T:{w['art']}", f"S:{_wlan_wert(w['ssid'])}"]
    if w['art'] != 'nopass':
        teile.append(f"P:{_wlan_wert(w['passwort'])}")
    if w['versteckt']:
        teile.append('H:true')
    return 'WIFI:' + ';'.join(teile) + ';;'


@app.get('/api/wlan')
async def get_wlan():
    """Name und Passwort des Gaestenetzes.

    Das Passwort geht mit hinaus. Es steht ohnehin im QR-Code daneben, und die
    Seite zeigt es als Text darunter — nicht jeder scannt.
    """
    w = _wlan_lesen()
    return {**w, 'eingerichtet': bool(w['ssid'])}


@app.put('/api/wlan')
async def put_wlan(request: Request):
    daten = await _json_body(request)
    if not isinstance(daten, dict):
        raise HTTPException(400, detail='Erwartet wird ein Objekt')
    # NICHT beschneiden. Ein Netzname darf auf ein Leerzeichen enden, und das
    # `.strip()`, das hier stand, machte daraus stillschweigend einen anderen
    # Namen — der QR-Code war dann gueltig und fuehrte ins Leere
    # (Eignermeldung). Sichtbar gemacht wird es in der Oberflaeche, nicht
    # weggeraeumt.
    ssid = str(daten.get('ssid') or '')[:64]
    art = daten.get('art') if daten.get('art') in _WLAN_ARTEN else 'WPA'
    passwort = str(daten.get('passwort') or '')[:128]
    if ssid and art != 'nopass' and len(passwort) < 8:
        # WPA verlangt acht Zeichen. Ein kuerzeres Passwort ergaebe einen
        # QR-Code, den jedes Telefon annimmt und an dem dann jeder Beitritt
        # scheitert — und gesucht wird der Fehler beim WLAN.
        raise HTTPException(400, detail='WPA braucht mindestens 8 Zeichen')
    neu = {'ssid': ssid, 'passwort': passwort, 'art': art,
           'versteckt': bool(daten.get('versteckt'))}
    await _run_blocking(write_json, WLAN_FILE, neu)
    with suppress(OSError):
        os.chmod(WLAN_FILE, 0o600)
    log.info('Gäste-WLAN gesetzt: %r', ssid or '(geleert)')
    return {'ok': True, **neu, 'eingerichtet': bool(ssid)}


@app.get('/api/wlan/qr.svg')
async def get_wlan_qr():
    """Der QR-Code als SVG.

    DUNKEL AUF WEISS, mit weissem Rand. Auf dunklem Grund mit durchsichtigem
    Hintergrund waere er zwar huebscher, aber ein Teil der Telefone liest
    umgekehrte Codes nicht — und wenn es nicht klappt, sucht niemand die
    Ursache beim Farbschema.
    """
    w = _wlan_lesen()
    if not w['ssid']:
        raise HTTPException(404, detail='Kein Gäste-WLAN eingerichtet')
    import io
    try:
        import segno
    except ImportError:                     # pragma: no cover — Paket fehlt
        raise HTTPException(
            503, detail='Das Paket segno fehlt — QR-Code kann nicht erzeugt '
                        'werden (pip install -r requirements.txt)') from None
    code = segno.make(wlan_kette(w), error='m')
    puffer = io.BytesIO()
    code.save(puffer, kind='svg', scale=8, border=3,
              dark='#000000', light='#ffffff')
    return Response(content=puffer.getvalue(), media_type='image/svg+xml',
                    headers={'Cache-Control': 'no-store'})


@app.get('/api/pegel/orte')
async def get_pegel_orte():
    """Die gepflegten Pegel, in gepflegter Reihenfolge."""
    return {'stationen': _pegel_liste(),
            'gepflegt': bool(((read_json(PRESETS_FILE, {}) or {}).get('pegel') or {}).get('stationen'))}


@app.get('/api/pegel/suche')
async def get_pegel_suche(q: str = ''):
    """Pegel nach Namen suchen.

    `fuzzyId` von pegelonline statt der vollen Stationsliste: die haette rund
    700 Eintraege und ein Megabyte, und der Pi soll sie weder holen noch
    durchsuchen.
    """
    begriff = (q or '').strip()
    if len(begriff) < 2:
        return {'treffer': []}
    url = f'{_PEGEL_BASIS}/stations.json?fuzzyId={urllib.parse.quote(begriff)}'
    loop = asyncio.get_event_loop()
    try:
        roh = await loop.run_in_executor(None, _http_json, url)
    except Exception as e:
        log.warning('Pegelsuche fehlgeschlagen: %s', e)
        raise HTTPException(503, detail='Pegelsuche nicht verfügbar') from None
    treffer = []
    for t in (roh or [])[:12]:
        gewaesser = ((t.get('water') or {}).get('longname') or '').title()
        treffer.append({'name': (t.get('longname') or t.get('shortname') or '').title(),
                        'zusatz': gewaesser, 'uuid': t.get('uuid')})
    return {'treffer': [t for t in treffer if t['uuid']]}


@app.get('/api/waterlevel')
async def get_waterlevel(station: str | None = None):
    """Wasserstand eines Pegels. Ohne Angabe: der erste gepflegte.

    Je Pegel ein eigener Zwischenspeicher — wer auf der Kachel zwischen seinen
    Pegeln durchschaltet, soll nicht bei jedem Wechsel fünf Minuten alte Daten
    wegwerfen und neu holen.
    """
    pegel = _pegel_finden(station)
    uuid, name = pegel.get('uuid', ''), pegel.get('name', '')
    now = time.time()
    eintrag = _wl_cache.get(uuid)
    if eintrag and now - eintrag['ts'] < 300:
        return eintrag['data']
    loop = asyncio.get_event_loop()
    try:
        wl_data = await loop.run_in_executor(None, _fetch_waterlevel, uuid, name)
    except Exception as e:
        log.warning('Wasserstand-Fetch fehlgeschlagen: %s', e)
        if eintrag:
            return eintrag['data']
        raise HTTPException(503, detail='Wasserstand nicht verfügbar')

    # BSH-Prognose: eigener Zwischenspeicher je Pegel, 30 Minuten (das Bild
    # wird etwa stuendlich neu gerechnet). Fuer die allermeisten Pegel gibt es
    # keine — der Fehlschlag wird MITGESPEICHERT, sonst laeuft die Anwendung
    # alle fuenf Minuten in denselben 404.
    bsh_eintrag = _bsh_cache.get(uuid)
    if not bsh_eintrag or now - bsh_eintrag['ts'] >= 1800:
        try:
            bsh = await loop.run_in_executor(None, _fetch_bsh_forecast, name)
        except Exception as e:
            log.info('Keine BSH-Vorhersage für %s: %s', name, e)
            bsh = None
        bsh_eintrag = {'data': bsh, 'ts': now}
        if len(_bsh_cache) > 12:
            _bsh_cache.clear()
        _bsh_cache[uuid] = bsh_eintrag
    bsh = bsh_eintrag['data']
    if bsh is None:
        # Es gibt keine Kurve fuer diesen Pegel. Dann darf auch kein Bild
        # angeboten werden — sonst steht auf der Seite ein leerer Rahmen mit
        # kaputtem Verweis.
        wl_data.pop('forecast_img', None)
    elif 'min_nhn_cm' in bsh:
        wl_data['forecast_min_nhn_cm'] = bsh['min_nhn_cm']
        wl_data['forecast_alarm']      = bsh['min_nhn_cm'] < _WL_ALARM_NHN_CM

    if len(_wl_cache) > 12:
        _wl_cache.clear()
    _wl_cache[uuid] = {'data': wl_data, 'ts': now}
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
_WX_LAND = (53.87, 10.69)   # Lübeck (Stadt) — Vorgabe, wenn nichts gepflegt ist
_WX_SEA  = (54.10, 11.00)   # Lübecker Bucht (außen)
_WX_STORM_CODES = {95, 96, 99}
# Je Ort und Modell ein eigener Eintrag: wer zwischen Orten durchschaltet, soll
# nicht bei jedem Wechsel neu ins Netz muessen.
_wx_cache: dict = {}
_WX_FRISCH_S = 1800

# Die Wettermodelle, die Open-Meteo ohne Anmeldung hergibt und die hier
# ueberhaupt Sinn ergeben. `auto` laesst Open-Meteo je Ort das beste waehlen —
# in Nordeuropa meist ICON, weiter draussen ECMWF.
#
# Warum ueberhaupt eine Wahl: zwei Modelle, die dasselbe sagen, sind eine
# belastbare Vorhersage; zwei, die auseinanderlaufen, sind eine Warnung. Das
# sieht man nur, wenn man umschalten kann.
WX_MODELLE = {
    'auto':    'Automatisch',
    'icon':    'ICON (DWD)',
    'ecmwf':   'ECMWF',
    'gfs':     'GFS (NOAA)',
    'arpege':  'ARPEGE (Météo-France)',
    'ukmo':    'UKMO (Met Office)',
}
_WX_MODELL_PARAM = {
    'icon': 'icon_seamless', 'ecmwf': 'ecmwf_ifs025',
    'gfs': 'gfs_seamless', 'arpege': 'arpege_seamless', 'ukmo': 'ukmo_seamless',
}


def _http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'mave-boatui'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _fetch_weather(lat: float, lon: float, modell: str = 'auto') -> dict:
    """Wetter fuer EINEN Ort — Tage fuer den Ueberblick, Stunden fuers Segeln.

    Die Tagesuebersicht beantwortet "wie wird das Wochenende". Die Frage vor
    dem Ablegen ist eine andere: WANN geht es, und was zieht durch. Dafuer
    braucht es Stundenwerte — aus einem Tagesmaximum von 22 Knoten liest
    niemand ab, ob das eine Boe am Nachmittag war oder der ganze Tag.

    Land- und Seewetter kommen nicht mehr von zwei festen Punkten: der Ort
    ist jetzt einer, und die Seegangswerte holt die Marine-API fuer denselben.
    Liegt er an Land, gibt sie nichts zurueck — dann fehlt der Seegang, und
    das ist richtig so.
    """
    base = 'https://api.open-meteo.com/v1/forecast'
    m = _WX_MODELL_PARAM.get(modell)
    modellteil = f'&models={m}' if m else ''
    gemeinsam = (f'?latitude={lat:.4f}&longitude={lon:.4f}'
                 '&timezone=Europe%2FBerlin' + modellteil)

    tage = _http_json(
        f'{base}{gemeinsam}&forecast_days=5&windspeed_unit=kn'
        '&daily=weathercode,temperature_2m_max,temperature_2m_min,'
        'precipitation_sum,precipitation_probability_max,'
        'windspeed_10m_max,windgusts_10m_max,winddirection_10m_dominant,'
        'sunrise,sunset')
    stunden = _http_json(
        f'{base}{gemeinsam}&forecast_days=3&windspeed_unit=kn'
        '&hourly=temperature_2m,weathercode,precipitation,'
        'windspeed_10m,windgusts_10m,winddirection_10m,pressure_msl')

    see_tage, see_stunden = {}, {}
    try:
        marine = 'https://marine-api.open-meteo.com/v1/marine'
        see_tage = _http_json(
            f'{marine}?latitude={lat:.4f}&longitude={lon:.4f}'
            '&timezone=Europe%2FBerlin&forecast_days=5'
            '&daily=wave_height_max,wave_direction_dominant,wave_period_max')
        see_stunden = _http_json(
            f'{marine}?latitude={lat:.4f}&longitude={lon:.4f}'
            '&timezone=Europe%2FBerlin&forecast_days=3'
            '&hourly=wave_height,wave_direction,wave_period')
    except Exception:
        # Ein Ort an Land hat keinen Seegang. Das ist kein Fehler.
        pass

    def reihe(quelle, block, feld):
        werte = ((quelle or {}).get(block) or {}).get(feld)
        return werte if isinstance(werte, list) else []

    td, sd = tage.get('daily', {}), see_tage.get('daily', {})
    def g(d, k, i):
        a = d.get(k)
        return a[i] if isinstance(a, list) and i < len(a) else None

    tagesliste = []
    for i, datum in enumerate(td.get('time', [])):
        wmo = g(td, 'weathercode', i)
        tagesliste.append({
            'date': datum, 'wmo': wmo,
            'tmax': g(td, 'temperature_2m_max', i), 'tmin': g(td, 'temperature_2m_min', i),
            'precip': g(td, 'precipitation_sum', i),
            'pop': g(td, 'precipitation_probability_max', i),
            'wind': g(td, 'windspeed_10m_max', i), 'gust': g(td, 'windgusts_10m_max', i),
            'dir': g(td, 'winddirection_10m_dominant', i),
            'wave': g(sd, 'wave_height_max', i),
            'wave_dir': g(sd, 'wave_direction_dominant', i),
            'wave_periode': g(sd, 'wave_period_max', i),
            'auf': g(td, 'sunrise', i), 'unter': g(td, 'sunset', i),
            'storm': wmo in _WX_STORM_CODES,
        })

    zeiten = reihe(stunden, 'hourly', 'time')
    stundenliste = []
    for i, t in enumerate(zeiten):
        def h(feld, quelle=stunden, block='hourly'):
            a = reihe(quelle, block, feld)
            return a[i] if i < len(a) else None
        stundenliste.append({
            't': t, 'temp': h('temperature_2m'), 'wmo': h('weathercode'),
            'regen': h('precipitation'),
            'wind': h('windspeed_10m'), 'boe': h('windgusts_10m'),
            'dir': h('winddirection_10m'), 'druck': h('pressure_msl'),
            'welle': h('wave_height', see_stunden), 'welle_dir': h('wave_direction', see_stunden),
            'welle_periode': h('wave_period', see_stunden),
        })

    return {'updated': time.time(), 'source': 'open-meteo',
            'modell': modell, 'modell_name': WX_MODELLE.get(modell, modell),
            'ort': {'lat': round(lat, 4), 'lon': round(lon, 4)},
            'tage': tagesliste, 'stunden': stundenliste,
            # Die alten Schluessel bleiben, solange die Kachel sie liest.
            'land': {'name': '', 'days': tagesliste},
            'sea':  {'name': '', 'days': tagesliste}}


@app.get('/api/weather')
async def get_weather(lat: float | None = None, lon: float | None = None,
                      modell: str = ''):
    """Wetter fuer einen Ort. Ohne Angabe: der erste gepflegte Favorit.

    Je Ort UND Modell ein eigener Zwischenspeicher: wer zwischen seinen Orten
    durchschaltet, soll nicht bei jedem Wechsel warten. Eine halbe Stunde ist
    reichlich frisch — die Modelle rechnen ohnehin nur alle paar Stunden neu.
    """
    modell = modell or _wetter_modell()
    if modell not in WX_MODELLE:
        raise HTTPException(400, detail=f'Unbekanntes Modell: {modell}')
    if lat is None or lon is None:
        favorit = (_wetter_orte() or [None])[0]
        lat, lon = (favorit['lat'], favorit['lon']) if favorit else _WX_LAND
    lat = _zahl(lat, -90, 90, 'lat')
    lon = _zahl(lon, -180, 180, 'lon')

    schluessel = f'{lat:.4f},{lon:.4f},{modell}'
    now = time.time()
    eintrag = _wx_cache.get(schluessel)
    if eintrag and now - eintrag['ts'] < _WX_FRISCH_S:
        return eintrag['data']
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, _fetch_weather, lat, lon, modell)
    except Exception as e:
        log.warning('Wetter-Fetch fehlgeschlagen: %s', e)
        if eintrag:
            return eintrag['data']
        raise HTTPException(503, detail='Wetter nicht verfügbar')
    # Der Speicher darf nicht mit jedem angetippten Ort wachsen.
    if len(_wx_cache) > 24:
        _wx_cache.clear()
    _wx_cache[schluessel] = {'data': data, 'ts': now}
    return data


def _fetch_wetter_vergleich(lat: float, lon: float) -> dict:
    """Derselbe Ort, dieselben Stunden, fuenf Rechenmodelle.

    Zwei Modelle, die dasselbe sagen, sind eine belastbare Vorhersage; zwei,
    die auseinanderlaufen, sind eine Warnung — und zwar die einzige, die man
    aus einer Vorhersage ueberhaupt herauslesen kann. 18 Knoten aus einem
    Modell sehen genauso sicher aus wie 18 Knoten aus fuenfen; erst der
    Vergleich zeigt, ob man sich darauf einrichten kann.

    Open-Meteo beantwortet das in EINEM Aufruf (`&models=a,b,c`) — auf dem Pi
    ist das der Unterschied zwischen einer Anfrage und fuenfen. Die Felder
    heissen dann `windspeed_10m_<modell>`.
    """
    kennungen = [k for k in WX_MODELLE if k != 'auto']
    param = ','.join(_WX_MODELL_PARAM[k] for k in kennungen)
    roh = _http_json(
        f'https://api.open-meteo.com/v1/forecast'
        f'?latitude={lat:.4f}&longitude={lon:.4f}'
        '&timezone=Europe%2FBerlin&forecast_days=3&windspeed_unit=kn'
        '&hourly=windspeed_10m,windgusts_10m'
        f'&models={param}')
    h = roh.get('hourly') or {}

    def reihe(feld):
        a = h.get(feld)
        return a if isinstance(a, list) else []

    modelle = []
    for k in kennungen:
        suffix = _WX_MODELL_PARAM[k]
        wind = reihe(f'windspeed_10m_{suffix}')
        boe = reihe(f'windgusts_10m_{suffix}')
        # Ein Modell, das fuer diesen Ort nichts rechnet, gehoert nicht in den
        # Vergleich — eine leere Linie sieht sonst aus wie Flaute.
        if not any(x is not None for x in wind):
            continue
        modelle.append({'kennung': k, 'name': WX_MODELLE[k],
                        'wind': wind,
                        # ECMWF liefert keine Boeen. Weglassen statt Nullen.
                        'boe': boe if any(x is not None for x in boe) else []})
    return {'updated': time.time(),
            'ort': {'lat': round(lat, 4), 'lon': round(lon, 4)},
            'zeiten': reihe('time'), 'modelle': modelle}


_wx_vergleich_cache: dict = {}


@app.get('/api/wetter/vergleich')
async def get_wetter_vergleich(lat: float | None = None, lon: float | None = None):
    """Wie weit die Modelle beim Wind auseinanderliegen."""
    if lat is None or lon is None:
        favorit = (_wetter_orte() or [None])[0]
        lat, lon = (favorit['lat'], favorit['lon']) if favorit else _WX_LAND
    lat = _zahl(lat, -90, 90, 'lat')
    lon = _zahl(lon, -180, 180, 'lon')

    schluessel = f'{lat:.4f},{lon:.4f}'
    now = time.time()
    eintrag = _wx_vergleich_cache.get(schluessel)
    if eintrag and now - eintrag['ts'] < _WX_FRISCH_S:
        return eintrag['data']
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, _fetch_wetter_vergleich, lat, lon)
    except Exception as e:
        log.warning('Modellvergleich fehlgeschlagen: %s', e)
        if eintrag:
            return eintrag['data']
        raise HTTPException(503, detail='Modellvergleich nicht verfügbar') from None
    if len(_wx_vergleich_cache) > 12:
        _wx_vergleich_cache.clear()
    _wx_vergleich_cache[schluessel] = {'data': data, 'ts': now}
    return data


@app.get('/api/wetter/orte')
async def get_wetter_orte():
    """Die gepflegten Orte, das gewaehlte Modell und die Auswahl dazu."""
    return {'orte': _wetter_orte(), 'modell': _wetter_modell(),
            'modelle': WX_MODELLE}


def _wetter_einstellung() -> dict:
    roh = (read_json(PRESETS_FILE, {}) or {}).get('wetter') or {}
    return roh if isinstance(roh, dict) else {}


def _wetter_orte() -> list:
    """Favoriten aus presets.json — hoechstens fuenf, in gepflegter Reihenfolge."""
    orte = _wetter_einstellung().get('orte')
    return orte if isinstance(orte, list) else []


def _wetter_modell() -> str:
    modell = _wetter_einstellung().get('modell')
    return modell if modell in WX_MODELLE else 'auto'


@app.get('/api/wetter/suche')
async def get_wetter_suche(q: str = ''):
    """Ort nach Namen suchen, damit Favoriten ohne Koordinaten anzulegen sind.

    Wer einen Hafen eintragen will, kennt seinen Namen und nicht seine
    Dezimalgrade. Die Suche laeuft ueber dieselbe Quelle wie die Vorhersage
    (Open-Meteo), damit die Koordinate zum Modellraster passt.
    """
    begriff = (q or '').strip()
    if len(begriff) < 2:
        return {'treffer': []}
    url = ('https://geocoding-api.open-meteo.com/v1/search'
           f'?name={urllib.parse.quote(begriff)}&count=8&language=de&format=json')
    loop = asyncio.get_event_loop()
    try:
        roh = await loop.run_in_executor(None, _http_json, url)
    except Exception as e:
        log.warning('Ortssuche fehlgeschlagen: %s', e)
        raise HTTPException(503, detail='Ortssuche nicht verfügbar') from None

    treffer = []
    for t in (roh.get('results') or []):
        # Land und Region dazu: "Neustadt" gibt es reichlich, und aus der
        # nackten Liste waere nicht zu erkennen, welches gemeint ist.
        zusatz = ', '.join(x for x in (t.get('admin1'), t.get('country')) if x)
        treffer.append({'name': t.get('name') or '', 'zusatz': zusatz,
                        'lat': t.get('latitude'), 'lon': t.get('longitude')})
    return {'treffer': treffer}



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


# ── Verbindung zum Server ───────────────────────────────────────────────────

@app.get('/api/sync')
async def sync_stand():
    """Ob und wie das Boot mit dem Server spricht. Auch fuer die Oberflaeche."""
    cfg = _sync_konfig()
    return {
        'eingerichtet': sync.eingerichtet,
        'verbunden':    sync.verbunden,
        'betriebsart':  sync._art() if sync.eingerichtet else None,
        'schalter':     cfg.get('schalter', 'auto'),
        'verlauf_stand': grob_store.hoechste_folge(),
        'gesendet': {'zustand': sync.zustand_gesendet,
                     'verlauf': sync.verlauf_gesendet,
                     'befehle': sync.befehle_ausgefuehrt},
    }


@app.post('/api/sync/schalter')
async def sync_schalter(body: dict):
    """Handschalter fuer die Betriebsart: auto, voll oder gedrosselt.

    Wer im Hafen an einer teuren Marina-SIM haengt, soll drosseln koennen,
    obwohl der Router 'wifi' meldet — und umgekehrt.
    """
    wert = (body or {}).get('schalter')
    if wert not in sync_client.p.SCHALTER:
        raise HTTPException(400, detail=f'Unbekannte Stellung: {wert!r}')
    cfg = _sync_konfig()
    cfg['schalter'] = wert
    await _run_blocking(write_json, SYNC_FILE, cfg)
    return {'schalter': wert, 'betriebsart': sync._art() if sync.eingerichtet else None}
