"""Connectivity monitor: RUTX50 Router (WAN/Mobile) + Starlink dish."""
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

POLL_INTERVAL = 20  # seconds

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode    = ssl.CERT_NONE


def _gps_lesen(roh: dict):
    """Die Positionsmeldung des Routers in Zahlen übersetzen — oder None.

    Zwei Eigenheiten von RutOS, die man kennen muss:

    Erstens liefert es ALLES als Zeichenkette, auch Zahlen. Wer sie ungeprüft
    weiterreicht, hat später Strings in einer Karte.

    Zweitens antwortet es auch dann mit einer Position, wenn es gerade keinen
    Fix hat — dann steht dort die zuletzt bekannte. Ohne Fix ist sie wertlos
    und wird hier verworfen: eine alte Position, die aussieht wie eine
    aktuelle, ist auf einem Boot schlimmer als gar keine.

    Der Router führt zwei Zeitstempel. `timestamp` steht in seiner eigenen,
    hier falsch gestellten Zeitzone (UTC+1, während Sommerzeit gilt);
    `utc_timestamp` stimmte im Vergleich mit der Uhr des Bordrechners auf die
    Sekunde. Deshalb nur dieser.
    """
    def zahl(schluessel):
        try:
            return float(roh.get(schluessel))
        except (TypeError, ValueError):
            return None

    fix = str(roh.get('fix_status') or '0').strip()
    lat, lon = zahl('latitude'), zahl('longitude')
    if fix in ('0', '', 'none', 'None') or lat is None or lon is None:
        return None
    # 0/0 liegt im Atlantik vor Ghana und heisst in der Praxis "kein Wert".
    if abs(lat) < 1e-7 and abs(lon) < 1e-7:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None

    zeit = zahl('utc_timestamp')
    return {
        'lat': round(lat, 6),          # sechs Stellen sind gut 10 cm
        'lon': round(lon, 6),
        'hoehe_m': zahl('altitude'),
        'satelliten': int(zahl('satellites') or 0),
        'genauigkeit': zahl('accuracy'),
        'zeit': zeit,
    }


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
        # Der gRPC-Kanal MUSS neben dem Stub gehalten werden: nur ueber ihn
        # laesst er sich schliessen. Ohne ihn blieb bei jedem Fehler ein
        # verwaister Kanal samt Hintergrund-Threads und Sockets zurueck —
        # alle 20 s einer, auf einem Pi Zero mit 512 MB nicht harmlos.
        self._grpc_channel  = None
        # DHCP-Leases aendern sich traege; sie werden nicht bei jedem Poll neu
        # geholt, sondern jeden fuenften (100 s). Ein Router-Aufruf mehr im
        # 20-s-Takt waere fuer eine Liste, die sich stuendlich bewegt, Verschwendung.
        self._leases: list  = []
        self._leases_ok     = False
        self._leases_zaehler = 0

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name='connectivity')
        t.start()

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    # ── polling ───────────────────────────────────────────────────────────────

    def _loop(self):
        # Dieselbe Stoerung nicht dreimal je Minute in den Mitschnitt schreiben.
        # Am 05.09.2026 bestand er zu neun Zehnteln aus derselben Zeile ("HTTP
        # Error 401") — sie verdeckte alles andere, und das eigentlich
        # Interessante (seit wann, und wann wieder gut) stand nirgends.
        letzte_stoerung = None
        while True:
            try:
                router   = self._fetch_router()
                starlink = self._fetch_starlink()
                with self._lock:
                    self._status = {'router': router, 'starlink': starlink,
                                    'router_url': self._router_host,
                                    'ts': time.time()}
                if letzte_stoerung is not None:
                    log.warning('Verbindungsabfrage geht wieder (vorher: %s)', letzte_stoerung)
                    letzte_stoerung = None
            except Exception as e:
                text = f'{type(e).__name__}: {e}'
                if text != letzte_stoerung:
                    log.warning('connectivity poll: %s', text)
                    letzte_stoerung = text
            time.sleep(POLL_INTERVAL)

    # ── router ────────────────────────────────────────────────────────────────

    def _http(self, url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        # 15 s: /api/interfaces/status auf dem RUTX50 braucht je nach Router-Last
        # bis ~10 s; 6 s war zu knapp und ließ den ganzen Router-Poll scheitern.
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=15) as r:
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

    def _router_holen(self, pfad: str) -> dict:
        """Etwas beim Router abfragen — und bei einer Abweisung neu anmelden.

        Der Router wirft einen Zugang weg, ohne uns zu fragen: RutOS begrenzt
        die Zahl gleichzeitiger Sitzungen, und meldet sich jemand am Router an,
        faellt die aelteste heraus. Auch sonst gilt ein Zugang nicht ewig.

        `_token_headers` erneuerte nur nach ALTER (50 min). Wurde der Zugang
        vorher abgewiesen, half das nichts: die Anwendung lief in ein 401 und
        fragte danach fuenfzig Minuten lang immer wieder mit demselben toten
        Zugang nach. Am 05.09.2026 genau so vorgefunden — alle 20 Sekunden ein
        "HTTP Error 401: Unauthorized", und weil der erste Aufruf schon
        scheiterte, wurde der GESAMTE Verbindungszustand nicht mehr
        aktualisiert. Sichtbar wurde es an der Position: der Router hatte einen
        Fix, das Logbuch meldete "kein Fix vom Router".

        Deshalb: eine Abweisung wirft den Zugang weg und der Aufruf wird
        einmal wiederholt. Genau einmal — scheitert auch die frische Anmeldung,
        ist es kein Zugangsproblem mehr, und eine Schleife wuerde den Router nur
        beschaeftigen.
        """
        try:
            return self._http(self._router_host + pfad, headers=self._token_headers())
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):
                raise
            log.info('Router hat den Zugang abgewiesen (%s) — melde neu an', e.code)
            self._token = None
            return self._http(self._router_host + pfad, headers=self._token_headers())

    def _fetch_router(self):
        ifaces = self._router_holen('/api/interfaces/status')['data']
        modems = self._router_holen('/api/modems/status')['data']

        wan = [i for i in ifaces if i.get('area_type') == 'wan' and i.get('is_up')]
        wan.sort(key=lambda i: i.get('metric', 99))
        primary = wan[0] if wan else {}
        mobile  = next((i for i in wan if i.get('network_type') == 'mobile'), None)
        wired   = next((i for i in wan if i.get('network_type') == 'wired'),  None)
        modem   = modems[0] if modems else {}

        # Verbundene WLAN-Clients aus /api/wireless/interfaces/status.
        # Jedes Interface (pro Band) hat eine clients-Liste mit hostname/ip/signal.
        wifi_clients = []
        try:
            wifi = self._router_holen('/api/wireless/interfaces/status')
            for iface in (wifi.get('data') or []):
                ssid = iface.get('ssid', '')
                for c in (iface.get('clients') or []):
                    sig = c.get('signal')
                    if isinstance(sig, str):                  # "-56 dBm" → -56
                        try: sig = int(sig.split()[0])
                        except Exception: sig = None
                    wifi_clients.append({
                        'hostname': c.get('hostname') or '',
                        'mac':      c.get('macaddr', ''),
                        'ip':       c.get('ipaddr', ''),
                        'signal':   sig,
                        'band':     c.get('band', ''),
                        'standard': c.get('standard', ''),
                        'ssid':     ssid,
                    })
        except Exception as e:
            if not getattr(self, '_wifi_warn_logged', False):
                log.info('WLAN-Clients nicht verfügbar (%s) — Karte wird ausgeblendet', e)
                self._wifi_warn_logged = True

        # Vergebene Adressen (auch fuer Geraete am Kabel, die in der
        # WLAN-Liste grundsaetzlich fehlen). ACHTUNG: Eine Lease sagt, dass ein
        # Geraet hier eine Adresse bekommen hat — NICHT, dass es gerade da ist.
        # Sie laeuft stundenlang weiter, wenn das Geraet abgezogen wurde.
        if self._leases_zaehler <= 0:
            self._leases_zaehler = 5
            try:
                antwort = self._router_holen('/api/dhcp/leases/ipv4/status')
                self._leases = [{
                    'hostname':  l.get('hostname') or '',
                    'mac':       l.get('macaddr', ''),
                    'ip':        l.get('ipaddr', ''),
                    'interface': l.get('interface', ''),
                } for l in (antwort.get('data') or [])]
                self._leases_ok = True
            except Exception as e:
                if not getattr(self, '_lease_warn_logged', False):
                    log.info('DHCP-Leases nicht verfügbar (%s)', e)
                    self._lease_warn_logged = True
                self._leases_ok = False
        self._leases_zaehler -= 1

        # Die Position aus dem GNSS des Routers. Sie ist die EINZIGE Quelle
        # dafuer an Bord: am NMEA-2000-Bus sendet niemand 129025/129026/129029
        # — am laufenden Bus nachgesehen, dort kommen nur Tank-, Batterie-,
        # Lader- und Victron-eigene PGNs an.
        #
        # Scheitert der Abruf, ist das kein Fehler des ganzen Durchlaufs: die
        # Verbindungsdaten sind wichtiger als die Position, und ohne Fix gibt
        # es sie ohnehin nicht immer.
        gps = None
        try:
            roh = self._router_holen('/api/gps/position/status').get('data') or {}
            gps = _gps_lesen(roh)
        except Exception as e:
            if not getattr(self, '_gps_warn_logged', False):
                log.info('Position nicht verfügbar (%s)', e)
                self._gps_warn_logged = True

        # Wie es dem Router SELBST geht. Bisher wurde nur abgefragt, was er
        # ueber die Aussenwelt weiss — nie, wie es ihm dabei ergeht.
        #
        # Der Anlass ist handfest: das 2,4-GHz-Radio (ath10k, QCA4019) haengt
        # sich auf, danach setzt der Watchdog den ganzen Router zurueck. Der
        # einzige Zeuge war ein 8-kB-Ringpuffer im RAM, der nur die letzte
        # Minute vor dem Absturz behaelt, und das Ereignisprotokoll des Routers
        # ist seit dem 02.09.2026 beschaedigt. Ohne eigenen Mitschnitt steht man
        # nach jedem Absturz wieder vor derselben leeren Seite.
        #
        # Die Laufzeit ist dabei die wichtigste Zahl ueberhaupt: faellt sie,
        # hat er neu gestartet. Genauer und billiger laesst sich ein Neustart
        # nicht feststellen.
        gesundheit = None
        try:
            u = self._router_holen('/api/system/device/usage/status').get('data') or {}
            speicher = u.get('memory') or {}
            last = (u.get('load') or {}).get('min1')
            gesundheit = {
                'uptime_s':    u.get('uptime_seconds'),
                'ram_prozent': speicher.get('ram_percentage'),
                # Last als Prozent statt als Lastzahl: der RUTX50 hat vier
                # Kerne (am Geraet nachgezaehlt), Last 4,0 heisst also
                # ausgelastet. So steht sie neben dem Speicher auf derselben
                # Achse — und niemand muss sich merken, ab wann 1,7 viel ist.
                'cpu_prozent': (round(min(100.0, last / 4 * 100), 1)
                                if isinstance(last, (int, float)) else None),
            }
        except Exception as e:
            if not getattr(self, '_gesund_warn_logged', False):
                log.info('Router-Gesundheit nicht abrufbar (%s)', e)
                self._gesund_warn_logged = True

        # Die Radios einzeln. Aus der Client-Liste allein liesse sich nicht
        # unterscheiden, ob ein Band LEER oder AUS ist — und genau darum geht
        # es hier: faellt 2,4 GHz vor dem Neustart aus oder mit ihm?
        radios = []
        try:
            for r in (self._router_holen('/api/wireless/devices/status').get('data') or []):
                radios.append({
                    'id':    r.get('id'),
                    'band':  r.get('band'),
                    'kanal': r.get('channel'),
                    'up':    bool(r.get('up')),
                })
        except Exception as e:
            if not getattr(self, '_radio_warn_logged', False):
                log.info('Funkradios nicht abrufbar (%s)', e)
                self._radio_warn_logged = True

        wan_ip = primary.get('ipaddr', '') or ''
        return {
            'gesundheit':     gesundheit,
            'radios':         radios,
            'gps':            gps,
            'active_type':    primary.get('network_type', 'unknown'),
            'active_uptime':  primary.get('uptime', 0),
            'wired_up':       wired  is not None,
            'mobile_up':      mobile is not None,
            'wifi_clients':   wifi_clients,
            'wifi_client_count': len(wifi_clients),
            'dhcp_leases':    list(self._leases),
            'dhcp_ok':        self._leases_ok,
            'wan_ip':         wan_ip,
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

    def _close_grpc(self):
        """Schliesst den Status-Kanal zur Dish und vergisst den Stub.

        Wird ausschliesslich aus dem Poll-Thread gerufen (_fetch_starlink).
        Damit schliesst nie ein fremder Thread einen Kanal, den der Poll gerade
        benutzt. Seit die Steuerung entfernt ist, gibt es ohnehin nur noch
        diesen einen, lesenden Zugriff auf die Dish.
        """
        ch, self._grpc_channel, self._grpc_stub = self._grpc_channel, None, None
        if ch is not None:
            try:
                ch.close()
            except Exception as e:
                log.debug('Starlink-Kanal schliessen: %s', e)

    def _fetch_starlink(self):
        try:
            import grpc
            from google.protobuf.json_format import MessageToDict
            from yagrc import reflector as yr

            if self._grpc_stub is None:
                self._close_grpc()          # Rest eines frueheren Versuchs
                ch  = grpc.insecure_channel(self._starlink_host)
                # sofort merken, damit auch ein Fehler beim Laden der
                # Reflection-Protokolle den Kanal nicht zurueckliesse
                self._grpc_channel = ch
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
                'api_version':         str(d.get('api_version', '')),
                'uptime_s':            dish.get('device_state', {}).get('uptime_s', 0),
                'downlink_bps':        dish.get('downlink_throughput_bps', 0),
                'uplink_bps':          dish.get('uplink_throughput_bps', 0),
                'ping_ms':             dish.get('pop_ping_latency_ms', 0),
                'drop_rate':           dish.get('pop_ping_drop_rate', 0),
                'obstructed':          obs.get('currently_obstructed', False),
                'fraction_obstructed': obs.get('fraction_obstructed', 0),
                'alerts':              alerts,
            }
        except Exception as e:
            log.warning('Starlink gRPC: %s', e)
            self._close_grpc()
            return {'reachable': False, 'error': str(e)}

    # Hier stand set_starlink_sleep(): sie schickte per gRPC ein
    # dish_power_save an die Antenne. Auf Anweisung des Eigners entfernt —
    # Verdacht, dass die Steuerbefehle der Antenne schaden. Es geht seither
    # NICHTS Steuerndes mehr an die Starlink. Die Statusabfrage oben
    # (dish_get_status) ist rein lesend und bleibt.
