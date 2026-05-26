"""Connectivity monitor: RUTX50 Router (WAN/Mobile) + Starlink dish."""
import json
import logging
import ssl
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

POLL_INTERVAL = 20  # seconds

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode    = ssl.CERT_NONE


class ConnectivityMonitor:

    def __init__(self, router_host, router_user, router_pass,
                 starlink_host='192.168.100.1:9200'):
        self._router_host   = router_host
        self._router_user   = router_user
        self._router_pass   = router_pass
        self._starlink_host = starlink_host
        self._lock          = threading.Lock()
        self._status: dict  = {}
        self._token         = None
        self._token_ts      = 0.0
        self._grpc_stub     = None

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name='connectivity')
        t.start()

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    # ── polling ───────────────────────────────────────────────────────────────

    def _loop(self):
        while True:
            try:
                router   = self._fetch_router()
                starlink = self._fetch_starlink()
                with self._lock:
                    self._status = {'router': router, 'starlink': starlink,
                                    'ts': time.time()}
            except Exception as e:
                log.warning('connectivity poll: %s', e)
            time.sleep(POLL_INTERVAL)

    # ── router ────────────────────────────────────────────────────────────────

    def _http(self, url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=6) as r:
            return json.loads(r.read())

    def _token_headers(self):
        now = time.time()
        if not self._token or now - self._token_ts > 3000:
            resp = self._http(
                f'{self._router_host}/api/login',
                data=json.dumps({'username': self._router_user,
                                 'password': self._router_pass}).encode(),
                headers={'Content-Type': 'application/json'},
            )
            self._token    = resp['data']['token']
            self._token_ts = now
        return {'Authorization': f'Bearer {self._token}'}

    def _fetch_router(self):
        hdrs   = self._token_headers()
        ifaces = self._http(f'{self._router_host}/api/interfaces/status', headers=hdrs)['data']
        modems = self._http(f'{self._router_host}/api/modems/status',     headers=hdrs)['data']

        wan = [i for i in ifaces if i.get('area_type') == 'wan' and i.get('is_up')]
        wan.sort(key=lambda i: i.get('metric', 99))
        primary = wan[0] if wan else {}
        mobile  = next((i for i in wan if i.get('network_type') == 'mobile'), None)
        wired   = next((i for i in wan if i.get('network_type') == 'wired'),  None)
        modem   = modems[0] if modems else {}

        return {
            'active_type':   primary.get('network_type', 'unknown'),
            'active_uptime': primary.get('uptime', 0),
            'wired_up':      wired  is not None,
            'mobile_up':     mobile is not None,
            'mobile': {
                'operator':    modem.get('operator', ''),
                'conntype':    modem.get('conntype', ''),
                'ntype':       modem.get('ntype', ''),
                'signal_pct':  modem.get('signal_quality', 0),
                'rssi':        modem.get('rssi'),
                'rsrp':        modem.get('rsrp'),
                'rsrq':        modem.get('rsrq'),
                'sinr':        modem.get('sinr'),
                'band':        modem.get('band', ''),
                'temperature': modem.get('temperature'),
                'state':       modem.get('state', ''),
            },
        }

    # ── starlink ──────────────────────────────────────────────────────────────

    def _fetch_starlink(self):
        try:
            import grpc
            from google.protobuf.json_format import MessageToDict
            from yagrc import reflector as yr

            if self._grpc_stub is None:
                ch  = grpc.insecure_channel(self._starlink_host)
                ref = yr.GrpcReflectionClient()
                ref.load_protocols(ch, symbols=['SpaceX.API.Device.Device'])
                Req      = ref.message_class('SpaceX.API.Device.Request')
                # Typ des get_status-Feldes dynamisch ermitteln (firmware-unabhängig)
                status_type = Req.DESCRIPTOR.fields_by_name['get_status'].message_type.full_name
                StatusReq   = ref.message_class(status_type)
                self._grpc_stub = ref.service_stub_class('SpaceX.API.Device.Device')(ch)
                self._grpc_req  = Req(get_status=StatusReq())

            resp = self._grpc_stub.Handle(self._grpc_req, timeout=8)
            d    = MessageToDict(resp, preserving_proto_field_name=True)
            dish = d.get('dish_get_status', {})

            state = dish.get('state', 0)
            if isinstance(state, str):
                state = state.replace('DISH_STATE_', '') or 'CONNECTED'
            else:
                state = {0: 'CONNECTED', 1: 'SEARCHING', 2: 'BOOTING',
                         3: 'SLEEPING', 5: 'SLEEPING'}.get(state, str(state))

            obs    = dish.get('obstruction_stats', {})
            alerts = {k: v for k, v in dish.get('alerts', {}).items() if v is True}
            info   = dish.get('device_info', {})

            return {
                'reachable':           True,
                'state':               state,
                'uptime_s':            dish.get('device_state', {}).get('uptime_s', 0),
                'downlink_bps':        dish.get('downlink_throughput_bps', 0),
                'uplink_bps':          dish.get('uplink_throughput_bps', 0),
                'ping_ms':             dish.get('pop_ping_latency_ms', 0),
                'drop_rate':           dish.get('pop_ping_drop_rate', 0),
                'obstructed':          obs.get('currently_obstructed', False),
                'fraction_obstructed': obs.get('fraction_obstructed', 0),
                'alerts':              alerts,
                'hardware':            info.get('hardware_version', ''),
                'software':            info.get('software_version', ''),
            }
        except Exception as e:
            log.warning('Starlink gRPC: %s', e)
            self._grpc_stub = None
            return {'reachable': False, 'error': str(e)}
