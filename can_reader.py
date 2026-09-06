"""CAN-Bus Interface: liest NMEA2000-Frames, verwaltet den Systemzustand."""
import asyncio
import logging
import threading
import time
from pathlib import Path

import can

from daily_stats import DailyStatsTracker

from nmea2000 import (
    DC_TYPE_ALTERNATOR, DC_TYPE_SOLAR,
    FAST_PACKET_PGNS, FastPacketReassembler,
    build_alert_frame,
    build_brightness_frames, build_charger_config_request, build_charger_register_frame,
    build_charger_setpoints_frame,
    build_inverter_mode_frame, build_iso_request, build_time_frame, make_can_id,
    parse_battery_stats, parse_bms_cells, parse_bms_pack, parse_brightness,
    parse_can_id, parse_charger_config_pgn, parse_dc_detailed, parse_dc_status, parse_fluid_level,
    parse_charger_status_pgn, parse_inverter_status, parse_ve_direct_ext,
    parse_solar_ext, parse_dcdc_ext,
    parse_iso_address_claim, parse_product_info,
    parse_temperature,
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
            'cell_voltage_min': None, 'cell_voltage_max': None,
            'temperature': None,
            # Erweiterte Shunt-Felder (ab 50-Byte-PGN 130900)
            'ttg': None,                  # Time to Go (Minuten)
            'starter_min_voltage': None,  # H15 – Min. Starterspannung (V)
            'starter_max_voltage': None,  # H16 – Max. Starterspannung (V)
            'energy_out_kwh': None,       # H17 – Entladene Energie gesamt (kWh)
            'energy_in_kwh': None,        # H18 – Geladene Energie gesamt (kWh)
        }
        self.tanks  = {'tank1': None, 'tank2': None}
        self.lights = {'channels': [0] * 9}
        # 'power' = batterieseitige Ladeleistung des MPPT (Ausgang, V × A auf der
        # Batterieseite). Für die Energiebilanz ist das die richtige Größe, und sie
        # hat genau EINE Quelle: PGN 127508 (DC-Status, dc_type Solar) bzw. PGN
        # 130910 (VE.Direct type 3) – beide liefern denselben physikalischen Wert.
        # Die Panel-Leistung (Eingangsseite, PPV aus PGN 130912) liegt ausschließlich
        # im eigenen Feld 'ppv' und schreibt NICHT mehr nach 'power'; das Hin und Her
        # zwischen beiden Quellen hat die Solar-Kurve dauerhaft sägezahnen lassen.
        self.solar      = {
            'charge_state_n2k': None,
            'power': None, 'current': None, 'voltage': None,
            # MPPT 75/15 – aus PGN 130910 (type 3)
            'cs': None, 'cs_label': None,
            # MPPT 75/15 – aus PGN 130912; 'ppv' = Panel-/Eingangsleistung (W)
            'vpv': None, 'ppv': None,
            'yield_today_wh': None, 'max_power_today_w': None,
            'mppt_mode': None, 'mppt_mode_label': None,
            # Batterieseitige Klemmwerte aus PGN 130910 (Typ 3) — gleiches
            # Muster wie inverter['ac_power']: werden geschrieben, fehlten aber
            # im Konstruktor und tauchten erst beim ersten Frame auf.
            'dc_voltage': None, 'dc_current': None,
        }
        self.alternator = {'power': None, 'current': None, 'voltage': None}
        self.bms = {
            'voltage': None, 'current_total': None,
            'current_charge': None, 'current_discharge': None,
            'soc': None, 'capacity_ah': None, 'remaining_kwh': None,
            'lowest_cell_v': None, 'lowest_cell_nr': None,
            'highest_cell_v': None, 'highest_cell_nr': None,
            'lowest_temp': None, 'highest_temp': None,
            'cell_count': None,
            'allow_charge': None, 'allow_discharge': None,
            'comm_error': None,
            'alarm_min_volt': None, 'alarm_max_volt': None,
            'alarm_min_temp': None, 'alarm_max_temp': None,
            'cells': [],
        }
        # VE.Direct-Geräte (via PGN 130910 / 127507 / 127750)
        # 'ac_power' = AC-Scheinleistung aus PGN 130910 (Typ 2). Das Feld MUSS
        # hier stehen: der 130910-Zweig schreibt es, und ohne Anlage im
        # Konstruktor waechst das Dict erst beim ersten Frame um einen Schluessel
        # — die Form der API-/WebSocket-Nutzlast aenderte sich also zur Laufzeit.
        self.inverter = {'state': None, 'power': None, 'cs': None, 'cs_label': None,
                         'ac_voltage': None, 'ac_current': None, 'ac_power': None,
                         'dc_voltage': None, 'dc_current': None,
                         'err': None, 'warn': None}  # err = AR (Alarm Reason), warn = WARN
        # charge_state_n2k = grober NMEA-Standardzustand aus PGN 127507. Bewusst
        # getrennt von cs_label (feineres VE.Direct-Label aus PGN 130910).
        self.charger  = {'state': None, 'power': None, 'cs': None, 'cs_label': None,
                         'charge_state_n2k': None,
                         'dc_voltage': None, 'dc_current': None, '_last_seen': 0.0}  # Smart IP43
        self.orion    = {
            'state': None, 'power': None, 'cs': None, 'cs_label': None,
            'charge_state_n2k': None,
            'dc_voltage': None, 'dc_current': None,   # Ausgang (Batterieseite) aus PGN 130910
            # Orion-XS erweitert – aus PGN 130913
            'output_power': None,
            'input_voltage': None, 'input_current': None, 'input_power': None,
            'off_reason': None, 'off_reason_label': None,
        }
        # Zeitpunkt des letzten Frames je Quelle (time.monotonic()). Wird vom
        # CAN-Thread geschrieben und vom Event-Loop gelesen – eine einzelne
        # dict-Zuweisung ist unter dem GIL atomar, deshalb ohne Lock.
        self._updated: dict[str, float] = {}

    def touch(self, source: str):
        """Vermerkt, dass für diese Quelle gerade ein Frame angekommen ist."""
        self._updated[source] = time.monotonic()

    def age_s(self, source: str):
        """Sekunden seit dem letzten Frame dieser Quelle (None = nie gesehen)."""
        t = self._updated.get(source)
        return round(time.monotonic() - t, 1) if t is not None else None

    def to_dict(self) -> dict:
        """Momentaufnahme für API und WebSocket.

        Jede Gruppe bekommt zusätzlich '_age_s': Sekunden seit dem letzten Frame
        dieser Quelle (None = seit dem Start nie gesehen). Fällt ein Sensor aus,
        friert sein letzter Wert sonst unbemerkt ein und wird weiter als aktueller
        Messwert angezeigt; mit dem Alter kann das Frontend ihn kennzeichnen.
        Die Werte selbst werden bewusst NICHT auf None gesetzt – das würde
        bestehende Anzeigen brechen.
        """
        charger_d = {k: v for k, v in self.charger.items() if k != '_last_seen'}
        charger_d['active'] = (time.time() - self.charger['_last_seen']) < 30
        out = {
            'battery':    dict(self.battery),
            'tanks':      dict(self.tanks),
            'lights':     dict(self.lights),
            'solar':      dict(self.solar),
            'alternator': dict(self.alternator),
            'bms':        dict(self.bms),
            'inverter':   dict(self.inverter),
            'charger':    charger_d,
            'orion':      dict(self.orion),
        }
        now = time.monotonic()
        for name, d in out.items():
            t = self._updated.get(name)
            d['_age_s'] = round(now - t, 1) if t is not None else None
        # Die Batterietemperatur kommt aus einer eigenen Quelle (PGN 130312) und
        # kann ausfallen, während der Shunt weiter sendet – deshalb eigenes Alter.
        t = self._updated.get('battery_temperature')
        out['battery']['_temperature_age_s'] = round(now - t, 1) if t is not None else None
        return out


