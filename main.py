"""Mave Boat Monitor — FastAPI Backend."""
import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from alarm_engine import AlarmEngine
from can_reader import BoatState, CanInterface

VERSION = '1.3.2'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger(__name__)

BASE_DIR     = Path(__file__).parent
PRESETS_FILE = BASE_DIR / 'presets.json'
STATIC_DIR   = BASE_DIR / 'static'

state   = BoatState()
can_if  = CanInterface(channel='can0', state=state)
alarms  = AlarmEngine()

def _apply_presets_config():
    data = json.loads(PRESETS_FILE.read_text())
    batt = data.get('batteries', {})
    can_if.set_battery_instances(
        service=int(batt.get('service_instance', 0)),
        starter=int(batt.get('starter_instance', 1)),
    )

_apply_presets_config()

ws_clients: set[WebSocket] = set()


async def broadcast(data: dict):
    check_data = {**data, '_network_age': can_if.time_since_last_message()}
    alarms.check(check_data)
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
    yield
    can_if.stop()
    log.info("CAN-Reader gestoppt")


app = FastAPI(title='Mave Boat Monitor', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', include_in_schema=False)
async def root():
    return FileResponse(STATIC_DIR / 'index.html')


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
    return json.loads(PRESETS_FILE.read_text())


@app.post('/api/lights/preset/{preset_id}')
async def apply_preset(preset_id: int):
    data    = json.loads(PRESETS_FILE.read_text())
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
    data    = json.loads(PRESETS_FILE.read_text())
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
    PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    log.info("Preset %d aktualisiert: '%s'", preset_id, presets[preset_id]['name'])
    return data


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

@app.get('/api/alarms/rules')
async def get_alarm_rules():
    return alarms.get_rules()

@app.patch('/api/alarms/rules')
async def update_alarm_rules(body: dict):
    return alarms.update_rules(body)


@app.patch('/api/settings')
async def update_settings(body: dict):
    data = json.loads(PRESETS_FILE.read_text())
    if 'tanks' in body:
        for key, val in body['tanks'].items():
            if key in data.get('tanks', {}):
                data['tanks'][key].update(val)
    if 'devices' in body:
        data.setdefault('devices', {}).update(body['devices'])
    if 'batteries' in body:
        data.setdefault('batteries', {}).update(body['batteries'])
        _apply_presets_config()
    PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data
