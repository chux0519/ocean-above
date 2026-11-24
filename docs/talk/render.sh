#!/bin/sh
# Render vibe-process.html (1920x1080 stage) to vibe-process.pdf, one slide per page.
# --png also writes per-slide previews (final state of every animation) to png/.
set -e
cd "$(dirname "$0")"
CH="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
nice -n 10 "$CH" --headless=new --disable-gpu --allow-file-access-from-files \
  --no-pdf-header-footer --virtual-time-budget=8000 \
  --print-to-pdf="$PWD/vibe-process.pdf" "file://$PWD/vibe-process.html?static" 2>/dev/null
if [ "$1" = "--png" ]; then
  mkdir -p png
  n=$(grep -c '<section class="slide' vibe-process.html)
  i=1
  while [ "$i" -le "$n" ]; do
    nice -n 10 "$CH" --headless=new --disable-gpu --allow-file-access-from-files --hide-scrollbars \
      --window-size=1920,1080 --virtual-time-budget=8000 \
      --screenshot="$PWD/png/slide-$(printf %02d "$i").png" "file://$PWD/vibe-process.html?static&s=$i" 2>/dev/null
    i=$((i + 1))
  done
fi
