# Remote-Zugriff — Cloudflare Tunnel + Access

**Entscheidung:** Cloudflare Tunnel (Transport, CGNAT-tauglich, kein Server mieten,
kein Port-Forwarding) + Cloudflare Access (Login, Rollen, Audit). Boot-seitig
gekapselt in `platform/remote/` → später ohne App-Änderung auf Self-Host
(WireGuard + Caddy) migrierbar. **Offline-first bleibt:** das lokale UI im Bootsnetz
ist immer direkt erreichbar, unabhängig vom Tunnel.

## Was DU einmalig brauchst
1. Eine **Domain bei Cloudflare** (Free-Plan reicht, ~10 €/Jahr für die Domain).
2. Cloudflare-Login (für `cloudflared login` bzw. einen API-Token).

Mehr nicht — kein VPS, keine feste IP. Ein Cloudflare-Account bedient alle Boote.

## Pro Boot einrichten (automatisierbar, sobald Domain da ist)
```bash
sudo bash platform/remote/install-cloudflared.sh      # cloudflared installieren (arm64)
cloudflared login                                     # einmal pro Cloudflare-Account
cloudflared tunnel create mave-01                      # erzeugt UUID + Credentials-JSON
cloudflared tunnel route dns mave-01 mave-01.<domain>  # Subdomain -> Tunnel
sudo cp platform/remote/cloudflared/config.example.yml /etc/cloudflared/config.yml
#   -> UUID + hostname + Credentials-Pfad in config.yml eintragen
sudo cp platform/remote/mave-remote.service /etc/systemd/system/
sudo systemctl enable --now mave-remote.service
```
Ergebnis: `https://mave-01.<domain>` zeigt die Boot-UI — von überall, hinter CGNAT.

## Zugriff absichern (Cloudflare Access — Rollen)
In Cloudflare → Zero Trust → Access → Applications, eine App auf
`mave-01.<domain>`:
- **Policy „Eigner"**: E-Mail des Eigners → voller Zugriff.
- **Policy „Werft-Diagnose"**: Werft-E-Mails/Gruppe → Zugriff (zeitlich/rollenbeschränkt
  über separate Pfade/Policies möglich), Audit-Log inklusive.
Multi-Mandant: pro Boot eigene Subdomain + eigene Access-App/Policies → saubere
Trennung; jedes Boot = eigene Identität.

## Migrationspfad (später, Datenhoheit)
Self-Host: ein VPS mit WireGuard (Transport) + Caddy (TLS-Reverse-Proxy
`boatX.<domain>`) + eigener Auth. Nur dieser `remote/`-Ordner ändert sich, App bleibt.
