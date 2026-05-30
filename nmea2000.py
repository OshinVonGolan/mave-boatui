"""NMEA 2000 PGN-Parser und Fast-Packet Reassembler für den Boat-Monitor."""
import math
import struct

LIGHT_BANK_INSTANCE = 1
RPI_SOURCE_ADDRESS  = 100   # CAN-Quelladresse des Raspberry Pi


# ── CAN-ID Hilfsfunktionen ───────────────────────────────────────────────────

def parse_can_id(arb_id: int) -> tuple[int, int]:
    """Extrahiert (PGN, Quelladresse) aus einem 29-Bit NMEA2000 CAN-ID."""
    src = arb_id & 0xFF
    pf  = (arb_id >> 16) & 0xFF
    ps  = (arb_id >> 8)  & 0xFF
    dp  = (arb_id >> 24) & 0x01
    pgn = (dp << 16) | (pf << 8) | (ps if pf >= 240 else 0)
    return pgn, src


def make_can_id(pgn: int, src: int, dst: int = 0xFF, priority: int = 6) -> int:
    """Erstellt einen 29-Bit NMEA2000 CAN-ID."""
    pf = (pgn >> 8) & 0xFF
    dp = (pgn >> 16) & 0x01
    ps = (pgn & 0xFF) if pf >= 240 else dst
    return (priority << 26) | (dp << 24) | (pf << 16) | (ps << 8) | src


# ── Fast-Packet Reassembler ──────────────────────────────────────────────────

FAST_PACKET_PGNS = {126720, 126996, 130900, 130901, 130902, 130910}


class FastPacketReassembler:
    """Reassembliert mehrteilige NMEA2000 Fast-Packet-Nachrichten."""

    def __init__(self):
        self._buf: dict = {}

    def process(self, pgn: int, src: int, raw: bytes):
        """Gibt den vollständigen Payload zurück, sobald alle Frames empfangen wurden."""
        if not raw:
            return None

        frame_num = raw[0] & 0x1F
        seq_id    = (raw[0] >> 5) & 0x07
        key       = (pgn, src, seq_id)

        if frame_num == 0:
            if len(raw) < 2:
                return None
            self._buf[key] = {'total': raw[1], 'data': bytearray(raw[2:])}
        else:
            if key not in self._buf:
                return None
            self._buf[key]['data'].extend(raw[1:])

        entry = self._buf.get(key)
        if entry and len(entry['data']) >= entry['total']:
            result = bytes(entry['data'][:entry['total']])
            del self._buf[key]
            return result

        return None


# ── PGN-Parser ───────────────────────────────────────────────────────────────

def parse_fluid_level(data: bytes):
    """PGN 127505 – Fluid Level (Single Frame, 7 Byte)."""
    if len(data) < 7:
        return None
    instance  = data[0] & 0x0F
    level_raw = struct.unpack_from('<H', data, 1)[0]
    if level_raw == 0xFFFF:
        return None
    return {'instance': instance, 'level': round(level_raw * 0.004, 1)}


def parse_dc_status(data: bytes):
    """PGN 127508 – Battery Status (Single Frame, 8 Byte)."""
    if len(data) < 5:
        return None
    instance = data[0]
    v_raw    = struct.unpack_from('<H', data, 1)[0]
    i_raw    = struct.unpack_from('<h', data, 3)[0]
    return {
        'instance': instance,
        'voltage':  round(v_raw * 0.01, 2) if v_raw != 0xFFFF  else None,
        'current':  round(i_raw * 0.1,  1) if i_raw != -32768  else None,
    }


def parse_battery_stats(data: bytes):
    """PGN 130900 – Custom Battery Stats (Fast Packet, 26 Byte)."""
    if len(data) < 26:
        return None

    def _f(offset):
        v = struct.unpack_from('<f', data, offset)[0]
        return None if (math.isnan(v) or math.isinf(v)) else v

    power       = _f(0)
    consumed_ah = _f(4)
    cycles      = struct.unpack_from('<H', data,  8)[0]
    min_v       = _f(10)
    max_v       = _f(14)
    time_since  = struct.unpack_from('<I', data, 18)[0]
    soc         = _f(22)

    return {
        'power':           round(power,       1) if power       is not None else None,
        'consumed_ah':     round(consumed_ah, 1) if consumed_ah is not None else None,
        'cycles':          cycles      if cycles     != 0xFFFF        else None,
        'min_voltage':     round(min_v, 3)        if min_v      is not None else None,
        'max_voltage':     round(max_v, 3)        if max_v      is not None else None,
        'time_since_full': time_since  if time_since != 0xFFFF_FFFF   else None,
        'soc':             round(soc,  1)         if soc        is not None else None,
    }


