"""Mave Boat Monitor — FastAPI Backend."""
import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
import urllib.request
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from alarm_engine import AlarmEngine
from can_reader import BoatState, CanInterface
from charge_control import ChargeController
from connectivity import ConnectivityMonitor

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

VERSION  = _git_semver() or '1.17.0'
GIT_HASH = _git_hash()

# Hintergrund-Cache: lesbare Remote-Version + ob ein Update verfügbar ist.
# Wird periodisch in einem Thread aktualisiert, damit der Endpunkt nie blockiert.
_remote_ver = {'ts': 0.0, 'version': '', 'hash': '', 'up_to_date': None}

def _parse_version_fallback(text: str) -> str:
    """Liest die lesbare Version aus  VERSION = _git_semver() or 'x.y.z'  ."""
    m = re.search(r"_git_semver\(\)\s*or\s*'([^']+)'", text)
    return m.group(1) if m else ''

def _refresh_remote_version():
    try:
        subprocess.run(['git', 'fetch', '--quiet'], cwd=Path(__file__).parent, timeout=30)
        h = subprocess.run(['git', 'rev-parse', '--short', '@{u}'],
                           cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        rhash = h.stdout.strip() if h.returncode == 0 else ''
        show = subprocess.run(['git', 'show', '@{u}:main.py'],
                              cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        rver = _parse_version_fallback(show.stdout) if show.returncode == 0 else ''
        _remote_ver.update(ts=time.time(), version=rver, hash=rhash,
                           up_to_date=((rhash == GIT_HASH) if rhash else None))
    except Exception as e:
        logging.getLogger(__name__).debug('Remote-Version-Check: %s', e)

def _remote_version_loop():
    while True:
        _refresh_remote_version()
        time.sleep(300)


def read_json(path: Path, default=None):
    """JSON-Datei laden; default zurückgeben wenn nicht vorhanden."""
    if path.exists():
        return json.loads(path.read_text())
    return default


def write_json(path: Path, data) -> None:
    """JSON-Datei hübsch + UTF-8 schreiben."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger(__name__)

BASE_DIR      = Path(__file__).parent
PRESETS_FILE  = BASE_DIR / 'presets.json'
STATIC_DIR    = BASE_DIR / 'static'
STAUPLAN_FILE = BASE_DIR / 'stauplan.json'
WARTUNG_FILE  = BASE_DIR / 'wartung.json'

state       = BoatState()
can_if      = CanInterface(channel='can0', state=state,
                           stats_path=BASE_DIR / 'daily_stats.json')
alarms      = AlarmEngine()
charge_ctrl = ChargeController()
can_if._charger_config_cb = charge_ctrl.update_actual_setpoints

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
    data = read_json(PRESETS_FILE)
    batt = data.get('batteries', {})
    can_if.set_battery_instances(
        service=int(batt.get('service_instance', 0)),
        starter=int(batt.get('starter_instance', 1)),
    )

_apply_presets_config()

ws_clients: set[WebSocket] = set()
history: deque[dict] = deque(maxlen=10800)   # 10800 × 5 s ≈ 15 h
_hist_last_ts: float = 0.0


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


async def broadcast(data: dict):
    global _hist_last_ts
    check_data = {**data, '_network_age': can_if.time_since_last_message()}
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
    for src_key, field in (('solar', 'solar1'), ('alternator', 'alternator'),
                            ('solar2', 'solar2'), ('solar3', 'solar3'),
                            ('charger', 'charger'), ('wind', 'wind')):
        p = data.get(src_key, {}).get('power')
        if p is not None:
            entry[field] = p
    bms = data.get('bms', {})
    for bms_key in ('current_charge', 'current_discharge'):
        v = bms.get(bms_key)
        if v is not None:
            entry[bms_key] = v
    if len(entry) > 1 and now - _hist_last_ts >= 5.0:
        history.append(entry)
        _hist_last_ts = now
    payload = {**data, 'alarms': alarms.get_alarms(), 'unack_alarms': alarms.unack_count, 'version': VERSION}
    dead = set()
    for ws in list(ws_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


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
    yield
    can_if.stop()
    log.info("CAN-Reader gestoppt")


class _NoCacheStatic(StaticFiles):
    """StaticFiles, das Revalidierung erzwingt (no-cache) — so erscheinen
    JS/CSS-Updates nach git pull sofort, liefern aber 304 wenn unverändert."""
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers['Cache-Control'] = 'no-cache'
        return resp


app = FastAPI(title='Mave Boat Monitor', lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount('/static', _NoCacheStatic(directory=STATIC_DIR), name='static')

# JS files in dependency order — concatenated into one request on /js-bundle.js
_JS_FILES = [
    'core.js', 'battery.js', 'tanks.js', 'lights.js', 'charts.js',
    'alarms.js', 'settings.js', 'connectivity.js', 'ws.js', 'lightdetail.js',
    'wartung.js', 'stauplan.js', 'monday.js', 'flow.js', 'display.js',
    'waterlevel.js', 'init.js',
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


@app.get('/', include_in_schema=False)
async def root():
    return FileResponse(
        STATIC_DIR / 'index.html',
        headers={'Cache-Control': 'no-cache, no-store, must-revalidate',
                 'Pragma': 'no-cache', 'Expires': '0'},
    )


@app.websocket('/ws')
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    log.info("WebSocket verbunden (%d aktiv)", len(ws_clients))
    try:
        try:
            await ws.send_json({**state.to_dict(), 'version': VERSION})
        except Exception as e:
            log.error("WebSocket init JSON-Fehler: %s", e)
            raise
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await ws.send_json({'ping': True})
    except (WebSocketDisconnect, Exception) as e:
        if not isinstance(e, WebSocketDisconnect):
            log.debug("WebSocket Fehler: %s", e)
    finally:
        ws_clients.discard(ws)
        log.info("WebSocket getrennt (%d aktiv)", len(ws_clients))


@app.get('/api/presets')
async def get_presets():
    return read_json(PRESETS_FILE)


@app.post('/api/lights/preset/{preset_id}')
async def apply_preset(preset_id: int):
    data    = read_json(PRESETS_FILE)
    presets = data.get('presets', [])
    if not (0 <= preset_id < len(presets)):
        raise HTTPException(404, detail='Preset nicht gefunden')
    preset = presets[preset_id]
    if preset.get('values') is None:
        raise HTTPException(400, detail='Preset nicht konfiguriert')
    can_if.send_brightness(preset['values'])
    log.info("Preset %d '%s' aktiviert", preset_id, preset['name'])
    return {'ok': True, 'preset': preset['name']}


@app.post('/api/lights/channels')
async def set_channels(body: dict):
    values = body.get('values', [])
    if len(values) != 9:
        raise HTTPException(400, detail='9 Werte erforderlich')
    values = [max(0, min(255, int(v))) for v in values]
    can_if.send_brightness(values)
    state.lights['channels'] = values
    return {'ok': True}


@app.patch('/api/lights/preset/{preset_id}')
async def update_preset(preset_id: int, body: dict):
    data    = read_json(PRESETS_FILE)
    presets = data.get('presets', [])
    if not (0 <= preset_id < len(presets)):
        raise HTTPException(404, detail='Preset nicht gefunden')
    if 'name' in body:
        presets[preset_id]['name'] = str(body['name'])[:32]
    if 'emoji' in body:
        presets[preset_id]['emoji'] = str(body['emoji'])[:4]
    if 'values' in body:
        vals = list(body['values'])
        if len(vals) == 9:
            presets[preset_id]['values'] = [max(0, min(255, int(v))) for v in vals]
    write_json(PRESETS_FILE, data)
    log.info("Preset %d aktualisiert: '%s'", preset_id, presets[preset_id]['name'])
    return data


@app.get('/api/history')
async def get_history():
    return list(history)


@app.get('/api/status')
async def get_status():
    return state.to_dict()


@app.get('/api/daily-stats')
async def get_daily_stats(days: int = 7):
    return can_if.get_daily_stats(min(days, 30))


@app.get('/api/network')
async def get_network():
    return can_if.get_network_stats()


_js_errors: list[dict] = []

@app.post('/api/jserror')
async def post_jserror(request: Request):
    """Empfängt JS-Fehler vom Frontend und speichert sie im RAM-Log."""
    try:
        entry = await request.json()
        _js_errors.append(entry)
        if len(_js_errors) > 50:
            _js_errors.pop(0)
        logging.error('JS-FEHLER: %s @ %s:%s', entry.get('msg','?'), entry.get('src','?'), entry.get('line','?'))
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


@app.post('/api/system/update')
async def system_update():
    before = _git_hash()
    result = subprocess.run(
        ['git', 'pull'],
        cwd=BASE_DIR, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise HTTPException(500, detail=result.stderr.strip())
    changed = 'Already up to date.' not in result.stdout
    after = _git_hash()
    log.info("git pull: %s", result.stdout.strip())
    changelog = []
    if changed:
        try:
            cl = subprocess.run(
                ['git', 'log', 'ORIG_HEAD..HEAD', '--no-merges',
                 '--pretty=format:ENTRY%n%s%n%b'],
                cwd=BASE_DIR, capture_output=True, text=True, timeout=10,
            )
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


@app.get('/api/wartung')
async def get_wartung():
    return read_json(WARTUNG_FILE, [])

@app.put('/api/wartung')
async def save_wartung(request: Request):
    body = await request.json()
    write_json(WARTUNG_FILE, body)
    return body


@app.get('/api/stauplan')
async def get_stauplan():
    return read_json(STAUPLAN_FILE, [])

@app.put('/api/stauplan')
async def save_stauplan(request: Request):
    body = await request.json()
    write_json(STAUPLAN_FILE, body)
    return body


@app.patch('/api/settings')
async def update_settings(body: dict):
    data = read_json(PRESETS_FILE)
    if 'tanks' in body:
        for key, val in body['tanks'].items():
            if key in data.get('tanks', {}):
                data['tanks'][key].update(val)
    if 'devices' in body:
        data.setdefault('devices', {}).update(body['devices'])
    if 'batteries' in body:
        data.setdefault('batteries', {}).update(body['batteries'])
        _apply_presets_config()
    if 'wartung' in body:
        w = body['wartung']
        if 'due_soon_days' in w:
            days = max(1, min(14, int(w['due_soon_days'])))
            data.setdefault('wartung', {})['due_soon_days'] = days
    write_json(PRESETS_FILE, data)
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


@app.patch('/api/charger/settings')
async def update_charger_settings(body: dict):
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
        r = subprocess.run(['vcgencmd', 'display_power', '1' if on else '0'],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            raise HTTPException(503, detail=f'Display-Steuerung nicht verfügbar: '
                                            f'{(r.stderr or r.stdout).strip()}')
    except FileNotFoundError:
        raise HTTPException(503, detail='vcgencmd nicht gefunden')
    return {'ok': True, 'power': on}


@app.post('/api/display/brightness')
async def display_brightness(body: dict):
    val = max(0, min(100, int(body.get('value', 50))))
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
