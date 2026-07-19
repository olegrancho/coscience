# Artifacts Phase 3a — Backend Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose the artifact store over the Service + HTTP API so the dashboard
(Phase 3b) can list, view, revert, archive, comment on, and download artifacts,
render interactive pages in a sandboxed iframe, and see sprint↔artifact links.

**Architecture:** New `# --- artifacts ---` section in `service.py` (read +
write methods over `coscience.artifacts`/`substrate`), and matching routes in
`http_api.py`. Artifact comment threads reuse `coscience.threads` (always
`target:"pm"`, like idea threads). Downloads use FastAPI `FileResponse` (single
file) or a zip `Response` (multi-file); interactive pages are served per-file
with a restrictive CSP header for the sandboxed iframe.

**Tech Stack:** FastAPI (`APIRouter`, `FileResponse`, `Response`, `Depends`),
`pathlib` (`is_relative_to` path-guarding), `zipfile`/`io`, `pytest` +
`fastapi.testclient` (mirror `tests/test_http_api.py`), the `substrate` fixture.

## Global Constraints

- **Runtime is Linux-only**; tests run on the Linux dev host, not Windows.
- **Artifact comment threads always `target:"pm"`** (reuse `coscience.threads`;
  mirror idea threads). Thread ops (complete/reopen/seen/delete) mirror the
  sprint-thread service+routes exactly.
- **Path-guard every file read/serve** to the specific version directory — no
  traversal, using `Path.resolve()` + `is_relative_to`. Mirror the guard style
  of `read_sprint_file`.
- **No hard delete** — discard is archive; endpoints only ever set `archived`.
- **Write endpoints commit** via `substrate.commit(...)` and return the updated
  `get_artifact(...)` dict (mirror how sprint write routes return `get_sprint`).
- **`get_artifact` returns the version TREE** (each `{id,parent,created_at,created_by,archived,note}`),
  the public threads, the current-version file list, and the linked sprints.
- Write routes take `user: auth.User | None = Depends(current_user)` and pass
  `by=(user.username if user else "")`, exactly like the sprint routes.

**Base commit for this phase:** `865e9a9` (Phase 2 complete, `feat/artifacts`).

---

### Task 1: Service — list_artifacts / get_artifact / cross-links

**Files:**
- Modify: `src/coscience/service.py` (new `# --- artifacts ---` section, place after the `# --- results ---` block ~line 1019)
- Test: `tests/test_service_artifacts_read.py`

**Interfaces:**
- Consumes: `Substrate.iter_artifacts/load_artifact/artifact_dir`, `artifacts.sprint_aids`, `threads.public`.
- Produces (methods on `Service`):
  - `list_artifacts(program_id: str) -> list[dict]` — non-archived artifacts: `{id,title,kind,current,archived,lock,version_count,linked_sprints}`.
  - `get_artifact(program_id: str, aid: str) -> dict` — `{id,program,title,kind,current,archived,lock,versions:[...tree...],threads:[public],current_files:[names],linked_sprints}`; raises `NotFoundError(aid)` if absent.
  - `_artifact_sprints(program_id, aid) -> list[dict]` — sprints whose `sprint_aids` include `aid`: `[{id,status,title}]`.
  - `_artifact_version_files(program_id, aid, vid) -> list[str]` — sorted relative file paths in a version dir ("" for a version with no dir / no files).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_artifacts_read.py
from coscience import artifacts
from coscience.models import Sprint, SprintStatus
from coscience.service import NotFoundError, Service


def _seed_artifact(substrate, aid="doc", kind="md", text="hello"):
    artifacts.create_artifact(substrate, "p", aid, aid.title(), kind)
    work = artifacts.seed_work(substrate, "p", aid)
    (work / "content.md").write_text(text)
    artifacts.cut_version(substrate, "p", aid, "human", now=1.0)


def test_list_artifacts_hides_archived(substrate):
    _seed_artifact(substrate, "a")
    _seed_artifact(substrate, "b")
    artifacts.archive_artifact(substrate, "p", "b")
    svc = Service(substrate.repo_root)
    ids = [a["id"] for a in svc.list_artifacts("p")]
    assert ids == ["a"]
    a = next(x for x in svc.list_artifacts("p") if x["id"] == "a")
    assert a["kind"] == "md"
    assert a["current"] == "v1"
    assert a["version_count"] == 1


