#!/bin/sh
# Renders the made-up sample photos from src/items.html with headless Chrome, then saves them as JPEGs.
set -e
cd "$(dirname "$0")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
for v in ti84 ti84-checkin ti84-other goggles; do
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 --window-size=1200,900 \
    --virtual-time-budget=2000 --screenshot="/tmp/passiton-$v.png" "file://$PWD/src/items.html#$v" >/dev/null 2>&1
  ffmpeg -y -v error -i "/tmp/passiton-$v.png" -q:v 3 "$v.jpg"
  rm -f "/tmp/passiton-$v.png"
  echo "wrote $v.jpg"
done
