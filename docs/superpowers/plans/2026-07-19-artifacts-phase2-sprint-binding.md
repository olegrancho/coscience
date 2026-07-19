# Artifacts Phase 2 — Sprint ↔ Artifact Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a sprint bind existing artifacts and/or create new ones; make the
artifact a capacity-1 resource the dispatcher serializes execution on; write a
new artifact version when the sprint terminates.

**Architecture:** Phase 1 built the store (`coscience.artifacts`). Phase 2 wires
it into the sprint lifecycle: two new `Sprint` fields; sprint-oriented helpers in
`artifacts.py` (acquire/release/blocked for a sprint's artifact set); a dispatcher
grant gate that skips artifact-blocked sprints and acquires their locks on grant;
worker release-with-version-cut at the DONE/FAILED terminals; and the agent's
instructions naming each artifact's `work/` directory so a real worker writes
deliverables there.

**Tech Stack:** Python 3.11 dataclasses; `coscience.frontmatter_io`; the existing
`Dispatcher`/`Worker`/`Ledger`/`SchedulerPolicy`; `pytest` with the `substrate`
fixture and `FakeAgent` from `tests/conftest.py`.

## Global Constraints

- **Runtime is Linux-only** (`fcntl`); tests run on the Linux dev host, not Windows.
- **Artifact = capacity-1 resource** via the Phase-1 lock record; artifacts are
  dynamic (NOT `resources.yaml` entries). The dispatcher's grant decision
  consults the lock.
- **A sprint bound to artifacts may sit in `queued` freely; it only gets an
  execute lease when all its artifacts are free** (unlocked or already held by
  itself). Acquire is atomic all-or-none across the sprint's artifacts.
- **Lock acquired at grant (lease), released at the terminal transition only**
  (DONE / FAILED). Hibernation and reconcile-stop KEEP the lock (work preserved
  for resume). Re-granting a hibernated sprint re-acquires idempotently.
- **Release cuts a new version from `work/`** (Phase-1 dedup applies: no version
  if `work/` is unchanged). No hard delete.
- `artifacts_create` entries are dicts `{"aid": str, "title": str, "kind": str}`;
  `kind` ∈ {md, data, figure, page}. Create-targets are instantiated + locked at
  grant.
- All substrate writes go through Python; the reasoner/agent never writes the store.

**Base commit for this phase:** `33f0426` (Phase 1 complete, `feat/artifacts`).

---

### Task 1: Sprint model fields + substrate round-trip

**Files:**
- Modify: `src/coscience/models.py` (append two fields to the `Sprint` dataclass, after `edges` at line 64)
- Modify: `src/coscience/substrate.py` (`load_sprint` ~line 34-60, `save_sprint` ~line 62-111)
- Test: `tests/test_artifact_sprint_fields.py`

**Interfaces:**
- Consumes: existing `Sprint`, `Substrate.load_sprint/save_sprint`.
- Produces: `Sprint.artifacts_bound: list[str]`, `Sprint.artifacts_create: list[dict]`; both round-trip through `sprint.md` frontmatter (omitted when empty).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_sprint_fields.py
from coscience.models import Sprint, SprintStatus


def test_sprint_artifact_fields_default_empty():
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g")
    assert s.artifacts_bound == []
    assert s.artifacts_create == []


def test_sprint_artifact_fields_roundtrip(substrate):
    s = Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["do"],
               program="prog",
               artifacts_bound=["manuscript", "umap"],
               artifacts_create=[{"aid": "table1", "title": "Table 1", "kind": "data"}])
    substrate.save_sprint(s)
    b = substrate.load_sprint("s1")
    assert b.artifacts_bound == ["manuscript", "umap"]
    assert b.artifacts_create == [{"aid": "table1", "title": "Table 1", "kind": "data"}]


def test_sprint_without_artifacts_omits_keys(substrate):
    substrate.save_sprint(Sprint(id="s2", status=SprintStatus.PROPOSED, goals="g", plan=["x"]))
    text = (substrate.sprint_dir("s2") / "sprint.md").read_text()
    assert "artifacts_bound" not in text
    assert "artifacts_create" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_sprint_fields.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'artifacts_bound'`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/models.py`, append to the `Sprint` dataclass (immediately after the `edges` field at line 64):

