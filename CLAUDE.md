# Mave Boat Monitor — Arbeitsanweisungen für AI

## Projekt-Übersicht

Raspberry Pi Echtzeit-Bootsmonitor mit Web-UI (PWA). Liest alle Bordnetz-Daten über NMEA 2000 (CAN-Bus) ein, zeigt sie in einer mobilen Web-App an und erlaubt Steuerung von Inverter und Licht. Daten kommen von zwei Teensy-Mikrocontrollern die als NMEA 2000 Knoten am Bus hängen.

**URL im Netz:** `http://mave.local` (Pi im WLAN)  
**Start:** systemd-Service `boatui.service`

---

## Pflicht-Regeln (IMMER beachten)

### 1. Version bumpen bei jedem Commit der die App verändert
```python
# main.py Zeile ~45
VERSION = _git_semver() or '1.16.64'   # ← hochzählen: 1.16.64 → 1.16.65
```
Commit-Message-Format (für Changelog):
```
v1.16.65 — Kurze Beschreibung der Änderung

- Detailpunkt 1
- Detailpunkt 2
```

### 2. Nach jedem Commit sofort pushen
```bash
git push
```
Der Pi nutzt den **Aktualisieren**-Button im UI um `git pull` von GitHub zu machen. Ohne Push findet der Pi den neuen Commit nicht. **Immer direkt nach `git commit` auch `git push` ausführen.**

### 3. PROTOCOL.md bei neuen PGNs aktualisieren
Bei jeder Änderung am Kommunikationsprotokoll (neues PGN, neuer commandType, neue Register, geändertes Payload-Layout):
- Neue PGN-Sektion in `PROTOCOL.md` anlegen (Format, Payload-Tabelle, Beispiele)
- PGN-Übersichtstabelle am Ende ergänzen
- Fast-Packet-PGN-Liste im CAN-Konfiguration-Block aktualisieren

### 4. JS-Syntax-Check vor jedem Commit
Doppelte `let`/`const`/`var`-Deklarationen in einer Datei töten die **gesamte Datei** still (kein Fehler sichtbar, alles bricht). Prüfen mit:
```bash
node --check static/js/*.js
```
Oder manuell: grep nach doppelten `let foo` / `const foo` in der geänderten Datei.

### 5. VE.Direct-Gateway: nach Teensy-Änderungen flashen
```bash
cd "Dokumente/PlatformIO/Projects/VE.Direct - NMEA2K Gateway"
~/.platformio/penv/bin/pio run -e teensy41 -t upload
```
Serial-Monitor zum Debuggen:
```bash
timeout 15 cat /dev/ttyACM0
```

---

## Architektur

```
CAN-Bus (can0, 250 kbit/s, NMEA 2000)
    │
    ├── Battery Board (Teensy 3.1, Addr 1)
    │     PGN 127508 (Batterie V/I), 130312 (Temp),
    │     130900 (Shunt Stats), 130901 (BMS Pack), 130902 (BMS Zellen)
    │
    └── VE.Direct Gateway (Teensy 4.1, Addr 0)
          PGN 127507 (Charger Status), 127750 (Inverter),
          130910 (VE.Direct Extended), 130912 (Solar), 130913 (DC-DC)
              ↑ liest Victron-Geräte via VE.Direct Text-Protokoll (19200 Baud)
              ├── Serial3: Orion-XS DC-DC       (deviceInstance 0)
              ├── Serial4: Phoenix Inverter 2000VA (deviceInstance 0)
              ├── Serial5: Phoenix Smart IP43    (deviceInstance 1)
              └── Serial6: MPPT 75/15            (deviceInstance 3)

Raspberry Pi
    └── can_reader.py (Thread) → nmea2000.py (Parser) → BoatState
             │
             └── main.py (FastAPI) → WebSocket /ws → Browser (index.html)
                                   → REST /api/* → Browser
```

---

## Datei-Übersicht

### Backend (Python)

| Datei | Funktion |
|-------|----------|
| `main.py` | FastAPI-App, WebSocket-Endpoint `/ws`, alle REST-Routen `/api/*`, History-Deque, Broadcast-Debouncer |
| `nmea2000.py` | Alle NMEA 2000 PGN-Parser, FastPacketReassembler, CAN-Frame-Builder (Senden), PGN-Namensliste |
| `can_reader.py` | CAN-Bus-Thread, `BoatState`-Klasse, PGN-Handler-Routing, Netzwerk-Tracking |
| `alarm_engine.py` | Alarmregeln prüfen, aktive Alarme verwalten, `alarms.json` lesen/schreiben |
| `connectivity.py` | Internetverbindung und Mobilfunk überwachen (ConnectivityMonitor) |
| `monday.py` | Monday.com API-Integration (Wartungsboard) |

### Konfiguration / Daten

