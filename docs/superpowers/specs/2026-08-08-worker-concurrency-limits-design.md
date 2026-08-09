# Worker concurrency limits, editable from the dashboard

**Date:** 2026-08-08
**Status:** approved, ready to plan

## Problem

Nothing in the platform limits how many workers run at once.

Concurrency is an emergent property of the resource ledger. `Dispatcher.run_one_cycle`
grants a lease to every eligible sprint that fits the pool, and each leased
`executing` sprint's beat launches its own detached `claude` process through
`ClaudeAgent.start` → `launch_detached`. The dispatcher only polls them, so N
leases means N Claude sessions running in genuine parallel. The ceiling is
therefore "however many sprints fit in `.coscience/resources.yaml`" — on Avatar,
`cpu: 16` against the usual `resources_required: {cpu: 1}`, so sixteen.

Three specific problems follow.

**A sprint that declares no resources is unbounded.** `select_grants` tests
`all(avail.get(k, 0.0) >= v for k, v in sprint.resources_required.items())`.
Over an empty dict that is vacuously true, so a sprint with
`resources_required: {}` — what the PM produces whenever it proposes
`resources_required: null` — always fits. Any number of them are granted at once.
This is the likely real-world concurrency, and it has no ceiling at all.

**The pool does not describe the hardware.** Avatar's file declares `gpu: 4` on a
box with one RTX 4090; four GPU sprints can hold leases simultaneously and
contend for 24GB. `local_setup_avatar.md` documents 28 CPU / 1 GPU, which matches
neither the file nor the enforcement.

**The scarce resource is not CPU.** Sixteen parallel agents drain the 5-hour
Claude window roughly sixteen times faster. `Worker._usage_ok()` refuses to
*launch* into an exhausted budget, but it does not bound parallelism, and the
pool is denominated in CPUs.

There is also no way to change any of this without editing YAML on the host and
knowing that `Service.pool` re-reads it live.

## The approach

**A worker slot is just another pooled resource.** The pool may declare a
`workers` key; when it does, every sprint costs one worker slot on top of
whatever it declares.

This is chosen over a separate scalar cap in the dispatcher because the ledger's
existing machinery — priority, aging, all-or-nothing grants, TTL, cooperative
yield, and the dashboard's capacity gauges — is already generic over resource
keys. Modelling workers as a resource inherits all of it and adds no second
scheduling concept. It also bounds `resources_required: {}` sprints as a side
effect rather than as separate work.

It is chosen over capping at agent launch (letting leases flow but refusing to
start the process) because that would leave sprints holding leases while doing
nothing: the ledger would stop describing what is actually running, and
starvation would be invisible on the Compute page.

The limits stay in `.coscience/resources.yaml` inside the substrate. `Service.pool`
already re-reads that file on every access and `dispatch_once` rebuilds its pool
from `load_pool` each cycle, so an edit takes effect without restarting anything.
Edits are committed to substrate git, giving a history of capacity changes
alongside the work they affected.

## Backend — `src/coscience/`

### 1. The effective requirement (`resources.py`)

One module-level constant and one pure function:

```python
WORKER_KEY = "workers"

def effective_requirement(required: dict[str, float], pool: ResourcePool) -> dict[str, float]:
    """What a sprint actually consumes. When the pool declares a worker cap,
    every sprint costs one worker slot on top of what it declares."""
    if WORKER_KEY not in pool.capacity:
        return dict(required)
    return {**required, WORKER_KEY: 1.0}
```

**Opt-in by design.** A substrate whose `resources.yaml` has no `workers` key
behaves exactly as it does today. Inventing a ceiling for a substrate that never
had one would silently change scheduling on deploy; instead the dashboard makes
the missing cap visible (§3). Avatar's file gets `workers` added explicitly as
part of this work (§1a).

`workers: 0` is a valid setting and means a full stop: no new grants, running
agents drain. This falls out of the arithmetic, is useful as a pause switch, and
is not special-cased.

### 1a. The values Avatar ships with

Avatar's `.coscience/resources.yaml` becomes:

```yaml
cpu: 16
gpu: 1        # was 4 — the box has one RTX 4090
workers: 1    # one worker agent at a time
```

`workers: 1` fully serialises worker agents: one sprint executes at a time, which
is the intended protection for the shared 5-hour Claude window. `gpu: 1` corrects
a pool that permitted four concurrent sprints on a single 24GB card, and makes
the "GPU sprints serialise one at a time" note in `local_setup_avatar.md` true in
enforcement rather than only in prose.

`cpu` is left at 16. With a single worker it no longer bounds concurrency at all;
it only needs to exceed the largest single sprint's request. (It disagrees with
the 28 documented in `local_setup_avatar.md`; reconciling that doc is not part of
this work.)

