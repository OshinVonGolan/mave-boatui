# Netzwerk und Zugänge

Für alle, die später an diesem System arbeiten — Menschen wie Agenten. Stand
03.09.2026, am laufenden System erhoben, nicht aus Quelltext geschlossen.

**Geheimnisse stehen hier nicht drin.** Dieses Dokument beschreibt *Wege*, nicht
Passwörter. Wo ein Geheimnis nötig ist, steht, wo es liegt und wer es hat.

---

## Die Geräte

| Gerät | Adresse | Was es ist |
|---|---|---|
| **mave-control** | `192.168.1.103` (WLAN, DHCP)<br>`100.116.85.65` (Tailscale)<br>`mave-control.local` (mDNS) | Raspberry Pi Zero W, ARMv6, 427 MB RAM. Der Bordmonitor. Raspbian 13 |
| **Stoker-Hub** | `192.168.1.118` | Heizungssteuerung (ESP32), eigenes Projekt `mave-heater-control` |
| **Router** | `192.168.1.1` | **Achtung, siehe unten** — an Bord ein Teltonika RUTX50, zu Hause der Heimrouter |
| **Starlink** | `192.168.100.1:9200` | Dish-Status über gRPC, nur an Bord erreichbar |
| **Server** | `46.224.25.31` | Der Mietserver, auf dem auch Gantrya läuft |

### Die Falle mit `192.168.1.x`

**Bordnetz und Heimnetz benutzen dasselbe Subnetz.** Der Pi hat hier wie dort
`192.168.1.103`, und unter `192.168.1.1` steht zu Hause der Heimrouter, an Bord
der RUTX50. Das heißt:

- Der Verbindungsmonitor (`connectivity.py`) meldet sich zu Hause beim
  **falschen** Router an und bekommt keine Daten. Das ist kein Fehler, sondern
  die Folge gleicher Adressen — an Bord funktioniert es.
- Wer eine Anleitung schreibt, muss dazusagen, **wo** er war. „Router antwortet
  nicht" heißt zu Hause etwas anderes als am Liegeplatz.

---

## Zugänge

### Zum Pi

| Weg | Wie | Wann |
|---|---|---|
| **SSH** | Benutzer `joshy`, Passwort-Anmeldung (Passwort beim Eigner) | im selben Netz oder über Tailscale |
| **Weboberfläche** | `http://192.168.1.103:8080` | dito |
| **Tailscale** | `100.116.85.65`, dasselbe über das Tailnet | von überall, solange beide Geräte im Tailnet sind |

Aus Python heraus geht SSH über **paramiko** (installiert), nicht über das
`ssh`-Kommando — es gibt keinen hinterlegten Schlüssel, nur Passwort-Anmeldung:

```python
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.1.103', username='joshy', password='...', look_for_keys=False)
```

`sudo` verlangt das Passwort. Für den Alltag wird es nicht gebraucht: Der Dienst
läuft als `joshy`, und das Update startet ihn selbst neu (siehe unten).

### Zum Server

SSH mit Schlüssel, kein Passwort:

```
ssh -i ~/.ssh/bosun_server bosun@46.224.25.31
```

Dort läuft Docker mit Caddy (80/443) davor. Gantrya und das Bootssystem stehen
getrennt nebeneinander; Einzelheiten in `KONZEPT-SERVER.md`.

### Zu GitHub

