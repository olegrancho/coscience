# Co-Science Platform

LLM-researcher coordination platform. Python backend (`src/coscience/`) + React
dashboard (`frontend/`), one substrate git repo holds the content (programs,
sprints, results). Runtime is **Linux-only** (uses `/proc`, `os.killpg`,
`fcntl`); it does not run natively on Windows — develop locally, deploy to a
Linux host.

## Two repos, don't confuse them

- **Code** — this repo. Deployed to `~/coscience` on the host.
- **Substrate** — the data (programs/sprints/results). Lives at
  `~/coscience-substrate` on the host, pointed to by `COSCIENCE_REPO`. Code
  deploys never touch it.

## Deployment

The live deployment runs on `aish-sandbox` (rbscomp.net). Code lives in
`~/coscience`; the service is started with `COSCIENCE_REPO=~/coscience-substrate`
and userspace `claude` (`~/.local/bin`) + `node` (`~/node20/bin`) on `PATH`.

**Deploy with one command** (run on the host, from the code checkout):

```bash
bash scripts/deploy.sh
```

It: `git pull` → editable `pip install` → **`npm run build` (always)** →
restart the backend → **restart the agent loops (`pm` + `dispatch`)** → print
health + version. Then hard-reload the dashboard.

**Agents are separate from the server.** `coscience-http` is only the coordination
service/dashboard — it runs no agents. The autonomous work happens in heartbeat
loops (`coscience pm --loop`, `coscience dispatch --loop`, which also drives
workers). `deploy.sh` starts/restarts them so a deploy never leaves a loop on
stale code. Set `COSCIENCE_NO_AGENTS=1` for a dashboard-only box. A `@reboot`
crontab entry re-runs `deploy.sh` so everything comes back after a reboot. Loops
are usage-gated and idle beats make no Claude call.

### The rules (why `deploy.sh` does what it does)

1. **Push to `rancho`, not `personal`.** The host clones from
   `github.com/olegrancho/coscience` (remote `rancho`). `personal` currently has
   an auth issue. From local: `git push rancho main`.
2. **ALWAYS `npm run build` on every deploy — even python-only changes.** The
   dashboard's version banner compares the SHA baked into the JS bundle against
   the backend git SHA (`/api/version`). Skip the build and it shows a false
   "server X ≠ page Y" drift warning until the bundle is rebuilt.
3. **Restart the backend to pick up python changes.** The install is editable
   (`pip install -e`), but uvicorn holds the old modules in memory — no reload.
   `deploy.sh` kills and relaunches it.
4. **Frontend is served static from `frontend/dist`** — no restart needed for a
   rebuild, but a browser hard-reload (Ctrl-Shift-R) is, to drop the cached bundle.

### Manual equivalent (if not using the script)

```bash
# local
git push rancho main
# host
cd ~/coscience && git pull --ff-only
~/venvs/coscience/bin/pip install -e ".[dev,http]"          # if deps changed
( cd frontend && PATH=$HOME/node20/bin:$PATH npm run build ) # ALWAYS
pgrep -f "coscience-h[t]tp" | xargs -r kill                  # bracket avoids self-match
COSCIENCE_REPO=~/coscience-substrate COSCIENCE_HOST=127.0.0.1 COSCIENCE_PORT=8000 \
  PATH=$HOME/.local/bin:$HOME/node20/bin:$PATH \
  nohup ~/venvs/coscience/bin/coscience-http > ~/coscience-http.log 2>&1 & disown
```

## Conventions

- Never commit or push without explicit approval.
- Editable install: python `src/` changes are live after a backend restart; no
  reinstall unless `pyproject.toml` deps change.