def parse_bms_pack(data: bytes):
    """PGN 130901 – 123SmartBMS Pack-Daten (Fast Packet, 45 Byte).

    Layout (Teensy Battery Bord):
      0  float packVoltage
      4  float currentTotal
      8  float currentCharge
     12  float currentDischarge
     16  uint8 SOC %
     17  float capacity_Ah
     21  float remainingEnergy_kWh
     25  float lowestCellVoltage
     29  uint8 lowestCellNumber
     30  float highestCellVoltage
     34  uint8 highestCellNumber
     35  float lowestCellTemp
     39  float highestCellTemp
     43  uint8 cellCount
     44  uint8 statusFlags
    """
    if len(data) < 45:
        return None

    def _f(off):
        v = struct.unpack_from('<f', data, off)[0]
        return None if (math.isnan(v) or math.isinf(v)) else v

    pack_v = _f(0);  curr_t = _f(4);  curr_c = _f(8);  curr_d = _f(12)
    cap    = _f(17); rem    = _f(21); lo_v   = _f(25); hi_v   = _f(30)
    lo_t   = _f(35); hi_t   = _f(39)
    flags  = data[44]

    return {
        'voltage':           round(pack_v, 2) if pack_v is not None else None,
        'current_total':     round(curr_t, 2) if curr_t is not None else None,
        'current_charge':    round(curr_c, 2) if curr_c is not None else None,
        'current_discharge': round(curr_d, 2) if curr_d is not None else None,
        'soc':               int(data[16]),
        'capacity_ah':       round(cap,  1)   if cap    is not None else None,
        'remaining_kwh':     round(rem,  3)   if rem    is not None else None,
        'lowest_cell_v':     round(lo_v, 3)   if lo_v   is not None else None,
        'lowest_cell_nr':    int(data[29]),
        'highest_cell_v':    round(hi_v, 3)   if hi_v   is not None else None,
        'highest_cell_nr':   int(data[34]),
        'lowest_temp':       round(lo_t, 1)   if lo_t   is not None else None,
        'highest_temp':      round(hi_t, 1)   if hi_t   is not None else None,
        'cell_count':        int(data[43]),
        'allow_charge':      bool(flags & 0x01),
        'allow_discharge':   bool(flags & 0x02),
        'comm_error':        bool(flags & 0x04),
        'alarm_min_volt':    bool(flags & 0x08),
        'alarm_max_volt':    bool(flags & 0x10),
        'alarm_min_temp':    bool(flags & 0x20),
        'alarm_max_temp':    bool(flags & 0x40),
    }


def parse_bms_cells(data: bytes):
    """PGN 130902 – 123SmartBMS Einzelzellen (Fast Packet).

    Layout:
      0         uint8  cellCount
      1 + i*4   uint16 voltage_mV  (0xFFFF = N/A)
      3 + i*4   int16  temp × 0.1°C (0x7FFF = N/A)
    """
    if len(data) < 1:
        return None
    cell_count = data[0]
    if cell_count == 0 or len(data) < 1 + cell_count * 4:
        return None
    cells = []
    for i in range(cell_count):
        off   = 1 + i * 4
        v_raw = struct.unpack_from('<H', data, off)[0]
        t_raw = struct.unpack_from('<h', data, off + 2)[0]
        cells.append({
            'voltage': round(v_raw * 0.001, 3) if v_raw != 0xFFFF else None,
            'temp':    round(t_raw * 0.1,   1) if t_raw != 0x7FFF else None,
        })
    return {'cell_count': cell_count, 'cells': cells}


DC_TYPE_ALTERNATOR = 1
DC_TYPE_SOLAR      = 4

def parse_dc_detailed(data: bytes):
    """PGN 127506 – DC Detailed Status: liefert Instanz + DC-Typ."""
    if len(data) < 3:
        return None
    return {'instance': data[1], 'dc_type': data[2] & 0x0F}


