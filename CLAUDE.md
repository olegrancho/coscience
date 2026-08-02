# Co-Science Platform

LLM-researcher coordination platform. Python backend (`src/coscience/`) + React
dashboard (`frontend/`), one substrate git repo holds the content (programs,
sprints, results). Runtime is **Linux-only** (uses `/proc`, `os.killpg`,
`fcntl`); it does not run natively on Windows. More than one host may run the
full platform (backend + agent loops), not just a single production box.

## Two repos, don't confuse them

- **Code** — this repo.
- **Substrate** — the data (programs/sprints/results). A separate git repo,
  pointed to by `COSCIENCE_REPO`. Code deploys never touch it.

## Deployment

Host, paths and git remotes are deployment-specific and live in
`local_setup.md` (untracked): @local_setup.md

**Deploy with one command:**

```bash
bash scripts/deploy.sh
```

It: `git pull` → editable `pip install` → **`npm run build` (always)** →
restart the backend → **restart the agent loops (`pm` + `dispatch`)** → print
health + version.

⚠️ `deploy.sh` assumes the production host's layout. It is not portable to
every box — a host whose venv, git remote or node install differs will need the
steps run by hand. Check that host's `local_setup_*.md` before reaching for it.

**Agents are separate from the server.** `coscience-http` is only the coordination
service/dashboard — it runs no agents. The autonomous work happens in heartbeat
loops (`coscience pm --loop`, `coscience dispatch --loop`, which also drives
workers). `deploy.sh` starts/restarts them so a deploy never leaves a loop on
stale code. Set `COSCIENCE_NO_AGENTS=1` for a dashboard-only box. Loops are
usage-gated and idle beats make no Claude call.

### The rules (why `deploy.sh` does what it does)

1. **ALWAYS `npm run build` on every deploy — even python-only changes.** The
   dashboard's version banner compares the SHA baked into the JS bundle against
   the backend git SHA (`/api/version`). Skip the build and it shows a false
   "server X ≠ page Y" drift warning until the bundle is rebuilt.
2. **Restart the backend to pick up python changes.** The install is editable
   (`pip install -e`), but uvicorn holds the old modules in memory — no reload.
   `deploy.sh` kills and relaunches it.
3. **Frontend is served static from `frontend/dist`** — no restart needed for a
   rebuild, but a browser hard-reload (Ctrl-Shift-R) is, to drop the cached bundle.

## Conventions

- Never commit or push without explicit approval.
- No reinstall needed unless `pyproject.toml` deps change (see rule 2).
