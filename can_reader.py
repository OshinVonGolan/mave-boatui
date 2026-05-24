"""CAN-Bus Interface: liest NMEA2000-Frames, verwaltet den Systemzustand."""
import asyncio
import logging
import time

import can

from nmea2000 import (
    FAST_PACKET_PGNS, FastPacketReassembler,
    build_brightness_frames, make_can_id,
    parse_battery_stats, parse_brightness,
    parse_can_id, parse_dc_status, parse_fluid_level,
    PGN_NAMES, RPI_SOURCE_ADDRESS,
)

log = logging.getLogger(__name__)


class BoatState:
    def __init__(self):
        self.battery = {
            'voltage': None, 'current': None, 'soc': None,
            'power': None, 'consumed_ah': None, 'cycles': None,
            'starter_voltage': None, 'min_voltage': None,
            'max_voltage': None, 'time_since_full': None,
        }
        self.tanks  = {'tank1': None, 'tank2': None}
        self.lights = {'channels': [0] * 9}

    def to_dict(self) -> dict:
        return {
            'battery': dict(self.battery),
            'tanks':   dict(self.tanks),
            'lights':  dict(self.lights),
        }


class CanInterface:
    """Verwaltet den CAN-Bus in einem eigenen Thread und benachrichtigt asyncio."""

    def __init__(self, channel: str, state: BoatState):
        self.channel      = channel
        self.state        = state
        self._bus         = None
        self._fp          = FastPacketReassembler()
        self._seq_id      = 0
        self._running     = False
        self._loop        = None
        self._on_change   = None   # async callback(data: dict)
        self._broadcast_pending = False
        self._network: dict = {}   # (pgn, src) → tracking entry

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def on_change(self, coro_fn):
        """Setzt die async-Callback-Funktion, die bei Datenänderung aufgerufen wird."""
        self._on_change = coro_fn

    # ── Senden ──────────────────────────────────────────────────────────────

    def send_brightness(self, values: list[int]):
        if self._bus is None:
            log.warning("CAN nicht verbunden – Senden nicht möglich")
            return
        frames = build_brightness_frames(values, self._seq_id)
        self._seq_id = (self._seq_id + 1) & 0x07
        for can_id, data in frames:
            try:
                self._bus.send(can.Message(
                    arbitration_id=can_id, data=data, is_extended_id=True))
            except can.CanError as e:
                log.error("CAN-Sendefehler: %s", e)

    # ── Empfangen ───────────────────────────────────────────────────────────

    def _track_network(self, pgn: int, src: int):
        now = time.monotonic()
        key = (pgn, src)
        if key not in self._network:
            self._network[key] = {'count': 1, 'first_seen': now, 'last_seen': now, 'intervals': []}
        else:
            e = self._network[key]
            e['intervals'].append(now - e['last_seen'])
            if len(e['intervals']) > 20:
                e['intervals'] = e['intervals'][-20:]
            e['last_seen'] = now
            e['count'] += 1

    def get_network_stats(self) -> list[dict]:
        now = time.monotonic()
        result = []
        for (pgn, src), e in sorted(self._network.items(), key=lambda x: (x[0][1], x[0][0])):
            ivs = e['intervals']
            avg_ms = round(sum(ivs) / len(ivs) * 1000) if ivs else None
            result.append({
                'pgn':         pgn,
                'src':         src,
                'description': PGN_NAMES.get(pgn, f'PGN {pgn}'),
                'count':       e['count'],
                'interval_ms': avg_ms,
                'age_s':       round(now - e['last_seen'], 1),
            })
        return result

    def _handle(self, msg: can.Message):
        pgn, src = parse_can_id(msg.arbitration_id)
        raw = bytes(msg.data)

        is_fp = pgn in FAST_PACKET_PGNS
        if not is_fp or (raw and (raw[0] & 0x1F) == 0):
            self._track_network(pgn, src)

        payload = self._fp.process(pgn, src, raw) if is_fp else raw

        if payload is None:
            return

        changed = False

        if pgn == 127505:
            p = parse_fluid_level(payload)
            if p:
                key = 'tank1' if p['instance'] == 0 else 'tank2'
                if self.state.tanks[key] != p['level']:
                    self.state.tanks[key] = p['level']
                    changed = True

        elif pgn == 127508:
            p = parse_dc_status(payload)
            if p:
                if p['instance'] == 0:
                    for field, key in [('voltage', 'voltage'), ('current', 'current')]:
                        if p[field] is not None and self.state.battery[key] != p[field]:
                            self.state.battery[key] = p[field]
                            changed = True
                elif p['instance'] == 1:
                    if p['voltage'] is not None and self.state.battery['starter_voltage'] != p['voltage']:
                        self.state.battery['starter_voltage'] = p['voltage']
                        changed = True

        elif pgn == 130900:
            p = parse_battery_stats(payload)
            if p:
                for k, v in p.items():
                    if self.state.battery.get(k) != v:
                        self.state.battery[k] = v
                        changed = True

        elif pgn == 126720:
            p = parse_brightness(payload)
            if p and self.state.lights['channels'] != p['channels']:
                self.state.lights['channels'] = p['channels']
                changed = True

        if changed:
            self._schedule_broadcast()

    def _schedule_broadcast(self):
        """Sendet state-Update an alle WS-Clients (debounced, max 20/s)."""
        if self._loop is None or self._on_change is None:
            return
        if not self._broadcast_pending:
            self._broadcast_pending = True
            asyncio.run_coroutine_threadsafe(self._delayed_broadcast(), self._loop)

    async def _delayed_broadcast(self):
        await asyncio.sleep(0.05)
        self._broadcast_pending = False
        await self._on_change(self.state.to_dict())

    # ── Thread-Hauptschleife ─────────────────────────────────────────────────

    def run(self):
        """Blockierende Schleife — läuft in einem Hintergrund-Thread."""
        self._running = True
        while self._running:
            try:
                with can.Bus(channel=self.channel, bustype='socketcan') as bus:
                    self._bus = bus
                    log.info("CAN-Bus %s verbunden", self.channel)
                    for msg in bus:
                        if not self._running:
                            break
                        self._handle(msg)
            except Exception as e:
                self._bus = None
                log.error("CAN-Fehler: %s — Reconnect in 5 s", e)
                time.sleep(5)

    def stop(self):
        self._running = False
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass
