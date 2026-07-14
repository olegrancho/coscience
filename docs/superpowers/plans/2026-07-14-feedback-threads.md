# Threaded Feedback + PM Compute Edits — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the PM edit a sprint's compute, and turn every human→agent feedback surface (sprint→planner, sprint→worker, idea, guidance) into a two-way thread where the responsible agent acts and replies.

**Architecture:** A shared thread structure (`list[dict]`) stored inline where feedback lives today. The PM replies via structured `thread_replies` in its reasoning cycle; the worker replies via a `feedback.out` file the dispatch beat harvests. A single `<FeedbackThread>` React component renders all surfaces. Legacy comments/notes adapt to one-message threads on load (no migration).

**Tech Stack:** Python 3.11+ (stdlib), PyYAML, FastAPI, React + Mantine + TanStack Query.

## Global Constraints

- No new Python dependencies.
- Thread shape: `{id, target: "pm"|"worker", status: "open"|"complete", agent_unseen: bool, created_at, messages: [{role: "human"|"pm"|"worker", text, by, at}]}`.
- Actor (`by`) on human messages is server-derived from `current_user`; never trust client `by`.
- Reply cadence: an agent answers a thread only when `status=="open"` AND its last message role is `"human"`. After it replies, last role is the agent → not re-answered until the human adds a message. Completed threads are excluded from agent context.
- Back-compat: legacy `comments`/guidance `notes` load as a single `human`-message open thread; `by` defaults `""`, rendered "—".
- `agent_unseen` is one shared per-thread boolean (not per-user): set on any agent reply, cleared when any user opens the thread.
- Worker threads (`target: worker`) are answered only while the sprint is `executing`.
- Follow existing patterns (dataclasses, `frontmatter_io`, `list[dict]` fields like `comments`/`votes`, routes as closures over `service`, `TestClient(build_app(Service(tmp_path)))`, react-query + Mantine).
- Tests run on the Linux host (`~/venvs/coscience-dev/bin/pytest`), not locally (Linux-only runtime).

---

### Task 1: PM can edit `resources_required`

**Files:**
- Modify: `src/coscience/pm_claude.py` (sprint_edits schema + one prompt line)
- Modify: `src/coscience/pm_agent.py:365-386` (apply resources on edit)
- Test: `tests/test_pm_resources_edit.py`

**Interfaces:**
- Produces: PM `sprint_edits` items may carry `resources_required: {name: number} | null`; `apply` sets it on any non-done/canceled sprint.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pm_resources_edit.py
from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus, Program, ProgramStatus, PMState
from coscience.pm_reasoner import PMCycleOutput
from coscience import pm_agent


def _sprint(sub, sid, status):
    sub.save_sprint(Sprint(id=sid, status=status, goals="g", plan=["a"],
                           program="p1", resources_required={"gpu": 1}))


