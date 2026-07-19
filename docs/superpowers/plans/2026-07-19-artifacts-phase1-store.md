# Artifacts Phase 1 — Store + Versioning + Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend core of the artifacts subsystem — a versioned,
tree-structured, lockable per-program artifact store — with no UI, sprint, or PM
wiring yet.

**Architecture:** Two new units. `models.py` gains `Artifact` +
`ArtifactVersion` dataclasses. A new `src/coscience/artifacts.py` module holds
all store *operations* (create, version cut with dedup, revert, archive, lock
acquire/release, stale-lock reap, `work/` seed+snapshot). `substrate.py` gains
only the persistence seam (`artifact_dir`, `load_artifact`, `save_artifact`,
`iter_artifacts`) so the big file stays persistence-only and the tree/lock logic
lives in its own focused module.

**Tech Stack:** Python 3.11 (dataclasses, `StrEnum`-style enums already in the
repo), `pyyaml` via `coscience.frontmatter_io`, `fcntl.flock` for the
cross-process lock guard (mirrors `pm_agent._acquire_program_lock`), `shutil`
for version-folder copies, `pytest` with the existing `substrate` (`tmp_path`)
fixture.

## Global Constraints

- **Runtime is Linux-only** (`fcntl`/`flock`); tests run on the Linux dev host
  (`~/coscience-dev`, port 8001) or CI, NOT on the Windows dev box. See
  `CLAUDE.md` and the `coscience-linux-only` memory.
- **Storage layout:** `programs/{pid}/artifacts/{aid}/` with `meta.md`, a
  mutable `work/`, and immutable `v1/ v2/ …` version folders.
- **No hard delete anywhere** — discard is `archived: true`, always reversible.
- **Dedup rule:** cut no version when `work/` is byte-identical to its parent
  version folder.
- **Lock is exclusive, capacity-1.** Multi-artifact acquire is atomic —
  all-or-none; never leave a partial acquire.
- **`meta.md` is written/read only via `coscience.frontmatter_io`** (`serialize`
  / `parse`), same as every other substrate doc.
- **`kind` ∈ `{md, data, figure, page}`.**
- **`created_by` ∈ sprint id | `"chat:<id>"` | `"human"`.**
- **Chat inactivity timeout = 1800.0 s (30 min).**
- All substrate writes go through Python; the reasoner/agent never writes the
  store directly (propose-only invariant, enforced later phases).

---

### Task 1: `Artifact` + `ArtifactVersion` models

**Files:**
- Modify: `src/coscience/models.py` (append after the `Result` dataclass, ~line 107)
- Test: `tests/test_artifact_models.py`

**Interfaces:**
- Consumes: nothing (leaf).
- Produces:
  - `ArtifactVersion(id: str, parent: str = "", created_at: float = 0.0, created_by: str = "", archived: bool = False, note: str = "")`
  - `Artifact(id: str, program: str, title: str = "", kind: str = "md", current: str = "", lock: dict = {}, versions: list[ArtifactVersion] = [], threads: list[dict] = [], archived: bool = False)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_models.py
from coscience.models import Artifact, ArtifactVersion


def test_artifact_defaults():
    a = Artifact(id="manuscript", program="kg-biomed")
    assert a.kind == "md"
    assert a.current == ""
    assert a.lock == {}
    assert a.versions == []
    assert a.threads == []
    assert a.archived is False


def test_artifact_version_defaults():
    v = ArtifactVersion(id="v1")
    assert v.parent == ""
    assert v.created_by == ""
    assert v.archived is False
    assert v.note == ""


def test_artifact_holds_versions():
    a = Artifact(id="fig", program="p", kind="figure", current="v2",
                 versions=[ArtifactVersion(id="v1"),
                           ArtifactVersion(id="v2", parent="v1")])
    assert a.current == "v2"
    assert [v.id for v in a.versions] == ["v1", "v2"]
    assert a.versions[1].parent == "v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'Artifact'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/models.py` (after `Result`, before `ProgressState`):

