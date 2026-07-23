# Artifacts Phase 4a — Chat-Mode Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a chat thread be *bound to an artifact*: starting it locks the
artifact (chat holder) and seeds `work/`; the chat's turns run inside `work/` so
the agent edits the artifact live; a "Save as version" snapshots it; and an idle
chat's lock is reaped (final version cut) after 30 min.

**Architecture:** `ChatThread` gains an `artifacts` list. A bound chat is created
full-scope with the artifact lock acquired (holder `chat:<thread_id>`). Turns run
with `cwd` = the bound artifact's `work/` dir and bump the lock's `last_activity`.
New service methods cut a version on demand (save) and release on delete; the
dispatcher's cycle calls the Phase-1 `reap_stale_chat_locks`. `release_lock` gains
a holder-ownership guard (the deferred P4 item) so only the true holder releases.

**Tech Stack:** the existing `ChatThread`/`chat_agent`/`Service` chat methods;
`coscience.artifacts` (acquire/release/cut/bump/reap from Phases 1); `Dispatcher`;
`pytest` with `substrate` + `FakeAgent`.

## Global Constraints

- **Runtime is Linux-only** (`fcntl`); tests run on the Linux dev host.
- **A bound chat holds the artifact as the "chat" lock holder** (`holder_kind:"chat"`,
  `holder_id:"chat:<thread_id>"`). It is **exclusive** — starting a bound chat on a
  busy artifact is rejected; a sprint won't execute while a chat holds its artifact
  (Phase-2 gate already consults the lock).
- **A bound chat is full-scope** (it edits files). Its turns run with **cwd = the
  bound artifact's `work/` dir**, so agent edits land on the working copy.