# ── Wie oft die Oberflaeche einen neuen Zustand bekommt ─────────────────────
#
# Gemessen am 04.09.2026 auf dem Pi Zero W: 318 CAN-Rahmen je Sekunde. Bei
# einem Fenster von 50 ms hiess das 20 Rundrufe je Sekunde, und jeder kostet
# 2,74 ms auf dem Pi (Zustand einsammeln, 17 Alarmregeln pruefen, JSON
# schreiben) — zusammen 5,5 % des einzigen Kerns. Am anzeigenden Geraet kam
# derselbe Takt noch einmal an und liess dort die ganze Oberflaeche 20-mal je
# Sekunde neu zeichnen.
#
# Der alte Wert stand seit einer Zeit mit deutlich weniger Busverkehr da; der
# Kommentar daneben begruendete ihn mit "rund 2,4 Nachrichten/s", was heute
# nicht mehr zutrifft. 200 ms viertelt beides. Fuenf Bilder je Sekunde sind
# fuer Messwerte reichlich — eine Spannung, die sich in der zweiten
# Nachkommastelle bewegt, gewinnt nichts durch viermal haeufigeres Anzeigen.
RUNDRUF_TAKT_S = 0.20

# Wer gerade geschaltet hat, wartet auf genau diese eine Antwort — da waeren
# 200 ms spuerbar. In den Sekunden nach einem Befehl gilt deshalb wieder das
# alte, kurze Fenster (siehe CanInterface.eilig).
EILFENSTER_TAKT_S = 0.05
EILFENSTER_S      = 3.0


