# O6 — Place and Run Work on a Remote Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a sprint be placed on a confirmed remote host and have its detached jobs launched, watched, stopped and collected there, without ever killing a job because the host could not be reached.

**Architecture:** Placement already works per host (O2–O4); a remote host becomes placeable only when the deployment sets `COSCIENCE_ALLOW_REMOTE=1`. A sprint that has launched anything stays on its host (`ProgressState.host`), and the scheduler, ledger and dispatcher honour that pin. Following O1, the worker agent itself launches remote jobs over SSH into `<run_root>/<sprint-id>/` and declares them in `job.json` with `host` and `collect`. A new `remote_exec` module — runner-injected like `host_probe` — reads a job's identity on its host (`<host>:<pid>:<starttime>:<boot_id>`), tells alive from gone from lost from unknown, stops its process group, and copies declared paths back. The worker verifies a job at declare time, treats "unknown" as alive, marks a reboot as lost, copies outputs into the sprint's `collected/` folder before waking the agent and tells it so. The agent's instructions gain a host section with the SSH form of the detached-job protocol. The Compute page edits this machine's capacity, not the pool total, once remote hosts take work.

**Tech Stack:** Python 3.12, subprocess (ssh, rsync), pytest; React + Mantine + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§3 decisions, §6 Running work on a remote host, §7 Unreachable hosts and reboots, §10 row O6, §11 "Before O6 makes any remote host placeable")

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python`, prefixed `PYTHONPATH=src` in a worktree. Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Tests never run ssh or rsync: they inject a runner.
- A remote host is placeable only when `COSCIENCE_ALLOW_REMOTE=1` is set; without it nothing about placement changes.
- An ssh failure makes a job's state **unknown**, never dead: the job and its lease stay. A changed boot id means the host rebooted and the job is **lost**.
- A local job token stays `<pid>:<starttime>`; a remote job token is `<host>:<pid>:<starttime>:<boot_id>`. Existing progress files keep working.
- Remote commands go through `ssh <target> bash -c <one shell-quoted string>`; every value embedded in one is validated first (pid is an int, start time is digits, paths match the run-root safe form).
- Copying back never deletes anything on either side (no `--delete`), and never copies `~` or `/` as a whole.
- A sprint that has launched anything on a host is only ever re-granted on that host.
- `distributed` stays recorded-only: a request is still placed on one host (spanning hosts is not built here).
- Local sprints behave exactly as before.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/remote_exec.py` | create | remote token, identity, job state, terminate, collect |
| `src/coscience/resources.py` | modify | `REMOTE_ENV`, `Host.placeable`, `over_capacity(..., only_host=)`, `over_capacity_on` |
| `src/coscience/models.py` | modify | `ProgressState.host`, `job_host`, `job_collect`, `collect_note` |
| `src/coscience/substrate.py` | modify | persist the new progress fields |
| `src/coscience/ledger.py` | modify | `acquire(..., host=)` |
| `src/coscience/scheduler.py` | modify | `select_grants(..., pinned=)`, `select_yield_victims(..., pinned_host=)` |
| `src/coscience/dispatcher.py` | modify | pin grants, yields and the unrunnable check to a sprint's host; `_WorkerSlots.host`, `_WorkerSlots.ssh_for` |
| `src/coscience/service.py` | modify | unrunnable names the pinned or closest host; `ledger_status.local_capacity` |
| `src/coscience/executor.py` | modify | `ExecutionContext` host and collect fields |
| `src/coscience/worker.py` | modify | remote declare/verify, liveness, terminate, collect-before-wake, record the host |
| `src/coscience/claude_executor.py` | modify | host section; collect note in the assess section |
| `frontend/src/api.ts`, `frontend/src/views/Ledger.tsx` | modify | edit local capacity; show a lease's host |
| `tests/test_remote_exec.py` | create | module tests |
| `tests/test_remote_placement.py` | create | placement, pinning, unrunnable, local capacity |
| `tests/test_remote_jobs.py` | create | worker remote job flow and instructions |
| `tests/test_http_api.py`, `tests/test_mcp_server.py` | modify | ledger shape gains `local_capacity` |
| `frontend/src/views/Ledger.test.tsx` | modify | local capacity and lease host |

---

### Task 1: The remote job module

**Files:**
- Create: `src/coscience/remote_exec.py`, `tests/test_remote_exec.py`