```python
@dataclass
class ArtifactVersion:
    """One node in an artifact's version tree. `parent` is the version this one
    was derived from ("" = root). Append-only; never deleted, only `archived`."""
    id: str                              # "v1", "v2", ...
    parent: str = ""                     # "" = root
    created_at: float = 0.0
    created_by: str = ""                 # sprint id | "chat:<id>" | "human"
    archived: bool = False
    note: str = ""


@dataclass
class Artifact:
    """A program-level deliverable (report/data/figure/page) with a versioned,
    tree-structured store. `current` names the active leaf version; `lock` is the
    exclusive editing hold ({} = unlocked); `archived` is a whole-artifact discard."""
    id: str
    program: str
    title: str = ""
    kind: str = "md"                     # md | data | figure | page
    current: str = ""                    # active leaf version id; "" = no versions yet
    lock: dict = field(default_factory=dict)          # {} = unlocked
    versions: list[ArtifactVersion] = field(default_factory=list)
    threads: list[dict] = field(default_factory=list)  # feedback threads (target "pm")
    archived: bool = False               # whole-artifact discard (reversible)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/models.py tests/test_artifact_models.py
git commit -m "feat(artifacts): Artifact + ArtifactVersion models"
```

---

### Task 2: Substrate persistence (`meta.md` round-trip)

**Files:**
- Modify: `src/coscience/substrate.py` (add an `# --- artifacts ---` section after the `# --- results ---` block, ~line 212; import `Artifact, ArtifactVersion` at line 9)
- Test: `tests/test_artifact_substrate.py`

**Interfaces:**
- Consumes: `Artifact`, `ArtifactVersion` (Task 1); `Substrate.program_dir`, `frontmatter_io.parse/serialize` (existing).
- Produces (methods on `Substrate`):
  - `artifact_dir(program_id: str, aid: str) -> Path`
  - `load_artifact(program_id: str, aid: str) -> Artifact`
  - `save_artifact(artifact: Artifact) -> None`  (uses `artifact.program`)
  - `iter_artifacts(program_id: str, include_archived: bool = False) -> list[Artifact]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_substrate.py
from coscience.models import Artifact, ArtifactVersion


def test_save_load_roundtrip(substrate):
    a = Artifact(id="manuscript", program="kg-biomed", title="Manuscript",
                 kind="md", current="v1",
                 lock={"holder_kind": "chat", "holder_id": "chat:ab12",
                       "acquired_at": 10.0, "last_activity": 20.0},
                 versions=[ArtifactVersion(id="v1", created_by="human",
                                           created_at=5.0, note="first")],
                 threads=[{"id": "t1"}])
    substrate.save_artifact(a)
    b = substrate.load_artifact("kg-biomed", "manuscript")
    assert b.title == "Manuscript"
    assert b.kind == "md"
    assert b.current == "v1"
    assert b.lock["holder_id"] == "chat:ab12"
    assert b.lock["last_activity"] == 20.0
    assert len(b.versions) == 1
    assert b.versions[0].created_by == "human"
    assert b.versions[0].note == "first"
    assert b.threads == [{"id": "t1"}]
    assert b.archived is False


def test_artifact_dir_path(substrate):
    p = substrate.artifact_dir("kg-biomed", "fig")
    assert p == substrate.program_dir("kg-biomed") / "artifacts" / "fig"


def test_iter_artifacts_hides_archived_by_default(substrate):
    substrate.save_artifact(Artifact(id="a1", program="p", title="A1"))
    substrate.save_artifact(Artifact(id="a2", program="p", title="A2", archived=True))
    ids = [a.id for a in substrate.iter_artifacts("p")]
    assert ids == ["a1"]
    ids_all = [a.id for a in substrate.iter_artifacts("p", include_archived=True)]
    assert ids_all == ["a1", "a2"]


def test_iter_artifacts_empty_when_none(substrate):
    assert substrate.iter_artifacts("no-such-program") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_substrate.py -v`
Expected: FAIL with `AttributeError: 'Substrate' object has no attribute 'save_artifact'`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/substrate.py`, extend the model import on line 9:

```python
from coscience.models import (Sprint, SprintStatus, ProgressState, Result, Program,
                              ProgramStatus, PMState, Idea, ChatThread, Artifact,
                              ArtifactVersion)
```

Add after the `iter_results` method (after ~line 212), before `# --- programs ---`:

