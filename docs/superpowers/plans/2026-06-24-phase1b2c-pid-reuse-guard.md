# Co-Science Platform — Phase 1b-2c (PID-Reuse Guard) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close a correctness hole in detached-job control. We store a job's PID on disk (`progress.detached[step_id]`) and later signal it (`is_running`, `terminate_detached`). After a dispatcher outage or a reboot, that PID may have been **recycled by the OS** and now belong to an unrelated process — so `is_running` would report a stranger as "our job still running", and `terminate_detached` would **kill someone else's process**. This phase captures a process-identity token at launch (the Linux start-time, `/proc/<pid>/stat` field 22, which is stable for a PID's lifetime and differs on reuse) and verifies it before every signal. A mismatched identity is treated as "our job is gone" — never signalled.

**Architecture:** An identity token is the string `"<pid>:<starttime>"`. `launch_detached` returns it (instead of a bare PID); it is what gets stored in `progress.detached`. `is_running` and `terminate_detached` accept either a token (verify identity before acting) or — for backward tolerance with legacy on-disk PIDs — a bare `int`/PID-only string (fall back to plain liveness, exactly today's behavior). The change lands in two commits, each leaving the full suite green: **Task 1** adds the executor mechanism (callers still pass ints, behavior unchanged); **Task 2** activates it by threading tokens through `ProgressState`/substrate/worker/dispatcher.

**Tech Stack:** Python 3.12 (venv `/home/oleg/venvs/coscience`), PyYAML, pytest. No new dependencies. Linux-specific (`/proc`), which the existing `is_running` zombie check already relies on.

## Global Constraints

- **Interpreter:** canonical venv `/home/oleg/venvs/coscience`. Run tests with `/home/oleg/venvs/coscience/bin/python -m pytest`.
- **Dependencies:** none added.
- **The full suite stays green at EVERY commit.** Task 1 must not change the behavior of any existing caller (they still pass ints; the int path is byte-for-byte the old behavior). Task 2 migrates callers and storage together.
- **Identity guard is fail-safe:** when a stored token's start-time does not match the live process at that PID, treat the job as **gone** — `is_running` returns `False`, `terminate_detached` is a **no-op**. Never signal a PID whose identity can't be confirmed against a captured token.
- **Backward tolerance:** a legacy bare-int PID (or a token with an empty start-time) carries no identity to verify, so it falls back to plain liveness — exactly the current behavior. Old on-disk `progress.detached` values (ints) must keep working.
- **Preserve the existing zombie exclusion** in `is_running` (a zombie/`Z` process counts as not running).
- **No behavior change to scheduling/leases/service/transports** — this is purely job-signal hardening. `get_sprint`'s `detached` field will now expose token strings instead of ints; that is the only transport-visible shape change and it stays JSON-serialisable.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass, commit. End every commit message body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Phase Roadmap (context — only 1b-2c is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| 0 / 1 / 1b-1 / 1b-2a / 1b-2b (i/ii/iii) | skeleton; scheduling; job kill; restart reconciliation; service core + MCP + HTTP + container | DONE |
| **1b-2c — PID-reuse guard** (this plan) | identity token verified before signalling a stored PID | **planned here** |
| Phase 2 | PM agent + internal dashboard | next |

---

## File Structure

```
src/coscience/
  executor.py    # MODIFY (Task 1): _starttime, process_token, _parse_target, _identity_ok;
                 #   is_running / terminate_detached accept (int | str) token + verify identity;
                 #   (Task 2): launch_detached returns the token string
  models.py      # MODIFY (Task 2): ProgressState.detached: dict[str, str]
  substrate.py   # MODIFY (Task 2): load_progress coerces detached values to str (legacy-int tolerant)
  worker.py      # MODIFY (Task 2): store/read tokens; pass tokens to is_running / terminate_detached
  dispatcher.py  # MODIFY (Task 2): pass tokens to is_running in reconciliation
tests/
  test_pid_reuse_guard.py   # NEW (Task 1): real-process identity verification
  test_detached_tokens.py   # NEW (Task 2): wiring — stored value is a token; kill/stop still work
```

---

## Task 1: Executor identity-token mechanism (callers unchanged)

**Files:**
- Modify: `src/coscience/executor.py`
- Test: `tests/test_pid_reuse_guard.py`

**Interfaces:**
- `_starttime(pid: int) -> str | None` — field 22 of `/proc/<pid>/stat` (start time in clock ticks since boot); `None` if the process is gone / unreadable. The `comm` field (2) is parenthesised and may contain spaces and `)`, so split on the **last** `)`.
- `process_token(pid: int) -> str` — `"<pid>:<starttime>"`, or `"<pid>:"` if the start time can't be read.
- `_parse_target(pid_or_token: int | str) -> tuple[int, str | None]` — an `int` → `(pid, None)` (no identity); a `"<pid>:<st>"` string → `(pid, st or None)`.
- `_identity_ok(pid: int, expected_st: str | None) -> bool` — `True` if `expected_st is None` (nothing to verify → liveness-only) **or** the live process's current start-time equals `expected_st`.
- `is_running(pid_or_token: int | str) -> bool` — unchanged semantics for an `int`; for a token, additionally returns `False` when identity doesn't match (PID reused) even though the PID is alive. Zombie exclusion preserved.
- `terminate_detached(pid_or_token: int | str, grace: float = 2.0) -> None` — verifies identity first; a mismatch is a **no-op**. Otherwise behaves as today (process-group SIGTERM, wait, SIGKILL).
- `launch_detached` is **unchanged in this task** (still returns `int`).

- [ ] **Step 1: Write the failing tests**

`tests/test_pid_reuse_guard.py`:
```python
import subprocess
import time

import pytest

from coscience.executor import (is_running, process_token, terminate_detached)


def _spawn(cmd="sleep 30"):
    return subprocess.Popen(cmd, shell=True, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_process_token_has_pid_and_nonempty_starttime():
    p = _spawn()
    try:
        tok = process_token(p.pid)
        assert tok.startswith(f"{p.pid}:")
        assert tok.split(":", 1)[1]  # start-time present
    finally:
        p.kill(); p.wait()


def test_is_running_true_for_matching_token():
    p = _spawn()
    try:
        assert is_running(process_token(p.pid)) is True
    finally:
        p.kill(); p.wait()


def test_is_running_false_for_reused_pid_token():
    # Same live PID, but a stale start-time => simulated PID reuse.
    p = _spawn()
    try:
        real = process_token(p.pid)
        pid, st = real.split(":")
        stale = f"{pid}:{int(st) + 1}"
        assert is_running(stale) is False   # identity mismatch -> treated as gone
        assert is_running(real) is True     # control: the real token still matches
    finally:
        p.kill(); p.wait()


def test_terminate_is_noop_for_reused_pid_token():
    p = _spawn()
    try:
        real = process_token(p.pid)
        pid, st = real.split(":")
        stale = f"{pid}:{int(st) + 1}"
        terminate_detached(stale, grace=0.3)
        assert is_running(real) is True      # real process must NOT have been killed
    finally:
        p.kill(); p.wait()


def test_terminate_kills_matching_token():
    p = _spawn()
    terminate_detached(process_token(p.pid), grace=1.0)
    assert p.wait(timeout=3) is not None     # process actually exited


def test_is_running_false_after_process_dies():
    p = _spawn()
    tok = process_token(p.pid)
    p.kill(); p.wait()
    time.sleep(0.05)
    assert is_running(tok) is False


def test_legacy_int_pid_liveness_preserved():
    p = _spawn()
    try:
        assert is_running(p.pid) is True     # bare int => liveness-only (old behavior)
    finally:
        p.kill(); p.wait()
    time.sleep(0.05)
    assert is_running(p.pid) is False        # dead pid
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pid_reuse_guard.py -v`
Expected: FAIL with `ImportError: cannot import name 'process_token'`.

- [ ] **Step 3: Implement the mechanism in `executor.py`**

Add the helpers and rewrite `is_running` / `terminate_detached` to accept `int | str`:
```python
def _starttime(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat: process start time in clock ticks since
    boot. Stable for the life of a PID; differs if the PID is later reused.
    Returns None if the process is gone / unreadable."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
    except OSError:
        return None
    # comm (field 2) is parenthesised and may contain spaces/')'; split after the last ')'.
    rparen = data.rfind(")")
    if rparen == -1:
        return None
    fields = data[rparen + 2:].split()
    # After comm: index 0 == state (field 3); start-time is field 22 == index 19.
    return fields[19] if len(fields) > 19 else None


def process_token(pid: int) -> str:
    """Identity token '<pid>:<starttime>' for a process. Falls back to '<pid>:'
    when the start time can't be read (degrades to PID-only liveness)."""
    st = _starttime(pid)
    return f"{pid}:{st if st is not None else ''}"


def _parse_target(pid_or_token: "int | str") -> "tuple[int, str | None]":
    if isinstance(pid_or_token, int):
        return pid_or_token, None  # legacy: no identity to verify
    pid_str, _, st = str(pid_or_token).partition(":")
    return int(pid_str), (st or None)


def _identity_ok(pid: int, expected_st: "str | None") -> bool:
    """True if there is no identity to check (liveness-only), or the live
    process's current start time matches the captured one."""
    if expected_st is None:
        return True
    return _starttime(pid) == expected_st
```

Rewrite `is_running`:
```python
def is_running(pid_or_token: "int | str") -> bool:
    """True if the target is alive AND (if a token was given) its identity
    matches. Zombies and reused PIDs are treated as not running."""
    pid, expected_st = _parse_target(pid_or_token)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but not ours to signal — still verify identity (stat is readable).
        return _identity_ok(pid, expected_st)
    if not _identity_ok(pid, expected_st):
        return False  # PID reused by a different process
    # On Linux, os.kill(pid, 0) succeeds for zombie processes; exclude them.
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("State:"):
                    return line.split()[1] != "Z"
    except OSError:
        pass
    return True
```

Rewrite `terminate_detached`:
```python
def terminate_detached(pid_or_token: "int | str", grace: float = 2.0) -> None:
    """Stop a detached job's whole process group (SIGTERM, then SIGKILL).

    PID-reuse guard: if a token was given and its identity does not match the
    live process, do nothing (the original job is gone; the PID belongs to a
    stranger). A dead/unknown PID is a no-op. A bare int falls back to plain
    liveness, preserving the original behavior.
    """
    pid, expected_st = _parse_target(pid_or_token)
    if not _identity_ok(pid, expected_st):
        return  # PID reused — never signal a stranger
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
        if not is_running(pid_or_token):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_pid_reuse_guard.py -v`
Expected: all pass.

- [ ] **Step 5: Run the FULL suite (must stay green — callers still pass ints)**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass (130 + the new file). If any existing executor test broke, the int path diverged from the old behavior — fix the mechanism, not the old test.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/executor.py tests/test_pid_reuse_guard.py
git commit -m "feat: executor process-identity token + reuse-guarded is_running/terminate"
```

---

## Task 2: Activate the guard — thread tokens through storage and callers

**Files:**
- Modify: `src/coscience/executor.py` (`launch_detached` returns the token), `src/coscience/models.py`, `src/coscience/substrate.py`, `src/coscience/worker.py`, `src/coscience/dispatcher.py`
- Test: `tests/test_detached_tokens.py`

**Interfaces:**
- `launch_detached(command: str) -> str` — now returns `process_token(proc.pid)` (the `"<pid>:<starttime>"` token) instead of the bare PID.
- `ProgressState.detached: dict[str, str]` (was `dict[str, int]`) — maps `step_id -> token`.
- `Substrate.load_progress` — `detached={str(k): str(v) for k, v in (fm.get("detached") or {}).items()}` (legacy int values become `"1234"`, a PID-only string → liveness fallback; new values are full tokens). `save_progress` is unchanged (it serialises the dict as-is).
- `worker.run_one_beat` (the `detached:` branch) and `worker.stop_sprint` — read the stored token and pass it to `is_running` / `terminate_detached`; store the token returned by `launch_detached`.
- `dispatcher.run_one_cycle` reconciliation — `any(is_running(tok) for tok in progress.detached.values())`.

- [ ] **Step 1: Write the failing tests**

`tests/test_detached_tokens.py`:
```python
import re

from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.worker import Worker


def test_launch_stores_identity_token(substrate):
    substrate.save_sprint(Sprint(
        id="J", status=SprintStatus.EXECUTING, goals="g",
        plan=[Step("job", "detached: sleep 30")]))
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_sprint_beat(substrate.load_sprint("J"))  # launches the detached job

    token = substrate.load_progress("J").detached["job"]
    assert re.fullmatch(r"\d+:\d+", token)   # "<pid>:<starttime>", not a bare int
    assert is_running(token) is True

    worker.stop_sprint(substrate.load_sprint("J"))       # cleanup
    assert substrate.load_progress("J").detached == {}


def test_legacy_int_detached_value_still_readable(substrate, tmp_path):
    # Simulate an old on-disk progress file whose detached value is a bare int.
    substrate.save_sprint(Sprint(id="L", status=SprintStatus.EXECUTING, goals="g",
                                 plan=[Step("job", "detached: sleep 30")]))
    prog = substrate.load_progress("L")
    prog.detached["job"] = "999999999"   # implausible bare PID (dead) — legacy shape
    substrate.save_progress(prog)
    # Reloads as a string and is_running treats it as plain liveness (dead -> False).
    reloaded = substrate.load_progress("L")
    assert reloaded.detached["job"] == "999999999"
    assert is_running(reloaded.detached["job"]) is False
```
**Implementer note:** use the existing `substrate` test fixture (see `tests/conftest.py`) and the real `Worker` constructor used elsewhere (e.g. in `tests/test_integration_phase1b1.py`). If `Worker` needs an executor argument, construct it the same way the existing integration tests do. The first test launches a real `sleep 30` and must stop it in the same test (it does).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_detached_tokens.py -v`
Expected: FAIL — `detached["job"]` is currently an int-derived value, not a `"<pid>:<starttime>"` token (the `re.fullmatch` for `\d+:\d+` fails).

- [ ] **Step 3: Implement the wiring**

1. `executor.py` — change `launch_detached` to return the token:
```python
def launch_detached(command: str) -> str:
    """Start a shell command fully detached from this process; return its
    identity token '<pid>:<starttime>'."""
    proc = subprocess.Popen(
        command, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process_token(proc.pid)
```
2. `models.py` — `detached: dict[str, str] = field(default_factory=dict)`.
3. `substrate.py` `load_progress` — `detached={str(k): str(v) for k, v in (fm.get("detached") or {}).items()}`.
4. `worker.py` — in the `detached:` branch, rename the local from `pid` to `token` and store/check the token (the logic is otherwise identical); in `stop_sprint`, iterate tokens and call `terminate_detached(token)`.
5. `dispatcher.py` — the reconciliation `any(is_running(...) ...)` iterates `progress.detached.values()` (now tokens); no logic change beyond the variable being a token.

- [ ] **Step 4: Run the new tests, then the FULL suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_detached_tokens.py -v`
Expected: 2 passed.

Run: `/home/oleg/venvs/coscience/bin/python -m pytest`
Expected: all pass. If an existing test asserted an int-typed `detached` value, update it to expect the token string (the kill/resume *behavior* it checks must remain identical). Record the final count.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/executor.py src/coscience/models.py src/coscience/substrate.py src/coscience/worker.py src/coscience/dispatcher.py tests/test_detached_tokens.py
git commit -m "feat: store identity tokens for detached jobs (activate PID-reuse guard)"
```

---

## Self-Review

**Spec coverage:**
- identity token captured + verified before every signal → Task 1 ✓
- fail-safe on mismatch (is_running False, terminate no-op) → Task 1 ✓
- backward tolerance for legacy int PIDs / liveness fallback → Task 1 + Task 2 ✓
- token threaded through storage + all three signal sites (worker ×2, dispatcher) → Task 2 ✓
- suite green at each commit (mechanism first, activation second) → task ordering ✓
- Explicitly deferred: nothing further in this micro-phase; next is Phase 2.

**Placeholder scan:** complete code in every step; real assertions against real processes; no TBD. ✓

**Type consistency:** token is `"<pid>:<starttime>"`; `process_token(pid)->str`; `is_running(int|str)->bool`; `terminate_detached(int|str, grace)->None`; `launch_detached(str)->str`; `ProgressState.detached: dict[str,str]`; substrate coerces values to `str`. ✓

**Known simplifications (intentional):**
- Linux-only (`/proc`), consistent with the existing zombie check; no Windows/macOS path.
- A token whose start time couldn't be read at launch (`"<pid>:"`) degrades to liveness-only — strictly no worse than today, and only happens if the just-launched process vanished before its stat could be read.
- The guard protects the *signal* path; it does not attempt to re-attach to a job whose identity is lost — by design, an unverifiable job is considered gone and its step relaunches on a later beat (same semantics restart-reconciliation already assumes).
```
