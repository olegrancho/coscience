# Co-Science Platform — Phase 1b-1 (Lease-Driven Job Control / Kill-Hook) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make resource leases govern *physical* compute, not just accounting: when a sprint loses its lease through **preemption**, the dispatcher actually terminates that sprint's running detached job (its whole process group), freeing the GPU immediately. The preempted sprint stays `EXECUTING` without a lease and, when later re-granted, relaunches its interrupted step from scratch. This closes the one Important finding from the Phase 1 review and removes the "mark detached sprints `preemptible: false`" caveat as a *requirement* (it stays available as a choice for jobs that must never be interrupted).

**Architecture:** Unchanged model — filesystem + process based, single-writer dispatcher. Detached jobs are launched with `start_new_session=True` (Phase 0), so each is its own process-group leader (PGID == PID); terminating the group with SIGTERM→SIGKILL reliably stops the shell and its children. The dispatcher gains a stop step in its preemption branch; the Worker gains `stop_sprint`; the executor gains `terminate_detached`.

**Tech Stack:** Python 3.12 (venv `/home/oleg/venvs/coscience`), PyYAML, pytest. No new dependencies.

## Global Constraints

- **Interpreter:** canonical venv `/home/oleg/venvs/coscience` (CPython 3.12.13). Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`.
- **Dependencies:** runtime `pyyaml` only; dev `pytest` only. Add nothing else (`os`, `signal`, `time` are stdlib).
- **Scope:** this plan handles the **preemption** kill path only. Expiry-driven kill and dispatcher-crash reconciliation (a lease expires while its job still runs) are deliberately DEFERRED — in normal operation the dispatcher renews every cycle so leases never expire; that edge case belongs to 1b-2. Do not implement it here.
- **Resume semantics:** a killed step was never in `completed_steps` (it was tracked in `progress.detached`), so clearing it from `detached` makes the next beat relaunch it. The job restarts from scratch unless it self-checkpoints — this is intended and must be documented, not worked around.
- **Backward compatibility:** all 77 existing tests stay green; `run_one_beat`, `run_sprint_beat`, and the single-worker CLI behave unchanged.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only Phase 1b-1 is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 0 | Walking skeleton (substrate, heartbeat, checkpoint/resume, detached jobs) | DONE |
| 1 | Coordination & scheduling (pool, ledger/leases, scheduler, dispatcher, output capture) | DONE |
| **1b-1 — lease-driven job control** (this plan) | preemption terminates running jobs; victims resume on re-grant | **planned here** |
| 1b-2 — MCP/API service + containerization | network interface (submit sprints, read results, query leases); `docker compose up`; ALSO: expiry/crash-recovery lease↔job reconciliation | next |
| 1b-3 — LLM resource-manager agent | agent sets priorities/budgets over the deterministic ledger | later |
| 2 — PM + OC dashboard loop | program-manager agent, internal dashboard | later |
| 3 — Scout + sandbox hardening | capability enforcement, quarantine pipeline | later |

Design source: `docs/superpowers/specs/2026-06-23-co-science-platform-design.md` §6 (graceful preemption) + the Phase 1 review's Important finding (preemption must free physical compute).

---

## File Structure

```
src/coscience/
  executor.py     # MODIFY: add terminate_detached(pid, grace)
  worker.py       # MODIFY: add stop_sprint(sprint) -> list[str]
  dispatcher.py   # MODIFY: preemption branch stops each victim's jobs after releasing its lease
tests/
  test_terminate.py            # NEW: terminate_detached
  test_worker_stop.py          # NEW: stop_sprint
  test_dispatcher_preempt_kill.py  # NEW: preemption kills victim job
  test_integration_phase1b1.py # NEW: end-to-end preempt → kill → resume → both complete
