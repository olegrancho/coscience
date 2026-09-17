# O8 Escalation ("red button") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A worker agent that is stuck, or believes it broke something, stops and asks for help; the sprint is held with its lease and job intact; the PM answers by resuming with instructions, reallocating to another host or passing it to a human; a human sees human-level escalations unmistakably and answers them on the dashboard.

**Architecture:** The worker agent writes `sprints/<id>/escalate.json` and ends its turn; the dispatcher raises the same record itself when a sprint sleeps on a job whose host went quiet (O7). A new `escalation` module owns the record: it moves the sprint to a new status **`escalated`**, keeps its lease, and posts a thread (marked `kind: "escalation"`) targeting the PM — or a human when the PM already answered this sprint once. While escalated, the dispatcher keeps renewing the lease and the worker keeps watching and collecting a job but never launches the agent. Answers — from the PM's new `escalation_answers` output or from the dashboard — go through one `escalation.answer` function: `resume` (back to executing, instructions handed to the next run), `reallocate` (the dispatcher stops and collects on the old host, releases the lease and re-pins the sprint to the new host, which starts fresh), `to_human` (PM only), `stop` (human only: the sprint fails cleanly).

**Tech Stack:** Python 3 (FastAPI, pytest), React + Mantine + TanStack Query (vitest).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§8 Escalation, §10 row O8), and `docs/sprint-lifecycle.md` (the authoritative state machine this changes).

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python`, prefixed `PYTHONPATH=src` in a worktree (the project's pytest addopts already has `-q`; never add another). Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Tests never run ssh or rsync: they inject a runner.
- An escalated sprint keeps its lease; its worker slot is released; a running job keeps running and is still collected when it ends, but the agent is not relaunched until the escalation is answered.
- The PM has no tools and does no hands-on repair: it answers only with `resume`, `reallocate` or `to_human`. A sprint that escalates again after the PM resumed or reallocated it goes straight to a human.
- Every state change the PM makes is applied by Python from its JSON output and listed in the cycle's "Actions this cycle" block, including skipped answers and why.
- A human answer may be `resume`, `reallocate` or `stop`, at either level.
- Sprints that never escalate behave exactly as before; a program with no escalations keeps its PM fingerprint unchanged (no new key unless there is something to report).
- `docs/sprint-lifecycle.md` gains the `escalated` state and its transitions in the same change.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/models.py` | modify | `SprintStatus.ESCALATED`; `ProgressState.escalation`, `pm_answered`, `resume_note`, `reallocate_to`, `stop_requested` |
| `src/coscience/substrate.py` | modify | persist the new progress fields |
| `src/coscience/escalation.py` | create | read `escalate.json`, raise, answer, render the thread text |
| `src/coscience/worker.py` | modify | consume `escalate.json`; dispatcher-raised escalation on a quiet host; `run_escalated_beat`; `relocate`; resume note into the context |
| `src/coscience/dispatcher.py` | modify | escalated sprints stay eligible and are beaten by `run_escalated_beat`; never granted; reallocation executed before a beat; `_WorkerSlots.host_quiet` |
| `src/coscience/executor.py`, `src/coscience/claude_executor.py` | modify | `ExecutionContext.resume_note`; "when to escalate" instructions; resume note rendered |
| `src/coscience/pm_reasoner.py`, `src/coscience/pm_agent.py`, `src/coscience/pm_claude.py` | modify | `PMContext.escalations`; `PMCycleOutput.escalation_answers`; prompt block; parse; apply with skips |
| `src/coscience/service.py`, `src/coscience/http_api.py` | modify | sprint `escalation` field; `answer_escalation`; `attention`; routes |
| `docs/sprint-lifecycle.md` | modify | the `escalated` state and who moves it |
| `frontend/src/api.ts`, `frontend/src/sprintActions.ts`, `frontend/src/components/status.ts`, `frontend/src/styles.css`, `frontend/src/views/SprintDetail.tsx`, `frontend/src/App.tsx` | modify | status, escalation panel and answers, header count |
| `tests/test_escalation.py` | create | module, worker, dispatcher |
| `tests/test_escalation_pm.py` | create | PM context, prompt, parse, apply |
| `tests/test_escalation_api.py` | create | service and HTTP |
| `frontend/src/components/EscalationPanel.tsx`, `.test.tsx` | create | the panel and its answers |

---

### Task 1: An escalation holds the sprint

**Files:**
- Create: `src/coscience/escalation.py`, `tests/test_escalation.py`
- Modify: `src/coscience/models.py`, `src/coscience/substrate.py`, `src/coscience/worker.py`, `src/coscience/dispatcher.py`, `src/coscience/claude_executor.py`, `docs/sprint-lifecycle.md`