```python
    # --- artifacts (versioned program deliverables) ---
    def artifact_dir(self, program_id: str, aid: str) -> Path:
        return self.program_dir(program_id) / "artifacts" / aid

    def load_artifact(self, program_id: str, aid: str) -> Artifact:
        text = (self.artifact_dir(program_id, aid) / "meta.md").read_text()
        fm, _body = parse(text)
        return Artifact(
            id=aid, program=program_id,
            title=str(fm.get("title", "")),
            kind=str(fm.get("kind", "md")),
            current=str(fm.get("current", "")),
            lock=dict(fm.get("lock") or {}),
            versions=[ArtifactVersion(
                id=str(v["id"]), parent=str(v.get("parent", "")),
                created_at=float(v.get("created_at", 0.0)),
                created_by=str(v.get("created_by", "")),
                archived=bool(v.get("archived", False)),
                note=str(v.get("note", "")))
                for v in fm.get("versions", [])],
            threads=list(fm.get("threads", [])),
            archived=bool(fm.get("archived", False)),
        )

    def save_artifact(self, artifact: Artifact) -> None:
        fm = {
            "type": "artifact",
            "title": artifact.title,
            "kind": artifact.kind,
            "current": artifact.current,
            "lock": artifact.lock,
            "versions": [
                {"id": v.id, "parent": v.parent, "created_at": v.created_at,
                 "created_by": v.created_by, "archived": v.archived, "note": v.note}
                for v in artifact.versions],
        }
        if artifact.threads:
            fm["threads"] = list(artifact.threads)
        if artifact.archived:
            fm["archived"] = True
        d = self.artifact_dir(artifact.program, artifact.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.md").write_text(serialize(fm, f"# {artifact.title or artifact.id}\n"))

    def iter_artifacts(self, program_id: str,
                       include_archived: bool = False) -> list[Artifact]:
        d = self.program_dir(program_id) / "artifacts"
        out: list[Artifact] = []
        for sub in (sorted(d.iterdir()) if d.is_dir() else []):
            if (sub / "meta.md").is_file():
                a = self.load_artifact(program_id, sub.name)
                if include_archived or not a.archived:
                    out.append(a)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_substrate.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/substrate.py tests/test_artifact_substrate.py
git commit -m "feat(artifacts): substrate persistence for artifact meta.md"
```

---

### Task 3: Create + version cut with dedup (`work/` seed + snapshot)

**Files:**
- Create: `src/coscience/artifacts.py`
- Test: `tests/test_artifact_versioning.py`