def test_get_artifact_returns_tree_and_files(substrate):
    _seed_artifact(substrate, "doc", text="one")
    work = artifacts.seed_work(substrate, "p", "doc")
    (work / "content.md").write_text("two")
    artifacts.cut_version(substrate, "p", "doc", "human", now=2.0)      # v2
    svc = Service(substrate.repo_root)
    d = svc.get_artifact("p", "doc")
    assert d["current"] == "v2"
    assert [v["id"] for v in d["versions"]] == ["v1", "v2"]
    assert d["versions"][1]["parent"] == "v1"
    assert d["current_files"] == ["content.md"]
    assert d["threads"] == []


def test_get_artifact_missing_raises(substrate):
    svc = Service(substrate.repo_root)
    try:
        svc.get_artifact("p", "nope")
        assert False, "expected NotFoundError"
    except NotFoundError:
        pass


def test_linked_sprints_cross_reference(substrate):
    _seed_artifact(substrate, "doc")
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.QUEUED, goals="g",
                                 plan=["x"], program="p", artifacts_bound=["doc"]))
    svc = Service(substrate.repo_root)
    d = svc.get_artifact("p", "doc")
    assert [s["id"] for s in d["linked_sprints"]] == ["s1"]
    assert d["linked_sprints"][0]["status"] == "queued"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_service_artifacts_read.py -v`
Expected: FAIL with `AttributeError: 'Service' object has no attribute 'list_artifacts'`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/service.py`, add after the `get_result` method (after line 1019),
before `# --- ledger ---`:

```python
    # --- artifacts ---
    def _artifact_sprints(self, program_id: str, aid: str) -> list[dict]:
        from coscience import artifacts
        out = []
        for s in self.substrate.iter_sprints():
            if s.program == program_id and aid in artifacts.sprint_aids(s):
                out.append({"id": s.id, "status": s.status.value, "title": s.title})
        return out

    def _artifact_version_files(self, program_id: str, aid: str, vid: str) -> list[str]:
        vdir = self.substrate.artifact_dir(program_id, aid) / vid
        if not vdir.is_dir():
            return []
        return sorted(str(p.relative_to(vdir)) for p in vdir.rglob("*") if p.is_file())

    def list_artifacts(self, program_id: str) -> list[dict]:
        out = []
        for a in self.substrate.iter_artifacts(program_id):
            out.append({
                "id": a.id, "title": a.title, "kind": a.kind, "current": a.current,
                "archived": a.archived, "lock": a.lock,
                "version_count": sum(1 for v in a.versions if not v.archived),
                "linked_sprints": self._artifact_sprints(program_id, a.id),
            })
        return out

    def get_artifact(self, program_id: str, aid: str) -> dict:
        from coscience import threads as _th
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        a = self.substrate.load_artifact(program_id, aid)
        return {
            "id": a.id, "program": program_id, "title": a.title, "kind": a.kind,
            "current": a.current, "archived": a.archived, "lock": a.lock,
            "versions": [
                {"id": v.id, "parent": v.parent, "created_at": v.created_at,
                 "created_by": v.created_by, "archived": v.archived, "note": v.note}
                for v in a.versions],
            "threads": [_th.public(t) for t in a.threads],
            "current_files": self._artifact_version_files(program_id, aid, a.current) if a.current else [],
            "linked_sprints": self._artifact_sprints(program_id, aid),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_service_artifacts_read.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_artifacts_read.py
git commit -m "feat(artifacts): service list_artifacts / get_artifact + cross-links"
```

---

### Task 2: Service — version file content + path-guarded serve

**Files:**
- Modify: `src/coscience/service.py` (append to the artifacts section)
- Test: `tests/test_service_artifacts_files.py`

**Interfaces:**
- Consumes: `Substrate.artifact_dir` (Task 1 context).
- Produces (methods on `Service`):
  - `artifact_version_dir(program_id, aid, vid) -> Path` — the resolved version dir; raises `NotFoundError` if the artifact/vid dir is absent.
  - `read_artifact_file(program_id, aid, vid, name) -> dict` — `{name,size,content,binary}`, path-guarded to the version dir; text decoded (errors="replace"), binaries flagged with empty content; raises `NotFoundError` on traversal/missing.
  - `artifact_page_file(program_id, aid, vid, relpath) -> Path` — path-guarded absolute file `Path` for serving a page asset; raises `NotFoundError` on traversal/missing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_artifacts_files.py
from coscience import artifacts
from coscience.service import NotFoundError, Service


