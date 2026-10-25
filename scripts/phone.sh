#!/bin/sh
# Starts the app plus a temporary public https link (a Cloudflare quick tunnel), so phones can reach it from any
# network, and points sign-in links and QR codes at that link. The link changes every run; anyone with it can open
# the app while this runs. Stop with Ctrl-C.
set -e
cd "$(dirname "$0")/.."
LOG=$(mktemp)
cloudflared tunnel --url http://localhost:8000 --no-autoupdate > "$LOG" 2>&1 &
TUNNEL=$!
trap 'kill $TUNNEL 2>/dev/null; rm -f "$LOG"' EXIT INT TERM
URL=""
for _ in $(seq 1 40); do
  URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$LOG" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
if [ -z "$URL" ]; then
  echo "Couldn't start the tunnel:"; cat "$LOG"; exit 1
fi
echo ""
echo "  On your phone, open:  $URL"
echo "  Demo (on a laptop):   $URL/demo"
echo ""
BASE_URL="$URL" .venv/bin/uvicorn passiton.app:app --host 127.0.0.1 --port 8000