| Datei | Inhalt |
|-------|--------|
| `presets.json` | Licht-Presets, Batterie-Instanzen (service/starter), Tank-Namen, Kapazitäten |
| `alarms.json` | Aktive Alarme (zur Laufzeit geschrieben) |
| `connectivity.json` | Konfiguration für Connectivity-Monitor |
| `monday.json` | Monday.com Token, Board-IDs |
| `PROTOCOL.md` | Vollständige NMEA 2000 PGN-Spezifikation dieses Systems |

### Frontend (static/)

| Datei | Funktion |
|-------|----------|
| `index.html` | Einzel-HTML (SPA), alle Overlays als `<div class="overlay hidden">` |
| `css/style.css` | Komplettes CSS, CSS Custom Properties für Theme |
| `js/core.js` | Hilfsfunktionen `$()`, `fmt()`, `fmtV()`, `timeSince()`, `colorClass()` |
| `js/ws.js` | WebSocket-Verbindung, `handleData()` (verteilt Daten an alle Module), History-Fetch |
| `js/battery.js` | Batterie-Kachel, SOC-Gauge, Tagesverbrauch-Akkumulation, `renderDeviceTiles()` |
| `js/charts.js` | Verlaufsgraph (Canvas), `openBattDetail()`, BMS-Detailansicht, Chart-Rendering |
| `js/alarms.js` | Alarm-Overlay, Netzwerk-View (`renderNetworkInto()`), PGN-Detail-Popup |
| `js/lights.js` | Licht-Steuerung, Preset-Buttons |
| `js/lightdetail.js` | Licht-Detail-Overlay, PWM-Slider, Netzwerk-View im Settings-Kontext |
| `js/tanks.js` | Tank-Kacheln |
| `js/flow.js` | Energiefluss-Diagramm (SVG-Animation) |
| `js/settings.js` | Einstellungs-Overlay (Batterien, Tanks, Netzwerk) |
| `js/connectivity.js` | Internet/SIM-Status-Anzeige |
| `js/init.js` | Initialisierung, globale State-Variablen, `batteriesConfig` |
| `js/wartung.js` | Wartungsplan-Overlay (Monday.com-Integration) |
| `js/monday.js` | Monday.com Frontend-Logik |
| `js/stauplan.js` | Stauplan-Overlay |

---

## Neue PGN hinzufügen (End-to-End Checkliste)

So geht es richtig — am Beispiel der PGN 130912 (Solar Extended):

**1. Teensy (VE.Direct Gateway)**
- In `src/main.cpp`: neue Funktion `sendXxxExt()`, in `TransmitPGNs[]` eintragen, `sendDevice()` aufrufen

**2. nmea2000.py**
- `FAST_PACKET_PGNS` ergänzen (wenn Fast Packet)
- Parser-Funktion `parse_xxx()` schreiben
- Eintrag in `PGN_NAMES`
- Eintrag in `parse_pgn_fields()` (für Netzwerk-View Detail-Popup)

**3. can_reader.py**
- Import der neuen Parser-Funktion
- `_INSTANCE_PGNS` ergänzen wenn PGN eine Instanz hat (Byte 0)
- `BoatState.__init__()`: neue Felder in `self.solar` / `self.orion` etc.
- In `_handle()`: `elif pgn == 130912:` Block mit State-Update
- Tracking: `self._track_network(pgn, src, instance)` nicht vergessen
- Instanz für `_last_raw` in der Instanz-Extraktion ergänzen

**4. Frontend**
- `battery.js`: `renderDeviceTiles()` / Tile-Hilfsfunktionen anpassen
- `ws.js / charts.js`: falls neue State-Felder im `data`-Dict sichtbar sein müssen

**5. PROTOCOL.md** aktualisieren (Payload-Layout, Geräte-Tabelle, PGN-Übersicht)

**6. Version bumpen + committen**

---

## Wichtige Eigenheiten & Fallstricke

### VE.Direct Protokoll-Einheiten (Orion-XS)
Nicht alle Felder sind in mV/mA — Orion-XS-spezifisch:
- `V`: mV (÷ 1000 → V) — Standard
- `I`: mA (÷ 1000 → A) — Standard
- `DC_IN_V`: **0,01V-Einheiten** (÷ 100 → V) — NICHT mV!
- `DC_IN_I`: mA (÷ 1000 → A)
- `DC_IN_P`: W direkt
- `OR` (Off Reason): hex-formatiert im Text → `strtoul(str, &end, 0)` nötig

### OR-Feld (Orion-XS Off Reason) hex-parsen
VE.Direct sendet `OR = 0x00000080` als ASCII. `strtol(..., 10)` gibt 0! Richtig:
```cpp
uint32_t orVal = (uint32_t)strtoul(orStr, &end, 0);  // base 0 = auto-detect hex
```

### PGN 127507 Byte-Layout — Nibble-Packing
Byte 0 ist NICHT der volle device instance Wert:
```
byte 0 bits 0–3:  device instance   → data[0] & 0x0F
byte 0 bits 4–7:  battery instance  → (data[0] >> 4) & 0x0F
byte 1 bits 0–3:  charge state      → data[1] & 0x0F
```
Falsch: `inst = data[0]` (gibt 32, 64, ... statt 0, 1, 3 wenn battery instance ≠ 0).
Richtig: `inst = data[0] & 0x0F`. Außerdem muss 127507 in `_INSTANCE_PGNS` stehen, sonst doppeltes Tracking (einmal mit `None`, einmal mit Instanz).