```python
    artifacts_bound: list[str] = field(default_factory=list)   # existing artifact ids this sprint edits (locked as a resource)
    artifacts_create: list[dict] = field(default_factory=list)  # new artifacts to produce: [{aid, title, kind}]
```

In `src/coscience/substrate.py` `load_sprint`, add these two arguments to the
`Sprint(...)` constructor (after `edges=...` on line 59):

```python
            artifacts_bound=[str(a) for a in fm.get("artifacts_bound", [])],
            artifacts_create=[dict(c) for c in fm.get("artifacts_create", [])],
```

In `src/coscience/substrate.py` `save_sprint`, add after the `if sprint.edges:`
block (after line 109):

```python
        if sprint.artifacts_bound:
            fm["artifacts_bound"] = list(sprint.artifacts_bound)
        if sprint.artifacts_create:
            fm["artifacts_create"] = [dict(c) for c in sprint.artifacts_create]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_sprint_fields.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_artifact_sprint_fields.py
git commit -m "feat(artifacts): sprint artifacts_bound / artifacts_create fields"
```

---

### Task 2: Sprint-oriented artifact helpers

**Files:**
- Modify: `src/coscience/artifacts.py` (append)
- Test: `tests/test_artifact_sprint_helpers.py`

**Interfaces:**
- Consumes: `create_artifact`, `acquire_lock`, `release_lock` (Phase 1); `Substrate.artifact_dir/load_artifact`.
- Produces (sprint-agnostic — duck-typed on `.program`, `.artifacts_bound`, `.artifacts_create`, `.id`):
  - `sprint_aids(sprint) -> list[str]` — bound ids + each create-spec's `aid`.
  - `acquire_for_sprint(substrate, sprint, now) -> bool` — instantiate any not-yet-existing create-targets, then `acquire_lock` all the sprint's aids under holder `("sprint", sprint.id)`. Returns True (no-op success) when the sprint has no program or no artifacts.
  - `release_for_sprint(substrate, sprint, now) -> list` — `release_lock` the sprint's aids (created_by = sprint.id); returns per-aid version ids. No-op ([]) when no program/artifacts.
  - `sprint_blocked(substrate, sprint) -> bool` — True if any EXISTING bound artifact is locked by a *different* holder (create-targets never block).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_sprint_helpers.py
from coscience import artifacts
from coscience.models import Sprint, SprintStatus


def _sprint(**kw):
    return Sprint(id=kw.pop("id", "s1"), status=SprintStatus.EXECUTING,
                  goals="g", program=kw.pop("program", "p"), **kw)


def test_sprint_aids_bound_plus_create():
    s = _sprint(artifacts_bound=["a"], artifacts_create=[{"aid": "b", "kind": "md"}])
    assert artifacts.sprint_aids(s) == ["a", "b"]


def test_acquire_for_sprint_creates_targets_and_locks(substrate):
    artifacts.create_artifact(substrate, "p", "a", "A", "md")
    s = _sprint(artifacts_bound=["a"], artifacts_create=[{"aid": "b", "title": "B", "kind": "figure"}])
    ok = artifacts.acquire_for_sprint(substrate, s, now=1.0)
    assert ok is True
    # create-target instantiated with its kind, and both locked to this sprint
    b = substrate.load_artifact("p", "b")
    assert b.kind == "figure"
    assert substrate.load_artifact("p", "a").lock["holder_id"] == "s1"
    assert b.lock["holder_id"] == "s1"
    assert (substrate.artifact_dir("p", "a") / "work").is_dir()


def test_acquire_for_sprint_blocked_returns_false_and_locks_none(substrate):
    artifacts.create_artifact(substrate, "p", "a", "A", "md")
    artifacts.acquire_lock(substrate, "p", ["a"], "chat", "chat:x", now=0.0)   # busy
    s = _sprint(artifacts_bound=["a"], artifacts_create=[{"aid": "b", "kind": "md"}])
    ok = artifacts.acquire_for_sprint(substrate, s, now=1.0)
    assert ok is False
    # 'a' stays with the chat; 'b' was instantiated but NOT locked (all-or-none)
    assert substrate.load_artifact("p", "a").lock["holder_id"] == "chat:x"
    assert substrate.load_artifact("p", "b").lock == {}