def _seed(substrate, aid="page", files=None):
    artifacts.create_artifact(substrate, "p", aid, aid, "page")
    work = artifacts.seed_work(substrate, "p", aid)
    for name, text in (files or {"index.html": "<h1>hi</h1>"}).items():
        (work / name).write_text(text)
    artifacts.cut_version(substrate, "p", aid, "human", now=1.0)


def test_read_artifact_file_text(substrate):
    _seed(substrate, "doc", {"content.md": "hello world"})
    svc = Service(substrate.repo_root)
    f = svc.read_artifact_file("p", "doc", "v1", "content.md")
    assert f["content"] == "hello world"
    assert f["binary"] is False
    assert f["size"] == 11


def test_read_artifact_file_traversal_blocked(substrate):
    _seed(substrate, "doc", {"content.md": "x"})
    svc = Service(substrate.repo_root)
    for bad in ("../../meta.md", "/etc/passwd", "../meta.md"):
        try:
            svc.read_artifact_file("p", "doc", "v1", bad)
            assert False, f"traversal not blocked: {bad}"
        except NotFoundError:
            pass


def test_artifact_page_file_returns_guarded_path(substrate):
    _seed(substrate, "site", {"index.html": "<h1>hi</h1>", "app.js": "1"})
    svc = Service(substrate.repo_root)
    p = svc.artifact_page_file("p", "site", "v1", "app.js")
    assert p.read_text() == "1"
    try:
        svc.artifact_page_file("p", "site", "v1", "../meta.md")
        assert False, "traversal not blocked"
    except NotFoundError:
        pass


def test_version_dir_missing_raises(substrate):
    _seed(substrate, "doc", {"content.md": "x"})
    svc = Service(substrate.repo_root)
    try:
        svc.artifact_version_dir("p", "doc", "v9")
        assert False
    except NotFoundError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_service_artifacts_files.py -v`
Expected: FAIL with `AttributeError: 'Service' object has no attribute 'read_artifact_file'`.

- [ ] **Step 3: Write minimal implementation**

Append to the artifacts section in `src/coscience/service.py`:

```python
    def artifact_version_dir(self, program_id: str, aid: str, vid: str) -> Path:
        d = (self.substrate.artifact_dir(program_id, aid) / vid).resolve()
        base = self.substrate.artifact_dir(program_id, aid).resolve()
        if d.parent != base or not d.is_dir():
            raise NotFoundError(vid)
        return d

    def _guarded_file(self, program_id: str, aid: str, vid: str, relpath: str) -> Path:
        vdir = self.artifact_version_dir(program_id, aid, vid)
        path = (vdir / relpath).resolve()
        if not path.is_file() or not path.is_relative_to(vdir):
            raise NotFoundError(relpath)
        return path

    def read_artifact_file(self, program_id: str, aid: str, vid: str, name: str) -> dict:
        path = self._guarded_file(program_id, aid, vid, name)
        raw = path.read_bytes()
        binary = b"\x00" in raw[:8192]
        return {"name": name, "size": len(raw),
                "content": "" if binary else raw.decode("utf-8", errors="replace"),
                "binary": binary}

    def artifact_page_file(self, program_id: str, aid: str, vid: str, relpath: str) -> Path:
        return self._guarded_file(program_id, aid, vid, relpath)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_service_artifacts_files.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_artifacts_files.py
git commit -m "feat(artifacts): service version file read + path-guarded serve"
```

---

### Task 3: Service — revert + archive

**Files:**
- Modify: `src/coscience/service.py` (append to the artifacts section)
- Test: `tests/test_service_artifacts_write.py`

**Interfaces:**
- Consumes: `artifacts.revert/archive_version/archive_artifact` (Phase 1); `get_artifact` (Task 1).
- Produces (methods on `Service`, each commits and returns `get_artifact(...)`):
  - `revert_artifact(program_id, aid, vid) -> dict` — `NotFoundError` if artifact missing; `ValueError` (unknown vid) propagates from `artifacts.revert`.
  - `set_artifact_archived(program_id, aid, archived: bool) -> dict`.
  - `set_artifact_version_archived(program_id, aid, vid, archived: bool) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_artifacts_write.py
from coscience import artifacts
from coscience.service import NotFoundError, Service


def _two_versions(substrate, aid="doc"):
    artifacts.create_artifact(substrate, "p", aid, aid, "md")
    for text, now in (("one", 1.0), ("two", 2.0)):
        work = artifacts.seed_work(substrate, "p", aid)
        (work / "c.md").write_text(text)
        artifacts.cut_version(substrate, "p", aid, "human", now=now)


