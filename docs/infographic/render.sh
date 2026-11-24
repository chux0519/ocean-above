#!/bin/sh
# Render poster.html to poster.png (2400x1410, 1.5x) and poster.pdf with local headless Chrome.
set -e
cd "$(dirname "$0")"
CH="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
nice -n 10 "$CH" --headless=new --disable-gpu --allow-file-access-from-files --hide-scrollbars \
  --force-device-scale-factor=1.5 --window-size=1600,940 --virtual-time-budget=3000 \
  --screenshot="$PWD/poster.png" "file://$PWD/poster.html" 2>/dev/null
nice -n 10 "$CH" --headless=new --disable-gpu --allow-file-access-from-files \
  --no-pdf-header-footer --virtual-time-budget=3000 \
  --print-to-pdf="$PWD/poster.pdf" "file://$PWD/poster.html" 2>/dev/null