**Interfaces:**
- Produces:
  - `SprintStatus.ESCALATED = "escalated"`
  - `ProgressState.escalation: dict` (default `{}`; keys `by`, `at`, `what`, `tried`, `may_have_broken_something`, `needs`, `host`, `level`, `thread_id`), `pm_answered: bool = False`, `resume_note: str = ""`, `reallocate_to: str = ""`, `stop_requested: bool = False` — all persisted
  - `escalation.read_escalate_json(sprint_dir) -> dict | None`
  - `escalation.raise_escalation(substrate, sprint, progress, record: dict, now: float) -> None` — sets status, posts the thread, saves sprint and progress (the caller commits)
  - `escalation.render(record) -> str`
  - `Worker.run_escalated_beat(sprint) -> BeatOutcome`
  - `_WorkerSlots.host_quiet(host_name) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_escalation.py`:

```python
"""O8: an escalation holds the sprint with its lease and job, and never relaunches the agent."""
import json

from coscience import escalation
from coscience.models import ProgressState, Sprint, SprintStatus
from coscience.substrate import Substrate

RECORD = {"what": "gpu1 refuses SSH since 13:10; job 4121 state unknown",
          "tried": "three retries over 20 min", "may_have_broken_something": False,
          "needs": "a working host, or confirmation the job is still running"}


def _executing(substrate, sid="s1"):
    sp = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g", plan=["a"], program="p1")
    substrate.save_sprint(sp)
    substrate.save_progress(ProgressState(sprint_id=sid))
    return sp


def test_escalate_json_is_read_and_validated(tmp_path):
    (tmp_path / "escalate.json").write_text(json.dumps(RECORD))
    assert escalation.read_escalate_json(tmp_path) == {**RECORD, "host_notes": ""}
    (tmp_path / "escalate.json").write_text("{not json")
    rec = escalation.read_escalate_json(tmp_path)
    assert rec["what"].startswith("escalate.json could not be read")
    (tmp_path / "escalate.json").unlink()
    assert escalation.read_escalate_json(tmp_path) is None


def test_raising_holds_the_sprint_and_asks_the_pm(substrate):
    sp = _executing(substrate)
    progress = substrate.load_progress("s1")
    escalation.raise_escalation(substrate, sp, progress, {**RECORD, "by": "agent", "host": "gpu1"}, now=100.0)
    sp, progress = substrate.load_sprint("s1"), substrate.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED
    th = sp.threads[-1]
    assert th["target"] == "pm" and th["kind"] == "escalation" and "refuses SSH" in th["messages"][0]["text"]
    assert progress.escalation["level"] == "pm" and progress.escalation["thread_id"] == th["id"]
    assert progress.escalation["by"] == "agent" and progress.escalation["host"] == "gpu1"


def test_a_second_escalation_after_a_pm_answer_goes_to_a_human(substrate):
    sp = _executing(substrate)
    progress = substrate.load_progress("s1")
    progress.pm_answered = True
    escalation.raise_escalation(substrate, sp, progress, {**RECORD, "by": "agent", "host": ""}, now=100.0)
    sp = substrate.load_sprint("s1")
    assert sp.threads[-1]["target"] == "human"
    assert substrate.load_progress("s1").escalation["level"] == "human"


def test_the_new_progress_fields_survive_a_round_trip(substrate):
    p = ProgressState(sprint_id="s1", escalation={"level": "pm", "what": "x"}, pm_answered=True,
                      resume_note="try host b", reallocate_to="b", stop_requested=True)
    substrate.save_progress(p)
    back = substrate.load_progress("s1")
    assert (back.escalation, back.pm_answered, back.resume_note, back.reallocate_to, back.stop_requested) == \
        ({"level": "pm", "what": "x"}, True, "try host b", "b", True)
```

Then, following the existing worker tests (`tests/test_worker_detached_job.py` for the fake agent that finishes a run with files written into the sprint dir, and `tests/test_remote_jobs.py` for fake slots and a sleeping remote job), add:

1. `test_an_agent_that_writes_escalate_json_is_held_not_relaunched`: the fake agent's run writes `escalate.json` (RECORD) and exits cleanly. After the collecting beat: sprint ESCALATED, `escalate.json` gone, `progress.agent_token == ""`, the slot was released, and a further `run_escalated_beat` does not call `agent.start`.
2. `test_escalation_beats_still_collect_an_ended_job_but_wake_nobody`: an ESCALATED sprint with a remote job token whose runner reports the job gone; `run_escalated_beat` collects (rsync call seen), clears the job fields, leaves the status ESCALATED, and does not start the agent.
3. `test_a_job_sleeping_on_a_quiet_host_raises_a_dispatcher_escalation`: a sleeping remote job on `gpu1` with fake slots whose `host_quiet("gpu1")` is True; `run_sprint_beat` sets the sprint ESCALATED with `progress.escalation["by"] == "dispatcher"` and `"gpu1"` in `what`; the job token is kept.
4. `test_a_stop_requested_escalated_sprint_fails_cleanly`: ESCALATED with `stop_requested=True`; `run_escalated_beat` returns COMPLETED, the sprint is FAILED with `last_error` mentioning the escalation, and artifacts are released (spy `artifacts.release_for_sprint`).

