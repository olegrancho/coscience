#!/usr/bin/env bash
# Deploy the coscience backend + dashboard on the host that runs the service.
#
# ALWAYS rebuilds the frontend. The dashboard's version banner compares the SHA
# baked into the JS bundle against the backend's git SHA, so even a python-only
# change needs a fresh `npm run build` or the UI shows a false "drift" warning.
#
# Env overrides (defaults match the aish-sandbox / rbscomp deployment):
#   COSCIENCE_CODE   code checkout            (default: ~/coscience)
#   COSCIENCE_REPO   substrate / programs dir (default: ~/coscience-substrate)
#   COSCIENCE_VENV   virtualenv               (default: ~/venvs/coscience)
#   COSCIENCE_HOST   bind host                (default: 127.0.0.1)
#   COSCIENCE_PORT   bind port                (default: 8000)
set -euo pipefail

REPO="${COSCIENCE_CODE:-$HOME/coscience}"
SUBSTRATE="${COSCIENCE_REPO:-$HOME/coscience-substrate}"
VENV="${COSCIENCE_VENV:-$HOME/venvs/coscience}"
HOST="${COSCIENCE_HOST:-127.0.0.1}"
PORT="${COSCIENCE_PORT:-8000}"
# claude (agents) + node (build) live in userspace on this box:
export PATH="$HOME/.local/bin:$HOME/node20/bin:$PATH"

cd "$REPO"
echo "==> pull"
git pull --ff-only

echo "==> python deps (editable install; no-op if unchanged)"
"$VENV/bin/pip" install -q -e ".[dev,http]"

echo "==> build frontend (ALWAYS — clears the version-drift banner)"
( cd frontend && { [ -d node_modules ] || npm ci --silent; }; npm run build )

echo "==> restart backend"
# Bracket in the pattern so pgrep never matches this script's own command line.
pgrep -f "coscience-h[t]tp" | xargs -r kill || true
sleep 1
COSCIENCE_REPO="$SUBSTRATE" COSCIENCE_HOST="$HOST" COSCIENCE_PORT="$PORT" \
  nohup "$VENV/bin/coscience-http" > "$HOME/coscience-http.log" 2>&1 </dev/null &
disown
sleep 3

echo "==> health:  $(curl -s "$HOST:$PORT/api/health")"
echo "==> version: $(curl -s "$HOST:$PORT/api/version")"
echo "Done. Hard-reload the dashboard (Ctrl-Shift-R) to drop the cached bundle."
