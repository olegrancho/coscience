#!/bin/bash
# Render dashboard routes in a real browser and save screenshots.
#
# Why this exists: the vitest suite runs the TypeScript SOURCE through Vite's
# dev transform in jsdom, against fixtures we wrote ourselves. That combination
# cannot see three whole classes of failure —
#
#   * anything that only breaks in the built, minified, chunk-split bundle;
#   * anything about how the page LOOKS (jsdom applies no CSS and computes no
#     layout, so `--dump-dom` passing proves only that nodes exist);
#   * any state our fixtures never described, which is the one that bit us:
#     every wiki fixture had pages in it, so a program with the wiki switched
#     off rendered a blank shell and no test had an opinion about it.
#
# Point it at the running server and LOOK at the output.
#
#   scripts/shoot.sh                                  # the default route set
#   scripts/shoot.sh /programs/p1/wiki /programs/p2    # specific routes
#
# Set BASE to aim elsewhere (default http://127.0.0.1:8000) and OUT to choose
# where the PNGs land.
set -u

BASE="${BASE:-http://127.0.0.1:8000}"
OUT="${OUT:-/tmp/coscience-shots}"
CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"

if [ -z "$CHROME" ]; then
  echo "no chrome/chromium on this host; install one or set CHROME" >&2
  exit 1
fi

ROUTES=("$@")
if [ ${#ROUTES[@]} -eq 0 ]; then
  # One route per state that has ever rendered differently, INCLUDING the
  # empty ones. A route set that only covers programs with data is how a blank
  # page ships.
  ROUTES=(
    /programs/wikitest/wiki
    /programs/wikitest/wiki/concepts/ivywrel-correlation
    /programs/wikitest/wiki/graph
    /programs/wikitest/wiki/lint
    /programs/p1/wiki
  )
fi

mkdir -p "$OUT"
for route in "${ROUTES[@]}"; do
  name="$(echo "$route" | sed 's#^/##; s#/#_#g')"
  [ -n "$name" ] || name=root
  timeout 60 "$CHROME" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
    --window-size="${WIDTH:-1600},${HEIGHT:-1100}" --virtual-time-budget=${BUDGET:-7000} \
    --screenshot="$OUT/$name.png" "$BASE$route" >/dev/null 2>&1
  if [ -s "$OUT/$name.png" ]; then
    echo "$OUT/$name.png  <-  $route"
  else
    echo "FAILED            <-  $route" >&2
  fi
done
