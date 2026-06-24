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

## Important caveat: leases are advisory; preemption does NOT kill running jobs
The dispatcher only manages lease *accounting* — it never kills work. If a
sprint holding a `detached:` job (e.g. `detached: python train.py`) is
preempted, its lease is released and reassigned, but **the detached process
keeps running and keeps using the real GPU**. For that window the physical
resource is over-subscribed even though the ledger looks correct. Until a
kill-hook lands (Phase 1b), the safe convention is:

> **Mark any sprint that launches a long detached job `preemptible: false`.**

This makes the capacity guarantee hold for *physical* resources, not just
lease accounting. Short, checkpoint-and-resume steps are fine to leave
preemptible.
