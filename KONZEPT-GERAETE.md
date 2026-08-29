# Geräteübersicht — Konzept und Stand

Stand 28.08.2026. Verbindlich für die Weiterarbeit. Wer etwas anders bauen
will, ändert zuerst dieses Dokument.

---

## 1. Was die Seite beantwortet

Drei Fragen, in dieser Reihenfolge:

1. **Was hängt überhaupt an Bord?** Das Wissen war verstreut: der
   NMEA-2000-Bus in der Netzwerk-Ansicht, die Heizungsknoten in der
   Heizungsseite, Router und Starlink in der Verbindungsanzeige, der Rest in
   niemandes Liste.
2. **Lebt es?** Ein Blick statt fünf Seiten.
3. **Wie komme ich ran?** IP, Einbauort, Sicherung, Handbuch, Elterngerät.

Ausdrücklich **kein** Ziel: eine weitere Bedienoberfläche. Geschaltet wird
weiterhin auf den Fachseiten. Die Geräteseite zeigt und verlinkt dorthin.

---

## 2. Entscheidungen

| Frage | Entscheidung |
|---|---|
| Darstellung | Kategorie-Kacheln → Liste je Gruppe → Detail-Popup |
| Umfang | automatisch erkannte Geräte **plus** gepflegte Liste (auch stumme Geräte) |
| Ort | `mave-boatui`. Der Stoker bleibt reiner Datenlieferant; an der Firmware ändert sich nichts |

Der Bootsriss als große Karte ist vertagt, nicht gestrichen: der Einbauort
steckt als Ortsschlüssel im Datenmodell, und das Detail-Popup zeigt ihn schon
heute in einem kleinen Riss.

---

## 3. Die beiden Netze an Bord

Das Boot hat **zwei getrennte NMEA-2000-Netze**. Der Pi hängt nur am
**Bordnetz**. Das **Navigationsnetz** sieht er nicht:

```
Bordnetz (der Pi hört mit)          Navigationsnetz (der Pi hört NICHT mit)
  VE.Direct NMEA2K Gateway            Raymarine ITC-5 ── Airmar-Geber
  Batterie-Board                         (Tiefe, Speed, Temperatur)
  Boat Net Monitor                    Garmin AIS
                                      Yacht Devices WLAN-Gateway
                                      Garmin GMI 10 Display
                                      SeaTalk-auf-SeaTalk-NG-Adapter
                                          └── Raymarine Autopilot (SeaTalk)
```

