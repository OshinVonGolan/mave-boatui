# Mave Platform (v2) — Architektur

> Umbau von der hartverdrahteten Ein-Boot-App (`master`, läuft auf dem Pi Zero W)
> zur **konfigurierbaren Produkt-Plattform** aus `Bootssystem-Monitor_Konzept.md`.
> Branch `platform`. `master` bleibt unberührt und produktiv, bis die Ziel-Hardware
> (Pi 4) bereit ist.

## Kernentscheidungen

1. **Signal K (Node) = Datenschicht.** Normalisiertes, selbstbeschreibendes
   Marine-Datenmodell (NMEA 2000 via canboat, später Victron Cerbo via
   `signalk-venus-plugin`). Löst Konzept-Prinzipien 1–3 (Config statt Code,
   datengetriebene UI, Quellen-Abstraktion) an der Wurzel.
2. **Python/FastAPI = Business-/API-Layer**, der Signal K *konsumiert* (nicht in
   Node neu geschrieben). Hier leben Boot-Profil, Modul-Loader, Alarm-Engine,
   Presets, Steuerung, Persistenz, REST+WebSocket-API.
3. **Eigene I/O bleibt bespoke** (Licht-PWM/Relais, Inverter-Steuerung via N2K) —
   als IO-Treiber, die ihren Zustand zusätzlich als Signal-K-Pfade
   (`electrical/switches/...`) spiegeln.
4. **Lean by default.** Ziel-HW zunächst Pi 4 / 1 GB (später größerer Pi als reine
   Reserve). Schwere Module (OpenCV-Pegel-OCR) nur on-demand.

## Schichten

```
NMEA2000 (can0) ─canboat─┐
Victron Cerbo (MQTT) ────┤
                         ▼
                   ┌──────────────┐   REST/WS/Delta   ┌────────────────────┐
                   │  Signal K     │ ◀──────────────▶ │ Python Business/API │
                   │ (Datenmodell) │                  │ - Boot-Profil       │
                   └──────────────┘                  │ - Modul-Loader      │
  PWM/Relais ─IO-Treiber──────────────────────────▶  │ - Alarm-Engine      │
  Router/Starlink/Wetter/Pegel ─Module───────────▶   │ - REST + WebSocket  │
                                                      └─────────┬──────────┘
                                                                ▼
                                          Web-UI · festes Display · Mobile (gleiche API)
```

## Repo-Struktur (Branch `platform`)

```
platform/
  signalk/      Signal-K-Server (Node) + Konfiguration/Plugins
  backend/      Python Business-/API-Layer
    datasource/ DataSource-Abstraktion: SignalKSource (+ ggf. weitere)
    profile/    Boot-Profil-Loader/-Schema
    modules/    Feature-Module (lights, tanks, waterlevel, weather, ...)
  profiles/     Boot-Profile pro Boot (mave.json = dieses Boot)
  frontend/     datengetriebenes Web-UI
  ARCHITECTURE.md
```

## Migrationsphasen

- **P0 (Dev-Box, jetzt):** Node+SK lokal, Boot-Profil-Schema, DataSource-Abstraktion,
  erster end-to-end Beweis (datengetriebene Tanks). Reale N2K-Daten per candump-Replay
  vom Boot in SK einspielen (Dev-Parität).
- **P1 (Pi 4):** Deploy, canboat liest echten can0 → SK; Python konsumiert SK;
  Frontend datengetrieben aus Profil; bestehende Module migriert.
- **P2 (Produkt):** Geräte-Discovery, Modul-/Plugin-Loader, Cloud-Relay-Fernzugriff
  mit Mandanten/Rollen, A/B-Update + Recovery-Layer + Watchdog (Konzept §13).

## Remote-Zugriff (Konzept §8)

Boot hängt hinter **CGNAT** (Starlink/Mobilfunk) → keine eingehende Verbindung
möglich, ein Rendezvous-Punkt ist Pflicht. **Entscheidung: Cloudflare Tunnel +
Access** (kein eigener Server nötig, kein Port-Forwarding; TLS/Login/Rollen/Audit
inklusive). Boot-seitig gekapselt in `platform/remote/` (cloudflared-Config,
systemd-Unit, Install-Skript, `SETUP.md`). **Migrationspfad** zu Self-Host
(VPS + WireGuard + Caddy) ohne App-Änderung möglich. **Offline-first:** lokales UI
im Bootsnetz immer direkt erreichbar, unabhängig vom Tunnel. Mechanismus per
TryCloudflare-Quick-Tunnel end-to-end verifiziert (öffentliche URL → lokaler Server).

## Designprinzipien (nicht verhandelbar — Konzept §2)

1. Konfiguration statt Code  2. Datengetriebene UI  3. Quellen-Abstraktion
4. Modularität/Plugins  5. Mehrere Frontends, eine Datenbasis
