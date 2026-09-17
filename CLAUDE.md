# Co-Science Platform

LLM-researcher coordination platform. Python backend (`src/coscience/`) + React
dashboard (`frontend/`), one substrate git repo holds the content (programs,
sprints, results). Runtime is **Linux-only** (uses `/proc`, `os.killpg`,
`fcntl`); it does not run natively on Windows. More than one host may run the
full platform (backend + agent loops), not just a single production box.

## How work moves

`docs/sprint-lifecycle.md` is the authoritative sprint state machine: the states and
which actor owns each transition. Read it before explaining, changing or debugging
sprint flow — notably, **`approve` authorizes but does not schedule**; the approved pool
is the PM's queue and the PM releases from it.

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

### Remote servers (off unless a deployment turns them on)

Two environment switches control whether the platform reaches other machines. Both
default to off: the dashboard has no login, and both let the backend use its own SSH
keys on other machines.

| Switch | Turns on | Set it for |
|---|---|---|
| `COSCIENCE_ALLOW_ONBOARDING=1` | Compute → Add server: probing a server over SSH and adding it to `hosts:` in `resources.yaml` | the backend (`coscience-http`) |
| `COSCIENCE_ALLOW_REMOTE=1` | remote servers in `hosts:` take sprint grants; health checks, drain and remove | the backend **and** the dispatch loop (and the PM loop, so its COMPUTE block matches) |

Set a switch wherever the host's processes read their environment, then restart the
processes that read it. If only some of them have `COSCIENCE_ALLOW_REMOTE`, the Compute
page, the PM and actual placement disagree about which servers take work. To turn a
switch off, remove it and restart the same processes: servers stay declared in
`hosts:`, but take no new work. Each host's `local_setup_*.md` says where that host sets
them.

## Conventions

- Never commit or push without explicit approval.
- No reinstall needed unless `pyproject.toml` deps change (see rule 2).
