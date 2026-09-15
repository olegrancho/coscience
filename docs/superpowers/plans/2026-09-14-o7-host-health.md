# O7 Host Health, Drain and Remove Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every remote host is checked regularly; one that stops answering takes no new grants without losing its jobs; a human can drain and remove a host; the Compute page shows each host's health, what is in use on it, and what sprints left there.

**Architecture:** A new `host_health` module checks each placeable remote host at most once a minute with `ssh <target> bash -c true` and records the result in `.coscience/host-health.json`, the file both the dispatch loop (which writes it) and the HTTP server (which reads it) agree on. A host failing for 30 minutes is **quiet**. The pool gains `grantable_hosts`: placeable hosts that are neither drained (`drain: true` in `resources.yaml`) nor closed for this cycle (quiet). Only grants and yield choose among grantable hosts; leases, jobs, liveness and totals are untouched. Leases on hosts that left the pool stop counting in pool-wide use and are listed as stranded. One sprint's failing beat no longer aborts the cycle. The service exposes health, per-host use, leftover run directories, drain and remove; the Compute page's servers card shows and drives them.

**Tech Stack:** Python 3 (FastAPI, PyYAML, pytest), React + Mantine + TanStack Query (vitest).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§7 Unreachable hosts and reboots, §10 row O7, §11 "O7" and "O7, from O6's final review")

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python`, prefixed `PYTHONPATH=src` in a worktree. Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Tests never run ssh or rsync: they inject a runner, and an autouse fixture replaces the health check's default runner.
- No job is killed and no lease released because a host stopped answering (§7). A quiet host only stops taking new grants.
- Escalating sprints that sleep on a quiet host is O8's; O7 only exposes the state.
- A host is removed only after it is drained and no lease names it.
- Local sprints behave exactly as before; `local` is never checked, drained or removed (the platform has Pause for that).
- With `COSCIENCE_ALLOW_REMOTE` unset no remote host is placeable, so no check runs and nothing about placement changes.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/host_health.py` | create | check remote hosts, persist health, tell ok / failing / quiet / unchecked |
| `src/coscience/resources.py` | modify | `Host.drain`, `ResourcePool.closed`, `grantable_hosts` |
| `src/coscience/ledger.py` | modify | fit and can_fit over grantable hosts; stranded leases leave pool-wide use; `stranded()` |
| `src/coscience/scheduler.py` | modify | yield victims only on grantable hosts |
| `src/coscience/dispatcher.py` | modify | health check each cycle closes quiet hosts; a failing beat is isolated |
| `src/coscience/worker.py` | modify | a remote job the stop path could not end is named in the note |
| `src/coscience/service.py` | modify | ledger status per host: health, drain, use, leases, leftovers; stranded; drain and remove; unrunnable names a draining or quiet pin |
| `src/coscience/pm_agent.py`, `src/coscience/pm_claude.py` | modify | COMPUTE says a host takes no new work |
| `src/coscience/http_api.py` | modify | `PUT /api/hosts/{name}/drain`, `DELETE /api/hosts/{name}` |
| `frontend/src/api.ts` | modify | host health/use/leftover types; `drainHost`, `removeHost` |
| `frontend/src/components/HostsCard.tsx` | modify | health, in-use, leftovers, drain and remove |
| `tests/conftest.py` | modify | autouse fixture: the health check never runs ssh |
| `tests/test_host_health.py` | create | module, grantability, stranded, dispatcher, beat isolation, stop note |
| `tests/test_host_admin.py` | create | ledger status fields, drain, remove, unrunnable, PM compute, HTTP |
| `frontend/src/components/HostsCard.test.tsx` | modify | health, in use, leftovers, drain, remove |

---

### Task 1: Hosts are checked, and a quiet host takes no new grants

**Files:**
- Create: `src/coscience/host_health.py`, `tests/test_host_health.py`
- Modify: `src/coscience/resources.py`, `src/coscience/ledger.py`, `src/coscience/scheduler.py`, `src/coscience/dispatcher.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `host_probe.Runner`, `host_probe.ssh_argv`, `host_probe.subprocess_runner` (O5); `Host.placeable` (O6).
- Produces:
  - `host_health.HEALTH_FILE = ".coscience/host-health.json"`, `CHECK_INTERVAL = 60.0`, `QUIET_AFTER = 1800.0`, `CHECK_TIMEOUT = 20.0`
  - `host_health.load(repo_root) -> dict[str, dict]`; each entry `{"checked_at": float, "last_ok": float, "fail_since": float, "reason": str}`
  - `host_health.state(entry: dict | None, now: float) -> str` — `"unchecked" | "ok" | "failing" | "quiet"`
  - `host_health.check(repo_root, pool, now, runner=None) -> dict[str, dict]` — checks due hosts, saves, returns all entries for hosts still in the pool
  - `host_health.quiet(entries, now) -> set[str]`
  - `Host.drain: bool = False` (parsed from `drain:`); `ResourcePool.closed: dict[str, str]` (host name → reason, set per cycle, never parsed); `ResourcePool.grantable_hosts(program) -> list[Host]`
  - `Dispatcher(..., host_runner=None)`

- [ ] **Step 1: Write the failing tests**

Add to `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def health_check_never_runs_ssh(monkeypatch):
    """The dispatcher checks remote hosts each cycle; in tests every host answers."""
    from coscience import host_health
    monkeypatch.setattr(host_health, "default_runner", lambda argv, stdin, timeout: (0, "", ""))
