#!/bin/sh
# Render vibe-process.html to vibe-process.pdf (one 1600x900 page per slide) and, with --png, per-slide previews in png/.
set -e
cd "$(dirname "$0")"
CH="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
nice -n 10 "$CH" --headless=new --disable-gpu --allow-file-access-from-files \
  --no-pdf-header-footer --virtual-time-budget=3000 \
  --print-to-pdf="$PWD/vibe-process.pdf" "file://$PWD/vibe-process.html" 2>/dev/null
if [ "$1" = "--png" ]; then
  mkdir -p png
  n=$(grep -c '<section class="slide' vibe-process.html)
  i=1
  while [ "$i" -le "$n" ]; do
    nice -n 10 "$CH" --headless=new --disable-gpu --allow-file-access-from-files --hide-scrollbars \
      --window-size=1600,900 --virtual-time-budget=3000 \
      --screenshot="$PWD/png/slide-$(printf %02d "$i").png" "file://$PWD/vibe-process.html?s=$i" 2>/dev/null
    i=$((i + 1))
  done
fi
