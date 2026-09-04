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
| **Router** | `192.168.1.1` | Teltonika RUTX50 — der Router des Bootes und zugleich der einzige. Uplink über Starlink, Kabel oder Mobilfunk |
| **Starlink** | `192.168.100.1:9200` | Dish-Status über gRPC, nur an Bord erreichbar |
| **Server** | `46.224.25.31` | Der Mietserver, auf dem auch Gantrya läuft |

### Es gibt nur EIN Netz

Wichtig für alle, die später hier arbeiten: **Der Eigner wohnt auf dem Boot.**
Der RUTX50 ist sein Router, immer — es gibt kein getrenntes „Heimnetz" neben
einem „Bordnetz". Wenn im Gespräch „zu Hause" fällt, ist das Boot gemeint.

Daraus folgt:

- `192.168.1.1` ist **immer** der RUTX50. Der Verbindungsmonitor
  (`connectivity.py`) spricht also stets mit dem richtigen Gerät.
- Die Adressen der Bordgeräte sind stabil, unabhängig vom Standort. Wandert das
  Boot, wandert das ganze Netz mit.
- Was sich ändert, ist nur der **Uplink**: Starlink oder Kabel am Liegeplatz,
  Mobilfunk unterwegs. Genau daran hängt später die Betriebsart des Syncs.

Am laufenden System abgelesen (03.09.2026, am Liegeplatz):

```
Uplink        wired          (Starlink hängt am Router, Zustand CONNECTED)
Mobilfunk     o2 - de, 5G (NSA), VoLTE — vorhanden, aber nicht der aktive Weg
WLAN-Clients  8
```

## Zugänge

### Zum Pi

| Weg | Wie | Wann |
|---|---|---|
| **SSH** | `ssh joshy@192.168.1.103` — Schlüssel `~/.ssh/id_ed25519` liegt, kein Passwort nötig | im selben Netz oder über Tailscale |
| **Weboberfläche** | `http://192.168.1.103:8080` | dito |
| **Tailscale** | `100.116.85.65`, dasselbe über das Tailnet | von überall, solange beide Geräte im Tailnet sind |

SSH läuft mit hinterlegtem Schlüssel, ganz gewöhnlich über das `ssh`-Kommando —
paramiko wird nicht gebraucht. (Eine frühere Fassung dieses Dokuments behauptete
das Gegenteil; das war falsch und hat schon einmal Zeit gekostet.)

**Wo der Zugang endet: `sudo` verlangt ein Passwort**, und das liegt nur beim
Eigner. Alles, was root braucht, geht deshalb nicht selbständig:

- Ports unter 1024 belegen (also auch 443)
- systemd-Einheiten anlegen oder ändern
- die nginx-Konfiguration anfassen
- Pakete installieren

Für den Alltag reicht das trotzdem: Der Dienst läuft als `joshy`, und das Update
startet ihn selbst neu (siehe unten).

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

## Was auf dem Pi sonst noch läuft

```
nginx           als root auf Port 80, Auslieferung der Standardseite
                (nicht von uns eingerichtet, Konfiguration ohne sudo unantastbar)
boatui.service  die App, als joshy auf Port 8080, ohne TLS
can0.service    der CAN-Bus
```

**Zertifikat.** Über `acme.sh` liegt eines für `pi.mave.circuit-sailor.com`
(ECC-256, Let's Encrypt, geholt per DNS-01 über die IONOS-API):

```
~/.acme.sh/pi.mave.circuit-sailor.com_ecc/fullchain.cer
~/.acme.sh/pi.mave.circuit-sailor.com_ecc/pi.mave.circuit-sailor.com.key
```

Die Erneuerung läuft bereits per Cron (viermal täglich `acme.sh --cron`).
DNS-01 wurde gewählt, weil der Name auf eine **private** Adresse zeigen soll —
eine HTTP-Prüfung von außen ist dafür unmöglich.

**Seit 04.09.2026 liefert der Pi HTTPS aus.** nginx nimmt auf 443 an und
reicht an uvicorn auf 8080 weiter (`/etc/nginx/sites-available/mave-tls`).

```
Zertifikat   /etc/nginx/mave/{fullchain.cer,privkey.key}
             Verzeichnis gehört joshy — acme.sh läuft als joshy im Cron und
             muss dort schreiben können; nginx liest als root.
Erneuerung   acme.sh --install-cert mit reloadcmd "sudo /usr/sbin/nginx -s reload"
Sudo-Regel   /etc/sudoers.d/020_nginx_reload — genau ein Befehl, ohne Passwort.
             Ohne sie müsste alle 60 Tage jemand von Hand eines eintippen, und
             wenn es niemand tut, ist die Bordansicht weg.
Verbindung   TLSv1.3 mit ChaCha20-Poly1305, Handschlag 71 ms über das Bordnetz
```

**Warum ChaCha20 und nicht AES:** Der Zero hat keine Krypto-Beschleunigung
(`OPENSSL_armcap=0x0`). Gemessen: ChaCha20 37 MB/s, AES-256-GCM 9,7 MB/s.
Python kann die TLS-1.3-Cipher nicht wählen (`ssl.set_ciphersuites` fehlt) —
uvicorn wäre deshalb beim langsamen AES gelandet. Das ist der Grund, warum die
Verschlüsselung bei nginx liegt und nicht in der Anwendung.

**Warum ECC und nicht RSA:** ECDSA P-256 signiert hier in 1,6 ms, RSA-2048
braucht 55 ms. Ein RSA-Zertifikat würde den einen Kern bei jedem Handschlag
für eine Zwanzigstelsekunde blockieren.

### Die Auflösung im Bordnetz

Erledigt seit 04.09.2026. Der A-Eintrag `pi.mave.circuit-sailor.com →
192.168.1.103` steht bei IONOS, aber im Bordnetz half er zunächst nicht: der
RUTX50 filtert private Adressen aus DNS-Antworten heraus (Schutz gegen
DNS-Rebinding).

Gelöst über einen **statischen Eintrag im Router** (Network → DNS → *Static
addresses*): Domain `pi.mave.circuit-sailor.com`, IP `192.168.1.103`.

Damit beantwortet der Router den Namen aus seiner eigenen Tabelle. Die
Rebind-Sperre greift dabei nicht — sie filtert nur, was von außen
hereinkommt. **Der Schutz bleibt deshalb eingeschaltet**, was die bessere
Lösung ist als ihn abzuschalten: das hätte die Sperre für sämtliche Domains
aufgehoben.

Der Weg über `http://192.168.1.103:8080` funktioniert weiterhin und bleibt der
Rückweg, falls am Router oder am Zertifikat etwas klemmt.

Zum Prüfen ohne Router-Änderung:

```
curl --resolve pi.mave.circuit-sailor.com:443:192.168.1.103 \
     https://pi.mave.circuit-sailor.com/api/zugang
```

**Warum das überhaupt zählt:** ohne TLS kein sicherer Kontext, ohne sicheren
Kontext kein Service Worker — und ohne den lässt sich die PWA nicht als
Anwendung installieren, sondern nur als Verknüpfung ablegen. Die Verschlüsselung
auf dem Pi ist also keine Kür, sondern die Voraussetzung dafür, dass an Bord
dieselbe App läuft wie unterwegs.

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