def parse_temperature(data: bytes):
    """PGN 130312 – Temperature (Single Frame, 8 Byte). Gibt Temperatur in °C zurück."""
    if len(data) < 5:
        return None
    source  = data[1]
    t_raw   = struct.unpack_from('<H', data, 2)[0]
    if t_raw == 0xFFFF:
        return None
    temp_c = round(t_raw * 0.01 - 273.15, 1)
    return {'instance': data[0], 'source': source, 'temperature_c': temp_c}


def parse_brightness(data: bytes):
    """PGN 126720 Typ 0xA1 – PWM-Helligkeit (Fast Packet, 11 Byte)."""
    if len(data) < 11 or data[0] != 0xA1 or data[1] != LIGHT_BANK_INSTANCE:
        return None
    return {'channels': list(data[2:11])}


# ── CAN-Frame-Sender ─────────────────────────────────────────────────────────

PGN_NAMES: dict[int, str] = {
    59392:  'ISO Acknowledge',
    59904:  'ISO Request',
    60928:  'ISO Address Claim',
    65240:  'ISO Commanded Address',
    126208: 'NMEA Request/Command',
    126720: 'Proprietary Fast-Packet',
    126992: 'System Time',
    126996: 'Product Information',
    127245: 'Rudder',
    127250: 'Vessel Heading',
    127251: 'Rate of Turn',
    127257: 'Attitude',
    127488: 'Engine Parameters Rapid',
    127489: 'Engine Parameters Dynamic',
    127501: 'Binary Switch Bank Status',
    127502: 'Switch Bank Control',
    127505: 'Fluid Level',
    127506: 'DC Detailed Status (Solar/Alternator)',
    127508: 'Battery Status',
    128259: 'Speed',
    128267: 'Water Depth',
    129025: 'Position Rapid',
    129026: 'COG & SOG Rapid',
    129029: 'GNSS Position Data',
    129283: 'Cross Track Error',
    129284: 'Navigation Data',
    129291: 'Set & Drift',
    130306: 'Wind Data',
    130310: 'Environmental Parameters',
    130311: 'Environmental Parameters 2',
    130312: 'Temperature',
    130900: 'Battery Stats (Custom)',
    130901: 'BMS Pack Data (Custom)',
    130902: 'BMS Cell Data (Custom)',
    130910: 'VE.Direct Extended (Custom)',
    130911: 'VE.Direct Control (Custom)',
    127507: 'Charger Status',
    127750: 'Converter / Inverter Status',
}

# CS → Ladebezeichnung (VE.Direct / NMEA2K)
_CHARGER_CS = {
    0: 'Aus', 2: 'Fehler', 3: 'Bulk', 4: 'Absorption', 5: 'Float',
    6: 'Storage', 7: 'Equalise', 245: 'Starting', 247: 'Auto-Equalise',
    252: 'Ext. Control',
}
_INVERTER_CS = {0: 'Aus', 1: 'Eco', 2: 'Fehler', 9: 'Aktiv'}


def parse_ve_direct_ext(data: bytes):
    """PGN 130910 – VE.Direct Extended (Fast Packet, 28 Byte Payload).
    Liefert Daten zu Ladern (Typ 0), DC-DC-Wandlern (Typ 1) und Invertern (Typ 2).
    """
    if len(data) < 28:
        return None
    inst  = data[0]
    dtype = data[1]
    cs    = data[2] if data[2] != 0xFF else None
    mode  = data[3] if data[3] != 0xFF else None

    def _f(off):
        v = struct.unpack_from('<f', data, off)[0]
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 3)

    dc_v = _f(4);  dc_i = _f(8)
    ac_v = _f(12); ac_i = _f(16); ac_s = _f(20)
    err  = struct.unpack_from('<h', data, 24)[0]
    warn = struct.unpack_from('<h', data, 26)[0]

    cs_label  = _CHARGER_CS.get(cs) if cs is not None else None
    if dtype == 2:  # Inverter
        cs_label = _INVERTER_CS.get(cs) if cs is not None else None
        power = round(ac_v * ac_i, 1) if ac_v and ac_i else None
    else:
        power = round(dc_v * dc_i, 1) if dc_v and dc_i else None

    return {
        'instance':   inst,
        'type':       dtype,       # 0=Lader, 1=DC-DC, 2=Inverter
        'cs':         cs,
        'cs_label':   cs_label,
        'mode':       mode,
        'dc_voltage': dc_v,
        'dc_current': dc_i,
        'ac_voltage': ac_v if dtype == 2 else None,
        'ac_current': ac_i if dtype == 2 else None,
        'ac_power':   ac_s if dtype == 2 else None,
        'power':      power,
        'err':        err  if err  != -1 else None,
        'warn':       warn if warn != -1 else None,
    }