These are Avatar's values, written into Avatar's substrate. Other hosts and
substrates are untouched, and a substrate with no `workers` key keeps today's
behaviour.

### 2. Three call sites must agree (`scheduler.py`, `dispatcher.py`)

Every place that reads `sprint.resources_required` for a fitting decision routes
through `effective_requirement` instead:

- `SchedulerPolicy.select_grants` — both the fit test and the `avail` decrement.
- `SchedulerPolicy.select_yield_victims` — the `need`/`deficit` computation.
- `Dispatcher.run_one_cycle` — the `ledger.acquire` call.

Both `SchedulerPolicy` methods already receive the `Ledger`, so they reach the
pool through `ledger.pool`; the dispatcher uses `self.ledger.pool`.

These cannot diverge. If only `acquire` injected the slot, `select_grants` would
over-select, `acquire` would return `None` for the excess, and those sprints
would fail to be granted without `report.granted` ever reflecting it — a silent
undercount rather than a visible limit.

`Ledger` itself is unchanged: it receives amounts that are already effective, and
`can_fit`/`available`/`used` stay generic.

### 3. Editing capacity (`service.py`, `http_api.py`)

`Service.set_capacity(capacity: dict) -> dict`:

- **Validates** that keys are non-empty strings and values are finite numbers
  `>= 0`; NaN and infinity are rejected. Failure raises `ValueError`.
- **Writes** `.coscience/resources.yaml` atomically — write to a `.tmp` sibling,
  then `os.replace`, the same pattern as `Ledger.save` — so a dispatcher reading
  the file concurrently never sees a partial document.
- **Commits** via `substrate.commit("capacity updated")`.
- **Returns** a fresh `ledger_status()` so the caller re-renders from the truth
  rather than from what it hoped it wrote.

`PUT /api/capacity` on the existing auth-gated `/api` router takes
`{"capacity": {...}}`, maps `ValueError` to HTTP 422, and returns the new ledger
status.

One inherited caveat, unchanged by this work but worth stating: `Substrate.commit`
runs `git add -A`, so saving capacity also commits anything else dirty in the
substrate at that moment.

## Frontend — `frontend/src/`

### 4. The modal (`components/CapacityModal.tsx`)

A new component following the existing `NewProgramModal` / `SprintEditModal`
pattern: one row per resource (label, number input, remove control), an "add
resource" affordance, Cancel and Save.

The modal shows in-use context drawn from the `used` map already present in the
ledger payload — for example *"14 cpu in use — lowering below that lets running
work finish and blocks new grants"* — so the drain behaviour is stated at the
moment of the decision rather than discovered afterwards.

### 5. The Compute page (`views/Ledger.tsx`, `api.ts`)

`api.ts` gains `setCapacity(capacity: Record<string, number>)`.

The "capacity in use" card gains an **Edit capacity** button in its header. On
success the `["ledger"]` query is invalidated so gauges re-render.

The `workers` gauge itself needs no new code: the card already maps over
`Object.keys(l.capacity)`, so the key appears as soon as it exists.

**When the pool has no `workers` key**, the card shows a callout — *"No worker
cap — any number of agents can run at once"* — with a control that opens the
modal pre-filled with `workers`. This is how the unbounded-concurrency hole
becomes discoverable rather than silent.

## Behaviour when capacity drops below usage

Lowering a limit below what is currently leased **drains**: running agents keep
their leases and run to completion, and no new grants happen until usage falls
under the new ceiling. The gauge reads over-committed (`4 / 2`) until it clears.

This is what the current code already does when `available()` goes negative, so
it needs no new logic and never discards work in progress. The existing
cooperative-yield path is deliberately *not* triggered by a capacity edit.

## Testing

**Python.** A pool with `workers: N` grants at most N sprints per cycle; a sprint
with `resources_required: {}` consumes a slot and is therefore bounded; a pool
with no `workers` key grants exactly as it does today (regression guard for
existing substrates); capacity below current usage grants nothing and hibernates
nothing; `set_capacity` rejects negative, NaN and non-numeric values, writes
atomically, and commits.

**Frontend.** Vitest alongside `NewProgramModal.test.tsx`: the modal renders
current capacity, adds and removes a key, and Save calls the API with the edited
map; the Compute page shows the no-cap callout when `workers` is absent.

## Out of scope

Per-program fairness (one program monopolising the pool), host-local overrides
for a substrate served by differently-sized boxes, auto-detecting hardware, and
any change to the PM loop. The PM runs in its own process, takes no lease, and
stays uncapped — the cap governs worker agents only.
