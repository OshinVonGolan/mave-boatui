# Mave Boat Monitor — Arbeitsanweisungen für AI

## Projekt-Übersicht

Raspberry Pi Echtzeit-Bootsmonitor mit Web-UI (PWA). Liest alle Bordnetz-Daten über NMEA 2000 (CAN-Bus) ein, zeigt sie in einer mobilen Web-App an und erlaubt Steuerung von Inverter und Licht. Daten kommen von zwei Teensy-Mikrocontrollern die als NMEA 2000 Knoten am Bus hängen.

**URL im Netz:** `http://mave-control.local:8080` (Pi im WLAN, Hostname `mave-control`)  
**Start:** systemd-Service `boatui.service`

---

## Pflicht-Regeln (IMMER beachten)

### 1. Version bumpen bei jedem Commit der die App verändert
```python
# main.py Zeile ~45
VERSION = _git_semver() or '1.56.4'   # ← hochzählen: 1.56.4 → 1.56.5
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

### 3. Zwei Fallen, die schon einmal teuer waren

**Nichts Blockierendes in async-Handler.** Der Pi Zero W hat einen Kern und einen
uvicorn-Worker. `/api/history` gab früher 10.800 Einträge über den `jsonable_encoder`
zurück — am Gerät gemessen 11,1 s, in denen der komplette Server stand (auch
`/api/status` brauchte dann 8,2 s statt 0,1 s). Alles, was rechnet, Dateien liest
oder `subprocess` startet, gehört in `run_in_executor`.

**Der Pi hat keine Echtzeituhr.** Nach Stromausfall läuft seine Uhr falsch, bis NTP
greift. Zeitfenster im Frontend deshalb NIE gegen `Date.now()` filtern, sondern
gegen `nowTs()` aus `charts.js` — der Offset kommt aus `server_now` in der Antwort
von `/api/history`.

### 4. Was existiert, sagt der laufende Bus — nicht der Quellordner

`GET /api/network` listet jede PGN und jedes Gerät, das der Pi wirklich gesehen hat. **Das
ist die Wahrheit.** Stand 27.08.2026 hängen genau vier Geräte am Bus:

| src | Gerätename | Firmware-Projekt | sendet |
|-----|-----------|------------------|--------|
| 0 | VE.Direct NMEA2K GW | `VE.Direct - NMEA2K Gateway` | 127507, 127750, 130910, 130912, 130913 |
| 1 | VE.Direct Bridge | `Battery Bord` | 127508, 130900, 130901, 130902 |
| 22 | Arduino N2k->PC | `Mave Boat Net Monitor` | 127505 (Tanks), 126720 (Licht) |
| 4 | (namenlos) | unbekannt | nur PGN 0, 5 Frames |

**Nicht am Bus: 127506 und 130312.** Parser dafür existieren, laufen aber leer.

**Es gibt ein ZWEITES NMEA-2000-Netz an Bord — das Navigationsnetz.** Der Pi hängt dort
nicht dran und sieht davon nichts: Raymarine ITC-5 (mit Airmar-Geber für Tiefe, Speed,
Temperatur), Garmin AIS, Yacht Devices WLAN-Gateway, Garmin GMI 10, SeaTalk-auf-SeaTalk-NG-
Adapter und daran der alte Raymarine-Autopilot. Diese Geräte stehen in `devices.json` und
tragen in der Geräteübersicht den Status `fremdnetz` — **kein Ausfall, sondern außer
Reichweite**. Wer dort Live-Daten will, muss zuerst das Yacht-Devices-Gateway anbinden.

**Das ist KEIN Grund zum Aufräumen.** Es gibt Vorbereitungen für Hardware, die noch nicht
verbaut ist — PGN 127506, PGN 130312, `alternator`, `solar2`, `solar3`, `wind`. Die Pfade
bleiben stehen, sie kosten nichts (fehlt die Quelle, liefert `.get()` None) und werden
gebraucht, sobald die Geräte dran sind. `/api/network` sagt, was **existiert** — es ist
kein Argument fürs Löschen.

In `~/Dokumente/PlatformIO/Projects/` liegen weitere Projekte (`Performance Meter`,
`baltic500-logger`, `Vivis-Wundersamer-Wecker`), die mit diesem Boot **nichts zu tun haben**.
Ein Projekt im selben Ordner ist kein Beleg dafür, dass das Gerät am Bus hängt — dieser
Trugschluss hat schon einmal zu einem erfundenen Befund geführt. Erst `/api/network` fragen,
dann analysieren.

### 5. PROTOCOL.md bei neuen PGNs aktualisieren
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
| `jsonio.py` | `read_json` (wirft nie) und `write_json` (atomar: tmp + fsync + replace) — alle JSON-Dateien laufen darüber |
| `history_store.py` | Verlauf als NDJSON auf der Platte, gepuffert im eigenen Thread geschrieben, beim Start zurückgeladen |
| `static/js/icons.js` | SVG-Icon-System: `icon(name, {size})` und `weatherIcon(code)` — **keine Emojis im UI** |
| `geraete.py` | Geräteübersicht: verbindet die gepflegte Liste (`devices.json`) mit den laufenden Quellen (Bus, WLAN, Stoker) zu einem Snapshot. Rein rechnend, ohne Netzzugriff — deshalb ohne Boot testbar (`test_geraete.py`). Konzept: `KONZEPT-GERAETE.md` |
| `static/js/geraete.js` | Seite „Geräte an Bord": Kacheln, Liste mit Eltern-Kind-Baum, Detail-Popup, Bearbeiten der Stammdaten |
| `static/js/topologie.js` | Das Schaltbild der Geräteseite: eine Schiene je Netz, Geräte als Knoten, Linienfarbe = Verbindungsart. Layout wird gerechnet, nicht simuliert |
| `static/js/orte.js` | Gemeinsame Ortsliste (Namen, Farben, Flächen des Grundrisses) und der Mini-Schiffsriss fürs Popup |
| `heating.py` | Anbindung an die Stoker-Heizung: pollt den Hub zentral (max. 1 Hz laut Gerätedoku), hält den Zustand vor, reicht Schaltbefehle durch. Relaisbetrieb bewusst nicht abgebildet |
| `static/js/heizung.js` | Heizungs-Kachel, Detailseite und Einstellungen. Spricht nur mit dem Pi (`/api/heizung/*`), nie direkt mit dem Hub |
| `static/js/verlauf.js` | Seite „Stromverlauf“: Erzeugung gestapelt, Verbrauch als Linie, Energiesummen. Holt eigene Daten per `/api/history?range=` |
| `static/js/weather.js` | Wetterkachel und Wetterseite. Bis zu fünf Orte plus die eigene Position, Modellwahl, Modellvergleich; Wind/Böen/Seegang/Regen als Leinwand-Streifen über 72 Stunden. Quelle Open-Meteo über `/api/weather`, `/api/wetter/*` |
| `static/js/grundrisseditor.js` | Werkzeug zum Zeichnen des Grundrisses — **läuft im LOGBUCH auf dem Server**, nicht am Boot. Rechteck- und Vieleckwerkzeug, sieben gerechnete Rumpfformen, Planvorlage zum Nachziehen. Arbeitet auf einer Kopie, speichert über `PUT /api/logbuch/grundriss`; ans Boot geht eine Datei. Konzept: `KONZEPT-GRUNDRISS.md` |
| `sync/grundriss.py` | Die Prüfung des Risses — dieselbe auf Server und Pi. Der Prüfer baut ein neues Objekt, statt das eingehende zu säubern |

### Konfiguration / Daten

| Datei | Inhalt |
|-------|--------|
| `presets.json` | Licht-Presets, Batterie-Instanzen (service/starter), Tank-Namen, Kapazitäten |
| `alarms.json` | Aktive Alarme (zur Laufzeit geschrieben) |
| `connectivity.json` | Konfiguration für Connectivity-Monitor |
| `monday.json` | Monday.com Token, Board-IDs |
| `PROTOCOL.md` | Vollständige NMEA 2000 PGN-Spezifikation dieses Systems |
| `NETZWERK-UND-ZUGANG.md` | Wie man an Pi, Server und GitHub kommt, wie das Netz aufgebaut ist und welche Fallen es hat. Am laufenden System erhoben |
| `KONZEPT-GRUNDRISS.md` | Der Grundriss als Daten: Aufbau, Prüfung beim Speichern, das Zeichenwerkzeug, die gerechneten Rumpfformen — und warum die automatische Planerkennung noch fehlt |
| `KONZEPT-SERVER.md` | Der geplante Betrieb mit Server: eine PWA für beide Seiten, Sync, Konten, Sicherheit, Etappen |
| `devices.json` | Geräteliste an Bord: Stammdaten, Einbauort, Netz, Zuordnung zur Live-Quelle |

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
- `battery.js`: neues Gerät in `_baGeraete()` an die Liste hängen (`renderDeviceTiles()` ruft sie auf)
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
- **Geräte-Kacheln** der Batterie-Detailseite sind rein JS-generiert — kein HTML nötig. Neues Gerät in
  `_baGeraete()` (battery.js) an die `liste` anhängen: `{name, icon, watt, chip, an, sub}`. Alle Kacheln haben
  bewusst dieselbe Form (Name, Zustands-Chip, eine große Wattzahl, eine Zusatzzeile), damit die Reihe nicht
  als Treppe ausläuft — was da nicht hineinpasst, gehört nicht auf die Batterieseite.
  (Die früheren Helfer `_tile()` / `_kv()` gibt es nicht mehr.)
- **State-Updates** nur wenn Wert sich geändert hat (`if target.get(k) != v`), sonst broadcast-Sturm.
- **Timeout-Schutz** im Gateway: PGNs werden nur gesendet wenn letzter VE.Direct-Frame < 5s alt (`DATA_TIMEOUT_MS = 5000`). Pi bekommt nie veraltete Werte.

---

## Repos

- **mave-boatui** (dieser Pi-Code): `https://github.com/OshinVonGolan/mave-boatui`
- **VE.Direct NMEA2K Gateway** (Teensy-Firmware): `https://github.com/OshinVonGolan/VEDirect-NMEA2K-Gateway`

## Änderungsverzeichnis in Commit-Nachrichten

Eine Commit-Nachricht hat **zwei Leser**: den Entwickler, der in einem Jahr
wissen will, WARUM etwas so gebaut wurde, und den Eigner, der im Logbuch sehen
will, WAS sich für ihn ändert. Beides in einen Text zu pressen macht ihn für
beide schlecht.

Deshalb trägt jeder Commit am Ende eine Kurzfassung — Zeilen, die mit `* `
beginnen:

```
Konten: Person und Spitzname, und sie lassen sich bearbeiten

[ausführliche Begründung für Entwickler, so lang wie nötig]

* Konten haben jetzt einen Spitznamen, der überall angezeigt wird
* Konten lassen sich bearbeiten, nicht nur sperren und löschen
```

Nur die `* `-Zeilen erscheinen im Logbuch unter „Änderungen". Sie sind für den
Eigner geschrieben: ein Satz je Änderung, keine Dateinamen, keine Fachbegriffe,
und sie sagen die Wirkung, nicht die Umsetzung.

Fehlen sie, bleibt es bei der Überschrift — die ist ohnehin die Kurzfassung.