**Interfaces:**
- Consumes: `host_probe.Runner`, `host_probe.ssh_argv`, `host_probe.subprocess_runner` (O5).
- Produces:
  - `RemoteToken(host: str, pid: int, starttime: str, boot_id: str)` (frozen dataclass; `str()` gives `host:pid:starttime:boot_id`)
  - `parse_token(token: str) -> RemoteToken | None` (None for a local or malformed token)
  - `read_identity(ssh_target, pid, runner=subprocess_runner) -> tuple[str, str, str] | None` — `(boot_id, state, starttime)`; state and starttime `""` when there is no such process; None when the host could not be asked (the host's answer is labelled `boot=<id>` / `proc=<state> <start>`, so an unreadable boot id is never mistaken for the process line)
  - `make_token(host, ssh_target, pid, runner=subprocess_runner) -> tuple[str, str]` — `(token, state)`, state `"alive" | "gone" | "unknown"`
  - `job_state(ssh_target, token: RemoteToken, runner=subprocess_runner) -> str` — `"alive" | "gone" | "lost" | "unknown"`
  - `terminate(ssh_target, token: RemoteToken, grace=2.0, runner=subprocess_runner) -> bool` — False without signalling anything when the token has no start time
  - `check_remote_path(path) -> str`
  - `collect(ssh_target, paths, local_dir, runner=subprocess_runner) -> list[dict]` — one `{"path", "ok", "detail"}` per path; a path whose last folder name repeats an earlier one is refused

- [ ] **Step 1: Write the failing tests**

Create `tests/test_remote_exec.py`:

```python
"""O6: remote jobs are identified, watched, stopped and collected over key-only SSH."""
import pytest

from coscience import remote_exec
from coscience.remote_exec import RemoteToken, check_remote_path, collect, job_state, make_token, parse_token


class ScriptRunner:
    """Answers calls in order; records every argv."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[str]] = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append(list(argv))
        return self.replies.pop(0) if self.replies else (0, "", "")


def test_parse_token_tells_remote_from_local():
    assert parse_token("gpu1:4242:777:boot-1") == RemoteToken("gpu1", 4242, "777", "boot-1")
    assert str(RemoteToken("gpu1", 4242, "777", "boot-1")) == "gpu1:4242:777:boot-1"
    assert parse_token("4242:777") is None
    assert parse_token("gpu1:abc:777:boot") is None
    assert parse_token("") is None


def test_a_declared_job_is_identified_on_its_host():
    runner = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    token, state = make_token("gpu1", "gpu1", 4242, runner=runner)
    assert (token, state) == ("gpu1:4242:777:boot-1", "alive")
    assert runner.calls[0][-1].startswith("bash -c ")
    assert "/proc/4242/stat" in runner.calls[0][-1]


@pytest.mark.parametrize("reply, state", [
    ((0, "boot=boot-1\n", ""), "gone"),
    ((0, "boot=boot-1\nproc=Z 777\n", ""), "gone"),
])
def test_a_job_that_is_not_running_at_declare_time_is_gone(reply, state):
    assert make_token("gpu1", "gpu1", 4242, runner=ScriptRunner(reply))[1] == state


def test_an_unreachable_host_at_declare_time_gives_a_token_without_identity():
    token, state = make_token("gpu1", "gpu1", 4242, runner=ScriptRunner((255, "", "ssh: connect to host")))
    assert (token, state) == ("gpu1:4242::", "unknown")


@pytest.mark.parametrize("reply, state", [
    ((0, "boot=boot-1\nproc=S 777\n", ""), "alive"),
    ((0, "boot=boot-1\nproc=S 999\n", ""), "gone"),        # the pid now belongs to another process
    ((0, "boot=boot-1\n", ""), "gone"),
    ((0, "boot=boot-2\nproc=S 777\n", ""), "lost"),        # the host rebooted
    ((255, "", "ssh: Connection timed out"), "unknown"),
    ((124, "", "timed out"), "unknown"),
])
def test_job_state_never_calls_an_unreachable_job_dead(reply, state):
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    assert job_state("gpu1", token, runner=ScriptRunner(reply)) == state


def test_a_token_without_identity_is_alive_while_its_pid_is():
    token = RemoteToken("gpu1", 4242, "", "")
    assert job_state("gpu1", token, runner=ScriptRunner((0, "boot=boot-9\nproc=S 1\n", ""))) == "alive"


def test_an_unreadable_boot_id_never_makes_a_live_job_lost():
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    assert job_state("gpu1", token, runner=ScriptRunner((0, "boot=\nproc=S 777\n", ""))) == "alive"


def test_an_answer_without_the_boot_label_is_unknown():
    token = RemoteToken("gpu1", 4242, "777", "boot-1")
    assert job_state("gpu1", token, runner=ScriptRunner((0, "S 777\n", ""))) == "unknown"


def test_terminate_checks_the_start_time_and_kills_the_group():
    runner = ScriptRunner((0, "", ""))
    assert remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "777", "boot-1"), runner=runner) is True
    command = runner.calls[0][-1]
    assert "kill -TERM" in command and "777" in command and "/proc/4242/stat" in command
    assert remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "777", "b"),
                                 runner=ScriptRunner((255, "", "no route"))) is False


def test_terminate_refuses_a_token_without_a_start_time():
    runner = ScriptRunner((0, "", ""))
    assert remote_exec.terminate("gpu1", RemoteToken("gpu1", 4242, "", ""), runner=runner) is False
    assert runner.calls == []


@pytest.mark.parametrize("path", ["~/runs/s1/work", "/data/runs/s1/work", "~/runs/s1/work/"])
def test_check_remote_path_accepts_a_safe_path(path):
    assert check_remote_path(path) == path.rstrip("/")


@pytest.mark.parametrize("path", ["~", "/", "~/", "~/a/../b", "~/a b", "relative/work", "$(id)", ""])
def test_check_remote_path_refuses_anything_else(path):
    with pytest.raises(ValueError, match="collect path"):
        check_remote_path(path)


def test_collect_copies_each_path_without_deleting_and_reports_each(tmp_path):
    runner = ScriptRunner((0, "", ""), (23, "", "rsync: link_stat failed: No such file\n"))
    results = collect("gpu1", ["~/runs/s1/work", "~/runs/s1/missing", "~"], tmp_path / "collected",
                      runner=runner)
    assert [r["ok"] for r in results] == [True, False, False]
    assert "No such file" in results[1]["detail"]
    assert "collect path" in results[2]["detail"]
    assert len(runner.calls) == 2                               # the refused path never ran
    first = runner.calls[0]
    assert first[:2] == ["rsync", "-a"] and "--delete" not in first
    assert first[-2:] == ["gpu1:~/runs/s1/work", f"{tmp_path / 'collected'}/"]
    assert (tmp_path / "collected").is_dir()


def test_collect_refuses_two_paths_with_the_same_folder_name(tmp_path):
    runner = ScriptRunner((0, "", ""))
    results = collect("gpu1", ["~/runs/s1/work", "~/runs/s2/work"], tmp_path / "collected", runner=runner)
    assert [r["ok"] for r in results] == [True, False]
    assert "folder name 'work'" in results[1]["detail"]
    assert len(runner.calls) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_remote_exec.py -q`
Expected: FAIL — `ImportError` (no `coscience.remote_exec`).

- [ ] **Step 3: Implement**

Create `src/coscience/remote_exec.py`:

```python
"""Remote job control over key-only SSH (O6).

The worker agent starts a remote job itself (O1); this module is what the platform
uses afterwards: read a job's identity on its host, tell alive from gone from lost
from unknown, stop its process group, and copy its declared outputs back. Every call
goes through an injectable runner, so tests never touch a network."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from coscience.host_probe import Runner, ssh_argv, subprocess_runner

REMOTE_TIMEOUT = 30.0
COLLECT_TIMEOUT = 600.0
_SAFE_PATH = re.compile(r"^(~/[A-Za-z0-9._/-]+|/[A-Za-z0-9._/-]+)$")


@dataclass(frozen=True)
class RemoteToken:
    host: str
    pid: int
    starttime: str       # "" when it could not be read at declare time
    boot_id: str         # "" when it could not be read at declare time

    def __str__(self) -> str:
        return f"{self.host}:{self.pid}:{self.starttime}:{self.boot_id}"


def parse_token(token: str) -> RemoteToken | None:
    """A remote token `<host>:<pid>:<starttime>:<boot_id>`, or None for a local
    `<pid>:<starttime>` token or anything malformed. Host names never contain ':'."""
    parts = str(token or "").split(":")
    if len(parts) != 4 or not parts[0]:
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    return RemoteToken(parts[0], pid, parts[2], parts[3]) if pid > 0 else None


def _run(runner: Runner, ssh_target: str, command: str) -> tuple[int, str, str]:
    return runner(ssh_argv(ssh_target) + [f"bash -c {shlex.quote(command)}"], None, REMOTE_TIMEOUT)


def read_identity(ssh_target: str, pid: int, runner: Runner = subprocess_runner) -> tuple[str, str, str] | None:
    """(boot_id, state, starttime) of `pid` on the host — state and starttime "" when
    there is no such process — or None when the host could not be asked. Each line the
    remote command prints is labelled, so a boot id that could not be read (blank after
    `boot=`) is never mistaken for a `proc=` line or for a missing answer."""
    pid = int(pid)
    command = (
        'printf \'boot=%s\\n\' "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"; '
        f"if [ -r /proc/{pid}/stat ]; then "
        f"printf 'proc=%s\\n' \"$(sed 's/.*) //' /proc/{pid}/stat | cut -d' ' -f1,20)\"; fi"
    )
    code, out, _ = _run(runner, ssh_target, command)
    if code != 0:
        return None
    boot_id = None
    state, starttime = "", ""
    for line in out.splitlines():
        if line.startswith("boot="):
            boot_id = line[len("boot="):].strip()
        elif line.startswith("proc="):
            fields = line[len("proc="):].strip().split()
            if len(fields) == 2 and fields[1].isdigit():
                state, starttime = fields
    if boot_id is None:
        return None
    return boot_id, state, starttime


def make_token(host: str, ssh_target: str, pid: int, runner: Runner = subprocess_runner) -> tuple[str, str]:
    """(token, state) for a job the agent just declared. State is "alive", "gone" (not
    running on its host), or "unknown" (the host could not be asked; the token then
    carries no identity and liveness falls back to the pid)."""
    identity = read_identity(ssh_target, pid, runner)
    if identity is None:
        return str(RemoteToken(host, int(pid), "", "")), "unknown"
    boot_id, state, starttime = identity
    token = str(RemoteToken(host, int(pid), starttime, boot_id))
    if not starttime or state == "Z":
        return token, "gone"
    return token, "alive"


def job_state(ssh_target: str, token: RemoteToken, runner: Runner = subprocess_runner) -> str:
    """"alive"; "gone" (exited, or its pid now belongs to another process); "lost" (the
    host rebooted since the job was declared); or "unknown" (the host could not be
    asked — never to be treated as dead)."""
    identity = read_identity(ssh_target, token.pid, runner)
    if identity is None:
        return "unknown"
    boot_id, state, starttime = identity
    if token.boot_id and boot_id and boot_id != token.boot_id:
        return "lost"
    if not starttime or state == "Z":
        return "gone"
    if token.starttime and starttime != token.starttime:
        return "gone"
    return "alive"


def terminate(ssh_target: str, token: RemoteToken, grace: float = 2.0,
              runner: Runner = subprocess_runner) -> bool:
    """Stop the job's process group on its host — TERM, then KILL after `grace` — but
    only while its pid still carries the declared start time. With no start time to
    verify, it never signals: returns False without calling the runner. Also False
    when the host could not be asked."""
    if not token.starttime.isdigit():
        return False
    pid = int(token.pid)
    same_process = f'[ "$(sed "s/.*) //" /proc/{pid}/stat | cut -d" " -f20)" = "{token.starttime}" ] || exit 0; '
    command = (f"[ -r /proc/{pid}/stat ] || exit 0; {same_process}"
               f'pg=$(ps -o pgid= -p {pid} | tr -d " "); [ -n "$pg" ] || exit 0; '
               f'kill -TERM -- -"$pg" 2>/dev/null; sleep {float(grace):g}; '
               f'kill -KILL -- -"$pg" 2>/dev/null; exit 0')
    code, _, _ = _run(runner, ssh_target, command)
    return code == 0


def check_remote_path(path: str) -> str:
    """A path the platform may copy from a host: under ~/ or absolute, in safe
    characters, no `..`, and never the whole home directory or root."""
    value = str(path or "").strip()
    trimmed = value.rstrip("/")
    if not _SAFE_PATH.match(trimmed) or ".." in trimmed.split("/"):
        raise ValueError(f"collect path {path!r} must be under ~/ or absolute, using only letters, "
                         "digits, '.', '_', '-' and '/'")
    return trimmed


def collect(ssh_target: str, paths: list[str], local_dir, runner: Runner = subprocess_runner) -> list[dict]:
    """Copy each declared path from the host into `local_dir`, never deleting anything
    on either side. All paths land flat in `local_dir` (the worker's wake note names
    `<dest>/<last component>`), so a path whose last component repeats an earlier one
    in the same call is refused — it would silently overwrite that path's files. One
    {"path", "ok", "detail"} per path, in order."""
    local = Path(local_dir)
    local.mkdir(parents=True, exist_ok=True)
    base = ssh_argv(ssh_target)
    shell = " ".join(shlex.quote(a) for a in base[:-1])
    results = []
    seen_names: set[str] = set()
    for raw in paths:
        try:
            path = check_remote_path(raw)
        except ValueError as exc:
            results.append({"path": str(raw), "ok": False, "detail": str(exc)})
            continue
        name = path.rsplit("/", 1)[-1]
        if name in seen_names:
            results.append({"path": path, "ok": False,
                            "detail": f"another collect path already uses the folder name {name!r}; "
                                      "give each a different last folder name"})
            continue
        seen_names.add(name)
        code, _, err = runner(["rsync", "-a", "-e", shell, f"{base[-1]}:{path}", f"{local}/"],
                              None, COLLECT_TIMEOUT)
        lines = [line for line in err.strip().splitlines() if line.strip()]
        results.append({"path": path, "ok": code == 0,
                        "detail": "" if code == 0 else (lines[-1] if lines else f"rsync exited {code}")})
    return results
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_remote_exec.py tests/test_host_probe.py -q`
Expected: PASS

---

### Task 2: Remote hosts take work, and a sprint stays on its host

**Files:**
- Modify: `src/coscience/resources.py`, `src/coscience/models.py`, `src/coscience/substrate.py`, `src/coscience/ledger.py`, `src/coscience/scheduler.py`, `src/coscience/dispatcher.py`, `src/coscience/service.py`
- Test: `tests/test_remote_placement.py` (create); `tests/test_http_api.py`, `tests/test_mcp_server.py` (shape tests)

**Interfaces:**
- Produces:
  - `REMOTE_ENV = "COSCIENCE_ALLOW_REMOTE"`; `Host.placeable` true for a remote host only when `os.environ.get(REMOTE_ENV) == "1"`
  - `over_capacity_on(required, pool, program=None, only_host=None) -> tuple[str | None, dict]` (closest host name, shortfall); `over_capacity(required, pool, program=None, only_host=None)` returns the shortfall only
  - `ProgressState.host: str = ""`, `job_host: str = ""`, `job_collect: list[str]`, `collect_note: str = ""` (persisted)
  - `Ledger.acquire(..., prefer_cards=(), host=None)`
  - `SchedulerPolicy.select_grants(candidates, queued_at, ledger, now, pinned=None)`; `select_yield_victims(candidate, candidate_priority, ledger, yieldable_ids, pinned_host=None)`
  - `ledger_status()["local_capacity"]` — platform keys plus this machine's own amounts, the thing the capacity editor edits
  - `Service._unrunnable` names a pinned host that left the pool, or the closest host when several take work

- [ ] **Step 1: Write the failing tests**

Create `tests/test_remote_placement.py`:

```python
"""O6: remote hosts take work only when the deployment allows it, and a sprint stays on its host."""
from coscience.ledger import Ledger
from coscience.models import ProgressState, Sprint, SprintStatus
from coscience.resources import ResourcePool, over_capacity
from coscience.scheduler import SchedulerPolicy
from coscience.service import Service

POOL = {"cpu": 4, "workers": 2, "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 16}}}}
POOL_YAML = "cpu: 4\nworkers: 2\nhosts:\n  big:\n    ssh: big\n    capacity: {cpu: 16}\n"


def _sprint(sid, req, prio=0, status=SprintStatus.QUEUED):
    return Sprint(id=sid, status=status, goals="g", plan=["a"], resources_required=req, priority=prio)


def _ledger(tmp_path):
    led = Ledger(ResourcePool.from_dict(POOL), tmp_path / "leases.json")
    led.load()
    return led


def test_a_remote_host_takes_work_only_when_remote_placement_is_on(monkeypatch):
    monkeypatch.delenv("COSCIENCE_ALLOW_REMOTE", raising=False)
    assert not ResourcePool.from_dict(POOL).host("big").placeable
    monkeypatch.setenv("COSCIENCE_ALLOW_REMOTE", "1")
    pool = ResourcePool.from_dict(POOL)
    assert pool.host("big").placeable
    assert pool.capacity["cpu"] == 20.0


def test_a_pinned_sprint_is_granted_only_on_its_host(tmp_path, every_host_placeable):
    led, pol = _ledger(tmp_path), SchedulerPolicy(aging_interval=0.0)
    granted = pol.select_grants([_sprint("s1", {"cpu": 2})], {"s1": 0.0}, led, now=0.0,
                                pinned={"s1": "big"})
    assert [s.id for s in granted] == ["s1"]
    assert led.acquire("s1", {"cpu": 2}, now=0.0, ttl=60.0, host="big").host == "big"


def test_a_pinned_sprint_waits_rather_than_moving(tmp_path, every_host_placeable):
    led, pol = _ledger(tmp_path), SchedulerPolicy(aging_interval=0.0)
    assert led.acquire("hog", {"cpu": 16}, now=0.0, ttl=60.0).host == "big"
    assert pol.select_grants([_sprint("s1", {"cpu": 2})], {"s1": 0.0}, led, now=0.0,
                             pinned={"s1": "big"}) == []
    assert [s.id for s in pol.select_grants([_sprint("s1", {"cpu": 2})], {"s1": 0.0}, led, now=0.0)] == ["s1"]


def test_yield_victims_come_only_from_the_pinned_host(tmp_path, every_host_placeable):
    led, pol = _ledger(tmp_path), SchedulerPolicy()
    assert led.acquire("local-lo", {"cpu": 4}, now=0.0, ttl=60.0, priority=0).host == "local"
    assert led.acquire("big-lo", {"cpu": 16}, now=1.0, ttl=60.0, priority=0).host == "big"
    cand = _sprint("hi", {"cpu": 4}, prio=5)
    victims = pol.select_yield_victims(cand, 5, led, {"local-lo", "big-lo"}, pinned_host="big")
    assert [v.sprint_id for v in victims] == ["big-lo"]


def test_over_capacity_can_be_judged_on_one_host(every_host_placeable):
    pool = ResourcePool.from_dict(POOL)
    assert over_capacity({"cpu": 8}, pool) == {}
    assert over_capacity({"cpu": 8}, pool, only_host="local") == {"cpu": (8.0, 4.0)}


def test_progress_remembers_the_host_and_the_remote_job(substrate):
    substrate.save_progress(ProgressState(sprint_id="s1", host="big", job_host="big",
                                          job_collect=["~/runs/s1/work"], collect_note="copied"))
    prog = substrate.load_progress("s1")
    assert (prog.host, prog.job_host, prog.job_collect, prog.collect_note) == (
        "big", "big", ["~/runs/s1/work"], "copied")
    assert substrate.load_progress("never").host == ""


def _write(tmp_path, text):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def test_the_sprint_page_names_the_closest_host_when_several_take_work(tmp_path, every_host_placeable):
    _write(tmp_path, POOL_YAML)
    svc = Service(tmp_path)
    svc.substrate.save_sprint(_sprint("s1", {"cpu": 32}))
    assert svc.get_sprint("s1")["unrunnable"] == "needs cpu 32 but capacity is 16 (closest host: big)"


def test_a_sprint_pinned_to_a_host_that_left_the_pool_says_so(tmp_path):
    _write(tmp_path, "cpu: 4\n")
    svc = Service(tmp_path)
    svc.substrate.save_sprint(_sprint("s1", {"cpu": 1}))
    svc.substrate.save_progress(ProgressState(sprint_id="s1", host="gone1"))
    assert svc.get_sprint("s1")["unrunnable"] == (
        "its work is on host gone1, which is not in the pool or not taking work")


def test_ledger_status_offers_this_machines_amounts_for_editing(tmp_path, every_host_placeable):
    _write(tmp_path, POOL_YAML)
    status = Service(tmp_path).ledger_status()
    assert status["capacity"]["cpu"] == 20.0
    assert status["local_capacity"] == {"workers": 2.0, "cpu": 4.0}
```

In `tests/test_http_api.py::test_ledger_status_shape` and `tests/test_mcp_server.py::test_ledger_status_shape`, add `"local_capacity"` to the expected key set.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_remote_placement.py -q`
Expected: FAIL — unknown keyword arguments (`host`, `pinned`, `only_host`), missing progress fields, missing `local_capacity`.

- [ ] **Step 3: Implement placement and pinning**

`src/coscience/resources.py`:
- `import os`; after `LOCAL = "local"` add `REMOTE_ENV = "COSCIENCE_ALLOW_REMOTE"`.
- `Host.placeable` becomes:

```python
    @property
    def placeable(self) -> bool:
        # A remote host takes work only where the deployment turned remote placement
        # on: it sends sprint code and data to another machine.
        return self.is_local or os.environ.get(REMOTE_ENV) == "1"
```

- Rename the body of `over_capacity` into `over_capacity_on(required, pool, program=None, only_host=None) -> tuple[str | None, dict]`, which filters `pool.placeable_hosts(program)` to `only_host` when it is given, tracks the name of the chosen closest host alongside `best`, and returns `(best_host_name, {**over, **best})` (`None` as the name when no host was considered). Keep `over_capacity` as:

```python
def over_capacity(required: dict[str, float], pool: ResourcePool, program: str | None = None,
                  only_host: str | None = None) -> dict[str, tuple[float, float]]:
    """{resource: (requested, capacity)} for a request no allowed host can ever hold."""
    return over_capacity_on(required, pool, program, only_host)[1]
```

`src/coscience/models.py` — add to `ProgressState` after `gpu_devices`:

```python
    host: str = ""                     # host this sprint's agent was last told it runs on; "" = never launched
    job_host: str = ""                 # host of the tracked detached job; "" = this machine
    job_collect: list[str] = field(default_factory=list)  # paths on job_host to copy back before waking
    collect_note: str = ""             # what was copied back, for the next agent run
```

`src/coscience/substrate.py` — `load_progress` adds `host=str(fm.get("host", "")), job_host=str(fm.get("job_host", "")), job_collect=[str(p) for p in (fm.get("job_collect") or [])], collect_note=str(fm.get("collect_note", ""))`; `save_progress` adds `"host": progress.host, "job_host": progress.job_host, "job_collect": list(progress.job_collect), "collect_note": progress.collect_note`.

`src/coscience/ledger.py` — `acquire` gains a keyword `host=None` passed to `self.fit(..., host=host)`.

`src/coscience/scheduler.py`:
- `select_grants(self, candidates, queued_at, ledger, now, pinned=None)`: `placed = ledger.fit(need, sprint.program, pending, host=(pinned or {}).get(sprint.id))`.
- `select_yield_victims(self, candidate, candidate_priority, ledger, yieldable_ids, pinned_host=None)`: the first fit check becomes `ledger.fit(need, candidate.program, host=pinned_host)`, and the host loop skips hosts other than `pinned_host` when it is given. Add one sentence to the docstring: a pinned candidate only ever frees room on its own host.

`src/coscience/dispatcher.py`, in `run_one_cycle`:
- Before the grant loop, build the pins from progress:

```python
        # A sprint that has launched anything stays on that host: its files, and any
        # job still running, are there.
        pinned = {s.id: host for s in needs if (host := self.substrate.load_progress(s.id).host)}
```

- Call `self.policy.select_grants(needs, queue, self.ledger, now, pinned=pinned)` and pass `host=pinned.get(sprint.id)` to `self.ledger.acquire(...)`.
- In the yield step: `victims = self.policy.select_yield_victims(cand, cand_eff, self.ledger, yieldable, pinned_host=self.substrate.load_progress(cand.id).host or None)`.
- In the unrunnable loop: `over_capacity(s.resources_required, self.ledger.pool, s.program, only_host=self.substrate.load_progress(s.id).host or None)`.

`src/coscience/service.py`:
- `ledger_status` adds

```python
            # What the capacity editor edits: the platform keys and this machine's own
            # amounts. Once remote hosts take work the totals above include them, and
            # writing a total back as this machine's capacity would be wrong.
            "local_capacity": {**{k: v for k, v in ledger.pool.capacity.items() if k in PLATFORM_KEYS},
                               **(ledger.pool.host(LOCAL).capacity if ledger.pool.host(LOCAL) else {})},
```

  (import `LOCAL`, `PLATFORM_KEYS` from `coscience.resources`).
- `_unrunnable` becomes an instance method (it is already called as `self._unrunnable(...)`):

```python
    def _unrunnable(self, sprint: Sprint, pool) -> str:
        """Why this sprint can never be granted, or "". Only for sprints still headed
        for a grant: a finished one's request no longer matters."""
        if sprint.status in (SprintStatus.DONE, SprintStatus.CANCELED, SprintStatus.FAILED):
            return ""
        from coscience.resources import describe_over_capacity, over_capacity_on
        pinned = self.substrate.load_progress(sprint.id).host
        if pinned:
            host = pool.host(pinned)
            if host is None or not host.placeable:
                return f"its work is on host {pinned}, which is not in the pool or not taking work"
        closest, over = over_capacity_on(sprint.resources_required, pool, sprint.program,
                                         only_host=pinned or None)
        text = describe_over_capacity(over)
        if text and closest and not pinned and len(pool.placeable_hosts(sprint.program)) > 1:
            text += f" (closest host: {closest})"
        return text
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_remote_placement.py tests/test_resources_hosts.py tests/test_gpu_pool.py tests/test_ledger.py tests/test_ledger_hosts.py tests/test_gpu_ledger.py tests/test_scheduler_grants.py tests/test_scheduler_preempt.py tests/test_scheduler_workers.py tests/test_scheduler_hosts.py tests/test_gpu_scheduler.py tests/test_dispatcher.py tests/test_dispatcher_reconcile.py tests/test_dispatcher_hibernate.py tests/test_unrunnable.py tests/test_service_capacity.py tests/test_http_api.py tests/test_mcp_server.py tests/test_gpu_handoff.py -q`
Expected: PASS

---

### Task 3: The worker runs, watches, stops and collects remote jobs

**Files:**
- Modify: `src/coscience/executor.py`, `src/coscience/dispatcher.py` (`_WorkerSlots`), `src/coscience/worker.py`, `src/coscience/claude_executor.py`
- Test: `tests/test_remote_jobs.py` (create); any test slot fake (`grep -rn "def acquire(self, sprint_id" tests/`)

**Interfaces:**
- Consumes: `remote_exec` (Task 1); `ProgressState.host/job_host/job_collect/collect_note`, `Host.placeable` (Task 2).
- Produces:
  - `ExecutionContext.host_name: str = "local"`, `host_ssh: str = ""`, `host_run_dir: str = ""`, `host_facts: str = ""`, `host_notes: str = ""`, `collect_note: str = ""`
  - slot handles gain `host(sprint_id) -> dict` (`{"name", "ssh", "run_root", "facts", "notes"}`) and `ssh_for(host_name) -> str`; `_WorkerSlots(ledger, repo_root=None)`
  - `Worker(..., runner=None)`; job.json may carry `host` and `collect`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_remote_jobs.py`:

```python
"""O6: the worker verifies, watches, stops and collects a job on a remote host."""
import json
import time
from pathlib import Path

from coscience.claude_executor import build_instructions
from coscience.executor import ExecutionContext
from coscience.models import Sprint, SprintStatus
from coscience.substrate import Substrate
from coscience.worker import Worker
from tests.test_worker_detached_job import FakeAgent


class Slots:
    def __init__(self, host="gpu1"):
        self.name = host

    def release(self, sprint_id):
        pass

    def acquire(self, sprint_id):
        return True

    def gpus(self, sprint_id):
        return [], None

    def host(self, sprint_id):
        return {"name": self.name, "ssh": self.name, "run_root": "~/runs",
                "facts": "Example Linux 9 · 12 threads", "notes": "nights only"}

    def ssh_for(self, host_name):
        return host_name if host_name == self.name else ""


class ScriptRunner:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[str]] = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append(list(argv))
        return self.replies.pop(0) if self.replies else (0, "", "")


def _queued(sub, sid="s1"):
    sub.save_sprint(Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1"))


def _remote_job(sprint_dir, host="gpu1"):
    (sprint_dir / "job.json").write_text(json.dumps({
        "pid": 4242, "host": host, "out_file": "~/runs/s1/work/train.out",
        "collect": ["~/runs/s1/work"], "expected_seconds": 60,
        "wake_after_seconds": 120, "max_seconds": 600, "note": "train"}))


def test_a_declared_remote_job_is_verified_and_tracked_by_its_host_token(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    runner = ScriptRunner((0, "boot=boot-1\nproc=S 777\n", ""))
    w = Worker(sub, FakeAgent(on_start=_remote_job, finished=False), slots=Slots(), runner=runner)
    w.run_one_beat()
    w.run_one_beat()
    prog = sub.load_progress("s1")
    assert prog.job_token == "gpu1:4242:777:boot-1"
    assert (prog.job_host, prog.job_collect, prog.host) == ("gpu1", ["~/runs/s1/work"], "gpu1")
    assert prog.assess_reason == ""


def test_a_remote_job_that_is_not_running_when_declared_is_assessed_at_once(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    runner = ScriptRunner((0, "boot=boot-1\n", ""))
    w = Worker(sub, FakeAgent(on_start=_remote_job, finished=False), slots=Slots(), runner=runner)
    w.run_one_beat()
    w.run_one_beat()
    prog = sub.load_progress("s1")
    assert prog.job_token == "" and prog.assess_reason == "finished"
    assert any(call[0] == "rsync" for call in runner.calls)          # outputs collected first


def test_a_job_declared_on_a_host_the_sprint_does_not_hold_is_refused(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    w = Worker(sub, FakeAgent(on_start=lambda d: _remote_job(d, host="elsewhere"), finished=False),
               slots=Slots(), runner=ScriptRunner())
    w.run_one_beat()
    w.run_one_beat()
    prog = sub.load_progress("s1")
    assert prog.job_token == ""
    assert "elsewhere" in prog.last_error


def _sleeping(sub, token="gpu1:4242:777:boot-1"):
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_host, prog.job_collect = token, "gpu1", ["~/runs/s1/work"]
    prog.job_out, prog.job_note, prog.host = "~/runs/s1/work/train.out", "train", "gpu1"
    prog.job_started_at, prog.job_max_seconds, prog.job_next_wake = time.time(), 9e9, time.time() + 9e9
    sub.save_progress(prog)


def test_an_unreachable_host_keeps_the_job_asleep(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((255, "", "ssh: connect to host gpu1: Connection timed out"))
    w = Worker(sub, FakeAgent(), slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))
    prog = sub.load_progress("s1")
    assert prog.job_token == "gpu1:4242:777:boot-1" and prog.assess_reason == ""
    assert not any(call[0] == "rsync" for call in runner.calls)


def test_a_rebooted_host_wakes_the_agent_with_the_job_lost(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((0, "boot=boot-2\n", ""))
    w = Worker(sub, FakeAgent(), slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))
    assert sub.load_progress("s1").assess_reason == "lost"


class CapturingAgent(FakeAgent):
    """Records the context each launch received."""

    def __init__(self):
        super().__init__(finished=False)
        self.contexts = []

    def start(self, sprint, ctx, sprint_dir, repo_root=None):
        self.contexts.append(ctx)
        return super().start(sprint, ctx, sprint_dir, repo_root)


def test_outputs_are_copied_back_before_the_agent_wakes_and_it_is_told(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((0, "boot=boot-1\n", ""), (0, "", ""))
    agent = CapturingAgent()
    w = Worker(sub, agent, slots=Slots(), runner=runner)
    w.run_sprint_beat(sub.load_sprint("s1"))            # job gone -> collect -> launch the assess run
    rsync = [c for c in runner.calls if c[0] == "rsync"]
    assert rsync and rsync[0][-2] == "gpu1:~/runs/s1/work"
    assert rsync[0][-1] == f"{sub.sprint_dir('s1') / 'collected'}/"
    note = agent.contexts[-1].collect_note               # handed to the woken agent...
    assert "~/runs/s1/work" in note and "do not need to copy" in note
    assert sub.load_progress("s1").collect_note == ""    # ...and not repeated on a later run


def test_stopping_a_sprint_kills_its_remote_job_over_ssh(tmp_path):
    sub = Substrate(tmp_path); _queued(sub); _sleeping(sub)
    runner = ScriptRunner((0, "", ""))
    w = Worker(sub, FakeAgent(), slots=Slots(), runner=runner)
    assert w.stop_sprint(sub.load_sprint("s1")) == ["s1"]
    assert "kill -TERM" in runner.calls[0][-1]
    assert sub.load_progress("s1").job_token == ""


def test_the_agent_is_told_how_to_work_on_its_host():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    ctx = ExecutionContext(host_name="gpu1", host_ssh="gpu1", host_run_dir="~/runs/s1",
                           host_facts="Example Linux 9 · 12 threads", host_notes="nights only")
    text = build_instructions(sprint, ctx, Path("/tmp/s1/scratchpad.md"))
    assert "## Where this sprint's heavy work runs" in text
    assert "ssh gpu1 'mkdir -p ~/runs/s1/work && cd ~/runs/s1 && setsid nohup" in text
    assert '"host": "gpu1"' in text and '"collect": ["~/runs/s1/work"]' in text
    assert "Example Linux 9 · 12 threads" in text and "nights only" in text


def test_a_local_sprint_gets_no_host_section():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    assert "heavy work runs" not in build_instructions(sprint, ExecutionContext(), Path("/tmp/s1/s.md"))


def test_the_wake_run_carries_the_collect_note():
    sprint = Sprint(id="s1", status=SprintStatus.APPROVED, goals="train", plan=["train"])
    ctx = ExecutionContext(assess_reason="finished", job_out="j.out", job_note="train",
                           collect_note="Before waking you, the platform copied these paths from gpu1")
    assert "the platform copied these paths from gpu1" in build_instructions(sprint, ctx, Path("/tmp/s.md"))


def test_the_dispatcher_slot_handle_describes_the_leases_host(tmp_path, every_host_placeable):
    from coscience.dispatcher import _WorkerSlots
    from coscience.ledger import Ledger
    from coscience.resources import ResourcePool
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {"gpu1": {
        "ssh": "gpu1", "run_root": "~/runs", "notes": "nights only", "capacity": {"cpu": 8}}}})
    led = Ledger(pool, tmp_path / "leases.json"); led.load()
    led.acquire("s1", {"cpu": 4}, now=0.0, ttl=60.0, host="gpu1")
    probes = tmp_path / ".coscience" / "host-probes"; probes.mkdir(parents=True)
    (probes / "gpu1.json").write_text(json.dumps({"facts": {
        "os": "Example Linux 9", "cpu_model": "Example CPU", "threads": 12, "mem_total_kb": 65000000}}))
    slots = _WorkerSlots(led, repo_root=tmp_path)
    assert slots.host("s1") == {"name": "gpu1", "ssh": "gpu1", "run_root": "~/runs",
                                "facts": "Example Linux 9 · Example CPU · 12 threads · 62 GB memory",
                                "notes": "nights only"}
    assert slots.ssh_for("gpu1") == "gpu1" and slots.ssh_for("nope") == ""
    assert slots.host("nobody")["name"] == "local"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_remote_jobs.py -q`
Expected: FAIL — `Worker` has no `runner` argument, `ExecutionContext` has no host fields, `_WorkerSlots` has no `host`.

- [ ] **Step 3: Implement the context and the slot handle**

`src/coscience/executor.py` — add to `ExecutionContext`:

```python
    host_name: str = "local"      # the host this sprint's lease is on
    host_ssh: str = ""            # ssh target for it; "" = this machine
    host_run_dir: str = ""        # this sprint's working directory on the host
    host_facts: str = ""          # one line from the host's onboarding probe
    host_notes: str = ""          # the host entry's notes
    collect_note: str = ""        # what the platform copied back before this run
```

`src/coscience/dispatcher.py` — `_WorkerSlots.__init__(self, ledger, repo_root=None)` stores `self.repo_root`; `Dispatcher.__init__` passes `repo_root=substrate.repo_root`. Add:

```python
    def ssh_for(self, host_name: str) -> str:
        host = self.ledger.pool.host(host_name)
        return host.ssh if host is not None else ""

    def host(self, sprint_id: str) -> dict:
        """Where this sprint's lease is, for the worker agent's instructions: the host's
        name, ssh target, run root, a line of probed facts and its notes."""
        lease = self.ledger.lease_for(sprint_id)
        host = self.ledger.pool.host(lease.host) if lease is not None else None
        if host is None or host.is_local:
            return {"name": LOCAL, "ssh": "", "run_root": "", "facts": "", "notes": ""}
        return {"name": host.name, "ssh": host.ssh, "run_root": host.run_root,
                "facts": _probe_facts_line(self.repo_root, host.name), "notes": host.notes}
```

and a module function:

```python
def _probe_facts_line(repo_root, host_name: str) -> str:
    """"OS · CPU · N threads · N GB memory" from the host's last onboarding probe, or ""."""
    if repo_root is None:
        return ""
    path = Path(repo_root) / ".coscience" / "host-probes" / f"{host_name}.json"
    try:
        facts = json.loads(path.read_text()).get("facts") or {}
    except (OSError, ValueError, AttributeError):
        return ""
    parts = [str(facts[k]) for k in ("os", "cpu_model") if facts.get(k)]
    if facts.get("threads"):
        parts.append(f"{facts['threads']} threads")
    if facts.get("mem_total_kb"):
        parts.append(f"{round(facts['mem_total_kb'] / 1024 / 1024)} GB memory")
    return " · ".join(parts)
```

(import `LOCAL` from `coscience.resources`; `json` and `Path` are already imported in dispatcher.py — add them if not.)

`src/coscience/worker.py` — `_NoSlots` gains:

```python
    def host(self, sprint_id: str) -> dict:
        return {"name": "local", "ssh": "", "run_root": "", "facts": "", "notes": ""}

    def ssh_for(self, host_name: str) -> str:
        return ""
```

Add the same two methods to every slot-handle fake under `tests/` (`grep -rn "def acquire(self, sprint_id" tests/`).

- [ ] **Step 4: Implement the worker's remote job flow**

In `src/coscience/worker.py`:

1. Imports: `from coscience import remote_exec` and `from coscience.host_probe import subprocess_runner`.
2. `Worker.__init__` gains `runner=None`: `self._runner = runner or subprocess_runner`; the defaults become methods — `self._job_alive = job_alive or self._default_job_alive` and `self._terminate = terminate or self._default_terminate` — and add:

```python
    def _default_job_alive(self, token: str) -> bool:
        """A local job by /proc; a remote job over ssh. An unreachable host counts as
        alive: a job is never declared dead because its host could not be asked."""
        remote = remote_exec.parse_token(token)
        if remote is None:
            self._last_job_state = ""
            return _job_is_running(token)
        ssh = self._slots.ssh_for(remote.host)
        state = remote_exec.job_state(ssh, remote, runner=self._runner) if ssh else "unknown"
        self._last_job_state = state
        return state in ("alive", "unknown")

    def _default_terminate(self, token: str) -> None:
        remote = remote_exec.parse_token(token)
        if remote is None:
            _terminate(token)
            return
        ssh = self._slots.ssh_for(remote.host)
        if ssh:
            remote_exec.terminate(ssh, remote, runner=self._runner)

    def _collect_job(self, progress, sprint_dir) -> None:
        """Copy a remote job's declared outputs into the sprint's `collected/` folder
        before the agent is woken, and leave a note saying exactly what was copied."""
        if not progress.job_host or not progress.job_collect:
            return
        ssh = self._slots.ssh_for(progress.job_host)
        dest = Path(sprint_dir) / "collected"
        if not ssh:
            progress.collect_note = (f"Nothing was copied back: host {progress.job_host} is no "
                                     "longer in the pool.")
            return
        results = remote_exec.collect(ssh, progress.job_collect, dest, runner=self._runner)
        stamp = time.strftime("%H:%M")
        lines = [f"- {r['path']} → {dest}/{Path(r['path']).name} (at {stamp})" if r["ok"]
                 else f"- {r['path']} was NOT copied: {r['detail']}" for r in results]
        progress.collect_note = (
            f"Before waking you, the platform copied these paths from {progress.job_host}. Read "
            "the results there; you do not need to copy them yourself. Nothing else was copied.\n"
            + "\n".join(lines))
```

   Initialise `self._last_job_state = ""` in `__init__`.

3. `_read_job_json` adds to the returned dict: `"host": str(d.get("host") or "")` and `"collect": [str(p) for p in (d.get("collect") or []) if isinstance(p, str)] if isinstance(d.get("collect"), list) else []`.

4. In step A (sleeping on a job), where the job is found not alive, set `progress.assess_reason = "lost" if self._last_job_state == "lost" else "finished"`. In each branch that ends the sleep (not alive, timed out, wake), call `self._collect_job(progress, sprint_dir)` before `self.substrate.save_progress(progress)`. After clearing `progress.job_token` in the not-alive and timed-out branches, also clear `progress.job_host = ""` and `progress.job_collect = []`.

5. In the launch path, right after `progress.gpu_devices = list(ctx.gpu_devices)`, add `progress.host = ctx.host_name` and `progress.collect_note = ""` (the note has now been handed to this run).

6. In `_build_context`, before `return ExecutionContext(`:

```python
        host = self._slots.host(sprint.id)
        host_run_dir = f"{host['run_root'].rstrip('/')}/{sprint.id}" if host["ssh"] and host["run_root"] else ""
```

   and pass `host_name=host["name"], host_ssh=host["ssh"], host_run_dir=host_run_dir, host_facts=host["facts"], host_notes=host["notes"], collect_note=progress.collect_note`.

7. Replace the declared-job block (`job = self._read_job_json(sprint_dir)` … `return BeatOutcome.PROGRESSED`) with:

```python
        job = self._read_job_json(sprint_dir)
        if job is not None and job["host"] not in ("", "local"):
            held = self._slots.host(sprint.id)["name"]
            if job["host"] != held:
                # A job on a host this sprint does not hold could be neither watched
                # nor stopped: refuse it and say why.
                (sprint_dir / "job.json").unlink(missing_ok=True)
                progress.last_error = (f"job.json named host {job['host']!r} but this sprint "
                                       f"runs on {held!r}")
                job = None
        if job is not None:
            now = time.time()
            job_host = "" if job["host"] in ("", "local") else job["host"]
            state = "alive"
            if job_host:
                progress.job_token, state = remote_exec.make_token(
                    job_host, self._slots.ssh_for(job_host), job["pid"], runner=self._runner)
            else:
                progress.job_token = process_token(job["pid"])
            progress.job_host = job_host
            progress.job_collect = job["collect"] if job_host else []
            progress.job_out = job["out_file"]
            progress.job_note = job["note"]
            progress.job_started_at = now
            progress.job_expected_seconds = job["expected_seconds"]
            progress.job_next_wake = now + job["wake_after_seconds"]
            progress.job_max_seconds = min(job["max_seconds"] or JOB_MAX_SECONDS, JOB_MAX_SECONDS)
            progress.assess_reason = ""
            progress.agent_token = ""
            progress.ambiguous_exits = 0
            (sprint_dir / "job.json").unlink(missing_ok=True)     # consume it
            if state == "gone":
                # The declared job was not running on its host: tell the agent now,
                # with whatever it wrote, rather than sleeping on nothing.
                self._collect_job(progress, sprint_dir)
                progress.assess_reason = "finished"
                progress.job_token = ""
                progress.job_host = ""
                progress.job_collect = []
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: detached job declared ({progress.job_note})")
            return BeatOutcome.PROGRESSED                          # stay executing, sleep on the job
```

8. `_reap_job`, `stop_sprint` and `hibernate_sprint`: wherever `progress.job_token = ""` is set after terminating, also set `progress.job_host = ""` and `progress.job_collect = []` (hibernate keeps `collect_note` for the resumed run).

`src/coscience/claude_executor.py`:
- Add `host_section = ""` beside the other section defaults; inside `if context is not None:` add:

```python
        if context.host_ssh:
            facts = f"\nWhat the host is: {context.host_facts}." if context.host_facts else ""
            notes = f"\nNotes on this host: {context.host_notes}" if context.host_notes else ""
            host_section = f"""

## Where this sprint's heavy work runs
This sprint holds host `{context.host_name}`. Your own shell runs on the platform's machine: edit, read
and run quick commands here, and run anything heavy on the host over SSH (`ssh {context.host_ssh} '<command>'`).
Use {context.host_run_dir} on the host as this sprint's working directory. Paths, environments and
installed software there differ from this machine, and nothing is shared unless you copy it there:
a job only sees what is on the host when it starts.{facts}{notes}
Long jobs on the host follow the DETACHED-JOB PROTOCOL below with two changes. Launch with
`ssh {context.host_ssh} 'mkdir -p {context.host_run_dir}/work && cd {context.host_run_dir} && setsid nohup <cmd> > work/<out_file> 2>&1 < /dev/null & echo $!'`
and add `"host": "{context.host_name}"` and `"collect": ["{context.host_run_dir}/work"]` to job.json (out_file is
then the path on the host). Before waking you, the platform copies each collect path into this sprint's
`collected/` folder. Before you finish, make sure the results you need are in this sprint's folder, then
delete what you created on the host."""
```

- Change the template line `{comments}{artifacts_section}{gpu_section}` to `{comments}{artifacts_section}{gpu_section}{host_section}`.
- In the assess section, when `context.collect_note` is set, append it on its own paragraph after the existing text.

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_remote_jobs.py tests/test_worker_detached_job.py tests/test_job_instructions.py tests/test_claude_executor.py tests/test_gpu_handoff.py tests/test_dispatcher.py tests/test_dispatcher_reconcile.py tests/test_dispatcher_hibernate.py tests/test_remote_exec.py tests/test_remote_placement.py -q`
Expected: PASS

---

### Task 4: The Compute page edits this machine and shows where work runs

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/views/Ledger.tsx`
- Test: `frontend/src/views/Ledger.test.tsx` (append)

**Interfaces:**
- Consumes: `ledger_status.local_capacity` and each lease's `host` (Task 2; O2).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/views/Ledger.test.tsx` (inside its top-level `describe`, reusing `ledger`, `renderPage` and the mocked `api`):

```tsx
  it("edits this machine's capacity, not the pool total, once remote servers take work", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 40, workers: 2 }, local_capacity: { cpu: 24, workers: 2 },
      used: {}, available: {}, leases: [], paused: false, host_errors: [],
      hosts: [
        { name: "local", ssh: "", placeable: true, programs: [], run_root: "", capacity: { cpu: 24 }, available: {}, gpus: [] },
        { name: "gpu1", ssh: "gpu1", placeable: true, programs: [], run_root: "~/runs", capacity: { cpu: 16 }, available: {}, gpus: [] },
      ],
    });
    renderPage();
    expect(await screen.findByText(/Edit capacity changes this machine only/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit capacity" }));
    expect((await screen.findByLabelText("cpu capacity") as HTMLInputElement).value).toBe("24");
  });

  it("shows which server a running experiment is on", async () => {
    ledger.mockResolvedValue({
      capacity: { cpu: 40 }, used: { cpu: 4 }, available: {}, paused: false, host_errors: [], hosts: [],
      leases: [{ id: "l1", sprint_id: "s1", amounts: { cpu: 4 }, host: "gpu1" }],
    });
    renderPage();
    expect(await screen.findByText(/on gpu1/)).toBeTruthy();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/views/Ledger.test.tsx`
Expected: FAIL — the hint text and "on gpu1" are not rendered; the dialog is seeded with 40.

- [ ] **Step 3: Implement**

`frontend/src/api.ts`: `Ledger` gains `local_capacity?: Record<string, number>`.

`frontend/src/views/Ledger.tsx`:
- After `const l = ledger.data;` add:

```tsx
  // Once a remote server takes work the pool totals include it; the capacity editor
  // and the steppers change this machine's own amounts.
  const editable = l.local_capacity ?? l.capacity;
  const remoteTakesWork = (l.hosts ?? []).some((h) => h.placeable && h.ssh);
```

- Replace `if (ledger.data) capacityRef.current = ledger.data.capacity;` with `if (ledger.data) capacityRef.current = ledger.data.local_capacity ?? ledger.data.capacity;`.
- In the capacity card, when `remoteTakesWork`, render (above the gauges) `<Text size="sm" c="dimmed" style={{ marginBottom: 16 }}>Totals include remote servers. Edit capacity changes this machine only.</Text>` and pass `onAdjust={remoteTakesWork ? undefined : (delta) => adjust(k, delta)}` to each `Gauge`.
- Pass `capacity={editable}` to `CapacityModal`.
- In the running-now table, type the lease with an optional `host?: string` and render the amounts cell as `` `${Object.entries(x.amounts).map(([k, v]) => `${v} ${k}`).join(", ")}${x.host && x.host !== "local" ? ` on ${x.host}` : ""}` ``.

- [ ] **Step 4: Run the tests and type check**

Run (from `frontend/`): `npx vitest run src/views/Ledger.test.tsx src/components/CapacityModal.test.tsx src/components/HostsCard.test.tsx` and `npx tsc -b`
Expected: PASS, and no type errors.

---

### Task 5: Whole suites, docs, todo

Run by the controller.

- [ ] **Step 1:** full Python suite, full vitest suite, `npx tsc -b` → pass.
- [ ] **Step 2:** read the live pool and leases with the new code, read-only — with `COSCIENCE_ALLOW_REMOTE` unset nothing is placeable remotely and every lease is local, exactly as before.
- [ ] **Step 3:** spec §11: mark the O6 preconditions this item closed (capacity editor edits the local host; unrunnable names the host; the card preference follows the pinned host; no `--delete`, never `~` or `/`; remote kill by token), and record that remote placement is opt-in through `COSCIENCE_ALLOW_REMOTE=1`, that `distributed` spanning is still not built, and that the probe still runs synchronously (the background probe remains open).
- [ ] **Step 4:** move O6 to To QC with a check a human runs against one real server.