- **`ChatThread.artifacts` is a list** (sized 1 for now — do NOT hard-code single;
  when >1, cwd is the first bound artifact's `work/`).
- **`release_lock` only releases an aid whose lock `holder_id` matches the caller's
  `created_by`** (holder-ownership guard) — a sprint can't release a chat's lock and
  vice versa.
- **The reaper releases idle (>30 min `last_activity`) chat locks**, cutting a final
  version (dedup applies); it runs inside the dispatcher cycle across all programs.
- No hard delete; all substrate writes go through Python.

**Base commit for this phase:** current `feat/artifacts` HEAD (Phase 3 complete).

---

### Task 1: `ChatThread.artifacts` field + round-trip

**Files:**
- Modify: `src/coscience/models.py` (`ChatThread` dataclass, add field after `messages`)
- Modify: `src/coscience/substrate.py` (`load_chat_thread` ~line 350-364, `save_chat_thread` ~line 366-374)
- Test: `tests/test_chat_artifacts_field.py`

**Interfaces:**
- Produces: `ChatThread.artifacts: list[str]` (default empty); round-trips through `thread.md` frontmatter (omitted-or-empty tolerated on load).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_artifacts_field.py
from coscience.models import ChatThread


def test_chatthread_artifacts_default_empty():
    t = ChatThread(id="c1")
    assert t.artifacts == []


def test_chat_thread_artifacts_roundtrip(substrate):
    t = ChatThread(id="c1", title="edit fig", scope="full", artifacts=["umap"])
    substrate.save_chat_thread("p", t)
    b = substrate.load_chat_thread("p", "c1")
    assert b.artifacts == ["umap"]
    assert b.scope == "full"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_chat_artifacts_field.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'artifacts'`.

- [ ] **Step 3: Implement**

In `src/coscience/models.py`, add to the `ChatThread` dataclass (after the
`messages` field):

```python
    artifacts: list[str] = field(default_factory=list)  # bound artifact ids (chat edits their work/); sized 1 for now
```

In `src/coscience/substrate.py` `load_chat_thread`, add to the `ChatThread(...)`
constructor (after the `messages=[...]` argument):

```python
            artifacts=[str(a) for a in fm.get("artifacts", [])],
```

In `src/coscience/substrate.py` `save_chat_thread`, add to the `fm` dict (after
`"agent_token": thread.agent_token,`):

```python
              "artifacts": list(thread.artifacts),
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_chat_artifacts_field.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_chat_artifacts_field.py
git commit -m "feat(artifacts): ChatThread.artifacts field"
```

---

### Task 2: `release_lock` holder-ownership guard (deferred P4)

**Files:**
- Modify: `src/coscience/artifacts.py` (`release_lock`)
- Test: `tests/test_artifact_lock.py` (append)

**Interfaces:**
- Changes `release_lock` so it only releases an aid whose current lock `holder_id`
  equals the passed `created_by`; a mismatched (or absent) holder appends `None`
  and leaves the lock untouched.

- [ ] **Step 1: Write the failing test** — append to `tests/test_artifact_lock.py`:

```python
def test_release_only_by_holder(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("edit")
    # a DIFFERENT holder must not release it
    out = artifacts.release_lock(substrate, "p", ["doc"], now=2.0, created_by="s1")
    assert out == [None]
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == "chat:x"   # still held
    # the true holder can
    out = artifacts.release_lock(substrate, "p", ["doc"], now=3.0, created_by="chat:x")
    assert out == ["v1"]
    assert substrate.load_artifact("p", "doc").lock == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_artifact_lock.py::test_release_only_by_holder -v`
Expected: FAIL (the different holder releases it and cuts v1).

- [ ] **Step 3: Implement** — in `src/coscience/artifacts.py` `release_lock`, add a
holder check right after loading the artifact and confirming it has a lock. The
loop body becomes:

```python
        for aid in aids:
            if not (substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
                out.append(None)
                continue
            art = substrate.load_artifact(program_id, aid)
            if not art.lock or art.lock.get("holder_id") != created_by:
                out.append(None)          # only the true holder may release
                continue
            vid = cut_version(substrate, program_id, aid, created_by, now)
            work = substrate.artifact_dir(program_id, aid) / "work"
            if work.is_dir():
                shutil.rmtree(work)
            art = substrate.load_artifact(program_id, aid)   # reload (cut_version saved)
            art.lock = {}
            substrate.save_artifact(art)
            out.append(vid)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_artifact_lock.py -v`
Expected: PASS (all lock tests green — the existing release tests pass `created_by`
matching the holder: `release_for_sprint` uses `sprint.id` which is the holder).

- [ ] **Step 5: Run the sprint-release regression + commit**

Run: `python -m pytest tests/test_artifact_lock.py tests/test_worker_artifacts.py tests/test_artifact_sprint_helpers.py -v`
Expected: PASS (sprint release still works — its `created_by=sprint.id` matches the holder).

```bash
git add src/coscience/artifacts.py tests/test_artifact_lock.py
git commit -m "fix(artifacts): release_lock only releases for the true holder"
```

---

### Task 3: Bound chat creation (acquire lock + seed work/ + full scope)

**Files:**
- Modify: `src/coscience/service.py` (`create_chat`)
- Test: `tests/test_chat_bound_create.py`

**Interfaces:**
- Consumes: `artifacts.acquire_lock/sprint helpers`, `Substrate.artifact_dir`.
- Produces: `create_chat(program_id, title="", artifacts=None) -> dict` — when
  `artifacts` is a non-empty list, the new chat is created **full-scope**, its
  `artifacts` set, and the artifact lock is acquired under holder `chat:<thread_id>`
  (seeding `work/`). Raises `ValueError("artifact busy")` if any bound artifact is
  already locked by someone else (acquire returns False) — and the chat is NOT
  created in that case. Unbound `create_chat` is unchanged (read scope, no lock).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_bound_create.py
from coscience import artifacts
from coscience.models import Program
from coscience.service import Service


def _program(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))


def test_unbound_chat_unchanged(substrate):
    _program(substrate)
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p")
    assert c["scope"] == "read"
    t = substrate.load_chat_thread("p", c["id"])
    assert t.artifacts == []


def test_bound_chat_locks_and_seeds(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p", title="edit", artifacts=["doc"])
    assert c["scope"] == "full"
    a = substrate.load_artifact("p", "doc")
    assert a.lock["holder_id"] == f"chat:{c['id']}"
    assert (substrate.artifact_dir("p", "doc") / "work").is_dir()


def test_bound_chat_rejected_when_busy(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", now=0.0)  # busy
    svc = Service(substrate.repo_root)
    try:
        svc.create_chat("p", artifacts=["doc"])
        assert False, "expected ValueError"
    except ValueError:
        pass
    # no orphan chat thread created
    assert svc.list_chats("p") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_chat_bound_create.py -v`
Expected: FAIL with `TypeError: create_chat() got an unexpected keyword argument 'artifacts'`.

- [ ] **Step 3: Implement** — replace `create_chat` in `src/coscience/service.py`:

```python
    def create_chat(self, program_id: str, title: str = "",
                    artifacts: list | None = None) -> dict:
        from coscience import artifacts as _art
        self._require_program(program_id)
        aids = [str(a) for a in (artifacts or [])]
        tid = uuid4().hex[:8]
        t = ChatThread(id=tid, title=(str(title).strip() or "New chat"),
                       scope="full" if aids else "read",
                       session_id=str(uuid4()), created_at=time.time(),
                       artifacts=aids)
        if aids:
            ok = _art.acquire_lock(self.substrate, program_id, aids, "chat",
                                   f"chat:{tid}", time.time())
            if not ok:
                raise ValueError("artifact busy — held by another editor")
        self.substrate.save_chat_thread(program_id, t)
        self.substrate.commit(f"program {program_id}: new chat {t.id}"
                              + (f" bound to {aids}" if aids else ""))
        return self._chat_public(t)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_chat_bound_create.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_chat_bound_create.py
git commit -m "feat(artifacts): bound chat creation acquires the artifact lock"
```

---

### Task 4: Bound-chat turn runs in work/ + bumps activity

**Files:**
- Modify: `src/coscience/service.py` (`post_chat_message`)
- Test: `tests/test_chat_bound_turn.py`

**Interfaces:**
- Consumes: `artifacts.bump_activity`, `Substrate.artifact_dir`.
- Produces: when a bound chat (`thread.artifacts` non-empty) posts a message, the
  turn's `workdir` is the first bound artifact's `work/` dir (not the program dir),
  the launched prompt includes an artifact-editing note, and each posted message
  bumps every bound artifact's lock `last_activity`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_bound_turn.py
from coscience import artifacts
from coscience.models import Program
from coscience.service import Service


def _bound_chat(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p", artifacts=["doc"])
    return svc, c["id"]


def test_bound_turn_runs_in_work_dir(substrate):
    svc, cid = _bound_chat(substrate)
    calls = {}
    def fake_launch(**kw):
        calls.update(kw)
        return "tok"
    svc.post_chat_message("p", cid, "make the title bold", launch=fake_launch)
    expected = str(substrate.artifact_dir("p", "doc") / "work")
    assert calls["workdir"] == expected
    assert "doc" in calls["prompt"] or "artifact" in calls["prompt"].lower()


def test_bound_message_bumps_activity(substrate):
    svc, cid = _bound_chat(substrate)
    # advance a lot of time, then post -> last_activity moves forward
    before = substrate.load_artifact("p", "doc").lock["last_activity"]
    svc.post_chat_message("p", cid, "hi", launch=lambda **k: "tok")
    after = substrate.load_artifact("p", "doc").lock["last_activity"]
    assert after >= before
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_chat_bound_turn.py -v`
Expected: FAIL — `workdir` is the program dir, not the artifact `work/` dir.

- [ ] **Step 3: Implement** — in `src/coscience/service.py` `post_chat_message`,
after `workdir = chat_agent.resolve_workdir(self.substrate, program.workdir)` (the
existing line), add an override + activity bump for bound chats, and fold an
artifact note into the prompt. Insert immediately after that `workdir = ...` line:

```python
        if thread.artifacts:
            from coscience import artifacts as _art
            aid0 = thread.artifacts[0]
            workdir = str(self.substrate.artifact_dir(program_id, aid0) / "work")
            for aid in thread.artifacts:
                _art.bump_activity(self.substrate, program_id, aid, time.time())
```

and, where the first-turn/`resume` prompt is assembled, append an artifact note so
the agent knows what it is editing. The simplest correct spot: after the `prompt`
variable is set (both branches), add:

```python
        if thread.artifacts:
            prompt = (f"[ARTIFACT] You are editing artifact(s) {thread.artifacts} — your working "
                      f"directory IS the artifact's working copy. Create and edit files here; "
                      f"the human snapshots them as versions.\n\n") + prompt
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_chat_bound_turn.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_chat_bound_turn.py
git commit -m "feat(artifacts): bound chat turn runs in work/ and bumps lock activity"
```

---

### Task 5: Save-as-version + release on delete

**Files:**
- Modify: `src/coscience/service.py` (add `save_chat_version`; extend `delete_chat`)
- Test: `tests/test_chat_save_release.py`

**Interfaces:**
- Consumes: `artifacts.cut_version`, `artifacts.release_lock`.
- Produces:
  - `save_chat_version(program_id, thread_id) -> dict` — for each bound artifact,
    cut a version from `work/` (dedup applies, holder must be this chat); returns
    `{artifact_id: version_id|None}`. `ValueError` if the chat is not bound.
  - `delete_chat(program_id, thread_id)` — before deleting, **release** every bound
    artifact under holder `chat:<thread_id>` (cuts a final version, clears the lock),
    then delete the thread as before.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_save_release.py
from coscience import artifacts
from coscience.models import Program
from coscience.service import Service


def _bound_chat(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p", artifacts=["doc"])
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("draft")
    return svc, c["id"]


def test_save_cuts_a_version_and_keeps_lock(substrate):
    svc, cid = _bound_chat(substrate)
    out = svc.save_chat_version("p", cid)
    assert out == {"doc": "v1"}
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"
    assert a.lock["holder_id"] == f"chat:{cid}"     # still editing


def test_save_dedup_returns_none(substrate):
    svc, cid = _bound_chat(substrate)
    svc.save_chat_version("p", cid)                  # v1
    out = svc.save_chat_version("p", cid)            # no change -> None
    assert out == {"doc": None}


def test_delete_releases_and_cuts_final(substrate):
    svc, cid = _bound_chat(substrate)
    svc.delete_chat("p", cid)
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"          # final snapshot on release
    assert a.lock == {}              # unlocked
    assert svc.list_chats("p") == []


def test_save_unbound_raises(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    svc = Service(substrate.repo_root)
    c = svc.create_chat("p")
    try:
        svc.save_chat_version("p", c["id"])
        assert False
    except ValueError:
        pass
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_chat_save_release.py -v`
Expected: FAIL — `save_chat_version` missing.

- [ ] **Step 3: Implement** — in `src/coscience/service.py`:

Add `save_chat_version`:

```python
    def save_chat_version(self, program_id: str, thread_id: str) -> dict:
        from coscience import artifacts as _art
        thread = self._thread_or_404(program_id, thread_id)
        if not thread.artifacts:
            raise ValueError("this chat is not bound to an artifact")
        out: dict = {}
        for aid in thread.artifacts:
            vid = _art.cut_version(self.substrate, program_id, aid,
                                   f"chat:{thread_id}", time.time())
            out[aid] = vid
        self.substrate.commit(f"program {program_id}: chat {thread_id} saved versions {out}")
        return out
```

Extend `delete_chat` to release first (replace the existing method body):

```python
    def delete_chat(self, program_id: str, thread_id: str) -> None:
        from coscience import artifacts as _art
        thread = self._thread_or_404(program_id, thread_id)
        if thread.artifacts:
            _art.release_lock(self.substrate, program_id, list(thread.artifacts),
                              time.time(), created_by=f"chat:{thread_id}")
        self.substrate.delete_chat_thread(program_id, thread_id)
        self.substrate.commit(f"program {program_id}: delete chat {thread_id}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_chat_save_release.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_chat_save_release.py
git commit -m "feat(artifacts): chat save-as-version + release on delete"
```

---

### Task 6: Reaper wired into the dispatcher cycle + HTTP routes

**Files:**
- Modify: `src/coscience/dispatcher.py` (`run_one_cycle` — reap idle chat locks per program)
- Modify: `src/coscience/service.py` (`get_chat_thread` include `artifacts`; ensure `_chat_public` carries the field)
- Modify: `src/coscience/http_api.py` (create-chat body gains `artifacts`; add `save_chat_version` route; add a `work/` file-read route)
- Modify: `src/coscience/service.py` (`read_artifact_work_file` for the live split-view)
- Test: `tests/test_chat_reaper_routes.py`

**Interfaces:**
- Consumes: `artifacts.reap_stale_chat_locks` (Phase 1).
- Produces:
  - The dispatcher, once per cycle, reaps stale chat locks for every program
    (`reap_stale_chat_locks(substrate, program.id, now)`), releasing (and version-
    cutting) chat locks idle ≥ 1800 s.
  - `_chat_public`/`get_chat_thread` expose `artifacts`.
  - `read_artifact_work_file(program_id, aid, name) -> dict` — path-guarded read of
    a file in the artifact's live `work/` dir (same guard shape as version files),
    for the split-view live render; `NotFoundError` if no `work/` or traversal.
  - HTTP: `POST /programs/{pid}/chats` body accepts `artifacts: list[str]`;
    `POST /programs/{pid}/chats/{tid}/save` → `save_chat_version`;
    `GET /programs/{pid}/artifacts/{aid}/work/{name}` → `read_artifact_work_file`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_reaper_routes.py
from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.dispatcher import Dispatcher
from coscience.http_api import build_app
from coscience.models import Program
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.service import Service
from tests.conftest import FakeAgent


def _svc(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    return Service(substrate.repo_root)


def test_reaper_releases_idle_chat_lock_in_cycle(substrate):
    svc = _svc(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc.create_chat("p", artifacts=["doc"])          # locks doc at t=now
    # force the lock old
    a = substrate.load_artifact("p", "doc")
    a.lock["last_activity"] = 0.0
    substrate.save_artifact(a)
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool({"cpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=1801.0)
    assert substrate.load_artifact("p", "doc").lock == {}   # reaped


def test_create_chat_route_accepts_artifacts(substrate):
    _svc(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    c = TestClient(build_app(Service(substrate.repo_root)))
    r = c.post("/api/programs/p/chats", json={"title": "edit", "artifacts": ["doc"]})
    assert r.status_code == 201
    assert r.json()["artifacts"] == ["doc"]


def test_save_route_and_work_read(substrate):
    svc = _svc(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    cid = svc.create_chat("p", artifacts=["doc"])["id"]
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("live edit")
    c = TestClient(build_app(Service(substrate.repo_root)))
    assert c.get("/api/programs/p/artifacts/doc/work/c.md").json()["content"] == "live edit"
    r = c.post(f"/api/programs/p/chats/{cid}/save")
    assert r.json() == {"doc": "v1"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_chat_reaper_routes.py -v`
Expected: FAIL (reaper not wired / routes missing).

- [ ] **Step 3: Implement**

In `src/coscience/dispatcher.py` `run_one_cycle`, near the end (before the final
`self._save_queue(queue)` / commit), add:

```python
        # Release chat locks left idle past the inactivity window (cuts a final
        # version), so a walked-away editing session frees the artifact.
        from coscience import artifacts as _artifacts
        for program in self.substrate.iter_programs():
            _artifacts.reap_stale_chat_locks(self.substrate, program.id, now)
```

In `src/coscience/service.py`, make `_chat_public` (line ~561) include the
artifacts — its returned dict's last key becomes:

```python
                "busy": thread.pending, "messages": list(thread.messages), "live": live,
                "artifacts": list(thread.artifacts)}
```

Add `read_artifact_work_file` to the artifacts section of `service.py`:

```python
    def read_artifact_work_file(self, program_id: str, aid: str, name: str) -> dict:
        work = (self.substrate.artifact_dir(program_id, aid) / "work").resolve()
        root = (self.substrate.repo_root / "programs").resolve()
        if not work.is_relative_to(root) or not work.is_dir():
            raise NotFoundError(name)
        try:
            path = (work / name).resolve()
        except (ValueError, OSError):
            raise NotFoundError(name)
        if not path.is_file() or not path.is_relative_to(work):
            raise NotFoundError(name)
        raw = path.read_bytes()
        binary = b"\x00" in raw[:8192]
        return {"name": name, "size": len(raw),
                "content": "" if binary else raw.decode("utf-8", errors="replace"),
                "binary": binary}
```

In `src/coscience/http_api.py`: extend `ChatCreateIn` (line ~108) with an
artifacts field, and update the `POST /programs/{pid}/chats` handler to forward it
AND map the "artifact busy" `ValueError` to 422:

```python
class ChatCreateIn(BaseModel):
    title: str = ""
    artifacts: list[str] | None = None
```

```python
    @api.post("/programs/{program_id}/chats", status_code=201)
    def create_chat(program_id: str, body: ChatCreateIn) -> dict:
        try:
            return service.create_chat(program_id, body.title, artifacts=body.artifacts)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
```

Then add two routes near the other chat/artifact routes:

```python
    @api.post("/programs/{program_id}/chats/{tid}/save")
    def save_chat_version(program_id: str, tid: str,
                          user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.save_chat_version(program_id, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.get("/programs/{program_id}/artifacts/{aid}/work/{name}")
    def read_artifact_work(program_id: str, aid: str, name: str) -> dict:
        try:
            return service.read_artifact_work_file(program_id, aid, name)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="work file not found")
```

(If the create-chat route currently returns 201 with the chat dict, keep that; just
thread `artifacts` through. Confirm the exact model name by reading `http_api.py`.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_chat_reaper_routes.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the chat + artifact suites + commit**

Run: `python -m pytest tests/test_chat_*.py tests/test_artifact_*.py -v`
Expected: PASS (all green — existing chat tests unaffected by the additive changes).

```bash
git add src/coscience/dispatcher.py src/coscience/service.py src/coscience/http_api.py tests/test_chat_reaper_routes.py
git commit -m "feat(artifacts): chat reaper in dispatch cycle + save/work-read routes"
```

---

## Phase 4a Done — What Exists Now

A chat can be bound to an artifact: creating it locks the artifact and seeds
`work/`; its full-scope turns run inside `work/` and bump the lock's activity;
"Save as version" snapshots on demand; deleting the chat releases (final version);
and the dispatcher reaps an idle chat's lock after 30 min. `release_lock` now only
releases for the true holder. A `work/`-file read endpoint backs the live view.

**Next: Phase 4b (frontend)** — an "Open chat" action on ArtifactDetail (creates a
bound full-scope chat), a split-view in ChatView (conversation beside the live
`work/` render), and a "Save as version" button. Then **Phase 5** (PM triggers).
