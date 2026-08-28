# TODO — Boat UI (Branch: `master`)

> Diese Liste ist für einen AI-Chat gedacht, der eigenständig am Boat-UI-Projekt
> (`/home/joshy/mave-boatui`) arbeitet. Jede Aufgabe ist self-contained.

> **Pflichtregeln aus CLAUDE.md — vor JEDEM Commit beachten:**
> - `VERSION` in `main.py` hochzählen
> - `node --check static/js/*.js` (doppelte `let`/`const` killen still die ganze Datei)
> - Bei neuer/geänderter PGN → `PROTOCOL.md` ergänzen (Sektion + Übersichtstabelle + Fast-Packet-Liste)
> - Nach `git commit` **sofort** `git push` (der Pi zieht per "Aktualisieren"-Button)

---

## #1 — Kritischer Alarm → NMEA-2000-Buzzer auslösen

**Ziel:** Wird ein Alarm mit `severity == 'critical'` aktiv, sendet der Pi eine
Alert-PGN auf den Bus, sodass ein NMEA-2000-Buzzer tönt. Bei Resolve/Acknowledge
wird der Alert zurückgenommen.

**PGN:** `126983` (Alert) zum Auslösen, `126984` (Alert Response) zum Quittieren.
Prio 6, Quelladresse Pi = 100.

**Dateien:**
- `nmea2000.py` — neue `build_alert_frame(...)` analog zu `build_inverter_mode_frame`
  (Zeile ~671); PGN in `PGN_NAMES` ergänzen.
- `can_reader.py` — `send_alert(...)` analog zu `send_inverter_mode` (Zeile ~159).
- `alarm_engine.py` — in `check()` (Zeile ~48) an den Stellen "neuer Alarm" /
  "resolved" einen Callback/Hook auslösen (NICHT direkt CAN aus der Engine senden —
  Callback an `main.py` durchreichen).
- `main.py` — Engine-Callback mit `can_if.send_alert(...)` verdrahten.
- `PROTOCOL.md` — PGN-126983/126984-Sektion + Übersichtstabelle.

**Akzeptanz:** Test-Alarm mit `severity=critical` → korrekt formatierte 126983-Frame
auf `can0` (mit `candump can0` prüfbar); Resolve sendet Entwarnung.

**Hardware:** Aktuell ist noch KEIN Buzzer am Bus → es gibt keine
proprietären Einschränkungen, daher auf den **Standard 126983** bauen. Empfohlener
kompatibler Buzzer: Digital Yacht **NAVAlarm** (reagiert auf Standard-Alert-PGNs).
Payload-Layout der Alert-PGN aus der NMEA-2000-Spec übernehmen.

**Abhängig von:** —

---

## #2 — Starlink-Antenne über die API deaktivieren

**Ziel:** Über die UI (Connectivity-Settings) lässt sich die Starlink schlafen
legen / aufwecken (Strom sparen).

**Hardware:** Es ist eine **Starlink Mini** — KEIN motorisierter Mast. `dish_stow`
(mechanisches Einfahren) bewegt physisch nichts und ist hier NICHT das Mittel der
Wahl. Stattdessen **`dish_power_save` / Sleep-Modus** über die API verwenden.

**Dateien:**
- `connectivity.py` — neue Methode `set_starlink_sleep(enable: bool)`, die denselben
  Reflection-gRPC-Stub nutzt wie `_fetch_starlink` (Zeile ~139), aber einen
  `dish_power_save`- (bzw. Sleep-Schedule-) Request sendet statt `get_status`.
- `main.py` — neue Route `POST /api/connectivity/starlink/sleep`.
- `static/js/connectivity.js` + Overlay in `static/index.html` — Toggle/Button.

**Akzeptanz:** Button im UI legt die Mini schlafen / weckt sie; Zustandswechsel
danach im `get_status` sichtbar. Fehler (Dish offline) sauber abgefangen, kein
Crash des Monitors.

