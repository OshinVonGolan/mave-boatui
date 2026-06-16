#!/usr/bin/env bash
# Installiert cloudflared passend zur Architektur (arm64 auf Pi 4 64-bit) nach
# /usr/local/bin/cloudflared. Idempotent. Als root/sudo ausfuehren.
set -euo pipefail

ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "$ARCH" in
  arm64|aarch64) FILE=cloudflared-linux-arm64 ;;
  armhf|armv7l)  FILE=cloudflared-linux-arm   ;;
  amd64|x86_64)  FILE=cloudflared-linux-amd64 ;;
  *) echo "Unbekannte Architektur: $ARCH" >&2; exit 1 ;;
esac

URL="https://github.com/cloudflare/cloudflared/releases/latest/download/${FILE}"
echo "Lade $FILE ..."
curl -fsSL "$URL" -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
/usr/local/bin/cloudflared --version
echo "OK. Naechste Schritte: siehe platform/remote/SETUP.md"
