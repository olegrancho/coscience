# Detached-Job Harness (Option 2) — Design

**Status:** Design approved (pending spec review)
**Date:** 2026-07-14
**Motivated by:** issue #3 — a worker `nohup`-launched a job then exited, so the sprint was marked `done` prematurely and the real results were orphaned. Option 1 (forbid background-and-exit) shipped as a stopgap; this replaces the blanket ban with a **sanctioned, tracked** detached-job path.

## 1. Summary

Let a worker hand a genuinely long job to the platform and be brought back to finish it, without the sprint being finalized while the job runs. The worker launches the job detached and **declares it** via a `job.json` file; the platform keeps the sprint `executing` (lease held), waits until the job ends or a declared wake time, then **re-launches a fresh worker to assess** the output and complete (or iterate). A duration cap + watchdog bound runaway jobs, and the dashboard shows a "sleeping" state with a manual **Wake now** button.

Undeclared background-and-exit stays forbidden (Option 1 rule) — the *only* sanctioned way to background is this protocol.

## 2. Agent contract — `job.json`

For a job too long to finish in-session, the worker launches it detached and writes `<sprint_dir>/job.json`, then ends its turn:

```json
{
  "pid": 12345,
  "cmd": "python harness/resplit_variance.py",
  "out_file": "resplit_variance.out",
  "expected_seconds": 5400,
  "wake_after_seconds": 6000,
  "max_seconds": 14400,
  "note": "7-seed resplit training"
}
```

- `pid` — the detached process id (`nohup … & echo $!`).
- `out_file` — where the job writes output (relative to `sprint_dir`), for the assess run to read.
- `expected_seconds` — the agent's estimate of how long the job takes.
- `wake_after_seconds` — when the platform should bring the agent back to check (typically ≈ expected + margin).
- `max_seconds` — hard watchdog cap; the platform clamps it to a ceiling (constant `JOB_MAX_SECONDS`, default 7 days).
- `note` — human-readable description (shown in the UI).

The worker's `build_instructions` gains a section describing this protocol and requiring `expected_seconds`/`wake_after_seconds`/`max_seconds` be filled before exiting. Option 1's rule 4 is amended: "do not background-and-exit **unless** you use the detached-job protocol below."

## 3. Data model

`ProgressState` (persisted in `progress.md`) gains a detached-job block:
- `job_token: str` — `"<pid>:<starttime>"` reuse-guard token (computed by the platform from the declared pid via the existing `executor.process_token`); `""` = no job.
- `job_out: str` — output path.
- `job_started_at: float` — when the platform first recorded the job.
- `job_expected_seconds: float`
- `job_next_wake: float` — absolute timestamp; when `now ≥ job_next_wake` the platform wakes the agent.
- `job_max_seconds: float` — clamped cap.
- `job_note: str`.

## 4. Lifecycle (`worker.run_sprint_beat`)

The beat gains a detached-job branch. States:

1. **No agent, no job** → launch the worker agent (unchanged).
2. **Agent running** → leave it (+ existing feedback harvest) (unchanged).
3. **Agent ended, exit ok, `job.json` present & declares a live/just-launched job** →
   record the job into progress (`job_token = process_token(pid)`, `job_started_at = now`,
   `job_next_wake = now + wake_after_seconds`, clamp `max_seconds`), clear `agent_token`,
   **keep sprint `executing`**, consume `job.json`. Do **not** mark done; the agent's
   premature final message is ignored (the real result comes from the assess run).
4. **Agent ended, exit ok, no job** → mark `done` (unchanged).
5. **No agent, `job_token` set** (the "sleeping" state) — a **cheap check** (no agent launched):
   - job process dead → **re-launch a fresh worker to assess** (clear `job_token`, launch agent
     with assess context).
   - `now - job_started_at > job_max_seconds` (**watchdog**) → `terminate_detached(job_token)`,
     then re-launch to assess with a "job hit its time cap" note.
   - `now ≥ job_next_wake` → **wake**: re-launch the worker to check (assess context noting the
     job may still be running); the agent decides to finish, intervene, or re-sleep (declare a new
     `job.json` → back to state 3).
   - else → wait (nothing launched; lease held via `executing`).

Failure/interrupted handling for agent runs is unchanged. The assess/wake run is an ordinary worker
agent session; when it exits ok with no new `job.json`, the sprint completes normally (state 4).

**Assess context:** `ExecutionContext` gains job fields so `build_instructions` can render a section:
"A previous run launched a detached job (`<note>`). It has finished / was terminated at its cap / may
still be running; its output is at `<out_file>`. Read it, judge whether the sprint goal is met, and
either produce the final result, launch a follow-up job, or (if still running and healthy) re-declare
`job.json` with a new wake time."

## 5. Duration cap + watchdog

- `max_seconds` from `job.json`, clamped to `JOB_MAX_SECONDS` (default 7 days; overridable via env).
- A job exceeding `max_seconds` is terminated and sent to assess (the agent sees partial output).
- A job dead without usable output falls to the assess agent's judgment → recover or fail (the
  existing `MAX_AGENT_FAILURES` cap applies to repeated failed assess runs).

## 6. Lease & preemption

- The sprint stays `executing` for the whole job, so the dispatcher keeps/renews its lease — the job
  holds its resource across the agent gaps (the design §4 intent).
- `stop_sprint` and dispatcher preemption also `terminate_detached(job_token)` (and clear it), so a
  preempted/stopped sprint's detached job is actually killed, not orphaned.

## 7. Assess-launch frequency

Each dispatch beat only does the cheap check in state 5; a **full agent session launches only** on
job-death, wake-time, watchdog, or a manual wake. Steady state ≈ **one assess launch per job** (agent
sets `wake_after_seconds ≈ job end`). An agent that wants to babysit sets a short `wake_after_seconds`
and re-sleeps → one launch per check-in (its choice). No per-beat launch storms.

## 8. UI

- `get_sprint` returns `agent_state` ∈ {`running`, `sleeping`, `idle`} and, when sleeping, a `job`
  block: `{note, out_file, started_at, expected_seconds, next_wake, max_seconds}`.
- SprintDetail shows a **"Agent sleeping — waiting on: `<note>`"** panel with expected finish + next
  scheduled wake.
- A **Wake now** button → `POST /sprints/{id}/wake` sets `job_next_wake = now`; the next dispatch beat
  wakes the worker. Auth-gated like other mutations.

## 9. Testing

- `ProgressState` job fields round-trip through `progress.md`.
- Beat: agent exits ok + `job.json` with a live pid → sprint stays `executing`, job recorded, **not** done, `job.json` consumed.
- Beat (sleeping): job dead → re-launch to assess; assess exits ok, no job → `done`.
- Beat (sleeping): `now ≥ next_wake`, job alive → wake re-launch.
- Beat (sleeping): over `max_seconds` → `terminate_detached` called + assess.
- `wake_sprint` sets `next_wake=now` → next beat wakes.
- `stop_sprint` terminates the job token.
- `agent_state` reported correctly (running/sleeping/idle) via `get_sprint`.

## 10. Non-goals (v1)

- No up-front duration in `resources_required` (agent declares at runtime, platform-clamped).
- No cross-host jobs (job runs on the same host as the dispatcher).
- Assess is a full agent session, not an auto-parse of `out_file`.
- No change to the Option 1 stopgap wording beyond amending rule 4 to allow the declared protocol.
