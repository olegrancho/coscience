# Co-Science Platform — Phase 1b-2a (Restart Reconciliation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the invariant **"a sprint without a lease must not have a running detached job."** This makes the dispatcher self-healing across restarts: if its process was down longer than the lease TTL, leases expire but the orphaned detached jobs keep running. On the next cycle the dispatcher **re-adopts** the still-running jobs that fit (the normal grant path re-grants their lease) and **kills** the ones that no longer fit (so physical resource use is reconciled back down to declared capacity). Closes the expiry/crash-recovery gap deferred from 1b-1.

**Architecture:** Unchanged — filesystem + process based, single-writer dispatcher. The only change is one reconciliation step added to `run_one_cycle`, placed after grants and preemption (so re-adoption via the grant path takes precedence) and before the beat loop. Re-adoption needs no new code — a leaseless `EXECUTING` sprint is already a grant candidate, so `select_grants` re-acquires it when capacity allows. The new code is only the kill-the-rest reconciliation.

**Tech Stack:** Python 3.12 (venv `/home/oleg/venvs/coscience`), PyYAML, pytest. No new dependencies.

## Global Constraints

- **Interpreter:** canonical venv `/home/oleg/venvs/coscience`. Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`.
- **Dependencies:** runtime `pyyaml` only; dev `pytest` only. Add nothing.
- **Single-writer dispatcher** remains the only ledger writer.
- **The reconciliation rule runs after grants + preemption, before the beat loop** — order matters: re-adoption (a grant) must win over the kill for any orphan that still fits.
- **A killed orphan relaunches from scratch on a later re-grant** (its step was never in `completed_steps`); same semantics as 1b-1 preemption.
- **Backward compatibility:** all existing tests stay green; Phase 0/1/1b-1 behavior unchanged for the normal (no-expiry) path — in steady operation leases are renewed every cycle, so no sprint is ever leaseless-with-a-running-job and the new rule is a no-op.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only 1b-2a is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 0 / 1 / 1b-1 | skeleton; scheduling; lease-driven job kill | DONE |
| **1b-2a — restart reconciliation** (this plan) | no-lease ⇒ no-running-job invariant; re-adopt-or-kill orphans | **planned here** |
| 1b-2b — MCP/API service + containerization | network interface over the substrate; transport/dependency decision pending | next |
| 1b-3 — LLM resource-manager agent | agent sets priorities/budgets over the ledger | later |

Carried from the 1b-1 review and addressed here: expiry/crash-recovery lease↔job reconciliation. **Still deferred (to 1b-2b):** the PID-reuse guard — `terminate_detached`/`is_running` trust that a stored PID still maps to *our* job; storing a process-identity token (e.g. `/proc/<pid>` start-time) to verify before signalling lands with the service layer.

---

## File Structure

```
src/coscience/dispatcher.py   # MODIFY: CycleReport.reconciled; import is_running; reconciliation step in run_one_cycle
tests/
  test_dispatcher_reconcile.py     # NEW: orphan killed when it no longer fits
  test_integration_phase1b2a.py    # NEW: orphan re-adopted when capacity free, then completes
docs/superpowers/plans/phase1-dispatch-runbook.md  # MODIFY: document restart reconciliation
```

---

## Task 1: Reconciliation — kill leaseless running jobs

**Files:**
- Modify: `src/coscience/dispatcher.py`
- Test: `tests/test_dispatcher_reconcile.py`

**Interfaces:**
- `CycleReport` gains `reconciled: int = 0`.
- `run_one_cycle` gains a reconciliation step **after** the preemption block and **before** the beat loop: for each eligible sprint that still has no lease, load its progress; if any pid in `progress.detached` is still running, call `self.worker.stop_sprint(sprint)` and increment `report.reconciled`. Include `report.reconciled` in the commit-gating condition.
- `dispatcher.py` imports `is_running` from `coscience.executor`.

- [ ] **Step 1: Write the failing test**

`tests/test_dispatcher_reconcile.py`:
```python
import time

