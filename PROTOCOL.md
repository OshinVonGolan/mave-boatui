# NMEA 2000 Protokoll – Mave Boat Monitor

Dieses Dokument beschreibt alle CAN-Bus / NMEA 2000 PGNs, die der Boat Monitor
empfängt oder sendet.

---

## Empfangene PGNs

### PGN 127505 – Fluid Level
**Format:** Single Frame (7 Byte)

| Byte | Inhalt |
|------|--------|
| 0    | Instanz (Nibble 0–3) |
| 1–2  | Füllstand (uint16, LE) × 0.004 → % |

Instanzen werden in `presets.json → tanks` konfiguriert und auf Tank 1 / Tank 2
gemappt.

---

### PGN 127506 – DC Detailed Status
**Format:** Single Frame

| Byte | Inhalt |
|------|--------|
| 1    | Instanz |
| 2    | DC-Typ (Nibble): 1 = Lichtmaschine, 4 = Solar |

Wird nur ausgewertet, um Lichtmaschine (Typ 1) und Solar (Typ 4) von
Batteriebänken zu unterscheiden. Spannungs-/Stromwerte kommen über PGN 127508.

---

### PGN 127508 – Battery Status
**Format:** Single Frame (8 Byte)

| Byte | Inhalt |
|------|--------|
| 0    | Instanz |
| 1–2  | Spannung (uint16 LE) × 0.01 V (0xFFFF = N/A) |
| 3–4  | Strom (int16 LE) × 0.1 A (−32768 = N/A) |

Instanzen für Service- und Starterbatterie werden in
`presets.json → batteries` konfiguriert.

---

### PGN 126720 – Proprietary Fast-Packet (Helligkeit empfangen)
**Format:** Fast Packet, 11 Byte Payload

Wird empfangen, wenn ein anderes Gerät am Bus den Helligkeits-Status meldet.

| Byte | Inhalt |
|------|--------|
| 0    | Typ-Byte: 0xA1 |
| 1    | Bank-Instanz (`LIGHT_BANK_INSTANCE = 1`) |
| 2–10 | 9 × PWM-Wert (0–255) für Kanäle 1–9 |

Kanäle 1–4 sind dimmbare Leuchtkanäle, Kanal 9 ist das Relais (0/255).

---

### PGN 130312 – Temperature
**Format:** Single Frame (8 Byte)

| Byte | Inhalt |
|------|--------|
| 0    | Instanz |
| 1    | Temperaturquelle |
| 2–3  | Temperatur (uint16 LE) × 0.01 K − 273.15 → °C (0xFFFF = N/A) |

---

### PGN 130900 – Battery Stats *(Custom)*
**Format:** Fast Packet, **26 Byte Payload**

Gesendet vom Teensy Battery Board als proprietäre Erweiterung.

| Offset | Typ     | Inhalt |
|--------|---------|--------|
| 0      | float32 | Leistung (W) |
| 4      | float32 | Verbrauchte Kapazität (Ah) |
| 8      | uint16  | Ladezyklen |
| 10     | float32 | Minimale Batteriespannung (V) |
| 14     | float32 | Maximale Batteriespannung (V) |
| 18     | uint32  | Zeit seit letzter Vollladung (s); 0xFFFFFFFF = N/A |
| 22     | float32 | Ladezustand SOC (%) |

---

### PGN 130901 – BMS Pack Data *(Custom)*
**Format:** Fast Packet, **45 Byte Payload**

Gesendet vom Teensy Battery Board mit aggregierten 123SmartBMS-Daten.

| Offset | Typ    | Inhalt |
|--------|--------|--------|
| 0      | float32 | Gesamtspannung des Packs (V) |
| 4      | float32 | Gesamtstrom (A, positiv = Entladen) |
| 8      | float32 | Ladestrom (A) |
| 12     | float32 | Entladestrom (A) |
| 16     | uint8   | SOC (%) |
| 17     | float32 | Kapazität (Ah) |
| 21     | float32 | Verbleibende Energie (kWh) |
| 25     | float32 | Niedrigste Zellspannung (V) |
| 29     | uint8   | Zellnummer der niedrigsten Spannung |
| 30     | float32 | Höchste Zellspannung (V) |
| 34     | uint8   | Zellnummer der höchsten Spannung |
| 35     | float32 | Niedrigste Zelltemperatur (°C) |
| 39     | float32 | Höchste Zelltemperatur (°C) |
| 43     | uint8   | Anzahl Zellen |
| 44     | uint8   | Status-Flags (Bitmask, s. u.) |

**Status-Flags (Byte 44):**

| Bit | Maske | Bedeutung |
|-----|-------|-----------|
| 0   | 0x01  | Laden erlaubt |
| 1   | 0x02  | Entladen erlaubt |
| 2   | 0x04  | Kommunikationsfehler zum BMS |
| 3   | 0x08  | Alarm: Zellspannung zu niedrig |
| 4   | 0x10  | Alarm: Zellspannung zu hoch |
| 5   | 0x20  | Alarm: Temperatur zu niedrig |
| 6   | 0x40  | Alarm: Temperatur zu hoch |

---

### PGN 130902 – BMS Cell Data *(Custom)*
**Format:** Fast Packet, variabler Payload

Gesendet vom Teensy Battery Board mit Einzelzellenwerten.

| Offset        | Typ    | Inhalt |
|---------------|--------|--------|
| 0             | uint8  | Anzahl Zellen |
| 1 + i×4       | uint16 | Zellspannung (mV); 0xFFFF = N/A |
| 3 + i×4       | int16  | Temperatur × 0.1 °C; 0x7FFF = N/A |

---

## Gesendete PGNs

### PGN 126720 – Proprietary Fast-Packet (Helligkeit setzen)
**Format:** Fast Packet, 11 Byte Payload, 2 CAN-Frames

| Byte | Inhalt |
|------|--------|
| 0    | Typ-Byte: 0xA1 |
| 1    | Bank-Instanz (`LIGHT_BANK_INSTANCE = 1`) |
| 2–10 | 9 × PWM-Wert (0–255) für Kanäle 1–9 |

Gesendet von Quelladresse **100** (`RPI_SOURCE_ADDRESS`).
Ausgelöst durch `/api/lights/preset/{id}` oder `/api/lights/channels`.

---

## CAN-Konfiguration

| Parameter | Wert |
|-----------|------|
| Interface | `can0` |
| Baudrate | 250 kbit/s (NMEA 2000 Standard) |
| Quelladresse Pi | 100 |
| Light Bank Instanz | 1 |

Fast-Packet PGNs: `{126720, 130900, 130901, 130902}`
