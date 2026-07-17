# Hibernation: cooperative preemption that never kills in-flight work

**Status:** design, approved in brainstorming 2026-07-17.

## Problem

The dispatcher preempts a running sprint by hard-killing it (`worker.stop_sprint`
→ SIGTERM, 2s, SIGKILL of the whole process group) to reclaim its resource lease
for a higher-priority sprint. This destroys two things the platform otherwise
promises to protect:

1. **Detached jobs.** A sprint sleeping on a declared `job.json` (e.g. a 9-hour
   PageRank) still holds a lease. Preempting it kills the job. With no per-job
   checkpoint, the next run restarts from zero.
2. **Active agent turns.** An agent mid-turn is killed with 2 seconds' grace and
   no warning; it only "learns" it was cut off by reading its scratchpad on the
   next relaunch.

Combined with priority **aging** (a waiting sprint's effective priority rises over
time), near-equal sibling sprints on a contended box trade the cores back and
forth — classic scheduler thrash — and each swap destroys in-flight work. On
`aish-sandbox` (16 cores, no GPU) this meant a real sprint
(`kg-biomed-c0-extend-cohorts-generalization`) could never land its heavy
comparisons; it completed only via a lighter proxy.

## Goal

Replace immediate, destructive preemption with **cooperative hibernation**: the
scheduler never hard-kills an active agent turn or a live detached job. A sprint
yields resources only at a *safe point*, by transitioning to a new `hibernated`
state that preserves its scratchpad and waits for capacity to free naturally.

## Core rules (the whole design in five statements)

1. **A sprint is never hard-killed for preemption.** The SIGTERM/SIGKILL
   preemption path is removed.
2. **The only yield point** is when a sprint is *idle and holds no live job*:
   its agent is not running AND it has no live detached job (no `job_token`, or
   the tracked job has ended). This is exactly the moment a sleeping sprint's job
   has finished and it would otherwise wake to assess, and also the brief gap
   between agent turns for a jobless sprint.
3. **At a yield point, the dispatcher decides wake-vs-hibernate freshly** (no
   stored flag): if a higher-priority sprint is starved and this sprint's
   resources would help cover the deficit → **hibernate** it; otherwise → let it
   proceed (wake/assess/relaunch as today).
4. **Hibernating** = kill any lingering job processes, do not launch/relaunch the
   agent, release the lease, set status `executing → hibernated`. The scratchpad
   (and any job-output files) are left on disk.
5. **Waking** = a hibernated sprint competes for grants in the same pool as
   queued sprints by `effective_priority` (it keeps aging). It is granted **only
   from free capacity — never by preempting anyone**. On grant:
   `hibernated → executing`, and the agent relaunches, resuming from its
   scratchpad.

Consequences (accepted): preemption no longer frees resources immediately — a
starved higher-priority sprint waits until a running sprint reaches a safe yield
point (its job ends, bounded by the existing `job_max_seconds` watchdog, or its
turn ends). In-flight compute always completes. A long non-preemptible job can
make higher-priority work wait; that is the intended trade-off.

## New state: `HIBERNATED`

Add `SprintStatus.HIBERNATED = "hibernated"`.

- **Meaning:** the sprint has run (has a scratchpad, possibly partial results),
  was asked to yield at a safe point, and is now parked with no lease and no
  compute, waiting for capacity.
- **Transitions:**
  - `executing → hibernated` — dispatcher yields it at a safe point (rule 4).
  - `hibernated → executing` — dispatcher grants it a lease from free capacity
    (rule 5).
- It is **eligible for grants** (like `queued`) but is **not beaten** (like
  `queued`/`parked`, it runs no agent while in this state).
- It is **not** a human-facing lifecycle knob (unlike `parked`); the dispatcher
  owns entry and exit. v1 exposes it read-only on the dashboard (badge only). A
  human `cancel`/force-wake can be added later.

## The `preemptible` flag, repurposed