from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.worker import Worker


def test_leaseless_running_job_is_reconciled_killed(substrate):
    # Orphan state (as produced by a dispatcher outage that expired the lease):
    # an EXECUTING sprint with a running detached job but NO lease in the ledger.
    orph = Sprint(id="ORPH", status=SprintStatus.EXECUTING, goals="g",
                  plan=[Step("job", "detached: sleep 30")],
                  resources_required={"gpu": 1.0}, priority=0)
    substrate.save_sprint(orph)
    Worker(substrate, ShellStepExecutor()).run_sprint_beat(orph)  # launches the job; no lease
    pid = substrate.load_progress("ORPH").detached["job"]
    assert is_running(pid) is True

    # A higher-priority sprint claims the single GPU, so ORPH cannot be re-adopted.
    substrate.save_sprint(Sprint(id="HOG", status=SprintStatus.APPROVED, goals="g",
                                 plan=[Step("s1", "true")],
                                 resources_required={"gpu": 1.0}, priority=9))
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))
    report = disp.run_one_cycle(now=0.0)

    disp.ledger.load()
    assert disp.ledger.lease_for("HOG") is not None
    assert disp.ledger.lease_for("ORPH") is None
    assert report.reconciled == 1

    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False  # orphaned job reconciled (killed)
    assert substrate.load_progress("ORPH").detached == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_dispatcher_reconcile.py -v`
Expected: FAIL — `report` has no attribute `reconciled` (and/or the orphan job is never killed).

- [ ] **Step 3: Implement the reconciliation step in `dispatcher.py`**

Add `is_running` to the executor import at the top of `dispatcher.py`. The file currently has no executor import (only `StepExecutor` via the type hint is imported); add:
```python
from coscience.executor import StepExecutor, is_running
```
(replace the existing `from coscience.executor import StepExecutor` line with the line above).

Add the field to `CycleReport`:
```python
@dataclass
class CycleReport:
    granted: int = 0
    preempted: int = 0
    beaten: int = 0
    completed: int = 0
    waiting: int = 0
    reconciled: int = 0
```

In `run_one_cycle`, immediately AFTER the preemption block and BEFORE the `# --- run one beat per leased, executing sprint ---` loop, insert:
```python
        # --- reconcile: no lease => no running job ---
        # Grants/preemption above re-adopted any leaseless running sprint that
        # still fits; kill the detached jobs of those that remain leaseless
        # (e.g. expired across a dispatcher outage) so physical use matches the
        # ledger.
        for sprint in eligible:
            if self.ledger.lease_for(sprint.id) is None:
                progress = self.substrate.load_progress(sprint.id)
                if any(is_running(pid) for pid in progress.detached.values()):
                    self.worker.stop_sprint(sprint)
                    report.reconciled += 1
```

Update the commit-gating line to include `reconciled`:
```python
        if report.granted or report.completed or report.preempted or report.reconciled:
            self.substrate.commit("dispatch cycle")
```

- [ ] **Step 4: Run the test + dispatcher no-regression**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_dispatcher_reconcile.py tests/test_dispatcher.py tests/test_dispatcher_preempt_kill.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/dispatcher.py tests/test_dispatcher_reconcile.py
git commit -m "feat: dispatcher reconciles leaseless running jobs (kill orphans that no longer fit)"
```

---

## Task 2: Re-adoption + integration + runbook

**Files:**
- Create: `tests/test_integration_phase1b2a.py`
- Modify: `docs/superpowers/plans/phase1-dispatch-runbook.md`

**Interfaces:**
- No new production code. Re-adoption is the existing grant path acting on a leaseless `EXECUTING` sprint; these tests prove it (and that reconciliation does NOT kill a job that gets re-adopted). If a test fails, fix the dispatcher, not the test.

- [ ] **Step 1: Write the tests**

`tests/test_integration_phase1b2a.py`:
```python
import time

from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.worker import Worker


