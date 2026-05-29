# NMEA 2000 Protokoll – Mave Boat Monitor

Dieses Dokument beschreibt alle CAN-Bus / NMEA 2000 PGNs, die im Bordnetz
verwendet werden – empfangen oder gesendet vom Raspberry Pi (mave-boatui),
sowie die PGNs des VE.Direct-NMEA2K-Gateways (Teensy 4.1).

---

## Geräte im Netzwerk

| Gerät                   | Hardware      | Quelladresse |
|-------------------------|---------------|--------------|
| mave-boatui (Pi)        | Raspberry Pi  | 100          |
| Battery Board           | Teensy 3.1    | –            |
| VE.Direct Gateway       | Teensy 4.1    | 22           |

---

## Empfangene PGNs (Pi / mave-boatui)

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

### PGN 127507 – Charger Status *(VE.Direct Gateway)*
**Format:** Single Frame · **Quelle:** VE.Direct-Gateway (Quelladresse 22)

Meldet den Ladezustand der Lader und DC-DC-Lader.

| Feld         | Inhalt                           |
|--------------|----------------------------------|
| deviceInstance | 0 = Orion-XS, 1 = Smart IP43, 2 = Ladegerät P5 |
| batteryInstance | 0 = Hausbatterie               |
| Charge State | siehe CS-Mapping-Tabelle unten   |
| Charger Mode | `Standalone`                     |

**VE.Direct CS → NMEA-2000-Ladezustand:**

| CS  | VE.Direct        | NMEA 2000            |
|-----|------------------|----------------------|
| 0   | Off              | Not Charging         |
| 2   | Fault            | Fault                |
| 3   | Bulk             | Bulk                 |
| 4   | Absorption       | Absorption           |
| 5   | Float            | Float                |
| 6   | Storage          | Float                |
| 7   | Equalize         | Equalise             |
| 245 | Starting-up      | Not Charging         |
| 247 | Auto equalize    | Equalise             |
| 252 | External control | Constant VI          |

---

### PGN 127508 – Battery Status
**Format:** Single Frame (8 Byte) · **Quelle:** Battery Board

| Byte | Inhalt |
|------|--------|
| 0    | Instanz |
| 1–2  | Spannung (uint16 LE) × 0.01 V (0xFFFF = N/A) |
| 3–4  | Strom (int16 LE) × 0.1 A (−32768 = N/A) |

Instanzen für Service- und Starterbatterie werden in
`presets.json → batteries` konfiguriert:

| Instanz | Bedeutung      |
|---------|----------------|
| 0       | Hausbatterie   |
| 1       | Starterbatterie |

---

### PGN 127750 – Converter (Inverter/Charger) Status *(VE.Direct Gateway)*
**Format:** Single Frame · **Quelle:** VE.Direct-Gateway (Quelladresse 22)

Meldet den Betriebszustand des Wechselrichters (Phoenix Inverter Smart 2000 VA).
`deviceInstance = 0`.

**VE.Direct CS → NMEA-2000-Converter-Mode:**

| CS | VE.Direct  | NMEA 2000            |
|----|------------|----------------------|
| 0  | Off        | `N2kCICS_Off`        |
| 1  | Low Power  | `N2kCICS_LP_Mode`    |
| 2  | Fault      | `N2kCICS_Fault`      |
| 9  | Inverting  | `N2kCICS_Inverting`  |

Der Zustand ändert sich nach einer Steuerung via PGN 130911:

| Steuer-Modus | VE.Direct CS | PGN 127750 Operating State |
|--------------|--------------|---------------------------|
| On  (2)      | 9            | `N2kCICS_Inverting`       |
| Eco (5)      | 1            | `N2kCICS_LP_Mode`         |
| Off (4)      | 0            | `N2kCICS_Off`             |

---

### PGN 130312 – Temperature
**Format:** Single Frame (8 Byte) · **Quelle:** Battery Board

