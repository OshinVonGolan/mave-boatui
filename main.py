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
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from alarm_engine import AlarmEngine
from can_reader import BoatState, CanInterface
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

VERSION  = _git_semver() or '1.13.6'
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

state   = BoatState()
can_if  = CanInterface(channel='can0', state=state)
alarms  = AlarmEngine()

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
history: deque[dict] = deque(maxlen=50000)


async def broadcast(data: dict):
    check_data = {**data, '_network_age': can_if.time_since_last_message()}
    alarms.check(check_data)
    batt = data.get('battery', {})
    entry: dict = {'ts': time.time()}
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
    if len(entry) > 1:
        history.append(entry)
    payload = {**data, 'alarms': alarms.get_alarms(), 'unack_alarms': alarms.unack_count, 'version': VERSION}
    dead = set()
    for ws in list(ws_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


can_if.on_change(broadcast)



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
app.mount('/static', _NoCacheStatic(directory=STATIC_DIR), name='static')


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
        await ws.send_json({**state.to_dict(), 'version': VERSION})
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await ws.send_json({'ping': True})
    except (WebSocketDisconnect, Exception):
        pass
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


@app.get('/api/network')
async def get_network():
    return can_if.get_network_stats()


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
    if changed:
        asyncio.get_event_loop().call_later(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
    return {'ok': True, 'changed': changed, 'output': result.stdout.strip(),
            'version_before': before, 'version_after': after}


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
    write_json(PRESETS_FILE, data)
    return data
