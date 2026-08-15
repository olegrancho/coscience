# Global Pause/Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Pause/Resume button on the Compute page that stops the platform starting any new Claude session — PM beats, worker launches, Replan, Compress/Brainstorm and chat — while work already running drains to completion.

**Architecture:** A marker file `.coscience/paused` in the substrate is the single source of truth, so all three processes (`coscience-http`, the PM loop, the dispatch loop) agree and the state survives a restart. Enforcement hangs off `claude_usage_ok()`, which is already the choke point every Claude-spending path calls. The dispatcher needs one narrow guard on its **grant step only** — its launches go through the Worker gate, but grants run earlier and consult no gate, so without it a paused platform keeps taking leases for sprints that never start. Everything else in its cycle (reap, release, reconcile, beat) keeps running so the drain completes.

**Tech Stack:** Python 3.12, FastAPI, pytest. React + TypeScript, Mantine, TanStack Query, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-12-global-pause-design.md`

## Global Constraints

- Runtime is Linux-only. Tests run with `~/venvs/coscience/bin/python -m pytest`.
- Frontend tests run with `cd frontend && npm test`.
- **Commit locally at the end of each task. Never push.** The human has authorized the
  local commits in this plan; nothing leaves the machine. Do not run `git push`, and do
  not deploy — deploying restarts live agent loops and is the human's call.
- Paused means **no new Claude session starts**. A session already running is never killed.
- `repo_root=None` must keep `claude_usage_ok` behaving exactly as it does today — existing callers and tests depend on it.

---

### Task 1: The pause marker

**Files:**
- Create: `src/coscience/pause.py`
- Test: `tests/test_pause.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `is_paused(repo_root) -> bool` and `set_paused(repo_root, paused: bool) -> None`. `repo_root` is a `Path` or `str` pointing at the substrate root (the dir containing `.coscience/`). Tasks 2 and 4 both import these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pause.py`:

```python
from coscience import pause


def test_a_fresh_substrate_is_not_paused(tmp_path):
    assert pause.is_paused(tmp_path) is False


def test_set_paused_round_trips(tmp_path):
    pause.set_paused(tmp_path, True)
    assert pause.is_paused(tmp_path) is True
    pause.set_paused(tmp_path, False)
    assert pause.is_paused(tmp_path) is False


def test_pausing_twice_is_harmless(tmp_path):
    pause.set_paused(tmp_path, True)
    pause.set_paused(tmp_path, True)
    assert pause.is_paused(tmp_path) is True


def test_resuming_a_running_platform_is_harmless(tmp_path):
    # The marker was never created; removing it must not raise.
    pause.set_paused(tmp_path, False)
    assert pause.is_paused(tmp_path) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pause.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.pause'`

- [ ] **Step 3: Write minimal implementation**

Create `src/coscience/pause.py`:

```python
"""Global pause: one marker file the whole platform reads.

Three processes need to agree on whether the platform is paused — coscience-http, the
PM loop and the dispatch loop — so the flag lives in the substrate rather than in any
one process's memory, and survives a restart. A bare marker, because "paused" is one
bit: there is no schema to get wrong or migrate."""
from __future__ import annotations

from pathlib import Path


def _marker(repo_root) -> Path:
    return Path(repo_root) / ".coscience" / "paused"


def is_paused(repo_root) -> bool:
    """True when a human has paused the platform. No marker = running."""
    return _marker(repo_root).is_file()


def set_paused(repo_root, paused: bool) -> None:
    """Create or remove the marker. Idempotent in both directions, so a double-click
    on Pause and a Resume on an already-running platform are both no-ops."""
    path = _marker(repo_root)
    if paused:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    elif path.is_file():
        path.unlink()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pause.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit** (ask the human first — see Global Constraints)

```bash
git add src/coscience/pause.py tests/test_pause.py
git commit -m "feat(pause): a marker file the whole platform reads"
```

---

### Task 2: Gate every Claude session on the marker

**Files:**
- Modify: `src/coscience/worker.py:80-91` (`claude_usage_ok`) and `src/coscience/worker.py:197`
- Modify: `src/coscience/cli.py:226-228`
- Modify: `src/coscience/service.py:587`, `src/coscience/service.py:601`, `src/coscience/service.py:809`
- Test: `tests/test_usage_gate.py`

**Interfaces:**
- Consumes: `pause.is_paused(repo_root)` from Task 1.
- Produces: `claude_usage_ok(threshold: float = 100.0, *, fail_open: bool = True, repo_root=None) -> bool`. When `repo_root` is not None and the platform is paused, returns False without running the usage script. Task 3 relies on this same call returning False.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_usage_gate.py`.