The old flag meant "may be hard-killed to reclaim resources." Immediate
preemption is gone, so it is repurposed: **`preemptible = false` means the sprint
is never hibernated** — a higher-priority sprint must wait for it to fully
complete. Default stays `true` (eligible to hibernate at safe points). The
existing model field, HTTP field, and UI are kept; only the meaning changes.

## Dispatcher algorithm (`run_one_cycle`)

Eligible set becomes `QUEUED ∪ EXECUTING ∪ HIBERNATED`.

1. **Expire** stale leases (unchanged).
2. **Grants.** Leaseless eligible sprints (queued, hibernated, and re-adopted
   leaseless executing) → `select_grants` from *free capacity* → `acquire`. On
   grant set `QUEUED → EXECUTING` and `HIBERNATED → EXECUTING`. (Grants never
   preempt; they only use free capacity — unchanged behavior, extended to
   hibernated.)
3. **Yield (replaces the preemption round).** After grants, if any **queued**
   candidate is still starved (leaseless — hibernated sprints do NOT trigger
   yields; see below), take the top one by `effective_priority` and compute its
   resource deficit. Choose **yield victims** among *leased*
   sprints that are all of:
   - at a **safe yield point** — `not worker.agent_running(id)` AND no live job
     (`progress.job_token` empty or `not job_alive(job_token)`);
   - **`preemptible`** (lease flag);
   - **lower lease priority** than the candidate's effective priority;
   selecting lowest-priority-first until the deficit is covered (mirror the old
   `select_preemptions` selection, but only over safe-point + preemptible
   leases). For each victim: `ledger.release(id)` + `worker.hibernate_sprint(id)`
   (reap job, `executing → hibernated`). Do **not** grant the candidate this
   cycle; the freed capacity is granted on the next cycle's step 2 (keeps the
   cycle simple and idempotent).
   - If no safe-point victims can cover the deficit, do nothing — the candidate
     keeps waiting (a live job or active agent must finish first).
4. **Reconcile.** Leaseless **executing** sprints with a running agent or tracked
   job → `stop_sprint` (kill stray physical use so it matches the ledger).
   Hibernated sprints are skipped (they are intentionally leaseless with no
   agent/job).
5. **Beat** each leased executing sprint → `run_sprint_beat` → renew lease;
   `COMPLETED` → release + drop from queue.

`waiting` counts leaseless eligible (queued + hibernated).

### Why yield-then-grant-next-cycle (not same cycle)

Hibernating a victim reaps its job asynchronously; deferring the grant to the
next cycle avoids granting into capacity that is mid-teardown and keeps
`run_one_cycle` a straight-line, testable pass. Dispatch beats are ~5s, so the
latency cost is negligible.

## Worker changes

- **`hibernate_sprint(sprint) -> None`:** stop any (idle-case) agent token, reap
  any tracked job (`_reap_job` — kills lingering procs, clears job fields),
  `set_status(HIBERNATED)`, clear `agent_token`, save + commit. Does NOT touch
  the scratchpad.
- **Preserve assess context across hibernation.** If the sprint is hibernated at
  the exact point a finished job was about to be assessed
  (`progress.assess_reason`/`job_out` set), keep those fields so the resumed
  launch is still an assess run and re-reads the job output. (If empty, resume is
  a normal scratchpad relaunch.)
- **`is_yieldable(sprint_id) -> bool`** helper (or inline in dispatcher):
  `not agent_running AND not job_alive`.
- `run_sprint_beat` step A (wake/assess/sleep) is otherwise unchanged: when the
  dispatcher does NOT yield a sprint, its beat proceeds exactly as today.
- Waking a hibernated sprint needs no special worker code: once the dispatcher
  sets it `EXECUTING` with a lease, the next beat hits step 1 (no agent, no job)
  and launches a fresh agent that resumes from the scratchpad.

## Scheduler changes

- `select_grants` unchanged (already free-capacity-only; will now also receive
  hibernated candidates and their queue timestamps).
