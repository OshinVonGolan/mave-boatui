# NMEA 2000 Protokoll – Mave Boat Monitor

Dieses Dokument beschreibt alle CAN-Bus / NMEA 2000 PGNs, die im Bordnetz
verwendet werden – empfangen oder gesendet vom Raspberry Pi (mave-boatui),
sowie die PGNs des VE.Direct-NMEA2K-Gateways (Teensy 4.1).

---

## Geräte im Netzwerk

| Gerät                   | Hardware      | Quelladresse                          |
|-------------------------|---------------|---------------------------------------|
| mave-boatui (Pi)        | Raspberry Pi  | 100 (fest, `RPI_SOURCE_ADDRESS`)      |
| Battery Board           | Teensy 3.1    | auto (bevorzugt 1, NMEA2K Address Claim) |
| VE.Direct Gateway       | Teensy 4.1    | auto (bevorzugt 0, NMEA2K Address Claim) |

**Victron-Geräte am VE.Direct-Gateway (Teensy 4.1):**

| Serial-Port | Gerät                  | VEDeviceType | deviceInstance |
|-------------|------------------------|--------------|----------------|
| Serial2     | Orion-XS DC-DC #1      | `VE_DCDC`    | 0              |
| Serial3     | Orion-XS DC-DC #2      | `VE_DCDC`    | 2              |
| Serial4     | Phoenix Inverter 2000VA | `VE_INVERTER` | 0             |
| Serial5     | Phoenix Smart IP43     | `VE_CHARGER` | 1              |
| Serial6     | MPPT 75/15             | `VE_SOLAR`   | 3              |

Beide Teensy-Geräte verwenden `SetMode(N2km_NodeOnly, 0)` — sie starten mit
Preferred Address 0 und wählen automatisch eine freie Adresse per Address Claim
(PGN 60928). Der Pi trackt die aktuellen Adressen laufend über empfangene
PGN 130910-Frames (VE.Direct Gateway) und PGN 130901-Frames (Battery Board).

---

## Empfangene PGNs (Pi / mave-boatui)

### PGN 59904 – ISO Request *(gesendet vom Pi beim Start)*
**Format:** Single Frame, 3 Byte · **Richtung:** Pi → alle Geräte (broadcast)

Der Pi sendet PGN 59904 nach dem CAN-Bus-Connect (1 s Delay), um alle Geräte
zur Übertragung ihrer Produktinformation (PGN 126996) aufzufordern.

| Byte | Inhalt |
|------|--------|
| 0–2  | Angefordertes PGN (uint24 LE): 126996 |

---

### PGN 60928 – ISO Address Claim
**Format:** Single Frame, 8 Byte

Wird von jedem Gerät beim Bus-Beitritt gesendet. Der Pi empfängt diese Frames
und speichert das 8-Byte-NAME-Feld pro Quelladresse intern (`_device_addrclaim`).

| Bits    | Inhalt |
|---------|--------|
| 0–20    | Identity Number (21 Bit, herstellereindeutig) |
| 21–30   | Manufacturer Code (10 Bit) |
| 40–47   | Device Function |
| 49–55   | Device Class |
| 63      | Arbitrary Address Capable |

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

### PGN 126996 – Product Information
**Format:** Fast Packet, ~134 Byte Payload

Wird als Antwort auf einen PGN-59904-Request empfangen. Enthält den
einprogrammierten Produktnamen (Model ID), der im Netzwerk-View angezeigt wird.

| Offset | Typ    | Inhalt |
|--------|--------|--------|
| 0–1    | uint16 | NMEA 2000 Database Version |
| 2–3    | uint16 | Manufacturer Product Code |
| 4–35   | char32 | Model ID (null-padded ASCII, z.B. `"VE.Direct NMEA2K GW"`) |
| 36–67  | char32 | Software Version Code |
| 68–99  | char32 | Model Version |
| 100–131| char32 | Serial Code |
| 132    | uint8  | Certification Level |
| 133    | uint8  | Load Equivalency |

Der Pi speichert `model_id` pro Quelladresse in `_device_names[src]`.

---

### PGN 127505 – Fluid Level
**Format:** Single Frame (7 Byte)

| Byte | Inhalt |
|------|--------|
| 0    | Bits 3–0: Instanz (0 = Tank 1, 1 = Tank 2) · Bits 7–4: Fluid-Typ (0=Kraftstoff, 1=Frischwasser, 2=Grauwasser …) |
| 1–2  | Füllstand (uint16 LE) × 0.004 → % (0xFFFF = N/A) |
| 3–6  | Kapazität (uint32 LE) × 0.1 L (0xFFFFFFFF = N/A) |

