# Signal K — Datenschicht

Normalisiertes Marine-Datenmodell (NMEA 2000 via canboat; später Victron Cerbo
via `signalk-venus-plugin`). Läuft als eigener Node-Prozess; Python konsumiert es.

## Setup (Dev-Box / Pi 4)
```bash
npm ci                                   # installiert signalk-server (Version aus package-lock.json)
./node_modules/.bin/signalk-server -c ./config --port 3000
```
Modell-API: `http://<host>:3000/signalk/v1/api/vessels/self`
Delta-Eingang: TCP `:8375` bzw. WS `/signalk/v1/stream`.

## Pi 4 (echter Bus)
Datenverbindung Typ **NMEA2000 / canboatjs**, Input `socketcan` Interface `can0`.