**`monkeypatch.undo()` is essential in every one of these.** `tests/conftest.py` has an
autouse fixture `_permissive_usage` that replaces `worker_mod.claude_usage_ok` with
`lambda *a, **k: True`. Without the undo these tests would call the stub, pass no matter
what you implement, and prove nothing. `test_the_gate_reads_the_configured_script`
in this same file already uses that pattern — follow it.

```python
def test_the_gate_refuses_while_paused(monkeypatch, tmp_path):
    """Usage wide open, so only the pause can refuse."""
    from coscience import pause, worker as worker_mod
    monkeypatch.undo()                      # drop conftest's autouse stub
    monkeypatch.setattr(worker_mod.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=_line(1, 1, "live")))

    assert worker_mod.claude_usage_ok(repo_root=tmp_path) is True
    pause.set_paused(tmp_path, True)
    assert worker_mod.claude_usage_ok(repo_root=tmp_path) is False


def test_a_paused_gate_never_runs_the_usage_script(monkeypatch, tmp_path):
    """Checked before shelling out: a paused platform costs nothing to poll."""
    from coscience import pause, worker as worker_mod
    monkeypatch.undo()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=_line(1, 1, "live"))

    monkeypatch.setattr(worker_mod.subprocess, "run", fake_run)
    pause.set_paused(tmp_path, True)

    assert worker_mod.claude_usage_ok(repo_root=tmp_path) is False
    assert calls == []


def test_without_a_repo_root_the_gate_ignores_pause(monkeypatch, tmp_path):
    """Existing callers pass no repo_root and must behave exactly as before."""
    from coscience import pause, worker as worker_mod
    monkeypatch.undo()
    monkeypatch.setattr(worker_mod.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=_line(1, 1, "live")))
    pause.set_paused(tmp_path, True)

    assert worker_mod.claude_usage_ok() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_usage_gate.py -v -k paused`