- `select_preemptions` is **removed** (or repurposed as the safe-point victim
  selector used by the dispatcher's yield step). The yield selector differs only
  by filtering the eligible-victim set to safe-point sprints; the
  lowest-priority-first, cover-the-deficit loop is identical.
- `effective_priority` unchanged; hibernated sprints keep their `queue.json`
  timestamp so they keep aging (do not reset on hibernate).

## Data model / persistence

- `models.py`: `SprintStatus.HIBERNATED`.
- `substrate.py`: no schema change — status round-trips through existing
  frontmatter. `assess_reason`/`job_out` already persist in progress.
- `queue.json`: hibernated sprints stay in the queue-age map (present in
  `eligible`), so they keep aging. Removed only on completion.

## Frontend

- `status.ts` / `styles.css`: `--st-hibernated` color + label (e.g. a muted
  blue/violet, "Hibernated").
- `StatusBadge`: render the new status.
- `sprintActions.ts`: no human actions for `hibernated` in v1 (empty action
  list) — dispatcher-managed. (Reserve `cancel`/`wake` for a later iteration.)
- Lineage graph / program views already key off status strings; add hibernated
  to any status filters so it is not treated as executing.

## Edge cases

- **Active agent, no job, contended:** almost always in a turn (state 1) → not a
  safe point → not yielded → higher-priority waits until the turn ends. Matches
  the approved "do not preempt active agents" choice.
- **Long non-preemptible job starving a higher-priority sprint:** accepted;
  bounded by `job_max_seconds`.
- **Hibernated sprint never gets capacity:** it waits, aging up, until free
  capacity ≥ its `resources_required`. If the pool can never fit it (e.g.
  requirements exceed capacity), it waits forever — same as a too-large `queued`
  sprint today (surfaced as `waiting`).
- **Dispatcher outage while a sprint sleeps on a job:** lease may expire; on
  restart, reconcile finds a leaseless executing sprint with a live job and kills
  it (unchanged pre-existing behavior; out of scope here).
- **Hibernate then immediately re-grant thrash:** prevented on two fronts.
  Waking is free-capacity-only, and — critically — **a hibernated sprint never
  triggers a yield** (only starved *queued* sprints do). So a just-hibernated
  sprint cannot cause the sprint that displaced it to hibernate in turn; there is
  no A↔B ping-pong. Hibernated sprints only ever re-enter via free capacity, in
  priority order.
- **`finished.json` contract interaction:** unchanged. A woken sprint that
  finishes writes `finished.json` → done; an ambiguous exit still resumes per the
  completion contract. Hibernation only intercepts at safe points when there is
  demand.

## Testing

Backend (pytest, Linux — run on the dev instance):

- **Non-preemption:** a leased sprint with a live job is never yielded even when a
  higher-priority sprint is starved (dispatcher leaves it; candidate waits).
- **Non-preemption:** a leased sprint with a running agent (no job) is never
  yielded.
- **Yield at safe point:** sleeping sprint whose job has ended + higher-priority
  starved candidate → sprint goes `hibernated`, lease released, job reaped, agent
  not relaunched; next cycle grants the candidate.
- **No yield without demand:** at a safe point with no starved higher-priority
  candidate, the sprint wakes/assesses normally (not hibernated).
- **`preemptible=false`:** never hibernated even at a safe point with demand.
- **Wake from free capacity:** a hibernated sprint is granted when capacity frees
  and it is top priority; `hibernated → executing`; relaunches from scratchpad.
- **Wake never preempts:** a hibernated sprint is NOT granted by preempting
  another sprint (only free capacity).
- **Aging:** a long-hibernated sprint climbs and is granted ahead of a
  lower-aged queued sprint.
- **Assess context preserved:** hibernating at a job-done point keeps
  `assess_reason`/`job_out` so the resumed run assesses.
- **Round-trip:** `hibernated` status persists through substrate save/load.

Frontend (vitest): `hibernated` renders a badge and exposes no actions.

## Out of scope (possible later iterations)

- Human force-wake / cancel of a hibernated sprint.
- Graceful "request yield" signaling so an active agent can checkpoint and yield
  mid-turn (v1 never interrupts active turns at all).
- Per-job checkpointing guidance in worker instructions (belt-and-suspenders).