| Byte | Inhalt |
|------|--------|
| 0    | Instanz |
| 1    | Temperaturquelle |
| 2–3  | Temperatur (uint16 LE) × 0.01 K − 273.15 → °C (0xFFFF = N/A) |

---

### PGN 130900 – Battery Stats *(Custom, Battery Board)*
**Format:** Fast Packet, **26 Byte Payload** · **Quelle:** Battery Board

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

### PGN 130901 – BMS Pack Data *(Custom, Battery Board)*
**Format:** Fast Packet, **45 Byte Payload** · **Quelle:** Battery Board

| Offset | Typ     | Inhalt |
|--------|---------|--------|
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

### PGN 130902 – BMS Cell Data *(Custom, Battery Board)*
**Format:** Fast Packet, variabler Payload · **Quelle:** Battery Board

| Offset        | Typ    | Inhalt |
|---------------|--------|--------|
| 0             | uint8  | Anzahl Zellen |
| 1 + i×4       | uint16 | Zellspannung (mV); 0xFFFF = N/A |
| 3 + i×4       | int16  | Temperatur × 0.1 °C; 0x7FFF = N/A |

---

### PGN 130910 – VE.Direct Extended *(Custom, VE.Direct Gateway)*
**Format:** Fast Packet, **28 Byte Payload** · **Quelle:** VE.Direct-Gateway (Quelladresse 22)

Enthält alle VE.Direct-Felder ohne Standard-PGN-Äquivalent. Byte 0 (Instanz)
und Byte 1 (Typ) identifizieren das Gerät; NaN-Felder sind für den jeweiligen
Gerätetyp nicht verfügbar.

| Offset | Typ     | Inhalt                                       | Skalierung       |
|--------|---------|----------------------------------------------|------------------|
| 0      | uint8   | Geräte-Instanz                               | –                |
| 1      | uint8   | Gerätetyp (0=Lader, 1=DC-DC, 2=Inverter)     | –                |
| 2      | uint8   | `CS` roh (Zustand; 0xFF = N/A)               | –                |
| 3      | uint8   | `MODE` roh (Gerätebetriebsmodus; 0xFF = N/A) | –                |
| 4      | float32 | DC-Spannung V                                | `V` ÷ 1000       |
| 8      | float32 | DC-Strom A                                   | `I` ÷ 1000       |
| 12     | float32 | AC-Ausgangsspannung V (NaN bei Ladern)       | `AC_OUT_V` ÷ 100 |
| 16     | float32 | AC-Ausgangsstrom A (NaN bei Ladern)          | `AC_OUT_I` ÷ 10  |
| 20     | float32 | AC-Scheinleistung VA (NaN bei Ladern)        | `AC_OUT_S`       |
| 24     | int16   | ERR (Lader/DC-DC) oder AR (Inverter); −1=N/A | –                |
| 26     | int16   | WARN Warning Reason; −1 = N/A bei Ladern     | –                |

**Feldverfügbarkeit je Gerätetyp:**

| Feld            | Lader | DC-DC | Inverter |
|-----------------|-------|-------|----------|
| CS / MODE / V / I | ✓   | ✓     | ✓        |
| AC_OUT_V/I/S    | –     | –     | ✓        |
| ERR (→ Offset 24) | ✓  | ✓     | –        |
| AR  (→ Offset 24) | –  | –     | ✓        |
| WARN            | –     | –     | ✓        |

**Geräte-Instanzen:**

| Instanz | Gerät                     | Typ    |
|---------|---------------------------|--------|
| 0 (Typ 1) | Orion-XS DC-DC           | DC-DC  |
| 1 (Typ 0) | Phoenix Smart IP43       | Lader  |
| 0 (Typ 2) | Phoenix Inverter 2000 VA | Inverter |
| 2 (Typ 0) | Ladegerät P5 (0xA340)    | Lader  |

---

## Gesendete PGNs (Pi / mave-boatui)

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