Tank-Zuweisung im Pi: Instanz 0 → `tank1`, Instanz 1 → `tank2`.
Namen/Kapazitäten werden in `presets.json → tanks` konfiguriert.

---

### PGN 127506 – DC Detailed Status
**Format:** Single Frame

| Byte | Inhalt |
|------|--------|
| 1    | Instanz |
| 2    | DC-Typ (Nibble 0–3): 1 = Lichtmaschine, 4 = Solar |

Wird nur ausgewertet, um Lichtmaschine (Typ 1) und Solar (Typ 4) von
Batteriebänken zu unterscheiden. Spannungs-/Stromwerte kommen über PGN 127508.

---

### PGN 127507 – Charger Status *(VE.Direct Gateway)*
**Format:** Single Frame · **Quelle:** VE.Direct-Gateway (auto-address)

Meldet den Ladezustand der Lader und DC-DC-Lader.
Wird nur gesendet, wenn innerhalb der letzten **5 Sekunden** ein gültiger
VE.Direct-Frame empfangen wurde (Timeout-Schutz gegen veraltete Werte).

| Feld            | Inhalt |
|-----------------|--------|
| deviceInstance  | 0 = Orion-XS #1, 1 = Smart IP43, 2 = Orion-XS #2, 3 = MPPT 75/15 |
| batteryInstance | 0 = Hausbatterie |
| Charge State    | s. CS-Mapping unten |
| Charger Mode    | `Standalone` |

**VE.Direct CS → NMEA-2000-Ladezustand:**

| CS  | VE.Direct        | NMEA 2000        |
|-----|------------------|------------------|
| 0   | Off              | Not Charging     |
| 2   | Fault            | Fault            |
| 3   | Bulk             | Bulk             |
| 4   | Absorption       | Absorption       |
| 5   | Float            | Float            |
| 6   | Storage          | Float            |
| 7   | Equalize         | Equalise         |
| 245 | Starting-up      | Not Charging     |
| 247 | Auto equalize    | Equalise         |
| 252 | External control | Constant VI      |

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

| Instanz | Bedeutung       |
|---------|-----------------|
| 0       | Hausbatterie    |
| 1       | Starterbatterie |

---

### PGN 127750 – Converter (Inverter/Charger) Status *(VE.Direct Gateway)*
**Format:** Single Frame · **Quelle:** VE.Direct-Gateway (auto-address)

Meldet den Betriebszustand des Wechselrichters (Phoenix Inverter Smart 2000 VA).
`deviceInstance = 0`.
Wird nur gesendet, wenn VE.Direct-Daten frisch (< 5 s) sind.

**VE.Direct CS → NMEA-2000-Converter-Mode:**

| CS | VE.Direct  | NMEA 2000            |
|----|------------|----------------------|
| 0  | Off        | `N2kCICS_Off`        |
| 1  | Low Power  | `N2kCICS_LP_Mode`    |
| 2  | Fault      | `N2kCICS_Fault`      |
| 9  | Inverting  | `N2kCICS_Inverting`  |

Der Zustand ändert sich nach einer Steuerung via PGN 61184:

| Steuer-Modus | VE.Direct CS | PGN 127750 Operating State |
|--------------|--------------|----------------------------|
| On  (2)      | 9            | `N2kCICS_Inverting`        |
| Eco (5)      | 1            | `N2kCICS_LP_Mode`          |
| Off (4)      | 0            | `N2kCICS_Off`              |

---

### PGN 130312 – Temperature
**Format:** Single Frame (8 Byte) · **Quelle:** Battery Board

| Byte | Inhalt |
|------|--------|
| 0    | Instanz (Nibble 0–3) |
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
**Format:** Fast Packet, **28 Byte Payload** · **Quelle:** VE.Direct-Gateway (auto-address)

Enthält alle VE.Direct-Felder ohne Standard-PGN-Äquivalent. Byte 0 (Instanz)
und Byte 1 (Typ) identifizieren das Gerät; NaN-Felder sind für den jeweiligen
Gerätetyp nicht verfügbar.
Wird nur gesendet, wenn VE.Direct-Daten frisch (< 5 s) sind.

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

| Feld              | Lader | DC-DC | Inverter |
|-------------------|-------|-------|----------|
| CS / MODE / V / I | ✓     | ✓     | ✓        |
| AC_OUT_V/I/S      | –     | –     | ✓        |
| ERR (Offset 24)   | ✓     | ✓     | –        |
| AR  (Offset 24)   | –     | –     | ✓        |
| WARN              | –     | –     | ✓        |

Byte 3 enthält bei `VE_INVERTER` den rohen `MODE`-Wert, bei `VE_SOLAR` den MPPT-Tracker-Modus
(0 = Aus, 1 = Spannungs-/Strombegrenzt, 2 = MPPT aktiv). Bei anderen Typen den `MODE`-Wert oder
0xFF wenn nicht verfügbar.