def parse_inverter_status(data: bytes):
    """PGN 127750 – Converter/Inverter Status (Single Frame)."""
    if len(data) < 4:
        return None
    # Byte 3 bits 0-3: operating state per NMEA2K N2kCI_OperatingState
    state_nibble = data[3] & 0x0F
    STATES = {0: 'Aus', 1: 'Eco', 2: 'Fehler', 3: 'Aktiv', 9: 'Aktiv'}
    return {'state': STATES.get(state_nibble, f'State {state_nibble}')}


def parse_charger_status_pgn(data: bytes):
    """PGN 127507 – Charger Status (Single Frame)."""
    if len(data) < 4:
        return None
    inst = data[0]
    batt = data[1]
    mode = data[2] & 0x0F   # charging mode / state
    MODES = {
        0: 'Unbekannt', 1: 'Aus', 2: 'Bulk', 3: 'Absorption',
        4: 'Überladen', 5: 'Equalise', 6: 'Float', 7: 'Kein Float',
        8: 'Const VI', 9: 'Deaktiviert', 0xF: 'Fehler',
    }
    return {'instance': inst, 'battery': batt, 'state': MODES.get(mode, f'Mode {mode}')}


def parse_product_info(data: bytes) -> dict | None:
    """PGN 126996 – Product Information (Fast Packet, ~134 Byte Payload).
    Gibt model_id (Gerätename) und sw_version zurück.
    """
    if len(data) < 36:
        return None
    def _str(raw: bytes) -> str:
        return raw.rstrip(b'\x00\xff ').decode('ascii', errors='replace').strip()
    model_id   = _str(data[4:36])
    sw_version = _str(data[36:68]) if len(data) >= 68 else ''
    if not model_id:
        return None
    return {'model_id': model_id, 'sw_version': sw_version}


def parse_iso_address_claim(data: bytes) -> dict | None:
    """PGN 60928 – ISO Address Claim (8 Byte NAME-Feld).
    Gibt Hersteller-Code und Geräte-Funktion zurück.
    """
    if len(data) < 8:
        return None
    name = int.from_bytes(data[:8], 'little')
    mfr_code    = (name >> 21) & 0x7FF   # Bits 21-30
    device_fn   = (name >> 40) & 0xFF    # Bits 40-47
    device_cls  = (name >> 49) & 0x7F    # Bits 49-55
    return {'mfr_code': mfr_code, 'device_fn': device_fn, 'device_cls': device_cls,
            'name_bytes': data[:8].hex()}


def build_inverter_mode_frame(mode: int) -> tuple[int, bytes]:
    """Erstellt PGN 130911 – Inverter Mode Control (Single Frame, 3 Byte).
    mode: 2=An, 4=Aus, 5=Eco
    """
    payload = bytes([0, 0, mode])   # deviceInstance=0, commandType=0, value
    can_id  = make_can_id(130911, RPI_SOURCE_ADDRESS, priority=3)
    return can_id, payload


def build_time_frame(ts: float) -> tuple[int, bytes]:
    """Erstellt NMEA2000 PGN 126992 (System Time) Frame.

    ts: Unix-Timestamp (Sekunden seit 1970-01-01 UTC)
    """
    days     = int(ts // 86400)
    secs     = ts - days * 86400
    time_raw = round(secs * 10000)           # 0.0001s units
    payload  = struct.pack('<BBHI', 0xFF, 0x03, days, time_raw)
    can_id   = make_can_id(126992, RPI_SOURCE_ADDRESS, priority=3)
    return can_id, payload


def build_brightness_frames(values: list[int], seq_id: int = 0) -> list[tuple]:
    """Erstellt Fast-Packet CAN-Frames für PGN 126720 Typ 0xA1 (11 Byte Payload)."""
    payload = bytes([0xA1, LIGHT_BANK_INSTANCE] + [int(v) for v in values[:9]])
    can_id  = make_can_id(126720, RPI_SOURCE_ADDRESS)
    s       = (seq_id & 0x07) << 5

    pad = lambda b, n: b + b'\xff' * max(0, n - len(b))

    return [
        (can_id, bytes([s | 0, len(payload)]) + pad(payload[0:6], 6)),
        (can_id, bytes([s | 1])               + pad(payload[6:],  7)),
    ]