### Inverter-Toggle — Optimistic UI
Der Toggle soll sofort umschalten, nicht erst nach Bus-Bestätigung (~5–10 s).
Pattern in `ws.js`:
```js
// 1. Sofort: _invCurrentState setzen + Lock für 20 s
_invCurrentState = isOn ? 'Aus' : 'Aktiv';
_invLockUntil    = Date.now() + 20000;
_invPending      = !isOn;   // "Startet…"-Label solange Bus nicht bestätigt
updateInverterCard({ state: _invCurrentState });  // sofort rendern
// 2. API async senden
setInverterMode(newMode);
// 3. In updateInverterCard: _invPending = false sobald state === 'Aktiv' vom Bus
// 4. Bei API-Fehler: optimistisches Update rückgängig machen
```
Lock-Zeit groß genug wählen (20 s) — der Inverter braucht ~5–10 s zum Hochfahren, erst dann kommt CS=9 per VE.Direct zurück.

### BoatState.to_dict() — `_last_seen` herausfiltern
`charger._last_seen` ist ein internes float, darf nicht ins JSON:
```python
charger_d = {k: v for k, v in self.charger.items() if k != '_last_seen'}
```

### Tagesverbrauch Ah — Reset-Falle
`_accumTodayWh()` in battery.js setzt bei `_lastShuntTs === null` den Zähler zurück und kehrt zurück ohne zu akkumulieren. Nach `recomputeDailyAhFromHist()` muss `_lastShuntTs` auf `now` (nicht `null`) gesetzt werden wenn keine History vorhanden. Sonst: nächster WS-Call resettet alles.

### Fast Packet Reassembler
PGNs in `FAST_PACKET_PGNS` werden durch `FastPacketReassembler` zusammengesetzt. Erst wenn alle Frames da sind gibt `process()` den vollen Payload zurück. Das macht ~30% der Frames zu `None` — ganz normal.

### Netzwerk-View: Einträge werden nie aus dem Backend gelöscht
`_network`-Dict in `CanInterface` wächst für immer. Frontend filtert `age_s > 300` (5 Minuten) beim Rendern heraus. Bei sehr langen Sessions könnte das Dict groß werden — ggf. Backend-Cleanup ergänzen.

### CAN-Adresse des VE.Direct-Gateways ist dynamisch
Der Pi ermittelt die Gateway-Adresse dynamisch aus empfangenen PGN-130910-Frames. Für PGN 61184 (Inverter-Steuerung) wird die aktuelle Adresse über `_find_vedirect_gateway_src()` ermittelt.

---

## Geräte-Übersicht

| Gerät | Hardware | NMEA-Adresse | Sendet PGNs |
|-------|----------|-------------|-------------|
| mave-boatui | Raspberry Pi | 100 (fest) | 59904, 61184, 126720, 126992 |
| Battery Board | Teensy 3.1 | auto (bevorzugt 1) | 127508, 130312, 130900, 130901, 130902 |
| VE.Direct Gateway | Teensy 4.1 | auto (bevorzugt 0) | 127507, 127750, 130910, 130912, 130913 |

**Victron-Geräte am Gateway:**

| Port | Gerät | Typ | deviceInstance |
|------|-------|-----|---------------|
| Serial3 | Orion-XS 12/12-30A | `VE_DCDC` | 0 |
| Serial4 | Phoenix Inverter 2000VA | `VE_INVERTER` | 0 |
| Serial5 | Phoenix Smart IP43 | `VE_CHARGER` | 1 |
| Serial6 | MPPT 75/15 | `VE_SOLAR` | 3 |

---

## Was gut funktioniert (bewährte Muster)

- **Neue Geräte/PGNs immer End-to-End** implementieren: Teensy → nmea2000.py → can_reader.py → Frontend. Kein halbfertiges Zwischending einbauen.
- **DEBUG_DUMP = true** kurz flashen um unbekannte Geräte zu identifizieren (alle Felder werden gedumpt wenn neuer Frame kommt). Danach wieder auf `false`.
- **Device-Tiles** in `battery.js` sind rein JS-generiert — kein HTML nötig. Felder mit `_kv(label, val, unit)` hinzufügen.
- **State-Updates** nur wenn Wert sich geändert hat (`if target.get(k) != v`), sonst broadcast-Sturm.
- **Timeout-Schutz** im Gateway: PGNs werden nur gesendet wenn letzter VE.Direct-Frame < 5s alt (`DATA_TIMEOUT_MS = 5000`). Pi bekommt nie veraltete Werte.

---

## Repos

- **mave-boatui** (dieser Pi-Code): `https://github.com/OshinVonGolan/mave-boatui`
- **VE.Direct NMEA2K Gateway** (Teensy-Firmware): `https://github.com/OshinVonGolan/VEDirect-NMEA2K-Gateway`
