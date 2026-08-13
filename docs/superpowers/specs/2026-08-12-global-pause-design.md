# Global pause/resume

A Pause button on the Compute page that stops the platform spending Claude quota,
and a Resume that starts it again.

## Why

Today the only brake is setting the `workers` capacity to 0. That is a poor stop
button on three counts:

- It does not stop the **PM**. With `workers: 0` the PM keeps beating, reasoning and
  proposing at roughly $1 a call into a queue nothing can drain.
- It **overwrites a real setting**. Resuming means remembering what the cap used to
  be; the substrate history shows `workers 1 -> 0` and back, mixed in with genuine
  capacity edits.
- It says nothing about **what is still running**, so there is no way to see the
  platform wind down.

## Semantics

**Paused means no new Claude session starts, from any path** — the PM loop, worker
launches, Replan, Compress/Brainstorm, and PM chat. Resume is the only way out.

That deliberately closes the human escape hatch added in `83089be` (a forced Replan
when the PM is stuck): while paused, a stuck PM needs Resume first. Resume *is* the
escape hatch.

**A session already running is left alone — it drains.** Pause blocks *starting*, not
a session that is already going, so an in-flight worker keeps calling Claude until it
finishes normally. Nothing is killed and no scratchpad work is lost. Spend therefore
stops within one agent run rather than instantly; the Compute page shows the count
winding down so that wait is visible.

A sprint that stops between agent runs while paused stays put: the worker's own
mid-sprint gate (`worker.py:455`) refuses the resume, and it picks up after Resume.

## State

`.coscience/paused` — a marker file. It exists (paused) or it does not (running).
No content, no schema.

It lives in the substrate beside `leases.json` and `queue.json` so all three
processes — `coscience-http`, the PM loop and the dispatch loop — read the same flag,
and so it survives a restart. `git add -A` picks it up like the rest of `.coscience/`,
making pause and resume auditable substrate commits.

## Enforcement

`claude_usage_ok()` is already the single choke point for Claude spend:

| Caller | Site |
|---|---|
| PM loop beats | `cli.py:228` |
| Worker agent launches | `worker.py:197` |
| Replan | `service.py:587` |
| Compress / Brainstorm | `service.py:601` |
| PM chat | `service.py:809` |

So the gate goes there. `claude_usage_ok` gains an optional `repo_root`; when it is
given and the marker exists, the function returns False without shelling out to the
usage script. `repo_root=None` (the default) skips the pause check, which keeps every
existing caller and test working. All five sites already hold the substrate.

The **dispatcher needs one narrow guard, on its grant step only.**

Its *launches* do go through the Worker gate, so no agent starts while paused. But the
grant step runs earlier and consults no gate at all: it acquires a lease and flips a
QUEUED sprint to EXECUTING regardless. Left alone, a paused platform would keep taking
leases for sprints that never start — and since the Compute page reports
`leases.length` as "still finishing", that count would *grow* while paused, which is
the opposite of what the button promises.

So `run_one_cycle` skips its grant loop when paused. Only that loop: reaping finished
agents, releasing leases, reconciling and beating leased sprints all keep running,
which is what lets work in flight drain to completion. A dispatcher frozen outright
would strand the very leases it was meant to collect.

The guard reads the marker directly rather than going through `claude_usage_ok` — this
is about the pause specifically. Routing it through the usage gate would also stop
grants whenever usage merely ran high, which is a behaviour change nobody asked for.

New module `src/coscience/pause.py`, two functions:

```python
def is_paused(repo_root) -> bool
def set_paused(repo_root, paused: bool) -> None
```

It is its own file to keep the import graph acyclic — `cli`, `worker` and `service`
all need it, and it depends on nothing.

## API

- `GET /api/ledger` — carries a `paused` boolean. The Compute page already polls this
  every 10s, so pause state needs no second poll.
- `PUT /api/pause` — `{"paused": true|false}`, returns the new state.

## Loop feedback

The PM loop checks pause *before* beating and logs

```
paused by human — Resume in Compute
```

skipping the cycle entirely. Distinct from the existing `paused — Claude usage
exhausted`, so the log says which kind of stop it was.

## Frontend

`Ledger.tsx` (the Compute page). The header row gets a Pause / Resume button.

While paused, it reads `Paused — N still finishing`, from the `l.leases.length` the
page already has, dropping to `Paused — nothing running` at zero. Making the drain
visible is the part `workers: 0` never gave.

## Tests

- `is_paused` is False when the marker is absent; `set_paused` round-trips.
- `claude_usage_ok` returns False when paused, whatever the usage percentage says.
- A paused PM loop makes no reasoner call and logs the human-pause line.
- A paused worker does not launch an agent.
- A paused dispatcher still reaps and releases, so a drain completes.
- `PUT /api/pause` flips the state and `GET /api/ledger` reports it.
- `Ledger.tsx` renders Pause/Resume and calls the API.

## Out of scope

No auto-resume timer, no per-program pause, no scheduled pause windows.
