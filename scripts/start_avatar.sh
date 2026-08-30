#!/bin/bash
# Bring the Avatar box up: backend + both agent loops against the real
# substrate, plus the scratch fixture server on 8001.
#
# Run this as a FILE, never pasted into `bash -c` or a heredoc: the pgrep -f
# patterns below match the invoking shell's own command line when that line
# contains them, so a wrapper kills itself mid-run (exit 144, nothing
# launched). From a file the pattern never reaches a command line, and the
# [o]/[t] bracket guards cover the rest.
#
# deploy.sh does not work here — it calls "$VENV/bin/pip", and this uv venv
# has only pip3. See local_setup_avatar.md.
set -u

REPO="$HOME/sync/local-share/ai-coscience"
SUB="${COSCIENCE_SUB:-$HOME/sync/bmt-share/coscience}"
SCRATCH="$HOME/coscience-wiki-scratch"
cd "$REPO"

pgrep -f "coscience-h[t]tp" | xargs -r kill
for loop in pm dispatch; do
  pgrep -f "bin/coscience $loop .*--l[o]op" | xargs -r kill
done
sleep 2

# Backend. 0.0.0.0 so it is reachable over the LAN.
COSCIENCE_REPO="$SUB" COSCIENCE_HOST=0.0.0.0 COSCIENCE_PORT=8000 \
  PATH="$HOME/.local/bin:$PATH" \
  setsid nohup "$HOME/venvs/coscience/bin/coscience-http" \
  > "$HOME/coscience-http.log" 2>&1 </dev/null &

# Agent loops. These are what actually do the autonomous work; the server
# runs none of them.
PATH="$HOME/.local/bin:$PATH" setsid nohup "$HOME/venvs/coscience/bin/coscience" pm \
  --repo "$SUB" --loop > "$HOME/coscience-pm.log" 2>&1 </dev/null &
PATH="$HOME/.local/bin:$PATH" setsid nohup "$HOME/venvs/coscience/bin/coscience" dispatch \
  --repo "$SUB" --loop > "$HOME/coscience-dispatch.log" 2>&1 </dev/null &

# Scratch fixture server: the deliberately-broken wiki bundle used to exercise
# the lint view. Skipped silently if the scratch substrate is not present.
if [ -d "$SCRATCH" ]; then
  COSCIENCE_REPO="$SCRATCH" COSCIENCE_HOST=0.0.0.0 COSCIENCE_PORT=8001 \
    PATH="$HOME/.local/bin:$PATH" \
    setsid nohup "$HOME/venvs/coscience/bin/coscience-http" \
    > "$HOME/coscience-http-scratch.log" 2>&1 </dev/null &
fi

sleep 1