Expected: FAIL — `TypeError: claude_usage_ok() got an unexpected keyword argument 'repo_root'`

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/worker.py`, add the import near the other `coscience` imports:

```python
from coscience.pause import is_paused
```

Replace `claude_usage_ok` (currently at line 80):

```python
def claude_usage_ok(threshold: float = 100.0, *, fail_open: bool = True,
                    repo_root=None) -> bool:
    """True if it's safe to launch a Claude agent at this threshold — neither the
    5-hour nor the weekly window has passed it. `fail_open` decides what an
    unreadable usage script means: True for human-triggered work (never block a
    person on a missing dotfile), False for autonomous loops (an unmetered loop is
    exactly what burns a window unattended).

    `repo_root` enables the human pause check, and is tested FIRST: a paused platform
    starts no new Claude session whatever the windows say, and polling usage.py to
    learn that would be wasted work. None (the default) skips the check, for callers
    that hold no substrate."""
    if repo_root is not None and is_paused(repo_root):
        return False
    try:
        out = subprocess.run([sys.executable, usage_meter.usage_script_path()],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return fail_open
    return _usage_ok_from_output(out, threshold=threshold)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_usage_gate.py -v`
Expected: PASS (all, including the pre-existing gate tests)

- [ ] **Step 5: Wire the five call sites**

`src/coscience/worker.py:197` — the worker's default gate:

```python
        return (self._usage_gate or
                (lambda: claude_usage_ok(WORKER_THRESHOLD, fail_open=False,
                                         repo_root=self.substrate.repo_root)))()
```

`src/coscience/cli.py:226-228` — the PM loop:

```python
            summaries = pm_run_once(substrate, reasoner,
                                    usage_ok=lambda: claude_usage_ok(
                                        AUTONOMOUS_THRESHOLD, fail_open=False,
                                        repo_root=substrate.repo_root))
```

`src/coscience/service.py:587` — Replan:

```python
        return pm_beat(self.substrate, program_id, self._pm_reasoner(),
                       usage_ok=lambda: claude_usage_ok(
                           repo_root=self.substrate.repo_root), force=True)
```

`src/coscience/service.py:601` — Compress/Brainstorm:

```python
        return pm_beat(self.substrate, program_id, self._pm_reasoner(),
                       usage_ok=lambda: claude_usage_ok(
                           repo_root=self.substrate.repo_root),
                       force=True, directive=mode)
```

`src/coscience/service.py:809` — chat:

```python
        if launch is None and not claude_usage_ok(repo_root=self.substrate.repo_root):
```

- [ ] **Step 5b: Guard the dispatcher's grant step**

Corrected during execution — the first draft of this plan wrongly said the dispatcher
needed no change. Its *launches* go through the Worker gate, but the grant step runs
earlier and consults no gate: it acquires a lease and flips a QUEUED sprint to
EXECUTING regardless. Left alone, a paused platform keeps taking leases for sprints
that never start, and Task 5 reports `leases.length` as "still finishing" — so that
count would *grow* while paused.

In `src/coscience/dispatcher.py`, add to the imports:

```python
from coscience.pause import is_paused
```

and in `run_one_cycle`, at the `# --- grants ---` step, replace the `needs = [...]`
assignment (keeping the existing comment about artifact-bound sprints) with:

```python
        # A paused platform starts nothing new. Guard the GRANT step only: reaping,
        # releasing, reconciling and beating leased sprints all keep running below,
        # which is what lets work already in flight drain to completion. Reads the
        # marker directly rather than via claude_usage_ok — this is about the pause,
        # and routing it through the usage gate would also stop grants whenever usage
        # merely ran high, which is a behaviour change nobody asked for.
        needs = [] if is_paused(self.substrate.repo_root) else [
            s for s in eligible if self.ledger.lease_for(s.id) is None
            and not artifacts.sprint_blocked(self.substrate, s)]
```

- [ ] **Step 6: Write the failing tests for worker wiring and for the drain**

Create `tests/test_pause_enforcement.py`.

Two things need proving, and they need different techniques:

1. **The worker's gate is actually handed `repo_root`.** Asserted on the call, not on a
   boolean — the same reasoning as `test_the_gate_reads_the_configured_script`: on a
   host with healthy usage the boolean is True either way, so only the argument proves
   the wiring.
2. **A paused dispatcher still drains.** Here the gate is replaced with one that
   consults *only* the pause, isolating the variable under test from the host's real
   usage. conftest's docstring explicitly invites this ("Tests that exercise the gate
   pass usage_gate=...").

```python
"""A paused platform starts nothing new, but its bookkeeping keeps running — that is
what lets already-running work drain instead of stranding its lease."""
from tests.conftest import FakeAgent

from coscience import pause
from coscience import worker as worker_mod
from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _queued(sid, req=None):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["do the work"],
                  resources_required=req or {})


def _dispatcher(substrate, capacity):
    return Dispatcher(substrate, FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def _pause_only_gate(monkeypatch, repo_root):
    """Usage always healthy; only the pause marker can refuse."""
    monkeypatch.setattr(worker_mod, "claude_usage_ok",
                        lambda *a, **k: not pause.is_paused(repo_root))


def test_the_workers_default_gate_is_given_the_substrate(substrate, monkeypatch):
    seen = {}
    monkeypatch.setattr(worker_mod, "claude_usage_ok",
                        lambda *a, **k: seen.update(k) or True)
    substrate.save_sprint(_queued("sp1", req={"gpu": 1.0}))

    _dispatcher(substrate, {"gpu": 1.0}).run_one_cycle(now=0.0)

    assert seen.get("repo_root") == substrate.repo_root


def test_a_paused_dispatcher_grants_nothing_new(substrate, monkeypatch):
    _pause_only_gate(monkeypatch, substrate.repo_root)
    substrate.save_sprint(_queued("sp1", req={"gpu": 1.0}))
    pause.set_paused(substrate.repo_root, True)
    disp = _dispatcher(substrate, {"gpu": 1.0})

    for t in range(4):
        disp.run_one_cycle(now=float(t))

    assert substrate.load_sprint("sp1").status == SprintStatus.QUEUED


def test_work_already_running_drains_while_paused(substrate, monkeypatch):
    """The point of choosing drain over hibernate: pausing mid-flight must not strand
    a lease. The collect path holds no usage gate, so a finished agent is still
    reaped, the sprint reaches DONE and the lease is released."""
    _pause_only_gate(monkeypatch, substrate.repo_root)
    substrate.save_sprint(_queued("sp1", req={"gpu": 1.0}))
    disp = _dispatcher(substrate, {"gpu": 1.0})

    disp.run_one_cycle(now=0.0)                                   # grant + launch
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    pause.set_paused(substrate.repo_root, True)                   # pause mid-flight

    for t in range(1, 6):
        disp.run_one_cycle(now=float(t))

    assert substrate.load_sprint("sp1").status == SprintStatus.DONE
    disp.ledger.load()
    assert disp.ledger.lease_for("sp1") is None
```

- [ ] **Step 7: Run it**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_pause_enforcement.py -v`
Expected: 3 PASS.

If `test_work_already_running_drains_while_paused` fails with the sprint stuck in
EXECUTING, do **not** relax the assertion — it means a usage gate sits on the collect
path and the drain genuinely cannot finish, which contradicts the spec. Stop and
re-read `worker.run_sprint_beat`.

- [ ] **Step 8: Run the whole suite**

Run: `~/venvs/coscience/bin/python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 9: Commit** (ask the human first)

```bash
git add src/coscience/worker.py src/coscience/cli.py src/coscience/service.py \
        tests/test_usage_gate.py tests/test_pause_enforcement.py
git commit -m "feat(pause): gate every Claude session on the pause marker"
```

---

### Task 3: Say which kind of pause it is in the loop log

**Files:**
- Modify: `src/coscience/cli.py` (`pm_beat_line`, and the `pm` command's `_beat`)
- Test: `tests/test_cli_pm.py`

**Interfaces:**
- Consumes: `pause.is_paused(repo_root)` from Task 1.
- Produces: the beat line string `"paused by human — Resume in Compute"`. Nothing else depends on it.

**Deliberate carve-out — do not "fix" this:** `coscience pm --once` stays unaffected.
That path passes no `usage_ok` at all today, so it already bypasses the usage limits;
it is a human at a terminal asking for one specific beat, the CLI equivalent of Replan.
Only the `--loop` path gets the pause check here. (Replan *through the dashboard* is
gated, via Task 2's `service.py:587`.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_pm.py`:

```python
def test_the_pm_loop_says_when_a_human_paused_it(tmp_path, monkeypatch, capsys):
    """'paused — Claude usage exhausted' and a human pause are different situations;
    the log has to tell them apart or a deliberate pause reads like an exhausted
    window that will fix itself after the reset."""
    from coscience import pause
    _seed_program(tmp_path)
    pause.set_paused(tmp_path, True)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)   # no real sleeping

    def _boom(*a):
        raise AssertionError("the reasoner must not be built while paused")

    monkeypatch.setattr(cli, "_make_pm_reasoner", _boom)

    rc = cli.main(["pm", "--repo", str(tmp_path), "--loop", "--max-rounds", "1"])

    assert rc == 0
    assert "paused by human" in capsys.readouterr().out
```

If the assertion on `capsys` comes up empty, check how `LoopStatus` renders — the beat
line goes through it rather than a bare `print`. The `_boom` half of the test is the
load-bearing part (no reasoner is built while paused); if the rendering makes the string
assertion awkward, assert on `LoopStatus`'s recorded last line instead of dropping it.

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_cli_pm.py -v -k human_paused`
Expected: FAIL — the reasoner is built, so `AssertionError: the reasoner must not be built while paused`

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/cli.py`, add to the imports:

```python
from coscience.pause import is_paused
```

In the `pm` command, check the marker before building the reasoner and inside `_beat`:

```python
    if args.command == "pm":
        substrate = Substrate(args.repo)
        if args.loop and is_paused(substrate.repo_root):
            # Built lazily: constructing the reasoner is cheap, but a paused platform
            # should be able to start its loop without touching Claude config at all.
            reasoner = None
        else:
            reasoner = _make_pm_reasoner(substrate)
```

Replace the loop's `_beat` with a pause check that skips the cycle entirely:

```python
        def _beat():
            nonlocal reasoner
            if is_paused(substrate.repo_root):
                return "paused by human — Resume in Compute", {"proposed": 0}, 0
            if reasoner is None:
                reasoner = _make_pm_reasoner(substrate)
            summaries = pm_run_once(substrate, reasoner,
                                    usage_ok=lambda: claude_usage_ok(
                                        AUTONOMOUS_THRESHOLD, fail_open=False,
                                        repo_root=substrate.repo_root))
            ids = [sid for s in summaries for sid in s["submitted"]]
            reasoned = sum(0 if s.get("skipped") else 1 for s in summaries)
            # reasoned == Claude calls this beat (skipped cycles don't call Claude)
            return pm_beat_line(summaries, reasoned), {"proposed": len(ids)}, reasoned
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_cli_pm.py -v`
Expected: PASS (all, including the pre-existing CLI PM tests)

- [ ] **Step 5: Commit** (ask the human first)

```bash
git add src/coscience/cli.py tests/test_cli_pm.py
git commit -m "feat(pause): the PM loop names a human pause in its beat line"
```

---

### Task 4: Report and set pause over HTTP

**Files:**
- Modify: `src/coscience/service.py` (`ledger_status` at line 1427; add `set_pause`)
- Modify: `src/coscience/http_api.py` (add `PauseUpdate` model near `CapacityUpdate` at line 92; add the route near `/capacity` at line 669)
- Test: `tests/test_http_capacity.py`

**Interfaces:**
- Consumes: `pause.is_paused` / `pause.set_paused` from Task 1.
- Produces: `GET /api/ledger` gains a `"paused": bool` key. `PUT /api/pause` takes `{"paused": bool}` and returns the same shape as `GET /api/ledger`. Task 5 consumes both.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_http_capacity.py`:

```python
def test_ledger_reports_not_paused_by_default(client):
    assert client.get("/api/ledger").json()["paused"] is False


def test_put_pause_flips_the_state_and_the_ledger_reports_it(client):
    assert client.put("/api/pause", json={"paused": True}).json()["paused"] is True
    assert client.get("/api/ledger").json()["paused"] is True

    assert client.put("/api/pause", json={"paused": False}).json()["paused"] is False
    assert client.get("/api/ledger").json()["paused"] is False


def test_put_pause_returns_the_full_ledger_status(client):
    """The page re-renders from one payload, so pause must return what ledger returns."""
    body = client.put("/api/pause", json={"paused": True}).json()
    assert set(body) >= {"capacity", "used", "available", "leases", "paused"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_capacity.py -v -k pause`
Expected: FAIL — `KeyError: 'paused'` on the first test, 404 on the others

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/service.py`, add `paused` to `ledger_status` (line 1427):

```python
    def ledger_status(self) -> dict:
        from coscience.pause import is_paused
        ledger = self._ledger()
        return {
            "capacity": dict(self.pool.capacity),
            "used": ledger.used(),
            "available": ledger.available(),
            "paused": is_paused(self.substrate.repo_root),
            "leases": [
                {"id": l.id, "sprint_id": l.sprint_id, "amounts": l.amounts,
                 "granted_at": l.granted_at, "expires_at": l.expires_at,
                 "priority": l.priority, "preemptible": l.preemptible}
                for l in ledger.all_leases()
            ],
        }
```

Add `set_pause` directly after it:

```python
    def set_pause(self, paused: bool) -> dict:
        """Pause or resume the whole platform. Commits so the substrate history records
        who stopped the machine and when, the way a capacity edit does. Returns fresh
        ledger status so one round-trip re-renders the Compute page."""
        from coscience.pause import set_paused
        set_paused(self.substrate.repo_root, bool(paused))
        self.substrate.commit("paused" if paused else "resumed")
        return self.ledger_status()
```

In `src/coscience/http_api.py`, add the request model next to `CapacityUpdate` (line 92):

```python
class PauseUpdate(BaseModel):
    paused: bool
```

And the route next to `PUT /capacity` (line 669):

```python
    @api.put("/pause")
    def set_pause(body: PauseUpdate) -> dict:
        return service.set_pause(body.paused)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_capacity.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `~/venvs/coscience/bin/python -m pytest -q`
Expected: PASS. If a ledger-shape test elsewhere asserts an exact key set, update it to
include `paused`.

- [ ] **Step 6: Commit** (ask the human first)

```bash
git add src/coscience/service.py src/coscience/http_api.py tests/test_http_capacity.py
git commit -m "feat(api): report and set the platform pause"
```

---

### Task 5: The Pause/Resume button on the Compute page

**Files:**
- Modify: `frontend/src/api.ts:78-81` (the `Ledger` interface) and `frontend/src/api.ts:314` (add `setPause`)
- Modify: `frontend/src/views/Ledger.tsx:61-66` (the header block)
- Test: `frontend/src/views/Ledger.test.tsx`

**Interfaces:**
- Consumes: `GET /api/ledger` `.paused` and `PUT /api/pause` from Task 4.
- Produces: nothing downstream.

- [ ] **Step 1: Write the failing test**

Append inside the existing `describe("Compute page", ...)` block in `frontend/src/views/Ledger.test.tsx`:

```tsx
  it("offers Pause while the platform is running", async () => {
    ledger.mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false });
    renderPage();
    expect(await screen.findByRole("button", { name: /pause/i })).toBeTruthy();
  });

  it("pauses the platform when Pause is clicked", async () => {
    ledger.mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /pause/i }));
    await waitFor(() => expect(api.setPause).toHaveBeenCalledWith(true));
  });

  it("shows what is still finishing while paused", async () => {
    ledger.mockResolvedValue({
      capacity: {}, used: {}, available: {},
      leases: [{ id: "l1", sprint_id: "p1-c0-a" }], paused: true,
    });
    renderPage();
    expect(await screen.findByText(/1 still finishing/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /resume/i })).toBeTruthy();
  });

  it("says nothing is running once the drain completes", async () => {
    ledger.mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: true });
    renderPage();
    expect(await screen.findByText(/nothing running/i)).toBeTruthy();
  });