### PGN 130911 – VE.Direct Control *(Custom, Inverter-Steuerung)*
**Format:** Single Frame, **3 Byte Payload**
**Richtung:** mave-boatui → VE.Direct-Gateway

Steuert den Inverter-Modus über den Bus. Das Gateway empfängt diesen PGN und
übersetzt ihn in einen VE.Direct-HEX-Befehl an den Wechselrichter.

| Offset | Typ   | Inhalt                            |
|--------|-------|-----------------------------------|
| 0      | uint8 | deviceInstance (Zielgerät)        |
| 1      | uint8 | commandType (0 = Modus setzen)    |
| 2      | uint8 | value (Modus-Wert; s. u.)         |

**Inverter-Modus-Werte (commandType = 0, deviceInstance = 0):**

| Wert | Modus              |
|------|--------------------|
| 2    | **An** (Inverting) |
| 4    | **Aus**            |
| 5    | **Eco** (Low Power Standby) |

Unbekannte Werte werden vom Gateway ignoriert. Der neue Zustand ist
anschließend in PGN 127750 und 130910 sichtbar.

---

## VE.Direct-HEX-Steuerung (Gateway-intern)

Wenn das Gateway PGN 130911 empfängt, sendet es auf dem UART des Zielgeräts
einen VE.Direct-HEX-SET-Befehl (Register 0x0200 = Device Mode).

**Befehlsformat:** `:8LLHH00VVCC\n`  
**Prüfsumme:** `(0x55 − (cmd + reg_lo + reg_hi + flags + value)) & 0xFF`

| Modus | Befehlsstring      | Prüfsumme                            |
|-------|--------------------|--------------------------------------|
| On  (0x02) | `:80002000249\n` | 0x55 − (8+0+2+0+2) = 0x49 |
| Off (0x04) | `:80002000447\n` | 0x55 − (8+0+2+0+4) = 0x47 |
| Eco (0x05) | `:80002000546\n` | 0x55 − (8+0+2+0+5) = 0x46 |

---

## CAN-Konfiguration

| Parameter              | Wert                       |
|------------------------|----------------------------|
| Interface (Pi)         | `can0`                     |
| Interface (Gateway)    | FlexCAN (Teensy 4.1)       |
| Baudrate               | 250 kbit/s (NMEA 2000)     |
| Quelladresse Pi        | 100 (`RPI_SOURCE_ADDRESS`) |
| Quelladresse Gateway   | 22                         |
| Light Bank Instanz     | 1                          |
| VE.Direct-Baudrate     | 19200 Baud                 |

**Fast-Packet-PGNs:** `126720, 130900, 130901, 130902, 130910`  
**Single-Frame-PGNs:** `127505, 127506, 127507, 127508, 127750, 130311, 130912`

---

## PGN-Übersicht (Gesamtnetz)

| PGN    | Quelle           | Empfänger  | Inhalt                            |
|--------|------------------|------------|-----------------------------------|
| 126720 | Pi (100)         | alle       | Licht-Helligkeit setzen/empfangen |
| 127505 | –                | Pi         | Füllstand Tanks                   |
| 127506 | Battery Board    | Pi         | DC-Typ (Lichtmaschine/Solar)      |
| 127507 | Gateway (22)     | Pi         | Ladestatus Lader/DC-DC            |
| 127508 | Battery Board    | Pi         | Batteriespannung/-strom           |
| 127750 | Gateway (22)     | Pi         | Inverter-Betriebszustand          |
| 130312 | Battery Board    | Pi         | Temperatur                        |
| 130900 | Battery Board    | Pi         | Battery Stats (Custom)            |
| 130901 | Battery Board    | Pi         | BMS Pack Data (Custom)            |
| 130902 | Battery Board    | Pi         | BMS Cell Data (Custom)            |
| 130910 | Gateway (22)     | Pi         | VE.Direct Extended (Custom)       |
| 130911 | Pi (100)         | Gateway    | Inverter-Steuerung (Custom)       |