docs/superpowers/plans/phase1-dispatch-runbook.md  # MODIFY: update the preemption caveat
```

---

## Task 1: `terminate_detached` — kill a detached job's process group

**Files:**
- Modify: `src/coscience/executor.py`
- Test: `tests/test_terminate.py`

**Interfaces:**
- Adds module-level `terminate_detached(pid: int, grace: float = 2.0) -> None`: send `SIGTERM` to the process group led by `pid`; poll up to `grace` seconds for exit; if still alive, send `SIGKILL`. A dead/unknown pid is a silent no-op (no exception). Relies on Phase 0's `launch_detached` using `start_new_session=True` (so `PGID == PID`). Reuses the existing `is_running`.

- [ ] **Step 1: Write the failing tests**

`tests/test_terminate.py`:
```python
import time

from coscience.executor import is_running, launch_detached, terminate_detached


def test_terminate_kills_running_job():
    pid = launch_detached("sleep 30")
    assert is_running(pid) is True
    terminate_detached(pid)
    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False


def test_terminate_kills_child_processes_too(tmp_path):
    # The shell spawns a child `sleep`; terminating the group must kill both.
    marker = tmp_path / "still_running.txt"
    # child writes the marker only AFTER the sleep finishes; if we kill the
    # group the sleep dies and the marker is never written.
    pid = launch_detached(f"sleep 30; echo done > {marker}")
    assert is_running(pid) is True
    terminate_detached(pid)
    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    assert not marker.exists()


def test_terminate_dead_pid_is_noop():
    terminate_detached(999999)  # no such process — must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_terminate.py -v`
Expected: FAIL with `ImportError: cannot import name 'terminate_detached'`.

- [ ] **Step 3: Implement `terminate_detached` in `executor.py`**

Add `import signal` and `import time` at the top of `executor.py` (alongside the existing `import os`, `import subprocess`). Append:
```python
def terminate_detached(pid: int, grace: float = 2.0) -> None:
    """Stop a detached job's whole process group (SIGTERM, then SIGKILL).

    launch_detached uses start_new_session=True, so the job is its own
    process-group leader (PGID == PID); signalling the group reaches the
    shell and its children. A dead/unknown pid is a no-op.
    """
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace
    while time.time() < deadline:
        if not is_running(pid):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_terminate.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/executor.py tests/test_terminate.py
git commit -m "feat: terminate_detached — kill a detached job's process group"
```

---

## Task 2: `Worker.stop_sprint` — terminate a sprint's detached jobs and arm them for relaunch

**Files:**
- Modify: `src/coscience/worker.py`
- Test: `tests/test_worker_stop.py`

**Interfaces:**
- Adds `Worker.stop_sprint(self, sprint: Sprint) -> list[str]`: for the sprint's current `progress.detached`, terminate each pid (via `terminate_detached`), clear `progress.detached` (so the steps relaunch on the next beat), save progress, commit, and return the list of stopped step ids. Does NOT touch `completed_steps`. No-op (returns `[]`) when there are no detached jobs.

- [ ] **Step 1: Write the failing tests**

`tests/test_worker_stop.py`:
```python
import time

from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.worker import Worker


def _detached_sprint(sid):
    return Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g",
                  plan=[Step("job", "detached: sleep 30")])


def test_stop_sprint_kills_and_clears(substrate):
    s = _detached_sprint("sp1")
    substrate.save_sprint(s)
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_sprint_beat(s)  # launches the detached job, records its pid
    pid = substrate.load_progress("sp1").detached["job"]
    assert is_running(pid) is True

    stopped = worker.stop_sprint(substrate.load_sprint("sp1"))
    assert stopped == ["job"]

    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False
    progress = substrate.load_progress("sp1")
    assert progress.detached == {}
    assert "job" not in progress.completed_steps  # not completed -> will relaunch


def test_stop_sprint_noop_when_no_detached(substrate):
    s = Sprint(id="sp2", status=SprintStatus.EXECUTING, goals="g",
               plan=[Step("s1", "echo hi")])
    substrate.save_sprint(s)
    assert Worker(substrate, ShellStepExecutor()).stop_sprint(s) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_worker_stop.py -v`
Expected: FAIL with `AttributeError: 'Worker' object has no attribute 'stop_sprint'`.