```

Add `setPause` to the mocked api at the top of the file:

```tsx
vi.mock("../api", () => ({
  api: {
    getLedger: () => ledger(),
    getUsage: () => Promise.resolve(null),
    setCapacity: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: false }),
    setPause: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [], paused: true }),
  },
}));
```

`setCapacity`'s mock needs the new `paused` key too — it resolves to a `Ledger`, and
`npm run build` typechecks the test files. Every other `Ledger` literal already in this
file needs it as well; the build will name them if you miss one.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- Ledger`
Expected: FAIL — no button matching `/pause/i` is found

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/api.ts`, add `paused` to the `Ledger` interface (line 78):

```ts
export interface Ledger {
  capacity: Record<string, number>; used: Record<string, number>;
  available: Record<string, number>; leases: unknown[];
  paused: boolean;
}
```

Add the client call after `setCapacity` (line 319):

```ts
  setPause: (paused: boolean) =>
    fetch("/api/pause", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused }),
    }).then(j<Ledger>),
```

In `frontend/src/views/Ledger.tsx`, add the mutation above the `if (ledger.isLoading)` guard (line 56):

```tsx
  const togglePause = async () => {
    setSaveError("");
    try {
      await api.setPause(!ledger.data?.paused);
      qc.invalidateQueries({ queryKey: ["ledger"] });
    } catch (e) {
      setSaveError(String(e));
    }
  };