**Geräte-Instanzen:**

| Instanz   | Gerät                      | Typ (Byte 1)          |
|-----------|----------------------------|-----------------------|
| 0 (Typ 1) | Orion-XS DC-DC #1          | DC-DC (`VE_DCDC`)     |
| 1 (Typ 0) | Phoenix Smart IP43         | Lader (`VE_CHARGER`)  |
| 0 (Typ 2) | Phoenix Inverter 2000 VA   | Inverter (`VE_INVERTER`) |
| 2 (Typ 1) | Orion-XS DC-DC #2          | DC-DC (`VE_DCDC`)     |
| 3 (Typ 3) | MPPT 75/15                 | Solar (`VE_SOLAR`)    |

---

### PGN 130912 – Solar Extended *(Custom, VE.Direct Gateway – MPPT 75/15)*
**Format:** Fast Packet, **14 Byte Payload** · **Quelle:** VE.Direct-Gateway (auto-address)

Enthält MPPT-Solar-Daten (Panelspannung, Panelleistung, Tagesertrag) ohne Standard-PGN-Äquivalent.
Wird nur gesendet wenn VE.Direct-Daten frisch (< 5 s) sind und das Gerät Typ `VE_SOLAR` hat.

| Offset | Typ     | Inhalt                                            | Skalierung        |
|--------|---------|---------------------------------------------------|-------------------|
| 0      | uint8   | Geräte-Instanz (3 = MPPT 75/15)                   | –                 |
| 1      | float32 | Panel-Spannung VPV (V); NaN = N/A                 | `VPV` ÷ 1000      |
| 5      | float32 | Panel-Leistung PPV (W); NaN = N/A                 | `PPV` direkt      |
| 9      | uint16  | Tagesertrag H20 (Wh); 0xFFFF = N/A               | `H20` × 10        |
| 11     | uint16  | Max. Leistung heute H21 (W); 0xFFFF = N/A         | `H21` direkt      |
| 13     | uint8   | MPPT-Tracker-Modus; 0xFF = N/A                    | –                 |

**H20-Skalierung:** VE.Direct-Feld `H20` ist in Einheiten von 0,01 kWh → × 10 ergibt Wh.

**MPPT-Tracker-Modus (Byte 13):**

| Wert | Bedeutung |
|------|-----------|
| 0    | Aus (Off season / Nacht) |
| 1    | Voltage or current limited |
| 2    | MPPT aktiv (Maximum Power Point Tracking) |
| 0xFF | N/A |

---

## Gesendete PGNs (Pi / mave-boatui)

### PGN 59904 – ISO Request *(Startup)*
**Format:** Single Frame, 3 Byte · **Richtung:** Pi → broadcast (0xFF)

Wird 1 Sekunde nach CAN-Bus-Connect gesendet. Fordert alle Geräte zur
Übertragung von PGN 126996 (Product Information) auf.

| Byte | Inhalt |
|------|--------|
| 0–2  | Angefordertes PGN (uint24 LE): 126996 = `0x14 0xF0 0x01` |

---

### PGN 61184 – VE.Direct Control *(Custom, Inverter-Steuerung)*
**Format:** Single Frame, 3 Byte, **PDU1 (adressiert)**
**Richtung:** Pi → VE.Direct-Gateway (Zieladresse dynamisch aus empfangenen PGN-130910-Frames)

Steuert den Inverter-Modus. PDU1 (pf=0xEF=239 < 240) → Zieladresse im CAN-Frame.
Der Pi ermittelt die aktuelle Gateway-Adresse aus `_network[(130910, src, ...)]`.
Fallback: Broadcast (0xFF) wenn Gateway noch nicht gesehen.

| Offset | Typ   | Inhalt |
|--------|-------|--------|
| 0      | uint8 | deviceInstance (0 = Inverter) |
| 1      | uint8 | commandType (0 = Modus setzen) |
| 2      | uint8 | value (Modus-Wert, s. u.) |

**Inverter-Modus-Werte (commandType = 0, deviceInstance = 0):**

| Wert | Modus |
|------|-------|
| 2    | **An** (Inverting) |
| 4    | **Aus** |
| 5    | **Eco** (Low Power Standby) |

Unbekannte Werte werden vom Gateway ignoriert. Der neue Zustand ist
anschließend in PGN 127750 und 130910 sichtbar.

**PGN 130911 (veraltet):** Das Gateway akzeptiert als Fallback noch PGN 130911
(PDU2, broadcast). Neuer Code soll ausschließlich PGN 61184 verwenden.

---

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

### PGN 126992 – System Time
**Format:** Single Frame, 8 Byte · **Richtung:** Pi → broadcast