- [ ] **Step 3: Implement `stop_sprint` in `worker.py`**

Add `terminate_detached` to the executor import at the top of `worker.py`:
```python
from coscience.executor import StepExecutor, is_running, launch_detached, terminate_detached
```
Add the method to the `Worker` class:
```python
    def stop_sprint(self, sprint: Sprint) -> list[str]:
        """Terminate the sprint's running detached jobs and clear them so the
        steps relaunch on a later beat. Returns the stopped step ids."""
        progress = self.substrate.load_progress(sprint.id)
        stopped = list(progress.detached.keys())
        for _step_id, pid in list(progress.detached.items()):
            terminate_detached(pid)
        if stopped:
            progress.detached = {}
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: stopped detached jobs {stopped}")
        return stopped
```

- [ ] **Step 4: Run the tests + worker no-regression**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_worker_stop.py tests/test_worker.py tests/test_worker_phase1.py tests/test_detached.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/worker.py tests/test_worker_stop.py
git commit -m "feat: Worker.stop_sprint — terminate detached jobs and arm for relaunch"
```

---

## Task 3: Dispatcher preemption terminates victim jobs

**Files:**
- Modify: `src/coscience/dispatcher.py`
- Test: `tests/test_dispatcher_preempt_kill.py`

**Interfaces:**
- In `run_one_cycle`'s preemption branch, after releasing each victim's lease, call `self.worker.stop_sprint(<victim sprint>)` so the freed capacity is physically free before the candidate's job starts. No signature change to `run_one_cycle`/`CycleReport`.

- [ ] **Step 1: Write the failing test**

`tests/test_dispatcher_preempt_kill.py`:
```python
import time

from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _disp(substrate):
    return Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))


def test_preemption_kills_victim_running_job(substrate):
    # V (low priority, preemptible) holds the GPU with a long detached job.
    substrate.save_sprint(Sprint(
        id="V", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("job", "detached: sleep 30")],
        resources_required={"gpu": 1.0}, priority=0))
    disp = _disp(substrate)
    disp.run_one_cycle(now=0.0)  # V granted + launches its job
    disp.ledger.load()
    pid = substrate.load_progress("V").detached["job"]
    assert disp.ledger.lease_for("V") is not None
    assert is_running(pid) is True

    # H (high priority) arrives and needs the same GPU.
    substrate.save_sprint(Sprint(
        id="H", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "true")],
        resources_required={"gpu": 1.0}, priority=9))
    disp.run_one_cycle(now=1.0)  # H preempts V

    disp.ledger.load()
    assert disp.ledger.lease_for("H") is not None
    assert disp.ledger.lease_for("V") is None

    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False  # V's job was physically terminated
    assert substrate.load_progress("V").detached == {}  # armed for relaunch
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_dispatcher_preempt_kill.py -v`
Expected: FAIL — `is_running(pid)` stays `True` after preemption (the job is not killed) and/or `progress.detached` is not cleared.

- [ ] **Step 3: Add the stop to the preemption branch in `dispatcher.py`**

Find the preemption block in `run_one_cycle` (it releases victims then acquires the candidate):
```python
            if victims:
                for v in victims:
                    self.ledger.release(v.sprint_id)
                    report.preempted += 1
```
Replace it with:
```python
            if victims:
                for v in victims:
                    self.ledger.release(v.sprint_id)
                    self.worker.stop_sprint(self.substrate.load_sprint(v.sprint_id))
                    report.preempted += 1
```
(Leave the rest of the preemption branch — the candidate `acquire` + EXECUTING set — unchanged.)

- [ ] **Step 4: Run the test + dispatcher no-regression**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_dispatcher_preempt_kill.py tests/test_dispatcher.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/dispatcher.py tests/test_dispatcher_preempt_kill.py
git commit -m "feat: dispatcher terminates a preempted victim's running job"
```

---

## Task 4: End-to-end proof + runbook update