```

Create `tests/test_host_health.py`:

```python
"""O7: remote hosts are checked; a quiet host takes no new grants and keeps its work."""
import json

from coscience import host_health
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy

POOL = {"cpu": 4, "workers": 4,
        "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 16}},
                  "gone": {"ssh": "gone", "capacity": {"cpu": 8}}}}


class ScriptRunner:
    def __init__(self, answers):
        self.answers, self.calls = dict(answers), []

    def __call__(self, argv, stdin, timeout):
        self.calls.append(list(argv))
        target = next(a for a in argv if a in self.answers)
        return self.answers[target]


def _sprint(sid, req, prio=0, status=SprintStatus.QUEUED):
    return Sprint(id=sid, status=status, goals="g", plan=["a"], resources_required=req, priority=prio)


def test_state_reads_unchecked_ok_failing_and_quiet():
    assert host_health.state(None, 100.0) == "unchecked"
    assert host_health.state({"checked_at": 90, "last_ok": 90, "fail_since": 0, "reason": ""}, 100.0) == "ok"
    failing = {"checked_at": 90, "last_ok": 0, "fail_since": 50, "reason": "timed out"}
    assert host_health.state(failing, 100.0) == "failing"
    assert host_health.state(failing, 50 + host_health.QUIET_AFTER) == "quiet"