```

Replace the header block (lines 63-66) with:

```tsx
      <Group justify="space-between" align="flex-end">
        <div>
          <div className="eyebrow" style={{ marginBottom: 7 }}>resources</div>
          <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: 0 }}>Compute</h1>
        </div>
        <Group gap="sm">
          {l.paused && (
            <Text size="sm" c="dimmed">
              {l.leases.length
                ? `Paused — ${l.leases.length} still finishing`
                : "Paused — nothing running"}
            </Text>
          )}
          <Button size="xs" color={l.paused ? "green" : "red"}
                  variant={l.paused ? "filled" : "default"}
                  onClick={() => { void togglePause(); }}>
            {l.paused ? "Resume" : "Pause"}
          </Button>
        </Group>
      </Group>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- Ledger`
Expected: PASS

- [ ] **Step 5: Run the full frontend suite and typecheck**

Run: `cd frontend && npm test && npm run build`
Expected: PASS, and the build succeeds (it typechecks — every other `Ledger` literal in
tests now needs the `paused` key, so fix any type errors the build reports).

- [ ] **Step 6: Commit** (ask the human first)

```bash
git add frontend/src/api.ts frontend/src/views/Ledger.tsx frontend/src/views/Ledger.test.tsx
git commit -m "feat(frontend): pause and resume the platform from Compute"
```

---

## Deploying

Per `CLAUDE.md` this is a backend + frontend change, so a deploy needs the frontend
build and a restart of the backend and both loops. `scripts/deploy.sh` does **not**
work on Avatar (it calls `pip`, and that venv has only `pip3`); use the script in
`local_setup_avatar.md`, run from a file, never `bash -c`.

After deploying, verify end to end:

```bash
curl -s -X PUT 127.0.0.1:8000/api/pause -H 'Content-Type: application/json' \
     -d '{"paused": true}' | python3 -m json.tool | grep paused
ls ~/sync/bmt-share/coscience/.coscience/paused        # marker exists
tail -2 ~/coscience-pm.log                             # "paused by human — Resume in Compute"
curl -s -X PUT 127.0.0.1:8000/api/pause -H 'Content-Type: application/json' \
     -d '{"paused": false}' | python3 -m json.tool | grep paused
```
