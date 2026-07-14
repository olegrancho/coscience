# Detached-Job Harness (Option 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a worker declare a long detached job (`job.json`), keep the sprint `executing` while it runs, and re-launch a fresh worker to assess the output when the job ends / a wake time hits / a watchdog cap trips — with a "sleeping" UI state and a manual wake button.

**Architecture:** Job liveness tracked by a `<pid>:<starttime>` token (reusing `executor.process_token`/`is_running`/`terminate_detached`). State lives in `progress.md`. The `worker.run_sprint_beat` gains a "sleeping" branch that does a cheap per-beat check and only launches a (costly) assess agent on death/wake/watchdog/manual-wake.

**Tech Stack:** Python 3.11+ (stdlib), FastAPI, React + Mantine + TanStack Query.

## Global Constraints

- No new Python dependencies.
- `job.json` shape (worker-written, in `sprint_dir`): `{pid:int, cmd:str, out_file:str, expected_seconds:number, wake_after_seconds:number, max_seconds:number, note:str}`.
- `max_seconds` is clamped to `JOB_MAX_SECONDS` (default `7*24*3600`, env `COSCIENCE_JOB_MAX_SECONDS`).
- Job liveness uses `coscience.executor.is_running(token)` / `process_token(pid)` / `terminate_detached(token)`.
- Undeclared background-and-exit stays forbidden (Option 1); the declared protocol is the only sanctioned background path.
- Sprint stays `executing` for the whole job so the dispatcher keeps its lease.
- Assess-launch happens only on job-death / wake-time / watchdog / manual wake — never per-beat.
- Actor on the wake route is server-derived; route is auth-gated.
- Tests run on the Linux host (`~/venvs/coscience-dev/bin/pytest`).
- Follow existing patterns (dataclasses, `frontmatter_io`, injectable `agent` on `Worker`, `TestClient(build_app(Service(tmp_path)))`, react-query + Mantine).

---

### Task 1: ProgressState job fields + persistence

**Files:**
- Modify: `src/coscience/models.py` (`ProgressState`)
- Modify: `src/coscience/substrate.py` (`load_progress`/`save_progress`)
- Test: `tests/test_progress_job_fields.py`

**Interfaces:**
- Produces: `ProgressState` gains `job_token: str = ""`, `job_out: str = ""`, `job_note: str = ""`, `job_started_at: float | None = None`, `job_expected_seconds: float = 0.0`, `job_next_wake: float = 0.0`, `job_max_seconds: float = 0.0`, `assess_reason: str = ""`. All persist and default cleanly for old files.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_progress_job_fields.py
from coscience.substrate import Substrate
from coscience.models import ProgressState


def test_job_fields_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    p = ProgressState(sprint_id="s1", job_token="123:456", job_out="out.log",
                      job_note="train", job_started_at=10.0, job_expected_seconds=100.0,
                      job_next_wake=110.0, job_max_seconds=200.0, assess_reason="")
    sub.save_progress(p)
    got = sub.load_progress("s1")
    assert got.job_token == "123:456" and got.job_out == "out.log"
    assert got.job_next_wake == 110.0 and got.job_max_seconds == 200.0
    assert got.job_note == "train"


