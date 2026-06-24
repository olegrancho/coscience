# Phase 1 — Dispatcher Runbook

Runs several sprints concurrently across a declared resource pool, never
exceeding capacity.

## 1. Declare the pool
Create `<repo>/.coscience/resources.yaml`, e.g. for the avatar+superskomputer fleet:
```
resources:
  gpu_24gb: 1       # avatar RTX 4090
  gpu_11gb: 1       # superskomputer RTX 2080 Ti
  cpu: 80           # combined cores
  disk_gb: 1000
  runtime_slots: 4  # max concurrent agent sessions
```

## 2. Declare each sprint's needs
In a sprint's `sprint.md` frontmatter:
```
status: approved
priority: 5
preemptible: false
resources_required:
  gpu_24gb: 1
  runtime_slots: 1
plan:
  - id: train
    run: "detached: python train.py"
```

## 3. Run the dispatcher
```
'/home/oleg/venvs/coscience/bin/coscience' dispatch --repo <repo> --loop --interval 5 --executor claude
```
Each cycle prints `granted=.. preempted=.. beaten=.. completed=.. waiting=..`.

## What it guarantees
- Never exceeds declared capacity (all-or-nothing leases).
- Higher `priority` runs first; long-waiting sprints age up.
- A higher-priority sprint can preempt a lower-priority **preemptible** holder.
- A stuck sprint's lease expires (TTL) and is reclaimed automatically.
- `.coscience/leases.json` / `queue.json` show live allocation.

## Preemption stops running jobs (since 1b-1)
When a higher-priority sprint preempts a lower-priority **preemptible** holder,
the dispatcher now terminates the victim's running detached job (its whole
process group, SIGTERM then SIGKILL), so the physical resource is genuinely
freed before the new job starts — the capacity guarantee holds for real GPU
use, not just lease accounting.

The preempted sprint stays `EXECUTING` (without a lease) and **relaunches its
interrupted step from scratch** when it is later re-granted a lease. The job
restarts unless it checkpoints its own progress, so:

> Trap SIGTERM in long jobs to checkpoint before exit, **or** mark a sprint
> `preemptible: false` if its work must never be interrupted (it will then be
> scheduled as a hard hold and never preempted).

**Restart reconciliation (since 1b-2a):** if the dispatcher is down longer than
a lease's TTL, leases expire but the detached jobs keep running. On the next
cycle the dispatcher re-adopts the still-running jobs that fit (re-granting
their lease) and kills the ones that no longer fit, so physical use is
reconciled back to declared capacity. In steady operation leases are renewed
every cycle, so this never triggers.

Still deferred (1b-2b): a PID-reuse guard — termination trusts that a stored
PID still maps to this job; storing a process-identity token to verify before
signalling lands with the service layer.