def test_revert_artifact_moves_current(substrate):
    _two_versions(substrate)
    svc = Service(substrate.repo_root)
    d = svc.revert_artifact("p", "doc", "v1")
    assert d["current"] == "v1"
    assert [v["id"] for v in d["versions"]] == ["v1", "v2"]   # nothing deleted


def test_revert_unknown_version_raises(substrate):
    _two_versions(substrate)
    svc = Service(substrate.repo_root)
    try:
        svc.revert_artifact("p", "doc", "v9")
        assert False
    except ValueError:
        pass


def test_archive_whole_artifact(substrate):
    _two_versions(substrate)
    svc = Service(substrate.repo_root)
    d = svc.set_artifact_archived("p", "doc", True)
    assert d["archived"] is True
    assert [a["id"] for a in svc.list_artifacts("p")] == []
    d = svc.set_artifact_archived("p", "doc", False)
    assert d["archived"] is False


def test_archive_single_version(substrate):
    _two_versions(substrate)
    svc = Service(substrate.repo_root)
    d = svc.set_artifact_version_archived("p", "doc", "v1", True)
    v1 = next(v for v in d["versions"] if v["id"] == "v1")
    assert v1["archived"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_service_artifacts_write.py -v`
Expected: FAIL with `AttributeError: 'Service' object has no attribute 'revert_artifact'`.

- [ ] **Step 3: Write minimal implementation**

Append to the artifacts section in `src/coscience/service.py`:

```python
    def revert_artifact(self, program_id: str, aid: str, vid: str) -> dict:
        from coscience import artifacts
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        artifacts.revert(self.substrate, program_id, aid, vid)   # ValueError on unknown vid
        self.substrate.commit(f"artifact {program_id}/{aid}: revert to {vid}")
        return self.get_artifact(program_id, aid)

    def set_artifact_archived(self, program_id: str, aid: str, archived: bool) -> dict:
        from coscience import artifacts
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        artifacts.archive_artifact(self.substrate, program_id, aid, archived)
        self.substrate.commit(f"artifact {program_id}/{aid}: archived={archived}")
        return self.get_artifact(program_id, aid)

    def set_artifact_version_archived(self, program_id: str, aid: str, vid: str,
                                      archived: bool) -> dict:
        from coscience import artifacts
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        artifacts.archive_version(self.substrate, program_id, aid, vid, archived)
        self.substrate.commit(f"artifact {program_id}/{aid}: version {vid} archived={archived}")
        return self.get_artifact(program_id, aid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_service_artifacts_write.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_artifacts_write.py
git commit -m "feat(artifacts): service revert + archive"
```

---

### Task 4: Service — artifact comment threads

**Files:**
- Modify: `src/coscience/service.py` (append to the artifacts section)
- Test: `tests/test_service_artifacts_threads.py`

**Interfaces:**
- Consumes: `coscience.threads` (`new_thread`, `append`, `public`); `Substrate.load_artifact/save_artifact`.
- Produces (methods on `Service`):
  - `add_artifact_comment(program_id, aid, text, by="", thread_id="") -> dict` — a thread always `target:"pm"`; new thread, or append a human message to `thread_id`. `ValueError` on empty text; `NotFoundError` on missing artifact/thread. Returns `threads.public(t)`.
  - `complete_artifact_thread`, `reopen_artifact_thread`, `seen_artifact_thread` `(program_id, aid, thread_id) -> dict`.
  - `delete_artifact_thread(program_id, aid, thread_id) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_artifacts_threads.py
from coscience import artifacts
from coscience.service import NotFoundError, Service


def _mk(substrate, aid="doc"):
    artifacts.create_artifact(substrate, "p", aid, aid, "md")


def test_add_comment_creates_pm_thread(substrate):
    _mk(substrate)
    svc = Service(substrate.repo_root)
    t = svc.add_artifact_comment("p", "doc", "please tighten the intro", by="oleg")
    assert t["target"] == "pm"
    assert t["messages"][0]["text"] == "please tighten the intro"
    assert t["messages"][0]["by"] == "oleg"
    # persisted on the artifact
    assert len(svc.get_artifact("p", "doc")["threads"]) == 1


def test_add_comment_appends_to_thread(substrate):
    _mk(substrate)
    svc = Service(substrate.repo_root)
    t = svc.add_artifact_comment("p", "doc", "first", by="oleg")
    t2 = svc.add_artifact_comment("p", "doc", "second", by="oleg", thread_id=t["id"])
    assert [m["text"] for m in t2["messages"]] == ["first", "second"]


def test_empty_comment_rejected(substrate):
    _mk(substrate)
    svc = Service(substrate.repo_root)
    try:
        svc.add_artifact_comment("p", "doc", "   ")
        assert False
    except ValueError:
        pass


def test_complete_and_delete_thread(substrate):
    _mk(substrate)
    svc = Service(substrate.repo_root)
    t = svc.add_artifact_comment("p", "doc", "note")
    assert svc.complete_artifact_thread("p", "doc", t["id"])["status"] == "complete"
    svc.delete_artifact_thread("p", "doc", t["id"])
    assert svc.get_artifact("p", "doc")["threads"] == []


def test_comment_missing_artifact_raises(substrate):
    svc = Service(substrate.repo_root)
    try:
        svc.add_artifact_comment("p", "ghost", "x")
        assert False
    except NotFoundError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_service_artifacts_threads.py -v`
Expected: FAIL with `AttributeError: 'Service' object has no attribute 'add_artifact_comment'`.

- [ ] **Step 3: Write minimal implementation**

Append to the artifacts section in `src/coscience/service.py` (the module already
imports `threads` and `time` at the top):

```python
    def _load_artifact(self, program_id: str, aid: str):
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            raise NotFoundError(aid)
        return self.substrate.load_artifact(program_id, aid)

    def add_artifact_comment(self, program_id: str, aid: str, text: str,
                             by: str = "", thread_id: str = "") -> dict:
        text = text.strip()
        if not text:
            raise ValueError("comment text is required")
        a = self._load_artifact(program_id, aid)
        if thread_id:
            t = next((x for x in a.threads if x["id"] == thread_id), None)
            if t is None:
                raise NotFoundError(thread_id)
            threads.append(t, "human", text, by, now=time.time())
        else:
            t = threads.new_thread("pm", text, by, now=time.time())   # artifact threads -> PM
            a.threads.append(t)
        self.substrate.save_artifact(a)
        self.substrate.commit(f"artifact {program_id}/{aid}: comment")
        return threads.public(t)

    def _mutate_artifact_thread(self, program_id: str, aid: str, thread_id: str, fn) -> dict:
        a = self._load_artifact(program_id, aid)
        t = next((x for x in a.threads if x["id"] == thread_id), None)
        if t is None:
            raise NotFoundError(thread_id)
        fn(t)
        self.substrate.save_artifact(a)
        self.substrate.commit(f"artifact {program_id}/{aid}: thread {thread_id}")
        return threads.public(t)

    def complete_artifact_thread(self, program_id: str, aid: str, thread_id: str) -> dict:
        return self._mutate_artifact_thread(program_id, aid, thread_id,
                                            lambda t: t.update(status="complete"))

    def reopen_artifact_thread(self, program_id: str, aid: str, thread_id: str) -> dict:
        return self._mutate_artifact_thread(program_id, aid, thread_id,
                                            lambda t: t.update(status="open"))

    def seen_artifact_thread(self, program_id: str, aid: str, thread_id: str) -> dict:
        return self._mutate_artifact_thread(program_id, aid, thread_id,
                                            lambda t: t.update(agent_unseen=False))

    def delete_artifact_thread(self, program_id: str, aid: str, thread_id: str) -> None:
        a = self._load_artifact(program_id, aid)
        if not any(x["id"] == thread_id for x in a.threads):
            raise NotFoundError(thread_id)
        a.threads = [x for x in a.threads if x["id"] != thread_id]
        self.substrate.save_artifact(a)
        self.substrate.commit(f"artifact {program_id}/{aid}: thread {thread_id} deleted")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_service_artifacts_threads.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_service_artifacts_threads.py
git commit -m "feat(artifacts): service artifact comment threads (target pm)"
```

---

### Task 5: get_sprint exposes bound/created artifacts

**Files:**
- Modify: `src/coscience/service.py` (`get_sprint`, the returned dict ~line 291-321)
- Test: `tests/test_sprint_artifacts_field.py`

**Interfaces:**
- Consumes: `Sprint.artifacts_bound/artifacts_create` (Phase 2).
- Produces: `get_sprint(...)` dict gains `"artifacts_bound": list[str]` and `"artifacts_create": list[dict]` (the cross-link from a sprint to its artifacts).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sprint_artifacts_field.py
from coscience.models import Sprint, SprintStatus
from coscience.service import Service


def test_get_sprint_exposes_artifact_fields(substrate):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.QUEUED, goals="g",
                                 plan=["x"], program="p",
                                 artifacts_bound=["doc"],
                                 artifacts_create=[{"aid": "fig", "title": "Fig", "kind": "figure"}]))
    svc = Service(substrate.repo_root)
    d = svc.get_sprint("s1")
    assert d["artifacts_bound"] == ["doc"]
    assert d["artifacts_create"] == [{"aid": "fig", "title": "Fig", "kind": "figure"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sprint_artifacts_field.py -v`
Expected: FAIL with `KeyError: 'artifacts_bound'`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/service.py` `get_sprint`, add to the returned dict (after
`"plan": list(sprint.plan),` on line 304):

```python
            "artifacts_bound": list(sprint.artifacts_bound),
            "artifacts_create": [dict(c) for c in sprint.artifacts_create],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sprint_artifacts_field.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py tests/test_sprint_artifacts_field.py
git commit -m "feat(artifacts): get_sprint exposes bound/created artifacts"
```

---

### Task 6: HTTP — read + download + page-serve routes

**Files:**
- Modify: `src/coscience/http_api.py` (imports; new routes near the results routes ~line 440)
- Test: `tests/test_http_artifacts_read.py`

**Interfaces:**
- Consumes: `Service.list_artifacts/get_artifact/read_artifact_file/artifact_version_dir/artifact_page_file` (Tasks 1-2).
- Produces routes:
  - `GET /programs/{pid}/artifacts` → `service.list_artifacts`.
  - `GET /programs/{pid}/artifacts/{aid}` → `service.get_artifact` (404 → NotFound).
  - `GET /programs/{pid}/artifacts/{aid}/versions/{vid}/files/{name}` → `service.read_artifact_file`.
  - `GET /programs/{pid}/artifacts/{aid}/versions/{vid}/download` → single file `FileResponse`, else zip `Response` (`application/zip`, `Content-Disposition` attachment).
  - `GET /programs/{pid}/artifacts/{aid}/versions/{vid}/page/{path:path}` → `FileResponse` of a page asset with a restrictive `Content-Security-Policy` header (`default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:`) + `X-Content-Type-Options: nosniff`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_artifacts_read.py
import io
import zipfile

from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.http_api import build_app
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _seed(substrate, aid, files):
    artifacts.create_artifact(substrate, "p", aid, aid, "page")
    work = artifacts.seed_work(substrate, "p", aid)
    for name, text in files.items():
        (work / name).write_text(text)
    artifacts.cut_version(substrate, "p", aid, "human", now=1.0)


def test_list_and_get(substrate):
    _seed(substrate, "doc", {"content.md": "hi"})
    c = _client(substrate)
    assert [a["id"] for a in c.get("/api/programs/p/artifacts").json()] == ["doc"]
    d = c.get("/api/programs/p/artifacts/doc").json()
    assert d["current"] == "v1"
    assert d["current_files"] == ["content.md"]


def test_read_file(substrate):
    _seed(substrate, "doc", {"content.md": "hello"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/doc/versions/v1/files/content.md")
    assert r.json()["content"] == "hello"


def test_download_single_file_raw(substrate):
    _seed(substrate, "doc", {"content.md": "hello"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/doc/versions/v1/download")
    assert r.status_code == 200
    assert r.content == b"hello"


def test_download_multi_file_zip(substrate):
    _seed(substrate, "site", {"index.html": "<h1>x</h1>", "app.js": "1"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/site/versions/v1/download")
    assert r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert set(names) == {"index.html", "app.js"}


def test_page_serve_has_csp(substrate):
    _seed(substrate, "site", {"index.html": "<h1>x</h1>"})
    c = _client(substrate)
    r = c.get("/api/programs/p/artifacts/site/versions/v1/page/index.html")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_get_missing_404(substrate):
    c = _client(substrate)
    assert c.get("/api/programs/p/artifacts/ghost").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_http_artifacts_read.py -v`
Expected: FAIL (404 for `/api/programs/p/artifacts` — route not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/http_api.py`, extend the imports at the top (after line 13):

```python
import io
import zipfile
```

Add these routes just before the `@api.get("/ledger")` route (~line 451):

```python
    @api.get("/programs/{program_id}/artifacts")
    def list_artifacts(program_id: str) -> list[dict]:
        return service.list_artifacts(program_id)

    @api.get("/programs/{program_id}/artifacts/{aid}")
    def get_artifact(program_id: str, aid: str) -> dict:
        try:
            return service.get_artifact(program_id, aid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"artifact not found: {aid}")

    @api.get("/programs/{program_id}/artifacts/{aid}/versions/{vid}/files/{name}")
    def read_artifact_file(program_id: str, aid: str, vid: str, name: str) -> dict:
        try:
            return service.read_artifact_file(program_id, aid, vid, name)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"file not found: {name}")

    @api.get("/programs/{program_id}/artifacts/{aid}/versions/{vid}/download")
    def download_artifact_version(program_id: str, aid: str, vid: str):
        try:
            vdir = service.artifact_version_dir(program_id, aid, vid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="version not found")
        files = sorted(p for p in vdir.rglob("*") if p.is_file())
        if not files:
            raise HTTPException(status_code=404, detail="version is empty")
        if len(files) == 1:
            return FileResponse(files[0], filename=files[0].name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in files:
                z.write(p, p.relative_to(vdir))
        return Response(
            content=buf.getvalue(), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{aid}-{vid}.zip"'})

    @api.get("/programs/{program_id}/artifacts/{aid}/versions/{vid}/page/{path:path}")
    def artifact_page(program_id: str, aid: str, vid: str, path: str):
        try:
            fp = service.artifact_page_file(program_id, aid, vid, path)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="page asset not found")
        resp = FileResponse(fp)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; font-src data:")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_http_artifacts_read.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/http_api.py tests/test_http_artifacts_read.py
git commit -m "feat(artifacts): HTTP read + download + sandboxed page routes"
```

---

### Task 7: HTTP — write routes (revert / archive / comment / threads)

**Files:**
- Modify: `src/coscience/http_api.py` (request models near line 46-60; routes after Task 6's routes)
- Test: `tests/test_http_artifacts_write.py`

**Interfaces:**
- Consumes: `Service.revert_artifact/set_artifact_archived/set_artifact_version_archived/add_artifact_comment/complete_artifact_thread/reopen_artifact_thread/seen_artifact_thread/delete_artifact_thread` (Tasks 3-4); `current_user`, `auth`.
- Produces routes (all write routes take `user=Depends(current_user)`):
  - `POST .../artifacts/{aid}/revert` body `{vid}` → `revert_artifact` (422 on unknown vid).
  - `POST .../artifacts/{aid}/archive` body `{archived}` → `set_artifact_archived`.
  - `POST .../artifacts/{aid}/versions/{vid}/archive` body `{archived}` → `set_artifact_version_archived`.
  - `POST .../artifacts/{aid}/comments` (201) body `{text, thread_id?}` → `add_artifact_comment`.
  - `POST .../artifacts/{aid}/threads/{tid}/{complete|reopen|seen}` and `DELETE .../artifacts/{aid}/threads/{tid}` (204).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_artifacts_write.py
from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.http_api import build_app
from coscience.service import Service


def _client(substrate):
    return TestClient(build_app(Service(substrate.repo_root)))


def _two(substrate, aid="doc"):
    artifacts.create_artifact(substrate, "p", aid, aid, "md")
    for text, now in (("one", 1.0), ("two", 2.0)):
        work = artifacts.seed_work(substrate, "p", aid)
        (work / "c.md").write_text(text)
        artifacts.cut_version(substrate, "p", aid, "human", now=now)


def test_revert(substrate):
    _two(substrate)
    c = _client(substrate)
    r = c.post("/api/programs/p/artifacts/doc/revert", json={"vid": "v1"})
    assert r.status_code == 200
    assert r.json()["current"] == "v1"


def test_revert_unknown_422(substrate):
    _two(substrate)
    c = _client(substrate)
    assert c.post("/api/programs/p/artifacts/doc/revert", json={"vid": "v9"}).status_code == 422


def test_archive_artifact_and_version(substrate):
    _two(substrate)
    c = _client(substrate)
    assert c.post("/api/programs/p/artifacts/doc/archive", json={"archived": True}).json()["archived"] is True
    r = c.post("/api/programs/p/artifacts/doc/versions/v1/archive", json={"archived": True})
    assert next(v for v in r.json()["versions"] if v["id"] == "v1")["archived"] is True


def test_comment_and_thread_lifecycle(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "doc", "md")
    c = _client(substrate)
    t = c.post("/api/programs/p/artifacts/doc/comments", json={"text": "tighten intro"}).json()
    assert t["target"] == "pm"
    tid = t["id"]
    assert c.post(f"/api/programs/p/artifacts/doc/threads/{tid}/complete").json()["status"] == "complete"
    assert c.delete(f"/api/programs/p/artifacts/doc/threads/{tid}").status_code == 204
    assert c.get("/api/programs/p/artifacts/doc").json()["threads"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_http_artifacts_write.py -v`
Expected: FAIL (404/405 — routes not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/http_api.py`, add request models near the other `BaseModel`
classes (after `SprintSubmit`, ~line 54):

```python
class ArtifactRevertIn(BaseModel):
    vid: str


class ArtifactArchiveIn(BaseModel):
    archived: bool = True


class ArtifactCommentIn(BaseModel):
    text: str
    thread_id: str = ""
```

Add these routes after Task 6's artifact routes:

```python
    @api.post("/programs/{program_id}/artifacts/{aid}/revert")
    def revert_artifact(program_id: str, aid: str, body: ArtifactRevertIn,
                        user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.revert_artifact(program_id, aid, body.vid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"artifact not found: {aid}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.post("/programs/{program_id}/artifacts/{aid}/archive")
    def archive_artifact(program_id: str, aid: str, body: ArtifactArchiveIn,
                         user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.set_artifact_archived(program_id, aid, body.archived)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"artifact not found: {aid}")

    @api.post("/programs/{program_id}/artifacts/{aid}/versions/{vid}/archive")
    def archive_artifact_version(program_id: str, aid: str, vid: str,
                                 body: ArtifactArchiveIn,
                                 user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.set_artifact_version_archived(program_id, aid, vid, body.archived)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"artifact not found: {aid}")

    @api.post("/programs/{program_id}/artifacts/{aid}/comments", status_code=201)
    def comment_artifact(program_id: str, aid: str, body: ArtifactCommentIn,
                         user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.add_artifact_comment(
                program_id, aid, body.text,
                by=(user.username if user else ""), thread_id=body.thread_id)
        except NotFoundError as exc:
            missing = exc.args[0] if exc.args else aid
            raise HTTPException(status_code=404, detail=f"not found: {missing}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.post("/programs/{program_id}/artifacts/{aid}/threads/{tid}/complete")
    def complete_artifact_thread(program_id: str, aid: str, tid: str,
                                 user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.complete_artifact_thread(program_id, aid, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/programs/{program_id}/artifacts/{aid}/threads/{tid}/reopen")
    def reopen_artifact_thread(program_id: str, aid: str, tid: str,
                               user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.reopen_artifact_thread(program_id, aid, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.post("/programs/{program_id}/artifacts/{aid}/threads/{tid}/seen")
    def seen_artifact_thread(program_id: str, aid: str, tid: str,
                             user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.seen_artifact_thread(program_id, aid, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @api.delete("/programs/{program_id}/artifacts/{aid}/threads/{tid}", status_code=204)
    def delete_artifact_thread(program_id: str, aid: str, tid: str,
                               user: "auth.User | None" = Depends(current_user)) -> Response:
        try:
            service.delete_artifact_thread(program_id, aid, tid)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")
        return Response(status_code=204)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_http_artifacts_write.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full artifact HTTP+service suites + commit**

Run: `python -m pytest tests/test_service_artifacts_*.py tests/test_http_artifacts_*.py tests/test_sprint_artifacts_field.py -v`
Expected: PASS (all green).

```bash
git add src/coscience/http_api.py tests/test_http_artifacts_write.py
git commit -m "feat(artifacts): HTTP write routes — revert / archive / comment / threads"
```

---

## Phase 3a Done — What Exists Now

The Service + HTTP API fully expose artifacts: list/get (with the version tree,
threads, current files, and sprint cross-links), per-file read, version download
(raw or zip), sandboxed page-asset serving, revert, archive (version + whole),
comment threads (target PM), and the sprint→artifacts cross-link on `get_sprint`.

**Next: Phase 3b (frontend)** consumes these — Artifacts tab, artifact detail with
version tree + revert/archive, comment thread, download, page iframe, cross-links,
sprint bind/create modals. `build_app` route wiring is checked here; the browser
UI is 3b. (Note: `build_app` must be the app factory the tests import — confirm
its name/signature in http_api.py during Task 6.)