class CanInterface:
    """Verwaltet den CAN-Bus in einem eigenen Thread und benachrichtigt asyncio."""

    def __init__(self, channel: str, state: BoatState, stats_path: Path | None = None):
        self.channel      = channel
        self.state        = state
        self._bus         = None
        self._fp          = FastPacketReassembler()
        self._seq_id      = 0
        # Der Fast-Packet-Zähler wird sowohl aus dem Event-Loop (API-Aufrufe wie
        # Helligkeit/Alert) als auch aus dem CAN-Thread hochgezählt. Ohne Lock
        # können sich zwei Sender dieselbe Sequenznummer greifen und ihre Frames
        # beim Empfänger gegenseitig zerlegen.
        self._seq_lock    = threading.Lock()
        self._running     = False
        self._loop        = None
        self._on_change   = None   # async callback(data: dict)
        self._broadcast_pending = False
        self._broadcast_lock    = None   # asyncio.Lock, wird im Loop angelegt
        self._eilig_bis         = 0.0    # monotone Zeit, bis zu der schnell gerundfunkt wird
        self._network:  dict = {}   # (pgn, src) → tracking entry
        self._last_raw: dict = {}   # pgn → {src, len, hex} (Debug)
        self._dc_types: dict = {}   # instance → dc_type (from PGN 127506)
        self._device_names: dict = {}   # src → model_id (aus PGN 126996)
        self._device_addrclaim: dict = {}  # src → name_bytes (aus PGN 60928)
        self._service_instance: int = 0
        self._starter_instance: int = 1
        self._charger_config_cb = None   # optional: callback(absorption_v, float_v)
        self._daily = DailyStatsTracker(stats_path or Path('daily_stats.json'))

    def _next_seq(self) -> int:
        """Liefert die nächste Fast-Packet-Sequenznummer (0-7), thread-sicher."""
        with self._seq_lock:
            seq = self._seq_id
            self._seq_id = (seq + 1) & 0x07
            return seq

    def set_battery_instances(self, service: int, starter: int):
        self._service_instance = service
        self._starter_instance = starter

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def on_change(self, coro_fn):
        """Setzt die async-Callback-Funktion, die bei Datenänderung aufgerufen wird."""
        self._on_change = coro_fn

    def eilig(self, dauer_s: float = EILFENSTER_S) -> None:
        """Für die nächsten Sekunden schnell rundrufen.

        Zu rufen, wenn gerade jemand geschaltet hat. Der normale Takt von 200 ms
        ist für Messwerte gedacht, die niemand erwartet; wer eben auf einen
        Knopf gedrückt hat, wartet dagegen auf genau diese eine Antwort. In
        diesem Fenster gilt wieder das alte, kurze Fenster.
        """
        self._eilig_bis = time.monotonic() + dauer_s

    # ── Senden ──────────────────────────────────────────────────────────────

    def send_time(self, ts: float):
        if self._bus is None:
            log.warning("CAN nicht verbunden – Senden nicht möglich")
            return False
        can_id, data = build_time_frame(ts)
        try:
            self._bus.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=True))
            log.info("Systemzeit gesendet (PGN 126992)")
            return True
        except can.CanError as e:
            log.error("CAN-Sendefehler Systemzeit: %s", e)
            return False

    def _find_vedirect_gateway_src(self) -> int:
        """Gibt die CAN-Quelladresse des VE.Direct-Gateways zurück.
        Sucht nach dem Gerät, das PGN 130910 (VE.Direct Extended) sendet.
        Fällt auf 0xFF (broadcast) zurück wenn unbekannt.
        """
        # Kopie: der CAN-Thread trägt laufend neue Schlüssel ein, eine Iteration
        # über das Original könnte mitten drin RuntimeError werfen. dict.copy()
        # läuft komplett in C und ist damit unter dem GIL atomar – kein Lock im
        # heißen Pfad nötig, der Schreiber bleibt unverändert schnell.
        for (pgn, src, _inst) in self._network.copy():
            if pgn == 130910:
                return src
        return 0xFF

    def send_inverter_mode(self, mode: int, instance: int = 0):
        """Sendet PGN 61184 – VE.Direct Control adressiert an den Gateway (PDU1).
        mode:     2=An, 4=Aus, 5=Eco
        instance: Geräte-Instanz des Inverters (Standard 0)
        """
        self.eilig()
        if self._bus is None:
            log.warning("CAN nicht verbunden – Senden nicht möglich")
            return False
        dst = self._find_vedirect_gateway_src()
        can_id, data = build_inverter_mode_frame(mode, instance, dst)
        try:
            self._bus.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=True))
            log.info("Inverter-Modus %d → Instanz %d → Gateway Adr.%d (PGN 61184)",
                     mode, instance, dst)
            return True
        except can.CanError as e:
            log.error("CAN-Sendefehler Inverter: %s", e)

            return False
    def send_charger_setpoints(self, absorption_v: float, float_v: float, instance: int = 1):
        """Setzt Absorptions- und Float-Spannung des IP43 via PGN 61184 (commandType=1).
        Benötigt aktualisierte VE.Direct-Gateway-Firmware um wirksam zu sein.
        """
        self.eilig()
        if self._bus is None:
            log.warning("CAN nicht verbunden – Charger-Setpoints nicht gesendet")
            return False
        dst    = self._find_vedirect_gateway_src()
        abs_mv = round(absorption_v * 1000)
        flt_mv = round(float_v * 1000)
        can_id, data = build_charger_setpoints_frame(abs_mv, flt_mv, instance, dst)
        try:
            self._bus.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=True))
            log.info("Charger Setpoints → Abs %.2fV / Float %.2fV → Inst %d → Gw Adr.%d",
                     absorption_v, float_v, instance, dst)
            return True
        except can.CanError as e:
            log.error("CAN-Sendefehler Charger Setpoints: %s", e)

            return False
    def send_charger_register(self, reg: int, val: int, instance: int = 1, size: int = 2):
        """Schreibt ein Register via PGN 61184 (cmdType=2).
        size=1 für 8-Bit-Register (z.B. DeviceMode 0x0200).
        size=2 für 16-Bit-Register (Standard).
        """
        self.eilig()
        if self._bus is None:
            return
        dst = self._find_vedirect_gateway_src()
        try:
            can_id, data = build_charger_register_frame(reg, val, size=size,
                                                         instance=instance, dst=dst)
            self._bus.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=True))
            log.info("Charger Register 0x%04X = %d (size=%d) → Inst %d → Gw Adr.%d",
                     reg, val, size, instance, dst)
            return True
        except can.CanError as e:
            log.error("CAN-Sendefehler Register Write: %s", e)

            return False
    def send_charger_config_request(self):
        """Sendet ISO Request für PGN 130914 → Teensy liest IP43-Setpoints und antwortet."""
        if self._bus is None:
            return False
        dst = self._find_vedirect_gateway_src()
        try:
            can_id, data = build_charger_config_request(dst)
            self._bus.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=True))
            log.debug("Charger Config Request → Gateway Adr.%d", dst)
            return True
        except can.CanError as e:
            log.warning("Charger Config Request Fehler: %s", e)
            return False

    def request_product_info(self):
        """Sendet PGN 59904 ISO Request für PGN 126996 (Produktinfo) an alle Geräte."""
        if self._bus is None:
            return
        try:
            can_id, data = build_iso_request(126996, RPI_SOURCE_ADDRESS)
            self._bus.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=True))
            log.info("ISO Request für PGN 126996 (Produktinfo) gesendet")
        except can.CanError as e:
            log.warning("ISO Request Fehler: %s", e)

    def send_brightness(self, values: list[int]):
        self.eilig()
        if self._bus is None:
            log.warning("CAN nicht verbunden – Senden nicht möglich")
            return False
        frames = build_brightness_frames(values, self._next_seq())
        for can_id, data in frames:
            try:
                self._bus.send(can.Message(
                    arbitration_id=can_id, data=data, is_extended_id=True))
            except can.CanError as e:
                log.error("CAN-Sendefehler: %s", e)
                # Ein halb gesendetes Fast-Packet ist kein Teilerfolg, sondern
                # ein kaputter Rahmen — der Aufrufer soll das erfahren.
                return False
        return True

    def send_alert(self, alert_id: int, active: bool, priority: int = 0):
        """Sendet PGN 126983 (Alert) als Fast-Packet — löst einen NMEA-2000-Buzzer
        aus (active=True) bzw. nimmt den Alarm zurück (active=False)."""
        if self._bus is None:
            log.warning("CAN nicht verbunden – Alert nicht gesendet")
            return False
        frames = build_alert_frame(alert_id, active, priority=priority, seq=self._next_seq())
        for can_id, data in frames:
            try:
                self._bus.send(can.Message(
                    arbitration_id=can_id, data=data, is_extended_id=True))
            except can.CanError as e:
                log.error("CAN-Sendefehler Alert: %s", e)
                return False
        log.info("Alert 126983 id=%d active=%s (%d Frames) gesendet", alert_id, active, len(frames))
        return True

    # ── Empfangen ───────────────────────────────────────────────────────────

    # PGNs die eine Instanz im Payload haben — werden nach Payload-Assembly getrackt
    _INSTANCE_PGNS = {127505, 127506, 127507, 127508, 130312, 130910, 130912, 130913}

    def _track_network(self, pgn: int, src: int, instance: int | None = None):
        now = time.monotonic()
        key = (pgn, src, instance)
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
        # Erst kopieren, dann sortieren: sorted() ruft pro Element Python-Code auf
        # und würde dem CAN-Thread Gelegenheit geben, das Dict zu vergrößern.
        for (pgn, src, instance), e in sorted(
            self._network.copy().items(),
            key=lambda x: (x[0][1], x[0][0], x[0][2] if x[0][2] is not None else -1)
        ):
            ivs = list(e['intervals'])   # Liste wird nebenläufig angehängt/ersetzt
            avg_ms = round(sum(ivs) / len(ivs) * 1000) if ivs else None
            result.append({
                'pgn':         pgn,
                'src':         src,
                'instance':    instance,
                'device_name': self._device_names.get(src, ''),
                # 64-Bit-NAME aus dem Address Claim (PGN 60928). Die Quelladresse
                # wird beim Claim ausgehandelt und kann sich nach einem Neustart
                # aendern; das NAME nicht. Wer ein Geraet wiedererkennen will,
                # nimmt dieses Feld — die Geraeteuebersicht tut genau das.
                'name_hex':    self._device_addrclaim.get(src, ''),
                'description': PGN_NAMES.get(pgn, f'PGN {pgn}'),
                'count':       e['count'],
                'interval_ms': avg_ms,
                'age_s':       round(now - e['last_seen'], 1),
            })
        return result

    def get_raw_frames(self) -> dict:
        """Backward-compat: ein Eintrag pro PGN (neueste src)."""
        result = {}
        for (pgn, src, instance), data in self._last_raw.copy().items():
            if pgn not in result:
                result[pgn] = data
        return result

    def get_raw_frame(self, pgn: int, src: int, instance: int | None = None) -> dict | None:
        return self._last_raw.get((pgn, src, instance))

    def time_since_last_message(self) -> float:
        entries = list(self._network.values())   # atomare Kopie, s. o.
        if not entries:
            return float('inf')
        now    = time.monotonic()
        latest = max(e['last_seen'] for e in entries)
        return round(now - latest, 1)

    @staticmethod
    def _merge(target: dict, values: dict) -> bool:
        """Übernimmt alle Nicht-None-Werte aus `values` nach `target`.

        Liefert True, wenn sich dabei mindestens ein Wert wirklich geändert hat.
        Zwei Punkte, die vorher Dauer-Broadcasts erzeugt haben:
          * Verglichen wird schlüsselweise – nicht das gelieferte Teil-Dict gegen
            das komplette Zustands-Dict (das war immer ungleich).
          * None bedeutet „in diesem Frame nicht enthalten" und überschreibt
            keinen vorhandenen Wert; sonst pendeln zwei Quellen gegeneinander.
        """
        changed = False
        for k, v in values.items():
            if v is not None and target.get(k) != v:
                target[k] = v
                changed = True
        return changed

    def _handle(self, msg: can.Message):
        pgn, src = parse_can_id(msg.arbitration_id)
        raw = bytes(msg.data)

        is_fp = pgn in FAST_PACKET_PGNS
        # Instanz-PGNs werden erst nach Payload-Assembly mit korrekter Instanz getrackt
        if pgn not in self._INSTANCE_PGNS:
            if not is_fp or (raw and (raw[0] & 0x1F) == 0):
                self._track_network(pgn, src, None)

        payload = self._fp.process(pgn, src, raw) if is_fp else raw

        if payload is None:
            return

        # Debug: letzten vollständigen Payload je (pgn, src, instance) merken
        _inst = None
        if pgn in (127507, 127508, 130910, 130912, 130913) and payload:  _inst = payload[0] & 0x0F
        elif pgn == 127505 and payload:                _inst = payload[0] & 0x0F
        # PGN 130312: Byte 0 ist die SID, die Instanz steht in Byte 1. Mit der
        # SID waere _last_raw unbegrenzt gewachsen und der Schluessel haette
        # nicht mehr zu dem aus _track_network gepasst (Detailansicht → 404).
        elif pgn == 130312 and len(payload) > 1:       _inst = payload[1]
        elif pgn == 127506 and len(payload) > 1:       _inst = payload[1]
        self._last_raw[(pgn, src, _inst)] = {'src': src, 'len': len(payload), 'hex': payload.hex()}

        changed = False

        if pgn == 127506:
            p = parse_dc_detailed(payload)
            if p:
                self._track_network(pgn, src, p['instance'])
                self._dc_types[p['instance']] = p['dc_type']

        elif pgn == 127505:
            p = parse_fluid_level(payload)
            if p:
                self._track_network(pgn, src, p['instance'])
                self.state.touch('tanks')
                key = 'tank1' if p['instance'] == 0 else 'tank2'
                if self.state.tanks[key] != p['level']:
                    self.state.tanks[key] = p['level']
                    changed = True

        elif pgn == 127508:
            p = parse_dc_status(payload)
            if p:
                inst = p['instance']
                self._track_network(pgn, src, inst)
                dc_type = self._dc_types.get(inst)

                if inst == self._service_instance and dc_type not in (DC_TYPE_SOLAR, DC_TYPE_ALTERNATOR):
                    self.state.touch('battery')
                    if self._merge(self.state.battery,
                                   {'voltage': p.get('voltage'), 'current': p.get('current')}):
                        changed = True
                elif inst == self._starter_instance and dc_type not in (DC_TYPE_SOLAR, DC_TYPE_ALTERNATOR):
                    self.state.touch('battery')
                    if self._merge(self.state.battery, {'starter_voltage': p.get('voltage')}):
                        changed = True

                if dc_type == DC_TYPE_SOLAR:
                    v, i = p.get('voltage'), p.get('current')
                    # batterieseitige Ladeleistung – die einzige Quelle für solar.power
                    pwr = round(v * i, 1) if v is not None and i is not None else None
                    self.state.touch('solar')
                    if self._merge(self.state.solar, {'power': pwr, 'current': i, 'voltage': v}):
                        changed = True
                elif dc_type == DC_TYPE_ALTERNATOR:
                    v, i = p.get('voltage'), p.get('current')
                    pwr = round(v * i, 1) if v is not None and i is not None else None
                    self.state.touch('alternator')
                    if self._merge(self.state.alternator, {'power': pwr, 'current': i, 'voltage': v}):
                        changed = True

        elif pgn == 130900:
            p = parse_battery_stats(payload)
            if p:
                self.state.touch('battery')
                # Die Temperatur hat mit PGN 130312 eine eigene, maßgebliche
                # Quelle. Hier steht sie oft als NaN (None) drin und hat den
                # echten Wert überschrieben – echter Wert, None, echter Wert …
                # also bei jedem Frame eine Änderung und damit einen Broadcast.
                # Deshalb: sobald 130312 schon einmal gesendet hat, gehört das
                # Feld allein dieser Quelle. Nur solange sie schweigt, dient
                # 130900 als Rückfall.
                if self.state.age_s('battery_temperature') is not None:
                    p.pop('temperature', None)
                # Alle übrigen Felder hat NUR diese PGN – hier MUSS None auch
                # durchschlagen: 'soc', 'ttg' und 'time_since_full' sind None,
                # wenn der Shunt keinen gültigen Wert hat. Würde man None
                # unterdrücken, bliebe der letzte Wert stehen und die
                # Hafen-SOC-Regelung (main.broadcast → charge_ctrl.update_soc)
                # sowie die Alarmprüfung liefen auf einem eingefrorenen SOC
                # weiter – genau das, was ein None verhindern soll.
                for k, v in p.items():
                    if self.state.battery.get(k) != v:
                        self.state.battery[k] = v
                        changed = True
                # Tägliche Energie aus Shunt-Strom (genauer als BMS)
                self._daily.update(
                    current_a=self.state.battery.get('current'),
                    soc=p.get('soc'),
                    ts=time.time(),
                )

        elif pgn == 130312:
            p = parse_temperature(payload)
            if p:
                # Byte 0 ist die SID (zaehlt pro Nachricht hoch), NICHT die
                # Instanz. Mit der SID als Schluessel legte der Netzwerk-Tracker
                # bis zu 16 Phantom-Eintraege je Geraet an; die echte Instanz
                # steht in Byte 1 und kommt vom Parser als p['instance'].
                self._track_network(pgn, src, p['instance'])
                # Maßgebliche Temperaturquelle – aber nur setzen, wenn wirklich
                # ein Messwert vorliegt.
                if p.get('temperature_c') is not None:
                    self.state.touch('battery_temperature')
                    if self._merge(self.state.battery, {'temperature': p['temperature_c']}):
                        changed = True

        elif pgn == 130901:
            p = parse_bms_pack(payload)
            if p:
                self.state.touch('bms')
                p = self._correct_bms_currents(p)
                for k, v in p.items():
                    if self.state.bms.get(k) != v:
                        self.state.bms[k] = v
                        changed = True

        elif pgn == 130902:
            p = parse_bms_cells(payload)   # gibt Dict {'cell_count':.., 'cells':[..]} zurück
            if p:
                self.state.touch('bms')
            if p and self.state.bms.get('cells') != p['cells']:
                self.state.bms['cells'] = p['cells']
                changed = True

        elif pgn == 126720:
            p = parse_brightness(payload)
            if p:
                self.state.touch('lights')
            if p and self.state.lights['channels'] != p['channels']:
                self.state.lights['channels'] = p['channels']
                changed = True

        elif pgn == 130910:
            p = parse_ve_direct_ext(payload)
            self._track_network(pgn, src, payload[0] if payload else None)
            if p:
                fields = ('state', 'power', 'cs', 'cs_label',
                          'dc_voltage', 'dc_current', 'ac_voltage', 'ac_current', 'ac_power',
                          'err', 'warn')
                if p['type'] == 2 and p['instance'] == 0:  # Inverter
                    target = self.state.inverter
                    self.state.touch('inverter')
                    if p.get('cs_label'):
                        p['state'] = p['cs_label']
                    for k in fields:
                        v = p.get(k)
                        if v is not None and target.get(k) != v:
                            target[k] = v; changed = True
                    # power=0 explizit aktualisieren (v is not None blockiert 0.0 nicht,
                    # aber der separate Block prüft nochmal)
                    if 'power' in p and target.get('power') != p['power']:
                        target['power'] = p['power']; changed = True
                elif p['type'] == 1 and p['instance'] == 0:  # Orion-XS DC-DC
                    target = self.state.orion
                    self.state.touch('orion')
                    if p.get('cs_label'):
                        p['state'] = p['cs_label']
                    for k in ('state','power','cs','cs_label','dc_voltage','dc_current'):
                        v = p.get(k)
                        if v is not None and target.get(k) != v:
                            target[k] = v; changed = True
                elif p['type'] == 3:  # Solar MPPT (inst 3)
                    target = self.state.solar
                    self.state.touch('solar')
                    if p.get('cs_label'):
                        p['state'] = p['cs_label']
                    # 'power' ist hier dc_voltage x dc_current, also die
                    # batterieseitige Ladeleistung – passt zu PGN 127508.
                    for k in ('cs', 'cs_label', 'dc_voltage', 'dc_current', 'power'):
                        v = p.get(k)
                        if v is not None and target.get(k) != v:
                            target[k] = v; changed = True
                    if p.get('mode') is not None and target.get('mppt_mode') != p['mode']:
                        target['mppt_mode'] = p['mode']; changed = True
                elif p['type'] == 0:  # Lader (IP43 = inst 1)
                    target = self.state.charger
                    target['_last_seen'] = time.time()
                    self.state.touch('charger')
                    if p.get('cs_label'):
                        p['state'] = p['cs_label']
                    for k in ('state','power','cs','cs_label','dc_voltage','dc_current'):
                        v = p.get(k)
                        if v is not None and target.get(k) != v:
                            target[k] = v; changed = True

        elif pgn == 127750:
            p = parse_inverter_status(payload)
            if p:
                self._track_network(pgn, src, None)
                self.state.touch('inverter')
                if p.get('state') and self.state.inverter.get('state') != p['state']:
                    self.state.inverter['state'] = p['state']
                    changed = True

        elif pgn == 127507:
            p = parse_charger_status_pgn(payload)
            if p:
                self._track_network(pgn, src, p['instance'])
                new_state = p.get('state')
                inst = p['instance']
                # WICHTIG: 127507 liefert den GROBEN NMEA-Standardzustand
                # (tN2kChargeState). Das VE.Direct-Label aus PGN 130910 ist
                # feiner: das Gateway bildet z. B. CS 5 (Absorption) UND CS 6
                # (Storage) beide auf N2kCS_Float ab. Wuerden wir 127507 in
                # 'state' schreiben, kippte die Anzeige im Sekundentakt zwischen
                # 'Storage' und 'Float' — samt Broadcast bei jedem Wechsel.
                # Deshalb bekommt der Standardzustand ein EIGENES Feld;
                # 'state'/'cs_label' bleiben allein bei 130910.
                ziel = {1: self.state.charger, 0: self.state.orion,
                        3: self.state.solar}.get(inst)
                gruppe = {1: 'charger', 0: 'orion', 3: 'solar'}.get(inst)
                if ziel is not None:
                    if inst == 1:
                        self.state.charger['_last_seen'] = time.time()
                    self.state.touch(gruppe)
                    if new_state and new_state != 'Unbekannt' and \
                            ziel.get('charge_state_n2k') != new_state:
                        ziel['charge_state_n2k'] = new_state
                        changed = True

        elif pgn == 130912:
            p = parse_solar_ext(payload)
            if p:
                self._track_network(pgn, src, p['instance'])
                self.state.touch('solar')
                for k in ('vpv', 'ppv', 'yield_today_wh', 'max_power_today_w',
                          'mppt_mode', 'mppt_mode_label'):
                    v = p.get(k)
                    if v is not None and self.state.solar.get(k) != v:
                        self.state.solar[k] = v; changed = True
                # PPV ist die PANEL-Leistung (Eingangsseite) und bleibt in 'ppv'.
                # Sie wird bewusst NICHT mehr nach 'power' geschrieben: 'power' ist
                # die batterieseitige Ladeleistung. Zwei Schreiber auf demselben
                # Feld haben die Solar-Kurve dauerhaft sägezahnen lassen, obwohl
                # beide Messwerte für sich korrekt waren (Wirkungsgrad-Differenz).

        elif pgn == 130913:
            p = parse_dcdc_ext(payload)
            if p:
                self._track_network(pgn, src, p['instance'])
                self.state.touch('orion')
                for k in ('output_power', 'input_voltage', 'input_current',
                          'input_power', 'off_reason', 'off_reason_label'):
                    v = p.get(k)
                    if v is not None and self.state.orion.get(k) != v:
                        self.state.orion[k] = v; changed = True

        elif pgn == 130914:
            p = parse_charger_config_pgn(payload)
            if p and p.get('instance') == 1:   # Smart IP43
                self._track_network(pgn, src, p['instance'])
                av = p.get('absorption_v')
                fv = p.get('float_v')
                if av is not None and fv is not None and self._charger_config_cb:
                    self._charger_config_cb(av, fv)

        elif pgn == 60928:
            p = parse_iso_address_claim(payload)
            if p:
                self._device_addrclaim[src] = p['name_bytes']

        elif pgn == 126996:
            p = parse_product_info(payload)
            if p and p['model_id']:
                self._device_names[src] = p['model_id']
                log.info("Gerät %d: %s (SW: %s)", src, p['model_id'], p['sw_version'])

        if changed:
            self._schedule_broadcast()

    def get_daily_stats(self, n: int = 7) -> list[dict]:
        return self._daily.get_last_n_days(n)

    # Hysterese der BMS-Stromkorrektur: eingeschaltet wird sie erst ab
    # Skalierungsfaktoren außerhalb dieses Bereichs sind unbrauchbar: negativ
    # heißt Vorzeichenkonflikt, sehr groß/klein entsteht, wenn das BMS-Netto
    # nahe Null liegt und eine Multiplikation die Einzelströme explodieren ließe.
    _BMS_FACTOR_MIN = 0.2
    _BMS_FACTOR_MAX = 5.0

    def _correct_bms_currents(self, p: dict) -> dict:
        """Korrigiert BMS-Lade-/Entladestrom proportional anhand des genaueren Shunt-Stroms.

        Weicht das BMS-Netto vom Shunt deutlich ab, werden die BMS-Werte so
        skaliert, dass die Differenz (charge − discharge) dem Shunt entspricht;
        das BMS-Verhältnis (wie viel Laden vs. Entladen) bleibt erhalten.

        Korrigiert wird IMMER, nicht erst ab einer Abweichung. Vorher sprang die
        Korrektur bei 5 A an und erst unter 3 A wieder ab — mit dem Ergebnis,
        dass Zufluss minus Verbrauch im Normalbetrieb eben NICHT der Bilanz
        entsprach, sondern bis zu fünf Ampere daneben lag. Drei Zahlen, von
        denen zwei nicht zur dritten passen, sind schlechter als eine.

        Die Hysterese war nur deshalb nötig, weil es zwei Betriebsarten gab
        (roh und korrigiert) und die Anzeige an der Schwelle zwischen ihnen
        flatterte. Mit nur noch einer Betriebsart entfällt der Grund.

        Was bleibt: bei Vorzeichenkonflikt (BMS lädt, Shunt entlädt oder
        umgekehrt) oder wenn das BMS-Netto nahe Null liegt, werden beide Ströme
        direkt aus dem Shunt abgeleitet. Eine Skalierung ergäbe dort absurde
        Werte, und der Shunt ist die verlässlichere Quelle.
        """
        shunt = self.state.battery.get('current')
        if shunt is None:
            return p
        charge    = p.get('current_charge')    or 0.0
        discharge = p.get('current_discharge') or 0.0
        bms_net   = charge - discharge   # positiv = Laden

        # Beide nahe Null → nichts zu verteilen, und eine Skalierung von
        # Rauschen auf Rauschen ergäbe nur Zappeln.
        if abs(bms_net) < 0.5 and abs(shunt) < 0.5:
            return p

        p = dict(p)    # Kopie — Original nicht ändern
        factor = shunt / bms_net if abs(bms_net) > 0.1 else 0.0
        if self._BMS_FACTOR_MIN <= factor <= self._BMS_FACTOR_MAX:
            # Proportionale Skalierung: Verhältnis charge/discharge bleibt gleich.
            # max(0.0, …) bleibt: Lade- und Entladestrom sind per Definition
            # Beträge; ein negativer Rohwert aus dem BMS darf nicht als
            # negativer Ladestrom in History und Alarmregeln landen.
            p['current_charge']    = round(max(0.0, charge    * factor), 2)
            p['current_discharge'] = round(max(0.0, discharge * factor), 2)
        else:
            # BMS meldet ~0, widerspricht im Vorzeichen oder liegt so weit daneben,
            # dass eine Skalierung absurde Werte ergäbe → direkt aus Shunt ableiten
            if shunt > 0:
                p['current_charge'],    p['current_discharge'] = round(shunt, 2), 0.0
            else:
                p['current_charge'],    p['current_discharge'] = 0.0, round(abs(shunt), 2)
        p['current_total'] = round(shunt, 2)
        return p

    def _schedule_broadcast(self):
        """Sendet state-Update an alle WS-Clients (entprellt, siehe RUNDRUF_TAKT_S)."""
        if self._loop is None or self._on_change is None:
            return
        if not self._broadcast_pending:
            self._broadcast_pending = True
            try:
                asyncio.run_coroutine_threadsafe(self._delayed_broadcast(), self._loop)
            except Exception as e:
                # Schlägt schon das Einreihen fehl (Loop wird gerade beendet),
                # räumt _delayed_broadcast das Flag nie weg – ab da käme nie
                # wieder ein Broadcast zustande und die Anzeige stünde still.
                self._broadcast_pending = False
                log.warning("Broadcast konnte nicht eingereiht werden: %s", e)

    async def _delayed_broadcast(self):
        """Wartet das Entprellfenster ab und sendet dann EINEN Zustand.

        Dauert ein Sendevorgang länger als die 50 ms (viele oder langsame
        WS-Clients), war vorher ein zweiter Broadcast bereits unterwegs und
        beide liefen gleichzeitig — mit der Gefahr, dass der ältere Zustand
        zuletzt beim Client ankommt. Der Lock serialisiert die Sendevorgänge;
        das Pending-Flag wird erst INNERHALB des Locks gelöscht, damit während
        eines laufenden Broadcasts höchstens ein weiterer wartet.
        """
        if self._broadcast_lock is None:
            # Läuft im Event-Loop, also ohne Nebenläufigkeit bis zum ersten await.
            self._broadcast_lock = asyncio.Lock()
        try:
            await asyncio.sleep(EILFENSTER_TAKT_S if time.monotonic() < self._eilig_bis
                                else RUNDRUF_TAKT_S)
            async with self._broadcast_lock:
                self._broadcast_pending = False
                await self._on_change(self.state.to_dict())
        except asyncio.CancelledError:
            self._broadcast_pending = False
            raise
        except Exception as e:
            # Flag zurücksetzen, sonst käme nie wieder ein Broadcast zustande.
            self._broadcast_pending = False
            log.warning("Broadcast fehlgeschlagen: %s", e)

    # ── Thread-Hauptschleife ─────────────────────────────────────────────────

    def run(self):
        """Blockierende Schleife — läuft in einem Hintergrund-Thread."""
        self._running = True
        while self._running:
            try:
                with can.Bus(channel=self.channel, bustype='socketcan') as bus:
                    self._bus = bus
                    log.info("CAN-Bus %s verbunden", self.channel)
                    time.sleep(1)  # kurz warten bis Bus stabil
                    self.request_product_info()
                    for msg in bus:
                        if not self._running:
                            break
                        try:
                            self._handle(msg)
                        except Exception as e:
                            # Ein einzelner fehlerhafter Frame darf NICHT die
                            # ganze CAN-Verbindung killen + Reconnect auslösen.
                            log.warning("Frame-Fehler (PGN-Verarbeitung): %s", e)
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