Geräte im Navigationsnetz sind deshalb **nicht offline** — sie sind außerhalb
der Reichweite dieser Anzeige. Dafür gibt es den eigenen Status `fremdnetz`
(„anderes Netz"), der die Kachel nicht rot färbt. Ein Ausfall dort wäre eine
Behauptung ohne Beleg.

**Ausbaupfad:** Das Yacht-Devices-WLAN-Gateway hängt am Navigationsnetz und
kann NMEA 2000 über WLAN ausgeben. Wenn es einmal mit dem Pi spricht, werden
ITC-5, AIS und GMI 10 hier live statt nur gelistet — das Datenmodell muss
dafür nicht angefasst werden, es braucht nur eine weitere Quelle und
`match: {typ: "n2k", …}` an den betroffenen Einträgen.

---

## 4. Datenmodell

### 4.1 Stammdaten — `devices.json`

Gelesen und geschrieben über `jsonio` (atomar), wie `stauplan.json`.

| Feld | Inhalt |
|---|---|
| `id` | stabiler Schlüssel, für den Bediener unsichtbar |
| `name` | Anzeigename |
| `kategorie` | genau eine (Abschnitt 5) |
| `netz` | `n2k-bord`, `n2k-nav`, `seatalk`, `lan`, `vedirect`, `analog`, `keins` |
| `ort` | Ortsschlüssel aus der gemeinsamen Ortsliste |
| `hersteller`, `modell`, `seriennr`, `baujahr` | für den Ersatzteilfall |
| `versorgung`, `sicherung` | 12V/230V, Beschriftung im Panel |
| `doku` | Link zum Handbuch (nur `http`/`https` wird verlinkt) |
| `verbunden_an` | `id` des Elterngeräts — ergibt die Baumdarstellung |
| `sprung` | zuständige Fachseite |
| `match` | Zuordnung zur Live-Quelle; fehlt sie, ist das Gerät stumm bzw. im Fremdnetz |

### 4.2 Live-Quellen

| Quelle | Woher | Zustand |
|---|---|---|
| NMEA-2000-Bus | `can_reader.get_network_stats()` | vorhanden; um `name_hex` erweitert |
| N2K-Identität | `_device_addrclaim` (PGN 60928) | wurde gesammelt, jetzt auch ausgeliefert |
| WLAN-Clients | `connectivity.py`, Feld `router.wifi_clients` | **war schon da** — kein neuer Sammler nötig |
| Vergebene Adressen | `connectivity.py`, Feld `router.dhcp_leases` (RutOS `/api/dhcp/leases/ipv4/status`, alle 100 s) | erfasst Geräte am **Kabel**, die in der WLAN-Liste nicht stehen können |
| Uplink | `conn_mon.get_status()` | vorhanden |
| Heizung | `heating.py` (`snapshot()`) | vorhanden, wird mitbenutzt — **kein zweiter Poll** |

**Zwei Qualitäten von Wissen, bewusst getrennt.** Die WLAN-Liste ist eine
Aussage über JETZT: wer dort steht, funkt gerade. Eine DHCP-Lease ist
schwächer — das Gerät hat hier einmal eine Adresse bekommen, und die läuft
stundenlang weiter, auch wenn es längst von Bord ist. Beides in einen Topf zu
werfen hieße, ein weggefahrenes Handy als „online" zu zeigen. Deshalb:

| Befund | Status |
|---|---|
| steht in der WLAN-Liste | `online` |
| nur eine Lease | `unbekannt` — „Adresse vergeben, Verbindung nicht bestätigt" |
| weder noch (bei erreichbarem Router) | `offline` |

**Restlücke:** Ein Kabelgerät mit **fest eingetragener** Adresse hat keine
Lease und funkt nicht — der Router weiß dann nichts von ihm, und es erscheint
als `offline`. Wer so ein Gerät führt, lässt die Zuordnung (`match`) besser
weg; dann steht es als `stumm` in der Liste, was der Wahrheit entspricht.

### 4.3 Zuordnung (`match`)

```json
{ "typ": "n2k",    "name_hex": "…", "src": 0, "device_name": "…", "pgn": 130912 }
{ "typ": "lan",    "mac": "aa:bb:cc:dd:ee:ff" }
{ "typ": "stoker", "rolle": "hub" }          // oder roomName / roomId / nodeId
{ "typ": "intern", "key": "router" }         // router | starlink | pi
```

Drei Punkte, die das Modell bewusst löst:

- **Die N2K-Quelladresse ist nicht stabil.** Sie wird beim Address Claim
  ausgehandelt. Stabil ist das 64-Bit-NAME aus PGN 60928 (`name_hex`); danach
  kommt der Modellname aus PGN 126996, erst zuletzt die Adresse.
- **Geräte hinter einem Gateway.** MPPT, Orion-XS und die beiden Phoenix
  hängen nicht am CAN, sondern per VE.Direct am Gateway, das je Gerät eine
  eigene PGN erzeugt. Mit `pgn` (und bei Bedarf `instanz`) im `match` wird
  jedes davon einzeln lebendig, statt als „das Gateway lebt schon".
- **Dieselbe Kiste nicht zweimal.** Ein Gerät, das über eine andere Quelle
  bekannt ist, erscheint nicht zusätzlich als unbekannter WLAN-Client:
  unterdrückt wird über die IP (der Stoker-Hub meldet sie selbst) und über den
  eigenen Rechnernamen (der Pi kennt sich).

Was am Bus oder im WLAN auftaucht und in keiner Liste steht, verschwindet
nicht — es erscheint als **neu erkannt** und lässt sich mit einem Klick
übernehmen. So füllt sich die Liste im Betrieb statt in einer Tippsitzung.

### 4.4 Orte

Die Ortsliste liegt in `static/js/orte.js`: Namen und Farben stammen aus
`STAU_FAECHER` (stauplan.js), die Flächen aus dem maßgetreuen Grundriss im
Stauplan-Overlay.

Sie ist dort vorerst **gespiegelt, nicht zusammengeführt**. stauplan.js hält
die Namen, das Markup die Flächen; beides an eine Stelle zu ziehen heißt,
funktionierende Anzeigen umzubauen. Das ist ein eigener Schritt (Abschnitt 8),
kein Nebenprodukt dieser Seite.

---

## 5. Kategorien und Status

Kategorien: Netzwerk, Energie, Heizung & Lüftung, NMEA-2000-Bus, Navigation,
Wasser, Licht, Motor & Antrieb, Sicherheit, Sonstiges. **Ein Gerät hat genau
eine** — sonst zählt dieselbe Sache in zwei Kacheln.

| Status | Bedeutung |
|---|---|
| `online` | frische Meldung |
| `traege` | veraltet, aber unter der Ausfallgrenze |
| `offline` | über der Grenze, obwohl eine Meldung erwartet wird |
| `stumm` | kein Melder verbaut — **kein Fehler** |
| `fremdnetz` | anderes Netz, hier nicht sichtbar — **kein Fehler** |
| `unbekannt` | die Quelle selbst antwortet nicht |

Die Fristen des Bordbusses (15 s / 300 s) sind aus `alarms.js` übernommen,
damit Bus-Ansicht und Geräteseite nicht zweierlei behaupten. Alter kommt immer
aus den Serverfeldern (`age_s`, `lastSeenS`), nie aus `Date.now()` — der Pi hat
keine Echtzeituhr.

Die Kachelfarbe richtet sich nach dem schlechtesten **wertenden** Status;
`stumm` und `fremdnetz` färben nichts rot.

---

## 6. Schnittstelle

| Endpunkt | Aufgabe |
|---|---|
| `GET /api/devices` | fertiger Snapshot: Stammdaten + Zustand + Kachelzahlen. Das Frontend rechnet nichts |
| `GET /api/devices/registry` | Stammdaten roh |
| `PUT /api/devices/registry` | Stammdaten schreiben; ungültige Eingaben werden mit 400 und Klartext abgelehnt |
| `GET /api/network` | zusätzlich `name_hex` (rückwärtskompatibel) |

Die Registry wird über einen mtime-Zwischenspeicher gelesen: sie ändert sich
selten, wird aber bei jedem Aufruf gebraucht — sonst läge bei jedem Poll ein
SD-Karten-Zugriff an. Dateizugriffe laufen über `_run_blocking`, nie im
Event-Loop (ein Kern, ein Worker).

**Gleichzeitiges Bearbeiten:** Die Oberfläche liest die Registry vor jedem
Speichern frisch und schreibt sie ganz zurück. Zwei Browser, die dasselbe Gerät
gleichzeitig ändern, überschreiben sich — bei einer Liste, die einer pflegt,
ist das vertretbar. Eine Versionsprüfung wäre der nächste Schritt, wenn es
stört.

---

## 7. Oberfläche

- `static/js/geraete.js` — Seite, Liste, Detail-Popup, Bearbeiten
- `static/js/topologie.js` — das Schaltbild (Verbindungskarte)
- `static/js/orte.js` — Ortsliste und Mini-Schiffsriss
- `static/css/geraete.css` — eigene Datei, ausschließlich vorhandene Tokens
- Einstieg über Kopfleiste und Burger-Menü; die Zurück-Geste kennt `#geraete`

Drei Ebenen: Kacheln (mit Balken aus online/träge/offline/neutral) → Liste mit
Eltern-Kind-Einrückung → Popup mit Stammdaten, Zustand, PGN-Tabelle
(führt in das vorhandene Rohdaten-Popup), Verbindungen, Mini-Riss und Sprung
zur Fachseite. Umschalter **Kategorie / Netz / Schaltbild**; Suche und „nur
Probleme" wirken immer über alle Geräte. Icons ausschließlich über `icon(...)`,
keine Emojis.

### Das Schaltbild

Je Netz eine Schiene, daran die Geräte, darunter als Baum, was an ihnen hängt.
Die **Farbe der Linie** sagt, worüber verbunden ist: VE.Direct zum MPPT ist
etwas anderes als SeaTalk zum Autopiloten, auch wenn beides „hängt an" heißt.
Gestrichelt heißt: meldet sich nicht selbst (stumm oder Fremdnetz).
Gestrichelter Rahmen heißt: gesehen, aber in keiner Liste. Ein Klick öffnet
dasselbe Detail-Popup wie in der Liste.

Zwei Festlegungen, die das Bild brauchbar halten:

- **Gerechnet, nicht simuliert.** Kein Kräftemodell, keine Zufallslage, kein
  Nachwackeln: dasselbe Boot ergibt immer dasselbe Bild, und der Pi Zero
  zeichnet einmal statt zu iterieren.
- **Ab vier Kindern untereinander statt nebeneinander.** Die sechs Kinder des
  Stoker-Hubs nebeneinander machten die Karte 2100 px breit — breiter als jeder
  Bildschirm an Bord. Als Kabelbaum bleibt sie unter 1200 px. Jede Leitung
  bekommt dabei einen eigenen senkrechten Kanal, sonst lägen sechs
  verschiedenfarbige Linien exakt übereinander.
- **Suche filtert hier nicht, sie hebt hervor.** Ein Schaltbild, aus dem Geräte
  herausgeschnitten sind, zeigt Verbindungen ins Nichts.

Die vorhandene Bus-Ansicht (`renderNetworkInto()`) bleibt unangetastet: sie ist
die technische Sicht auf den Bus, die Geräteseite die Sicht aufs Boot.

---

## 8. Stand und offene Punkte

| | Schritt | Stand |
|---|---|---|
| 1 | `geraete.py`, `devices.json`, drei Endpunkte, Aggregation | **fertig**, 23 Tests grün |
| 2 | `name_hex` aus PGN 60928 in `/api/network` | **fertig** |
| 3 | Oberfläche samt Detail-Popup und Bearbeiten | **fertig**, im Browser geprüft (Desktop und Handy) |
| 3b | Schaltbild: Geräte als Knoten, Linien nach Verbindungsart | **fertig** (v1.30.0) |
| 4 | Erstbefüllung: 29 Geräte inkl. Navigationsnetz | **fertig** — Modelle/Serien-Nr. teils offen, siehe Notizen in der Datei |
| 5 | Version bumpen, committen, pushen | **offen** — gehört dem Eigner, außerdem arbeitet gerade ein zweiter Agent im Repo |
| 6 | RutOS-Pfad für DHCP-Leases ermitteln und einbauen (Kabelgeräte) | **fertig** (v1.31.0): `/api/dhcp/leases/ipv4/status`, gegen den echten Router geprüft |
| 7 | Ortsliste zusammenführen (stauplan.js + Markup → orte.js) | offen |
| 8 | Bootsriss als große Karte mit allen Geräten (der Ort, nicht die Verkabelung) | offen, Datenmodell trägt es schon |
| 9 | Wartungsplan an Geräte hängen; Alarm bei Ausfall | offen |

**Nicht vergessen beim Commit:** `VERSION` in `main.py` hochzählen, JS prüfen
(auf dieser Maschine gibt es kein `node` — der Ersatz ist ein Chromium-Lauf,
siehe unten), danach sofort pushen, sonst findet der Pi den Commit nicht.

Prüfwerkzeuge:

```bash
python3 -m unittest test_geraete -v     # Aggregation, Zuordnung, Registry-Prüfung
```