And a dispatcher test (follow `tests/test_dispatcher.py` and the `substrate` fixture): an ESCALATED sprint holding a lease is beaten through `run_escalated_beat` (spy), its lease is renewed, reconcile does not stop it, and a leaseless ESCALATED sprint is not granted a lease.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_escalation.py`
Expected: FAIL — no `coscience.escalation`, no `ESCALATED`.

- [ ] **Step 3: Implement**

`src/coscience/models.py`: `ESCALATED = "escalated"   # stopped to ask for help; lease kept, agent not relaunched until answered` in `SprintStatus` (after HIBERNATED). `ProgressState` gains the five fields with comments. `src/coscience/substrate.py`: persist them like `collect_note` (dict via `dict(fm.get("escalation") or {})`, bools via `bool(...)`).

Create `src/coscience/escalation.py`:

```python
"""Escalation (O8): a sprint stops and asks for help.

The worker agent writes escalate.json and ends its turn, or the dispatcher raises the same
record when a sprint sleeps on a job whose host went quiet. The sprint is held in
`escalated` with its lease and job; nobody relaunches the agent until the PM or a human
answers through `answer`."""
from __future__ import annotations

import json
import time
from pathlib import Path

from coscience import threads
from coscience.models import SprintStatus, set_status

FIELDS = ("what", "tried", "may_have_broken_something", "needs", "host_notes")


def read_escalate_json(sprint_dir) -> dict | None:
    """The agent's escalation, or None. Presence is the signal: an unreadable file still
    escalates, saying so, rather than being taken for a normal exit."""
    f = Path(sprint_dir) / "escalate.json"
    if not f.is_file():
        return None
    try:
        d = json.loads(f.read_text())
        if not isinstance(d, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        return {"what": f"escalate.json could not be read ({exc}); the agent asked for help",
                "tried": "", "may_have_broken_something": False, "needs": "", "host_notes": ""}
    return {"what": str(d.get("what") or "").strip() or "(no description)",
            "tried": str(d.get("tried") or "").strip(),
            "may_have_broken_something": d.get("may_have_broken_something") is True,
            "needs": str(d.get("needs") or "").strip(),
            "host_notes": str(d.get("host_notes") or "").strip()}


def render(record: dict) -> str:
    lines = [f"**Escalation** raised by the {'platform' if record.get('by') == 'dispatcher' else 'worker agent'}"
             + (f" on {record['host']}" if record.get("host") else ""),
             "", f"**What happened:** {record.get('what', '')}"]
    if record.get("tried"):
        lines.append(f"**Tried:** {record['tried']}")
    if record.get("may_have_broken_something"):
        lines.append("**The agent believes it may have damaged the host or the program's data.**")
    if record.get("needs"):
        lines.append(f"**Needs:** {record['needs']}")
    return "\n".join(lines)


def raise_escalation(substrate, sprint, progress, record: dict, now: float | None = None) -> None:
    now = time.time() if now is None else now
    level = "human" if progress.pm_answered else "pm"
    by = str(record.get("by") or "agent")
    th = threads.new_thread(level, render(record), by, role="worker", now=now)
    th["kind"] = "escalation"
    sprint.threads.append(th)
    set_status(sprint, SprintStatus.ESCALATED, by=by, action="escalate")
    progress.escalation = {"by": by, "at": now, "host": str(record.get("host") or ""),
                           "level": level, "thread_id": th["id"],
                           **{k: record.get(k, "") for k in FIELDS}}
    substrate.save_sprint(sprint)
    substrate.save_progress(progress)
```

Check `threads.AGENT_ROLES`: use a role in it for the escalation message (`"worker"` if present; otherwise the role worker messages already use) and say which in the report.

`src/coscience/dispatcher.py`:
- `_ELIGIBLE` gains `SprintStatus.ESCALATED`.
- The grant step's `needs` excludes ESCALATED sprints (an escalated sprint is never granted; it keeps the lease it had).
- The beat loop: a sprint whose status is ESCALATED is beaten with `self.worker.run_escalated_beat(sprint)` (inside the same try/except isolation as the normal beat); EXECUTING stays `run_sprint_beat`; other statuses are skipped as today.
- `_WorkerSlots.host_quiet(host_name) -> bool`: `self.ledger.pool.closed.get(host_name) == "quiet"`.