**Files:**
- Create: `tests/test_integration_phase1b1.py`
- Modify: `docs/superpowers/plans/phase1-dispatch-runbook.md`

**Interfaces:**
- No new production code expected. If a test fails, fix the offending module (executor/worker/dispatcher), not the test.

- [ ] **Step 1: Write the integration test**

`tests/test_integration_phase1b1.py`:
```python
import time

from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def test_preempted_sprint_is_killed_then_resumes_and_both_complete(substrate):
    # V: low priority, a short detached job. H: high priority, quick.
    substrate.save_sprint(Sprint(
        id="V", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("job", "detached: sleep 1")],
        resources_required={"gpu": 1.0}, priority=0))
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))

    disp.run_one_cycle(now=0.0)  # V launches its job
    pid_v1 = substrate.load_progress("V").detached["job"]

    substrate.save_sprint(Sprint(
        id="H", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "true")],
        resources_required={"gpu": 1.0}, priority=9))

    h_done_first = False
    t = 1
    deadline = time.time() + 30
    while not (substrate.load_sprint("V").status == SprintStatus.DONE
               and substrate.load_sprint("H").status == SprintStatus.DONE):
        assert time.time() < deadline, "sprints did not both complete"
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0  # never physically overcommit
        if not h_done_first and substrate.load_sprint("H").status == SprintStatus.DONE:
            h_done_first = substrate.load_sprint("V").status != SprintStatus.DONE
        t += 1
        time.sleep(0.1)

    assert h_done_first  # H preempted V and finished first
    # V relaunched after preemption with a fresh pid (the old one was cleared/killed)
    assert "job" in substrate.load_progress("V").completed_steps
    assert disp.ledger.all_leases() == []  # everything released at the end
```

- [ ] **Step 2: Run the integration test**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_integration_phase1b1.py -v`
Expected: 1 passed. If it fails, fix the offending module, not the test.

- [ ] **Step 3: Run the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass (77 prior + the Phase 1b-1 additions).

- [ ] **Step 4: Update the runbook caveat**

In `docs/superpowers/plans/phase1-dispatch-runbook.md`, replace the existing "Important caveat" section (the block beginning `## Important caveat: leases are advisory; preemption does NOT kill running jobs`) with:
```markdown
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

Not yet handled (planned for 1b-2): a lease that **expires** (e.g. after a
dispatcher outage longer than the TTL) does not currently kill its job —
expiry-driven reconciliation lands with the service layer.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration_phase1b1.py docs/superpowers/plans/phase1-dispatch-runbook.md
git commit -m "test: phase 1b-1 end-to-end preempt-kill-resume + runbook update"
```

---

## Self-Review

**Spec coverage:**
- Terminate a detached job's process group → Task 1 ✓
- Worker stops a sprint's jobs and arms relaunch → Task 2 ✓
- Dispatcher kills victims on preemption (frees physical compute) → Task 3 ✓
- End-to-end preempt → kill → resume → both complete, with per-cycle no-overcommit invariant → Task 4 ✓
- Runbook reflects the new guarantee + resume-from-scratch caveat → Task 4 ✓
- Explicitly deferred (documented): expiry/crash-recovery lease↔job reconciliation (1b-2); MCP/API service (1b-2); LLM RM agent (1b-3).

**Placeholder scan:** every code step has complete runnable code; every test step has real assertions. ✓

**Type consistency:** `terminate_detached(pid: int, grace: float = 2.0) -> None`, `Worker.stop_sprint(sprint: Sprint) -> list[str]`, dispatcher preemption calls `stop_sprint(load_sprint(v.sprint_id))`. Reuses existing `is_running`, `launch_detached`, `Ledger`, `SchedulerPolicy`. ✓

**Known 1b-1 simplifications (intentional):**
- Preemption kill only; expiry-driven kill deferred to 1b-2.
- A killed step relaunches from scratch (no mid-job checkpointing; the job owns its own checkpointing).
- Process-group kill assumes jobs don't daemonize into a fresh session of their own (which would escape the group); documented edge case.