def test_pm_edit_sets_resources_on_queued(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    sub.save_pm_state(PMState(program_id="p1"))
    _sprint(sub, "p1-c0-x", SprintStatus.QUEUED)
    out = PMCycleOutput(report="r", proposals=[], delete_idea_ids=[],
                        sprint_edits=[{"sprint_id": "p1-c0-x",
                                       "resources_required": {"cpu": 2}}],
                        reopen_ids=[], release_ids=[], thread_replies=[])
    pm_agent.apply_cycle(sub, "p1", pm_agent.StagedCycle(cycle=0, output=out))
    assert sub.load_sprint("p1-c0-x").resources_required == {"cpu": 2.0}
```

> Note: match `apply_cycle`/`StagedCycle`/`PMCycleOutput` to their real signatures (read `pm_agent.py` + `pm_reasoner.py` first). `thread_replies` is added in Task 4 — if `PMCycleOutput` has no such field yet, omit it here and rely on the default.

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_pm_resources_edit.py -v`
Expected: FAIL (resources unchanged — apply ignores the field).

- [ ] **Step 3a: Schema + prompt (`pm_claude.py`)**

In the `sprint_edits` schema item (the object with `sprint_id`/`goals`/`plan`/`summary`/`title`/`priority`), add:
```
      "resources_required": {{}} or null,
```
Add one line to the prompt's sprint-edit guidance (near the `sprint_edits` explanation): `You may also change an editable sprint's resources_required (compute) here in response to feedback — e.g. drop a gpu the environment can't provide and run on cpu.`

- [ ] **Step 3b: Apply (`pm_agent.py`)** — in the `for edit in staged.output.sprint_edits:` loop (after the `priority` block, before `substrate.save_sprint(sp)`), add:
```python
        if "resources_required" in edit and edit["resources_required"] is not None:
            from coscience.pm_reasoner import coerce_resources
            sp.resources_required = coerce_resources(edit["resources_required"])
```
(`coerce_resources` already normalizes `{name: number}`. Editability: the loop already guards `sp.status in _EDITABLE` = proposed/approved/queued, which excludes done/canceled — correct for compute.)

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_pm_resources_edit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/coscience/pm_claude.py src/coscience/pm_agent.py tests/test_pm_resources_edit.py
git commit -m "feat(pm): allow editing a sprint's resources_required from feedback"
```

---

### Task 2: Shared thread helpers (`coscience/threads.py`)

**Files:**
- Create: `src/coscience/threads.py`
- Test: `tests/test_threads.py`

**Interfaces:**
- Produces:
  - `new_thread(target: str, text: str, by: str, role: str = "human", now: float) -> dict`
  - `append(thread: dict, role: str, text: str, by: str, now: float) -> None` — appends a message; if `role == "human"` and thread was `complete`, reopens it (`status="open"`); if role is an agent, sets `agent_unseen=True`.
  - `adapt_legacy(comment: dict, default_target: str, now: float) -> dict` — wraps a legacy `{text, added_at, target?, by?}` into a one-`human`-message open thread.
  - `needs_reply(thread: dict) -> bool` — `status=="open" and messages and messages[-1]["role"]=="human"`.
  - `public(thread: dict) -> dict` — normalized copy for the API.
  - Deterministic ids via `uuid4().hex[:8]` (pass `now`/ids in; no `Date.now`-style nondeterminism in tests — accept `now` param).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_threads.py
from coscience import threads


def test_new_and_needs_reply():
    t = threads.new_thread("pm", "run on cpu", "stroganov", now=1.0)
    assert t["target"] == "pm" and t["status"] == "open" and t["agent_unseen"] is False
    assert t["messages"] == [{"role": "human", "text": "run on cpu", "by": "stroganov", "at": 1.0}]
    assert threads.needs_reply(t) is True


def test_agent_reply_sets_unseen_and_stops_needing():
    t = threads.new_thread("pm", "x", "u", now=1.0)
    threads.append(t, "pm", "done — set cpu", "", now=2.0)
    assert t["agent_unseen"] is True
    assert threads.needs_reply(t) is False           # last msg is agent


def test_human_append_reopens_completed():
    t = threads.new_thread("pm", "x", "u", now=1.0)
    t["status"] = "complete"
    threads.append(t, "human", "one more thing", "u", now=3.0)
    assert t["status"] == "open" and threads.needs_reply(t) is True


def test_adapt_legacy_comment():
    t = threads.adapt_legacy({"text": "old", "added_at": 5.0, "target": "worker", "by": "u"},
                             default_target="pm", now=9.0)
    assert t["target"] == "worker" and t["status"] == "open"
    assert t["messages"][0] == {"role": "human", "text": "old", "by": "u", "at": 5.0}
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_threads.py -v`
Expected: FAIL (`No module named 'coscience.threads'`)

- [ ] **Step 3: Implement**

```python
# src/coscience/threads.py
"""A shared feedback-thread structure: a human↔agent conversation stored inline
(on a sprint, idea, or program guidance). Plain dicts, mirroring comments/votes."""
from __future__ import annotations

from uuid import uuid4

AGENT_ROLES = ("pm", "worker")


def new_thread(target: str, text: str, by: str, *, role: str = "human",
               now: float, tid: str | None = None) -> dict:
    t = {"id": tid or uuid4().hex[:8], "target": target, "status": "open",
         "agent_unseen": False, "created_at": now, "messages": []}
    append(t, role, text, by, now=now)
    return t


def append(thread: dict, role: str, text: str, by: str, *, now: float) -> None:
    thread.setdefault("messages", []).append(
        {"role": role, "text": str(text), "by": str(by or ""), "at": now})
    if role == "human":
        if thread.get("status") == "complete":
            thread["status"] = "open"
    elif role in AGENT_ROLES:
        thread["agent_unseen"] = True


def needs_reply(thread: dict) -> bool:
    msgs = thread.get("messages") or []
    return thread.get("status") == "open" and bool(msgs) and msgs[-1]["role"] == "human"


def adapt_legacy(comment: dict, default_target: str, *, now: float) -> dict:
    return {"id": str(comment.get("id") or uuid4().hex[:8]),
            "target": str(comment.get("target") or default_target),
            "status": "open", "agent_unseen": False,
            "created_at": float(comment.get("added_at", now)),
            "messages": [{"role": "human", "text": str(comment.get("text", "")),
                          "by": str(comment.get("by", "")),
                          "at": float(comment.get("added_at", now))}]}


def public(thread: dict) -> dict:
    return {"id": thread["id"], "target": thread.get("target", "pm"),
            "status": thread.get("status", "open"),
            "agent_unseen": bool(thread.get("agent_unseen", False)),
            "created_at": thread.get("created_at", 0.0),
            "messages": [{"role": m["role"], "text": m["text"], "by": m.get("by", ""),
                          "at": m["at"]} for m in thread.get("messages", [])]}
```

- [ ] **Step 4: Run to verify it passes**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_threads.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/coscience/threads.py tests/test_threads.py
git commit -m "feat(threads): shared feedback-thread helpers"
```

---

### Task 3: Sprint threads — model + persistence + back-compat

**Files:**
- Modify: `src/coscience/models.py` (`Sprint`: `comments` → `threads`)
- Modify: `src/coscience/substrate.py` (`load_sprint`/`save_sprint`)
- Test: `tests/test_sprint_threads_store.py`

**Interfaces:**
- Produces: `Sprint.threads: list[dict]` (feedback threads). `load_sprint` reads a `threads` frontmatter key if present, else adapts a legacy `comments` list via `threads.adapt_legacy` (sprint comments carry `target`, default `"worker"`). `save_sprint` writes `threads`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sprint_threads_store.py
from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus
from coscience import threads


def test_threads_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"])
    s.threads.append(threads.new_thread("pm", "hi", "u", now=1.0))
    sub.save_sprint(s)
    got = sub.load_sprint("s1")
    assert len(got.threads) == 1 and got.threads[0]["messages"][0]["text"] == "hi"


def test_legacy_comments_adapt_to_threads(tmp_path):
    sub = Substrate(tmp_path)
    # write a sprint.md with the OLD comments shape, no threads key
    d = sub.sprint_dir("s2"); d.mkdir(parents=True, exist_ok=True)
    (d / "sprint.md").write_text(
        "---\nstatus: proposed\ngoals: g\nplan: [a]\n"
        "comments:\n  - id: c1\n    text: legacy note\n    added_at: 5.0\n    target: pm\n"
        "---\n# s2\n")
    got = sub.load_sprint("s2")
    assert len(got.threads) == 1
    assert got.threads[0]["target"] == "pm"
    assert got.threads[0]["messages"][0]["text"] == "legacy note"
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_sprint_threads_store.py -v`
Expected: FAIL (`Sprint` has no `threads`).

- [ ] **Step 3a: Model** — in `src/coscience/models.py`, replace the `Sprint.comments` field with:
```python
    threads: list[dict] = field(default_factory=list)  # feedback threads (see coscience.threads)
```

- [ ] **Step 3b: Substrate load** — in `load_sprint`, replace the `comments=[...]` argument to `Sprint(...)` with a `threads=...` value computed just before the constructor:
```python
        import time as _t
        from coscience import threads as _th
        if "threads" in fm:
            sprint_threads = list(fm.get("threads") or [])
        else:  # back-compat: adapt legacy comments (target defaults to worker)
            sprint_threads = [_th.adapt_legacy(c, "worker", now=float(c.get("added_at", _t.time())))
                              for c in fm.get("comments", [])]
```
and pass `threads=sprint_threads` to `Sprint(...)` (drop the old `comments=` kwarg).

- [ ] **Step 3c: Substrate save** — in `save_sprint`, replace the `if sprint.comments: fm["comments"] = list(sprint.comments)` block with:
```python
        if sprint.threads:
            fm["threads"] = list(sprint.threads)
```

- [ ] **Step 4: Run to verify it passes**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_sprint_threads_store.py -v`
Expected: PASS

- [ ] **Step 5: Fix compile-time fallout, then commit.** Grep for remaining `.comments` on sprints and fix readers in later tasks; for now ensure the package imports:
```
~/venvs/coscience-dev/bin/python -c "import coscience.substrate, coscience.models"
```
Then commit:
```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_sprint_threads_store.py
git commit -m "feat(threads): sprint.threads model + persistence + legacy-comment adaptation"
```

> Callers of the old `sprint.comments` (`pm_agent.gather_context:87`, `service.get_sprint`/`add_sprint_comment`/`list_sprints`, `claude_executor` via ExecutionContext.human_comments) are updated in Tasks 4/5/7. Until then some tests referencing sprint comments may break — that's expected and fixed there.

---

### Task 4: PM answers sprint→planner threads (act + reply)

**Files:**
- Modify: `src/coscience/pm_reasoner.py` (`PMCycleOutput`: add `thread_replies`)
- Modify: `src/coscience/pm_agent.py` (`gather_context` surfaces open planner threads; `apply_cycle` appends PM replies)
- Modify: `src/coscience/pm_claude.py` (context rendering + `thread_replies` schema + prompt)
- Test: `tests/test_pm_thread_reply.py`

**Interfaces:**
- Consumes: Task 2 helpers, Task 3 `Sprint.threads`.
- Produces: `PMCycleOutput.thread_replies: list[dict]` (`[{thread_id, text}]`); `gather_context` puts open, human-last `target=="pm"` sprint threads (with history) into `PMContext.sprint_feedback` items as `{sprint_id, thread_id, editable, messages}`; `apply_cycle` appends each reply as a `pm` message on the matching thread and saves.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pm_thread_reply.py
from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus, Program, ProgramStatus, PMState
from coscience.pm_reasoner import PMCycleOutput
from coscience import pm_agent, threads


def test_pm_reply_appended_to_thread(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    sub.save_pm_state(PMState(program_id="p1"))
    s = Sprint(id="p1-c0-x", status=SprintStatus.QUEUED, goals="g", plan=["a"], program="p1")
    th = threads.new_thread("pm", "change to cpu", "stroganov", now=1.0)
    s.threads.append(th); sub.save_sprint(s)

    ctx = pm_agent.gather_context(sub, "p1")
    fb = [f for f in ctx.sprint_feedback if f["sprint_id"] == "p1-c0-x"]
    assert fb and fb[0]["thread_id"] == th["id"]      # surfaced to the PM

    out = PMCycleOutput(report="r", proposals=[], delete_idea_ids=[], sprint_edits=[],
                        reopen_ids=[], release_ids=[],
                        thread_replies=[{"thread_id": th["id"], "text": "done — set cpu"}])
    pm_agent.apply_cycle(sub, "p1", pm_agent.StagedCycle(cycle=0, output=out))
    got = sub.load_sprint("p1-c0-x").threads[0]
    assert got["messages"][-1] == {"role": "pm", "text": "done — set cpu", "by": "", "at": got["messages"][-1]["at"]}
    assert got["agent_unseen"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_pm_thread_reply.py -v`
Expected: FAIL.

- [ ] **Step 3a: `PMCycleOutput`** — add field `thread_replies: list[dict] = field(default_factory=list)` in `pm_reasoner.py`, and parse it in `pm_claude.py`'s output builder (`thread_replies=[dict(r) for r in data.get("thread_replies", [])]`).

- [ ] **Step 3b: `gather_context`** — replace the `pm_notes`/`sprint_feedback` block (`pm_agent.py:87-95`) so it surfaces open planner threads with history:
```python
        from coscience import threads as _th
        for th in s.threads:
            if th.get("target") == "pm" and _th.needs_reply(th) and s.status != SprintStatus.CANCELED:
                sprint_feedback.append({
                    "sprint_id": s.id, "goals": s.goals, "status": s.status.value,
                    "editable": s.status in (SprintStatus.PROPOSED, SprintStatus.APPROVED, SprintStatus.QUEUED),
                    "thread_id": th["id"],
                    "messages": [{"role": m["role"], "text": m["text"]} for m in th["messages"]],
                })
```
Update `_context_payload` (`pm_agent.py:34-35`): fingerprint on `(sprint_id, thread_id, last-human-text)` so a new human message re-triggers:
```python
        "sprint_feedback": sorted((f["sprint_id"], f["thread_id"], f["messages"][-1]["text"])
                                  for f in context.sprint_feedback),
```

- [ ] **Step 3c: `apply_cycle`** — after the existing sprint_edits loop, add a thread-reply loop:
```python
    from coscience import threads as _th
    import time as _t
    replies = {r["thread_id"]: r["text"] for r in staged.output.thread_replies if r.get("thread_id")}
    if replies:
        for s in substrate.iter_sprints():
            if s.program != program_id:
                continue
            touched = False
            for th in s.threads:
                if th["id"] in replies and _th.needs_reply(th):
                    _th.append(th, "pm", replies[th["id"]], "", now=_t.time())
                    touched = True
            if touched:
                substrate.save_sprint(s)
```

- [ ] **Step 3d: `pm_claude.py` prompt** — render each `sprint_feedback` item as a thread (id + message history) and add to the output schema:
```
  "thread_replies": [{{"thread_id": "<id of an open feedback thread shown above>",
                       "text": "<short reply: what you did in response, or why you can't>"}}],
```
Add prompt guidance: `FEEDBACK THREADS: for each open thread shown, take the action it asks for (edit the sprint, change compute, propose, curate) AND add a short thread_replies entry saying what you did. If you can't, say why.`

- [ ] **Step 4: Run to verify it passes**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_pm_thread_reply.py tests/test_pm_resources_edit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/coscience/pm_reasoner.py src/coscience/pm_agent.py src/coscience/pm_claude.py tests/test_pm_thread_reply.py
git commit -m "feat(pm): answer sprint planner-feedback threads (act + reply)"
```

---

### Task 5: Service + HTTP for sprint threads

**Files:**
- Modify: `src/coscience/service.py` (`get_sprint` projection; `add_sprint_comment` → thread start/append; new `complete_sprint_thread`, `seen_sprint_thread`)
- Modify: `src/coscience/http_api.py` (route bodies + new routes)
- Modify: `src/coscience/pm_agent.py` ExecutionContext build (worker feedback text) — see note
- Test: `tests/test_http_sprint_threads.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `get_sprint` returns `threads: [threads.public(t)]` (replacing `comments`).
  - `add_sprint_comment(sprint_id, text, target="worker", by="", thread_id="")` — with `thread_id`, appends a human message (reopen if complete); else starts a new thread. Returns the thread's `public`.
  - `complete_sprint_thread(sprint_id, thread_id)` / `seen_sprint_thread(sprint_id, thread_id)`.
  - Routes: `POST /sprints/{id}/comments` (unchanged path; body may include `thread_id`), `POST /sprints/{id}/threads/{tid}/complete`, `POST /sprints/{id}/threads/{tid}/seen`. Attributed routes derive `by` from `current_user`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_sprint_threads.py
from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus


def _c(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"], program="p1"))
    return TestClient(build_app(svc)), svc


def test_start_append_complete_seen(tmp_path):
    c, svc = _c(tmp_path)
    r = c.post("/api/sprints/s1/comments", json={"text": "cpu please", "target": "pm"})
    tid = r.json()["id"]
    assert r.status_code == 201 and r.json()["messages"][0]["text"] == "cpu please"
    # simulate a PM reply landing on the thread
    s = svc.substrate.load_sprint("s1"); 
    from coscience import threads as th; th.append(s.threads[0], "pm", "done", "", now=2.0)
    svc.substrate.save_sprint(s)
    got = c.get("/api/sprints/s1").json()
    assert got["threads"][0]["agent_unseen"] is True
    assert c.post(f"/api/sprints/s1/threads/{tid}/seen").status_code == 200
    assert c.get("/api/sprints/s1").json()["threads"][0]["agent_unseen"] is False
    c.post("/api/sprints/s1/comments", json={"text": "more", "target": "pm", "thread_id": tid})
    assert len(c.get("/api/sprints/s1").json()["threads"][0]["messages"]) == 3
    assert c.post(f"/api/sprints/s1/threads/{tid}/complete").status_code == 200
    assert c.get("/api/sprints/s1").json()["threads"][0]["status"] == "complete"
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_http_sprint_threads.py -v`
Expected: FAIL.

- [ ] **Step 3a: Service** — rewrite `add_sprint_comment` to use threads:
```python
    def add_sprint_comment(self, sprint_id, text, target="worker", by="", thread_id=""):
        from coscience import threads as th
        text = text.strip()
        if not text:
            raise ValueError("comment text is required")
        if target not in ("worker", "pm"):
            raise ValueError("target must be 'worker' or 'pm'")
        sprint = self._load_sprint(sprint_id)
        if thread_id:
            t = next((x for x in sprint.threads if x["id"] == thread_id), None)
            if t is None:
                raise NotFoundError(thread_id)
            th.append(t, "human", text, by, now=time.time())
        else:
            t = th.new_thread(target, text, by, now=time.time())
            sprint.threads.append(t)
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: feedback ({target})")
        return th.public(t)

    def complete_sprint_thread(self, sprint_id, thread_id):
        return self._mutate_sprint_thread(sprint_id, thread_id, lambda t: t.update(status="complete"))

    def seen_sprint_thread(self, sprint_id, thread_id):
        return self._mutate_sprint_thread(sprint_id, thread_id, lambda t: t.update(agent_unseen=False))

    def _mutate_sprint_thread(self, sprint_id, thread_id, fn):
        from coscience import threads as th
        sprint = self._load_sprint(sprint_id)
        t = next((x for x in sprint.threads if x["id"] == thread_id), None)
        if t is None:
            raise NotFoundError(thread_id)
        fn(t)
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id}: thread {thread_id}")
        return th.public(t)
```
In `get_sprint`, replace `"comments": list(sprint.comments),` with:
```python
            "threads": [__import__("coscience.threads", fromlist=["public"]).public(t) for t in sprint.threads],
```
(or add `from coscience import threads` at top and use `threads.public`). Remove other `sprint.comments` reads (e.g. `list_sprints` if any).

- [ ] **Step 3b: ExecutionContext (worker sees only worker-thread text).** Where the worker's `ExecutionContext.human_comments` is built (search `human_comments=` — in `worker.py`/`pm` sprint-run path), source it from worker-target threads' human messages instead of the old `comments`:
```python
        human_comments = [m["text"] for t in sprint.threads if t.get("target") == "worker"
                          for m in t["messages"] if m["role"] == "human"]
```

- [ ] **Step 3c: HTTP** — update `comment_sprint` to pass `thread_id=body.thread_id` (add `thread_id: str = ""` to `SprintCommentIn`) and add the two routes:
```python
    @api.post("/sprints/{sprint_id}/threads/{tid}/complete")
    def complete_sprint_thread(sprint_id: str, tid: str,
                               user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.complete_sprint_thread(sprint_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/sprints/{sprint_id}/threads/{tid}/seen")
    def seen_sprint_thread(sprint_id: str, tid: str,
                           user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.seen_sprint_thread(sprint_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")
```

- [ ] **Step 4: Run to verify it passes** (plus regression on sprint routes)

Run: `~/venvs/coscience-dev/bin/pytest tests/test_http_sprint_threads.py tests/test_http_api.py -p no:warnings -q`
Expected: PASS (fix any lingering `comments` references surfaced by `test_http_api.py`).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py src/coscience/http_api.py src/coscience/worker.py tests/test_http_sprint_threads.py
git commit -m "feat(threads): sprint feedback thread service + HTTP (start/append/complete/seen)"
```

---

### Task 6: Frontend — `<FeedbackThread>` + SprintDetail (planner threads)

**Files:**
- Create: `frontend/src/components/FeedbackThread.tsx`
- Modify: `frontend/src/api.ts` (types + endpoints)
- Modify: `frontend/src/views/SprintDetail.tsx` (render threads via the component)
- Verify: `npm run build`

**Interfaces:**
- Consumes: Task 5 API.
- Produces: `<FeedbackThread thread onReply onComplete onSeen currentUser />`; `api.ts` gains `FeedbackMessage`/`FeedbackThreadT` types, `sprint.threads`, `completeSprintThread`/`seenSprintThread`, and `addSprintComment(..., threadId?)`.

- [ ] **Step 1: api.ts** — add:
```typescript
export interface FeedbackMessage { role: "human" | "pm" | "worker"; text: string; by?: string; at: number }
export interface FeedbackThreadT { id: string; target: "pm" | "worker"; status: "open" | "complete"; agent_unseen: boolean; created_at: number; messages: FeedbackMessage[] }
```
Add `threads: FeedbackThreadT[]` to `Sprint` (remove `comments`). Add endpoints:
```typescript
  addSprintComment: (id, text, target, threadId) =>
    fetch(`/api/sprints/${id}/comments`, { method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ text, target, thread_id: threadId ?? "" }) }).then(j<FeedbackThreadT>),
  completeSprintThread: (id, tid) => fetch(`/api/sprints/${id}/threads/${tid}/complete`, {method:"POST"}).then(j<FeedbackThreadT>),
  seenSprintThread: (id, tid) => fetch(`/api/sprints/${id}/threads/${tid}/seen`, {method:"POST"}).then(j<FeedbackThreadT>),
```

- [ ] **Step 2: FeedbackThread.tsx** — collapsed row (first msg + author chip + badge when `agent_unseen`); click toggles open; on open call `onSeen`; unwrapped shows all messages (UserChip for human `by`, "PM"/"Agent" label for agents, `OTHER_SHADE` when not mine), an add-message box (`onReply(text)`), and a "Mark complete" button (`onComplete`). A worker thread whose sprint isn't executing shows a muted "the agent will respond when this runs" hint (pass `respondsNow: boolean`).
```tsx
import { useState } from "react";
import { Button, Group, Stack, Text, Textarea } from "@mantine/core";
import Md from "./Md";
import { RelTime } from "./ui";
import { UserChip, useIsMine, OTHER_SHADE } from "../auth";
import type { FeedbackThreadT } from "../api";

export function FeedbackThread({ thread, onReply, onComplete, onSeen, respondsNow = true }:
  { thread: FeedbackThreadT; onReply: (t: string) => void; onComplete: () => void; onSeen: () => void; respondsNow?: boolean }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const isMine = useIsMine();
  const first = thread.messages[0];
  const toggle = () => { const n = !open; setOpen(n); if (n && thread.agent_unseen) onSeen(); };
  return (
    <div style={{ border: "1px solid var(--hairline)", borderRadius: 8 }}>
      <div onClick={toggle} style={{ cursor: "pointer", padding: "8px 12px",
        opacity: thread.status === "complete" ? 0.6 : 1 }}>
        <Group justify="space-between" wrap="nowrap">
          <Text size="sm" lineClamp={open ? undefined : 1}>{first?.text}</Text>
          {thread.agent_unseen && <span className="pill" style={{ "--st": "var(--signal)" } as any}><span className="dot" />reply</span>}
        </Group>
      </div>
      {open && (
        <div style={{ padding: "0 12px 12px", borderTop: "1px solid var(--hairline)" }}>
          <Stack gap={7} mt={9}>
            {thread.messages.map((m, i) => (
              <div key={i} style={{ background: m.role === "human" && isMine(m.by) ? "var(--paper)" : OTHER_SHADE,
                borderRadius: 8, padding: "7px 11px" }}>
                <div className="md-tight"><Md>{m.text}</Md></div>
                <Group gap={8} mt={2} wrap="nowrap">
                  {m.role === "human" ? <UserChip username={m.by} /> : <Text size="xs" c="dimmed">{m.role === "pm" ? "PM" : "Agent"}</Text>}
                  <Text size="xs" c="dimmed"><RelTime at={m.at} /></Text>
                </Group>
              </div>
            ))}
          </Stack>
          {!respondsNow && <Text size="xs" c="dimmed" mt={7}>The agent will respond when this sprint runs.</Text>}
          {thread.status === "open" && (
            <Group gap={8} mt={9} align="flex-end">
              <Textarea style={{ flex: 1 }} autosize minRows={1} placeholder="Reply…"
                value={draft} onChange={(e) => setDraft(e.currentTarget.value)} />
              <Button size="xs" disabled={!draft.trim()} onClick={() => { onReply(draft.trim()); setDraft(""); }}>Send</Button>
              <Button size="xs" variant="default" onClick={onComplete}>Mark complete</Button>
            </Group>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: SprintDetail** — replace the old comments list with two thread groups (planner + worker), each mapping `s.threads.filter(t => t.target === "pm"|"worker")` to `<FeedbackThread>`. Wire callbacks to the new api functions + `refresh()`. `respondsNow` for worker threads = `s.status === "executing"`. Keep the existing "add feedback" box, routing to `addSprintComment(id, text, target)` (new thread).

- [ ] **Step 4: Build**

Run (host): `cd frontend && npm run build`
Expected: `✓ built`, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FeedbackThread.tsx frontend/src/api.ts frontend/src/views/SprintDetail.tsx
git commit -m "feat(ui): threaded feedback component + sprint planner threads"
```

---

### Task 7: Worker answers its threads (`feedback.out` harvest)

**Files:**
- Modify: `src/coscience/claude_executor.py` (`build_instructions`: tell the worker how to reply)
- Create: `src/coscience/feedback_harvest.py` (offset-tracked harvest) + wire into the worker/dispatch beat
- Modify: `src/coscience/worker.py` (call harvest each beat while executing)
- Test: `tests/test_feedback_harvest.py`

**Interfaces:**
- Produces: `harvest_feedback(substrate, sprint_id) -> int` — reads new lines from `<sprint_dir>/feedback.out` past a stored byte offset (`<sprint_dir>/feedback.offset`), appends each `{thread_id, text}` as a `worker` message to the matching open worker thread, saves, returns count.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_feedback_harvest.py
import json
from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus
from coscience import threads, feedback_harvest


def test_harvest_appends_worker_reply(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"], program="p1")
    th = threads.new_thread("worker", "use fewer epochs", "u", now=1.0)
    s.threads.append(th); sub.save_sprint(s)
    d = sub.sprint_dir("s1")
    (d / "feedback.out").write_text(json.dumps({"thread_id": th["id"], "text": "done, cut to 3"}) + "\n")
    n = feedback_harvest.harvest_feedback(sub, "s1")
    assert n == 1
    got = sub.load_sprint("s1").threads[0]
    assert got["messages"][-1]["role"] == "worker" and got["agent_unseen"] is True
    # idempotent: no new lines -> 0
    assert feedback_harvest.harvest_feedback(sub, "s1") == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_feedback_harvest.py -v`
Expected: FAIL (`No module named 'coscience.feedback_harvest'`).

- [ ] **Step 3a: Implement harvest**

```python
# src/coscience/feedback_harvest.py
"""Harvest worker replies to feedback threads. The worker appends JSONL lines
{thread_id, text} to <sprint_dir>/feedback.out; we consume new bytes past a stored
offset and append them as 'worker' messages. Best-effort; never raises into a beat."""
from __future__ import annotations

import json
import time

from coscience import threads


def harvest_feedback(substrate, sprint_id: str) -> int:
    d = substrate.sprint_dir(sprint_id)
    out = d / "feedback.out"
    if not out.is_file():
        return 0
    off_path = d / "feedback.offset"
    try:
        offset = int(off_path.read_text().strip()) if off_path.is_file() else 0
    except (OSError, ValueError):
        offset = 0
    data = out.read_bytes()
    if offset >= len(data):
        return 0
    chunk = data[offset:].decode("utf-8", "replace")
    sprint = substrate.load_sprint(sprint_id)
    by_id = {t["id"]: t for t in sprint.threads}
    n = 0
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        t = by_id.get(str(ev.get("thread_id", "")))
        if t is not None and t.get("target") == "worker" and t.get("status") == "open":
            threads.append(t, "worker", str(ev.get("text", "")), "", now=time.time())
            n += 1
    if n:
        substrate.save_sprint(sprint)
    try:
        off_path.write_text(str(len(data)))
    except OSError:
        pass
    return n
```

- [ ] **Step 3b: Worker instructions** — in `build_instructions`, when there are worker threads, add a section telling the agent it may reply: "To answer a reviewer's feedback thread, append one JSON line `{\"thread_id\": \"<id>\", \"text\": \"<short reply>\"}` to `<sprint_dir>/feedback.out`." Pass the open worker threads' `{id, last human text}` into the ExecutionContext so the instructions can list them (extend `ExecutionContext` with `feedback_threads: list[dict]` and render them under the existing "Human feedback" section, each with its `thread_id`).

- [ ] **Step 3c: Beat wiring** — in `worker.run_one_beat` (and/or the dispatcher's per-sprint beat that polls a running agent), after checking the agent is running, call `feedback_harvest.harvest_feedback(self.substrate, sprint_id)` for the executing sprint. (Read `worker.py` to place it beside the existing progress/collect handling.)

- [ ] **Step 4: Run to verify it passes**

Run: `~/venvs/coscience-dev/bin/pytest tests/test_feedback_harvest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/coscience/feedback_harvest.py src/coscience/claude_executor.py src/coscience/executor.py src/coscience/worker.py tests/test_feedback_harvest.py
git commit -m "feat(threads): worker answers feedback threads via harvested feedback.out"
```

---

### Task 8: Idea threads (reuse model + component)

**Files:**
- Modify: `models.py` (`Idea.comments` → `Idea.threads`; `protected` uses `threads`), `substrate.py` (load_ideas/save_ideas back-compat), `service.py` (`_idea_public` returns `threads`; `add_idea_comment` thread start/append; complete/seen), `http_api.py` (routes), `pm_agent.py` (`gather_context` idea threads → context; `apply_cycle` idea replies), `pm_claude.py` (render + reply schema already covers thread_replies — extend to idea threads)
- Modify: `frontend/src/views/IdeasView.tsx` (+ `api.ts` idea types/endpoints)
- Test: `tests/test_idea_threads.py`

**Interfaces:**
- Idea threads are always `target: "pm"`. PM `thread_replies` already carries `{thread_id, text}`; extend `gather_context` to also surface open idea threads (as `idea_feedback` items with `thread_id` + messages) and `apply_cycle` to append PM replies to idea threads. `_idea_public` returns `threads: [threads.public(t)]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_idea_threads.py
from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus


def test_idea_comment_starts_thread_and_completes(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    idea = svc.add_idea("p1", "an idea", source="human")
    c = TestClient(build_app(svc))
    r = c.post(f"/api/programs/p1/ideas/{idea['id']}/comments", json={"text": "refine this"})
    assert r.status_code == 201
    pub = c.get("/api/programs/p1/ideas").json()["ideas"][0]
    assert pub["threads"][0]["messages"][0]["text"] == "refine this"
    tid = pub["threads"][0]["id"]
    assert c.post(f"/api/programs/p1/ideas/{idea['id']}/threads/{tid}/complete").status_code == 200
```

- [ ] **Step 2–5:** Mirror Tasks 3–6 for ideas (model field rename + `adapt_legacy(default_target="pm")` in `load_ideas`; `_idea_public.threads`; `add_idea_comment(program_id, idea_id, text, by="", thread_id="")`; complete/seen service+routes; `gather_context`/`apply_cycle` idea-thread surfacing+reply keyed by thread_id across ideas; `IdeasView` renders `idea.threads` via `<FeedbackThread>` with `target="pm"`, `respondsNow` always true). Run `~/venvs/coscience-dev/bin/pytest tests/test_idea_threads.py tests/test_http_ideas.py -q` and `npm run build`; then commit `feat(threads): idea comment threads`.

> The PM apply must resolve a `thread_id` that could belong to a sprint OR an idea. Make `apply_cycle` try sprint threads first, then idea threads (a single reply map, applied to whichever surface owns the id).

---

### Task 9: Guidance threads (reuse) + full regression

**Files:**
- Modify: `substrate.py` (`load_guidance`/`save_guidance`: `notes` → `threads` with back-compat), `service.py` (`add_guidance` thread start/append; `list_guidance` returns threads; complete/seen), `http_api.py` (routes), `pm_agent.py` (`gather_context` guidance threads + `apply_cycle` replies), `pm_claude.py` (render), `frontend` (`ProgramDetail` guidance section + `api.ts`)
- Test: `tests/test_guidance_threads.py`

**Interfaces:** guidance threads are `target: "pm"`; same reply path. `gather_context.human_guidance` becomes the open guidance threads (with ids + messages); the fingerprint keys on last-human-text so a new guidance message re-triggers the PM.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_guidance_threads.py
from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus


def test_guidance_thread_roundtrip(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    c = TestClient(build_app(svc))
    r = c.post("/api/programs/p1/guidance", json={"text": "prefer cheap models"})
    assert r.status_code == 201
    gs = c.get("/api/programs/p1/guidance").json()
    assert gs[0]["messages"][0]["text"] == "prefer cheap models"
```

- [ ] **Step 2–4:** Mirror the pattern for guidance (each note → a `pm` thread; `add_guidance` starts/appends; complete/seen; `gather_context` uses open guidance threads; `apply_cycle` reply map already handles them via the shared id lookup; `ProgramDetail` guidance section renders `<FeedbackThread>`). Update `remove_guidance` semantics if kept, or replace with complete.

- [ ] **Step 5: Full regression + build, then commit**

Run (host): `~/venvs/coscience-dev/bin/pytest --ignore=tests/test_mcp_entry.py --ignore=tests/test_mcp_server.py --ignore=tests/test_transport_programs.py -p no:warnings -q` and `cd frontend && npm run build`.
Expected: all green; build clean. Fix any remaining legacy `.comments`/`notes` readers.
```bash
git add -A && git commit -m "feat(threads): guidance threads + full-surface regression"
```

---

## Notes for the implementer

- Run tests on the Linux host (`~/venvs/coscience-dev/bin/pytest`); the runtime is Linux-only.
- The single most error-prone thread is the field renames (`Sprint.comments`, `Idea.comments`, guidance `notes`): after each rename, grep the repo for the old name and fix every reader — `pm_agent`, `service`, `http_api`, `pm_claude` rendering, and the frontend. The plan's regression steps exist to catch stragglers.
- `apply_cycle` uses ONE reply map for all PM-answered surfaces (sprint + idea + guidance): look the `thread_id` up across surfaces and append where found.
- Keep the reply cadence rule central (`threads.needs_reply`) — it's what prevents the PM/worker from re-answering and burning tokens.
- Back-compat is load-time only; never rewrite old files eagerly — they upgrade to `threads` on next save.
