#!/bin/sh
# Startet den Server mit einem Wegwerf-Datenverzeichnis und laesst den
# E2E-Test dagegen laufen. Danach ist alles wieder weg — der Test darf keine
# Spuren im echten Bestand hinterlassen.
set -e
cd "$(dirname "$0")/.."
export PORT="${PORT:-8199}"
export MAVE_GERAET_TOKEN="test-geraet-$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export MAVE_PASSWORT="test-notzugang-$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
VERZ=$(mktemp -d)
export MAVE_DATEN="$VERZ"
export MAVE_STATISCH="$PWD/static"

./venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port "$PORT" \
    --log-level warning &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$VERZ"' EXIT

# Warten bis er antwortet, statt blind zu schlafen
for i in $(seq 40); do
    if curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT/api/zugang"; then break; fi
    sleep 0.25
done

./venv/bin/python server/test_konten_e2e.py