def test_old_progress_defaults_empty_job(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_progress(ProgressState(sprint_id="s2"))
    got = sub.load_progress("s2")
    assert got.job_token == "" and got.job_next_wake == 0.0 and got.assess_reason == ""
```

- [ ] **Step 2: Run to verify it fails** — `~/venvs/coscience-dev/bin/pytest tests/test_progress_job_fields.py -v` → FAIL (unexpected kwargs).

- [ ] **Step 3a: models.py** — add to `ProgressState` (after `last_error`):
```python
    job_token: str = ""                # tracked detached job "<pid>:<starttime>"; "" = none
    job_out: str = ""                  # job output path (relative to sprint dir)
    job_note: str = ""                 # human-readable job description
    job_started_at: float | None = None
    job_expected_seconds: float = 0.0
    job_next_wake: float = 0.0         # absolute ts; wake the agent when now >= this
    job_max_seconds: float = 0.0       # clamped watchdog cap
    assess_reason: str = ""            # "" normal; else "finished"/"timed out"/"wake" -> next launch is an assess run
```

- [ ] **Step 3b: substrate.py `load_progress`** — add to the `ProgressState(...)` call:
```python
            job_token=str(fm.get("job_token", "")),
            job_out=str(fm.get("job_out", "")),
            job_note=str(fm.get("job_note", "")),
            job_started_at=(None if fm.get("job_started_at") is None else float(fm["job_started_at"])),
            job_expected_seconds=float(fm.get("job_expected_seconds", 0.0)),
            job_next_wake=float(fm.get("job_next_wake", 0.0)),
            job_max_seconds=float(fm.get("job_max_seconds", 0.0)),
            assess_reason=str(fm.get("assess_reason", "")),
```

- [ ] **Step 3c: substrate.py `save_progress`** — add the same keys to `fm` (write them unconditionally alongside the existing four; simplest and round-trips cleanly).

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_progress_job_fields.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_progress_job_fields.py
git commit -m "feat(jobs): ProgressState detached-job fields + persistence"
```

---

### Task 2: Worker detached-job lifecycle

**Files:**
- Modify: `src/coscience/worker.py` (`__init__` job-liveness seam; `run_sprint_beat` sleeping branch + record-job-on-exit; `JOB_MAX_SECONDS`)
- Modify: `src/coscience/claude_executor.py` (`ClaudeAgent` unaffected; job.json read helper lives in worker)
- Test: `tests/test_worker_detached_job.py`

**Interfaces:**
- Consumes: Task 1 fields; `executor.process_token`/`is_running`/`terminate_detached`.
- Produces: `Worker(substrate, agent, usage_gate=None, job_alive=None, terminate=None)` — `job_alive(token)->bool` defaults to `executor.is_running`, `terminate(token)` defaults to `executor.terminate_detached` (both injectable for tests). `JOB_MAX_SECONDS` module constant. `_read_job_json(sprint_dir)->dict|None`. Beat behavior per spec §4.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_detached_job.py
import json, time
from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus, BeatOutcome
from coscience.worker import Worker


class FakeAgent:
    """Agent that, on start, optionally writes a job.json (to simulate the worker
    declaring a detached job), and on collect returns a canned (text, status)."""
    def __init__(self, on_start=None, collect_result=("done text", "ok")):
        self.on_start, self._collect = on_start, collect_result
        self.started, self.stopped = [], []
    def start(self, sprint, ctx, sprint_dir, repo_root=None):
        self.started.append(sprint.id)
        if self.on_start:
            self.on_start(sprint_dir)
        return "agent-token"
    def is_running(self, token):
        return False            # agent exits immediately after start
    def stop(self, token):
        self.stopped.append(token)
    def collect(self, sprint_dir):
        return self._collect


def _queued(sub, sid="s1"):
    sub.save_sprint(Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1"))


def test_ok_exit_with_live_job_stays_executing(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    def write_job(sprint_dir):
        (sprint_dir / "job.json").write_text(json.dumps(
            {"pid": 1, "out_file": "j.out", "expected_seconds": 5,
             "wake_after_seconds": 10, "max_seconds": 60, "note": "train"}))
    w = Worker(sub, FakeAgent(on_start=write_job), job_alive=lambda t: True)
    w.run_one_beat()                       # claim -> launch agent
    out = w.run_one_beat()                 # agent exited + job.json declared
    sp = sub.load_sprint("s1")
    assert sp.status == SprintStatus.EXECUTING          # NOT done
    prog = sub.load_progress("s1")
    assert prog.job_token and prog.job_note == "train"
    assert not (sub.sprint_dir("s1") / "job.json").exists()   # consumed
    assert sp.results == []


def test_dead_job_relaunches_assess_then_done(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_out, prog.job_note = "1:1", "j.out", "train"
    prog.job_next_wake = time.time() + 9999; prog.job_max_seconds = 9999
    sub.save_sprint(sub.load_sprint("s1"))
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    sub.save_progress(prog)
    w = Worker(sub, FakeAgent(collect_result=("final findings", "ok")), job_alive=lambda t: False)
    w.run_sprint_beat(sub.load_sprint("s1"))   # job dead -> assess launch
    w.run_sprint_beat(sub.load_sprint("s1"))   # assess agent exits ok, no job -> done
    assert sub.load_sprint("s1").status == SprintStatus.DONE


def test_watchdog_terminates_overrun_job(tmp_path):
    sub = Substrate(tmp_path); _queued(sub)
    s = sub.load_sprint("s1"); s.status = SprintStatus.EXECUTING; sub.save_sprint(s)
    prog = sub.load_progress("s1")
    prog.job_token, prog.job_out = "1:1", "j.out"
    prog.job_started_at = 0.0; prog.job_max_seconds = 1.0; prog.job_next_wake = 9e18
    sub.save_progress(prog)
    killed = []
    w = Worker(sub, FakeAgent(), job_alive=lambda t: True, terminate=lambda t: killed.append(t))
    w.run_sprint_beat(sub.load_sprint("s1"))
    assert killed == ["1:1"]
    assert sub.load_progress("s1").assess_reason == "timed out"
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_worker_detached_job.py -v` → FAIL.

- [ ] **Step 3a: Worker seams + constant.** In `worker.py`, top-level:
```python
import os
from coscience.executor import is_running as _job_is_running, process_token, terminate_detached as _terminate

JOB_MAX_SECONDS = float(os.environ.get("COSCIENCE_JOB_MAX_SECONDS", 7 * 24 * 3600))
```
`Worker.__init__` — add params `job_alive=None, terminate=None` and store `self._job_alive = job_alive or _job_is_running`, `self._terminate = terminate or _terminate`.

Add a helper:
```python
    def _read_job_json(self, sprint_dir):
        f = sprint_dir / "job.json"
        if not f.is_file():
            return None
        try:
            d = json.loads(f.read_text())
            return d if isinstance(d, dict) and d.get("pid") else None
        except (json.JSONDecodeError, ValueError, OSError):
            return None
```

- [ ] **Step 3b: Sleeping branch** — at the very top of `run_sprint_beat` (after loading `progress`/`sprint_dir`), before the "no agent" step, insert:
```python
        # A) sleeping on a tracked detached job (no agent runs it) — cheap check only.
        if progress.job_token and not self.agent.is_running(progress.agent_token):
            now = time.time()
            if not self._job_alive(progress.job_token):
                progress.assess_reason = "finished"
            elif progress.job_max_seconds and progress.job_started_at is not None \
                    and now - progress.job_started_at > progress.job_max_seconds:
                self._terminate(progress.job_token)
                progress.assess_reason = "timed out"
            elif progress.job_next_wake and now >= progress.job_next_wake:
                progress.assess_reason = "wake"
            else:
                return BeatOutcome.PROGRESSED                # keep waiting; lease held
            progress.job_token = ""                          # stop tracking; assess run takes over
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: job ended ({progress.assess_reason}), assessing")
            # fall through to step 1, which launches an assess agent (assess_reason set)
```

- [ ] **Step 3c: Record a declared job on clean exit.** In step 3 (agent ended), AFTER `text, status = self.agent.collect(...)` and the feedback harvest, and AFTER the interrupted/failed handling but BEFORE building the `Result`/marking done, insert:
```python
        job = self._read_job_json(sprint_dir)
        if status == "ok" and job is not None:
            now = time.time()
            progress.job_token = process_token(int(job["pid"]))
            progress.job_out = str(job.get("out_file", ""))
            progress.job_note = str(job.get("note", ""))
            progress.job_started_at = now
            progress.job_expected_seconds = float(job.get("expected_seconds", 0) or 0)
            progress.job_next_wake = now + float(job.get("wake_after_seconds", 0) or 0)
            progress.job_max_seconds = min(float(job.get("max_seconds", 0) or 0) or JOB_MAX_SECONDS,
                                           JOB_MAX_SECONDS)
            progress.assess_reason = ""
            progress.agent_token = ""
            (sprint_dir / "job.json").unlink(missing_ok=True)     # consume it
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: detached job declared ({progress.job_note})")
            return BeatOutcome.PROGRESSED                          # stay executing, sleep on the job
```
(Place this so a declared job takes precedence over the normal done path; the agent's premature final message is ignored.)

- [ ] **Step 3d: Clear job fields on done + carry assess through relaunch.** When the sprint is marked `DONE` (the existing result block), also reset the job/assess fields on `progress` before saving (`progress.job_out = progress.job_note = progress.assess_reason = ""`, `progress.job_next_wake = progress.job_max_seconds = 0.0`, `progress.job_started_at = None`). The assess launch itself is just the normal step-1 launch (no change needed there); the context uses `assess_reason`/`job_out` (Task 3).

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_worker_detached_job.py tests/test_worker.py -v`. Expected PASS (existing worker tests unaffected — no job.json ⇒ old path).

- [ ] **Step 5: Commit**
```bash
git add src/coscience/worker.py tests/test_worker_detached_job.py
git commit -m "feat(jobs): worker tracks declared detached jobs; sleep/wake/watchdog/assess lifecycle"
```

---

### Task 3: Job protocol + assess context in worker instructions

**Files:**
- Modify: `src/coscience/executor.py` (`ExecutionContext` job fields)
- Modify: `src/coscience/worker.py` (`_build_context` populates job fields from `progress`)
- Modify: `src/coscience/claude_executor.py` (`build_instructions`: job protocol section + assess section; amend rule 4)
- Test: `tests/test_job_instructions.py`

**Interfaces:**
- Consumes: Task 1/2.
- Produces: `ExecutionContext` gains `assess_reason: str = ""`, `job_out: str = ""`, `job_note: str = ""`. `build_instructions` renders (a) always: the detached-job protocol (how to declare `job.json`), and (b) when `assess_reason`: an assess section pointing at `job_out`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_job_instructions.py
from pathlib import Path
from coscience.claude_executor import build_instructions
from coscience.executor import ExecutionContext
from coscience.models import Sprint, SprintStatus


def _sprint():
    return Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"])


def test_protocol_documented_and_max_seconds():
    txt = build_instructions(_sprint(), ExecutionContext(), Path("/tmp/s1/scratchpad.md"))
    assert "job.json" in txt and "wake_after_seconds" in txt and "expected_seconds" in txt


def test_assess_section_when_reason_set():
    ctx = ExecutionContext(assess_reason="finished", job_out="j.out", job_note="train")
    txt = build_instructions(_sprint(), ctx, Path("/tmp/s1/scratchpad.md"))
    assert "j.out" in txt and ("finished" in txt or "assess" in txt.lower())
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_job_instructions.py -v` → FAIL.

- [ ] **Step 3a: ExecutionContext** — add `assess_reason: str = ""`, `job_out: str = ""`, `job_note: str = ""` to the dataclass in `executor.py`.

- [ ] **Step 3b: `_build_context`** (worker.py) — set these from `progress`:
```python
        progress = self.substrate.load_progress(sprint.id)
        ...
        ctx.assess_reason = progress.assess_reason
        ctx.job_out = progress.job_out
        ctx.job_note = progress.job_note
```
(Read the real `_build_context` to place this; it currently builds an `ExecutionContext` from program/prior results.)

- [ ] **Step 3c: `build_instructions`** — amend rule 4 to end with: "...unless you use the DETACHED-JOB PROTOCOL below." Add a protocol section:
```
## Long jobs: the detached-job protocol
If a job is too long to finish in this session, you MAY hand it to the platform instead of
running it foreground:
1. Launch it detached, capturing its pid and streaming output to a file in THIS sprint folder:
   `nohup <cmd> > <out_file> 2>&1 & echo $!`
2. Write `<sprint_dir>/job.json`:
   {"pid": <the pid>, "cmd": "<cmd>", "out_file": "<out_file>",
    "expected_seconds": <your estimate>, "wake_after_seconds": <when to bring you back>,
    "max_seconds": <hard cap>, "note": "<short description>"}
3. End your turn. The platform keeps the sprint running, waits for the job (or until
   wake_after_seconds), then launches you again to check its output. You MUST fill
   expected_seconds, wake_after_seconds, and max_seconds honestly.
```
And an assess section, rendered only when `context.assess_reason`:
```
## Resuming to check a detached job ({assess_reason})
A previous run launched a detached job ("{job_note}"); its output is at {job_out}.
Read it and decide: if the goal is met, produce the final result; if the job needs
more time and is still healthy, re-declare job.json with a new wake time; if it failed,
either relaunch it or report the failure. If you abandon a still-running job, kill it first.
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_job_instructions.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add src/coscience/executor.py src/coscience/worker.py src/coscience/claude_executor.py tests/test_job_instructions.py
git commit -m "feat(jobs): document detached-job protocol + assess context in worker instructions"
```

---

### Task 4: Service + HTTP — agent_state, job block, wake, stop kills job

**Files:**
- Modify: `src/coscience/service.py` (`get_sprint` adds `agent_state` + `job`; new `wake_sprint`)
- Modify: `src/coscience/worker.py` (`stop_sprint` also terminates the job)
- Modify: `src/coscience/http_api.py` (`POST /sprints/{id}/wake`)
- Test: `tests/test_http_job_wake.py`

**Interfaces:**
- Produces: `get_sprint` returns `agent_state` ∈ {`running`,`sleeping`,`idle`} and, when sleeping, `job: {note, out_file, started_at, expected_seconds, next_wake, max_seconds}`. `service.wake_sprint(sprint_id)` sets `job_next_wake = now`. `stop_sprint` calls `self._terminate(progress.job_token)` and clears it.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_http_job_wake.py
import time
from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus, ProgressState


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"], program="p1")
    svc.substrate.save_sprint(s)
    return svc


def test_sleeping_state_and_wake(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_progress(ProgressState(sprint_id="s1", job_token="1:1", job_note="train",
        job_out="j.out", job_started_at=time.time(), job_next_wake=time.time() + 9999,
        job_max_seconds=9999))
    c = TestClient(build_app(svc))
    got = c.get("/api/sprints/s1").json()
    assert got["agent_state"] == "sleeping" and got["job"]["note"] == "train"
    assert c.post("/api/sprints/s1/wake").status_code == 200
    assert svc.substrate.load_progress("s1").job_next_wake <= time.time() + 1
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_http_job_wake.py -v` → FAIL.

- [ ] **Step 3a: `get_sprint`** — compute state (import `executor` at top or lazily):
```python
        prog = progress   # already loaded
        if prog.job_token:
            agent_state = "sleeping"
        elif prog.agent_token:
            agent_state = "running"
        else:
            agent_state = "idle"
        job = None
        if prog.job_token:
            job = {"note": prog.job_note, "out_file": prog.job_out,
                   "started_at": prog.job_started_at, "expected_seconds": prog.job_expected_seconds,
                   "next_wake": prog.job_next_wake, "max_seconds": prog.job_max_seconds}
```
Add `"agent_state": agent_state, "job": job` to the returned dict. (Note: the existing `agent_running` key already exists — keep it; `agent_state` is the richer signal.)

- [ ] **Step 3b: `wake_sprint`** (service):
```python
    def wake_sprint(self, sprint_id: str) -> dict:
        self._load_sprint(sprint_id)                 # 404 if missing
        prog = self.substrate.load_progress(sprint_id)
        if prog.job_token:
            prog.job_next_wake = time.time()
            self.substrate.save_progress(prog)
            self.substrate.commit(f"sprint {sprint_id}: wake requested")
        return self.get_sprint(sprint_id)
```

- [ ] **Step 3c: `stop_sprint`** (worker) — before/after stopping the agent, also kill a tracked job:
```python
        if progress.job_token:
            try:
                self._terminate(progress.job_token)
            except Exception:
                pass
            progress.job_token = ""
```
(Keep the existing agent-stop path; return `[sprint.id]` if either an agent or a job was stopped.)

- [ ] **Step 3d: HTTP route** (gated `api` router):
```python
    @api.post("/sprints/{sprint_id}/wake")
    def wake_sprint(sprint_id: str, user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.wake_sprint(sprint_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_http_job_wake.py tests/test_http_api.py -p no:warnings -q` → PASS.

- [ ] **Step 5: Commit**
```bash
git add src/coscience/service.py src/coscience/worker.py src/coscience/http_api.py tests/test_http_job_wake.py
git commit -m "feat(jobs): agent_state + job block in get_sprint; wake route; stop_sprint kills job"
```

---

### Task 5: Frontend — sleeping panel + Wake button

**Files:**
- Modify: `frontend/src/api.ts` (types + `wakeSprint`)
- Modify: `frontend/src/views/SprintDetail.tsx` (sleeping panel + Wake button)
- Verify: `npm run build`

**Interfaces:**
- Consumes: Task 4 API.
- Produces: `Sprint.agent_state?: "running"|"sleeping"|"idle"`, `Sprint.job?: {note,out_file,started_at,expected_seconds,next_wake,max_seconds} | null`; `api.wakeSprint(id)`.

- [ ] **Step 1: api.ts** — add to the `Sprint` interface:
```typescript
  agent_state?: "running" | "sleeping" | "idle";
  job?: { note: string; out_file: string; started_at: number | null;
          expected_seconds: number; next_wake: number; max_seconds: number } | null;
```
and an endpoint:
```typescript
  wakeSprint: (id: string) => fetch(`/api/sprints/${id}/wake`, { method: "POST" }).then(j<Sprint>),
```

- [ ] **Step 2: SprintDetail** — when `s.agent_state === "sleeping"`, render a panel near the status row:
```tsx
{s.agent_state === "sleeping" && s.job && (
  <Card withBorder padding="sm" mt={10} style={{ background: "var(--paper)" }}>
    <Group justify="space-between" wrap="nowrap">
      <div>
        <Text size="sm" fw={600}>💤 Agent sleeping — waiting on a detached job</Text>
        <Text size="xs" c="dimmed">{s.job.note || "(job)"} · expected ~{Math.round(s.job.expected_seconds/60)}m · next check <RelTime at={s.job.next_wake} /></Text>
      </div>
      <Button size="xs" onClick={wake}>Wake now</Button>
    </Group>
  </Card>
)}
```
with a handler:
```tsx
const wake = async () => {
  try { await api.wakeSprint(id); notifications.show({ color: "teal", title: "Waking the agent", message: "It'll check the job on the next beat." }); refresh(); }
  catch (e) { notifications.show({ color: "red", title: "Couldn't wake", message: String(e) }); }
};
```
(Read the current SprintDetail imports/`refresh()`/`id` to wire this; `RelTime`, `Card`, `Button`, `Group`, `Text`, `notifications` are already used in the file.)

- [ ] **Step 3: Build** — `cd frontend && PATH=$HOME/node20/bin:$PATH npm run build` → `✓ built`, no type errors.

- [ ] **Step 4: Commit**
```bash
git add frontend/src/api.ts frontend/src/views/SprintDetail.tsx
git commit -m "feat(jobs): sleeping-agent panel + Wake now button"
```

---

## Notes for the implementer

- Run tests on the Linux host (`~/venvs/coscience-dev/bin/pytest`); runtime is Linux-only.
- The beat ordering matters: the **sleeping branch (2/3b) must run before the "no agent → launch" step**, and **record-job (3c) must run before the normal done path** — otherwise a declared job is either ignored or the sprint is finalized prematurely.
- `job_token` set ⇒ "sleeping"; it's cleared the moment an assess run is triggered (the assess agent owns the job from then on). `assess_reason`/`job_out`/`job_note` persist into the assess run for context and are cleared when the sprint is `done` or a new job is declared.
- A job whose pid is already dead when first read (fast job) is recorded, then the next beat's sleeping branch sees it dead → assess. Uniform, one extra beat.
- Do NOT add per-beat agent launches — the sleeping branch must only *launch* on death/wake/watchdog (it sets `assess_reason` and falls through to the single launch site).
- Preemption already routes through `worker.stop_sprint` (dispatcher.py:91,108), so killing the job there covers preemption automatically.