`src/coscience/worker.py`:
0. (From Task 1's review.) The launch path unlinks `escalate.json` beside `job.json` and `finished.json`. `escalate.json` is honoured on a real failed (nonzero) exit too — checked before the failure branch, and such a run is not counted as a failure. `is_yieldable` returns False for an ESCALATED sprint, so yield never hibernates and relaunches it. When `escalate.json` and `job.json` arrive in the same turn, the job declaration is registered first (without sleeping), then the escalation is raised; an escalation wins over `finished.json`.
1. In the post-exit handling, right after a clean exit's session id is captured and **before** 3a (`job.json`): `record = escalation.read_escalate_json(sprint_dir)`; if not None: unlink `escalate.json`; `progress.agent_token = ""`; compute `held = self._slots.host(sprint.id)["name"]` and `host = progress.job_host or ("" if held == LOCAL else held)`, then `escalation.raise_escalation(self.substrate, sprint, progress, {**record, "by": "agent", "host": host}, time.time())`; `self._slots.release(sprint.id)`; commit `sprint <id>: escalated by the agent`; return `BeatOutcome.PROGRESSED`. A tracked job is left as it is.
2. In step A (sleeping on a job), before the liveness check: when `progress.job_host` and `self._slots.host_quiet(progress.job_host)` and not `progress.escalation`: raise with `{"by": "dispatcher", "host": job_host, "what": f"{job_host} has not answered the platform's checks for 30 minutes while this sprint sleeps on its job ({progress.job_note or 'job'}); the job's state is unknown", "tried": "the platform's regular host checks", "may_have_broken_something": False, "needs": "a working host, or confirmation the job is still running"}`, release the slot, commit, return PROGRESSED. Test fakes: add `host_quiet` returning False to every slot fake the tests use (grep `def ssh_for(self` in tests/).
3. `run_escalated_beat(sprint) -> BeatOutcome`:
   - `stop_requested`: `stop_sprint(sprint)` (wrapped like the dispatcher's give-up path), set FAILED with `last_error = "stopped by a human after an escalation: " + escalation.what`, `artifacts.release_for_sprint`, clear `escalation` and `stop_requested`, save, commit, return COMPLETED.
   - A tracked job that is no longer alive (same liveness call as step A): `_collect_job`, clear the job fields (`job_token`, `job_host`, `job_collect`), set `assess_reason` as step A does, save, commit `sprint <id>: job ended while escalated`.
   - Always `self._slots.release(sprint.id)` and return PROGRESSED otherwise. Never launch.

`src/coscience/claude_executor.py`: after the DETACHED-JOB PROTOCOL section, add `## When to stop and ask for help (escalate)` telling the agent to write `{sprint_dir}/escalate.json` as `{"what": …, "tried": …, "may_have_broken_something": true|false, "needs": …}` and end its turn when: a host it cannot reach, a failure it cannot explain after a reasonable attempt, a belief it damaged the host or the program's data, or an environment blocker it cannot resolve within the sprint — never for ordinary bugs in its own code. Say that the sprint is then held, its job keeps running and is collected, and it will be relaunched with an answer. Test in `tests/test_escalation.py`: the built instructions contain `escalate.json` and `may_have_broken_something`.

`docs/sprint-lifecycle.md`: add `escalated` to the States table ("stopped to ask for help; lease kept, agent not relaunched until answered"), and transitions: `executing → escalated` (worker agent via `escalate.json`; dispatcher when a sprint sleeps on a quiet host's job); `escalated → executing` (PM `escalation_answers` resume/reallocate; human answer); `escalated → failed` (human stop); note that `to_human` keeps it escalated at human level.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_escalation.py tests/test_worker_detached_job.py tests/test_remote_jobs.py tests/test_dispatcher.py tests/test_dispatcher_reconcile.py tests/test_host_health.py`
Expected: PASS

---

### Task 2: An answer resumes or moves the sprint

**Files:**
- Modify: `src/coscience/escalation.py`, `src/coscience/worker.py`, `src/coscience/dispatcher.py`, `src/coscience/executor.py`, `src/coscience/claude_executor.py`
- Test: `tests/test_escalation.py` (append)

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `escalation.answer(substrate, sprint_id, action, *, instructions="", host="", by="", pool=None, now=None) -> str` — returns "" when applied, else the reason it was not (never raises for a bad answer)
  - `ExecutionContext.resume_note: str = ""`
  - `Worker.relocate(sprint) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_escalation.py`:

```python
from coscience.resources import ResourcePool

POOL = ResourcePool.from_dict({"cpu": 4, "hosts": {"gpu1": {"ssh": "gpu1", "capacity": {"cpu": 8}},
                                                    "gpu2": {"ssh": "gpu2", "capacity": {"cpu": 8},
                                                             "programs": ["other"]},
                                                    "gpu3": {"ssh": "gpu3", "capacity": {"cpu": 8}}}})


def _escalated(substrate, level="pm", host="gpu1"):
    sp = _executing(substrate)
    progress = substrate.load_progress("s1")
    progress.host = host
    escalation.raise_escalation(substrate, sp, progress, {**RECORD, "by": "agent", "host": host}, now=1.0)
    if level == "human":
        progress = substrate.load_progress("s1")
        progress.escalation["level"] = "human"
        substrate.save_progress(progress)
    return substrate.load_sprint("s1")


def test_a_pm_resume_hands_instructions_to_the_next_run(substrate):
    _escalated(substrate)
    assert escalation.answer(substrate, "s1", "resume", instructions="the job is fine; wait for it", by="pm") == ""
    sp, progress = substrate.load_sprint("s1"), substrate.load_progress("s1")
    assert sp.status == SprintStatus.EXECUTING
    assert progress.resume_note == "the job is fine; wait for it" and progress.pm_answered
    assert progress.escalation == {}
    th = sp.threads[-1]
    assert th["messages"][-1]["role"] == "pm" and "resume" in th["messages"][-1]["text"].lower()


def test_a_pm_reallocation_must_name_another_host_the_program_may_use(substrate, every_host_placeable):
    _escalated(substrate)
    assert "same host" in escalation.answer(substrate, "s1", "reallocate", host="gpu1", by="pm", pool=POOL)
    assert "may not use" in escalation.answer(substrate, "s1", "reallocate", host="gpu2", by="pm", pool=POOL)
    assert "no host" in escalation.answer(substrate, "s1", "reallocate", host="nope", by="pm", pool=POOL)
    assert escalation.answer(substrate, "s1", "reallocate", host="gpu3", instructions="use gpu3", by="pm", pool=POOL) == ""
    progress = substrate.load_progress("s1")
    assert progress.reallocate_to == "gpu3" and substrate.load_sprint("s1").status == SprintStatus.EXECUTING


def test_to_human_keeps_it_escalated_and_retargets_the_thread(substrate):
    _escalated(substrate)
    assert escalation.answer(substrate, "s1", "to_human", instructions="needs someone on the machine", by="pm") == ""
    sp, progress = substrate.load_sprint("s1"), substrate.load_progress("s1")
    assert sp.status == SprintStatus.ESCALATED and progress.escalation["level"] == "human"
    assert sp.threads[-1]["target"] == "human"


def test_the_pm_cannot_answer_a_human_level_escalation_or_stop_a_sprint(substrate):
    _escalated(substrate, level="human")
    assert "human" in escalation.answer(substrate, "s1", "resume", by="pm")
    _ = substrate
    assert "only a human" in escalation.answer(substrate, "s1", "stop", by="pm")
    assert escalation.answer(substrate, "s1", "stop", by="oleg") == ""
    assert substrate.load_progress("s1").stop_requested


def test_an_answer_to_a_sprint_that_is_not_escalated_is_refused(substrate):
    _executing(substrate)
    assert "not escalated" in escalation.answer(substrate, "s1", "resume", by="pm")
```

Append a worker test: a sprint EXECUTING with `resume_note="use gpu3"` launches with `ctx.resume_note == "use gpu3"` (capturing agent, as in O6's `test_remote_jobs.py`) and the saved progress has `resume_note == ""`; and a `relocate` test: progress `host="gpu1"`, a remote `job_token`, `job_collect`, `reallocate_to="gpu3"`, `agent_session_id="abc"` → `relocate(sprint)` terminates on gpu1 (runner saw a kill), collects (rsync seen), then `progress.host == "gpu3"`, job fields and `agent_session_id` cleared, `reallocate_to == ""`, and `resume_note` contains `gpu3` and `starts fresh`. And a dispatcher test: an EXECUTING sprint with `reallocate_to` set and a lease on gpu1 → after `run_one_cycle`, `worker.relocate` was called (spy), its lease is released that cycle and not renewed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_escalation.py`
Expected: FAIL.

- [ ] **Step 3: Implement**

`escalation.answer`:
- Load the sprint (a missing sprint returns `"no such sprint"`) and its progress; `status != ESCALATED` → `f"{sprint_id} is not escalated (it is {status})"`.
- `by == "pm"`: first `action == "stop"` → `"only a human can stop a sprint"`; then `level == "human"` → `"this escalation is with a human; the PM does not answer it"` (this order is what the tests expect). A human (`by != "pm"`) may not use `to_human` → `"already with a human"`.
- `action` not in `resume|reallocate|to_human|stop` → `f"unknown action {action!r}"`.
- `reallocate`: `pool = pool or load_pool(substrate.repo_root)`; `target = pool.host(host)`; None → `f"no host {host!r} in the pool"`; `host == progress.host` → `"reallocate to the same host it is on; use resume instead"`; not `target.placeable` or not `target.allows(sprint.program)` → `f"program {sprint.program} may not use {host}"`; else `progress.reallocate_to = host`.
- `resume` and `reallocate`: `progress.resume_note = instructions` (for reallocate also prefixed by the move, see below is done by relocate), status → EXECUTING (`set_status(..., by=by, action=action)`), `progress.pm_answered = (by == "pm")` (a human answer clears the flag), `progress.escalation = {}`.
- `to_human`: `progress.escalation["level"] = "human"`, the thread's `target = "human"`.
- `stop`: `progress.stop_requested = True` (status stays ESCALATED; the dispatcher's escalated beat carries it out).
- Every applied answer appends a message to the escalation thread (found by `progress.escalation["thread_id"]` before it is cleared) with role `"pm"` for the PM, `"human"` for a human, text `f"{action}: {instructions}"` (or the host for reallocate), and marks that thread `status = "complete"` for resume/reallocate/stop.
- Save sprint and progress. Return `""`. The caller commits.

`ExecutionContext.resume_note` (`executor.py`, beside `collect_note`); `Worker._build_context` passes `progress.resume_note`; the launch path clears `progress.resume_note = ""` right where it clears `collect_note`. `claude_executor.py` renders a non-empty resume note near the top of the instructions as `## An answer to your escalation\n<note>` (find where `collect_note` is rendered and follow it).

`Worker.relocate(sprint)`:
- `old = progress.host`, `new = progress.reallocate_to`.
- If a job is tracked: `self._terminate(progress.job_token)`; then `self._collect_job(progress, sprint_dir)`.
- Clear the job fields (as `_reap_job` does), `progress.agent_token = ""`, `progress.agent_session_id = ""`.
- Set `progress.host = new`, `progress.reallocate_to = ""`, `progress.gpu_devices = []`.
- Prepend to `resume_note`: `f"You were moved from {old or 'this machine'} to {new}. You start fresh there with only what is in the sprint folder (including collected/)."`.
- Save and commit `sprint <id>: reallocated {old} → {new}`.

(From Task 2's review.) When `relocate`'s terminate does not succeed, it appends O7's could-not-stop line to `collect_note` before clearing the job fields, so the new run and a human learn the old job may still be running. `Service.resume_sprint` (Task 4) also resets `pm_answered`, `escalation`, `resume_note`, `reallocate_to` and `stop_requested`.

`dispatcher.py` beat loop: before beating an EXECUTING sprint, `progress = self.substrate.load_progress(sprint.id)`; if `progress.reallocate_to`: `self.worker.relocate(sprint)` (inside the isolation try), `self.ledger.release(lease.sprint_id)`, and `continue` (no renew). The next cycle grants it pinned to the new host.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_escalation.py tests/test_remote_jobs.py tests/test_worker_detached_job.py tests/test_dispatcher.py tests/test_job_instructions.py tests/test_claude_executor.py`
Expected: PASS

---

### Task 3: The PM sees escalations and answers them

**Files:**
- Modify: `src/coscience/pm_reasoner.py`, `src/coscience/pm_agent.py`, `src/coscience/pm_claude.py`
- Test: `tests/test_escalation_pm.py` (create)

**Interfaces:**
- Consumes: `escalation.answer` (Task 2).
- Produces: `PMContext.escalations: list[dict]` (`{sprint_id, title, thread_id, by, host, what, tried, may_have_broken_something, needs, hosts_allowed}`); `PMCycleOutput.escalation_answers: list[dict]` (`{sprint_id, action, instructions, host}`); actions block entries `escalations_answered` and `escalation_skipped`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_escalation_pm.py`. Read `tests/test_pm_staging.py` and `tests/test_pm_beat.py` first for how a PM context is built from a substrate, how a staged cycle is applied, and how the actions block is asserted; follow them. Tests:

1. `test_an_escalated_sprint_reaches_the_pm_as_an_escalation_not_as_feedback`: a program `p1` with an ESCALATED sprint at level pm → the built context has one entry in `escalations` with the record fields and `hosts_allowed` (names of placeable hosts the program may use, excluding the current host), and `sprint_feedback` does not contain the escalation thread.
2. `test_a_human_level_escalation_is_not_shown_to_the_pm`: level human → `escalations == []`.
3. `test_escalations_change_the_fingerprint_only_when_present`: `_context_payload` has no `escalations` key for a context without escalations, and has `[(sprint_id, thread_id)]` when there is one.
4. `test_the_prompt_lists_escalations_and_the_answer_field`: `pm_claude` prompt text for a context with one escalation contains the sprint id, "ESCALATIONS", `"escalation_answers"`, "resume", "reallocate", "to_human", and the sentence telling the PM to pass to a human when someone needs to be on a machine, data may be damaged, or it is not confident.
5. `test_escalation_answers_are_parsed`: the reasoner's JSON with `escalation_answers: [{"sprint_id": "s1", "action": "resume", "instructions": "wait"}]` parses into the output; malformed entries (missing sprint_id or action, non-dict) are dropped.
6. `test_applying_answers_moves_the_sprint_and_reports_skips`: apply a staged cycle with one valid `resume` for `s1` and one for `s9` (no such sprint) → `s1` EXECUTING with `resume_note`, and the report's actions block lists `Escalation answered: s1 (resume)` and `Escalation answer FAILED: s9 — no such sprint`.
7. `test_the_pm_cannot_answer_another_programs_escalation`: an escalated sprint of `p2` answered from `p1`'s cycle is skipped with `belongs to program p2`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_escalation_pm.py`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `PMContext.escalations: list[dict] = field(default_factory=list)` and `PMCycleOutput.escalation_answers: list[dict] = field(default_factory=list)` (`pm_reasoner.py`).
- `pm_agent` context build: while walking `program_sprints`, skip threads with `th.get("kind") == "escalation"` when building `sprint_feedback`; for each ESCALATED sprint whose `progress.escalation.get("level") == "pm"`, append the escalation dict; `hosts_allowed` = `[h.name for h in load_pool(substrate.repo_root).placeable_hosts(program_id) if h.name != progress.host]`.
- `_context_payload`: `if context.escalations: payload["escalations"] = sorted((e["sprint_id"], e["thread_id"]) for e in context.escalations)`.
- Serialise/parse `escalation_answers` beside `thread_replies` (~396/~428 and the reasoner parse ~520): keep dict entries with non-empty `sprint_id` and `action`, coercing `instructions`/`host` to str.
- `pm_claude`: an `ESCALATIONS` block (only rendered when non-empty, placed right after FAILED SPRINTS) listing each: id, title, raised by, host, what, tried, may have broken something, needs, hosts it may move to. Rules text: "A sprint is held until you answer. Answer each with one entry in escalation_answers: resume (with instructions for the agent — e.g. the job is fine, wait; retry once; skip that step), reallocate (to one of the hosts listed, with instructions), or to_human. You have no tools and cannot repair anything yourself. Choose to_human whenever the issue needs someone on a machine, the program's data may be damaged, or you are not confident it can be fixed easily." Add the HOW TO ACT line `answer an escalation -> an entry in "escalation_answers"` and the JSON shape `"escalation_answers": [{"sprint_id": "<exact id>", "action": "resume|reallocate|to_human", "instructions": "<what the agent should do>", "host": "<only for reallocate>"}]`.
- Apply (after thread replies, before release): for each answer, check the sprint exists and belongs to this program (skips as for release), then `why = escalation.answer(substrate, sid, action, instructions=…, host=…, by="pm", now=now_ts)`; `""` → `escalations_answered.append((sid, action))`, else `escalation_skipped.append({"id": sid, "why": why})`. Render in the actions block: `- Escalation answered: \`<id>\` (<action>)` and add `("escalation_skipped", "Escalation answer FAILED")` to the skip labels.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_escalation_pm.py tests/test_pm_staging.py tests/test_pm_beat.py tests/test_pm_claude.py tests/test_pm_compute.py`
Expected: PASS

---

### Task 4: A human sees and answers escalations

**Files:**
- Modify: `src/coscience/service.py`, `src/coscience/http_api.py`
- Test: `tests/test_escalation_api.py` (create); shape tests if they compare exact sprint keys

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `get_sprint()["escalation"]`: `None`, or `{level, by, at, host, what, tried, may_have_broken_something, needs, thread_id, hosts_allowed}` for an ESCALATED sprint; `list_sprints` rows gain `escalation_level` (`""` | `"pm"` | `"human"`)
  - `Service.answer_escalation(sprint_id, action, instructions="", host="", by="") -> dict` (the sprint) — raises `NotFoundError` / `ValueError(why)`
  - `Service.attention() -> dict` — `{"escalated_to_human": [{"sprint_id", "program", "title", "what", "at"}]}`
  - `POST /api/sprints/{id}/escalation` body `{action, instructions?, host?}`; `GET /api/attention`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_escalation_api.py` (reuse `_executing`/`RECORD` shapes; build the service with `Service(tmp_path)` and the `client` fixture pattern from `tests/test_http_api.py`):

1. An ESCALATED sprint's `get_sprint` carries the escalation with its level and `hosts_allowed`; a non-escalated sprint has `escalation is None`; `list_sprints` rows carry `escalation_level`.
2. `answer_escalation("s1", "resume", instructions="go", by="oleg")` → the returned sprint is `executing`; a bad action raises `ValueError`; a missing sprint raises `NotFoundError`.
3. `attention()` lists only human-level escalations.
4. HTTP: `POST /api/sprints/s1/escalation {"action": "stop"}` → 200 and `progress.stop_requested`; `{"action": "to_human"}` from a human → 422; unknown sprint → 404; `GET /api/attention` → the list.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_escalation_api.py`
Expected: FAIL.

- [ ] **Step 3: Implement**

`service.py`: `get_sprint` adds `"escalation"` (when status is ESCALATED and `progress.escalation`; `hosts_allowed` from `self.pool.placeable_hosts(sprint.program)` minus `progress.host`); `list_sprints` rows add `escalation_level` (read the progress only for ESCALATED sprints). `answer_escalation`: `why = escalation.answer(self.substrate, sprint_id, action, instructions=…, host=…, by=by or "human", pool=self.pool)`; a missing sprint → `NotFoundError`; `why` → `ValueError(why)`; else commit `sprint <id>: escalation answered (<action>) by <by>` and return `get_sprint`. Guard: `by` may never be `"pm"` from this path (use `by or "human"`, and map a literal `"pm"` username to `"human:pm"`). `attention()`: walk ESCALATED sprints, include level human.

`http_api.py`: `class EscalationAnswerIn(BaseModel): action: str; instructions: str = ""; host: str = ""`; `POST /sprints/{sprint_id}/escalation` using the current user's name as `by` (same `Depends(current_user)` shape as approve), mapping NotFound → 404 and ValueError → 422; `GET /attention`.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_escalation_api.py tests/test_http_api.py tests/test_service_sprints.py tests/test_mcp_server.py`
Expected: PASS

---

### Task 5: The dashboard shows and answers escalations

**Files:**
- Create: `frontend/src/components/EscalationPanel.tsx`, `frontend/src/components/EscalationPanel.test.tsx`
- Modify: `frontend/src/api.ts`, `frontend/src/sprintActions.ts`, `frontend/src/components/status.ts`, `frontend/src/styles.css`, `frontend/src/views/SprintDetail.tsx`, `frontend/src/App.tsx`; their tests where statuses are enumerated

**Interfaces:**
- Consumes: Task 4's fields and routes.
- Produces: `api.answerEscalation(id, body) -> Promise<Sprint>`, `api.getAttention() -> Promise<Attention>`; `EscalationPanel({ sprint, onDone })`; an `AttentionBadge` in the header.

- [ ] **Step 1: Write the failing tests**

`EscalationPanel.test.tsx` (mock `../api` as the other component tests do):

1. A pm-level escalation renders the record (what, tried, needs, "may have damaged" line when true) and the text "The PM will answer this on its next cycle", followed by "Or answer it yourself now:" and the same three answers (a human may answer at either level — Task 5's review).
2. A human-level escalation renders "Needs you" and buttons "Resume", "Move to another server", "Stop sprint".
3. Resume with instructions typed into the "Instructions for the agent" textarea calls `api.answerEscalation("s1", { action: "resume", instructions: "go" })` and then `onDone`.
4. "Move to another server" shows a select of `hosts_allowed`; choosing `gpu3` and confirming calls `answerEscalation("s1", { action: "reallocate", host: "gpu3", instructions: "" })`; with no `hosts_allowed` the button is disabled with a title saying no other server this program may use.
5. "Stop sprint" asks `window.confirm` and calls `answerEscalation("s1", { action: "stop" })`.
6. An API error is shown.

`sprintActions.test.ts`: `availableActions("escalated")` is `[]`. `status.ts` test (if one enumerates statuses): `statusVar("escalated")` is the signal colour and `SPRINT_STATE_ORDER` includes it after `executing`. An `App` or `AttentionBadge` test: with `getAttention` resolving two entries, the header shows "2 need you" linking to the first sprint; with none, nothing.

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/EscalationPanel.test.tsx src/sprintActions.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `api.ts`: `SprintEscalation` type (the Task 4 fields), `Sprint.escalation?: SprintEscalation | null`, `Attention` type, `answerEscalation` (POST JSON) and `getAttention`.
- `sprintActions.ts`: `"escalated"` in `SprintStatus`; `availableActions` returns `[]` for it (answers live in the panel).
- `status.ts` / `styles.css`: `escalated: "var(--signal)"` (warm: wants attention); `SPRINT_STATE_ORDER` inserts `"escalated"` after `"executing"`.
- `EscalationPanel.tsx`: a Card styled like the failed-sprint card (`var(--signal-line)` border, `var(--signal-weak)` background) with the record; pm level shows the waiting text; human level shows the three answers (a Textarea for instructions shared by Resume and Move; a Select of `hosts_allowed` for Move; confirm on Stop), calling the API, showing errors, then `onDone`.
- `SprintDetail.tsx`: render `<EscalationPanel sprint={s} onDone={refresh} />` at the top of the main column when `s.status === "escalated" && s.escalation`.
- `App.tsx`: an `AttentionBadge` in the header group (left of `VersionBanner`): polls `api.getAttention` every 10 s with TanStack Query; when non-empty shows a red Mantine `Badge` "`N` need you" linking to `/sprints/<first sprint_id>`.

- [ ] **Step 4: Run the tests**

Run (from `frontend/`): `npx vitest run src/components/EscalationPanel.test.tsx src/sprintActions.test.ts src/views/SprintDetail.test.tsx` (if it exists), then `npx vitest run` and `npx tsc -b`.
Expected: PASS, tsc clean.

---

## Self-review notes

- §8.1 worker-raised record and when (Task 1 instructions); §8.2 dispatcher-raised on a quiet host (Task 1, builds on O7's quiet state); §8.3 hold — status, lease kept, slot released, job collected, thread to the PM (Task 1); §8.4 answers — resume, reallocate with stop/collect/release/re-pin/fresh start, to_human, no hands-on repair, repeat goes to a human (Tasks 2–3); §8.5 human badge, count, answers (Tasks 4–5). `host_notes` in an escalation is stored on the record for O9 to fold in.
- The escalation thread uses `kind: "escalation"` so it is not also counted as ordinary sprint feedback.
- Not built: a program-list badge (the header count links to the sprint); escalation of sprints on a host drained by a human (only quiet hosts raise automatically).