def test_release_for_sprint_cuts_versions_and_unlocks(substrate):
    artifacts.create_artifact(substrate, "p", "a", "A", "md")
    s = _sprint(artifacts_bound=["a"])
    artifacts.acquire_for_sprint(substrate, s, now=1.0)
    (substrate.artifact_dir("p", "a") / "work" / "c.md").write_text("done")
    vids = artifacts.release_for_sprint(substrate, s, now=2.0)
    assert vids == ["v1"]
    a = substrate.load_artifact("p", "a")
    assert a.lock == {}
    assert a.current == "v1"


def test_sprint_blocked_detects_other_holder(substrate):
    artifacts.create_artifact(substrate, "p", "a", "A", "md")
    s = _sprint(artifacts_bound=["a"])
    assert artifacts.sprint_blocked(substrate, s) is False
    artifacts.acquire_lock(substrate, "p", ["a"], "chat", "chat:x", now=0.0)
    assert artifacts.sprint_blocked(substrate, s) is True
    # held by itself -> not blocked
    s2 = _sprint(id="chat:x")   # holder id matches
    assert artifacts.sprint_blocked(substrate, s2) is False


def test_helpers_noop_without_program():
    s = _sprint(program=None, artifacts_bound=["a"])
    assert artifacts.sprint_blocked(None, s) is False
    # acquire/release short-circuit before touching substrate
    assert artifacts.acquire_for_sprint(None, s, now=1.0) is True
    assert artifacts.release_for_sprint(None, s, now=1.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_sprint_helpers.py -v`
Expected: FAIL with `AttributeError: module 'coscience.artifacts' has no attribute 'sprint_aids'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/artifacts.py`:

```python
# --- sprint binding: a sprint's artifacts are a capacity-1 resource it locks ---
def sprint_aids(sprint) -> list[str]:
    """The artifact ids a sprint touches: existing bound ids + each create-spec's aid."""
    aids = list(sprint.artifacts_bound)
    for spec in sprint.artifacts_create:
        aid = str(spec.get("aid") or "").strip()
        if aid:
            aids.append(aid)
    return aids


def acquire_for_sprint(substrate, sprint, now: float) -> bool:
    """Instantiate any not-yet-existing create-targets, then atomically lock every
    artifact the sprint touches under holder ("sprint", sprint.id). Returns False
    (locking none) if a bound artifact is held by someone else; True (no-op) when
    the sprint has no program or no artifacts."""
    if not sprint.program:
        return True
    for spec in sprint.artifacts_create:
        aid = str(spec.get("aid") or "").strip()
        if aid and not (substrate.artifact_dir(sprint.program, aid) / "meta.md").is_file():
            create_artifact(substrate, sprint.program, aid,
                            str(spec.get("title") or aid), str(spec.get("kind") or "md"))
    aids = sprint_aids(sprint)
    if not aids:
        return True
    return acquire_lock(substrate, sprint.program, aids, "sprint", sprint.id, now)


def release_for_sprint(substrate, sprint, now: float) -> list:
    """Release every artifact the sprint holds, cutting a version from each work/
    (dedup applies). No-op when the sprint has no program or no artifacts."""
    if not sprint.program:
        return []
    aids = sprint_aids(sprint)
    if not aids:
        return []
    return release_lock(substrate, sprint.program, aids, now, created_by=sprint.id)


def sprint_blocked(substrate, sprint) -> bool:
    """True if any EXISTING bound artifact is locked by a different holder, so the
    sprint must not be granted yet. Create-targets don't exist, so they never block."""
    if not sprint.program:
        return False
    for aid in sprint.artifacts_bound:
        p = substrate.artifact_dir(sprint.program, aid) / "meta.md"
        if not p.is_file():
            continue
        lock = substrate.load_artifact(sprint.program, aid).lock
        if lock and lock.get("holder_id") != sprint.id:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_sprint_helpers.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/artifacts.py tests/test_artifact_sprint_helpers.py
git commit -m "feat(artifacts): sprint acquire/release/blocked helpers"
```

---

### Task 3: Dispatcher grant gate + acquire on grant

**Files:**
- Modify: `src/coscience/dispatcher.py` (import `artifacts`; the grant block, lines 69-78)
- Test: `tests/test_dispatcher_artifacts.py`

**Interfaces:**
- Consumes: `artifacts.sprint_blocked`, `artifacts.acquire_for_sprint` (Task 2); existing `Ledger`, `SchedulerPolicy`, `Worker`.
- Produces: a dispatcher that (a) excludes artifact-blocked sprints from grant candidates, (b) on a successful ledger grant, acquires the sprint's artifact locks — releasing the just-taken lease and skipping the grant if the atomic acquire loses a same-cycle race.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dispatcher_artifacts.py
from tests.conftest import FakeAgent

from coscience import artifacts
from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _disp(substrate, capacity, agent=None):
    return Dispatcher(substrate, agent or FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def _queued(substrate, sid, program, bound=None, create=None, prio=0):
    substrate.save_sprint(Sprint(
        id=sid, status=SprintStatus.QUEUED, goals="g", plan=["x"], program=program,
        resources_required={"cpu": 1.0}, priority=prio,
        artifacts_bound=bound or [], artifacts_create=create or []))


def test_bound_sprint_not_granted_while_artifact_locked(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=0.0)  # chat owns it
    _queued(substrate, "s1", "p", bound=["doc"])
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is None                       # stays queued
    assert substrate.load_sprint("s1").status == SprintStatus.QUEUED


def test_bound_sprint_granted_and_locks_artifact_when_free(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _queued(substrate, "s1", "p", bound=["doc"])
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is not None
    assert substrate.load_sprint("s1").status == SprintStatus.EXECUTING
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == "s1"
    assert (substrate.artifact_dir("p", "doc") / "work").is_dir()    # seeded


def test_create_target_instantiated_and_locked_on_grant(substrate):
    _queued(substrate, "s1", "p", create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    fig = substrate.load_artifact("p", "fig")
    assert fig.kind == "figure"
    assert fig.lock["holder_id"] == "s1"


def test_two_sprints_same_artifact_only_one_granted(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _queued(substrate, "s1", "p", bound=["doc"], prio=5)
    _queued(substrate, "s2", "p", bound=["doc"], prio=1)
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    # higher-priority s1 wins the artifact; s2 stays queued (leaseless)
    assert disp.ledger.lease_for("s1") is not None
    assert disp.ledger.lease_for("s2") is None
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == "s1"


def test_nonartifact_sprint_unaffected(substrate):
    _queued(substrate, "s1", "p")     # no artifacts
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dispatcher_artifacts.py -v`
Expected: FAIL — `test_bound_sprint_not_granted_while_artifact_locked` fails (the sprint is granted despite the chat lock, because the gate doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/dispatcher.py`, add to the imports (after line 9-14):

```python
from coscience import artifacts
```

Replace the grant block (lines 69-78) with:

```python
        # --- grants ---
        # A sprint bound to artifacts is grantable only when none of its bound
        # artifacts is locked by another holder (the artifact is a capacity-1
        # resource). Filter those out before the pool scheduler runs.
        needs = [s for s in eligible if self.ledger.lease_for(s.id) is None
                 and not artifacts.sprint_blocked(self.substrate, s)]
        for sprint in self.policy.select_grants(needs, queue, self.ledger, now):
            eff = self.policy.effective_priority(sprint, queue.get(sprint.id, now), now)
            if self.ledger.acquire(sprint.id, sprint.resources_required, now, ttl,
                                   priority=eff, preemptible=sprint.preemptible):
                # Acquire the sprint's artifact locks (instantiating create-targets).
                # If a same-cycle race lost the atomic acquire, give the lease back
                # and leave the sprint queued for a later cycle.
                if not artifacts.acquire_for_sprint(self.substrate, sprint, now):
                    self.ledger.release(sprint.id)
                    continue
                report.granted += 1
                if sprint.status in (SprintStatus.QUEUED, SprintStatus.HIBERNATED):
                    set_status(sprint, SprintStatus.EXECUTING)
                    self.substrate.save_sprint(sprint)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dispatcher_artifacts.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Run the hibernation suite (guard against regression) + commit**

Run: `python -m pytest tests/test_dispatcher_hibernate.py tests/test_dispatcher_artifacts.py -v`
Expected: PASS (existing hibernation tests still green — the grant block change must not break them).

```bash
git add src/coscience/dispatcher.py tests/test_dispatcher_artifacts.py
git commit -m "feat(artifacts): dispatcher gates + acquires artifact locks on grant"
```

---

### Task 4: Worker cuts a version at the terminal transition

**Files:**
- Modify: `src/coscience/worker.py` (import `artifacts`; DONE path 3b ~line 394-410; the two FAILED paths ~line 353-360 and ~line 448-459)
- Test: `tests/test_worker_artifacts.py`

**Interfaces:**
- Consumes: `artifacts.release_for_sprint` (Task 2); existing `Worker.run_sprint_beat`.
- Produces: on the sprint's DONE and FAILED transitions, the worker releases the sprint's artifact locks, cutting a version from each `work/`. Hibernation / reconcile-stop are unchanged (they keep the lock).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_artifacts.py
from tests.conftest import FakeAgent

from coscience import artifacts
from coscience.models import Sprint, SprintStatus
from coscience.worker import Worker


def _executing_bound(substrate, sid="s1"):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    s = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g", plan=["x"],
               program="p", artifacts_bound=["doc"])
    substrate.save_sprint(s)
    artifacts.acquire_for_sprint(substrate, s, now=0.0)   # simulate the grant
    return s


def test_done_cuts_artifact_version_and_unlocks(substrate):
    agent = FakeAgent(finished=True)            # writes finished.json on launch
    s = _executing_bound(substrate)
    w = Worker(substrate, agent)
    w.run_sprint_beat(s)                        # beat 1: launch agent
    # agent "produced" a deliverable into the working copy
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("final")
    w.run_sprint_beat(s)                        # beat 2: collect -> DONE
    assert substrate.load_sprint("s1").status == SprintStatus.DONE
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"
    assert a.lock == {}
    assert (substrate.artifact_dir("p", "doc") / "v1" / "c.md").read_text() == "final"


def test_failed_releases_artifact_lock(substrate):
    # A real-failure agent (nonzero exit, no finished.json): the worker relaunches
    # then collects each beat, so it takes ~2 beats per failure to reach the cap.
    agent = FakeAgent(status="failed", finished=False)
    s = _executing_bound(substrate)
    w = Worker(substrate, agent)
    for _ in range(20):
        w.run_sprint_beat(s)
        s = substrate.load_sprint("s1")
        if s.status == SprintStatus.FAILED:
            break
    assert s.status == SprintStatus.FAILED
    assert substrate.load_artifact("p", "doc").lock == {}


def test_done_dedup_cuts_no_version_when_work_untouched(substrate):
    agent = FakeAgent(finished=True)
    s = _executing_bound(substrate)             # work/ seeded empty, no version yet
    w = Worker(substrate, agent)
    w.run_sprint_beat(s)
    w.run_sprint_beat(s)                        # DONE with an untouched empty work/
    a = substrate.load_artifact("p", "doc")
    assert a.versions == []                     # no spurious version
    assert a.lock == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_artifacts.py -v`
Expected: FAIL — `test_done_cuts_artifact_version_and_unlocks` fails (no version cut; lock still set) because the worker doesn't release artifacts yet.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/worker.py`, add to imports (after line 16):

```python
from coscience import artifacts
```

In the DONE path (3b), immediately after `progress.ambiguous_exits = 0` (line 396), add:

```python
            artifacts.release_for_sprint(self.substrate, sprint, time.time())  # snapshot deliverables
```

In the `MAX_AGENT_FAILURES` terminal branch, immediately after
`set_status(sprint, SprintStatus.FAILED)` (line 354), add:

```python
                artifacts.release_for_sprint(self.substrate, sprint, time.time())
```

In the `MAX_AMBIGUOUS_EXITS` terminal branch, immediately after
`set_status(sprint, SprintStatus.FAILED)` (line 449), add:

```python
            artifacts.release_for_sprint(self.substrate, sprint, time.time())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_artifacts.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the worker contract suite (regression) + commit**

Run: `python -m pytest tests/test_worker_finished_contract.py tests/test_worker_artifacts.py -v`
Expected: PASS (existing worker-contract tests still green).

```bash
git add src/coscience/worker.py tests/test_worker_artifacts.py
git commit -m "feat(artifacts): worker cuts an artifact version at DONE/FAILED"
```

---

### Task 5: Agent instructions name the artifact `work/` dirs

**Files:**
- Modify: `src/coscience/executor.py` (`ExecutionContext`, add `artifacts` field after line 36)
- Modify: `src/coscience/worker.py` (`_build_context`, populate the new field ~line 124-141)
- Modify: `src/coscience/claude_executor.py` (`build_instructions`, add a section ~line 21-152)
- Test: `tests/test_artifact_instructions.py`

**Interfaces:**
- Consumes: `artifacts.sprint_aids` (Task 2); `Substrate.artifact_dir/load_artifact`.
- Produces: `ExecutionContext.artifacts: list[dict]` — `[{"aid": str, "kind": str, "work_path": str}]`; `build_instructions` renders an "Artifacts to produce" section listing each `work_path` when the list is non-empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_instructions.py
from pathlib import Path

from coscience import artifacts
from coscience.claude_executor import build_instructions
from coscience.executor import ExecutionContext
from coscience.models import Sprint, SprintStatus
from coscience.worker import Worker
from tests.conftest import FakeAgent


def test_build_instructions_lists_artifact_work_paths(tmp_path):
    ctx = ExecutionContext(
        artifacts=[{"aid": "manuscript", "kind": "md",
                    "work_path": "/repo/programs/p/artifacts/manuscript/work"}])
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g")
    text = build_instructions(s, ctx, tmp_path / "scratchpad.md")
    assert "Artifacts to produce" in text
    assert "/repo/programs/p/artifacts/manuscript/work" in text
    assert "manuscript" in text


def test_build_instructions_no_section_without_artifacts(tmp_path):
    ctx = ExecutionContext()
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g")
    text = build_instructions(s, ctx, tmp_path / "scratchpad.md")
    assert "Artifacts to produce" not in text


def test_build_context_populates_artifact_work_paths(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "figure")
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["x"],
               program="p", artifacts_bound=["doc"])
    substrate.save_sprint(s)
    ctx = Worker(substrate, FakeAgent())._build_context(s)
    assert len(ctx.artifacts) == 1
    entry = ctx.artifacts[0]
    assert entry["aid"] == "doc"
    assert entry["kind"] == "figure"
    assert entry["work_path"].endswith("artifacts/doc/work")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_instructions.py -v`
Expected: FAIL — `test_build_instructions_lists_artifact_work_paths` fails (`ExecutionContext` has no `artifacts` field / no section rendered).

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/executor.py`, add to `ExecutionContext` (after `job_note` on line 36):

```python
    artifacts: list[dict] = field(default_factory=list)  # [{aid, kind, work_path}] deliverables to write into work/
```

`worker.py` already imports `from coscience import artifacts` (added in Task 4) —
do NOT add it again.

In `src/coscience/worker.py` `_build_context`, immediately before the
`return ExecutionContext(` line (line 124), add:

```python
        artifact_specs: list[dict] = []
        if sprint.program:
            for aid in artifacts.sprint_aids(sprint):
                work_path = self.substrate.artifact_dir(sprint.program, aid) / "work"
                try:
                    kind = self.substrate.load_artifact(sprint.program, aid).kind
                except OSError:
                    kind = next((str(c.get("kind") or "md")
                                 for c in sprint.artifacts_create
                                 if str(c.get("aid") or "") == aid), "md")
                artifact_specs.append({"aid": aid, "kind": kind, "work_path": str(work_path)})
```

and add `artifacts=artifact_specs,` as an argument inside the `ExecutionContext(...)`
constructor (e.g. after `job_note=progress.job_note,` on line 141).

In `src/coscience/claude_executor.py` `build_instructions`, add a section builder.
After the `comments` / `feedback_threads` block and before the `if context.assess_reason:`
block (i.e. after line 44, inside `if context is not None:`), add:

```python
        if context.artifacts:
            alines = "\n".join(
                f'- `{a["aid"]}` ({a["kind"]}): write this artifact\'s files into {a["work_path"]}'
                for a in context.artifacts)
            artifacts_section = (
                "\n\n## Artifacts to produce (deliverables)\n"
                "Write each artifact's files into its working directory below. The platform "
                "snapshots each working copy as a new immutable version when this sprint "
                "completes — you do not manage version numbers yourself; just create and edit "
                "the current files in place.\n" + alines)
```

Initialize `artifacts_section = ""` next to the other section defaults (with
`assess_section = ""` on line 27):

```python
    artifacts_section = ""
```

and interpolate it into the returned template immediately after `{comments}`
(the Objective block, line 77). Change:

```python
{comments}
```

to:

```python
{comments}{artifacts_section}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_instructions.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/executor.py src/coscience/worker.py src/coscience/claude_executor.py tests/test_artifact_instructions.py
git commit -m "feat(artifacts): agent instructions name each artifact work/ dir"
```

---

### Task 6: Human/API can create sprints that bind or create artifacts

**Files:**
- Modify: `src/coscience/service.py` (`submit_sprint`, lines 53-72)
- Modify: `src/coscience/http_api.py` (`SprintSubmit` model line 46-53; the `POST /sprints` handler lines 203-215)
- Test: `tests/test_artifact_submit.py`

**Interfaces:**
- Consumes: `Service.submit_sprint`; `Sprint.artifacts_bound/artifacts_create` (Task 1).
- Produces: `submit_sprint(..., artifacts_bound=None, artifacts_create=None)` persists the two fields; `SprintSubmit` accepts them; the create route forwards them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_submit.py
from coscience.service import Service


def test_submit_sprint_persists_artifact_fields(substrate):
    svc = Service(substrate)
    svc.submit_sprint(id="s1", goals="g", plan=["do"], program="p",
                      artifacts_bound=["manuscript"],
                      artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    s = substrate.load_sprint("s1")
    assert s.artifacts_bound == ["manuscript"]
    assert s.artifacts_create == [{"aid": "fig", "title": "Fig", "kind": "figure"}]


def test_submit_sprint_defaults_empty(substrate):
    svc = Service(substrate)
    svc.submit_sprint(id="s2", goals="g", plan=["do"], program="p")
    s = substrate.load_sprint("s2")
    assert s.artifacts_bound == []
    assert s.artifacts_create == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_submit.py -v`
Expected: FAIL with `TypeError: submit_sprint() got an unexpected keyword argument 'artifacts_bound'`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/service.py` `submit_sprint`, extend the signature (add before
`status: str = "proposed"`):

```python
                      artifacts_bound: list | None = None,
                      artifacts_create: list | None = None,
```

and set the fields on the `Sprint(...)` constructor (after `preemptible=preemptible,`):

```python
            artifacts_bound=[str(a) for a in (artifacts_bound or [])],
            artifacts_create=[dict(c) for c in (artifacts_create or [])],
```

In `src/coscience/http_api.py`, add to `SprintSubmit` (after
`resources_required: dict[str, float] | None = None` on line 53):

```python
    artifacts_bound: list[str] | None = None
    artifacts_create: list[dict] | None = None
```

and forward them in the `POST /sprints` handler (inside `service.submit_sprint(...)`,
after `resources_required=body.resources_required,` on line 211):

```python
                artifacts_bound=body.artifacts_bound,
                artifacts_create=body.artifacts_create,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_submit.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full artifact + sprint suites + commit**

Run: `python -m pytest tests/test_artifact_*.py tests/test_dispatcher_artifacts.py tests/test_worker_artifacts.py -v`
Expected: PASS (all green).

```bash
git add src/coscience/service.py src/coscience/http_api.py tests/test_artifact_submit.py
git commit -m "feat(artifacts): submit_sprint + API accept artifacts_bound/create"
```

---

## Phase 2 Done — What Exists Now

A sprint can bind existing artifacts and declare new ones; the dispatcher treats
each artifact as a capacity-1 resource (won't grant a sprint whose artifact is
busy, locks them atomically on grant), and the worker cuts a new artifact version
from each `work/` when the sprint reaches DONE or FAILED. The agent's instructions
name the `work/` directories so a real worker writes deliverables there.

**Deliberately NOT in this phase:** chat-mode binding + the inactivity reaper wired
to the loop (Phase 4); artifact UI + cross-link lists (Phase 3); PM `artifact_task`
emission (Phase 5). Carried-forward Phase-1 review defers to weigh here: make
`acquire_lock` exception-atomic mid-loop; don't nest `_lock_guard` in any loop
caller.