def _orphan_with_running_job(substrate, sid, run="detached: sleep 30"):
    s = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g",
               plan=[Step("job", run)], resources_required={"gpu": 1.0}, priority=0)
    substrate.save_sprint(s)
    Worker(substrate, ShellStepExecutor()).run_sprint_beat(s)  # launch job, no lease
    return substrate.load_progress(sid).detached["job"]


def test_orphan_is_readopted_when_capacity_free(substrate):
    pid = _orphan_with_running_job(substrate, "ORPH")
    assert is_running(pid) is True
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))
    report = disp.run_one_cycle(now=0.0)

    disp.ledger.load()
    assert disp.ledger.lease_for("ORPH") is not None  # re-adopted, not killed
    assert report.reconciled == 0
    assert is_running(pid) is True                     # the same job keeps running
    assert substrate.load_progress("ORPH").detached["job"] == pid


def test_readopted_orphan_runs_to_completion(substrate):
    pid = _orphan_with_running_job(substrate, "ORPH", run="detached: sleep 1")
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))
    t = 0
    deadline = time.time() + 20
    while substrate.load_sprint("ORPH").status != SprintStatus.DONE:
        assert time.time() < deadline, "re-adopted orphan never completed"
        disp.run_one_cycle(now=float(t))
        t += 1
        time.sleep(0.1)
    assert "job" in substrate.load_progress("ORPH").completed_steps
    disp.ledger.load()
    assert disp.ledger.all_leases() == []
```

- [ ] **Step 2: Run the tests**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_integration_phase1b2a.py -v`
Expected: 2 passed. If a test fails, fix the dispatcher (re-adoption order vs reconcile-kill), not the test.

- [ ] **Step 3: Run the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass (prior tests + the 1b-2a additions).

- [ ] **Step 4: Update the runbook**

In `docs/superpowers/plans/phase1-dispatch-runbook.md`, replace the final "Not yet handled (planned for 1b-2)…" paragraph (inside the "## Preemption stops running jobs (since 1b-1)" section) with:
```markdown
**Restart reconciliation (since 1b-2a):** if the dispatcher is down longer than
a lease's TTL, leases expire but the detached jobs keep running. On the next
cycle the dispatcher re-adopts the still-running jobs that fit (re-granting
their lease) and kills the ones that no longer fit, so physical use is
reconciled back to declared capacity. In steady operation leases are renewed
every cycle, so this never triggers.

Still deferred (1b-2b): a PID-reuse guard — termination trusts that a stored
PID still maps to this job; storing a process-identity token to verify before
signalling lands with the service layer.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration_phase1b2a.py docs/superpowers/plans/phase1-dispatch-runbook.md
git commit -m "test: phase 1b-2a re-adoption + restart-reconciliation integration + runbook"
```

---

## Self-Review

**Spec coverage:**
- "no lease ⇒ no running job" reconciliation kill → Task 1 ✓
- Re-adoption (grant path) takes precedence over kill → ordering in Task 1, proven in Task 2 ✓
- Orphan re-adopted runs to completion → Task 2 ✓
- Runbook documents restart reconciliation; PID-reuse still deferred → Task 2 ✓
- Steady-state no-op (renewed leases never leave a sprint leaseless-with-job) → Global Constraints + preserved by no-regression runs ✓

**Placeholder scan:** complete code in every step; real assertions; no TBD. ✓

**Type consistency:** `CycleReport(..., reconciled: int = 0)`; `run_one_cycle` reconcile loop uses `is_running` + `self.worker.stop_sprint`; reuses existing `Worker.stop_sprint`, `Ledger.lease_for`, `Substrate.load_progress`. ✓

**Known 1b-2a simplifications (intentional):**
- Reconciliation is per-cycle and synchronous; orphan kill is best-effort via `stop_sprint`.
- PID-reuse guard deferred to 1b-2b (documented).
- Re-adoption relies on the existing grant path; no special re-adoption code beyond running grants before the reconcile-kill.