Das Token in der HTTPS-Remote-Adresse des lokalen Arbeitsplatzes ist **tot**
(Stand 02.09.2026, „Invalid username or token"). Gepusht wird über SSH:

```
git push git@github.com:OshinVonGolan/mave-boatui.git master
```

Der Pi zieht weiterhin per HTTPS und kann das auch — sein Zugang ist intakt.

---

## Der Dienst auf dem Pi

```
systemd-Einheit   boatui.service
Benutzer          joshy
Verzeichnis       /home/joshy/mave-boatui
Start             venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Neustart          Restart=always, RestartSec=5
```

Dazu `can0.service` für den CAN-Bus. `can0` ist im Zustand `ERROR-ACTIVE` —
das ist der **Normalzustand**, kein Fehler.

**Ausrollen** geschieht nicht über SSH, sondern über die App selbst: Der Knopf
„Aktualisieren" ruft `/api/system/update`, das macht `git pull` und beendet dann
den eigenen Prozess. systemd startet ihn neu (`Restart=always`). Deshalb greifen
auch Änderungen an `main.py` ohne Handarbeit — und deshalb braucht niemand
`sudo`.

Der Pi zieht aus `master`. Was nicht gepusht ist, kommt dort nie an.

---

## mDNS

`avahi-daemon` läuft, und **auf dem Pi** löst `mave-control.local` korrekt auf.
Von einem anderen Rechner aus kann es trotzdem fehlschlagen — dann fehlt dort
die mDNS-Auflösung, nicht auf dem Pi. Für Skripte deshalb die **IP** benutzen
und `.local` nur als Bequemlichkeit behandeln.

Für die geplante PWA-Umschaltung ist `.local` **kein** tragfähiger Weg: Android
löst es im Browser unzuverlässig auf.

---

## Tailscale — Übergangslösung, soll weg

Im Tailnet (`gollerjoshua@`) hängen derzeit:

```
100.116.85.65   mave-control     linux
100.70.142.119  s24-von-joshua   android
```

Das ist der **heutige** Fernzugriff des Eigners und ausdrücklich eine
Zwischenlösung: **Tailscale soll verschwinden**, sobald das eigene System den
Fernzugriff trägt. Es ist keine Architekturoption — die Anlage soll nicht von
einem fremden Koordinationsdienst abhängen.

Für die Zwischenzeit gilt: Wer heute von außen an den Pi muss, nimmt diesen
Weg. Wer plant, plant **ohne** ihn.

**Reihenfolge beim Abschalten** — erst der Ersatz, dann die Abschaltung:

1. Etappe 1 läuft (Server erreichbar, Pi verbunden, Anmeldung steht).
2. Der Fernzugriff über die eigene Adresse ist einmal im Alltag erprobt, nicht
   nur im Test.
3. Erst dann `tailscale down` und das Paket entfernen. Vorher fiele der
   Fernzugriff ersatzlos weg — und zwar genau dann, wenn das Boot allein liegt.

Zu bedenken: Über Tailscale läuft heute auch die **Handarbeit** (SSH von
unterwegs). Nach der Abschaltung bleibt dafür nur das Bordnetz — es sei denn,
die Fernwartung im Diagnosewerkzeug kann genug (Protokolle lesen, Dienst neu
starten, Update auslösen). Das gehört bei der Ausgestaltung bedacht.

## Wo die Geheimnisse liegen

| Geheimnis | Ort | Im Repo? |
|---|---|---|
| Router-Zugang (RUTX50) | `connectivity.json` auf dem Pi | nein, in `.gitignore` |
| Stoker-Passwort | `heizung.json` auf dem Pi | nein, in `.gitignore` |
| SSH-Passwort des Pi | beim Eigner | nein |
| GitHub-Zugang | SSH-Schlüssel `~/.ssh/github_ed25519` | nein |
| Server-Zugang | SSH-Schlüssel `~/.ssh/bosun_server` | nein |
| Gerätetoken und Serverpasswort (geplant) | Umgebungsvariablen des Containers | nein |

**14 Laufzeitdateien** stehen aus gutem Grund in `.gitignore` — sie liegen nur
auf dem Gerät. Landet eine davon im Repo, scheitert auf dem Pi jeder `git pull`
mit „untracked working tree files would be overwritten".

---

## Zwei Dinge, die auffallen

Nicht dramatisch, aber sie gehören notiert:

1. **SSH mit Passwort-Anmeldung.** Solange der Pi nur im Bordnetz und im Tailnet
   steht, ist die Angriffsfläche klein. Ein hinterlegter Schlüssel und
   abgeschaltete Passwort-Anmeldung wären trotzdem die bessere Grundlage,
   bevor das System aus dem Internet erreichbar wird.
2. **Die Adresse des Pi ist nicht reserviert** (DHCP, Restlaufzeit sichtbar in
   `ip addr`). Für die geplante feste Zuordnung braucht es eine Reservierung im
   RUTX50 — sonst zeigt der DNS-Eintrag eines Tages ins Leere.