Wird beim Start und auf Anfrage (`/api/system/time-sync`) gesendet.

---

## VE.Direct-HEX-Steuerung (Gateway-intern)

Wenn das Gateway PGN 61184 empfängt, sendet es auf dem UART des Zielgeräts
einen VE.Direct-HEX-SET-Befehl (Register 0x0200 = Device Mode).

**Befehlsformat:** `:8LLHH00VVCC\r\n`
- LL = Register-Low-Byte (0x0200 → `00`)
- HH = Register-High-Byte (0x0200 → `02`)
- VV = Modusw-Wert
- CC = Prüfsumme: `(0x55 − (0x08 + LL + HH + 0x00 + VV)) & 0xFF`

| Modus    | Befehlsstring         | Prüfsumme                      |
|----------|-----------------------|--------------------------------|
| On  (2)  | `:80002000249\r\n`    | 0x55 − (8+0+2+0+2) = 0x49     |
| Off (4)  | `:80002000447\r\n`    | 0x55 − (8+0+2+0+4) = 0x47     |
| Eco (5)  | `:80002000546\r\n`    | 0x55 − (8+0+2+0+5) = 0x46     |

---

## VE.Direct Data Timeout (Gateway)

Das Gateway sendet PGN 127507, 127750 und 130910 nur wenn der letzte
VE.Direct-Frame des jeweiligen Geräts **weniger als 5 Sekunden alt** ist.
Bei Überschreitung (Gerät abgeschaltet/getrennt) werden keine PGNs gesendet,
sodass der Pi keine veralteten Werte mehr empfängt.

---

## CAN-Konfiguration

| Parameter              | Wert |
|------------------------|------|
| Interface (Pi)         | `can0` |
| Interface (Gateway)    | FlexCAN (Teensy 4.1) |
| Baudrate               | 250 kbit/s (NMEA 2000) |
| Quelladresse Pi        | 100 (fest, `RPI_SOURCE_ADDRESS`) |
| Quelladresse Gateway   | auto (bevorzugt 0) |
| Quelladresse Battery Board | auto (bevorzugt 1) |
| Light Bank Instanz     | 1 (`LIGHT_BANK_INSTANCE`) |
| VE.Direct-Baudrate     | 19200 Baud |
| VE.Direct Data Timeout | 5000 ms |

**Fast-Packet-PGNs (Pi):** `126720, 126996, 130900, 130901, 130902, 130910`
**Fast-Packet-PGNs (Gateway):** `130910, 130912`

---

## PGN-Übersicht (Gesamtnetz)

| PGN    | Richtung              | Quelle / Ziel           | Inhalt |
|--------|-----------------------|-------------------------|--------|
| 59904  | Pi → alle            | Pi (100) → broadcast    | ISO Request (fordert PGN 126996 an) |
| 60928  | alle → alle          | jedes Gerät             | ISO Address Claim (Adressvergabe) |
| 61184  | Pi → Gateway         | Pi (100) → Gateway (auto) | VE.Direct Control / Inverter-Modus (PDU1) |
| 126720 | Pi ↔ andere          | Pi (100) ↔ alle         | Licht-Helligkeit setzen / empfangen |
| 126992 | Pi → alle            | Pi (100) → broadcast    | Systemzeit |
| 126996 | Geräte → Pi          | alle → Pi               | Produktinformation / Gerätename |
| 127505 | Sensor → Pi          | – → Pi                  | Füllstand Tanks |
| 127506 | Battery Board → Pi   | Battery Board → Pi      | DC-Typ (Lichtmaschine / Solar) |
| 127507 | Gateway → Pi         | Gateway (auto) → Pi     | Ladestatus: Orion-XS #1/#2, Smart IP43, MPPT 75/15 |
| 127508 | Battery Board → Pi   | Battery Board → Pi      | Batteriespannung / -strom |
| 127750 | Gateway → Pi         | Gateway (auto) → Pi     | Inverter-Betriebszustand |
| 130312 | Battery Board → Pi   | Battery Board → Pi      | Temperatur |
| 130900 | Battery Board → Pi   | Battery Board → Pi      | Battery Stats (Custom) |
| 130901 | Battery Board → Pi   | Battery Board → Pi      | BMS Pack Data (Custom) |
| 130902 | Battery Board → Pi   | Battery Board → Pi      | BMS Cell Data (Custom) |
| 130910 | Gateway → Pi         | Gateway (auto) → Pi     | VE.Direct Extended (Custom) – alle 5 Geräte |
| 130911 | Pi → Gateway         | Pi (100) → broadcast    | Inverter-Steuerung (veraltet, Fallback für 61184) |
| 130912 | Gateway → Pi         | Gateway (auto) → Pi     | Solar Extended (Custom) – MPPT 75/15 |