**Offen / vor Start klären:** Sleep dauerhaft (manuell) oder als Zeitplan
(power_save schedule)? Sicherheitsabfrage im UI nötig?

**Abhängig von:** —

---

## #3 — Wartungskachel: Circle-Progress-Bar

**Ziel:** Auf der Wartungs-Home-Kachel ein Kreis-Fortschrittsbalken, der den
Erledigungsgrad zeigt; offener Rest in **Gelb** (anstehend) / **Rot** (überfällig).

**Dateien:**
- `static/js/wartung.js` — `updateWartungHomeTile()` (Zeile ~97): Fortschritt aus
  `getWartungStatus(task)` (Zeile ~30) aggregieren, SVG-Ring rendern
  (`stroke-dasharray`).
- `static/css/style.css` — Ring-Styles + Farb-Tokens (grün/gelb/rot konsistent zu
  bestehendem `colorClass()`).

**Akzeptanz:** Bei gemischten Task-Stati zeigt der Ring korrekten Prozentsatz;
überfällige Anteile rot, bald fällige gelb. Funktioniert in `half`/`normal`/`wide`
Kachelgröße.

**Abhängig von:** —

---

## #4 — Kiosk-Modus: Left-Bar + Slide-Down-Topbar

**Ziel:** Im Kiosk-Modus **keine** Top-/Bottom-Bar mehr, sondern eine **vertikale
Left-Bar** mit den Navigations-Buttons und eine **von oben einfahrbare** Topbar
(wie die mobile Ansicht).

**Dateien:**
- `static/js/display.js` — `applyDisplayConfig()` (Zeile ~199) / `_kioskNavInit()`:
  bestehende `kiosk-nav` von unten nach links umbauen; Slide-Down-Topbar-Logik.
- `static/css/style.css` — `.kiosk-nav` (aktuell `bottom`, `height: --kiosk-nav-h`,
  Zeile ~88) → seitliche Bar; `.kiosk-mode main` Padding von `bottom` auf `left`
  umstellen; Slide-Animation.
- `static/index.html` — ggf. Markup-Anpassung der `#kioskNav`-Nav (Zeile ~1704).

**Akzeptanz:** Im Kiosk-Profil erscheint links die Button-Leiste, oben die einfahrbare
Bar; normaler (Handy-)Modus unverändert. Tabs/`_kioskSetActive` funktionieren weiter.

**Abhängig von:** —

---

## #5 — Batterie-Kachel: Wert-Zellen flacher

**Ziel:** Die Zellen auf der Batterie-Kachel, in denen die Werte stehen, dürfen
ruhig etwas niedriger sein — aktuell ist sehr viel Leerraum über und unter der Zahl.

**Dateien:**
- `static/css/style.css` — Höhe/Padding der Wert-Zellen reduzieren:
  `.batt-item` (Zeile ~765, `padding: 7px 10px`) sowie `.batt-item .label` /
  `.batt-item .value` (~770/771) und die Kachel-eigene Überschreibung
  `.card-battery .batt-grid .batt-item` (~561). In der halbhohen Variante
  zusätzlich `.batt-half-stat` / `.batt-half-stat b` (~571/572).
- ggf. `static/js/battery.js` — nur falls die Höhe dort inline gesetzt wird
  (Wert-Zellen entstehen in `_renderBattGrid()` bzw. `_renderBattHalfWide()`).

> Hinweis: Diese Aufgabe nannte früher `.dt-kv` / `.dt-val` / `.dt-kvgrid` und
> `_kv()`. Diese Klassen und die Hilfsfunktion gab es zur Zeit der alten
> Geräte-Kacheln; sie sind mit dem Umbau der Batterie-Detailseite entfallen.
> Der Wunsch selbst — weniger Leerraum um die Zahl — ist unverändert offen.

**Akzeptanz:** Zahl bleibt zentriert und gut lesbar, aber der Leerraum ober-/
unterhalb ist sichtbar reduziert; Layout in `half`/`normal`/`wide` bleibt intakt.

**Abhängig von:** —