**Interfaces:**
- Consumes: `Substrate.artifact_dir/load_artifact/save_artifact` (Task 2); `Artifact`, `ArtifactVersion` (Task 1).
- Produces (module functions in `coscience.artifacts`):
  - `create_artifact(substrate, program_id: str, aid: str, title: str, kind: str) -> Artifact`
  - `seed_work(substrate, program_id: str, aid: str) -> Path` — (re)creates `work/` seeded from `current` (empty if no current); returns the `work/` path.
  - `cut_version(substrate, program_id: str, aid: str, created_by: str, now: float, note: str = "") -> str | None` — snapshot `work/` → new `v{n+1}` (parent = `current`), dedup vs `current`, bump `current`; returns the new version id, or `None` when there is no `work/`, when it is byte-identical to `current` (dedup), or when it is the first version but `work/` holds no files (nothing was produced).
  - `_dirs_identical(a: Path, b: Path) -> bool` (private helper, tested via `cut_version`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_versioning.py
from coscience import artifacts
from coscience.models import Artifact


def _write(work, name, text):
    (work / name).write_text(text)


def test_create_artifact(substrate):
    a = artifacts.create_artifact(substrate, "p", "fig", "Figure", "figure")
    assert a.kind == "figure"
    assert a.current == ""
    assert (substrate.artifact_dir("p", "fig") / "meta.md").is_file()


def test_create_rejects_duplicate(substrate):
    artifacts.create_artifact(substrate, "p", "fig", "Figure", "figure")
    try:
        artifacts.create_artifact(substrate, "p", "fig", "Figure", "figure")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_first_version_from_work(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    work = artifacts.seed_work(substrate, "p", "doc")
    _write(work, "content.md", "hello")
    vid = artifacts.cut_version(substrate, "p", "doc", "human", now=1.0, note="first")
    assert vid == "v1"
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"
    assert a.versions[0].parent == ""
    assert a.versions[0].note == "first"
    assert (substrate.artifact_dir("p", "doc") / "v1" / "content.md").read_text() == "hello"


def test_second_version_branches_from_current(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    work = artifacts.seed_work(substrate, "p", "doc")
    _write(work, "content.md", "one")
    artifacts.cut_version(substrate, "p", "doc", "human", now=1.0)
    work = artifacts.seed_work(substrate, "p", "doc")           # reseeded from v1
    assert (work / "content.md").read_text() == "one"           # seed copies current
    _write(work, "content.md", "two")
    vid = artifacts.cut_version(substrate, "p", "doc", "chat:x", now=2.0)
    assert vid == "v2"
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v2"
    assert a.versions[1].parent == "v1"


def test_dedup_identical_work_cuts_nothing(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    work = artifacts.seed_work(substrate, "p", "doc")
    _write(work, "content.md", "same")
    artifacts.cut_version(substrate, "p", "doc", "human", now=1.0)   # v1
    artifacts.seed_work(substrate, "p", "doc")                       # identical to v1
    vid = artifacts.cut_version(substrate, "p", "doc", "human", now=2.0)
    assert vid is None
    a = substrate.load_artifact("p", "doc")
    assert [v.id for v in a.versions] == ["v1"]
    assert a.current == "v1"


def test_cut_without_work_returns_none(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    assert artifacts.cut_version(substrate, "p", "doc", "human", now=1.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_versioning.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.artifacts'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/coscience/artifacts.py`:

```python
"""Operations on the versioned artifact store: create, cut a version (with the
dedup rule), seed/snapshot the `work/` copy, revert, archive, and the exclusive
lock (acquire/release + stale reap). All filesystem + tree logic lives here so
substrate.py stays a thin persistence seam. Substrate holds meta.md I/O."""
from __future__ import annotations

import fcntl
import shutil
from contextlib import contextmanager
from pathlib import Path

from coscience.models import Artifact, ArtifactVersion


def create_artifact(substrate, program_id: str, aid: str, title: str,
                    kind: str) -> Artifact:
    if (substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
        raise ValueError(f"artifact {aid!r} already exists in {program_id!r}")
    art = Artifact(id=aid, program=program_id, title=title, kind=kind)
    substrate.save_artifact(art)
    return art


def seed_work(substrate, program_id: str, aid: str) -> Path:
    """(Re)create `work/` as a fresh copy of the current version (empty if the
    artifact has no versions yet). Any pre-existing `work/` is discarded first."""
    art = substrate.load_artifact(program_id, aid)
    d = substrate.artifact_dir(program_id, aid)
    work = d / "work"
    if work.exists():
        shutil.rmtree(work)
    if art.current:
        shutil.copytree(d / art.current, work)
    else:
        work.mkdir(parents=True, exist_ok=True)
    return work


def _next_vid(art: Artifact) -> str:
    n = 0
    for v in art.versions:
        try:
            n = max(n, int(v.id[1:]))
        except (ValueError, IndexError):
            continue
    return f"v{n + 1}"


def _dirs_identical(a: Path, b: Path) -> bool:
    if not (a.is_dir() and b.is_dir()):
        return False
    fa = {p.relative_to(a): p for p in a.rglob("*") if p.is_file()}
    fb = {p.relative_to(b): p for p in b.rglob("*") if p.is_file()}
    if set(fa) != set(fb):
        return False
    return all(fa[rel].read_bytes() == fb[rel].read_bytes() for rel in fa)


def cut_version(substrate, program_id: str, aid: str, created_by: str,
                now: float, note: str = "") -> str | None:
    """Snapshot `work/` into a new immutable version whose parent is `current`,
    then move `current` to it. Returns the new version id, or None when there is
    no `work/` or it is byte-identical to `current` (the dedup rule)."""
    d = substrate.artifact_dir(program_id, aid)
    work = d / "work"
    if not work.is_dir():
        return None
    art = substrate.load_artifact(program_id, aid)
    parent = art.current
    if parent:
        if _dirs_identical(work, d / parent):
            return None
    elif not any(p.is_file() for p in work.rglob("*")):
        return None                                # empty first version -> nothing to cut
    vid = _next_vid(art)
    shutil.copytree(work, d / vid)
    art.versions.append(ArtifactVersion(
        id=vid, parent=parent, created_at=now, created_by=created_by, note=note))
    art.current = vid
    substrate.save_artifact(art)
    return vid
```

(The `fcntl`, `contextmanager` imports are used by Task 6; leaving them now keeps
the diff of Task 6 to additions only.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_versioning.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/artifacts.py tests/test_artifact_versioning.py
git commit -m "feat(artifacts): create + version cut with dedup and work/ snapshot"
```

---

### Task 4: Revert (pointer move) + tree query

**Files:**
- Modify: `src/coscience/artifacts.py`
- Test: `tests/test_artifact_tree.py`

**Interfaces:**
- Consumes: `cut_version`, `seed_work`, `create_artifact` (Task 3); `Substrate.load_artifact/save_artifact` (Task 2).
- Produces:
  - `revert(substrate, program_id: str, aid: str, vid: str) -> None` — set `current = vid` (raises `ValueError` if `vid` is not a known version). Pure pointer move; cuts no version, deletes nothing.
  - `children(art: Artifact, vid: str) -> list[str]` — ids whose `parent == vid`.
  - `is_leaf(art: Artifact, vid: str) -> bool` — no version has `parent == vid`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_tree.py
from coscience import artifacts


def _one_edit(substrate, aid, text, by, now):
    work = artifacts.seed_work(substrate, "p", aid)
    (work / "c.md").write_text(text)
    return artifacts.cut_version(substrate, "p", aid, by, now=now)


def test_revert_moves_current_without_new_version(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _one_edit(substrate, "doc", "one", "human", 1.0)   # v1
    _one_edit(substrate, "doc", "two", "human", 2.0)   # v2
    artifacts.revert(substrate, "p", "doc", "v1")
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"
    assert [v.id for v in a.versions] == ["v1", "v2"]   # nothing deleted or added


def test_edit_after_revert_branches_from_reverted_node(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _one_edit(substrate, "doc", "one", "human", 1.0)   # v1
    _one_edit(substrate, "doc", "two", "human", 2.0)   # v2 (parent v1)
    artifacts.revert(substrate, "p", "doc", "v1")
    vid = _one_edit(substrate, "doc", "three", "chat:x", 3.0)   # branch off v1
    assert vid == "v3"
    a = substrate.load_artifact("p", "doc")
    assert a.versions[2].parent == "v1"                # v3 is a sibling of v2
    assert a.current == "v3"


def test_revert_unknown_version_raises(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _one_edit(substrate, "doc", "one", "human", 1.0)
    try:
        artifacts.revert(substrate, "p", "doc", "v9")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_children_and_is_leaf(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _one_edit(substrate, "doc", "one", "human", 1.0)   # v1
    _one_edit(substrate, "doc", "two", "human", 2.0)   # v2 (parent v1)
    artifacts.revert(substrate, "p", "doc", "v1")
    _one_edit(substrate, "doc", "three", "human", 3.0)  # v3 (parent v1)
    a = substrate.load_artifact("p", "doc")
    assert sorted(artifacts.children(a, "v1")) == ["v2", "v3"]
    assert artifacts.is_leaf(a, "v2") is True
    assert artifacts.is_leaf(a, "v1") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_tree.py -v`
Expected: FAIL with `AttributeError: module 'coscience.artifacts' has no attribute 'revert'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/artifacts.py`:

```python
def revert(substrate, program_id: str, aid: str, vid: str) -> None:
    """Set `current` to an existing version. Pointer move only — no version is
    cut and nothing is deleted; a later edit branches from `vid`."""
    art = substrate.load_artifact(program_id, aid)
    if vid not in {v.id for v in art.versions}:
        raise ValueError(f"no version {vid!r} in artifact {aid!r}")
    art.current = vid
    substrate.save_artifact(art)


def children(art: Artifact, vid: str) -> list[str]:
    return [v.id for v in art.versions if v.parent == vid]


def is_leaf(art: Artifact, vid: str) -> bool:
    return not children(art, vid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_tree.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/artifacts.py tests/test_artifact_tree.py
git commit -m "feat(artifacts): revert (pointer move) + version-tree queries"
```

---

### Task 5: Archive (version / subtree / whole artifact)

**Files:**
- Modify: `src/coscience/artifacts.py`
- Test: `tests/test_artifact_archive.py`

**Interfaces:**
- Consumes: `create_artifact`, `seed_work`, `cut_version` (Task 3); `Substrate.load_artifact/save_artifact/iter_artifacts` (Task 2).
- Produces:
  - `archive_version(substrate, program_id: str, aid: str, vid: str, archived: bool = True) -> None` — flag one node.
  - `archive_subtree(substrate, program_id: str, aid: str, vid: str, archived: bool = True) -> None` — flag `vid` and all descendants.
  - `archive_artifact(substrate, program_id: str, aid: str, archived: bool = True) -> None` — whole-artifact discard flag.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_archive.py
from coscience import artifacts


def _edit(substrate, aid, text, now, by="human"):
    work = artifacts.seed_work(substrate, "p", aid)
    (work / "c.md").write_text(text)
    return artifacts.cut_version(substrate, "p", aid, by, now=now)


def test_archive_single_version(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _edit(substrate, "doc", "one", 1.0)
    artifacts.archive_version(substrate, "p", "doc", "v1")
    a = substrate.load_artifact("p", "doc")
    assert a.versions[0].archived is True
    # reversible
    artifacts.archive_version(substrate, "p", "doc", "v1", archived=False)
    assert substrate.load_artifact("p", "doc").versions[0].archived is False


def test_archive_subtree_flags_descendants(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _edit(substrate, "doc", "one", 1.0)            # v1
    _edit(substrate, "doc", "two", 2.0)            # v2 (parent v1)
    artifacts.revert(substrate, "p", "doc", "v1")
    _edit(substrate, "doc", "three", 3.0)          # v3 (parent v1)
    _edit(substrate, "doc", "four", 4.0)           # v4 (parent v3)
    artifacts.archive_subtree(substrate, "p", "doc", "v3")
    a = substrate.load_artifact("p", "doc")
    flagged = {v.id: v.archived for v in a.versions}
    assert flagged == {"v1": False, "v2": False, "v3": True, "v4": True}


def test_archive_whole_artifact_hides_from_default_iter(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.archive_artifact(substrate, "p", "doc")
    assert [a.id for a in substrate.iter_artifacts("p")] == []
    assert [a.id for a in substrate.iter_artifacts("p", include_archived=True)] == ["doc"]
    # never hard-deleted
    assert (substrate.artifact_dir("p", "doc") / "meta.md").is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_archive.py -v`
Expected: FAIL with `AttributeError: module 'coscience.artifacts' has no attribute 'archive_version'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/artifacts.py`:

```python
def archive_version(substrate, program_id: str, aid: str, vid: str,
                    archived: bool = True) -> None:
    art = substrate.load_artifact(program_id, aid)
    for v in art.versions:
        if v.id == vid:
            v.archived = archived
    substrate.save_artifact(art)


def archive_subtree(substrate, program_id: str, aid: str, vid: str,
                    archived: bool = True) -> None:
    art = substrate.load_artifact(program_id, aid)
    kids: dict[str, list[str]] = {}
    for v in art.versions:
        kids.setdefault(v.parent, []).append(v.id)
    seen: set[str] = set()
    stack = [vid]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(kids.get(cur, []))
    for v in art.versions:
        if v.id in seen:
            v.archived = archived
    substrate.save_artifact(art)


def archive_artifact(substrate, program_id: str, aid: str,
                     archived: bool = True) -> None:
    art = substrate.load_artifact(program_id, aid)
    art.archived = archived
    substrate.save_artifact(art)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_archive.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/artifacts.py tests/test_artifact_archive.py
git commit -m "feat(artifacts): archive version / subtree / whole artifact (reversible)"
```

---

### Task 6: Exclusive lock — acquire / release / bump / reap

**Files:**
- Modify: `src/coscience/artifacts.py`
- Test: `tests/test_artifact_lock.py`

**Interfaces:**
- Consumes: `seed_work`, `cut_version` (Task 3); `Substrate.load_artifact/save_artifact/iter_artifacts` + `Substrate.repo_root` (Task 2 / existing).
- Produces:
  - `acquire_lock(substrate, program_id: str, aids: list[str], holder_kind: str, holder_id: str, now: float) -> bool` — atomic multi-acquire (all-or-none). Returns `False` and acquires nothing if any `aid` is locked by a *different* holder. On success, sets each `lock` and (re)seeds each `work/`. Re-acquiring an artifact already held by the same `holder_id` is a no-op success (idempotent).
  - `release_lock(substrate, program_id: str, aids: list[str], now: float, created_by: str) -> list[str | None]` — for each aid: cut a version (dedup), remove `work/`, clear `lock`; returns the per-aid new version ids (`None` where dedup cut nothing). Ignores aids that are not locked.
  - `bump_activity(substrate, program_id: str, aid: str, now: float) -> None` — set `lock.last_activity = now` (no-op if unlocked).
  - `reap_stale_chat_locks(substrate, program_id: str, now: float, timeout: float = 1800.0, holder_alive=None) -> list[str]` — release every *chat*-held lock whose idle time ≥ `timeout`, or (when `holder_alive` is given) whose holder `holder_alive(holder_id)` is `False`. Returns released aids.
- Uses a repo-level flock guard `.coscience/artifacts.lock` (mirrors `pm_agent._acquire_program_lock`) so multi-acquire is atomic across the dispatcher and HTTP processes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_lock.py
from coscience import artifacts


def _mk(substrate, aid):
    artifacts.create_artifact(substrate, "p", aid, aid.title(), "md")


def test_acquire_sets_lock_and_seeds_work(substrate):
    _mk(substrate, "doc")
    ok = artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    assert ok is True
    a = substrate.load_artifact("p", "doc")
    assert a.lock["holder_id"] == "chat:x"
    assert a.lock["last_activity"] == 1.0
    assert (substrate.artifact_dir("p", "doc") / "work").is_dir()


def test_acquire_rejected_when_held_by_other(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    ok = artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", now=2.0)
    assert ok is False
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == "chat:x"


def test_acquire_same_holder_is_idempotent(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", now=1.0)
    ok = artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", now=2.0)
    assert ok is True


def test_multi_acquire_is_atomic_all_or_none(substrate):
    _mk(substrate, "a")
    _mk(substrate, "b")
    artifacts.acquire_lock(substrate, "p", ["b"], "chat", "chat:x", now=1.0)  # b busy
    ok = artifacts.acquire_lock(substrate, "p", ["a", "b"], "sprint", "s1", now=2.0)
    assert ok is False
    # a must NOT have been locked (all-or-none)
    assert substrate.load_artifact("p", "a").lock == {}
    assert substrate.load_artifact("p", "b").lock["holder_id"] == "chat:x"


def test_release_cuts_version_and_clears_lock(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("edited")
    vids = artifacts.release_lock(substrate, "p", ["doc"], now=2.0, created_by="chat:x")
    assert vids == ["v1"]
    a = substrate.load_artifact("p", "doc")
    assert a.lock == {}
    assert a.current == "v1"
    assert not (substrate.artifact_dir("p", "doc") / "work").exists()


def test_release_dedup_cuts_no_version(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)  # empty work
    vids = artifacts.release_lock(substrate, "p", ["doc"], now=2.0, created_by="chat:x")
    assert vids == [None]
    assert substrate.load_artifact("p", "doc").versions == []


def test_bump_activity(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    artifacts.bump_activity(substrate, "p", "doc", now=99.0)
    assert substrate.load_artifact("p", "doc").lock["last_activity"] == 99.0


def test_reap_releases_idle_chat_lock(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=0.0)
    released = artifacts.reap_stale_chat_locks(substrate, "p", now=1800.0, timeout=1800.0)
    assert released == ["doc"]
    assert substrate.load_artifact("p", "doc").lock == {}


def test_reap_ignores_fresh_and_sprint_locks(substrate):
    _mk(substrate, "chatdoc")
    _mk(substrate, "sprintdoc")
    artifacts.acquire_lock(substrate, "p", ["chatdoc"], "chat", "chat:x", now=1000.0)
    artifacts.acquire_lock(substrate, "p", ["sprintdoc"], "sprint", "s1", now=0.0)
    released = artifacts.reap_stale_chat_locks(substrate, "p", now=1500.0, timeout=1800.0)
    assert released == []                       # chat is fresh; sprint never reaped by this


def test_reap_releases_dead_holder(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:dead", now=1000.0)
    released = artifacts.reap_stale_chat_locks(
        substrate, "p", now=1001.0, timeout=1800.0, holder_alive=lambda h: False)
    assert released == ["doc"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_artifact_lock.py -v`
Expected: FAIL with `AttributeError: module 'coscience.artifacts' has no attribute 'acquire_lock'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/artifacts.py`:

```python
@contextmanager
def _lock_guard(substrate):
    """Repo-level flock so multi-artifact acquire/release is atomic across the
    dispatcher and HTTP processes (mirrors pm_agent's per-program lock)."""
    lockdir = substrate.repo_root / ".coscience"
    lockdir.mkdir(parents=True, exist_ok=True)
    f = open(lockdir / "artifacts.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def acquire_lock(substrate, program_id: str, aids: list[str], holder_kind: str,
                 holder_id: str, now: float) -> bool:
    with _lock_guard(substrate):
        arts = [substrate.load_artifact(program_id, aid) for aid in aids]
        for art in arts:
            if art.lock and art.lock.get("holder_id") != holder_id:
                return False                       # any busy -> acquire none
        for art in arts:
            art.lock = {"holder_kind": holder_kind, "holder_id": holder_id,
                        "acquired_at": now, "last_activity": now}
            substrate.save_artifact(art)
            seed_work(substrate, program_id, art.id)
        return True


def release_lock(substrate, program_id: str, aids: list[str], now: float,
                 created_by: str) -> list[str | None]:
    out: list[str | None] = []
    with _lock_guard(substrate):
        for aid in aids:
            art = substrate.load_artifact(program_id, aid)
            if not art.lock:
                out.append(None)
                continue
            vid = cut_version(substrate, program_id, aid, created_by, now)
            work = substrate.artifact_dir(program_id, aid) / "work"
            if work.is_dir():
                shutil.rmtree(work)
            art = substrate.load_artifact(program_id, aid)   # reload (cut_version saved)
            art.lock = {}
            substrate.save_artifact(art)
            out.append(vid)
    return out


def bump_activity(substrate, program_id: str, aid: str, now: float) -> None:
    art = substrate.load_artifact(program_id, aid)
    if art.lock:
        art.lock["last_activity"] = now
        substrate.save_artifact(art)


def reap_stale_chat_locks(substrate, program_id: str, now: float,
                          timeout: float = 1800.0, holder_alive=None) -> list[str]:
    released: list[str] = []
    for art in substrate.iter_artifacts(program_id, include_archived=True):
        lock = art.lock
        if not lock or lock.get("holder_kind") != "chat":
            continue
        idle = now - float(lock.get("last_activity", lock.get("acquired_at", now)))
        dead = holder_alive is not None and not holder_alive(lock.get("holder_id", ""))
        if idle >= timeout or dead:
            release_lock(substrate, program_id, [art.id], now,
                         created_by=lock.get("holder_id", ""))
            released.append(art.id)
    return released
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_artifact_lock.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Run the full artifact suite + commit**

Run: `python -m pytest tests/test_artifact_*.py -v`
Expected: PASS (all artifact tests green — 6 files).

```bash
git add src/coscience/artifacts.py tests/test_artifact_lock.py
git commit -m "feat(artifacts): exclusive lock — acquire/release/bump/reap"
```

---

## Phase 1 Done — What Exists Now

A fully unit-tested backend store: `Artifact`/`ArtifactVersion` models,
`meta.md` persistence, create, `work/` seed+snapshot, version cut with dedup, a
version **tree** with revert/branch, archive at three scopes, and the exclusive
capacity-1 lock (atomic multi-acquire, release-with-version-cut, activity bump,
stale-chat reap). No UI, no sprint, no PM wiring — those are Phases 2–5.

**Not yet wired (deliberately, later phases):** the dispatcher does not yet
consult `acquire_lock` (Phase 2); the loop does not yet call
`reap_stale_chat_locks` (Phase 4); no HTTP routes or service methods (Phase 3).
```