def test_check_asks_each_remote_placeable_host_and_records_the_answer(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict(POOL)
    runner = ScriptRunner({"big": (0, "", ""), "gone": (255, "", "ssh: connect to host gone: No route\n")})
    entries = host_health.check(tmp_path, pool, now=1000.0, runner=runner)
    assert entries["big"] == {"checked_at": 1000.0, "last_ok": 1000.0, "fail_since": 0.0, "reason": ""}
    assert entries["gone"] == {"checked_at": 1000.0, "last_ok": 0.0, "fail_since": 1000.0,
                               "reason": "ssh: connect to host gone: No route"}
    assert len(runner.calls) == 2 and all(c[-1] == "bash -c true" for c in runner.calls)
    assert json.loads((tmp_path / ".coscience" / "host-health.json").read_text()) == entries
    assert "local" not in entries


def test_a_host_is_checked_at_most_once_a_minute_and_failing_keeps_its_start(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict(POOL)
    down = ScriptRunner({"big": (255, "", "timed out"), "gone": (255, "", "timed out")})
    host_health.check(tmp_path, pool, now=1000.0, runner=down)
    host_health.check(tmp_path, pool, now=1030.0, runner=down)
    assert len(down.calls) == 2
    entries = host_health.check(tmp_path, pool, now=1000.0 + host_health.CHECK_INTERVAL, runner=down)
    assert len(down.calls) == 4 and entries["big"]["fail_since"] == 1000.0
    back = ScriptRunner({"big": (0, "", ""), "gone": (255, "", "timed out")})
    entries = host_health.check(tmp_path, pool, now=1200.0, runner=back)
    assert entries["big"]["fail_since"] == 0.0 and entries["big"]["last_ok"] == 1200.0


def test_nothing_is_checked_while_remote_placement_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("COSCIENCE_ALLOW_REMOTE", raising=False)
    runner = ScriptRunner({})
    assert host_health.check(tmp_path, ResourcePool.from_dict(POOL), now=1000.0, runner=runner) == {}
    assert runner.calls == []


def test_a_removed_host_leaves_the_health_file(tmp_path, every_host_placeable):
    host_health.check(tmp_path, ResourcePool.from_dict(POOL), now=1000.0,
                      runner=ScriptRunner({"big": (0, "", ""), "gone": (0, "", "")}))
    smaller = ResourcePool.from_dict({"cpu": 4, "hosts": {"big": POOL["hosts"]["big"]}})
    assert set(host_health.check(tmp_path, smaller, now=2000.0, runner=ScriptRunner({"big": (0, "", "")}))) == {"big"}


def test_an_unreadable_health_file_reads_as_nothing_checked(tmp_path):
    (tmp_path / ".coscience").mkdir()
    (tmp_path / ".coscience" / "host-health.json").write_text("{not json")
    assert host_health.load(tmp_path) == {}


def test_drained_and_closed_hosts_take_no_new_grants(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {
        "big": {"ssh": "big", "capacity": {"cpu": 16}, "drain": True},
        "gone": {"ssh": "gone", "capacity": {"cpu": 8}}}})
    assert pool.host("big").drain and not pool.host("gone").drain
    pool.closed = {"gone": "quiet"}
    assert [h.name for h in pool.grantable_hosts(None)] == ["local"]
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    assert led.fit({"cpu": 4}) is None and not led.can_fit({"cpu": 4})
    assert led.fit({"cpu": 1}) == ("local", [])


def test_a_sprint_pinned_to_a_quiet_host_waits_and_its_lease_is_kept(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict(POOL)
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    assert led.acquire("running", {"cpu": 2}, now=0.0, ttl=60.0, host="big").host == "big"
    pool.closed = {"big": "quiet"}
    pol = SchedulerPolicy(aging_interval=0.0)
    assert pol.select_grants([_sprint("s1", {"cpu": 2})], {"s1": 0.0}, led, now=0.0, pinned={"s1": "big"}) == []
    assert led.lease_for("running").host == "big"


def test_no_yield_victim_is_chosen_on_a_closed_host(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 0, "workers": 4,
                                   "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 4}}}})
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    led.acquire("low", {"cpu": 4}, now=0.0, ttl=60.0, priority=0)
    pool.closed = {"big": "quiet"}
    victims = SchedulerPolicy().select_yield_victims(_sprint("hi", {"cpu": 4}, prio=5), 5, led, {"low"})
    assert victims == []


def test_the_dispatcher_closes_a_quiet_host_for_the_cycle(substrate, every_host_placeable):
    from coscience.dispatcher import Dispatcher
    from tests.test_dispatcher import FakeAgent
    health = substrate.repo_root / ".coscience" / "host-health.json"
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text(json.dumps({"big": {"checked_at": 10.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "down"}}))
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict(POOL),
                      host_runner=ScriptRunner({"big": (255, "", "down"), "gone": (0, "", "")}))
    disp.run_one_cycle(now=1.0 + host_health.QUIET_AFTER)
    assert disp.ledger.pool.closed == {"big": "quiet"}
```

Before writing the last test, read `tests/test_dispatcher.py` for the name of its fake agent and the `substrate` fixture (`tests/conftest.py`); use whatever exists and adjust the import — keep the assertion.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_health.py -q`
Expected: FAIL — `ImportError` (no `coscience.host_health`); after it exists, `Host` has no `drain`, `ResourcePool` no `grantable_hosts`, `Dispatcher` no `host_runner`.

- [ ] **Step 3: Implement**

Create `src/coscience/host_health.py`:

```python
"""Is each remote host answering? (O7)

The dispatch loop asks every placeable remote host `bash -c true` over key-only SSH at
most once a minute and records the answer in `.coscience/host-health.json`, which the
HTTP server reads too. A host failing for QUIET_AFTER is quiet: it takes no new grants.
Nothing here kills a job or releases a lease (spec §7)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from coscience.host_probe import Runner, ssh_argv, subprocess_runner

HEALTH_FILE = ".coscience/host-health.json"
CHECK_INTERVAL = 60.0
QUIET_AFTER = 1800.0
CHECK_TIMEOUT = 20.0
default_runner: Runner = subprocess_runner


def _path(repo_root) -> Path:
    return Path(repo_root) / HEALTH_FILE


def load(repo_root) -> dict[str, dict]:
    try:
        data = json.loads(_path(repo_root).read_text())
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(repo_root, entries: dict[str, dict]) -> None:
    path = _path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(entries, indent=2, sort_keys=True))
    os.replace(tmp, path)


def state(entry: dict | None, now: float) -> str:
    if not entry:
        return "unchecked"
    since = float(entry.get("fail_since") or 0.0)
    if not since:
        return "ok"
    return "quiet" if now - since >= QUIET_AFTER else "failing"


def quiet(entries: dict[str, dict], now: float) -> set[str]:
    return {name for name, entry in entries.items() if state(entry, now) == "quiet"}


def check(repo_root, pool, now: float, runner: Runner | None = None) -> dict[str, dict]:
    """Ask every placeable remote host that is due; keep entries only for hosts still
    in the pool. Returns the entries after this round."""
    runner = runner or default_runner
    old = load(repo_root)
    names = {h.name for h in pool.hosts if h.ssh}
    entries = {name: entry for name, entry in old.items() if name in names}
    for host in pool.hosts:
        if not host.ssh or not host.placeable:
            continue
        prev = entries.get(host.name, {})
        if now - float(prev.get("checked_at") or 0.0) < CHECK_INTERVAL and prev:
            continue
        try:
            code, _, err = runner(ssh_argv(host.ssh) + ["bash -c true"], None, CHECK_TIMEOUT)
        except ValueError as exc:
            code, err = 255, str(exc)
        if code == 0:
            entries[host.name] = {"checked_at": now, "last_ok": now, "fail_since": 0.0, "reason": ""}
        else:
            lines = [line for line in str(err).strip().splitlines() if line.strip()]
            entries[host.name] = {
                "checked_at": now, "last_ok": float(prev.get("last_ok") or 0.0),
                "fail_since": float(prev.get("fail_since") or 0.0) or now,
                "reason": lines[-1] if lines else f"ssh exited {code}"}
    if entries != old:
        _save(repo_root, entries)
    return entries
```

In `src/coscience/resources.py`:
1. `Host` gains `drain: bool = False   # takes no new grants; running work finishes` (after `notes`).
2. `ResourcePool` gains `closed: dict[str, str] = field(default_factory=dict)` with the comment `# Hosts that take no new grants this cycle and why (e.g. quiet); set by the dispatcher, never parsed.`, and:

```python
    def grantable_hosts(self, program: str | None) -> list[Host]:
        """Hosts a new grant may land on: placeable, allowed, not drained, not closed."""
        return [h for h in self.placeable_hosts(program) if not h.drain and h.name not in self.closed]
```

3. `_parse_host` passes `drain=bool(spec.get("drain", False))`.

In `src/coscience/ledger.py`: `fit` iterates `self.pool.grantable_hosts(program)` instead of `placeable_hosts`, except when called with `readopt=True` (new keyword on `fit` and `acquire`), which keeps `placeable_hosts`: re-adopting a leaseless sprint whose agent or job is still running must succeed on a drained or quiet host, or reconcile would clear its job's tracking (found in Task 1's review). `select_grants` gains `readopt=frozenset()` (ids of live sprints) and the dispatcher passes the ids whose agent is running or whose progress has a `job_token`. `can_fit` with `host=None` becomes `return self.fit(amounts) is not None` — a request must fit on one grantable host, and the old pool-wide sum would say a drained or quiet host's room is still there. Grep every `can_fit(` caller first and confirm none relies on the pool-wide sum; with only `local` placeable the two answers are identical. A named host in `can_fit` stays as it is.

In `src/coscience/scheduler.py` `select_yield_victims`: iterate `ledger.pool.grantable_hosts(candidate.program)`.

In `src/coscience/dispatcher.py`:
1. `from coscience import host_health`.
2. `Dispatcher.__init__(..., wiki_agent=None, host_runner=None)`: `self._host_runner = host_runner`.
3. In `run_one_cycle`, right after `self.ledger.expire(now)`:

```python
        # Which remote hosts answer. A quiet one takes no new grants this cycle; its
        # leases, jobs and liveness are untouched (spec §7).
        entries = host_health.check(self.substrate.repo_root, self.ledger.pool, now,
                                    runner=self._host_runner)
        self.ledger.pool.closed = {name: "quiet" for name in host_health.quiet(entries, now)}
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_health.py tests/test_remote_placement.py tests/test_resources_hosts.py tests/test_scheduler_hosts.py tests/test_dispatcher.py tests/test_dispatcher_hibernate.py tests/test_dispatcher_workers.py -q`
Expected: PASS

---

### Task 2: Stranded leases, an isolated beat, and a stop that failed is named

**Files:**
- Modify: `src/coscience/ledger.py`, `src/coscience/dispatcher.py`, `src/coscience/worker.py`
- Test: `tests/test_host_health.py` (append)

**Interfaces:**
- Consumes: `ResourcePool.grantable_hosts` (Task 1); `Worker._last_terminate_ok` and the could-not-stop note (O6).
- Produces:
  - `Ledger.stranded() -> list[Lease]` — leases whose host is not in the pool or not placeable
  - pool-wide `Ledger.used()` (host=None) counts a stranded lease's platform keys only
  - `CycleReport.beat_errors: list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_host_health.py`:

```python
def test_a_lease_on_a_removed_host_is_stranded_and_leaves_pool_wide_use(tmp_path, every_host_placeable):
    led = Ledger(ResourcePool.from_dict(POOL), tmp_path / "leases.json")
    led.load()
    led.acquire("s1", {"cpu": 8, "workers": 1}, now=0.0, ttl=600.0, host="gone")
    led.save()
    smaller = Ledger(ResourcePool.from_dict({"cpu": 4, "workers": 4, "hosts": {"big": POOL["hosts"]["big"]}}),
                     tmp_path / "leases.json")
    smaller.load()
    assert [l.sprint_id for l in smaller.stranded()] == ["s1"]
    assert smaller.used()["cpu"] == 0.0 and smaller.used()["workers"] == 1.0
    assert smaller.available()["cpu"] == 20.0


def test_one_failing_beat_does_not_stop_the_others(substrate, monkeypatch):
    from coscience.dispatcher import Dispatcher
    from tests.test_dispatcher import FakeAgent
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict({"cpu": 4}))
    for sid in ("s1", "s2"):
        substrate.save_sprint(_sprint(sid, {"cpu": 1}, status=SprintStatus.EXECUTING))
        disp.ledger.acquire(sid, {"cpu": 1}, now=0.0, ttl=600.0)
    disp.ledger.save()
    beaten = []

    def beat(sprint):
        beaten.append(sprint.id)
        if sprint.id == "s1":
            raise RuntimeError("boom")
        return "idle"
    monkeypatch.setattr(disp.worker, "run_sprint_beat", beat)
    report = disp.run_one_cycle(now=1.0)
    assert sorted(beaten) == ["s1", "s2"] and report.beat_errors == ["s1"]
    assert "boom" in substrate.load_progress("s1").last_error
    assert disp.ledger.lease_for("s1") is not None
```

Before writing the second test, read `tests/test_dispatcher.py` for how an EXECUTING sprint is saved under a program (the `substrate` fixture may need a program id on the sprint) and follow it; keep the assertions.

Append a stop-path test modelled on O6's `test_a_timed_out_job_the_platform_could_not_stop_leaves_a_note` in `tests/test_remote_jobs.py` (read it first): a sprint with a remote `job_token` `"gpu1:4242::"` and `job_host` `"gpu1"`, `Worker.stop_sprint(sprint)` → no `kill` in the runner calls, `progress.job_token == ""`, and the saved `collect_note` contains `The platform could not stop the job on gpu1`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_health.py tests/test_remote_jobs.py -q`
Expected: FAIL — no `stranded`, the RuntimeError escapes `run_one_cycle`, no note on stop.

- [ ] **Step 3: Implement**

In `src/coscience/ledger.py`:

```python
    def _stranded(self, lease: Lease) -> bool:
        host = self.pool.host(lease.host)
        return host is None or not host.placeable

    def stranded(self) -> list[Lease]:
        """Leases on a host the pool no longer has or no longer places on. They keep
        their sprint's work alive but no longer count against pool-wide totals, which
        would otherwise go negative."""
        return [l for l in self._leases.values() if self._stranded(l)]
```

In `used`, when `host is None`, skip a stranded lease's non-platform keys and cards: change `on_host = host is None or lease.host == host` to

```python
            on_host = (lease.host == host) if host is not None else not self._stranded(lease)
```

and keep the platform-key rule (platform keys still count for every lease).

In `src/coscience/dispatcher.py`: `CycleReport.beat_errors: list[str] = field(default_factory=list)   # sprints whose beat raised`. In the beat loop:

```python
            try:
                outcome = self.worker.run_sprint_beat(sprint)
            except Exception as exc:          # one sprint's fault must not stall every other sprint
                progress = self.substrate.load_progress(sprint.id)
                progress.last_error = f"beat failed: {type(exc).__name__}: {exc}"
                self.substrate.save_progress(progress)
                report.beat_errors.append(sprint.id)
                outcome = None
```

The lease is still renewed after a failed beat (a crash must not let reconcile kill live work); `report.beaten` counts it; completion handling runs only when `outcome == BeatOutcome.COMPLETED`.

In `src/coscience/worker.py` `stop_sprint`: after `self._terminate(progress.job_token)`, when the token is remote and `self._last_terminate_ok` is False, add the same could-not-stop line O6's timed-out branch writes (reuse its helper or constant — extract one if the text is inline) to `progress.collect_note`, before clearing the token.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_health.py tests/test_remote_jobs.py tests/test_dispatcher.py tests/test_dispatcher_reconcile.py tests/test_ledger*.py -q`
Expected: PASS

---

### Task 3: The service shows each host's health and use, and drains and removes hosts

**Files:**
- Modify: `src/coscience/service.py`, `src/coscience/http_api.py`, `src/coscience/pm_agent.py`, `src/coscience/pm_claude.py`
- Test: `tests/test_host_admin.py` (create); shape tests in `tests/test_http_api.py`, `tests/test_mcp_server.py` if they compare the exact ledger keys

**Interfaces:**
- Consumes: `host_health.load/state` (Task 1), `Host.drain`, `Ledger.stranded` (Task 2).
- Produces:
  - `ledger_status()["hosts"][i]` gains `drain: bool`, `health: {"state", "checked_at", "last_ok", "fail_since", "reason"}` (`state` is `"local"` for this machine), `used: dict` (that host's non-platform amounts in use), `leases: int`, `leftover: [{"sprint_id", "status", "path"}]`
  - `ledger_status()["stranded"]: [{"sprint_id", "host"}]`
  - `Service.set_host_drain(name: str, drain: bool) -> dict` (ledger status); `Service.remove_host(name: str) -> dict`
  - `PUT /api/hosts/{name}/drain` body `{"drain": bool}`; `DELETE /api/hosts/{name}`
  - PM compute host entries gain `"closed": "draining" | "not answering" | ""`; the COMPUTE line says `— takes no new work (<reason>)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_host_admin.py`:

```python
"""O7: per-host health and use on the Compute page; drain and remove."""
import json

import pytest
import yaml

from coscience.ledger import Ledger
from coscience.models import ProgressState, Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service

POOL_YAML = ("cpu: 4\nworkers: 4\nhosts:\n"
             "  big:\n    ssh: big\n    run_root: ~/runs\n    capacity: {cpu: 16, memory_gb: 64}\n")


def _svc(tmp_path, text=POOL_YAML):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)
    return Service(tmp_path)


def _lease(tmp_path, sid, amounts, host):
    led = Ledger(ResourcePool.from_yaml(tmp_path / ".coscience" / "resources.yaml"),
                 tmp_path / ".coscience" / "leases.json")
    led.load()
    assert led.acquire(sid, amounts, now=0.0, ttl=1e9, host=host) is not None
    led.save()


def _host(status, name):
    return next(h for h in status["hosts"] if h["name"] == name)


def test_each_host_reports_health_use_and_leases(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 6, "memory_gb": 10}, "big")
    (tmp_path / ".coscience" / "host-health.json").write_text(json.dumps(
        {"big": {"checked_at": 5.0, "last_ok": 5.0, "fail_since": 0.0, "reason": ""}}))
    status = svc.ledger_status()
    big = _host(status, "big")
    assert big["health"]["state"] == "ok" and big["drain"] is False
    assert big["used"] == {"cpu": 6.0, "memory_gb": 10.0} and big["leases"] == 1
    assert _host(status, "local")["health"]["state"] == "local"
    assert status["stranded"] == []


def test_a_host_that_stopped_answering_long_ago_reads_quiet(tmp_path, every_host_placeable, monkeypatch):
    from coscience import host_health
    svc = _svc(tmp_path)
    (tmp_path / ".coscience" / "host-health.json").write_text(json.dumps(
        {"big": {"checked_at": 100.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "No route"}}))
    monkeypatch.setattr("time.time", lambda: 2.0 + host_health.QUIET_AFTER)
    assert _host(svc.ledger_status(), "big")["health"] == {
        "state": "quiet", "checked_at": 100.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "No route"}


def test_draining_is_written_to_the_pool_file_and_undone(tmp_path):
    svc = _svc(tmp_path)
    assert _host(svc.set_host_drain("big", True), "big")["drain"] is True
    assert yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["big"]["drain"] is True
    assert _host(svc.set_host_drain("big", False), "big")["drain"] is False
    assert "drain" not in yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["big"]


def test_this_machine_and_unknown_hosts_cannot_be_drained_or_removed(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="Pause"):
        svc.set_host_drain("local", True)
    with pytest.raises(NotFoundError):
        svc.set_host_drain("nope", True)
    with pytest.raises(ValueError, match="Pause"):
        svc.remove_host("local")
    with pytest.raises(NotFoundError):
        svc.remove_host("nope")


def test_a_host_is_removed_only_when_drained_and_empty(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 2}, "big")          # granted before the drain: a drained host takes none
    with pytest.raises(ValueError, match="drain big before removing it"):
        svc.remove_host("big")
    svc.set_host_drain("big", True)
    with pytest.raises(ValueError, match="1 sprint still holds a lease on big"):
        svc.remove_host("big")
    (tmp_path / ".coscience" / "leases.json").write_text("[]")
    status = svc.remove_host("big")
    assert [h["name"] for h in status["hosts"]] == ["local"]
    assert "big" not in (yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text()).get("hosts") or {})


def test_finished_sprints_list_the_run_directories_they_left(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    sub = svc.substrate
    for sid, status in (("s1", SprintStatus.DONE), ("s2", SprintStatus.EXECUTING), ("s3", SprintStatus.FAILED)):
        sub.save_sprint(Sprint(id=sid, status=status, goals="g", plan=["a"]))
        sub.save_progress(ProgressState(sprint_id=sid, host="big"))
    assert _host(svc.ledger_status(), "big")["leftover"] == [
        {"sprint_id": "s1", "status": "done", "path": "~/runs/s1"},
        {"sprint_id": "s3", "status": "failed", "path": "~/runs/s3"}]


def test_a_lease_on_a_removed_host_is_listed_as_stranded(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 2}, "big")
    (tmp_path / ".coscience" / "resources.yaml").write_text("cpu: 4\nworkers: 4\n")
    assert svc.ledger_status()["stranded"] == [{"sprint_id": "s1", "host": "big"}]


def test_a_sprint_pinned_to_a_draining_host_says_so(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    svc.set_host_drain("big", True)
    sprint = Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["a"], resources_required={"cpu": 2})
    svc.substrate.save_sprint(sprint)
    svc.substrate.save_progress(ProgressState(sprint_id="s1", host="big"))
    assert "big is draining" in svc._unrunnable(sprint)


def test_the_http_routes_drain_and_remove(client, every_host_placeable):
    from tests.test_http_api import write_pool   # see note below
    write_pool(POOL_YAML)
    r = client.put("/api/hosts/big/drain", json={"drain": True})
    assert r.status_code == 200 and _host(r.json(), "big")["drain"] is True
    assert client.delete("/api/hosts/nope").status_code == 404
    assert client.put("/api/hosts/local/drain", json={"drain": True}).status_code == 422
    r = client.delete("/api/hosts/big")
    assert r.status_code == 200 and [h["name"] for h in r.json()["hosts"]] == ["local"]
```

Before writing the HTTP test, read `tests/test_http_api.py` for its `client` fixture and how it points the service at a substrate; replace the `write_pool` import with however that file writes `resources.yaml` for its capacity tests. Keep the assertions. Before writing the service tests, check `Service(tmp_path)` is how `tests/test_host_onboarding.py` builds a service and how it saves sprints (a program id may be required); follow it.

Append to `tests/test_pm_compute.py` (read its existing COMPUTE tests first and use the same `PMContext` shape):

```python
def test_a_host_that_takes_no_new_work_says_so():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "big", "capacity": {"cpu": 16.0}, "gpus": [], "held": {}, "closed": "draining"}])
    assert "big" in render_compute(ctx) and "takes no new work (draining)" in render_compute(ctx)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_admin.py tests/test_pm_compute.py -q`
Expected: FAIL — missing fields, methods and routes.

- [ ] **Step 3: Implement**

In `src/coscience/service.py`:

1. `ledger_status`: `health = host_health.load(self.repo_root)` and `now = time.time()`. Build, once, the leftover map only when the pool has a host with `ssh`: for each sprint from `self.substrate.iter_sprints()` whose status is `DONE`, `CANCELED` or `FAILED`, load its progress; when `progress.host` names a host with a `run_root`, add `{"sprint_id", "status": str(status), "path": f"{run_root.rstrip('/')}/{sprint_id}"}` to that host's list (sorted by sprint id). Each host dict gains:

```python
                 "drain": h.drain,
                 "health": ({"state": "local", "checked_at": 0.0, "last_ok": 0.0, "fail_since": 0.0, "reason": ""}
                            if h.is_local else
                            {"state": host_health.state(health.get(h.name), now),
                             **{k: health.get(h.name, {}).get(k, default) for k, default in
                                (("checked_at", 0.0), ("last_ok", 0.0), ("fail_since", 0.0), ("reason", ""))}}),
                 "used": {k: v for k, v in ledger.used(h.name).items() if k not in PLATFORM_KEYS and v},
                 "leases": sum(1 for l in ledger.all_leases() if l.host == h.name),
                 "leftover": leftover.get(h.name, []),
```

and the status gains `"stranded": [{"sprint_id": l.sprint_id, "host": l.host} for l in ledger.stranded()]`.

2. A helper that loads `resources.yaml` and returns `(loaded, holder_hosts_mapping)` the way `confirm_host` does (factor it out of `confirm_host` and use it there too, with the same error messages). Then:

```python
    def set_host_drain(self, name: str, drain: bool) -> dict:
        """Stop new grants on a host (running work finishes), or take it back."""
        if name == LOCAL:
            raise ValueError("this machine is not drained; use Pause to stop new work here")
        loaded, hosts = self._resources_hosts()
        if name not in hosts:
            raise NotFoundError(f"no host {name!r} in the pool")
        if drain:
            hosts[name]["drain"] = True
        else:
            hosts[name].pop("drain", None)
        _parse_host(name, hosts[name])
        self._write_resources(loaded)
        self.substrate.commit(f"host {name} {'drained' if drain else 'takes work again'}")
        return self.ledger_status()

    def remove_host(self, name: str) -> dict:
        """Take a drained host with no leases out of the pool. Its probe record and any
        run directories on the host itself are left alone."""
        if name == LOCAL:
            raise ValueError("this machine cannot be removed; use Pause to stop new work here")
        loaded, hosts = self._resources_hosts()
        if name not in hosts:
            raise NotFoundError(f"no host {name!r} in the pool")
        if not hosts[name].get("drain"):
            raise ValueError(f"drain {name} before removing it, so no new sprint lands there meanwhile")
        holding = [l.sprint_id for l in self._ledger().all_leases() if l.host == name]
        if holding:
            raise ValueError(f"{len(holding)} sprint{' still holds' if len(holding) == 1 else 's still hold'} "
                             f"a lease on {name}: wait for {'it' if len(holding) == 1 else 'them'} to finish, "
                             "or stop them")
        del hosts[name]
        self._write_resources(loaded)
        self.substrate.commit(f"host {name} removed from the pool")
        return self.ledger_status()
```

(Import `host_health`, `LOCAL`, `_parse_host` as the file already imports its neighbours.)

3. `_unrunnable`: when the sprint is pinned (`progress.host`) to a host that is in the pool and `drain` is set, return `f"pinned to {name}, which is draining: take the host back or stop the sprint"`; when the health file says that host is `quiet`, return `f"pinned to {name}, which has not answered since {time.strftime('%Y-%m-%d %H:%M', time.localtime(fail_since))}: its work is kept and it takes no new grants"`. Put these before the existing capacity checks for a pinned host.

In `src/coscience/pm_agent.py` `_compute`: each per-host dict gains `"closed": "draining" if h.drain else ("not answering" if host_health.state(health.get(h.name), time.time()) == "quiet" else "")` with `health = host_health.load(substrate.repo_root)`. In `src/coscience/pm_claude.py` `render_compute`, a host with a non-empty `closed` appends ` — takes no new work ({closed})` to its line.

In `src/coscience/http_api.py`, beside the host routes:

```python
    class HostDrainIn(BaseModel):
        drain: bool

    @api.put("/hosts/{name}/drain")
    def set_host_drain(name: str, body: HostDrainIn) -> dict:
        try:
            return service.set_host_drain(name, body.drain)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.delete("/hosts/{name}")
    def remove_host(name: str) -> dict:
        try:
            return service.remove_host(name)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
```

(Declare `HostDrainIn` where the file declares its other request models.) These routes are not gated by `COSCIENCE_ALLOW_ONBOARDING`: they make no SSH call.

If `tests/test_http_api.py` or `tests/test_mcp_server.py` compare the exact ledger key set, add `stranded` (and the new host keys) there.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_admin.py tests/test_pm_compute.py tests/test_host_onboarding.py tests/test_http_api.py tests/test_mcp_server.py tests/test_service_capacity.py tests/test_remote_placement.py -q`
Expected: PASS

---

### Task 4: The servers card shows health, use and leftovers, and drains and removes

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/components/HostsCard.tsx`, `frontend/src/views/Ledger.tsx`
- Test: `frontend/src/components/HostsCard.test.tsx`

**Interfaces:**
- Consumes: the ledger fields and routes from Task 3.
- Produces: `api.drainHost(name, drain) -> Promise<Ledger>`, `api.removeHost(name) -> Promise<Ledger>`; `HostsCard({ hosts, errors, stranded })`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/HostsCard.test.tsx`:
1. The `vi.mock("../api", …)` factory gains `drainHost: vi.fn()` and `removeHost: vi.fn()`; import `{ api }` from `"../api"`.
2. `LOCAL` gains `drain: false, health: { state: "local", checked_at: 0, last_ok: 0, fail_since: 0, reason: "" }, used: { cpu: 3 }, leases: 1, leftover: []`.
3. `REMOTE` gains `drain: false, health: { state: "quiet", checked_at: 1_700_000_100, last_ok: 1_699_990_000, fail_since: 1_700_000_000, reason: "No route to host" }, used: { cpu: 4 }, leases: 1, leftover: [{ sprint_id: "s9", status: "done", path: "~/coscience-runs/s9" }]` and its card `shared_gb: 6`.
4. `renderCard(errors = [], hosts = [LOCAL, REMOTE], stranded = [])` passes `stranded`.
5. Replace the "waits for remote launch" expectation with the health tests below, and append:

```tsx
  it("says how each server is answering", () => {
    renderCard();
    expect(screen.getByText(/not answering since/)).toBeTruthy();
    expect(screen.getByText(/No route to host/)).toBeTruthy();
    expect(screen.getByText(/takes no new work/)).toBeTruthy();
  });

  it("shows what is in use on each server, including shared VRAM", () => {
    renderCard();
    expect(screen.getByText("4 of 10 CPU cores")).toBeTruthy();
    expect(screen.getByText("GPU 0: 6 of 10.8 GB shared")).toBeTruthy();
  });

  it("lists run directories finished sprints left on a server", () => {
    renderCard();
    expect(screen.getByText(/~\/coscience-runs\/s9/)).toBeTruthy();
  });

  it("drains a server", async () => {
    vi.mocked(api.drainHost).mockResolvedValue({} as never);
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Drain gpu1" }));
    await waitFor(() => expect(api.drainHost).toHaveBeenCalledWith("gpu1", true));
  });

  it("removes only a drained server with nothing running", async () => {
    vi.mocked(api.removeHost).mockResolvedValue({} as never);
    const busy = { ...REMOTE, drain: true };
    renderCard([], [LOCAL, busy]);
    expect((screen.getByRole("button", { name: "Remove gpu1" }) as HTMLButtonElement).disabled).toBe(true);
    const idle = { ...REMOTE, drain: true, leases: 0 };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderCard([], [LOCAL, idle]);
    const buttons = screen.getAllByRole("button", { name: "Remove gpu1" });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(api.removeHost).toHaveBeenCalledWith("gpu1"));
  });

  it("warns about leases on servers that left the pool", () => {
    renderCard([], [LOCAL, REMOTE], [{ sprint_id: "s4", host: "old1" }]);
    expect(screen.getByText(/s4 still holds a lease on old1/)).toBeTruthy();
  });

  it("offers no drain or remove for this machine", () => {
    renderCard();
    expect(screen.queryByRole("button", { name: "Drain local" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove local" })).toBeNull();
  });
```

Add `waitFor` to the testing-library import.

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/HostsCard.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

`frontend/src/api.ts`:

```ts
export interface HostHealth {
  state: "local" | "unchecked" | "ok" | "failing" | "quiet";
  checked_at: number; last_ok: number; fail_since: number; reason: string;
}
export interface HostLeftover { sprint_id: string; status: string; path: string }
export interface StrandedLease { sprint_id: string; host: string }
```

`LedgerHost` gains `drain?: boolean; health?: HostHealth; used?: Record<string, number>; leases?: number; leftover?: HostLeftover[]`; `Ledger` gains `stranded?: StrandedLease[]`. The `api` object gains:

```ts
  drainHost: (name: string, drain: boolean) =>
    fetch(`/api/hosts/${encodeURIComponent(name)}/drain`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ drain }),
    }).then(j<Ledger>),
  removeHost: (name: string) =>
    fetch(`/api/hosts/${encodeURIComponent(name)}`, { method: "DELETE" }).then(j<Ledger>),
```

`frontend/src/components/HostsCard.tsx`:
- Props `{ hosts, errors, stranded = [] }`; `useQueryClient()`; an `error` state shown in red under the table.
- Exported `healthText(host)`: `local` → "takes work" (the Reached-by column already says "this machine"); `unchecked` → "not checked yet"; `ok` → "answering"; `failing` → `not answering since HH:MM — <reason>`; `quiet` → `not answering since HH:MM — <reason>; takes no new work`. When `host.drain`, append (or return, for an answering host) `draining — takes no new work`. `HH:MM` is `new Date(fail_since * 1000)` formatted with `toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })`. A host with no `health` field (older backend) falls back to the old `placeable ? "takes work" : "waits for remote launch"`.
- Exported `inUseText(host)`: for `cpu` and `memory_gb` present in `capacity`, `"<used> of <cap> CPU cores"` / `"<used> of <cap> GB memory"` (used 0 when absent); for each card, `whole` → `GPU <i>: in use`, `shared_gb > 0` → `GPU <i>: <shared_gb> of <vram_gb> GB shared`, else `GPU <i>: free`. Render each part as its own `<Text size="sm">` line in a new "In use" column.
- The Status column shows `healthText`. A new last column holds, for a host with `ssh` only: a `Button` labelled by `aria-label={`${drain ? "Take back" : "Drain"} ${name}`}` showing "Take back"/"Drain", calling `api.drainHost(name, !drain)`; and a `Button` `aria-label={`Remove ${name}`}` "Remove", `disabled={!host.drain || (host.leases ?? 0) > 0}` with a `title` saying why ("drain it first" / "sprints still hold leases here"), which asks `window.confirm(`Remove ${name} from the pool? Its run directories stay on the server.`)` and then calls `api.removeHost(name)`. Both invalidate `["ledger"]` on success and set `error` on failure.
- Under a host's row, when `leftover` is non-empty, a full-width row with dimmed text: `Left on the server by finished sprints (remove by hand): <path> (<status>), …`.
- Under the table, for each stranded lease: red text `<sprint_id> still holds a lease on <host>, which is no longer in the pool: stop the sprint or add the server back.`

`frontend/src/views/Ledger.tsx`: pass `stranded={l.stranded ?? []}` to `HostsCard`.

- [ ] **Step 4: Run the tests**

Run (from `frontend/`): `npx vitest run src/components/HostsCard.test.tsx src/views/Ledger.test.tsx src/components/AddHostModal.test.tsx` then `npx tsc -b`
Expected: PASS, and tsc clean.

---

## Self-review notes

- Spec §7: checks retried each beat (here: at most once a minute), quiet after 30 minutes stops new grants, no job killed or lease released — Tasks 1–2. Escalation of sleeping sprints is O8 (Global Constraints).
- §10 O7: per-host page with probed facts (facts already on onboarding; health/use here), leftover run directories, drain and remove — Tasks 3–4.
- §11 O7 notes: removed-host leases no longer make `available` negative (stranded, Task 2); the stop path's missing note (Task 2); the rest stay open and are named in the spec: copy-back off the beat, a footprint record for collect paths outside the run root, the sprint list's progress reads, pre-O3 card-less GPU over-commitment display.
