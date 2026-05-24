"""Mave Boat Monitor — FastAPI Backend."""
import asyncio
import json
import logging
import math
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from can_reader import BoatState, CanInterface

DEMO = os.environ.get('DEMO', '').lower() in ('1', 'true', 'yes')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger(__name__)

BASE_DIR     = Path(__file__).parent
PRESETS_FILE = BASE_DIR / 'presets.json'
STATIC_DIR   = BASE_DIR / 'static'

state  = BoatState()
can_if = CanInterface(channel='can0', state=state)

ws_clients: set[WebSocket] = set()


async def broadcast(data: dict):
    dead = set()
    for ws in list(ws_clients):
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


can_if.on_change(broadcast)


async def _demo_task():
    """Generiert realistische Testdaten — kein CAN-Bus nötig (DEMO=1)."""
    t = 0
    light_channels = [0] * 9
    while True:
        t += 1
        soc     = 72 + 12 * math.sin(t * 0.04)
        voltage = 12.45 + 0.35 * math.sin(t * 0.03)
        current = 4.5 * math.sin(t * 0.06) - 1.2

        state.battery.update({
            'soc':             round(soc, 1),
            'voltage':         round(voltage, 2),
            'current':         round(current, 1),
            'power':           round(voltage * current, 1),
            'consumed_ah':     round(18.4 + t * 0.005, 1),
            'cycles':          47,
            'starter_voltage': round(12.28 + 0.08 * math.sin(t * 0.02), 2),
            'min_voltage':     11.92,
            'max_voltage':     13.18,
            'time_since_full': 7200 + t * 2,
        })
        state.tanks['tank1'] = round(63 + 4 * math.sin(t * 0.015), 1)
        state.tanks['tank2'] = round(28 + 2 * math.sin(t * 0.02), 1)

        # Kanal-Animation: alle 8 s neues Muster
        phase = (t // 8) % 4
        if   phase == 0: light_channels = [0]*9
        elif phase == 1: light_channels = [255,255,255,255,0,0,0,0,0]
        elif phase == 2: light_channels = [80,80,80,80,80,80,80,80,1]
        elif phase == 3: light_channels = [255]*8 + [1]
        state.lights['channels'] = light_channels

        # Demo-Netzwerkaktivität simulieren
        for pgn, src, every in [
            (127508, 22, 1), (130900, 22, 1), (127505, 22, 2),
            (129026, 35, 1), (127250, 35, 1), (130306, 48, 1),
            (126720, 100, 1), (60928, 22, 60), (60928, 35, 60),
        ]:
            if t % every == 0:
                can_if._track_network(pgn, src)

        await broadcast(state.to_dict())
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if DEMO:
        log.info("★ DEMO-Modus aktiv — keine CAN-Hardware nötig")
        can_if.seed_demo_network()
        asyncio.ensure_future(_demo_task())
    else:
        loop = asyncio.get_event_loop()
        can_if.set_loop(loop)
        t = threading.Thread(target=can_if.run, daemon=True, name='can-reader')
        t.start()
        log.info("CAN-Reader gestartet")
    yield
    if not DEMO:
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
        await ws.send_json(state.to_dict())
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


@app.get('/api/status')
async def get_status():
    return state.to_dict()


@app.get('/api/network')
async def get_network():
    return can_if.get_network_stats()


@app.patch('/api/settings')
async def update_settings(body: dict):
    data = json.loads(PRESETS_FILE.read_text())
    if 'tanks' in body:
        for key, val in body['tanks'].items():
            if key in data.get('tanks', {}):
                data['tanks'][key].update(val)
    if 'devices' in body:
        data.setdefault('devices', {}).update(body['devices'])
    PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data
