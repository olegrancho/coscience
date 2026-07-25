# Directory Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user set a program's project folder (`Program.workdir`) by browsing the server's filesystem from the dashboard, including creating a new folder.

**Architecture:** A standalone `fs_browse` module owns root configuration, path confinement, directory listing and mkdir — no `Service`/`Substrate` dependency, and not scoped to a program (the create-program flow picks a folder before a program exists). Two routes on the existing auth-gated `api` router expose it. One React modal serves both call sites, triggered by a subtle icon *inside* the existing text field.

**Tech Stack:** Python 3.12, FastAPI, pytest + `fastapi.testclient.TestClient`; React 18, Mantine 7.11, TanStack Query, vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-07-24-directory-picker-design.md`

## Global Constraints

- **Never commit or push without explicit approval** (project `CLAUDE.md`). Each task ends with a prepared commit step — show the command and the staged diff, and wait for Oleg's approval before running it.
- Runtime is **Linux-only**; `/` is the only path separator that needs rejecting in folder names. Do not add Windows path handling.
- `COSCIENCE_BROWSE_ROOTS` **must be read at call time**, never captured at import — every root test monkeypatches it against a `tmp_path`.
- Confinement uses the codebase's existing idiom: `.resolve()` then `.is_relative_to(root)` (as in `service.py:1155`, `http_api.py:917`).
- Both routes go on the existing `api` router (`http_api.py:181`) so they inherit `Depends(current_user)`.
- Backend tests run with `/home/oleg/venvs/coscience/bin/python -m pytest`; frontend tests with `npx vitest run` from `frontend/`.

---

### Task 1: `fs_browse` module — roots configuration

**Files:**
- Create: `src/coscience/fs_browse.py`
- Test: `tests/test_fs_browse_roots.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `roots() -> list[Path]`; exception classes `BrowseError`, `OutsideRoots`, `NotFound`, `InvalidName`, `AlreadyExists` — all used by Tasks 2, 3 and 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_browse_roots.py`:

```python
"""COSCIENCE_BROWSE_ROOTS parsing: the confinement boundary for directory browsing."""
from pathlib import Path

from coscience import fs_browse


def test_roots_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("COSCIENCE_BROWSE_ROOTS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert fs_browse.roots() == [tmp_path.resolve()]


def test_roots_parses_colon_separated_paths(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", f"{a}:{b}")
    assert fs_browse.roots() == [a.resolve(), b.resolve()]


def test_roots_expands_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", "~")
    assert fs_browse.roots() == [tmp_path.resolve()]


def test_roots_drops_nonexistent_entries(monkeypatch, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", f"{a}:{tmp_path / 'nope'}")
    assert fs_browse.roots() == [a.resolve()]


def test_roots_falls_back_to_home_when_all_invalid(monkeypatch, tmp_path):
    """A typo in the env var must not leave the picker dead."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(tmp_path / "gone"))
    assert fs_browse.roots() == [tmp_path.resolve()]


def test_roots_is_read_at_call_time(monkeypatch, tmp_path):
    """Not captured at import: changing the env var changes the next call."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(a))
    assert fs_browse.roots() == [a.resolve()]
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(b))
    assert fs_browse.roots() == [b.resolve()]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_fs_browse_roots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.fs_browse'`

- [ ] **Step 3: Write the module**

Create `src/coscience/fs_browse.py`:

```python
"""Browse the server's filesystem so the dashboard can pick a program's project
folder (`Program.workdir`).

Standalone by design: no Service/Substrate dependency, and not scoped to a
program — the create-program flow picks a folder before a program exists.

Browsing is confined to configured roots because the API is reachable on the LAN
and auth is inactive until `.coscience/users.yaml` exists.
"""
from __future__ import annotations

import os
from pathlib import Path


class BrowseError(Exception):
    """Base for browse failures the HTTP layer maps to status codes."""


class OutsideRoots(BrowseError):
    """Path resolved outside every configured root."""


class NotFound(BrowseError):
    """Path is missing, or exists but is not a directory."""


class InvalidName(BrowseError):
    """Proposed folder name is not a single usable path segment."""


class AlreadyExists(BrowseError):
    """A folder of that name is already there."""


def roots() -> list[Path]:
    """The directories browsing is confined to, from COSCIENCE_BROWSE_ROOTS
    (colon-separated, `~` expanded); defaults to the server user's home.

    Read at call time, never captured at import: tests monkeypatch the env var
    against a tmp_path. Entries that don't exist are dropped, and an empty
    result falls back to home so a typo can't leave the picker dead.
    """
    raw = os.environ.get("COSCIENCE_BROWSE_ROOTS", "")
    candidates = [Path(os.path.expanduser(p.strip())).resolve()
                  for p in raw.split(":") if p.strip()]
    if not candidates:
        candidates = [Path.home().resolve()]
    found = [p for p in candidates if p.is_dir()]
    return found or [Path.home().resolve()]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_fs_browse_roots.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Prepare the commit (await approval before running)**

```bash
git add src/coscience/fs_browse.py tests/test_fs_browse_roots.py
git commit -m "feat(fs-browse): configurable browse roots, default \$HOME"
```

---

### Task 2: Directory listing with confinement

**Files:**
- Modify: `src/coscience/fs_browse.py`
- Test: `tests/test_fs_browse_list.py`

**Interfaces:**
- Consumes: `roots()`, `OutsideRoots`, `NotFound` from Task 1.
- Produces: `list_dirs(path: str | None = None) -> dict` returning
  `{"path": str | None, "parent": str | None, "roots": [{"label": str, "path": str}], "entries": [{"name": str, "path": str}]}`.
  Task 3 reuses the private helper `_checked(path: str) -> Path`; Task 4 wraps `list_dirs`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_browse_list.py`:

```python
"""Listing directories: confinement, filtering, and the root level."""
import pytest

from coscience import fs_browse


@pytest.fixture
def root(monkeypatch, tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(r))
    return r


def test_lists_only_directories_sorted(root):
    (root / "beta").mkdir()
    (root / "alpha").mkdir()
    (root / "a-file.txt").write_text("x")
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["alpha", "beta"]
    assert out["entries"][0]["path"] == str(root / "alpha")


def test_hidden_directories_are_skipped(root):
    (root / ".git").mkdir()
    (root / "visible").mkdir()
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["visible"]


def test_parent_is_null_at_a_root(root):
    assert fs_browse.list_dirs(str(root))["parent"] is None


def test_parent_is_set_below_a_root(root):
    (root / "child").mkdir()
    out = fs_browse.list_dirs(str(root / "child"))
    assert out["parent"] == str(root)


def test_dotdot_escape_is_rejected(root):
    with pytest.raises(fs_browse.OutsideRoots):
        fs_browse.list_dirs(str(root / ".." / ".."))


def test_symlink_pointing_outside_a_root_is_rejected(root, tmp_path):
    """resolve() follows the link BEFORE the confinement check."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    with pytest.raises(fs_browse.OutsideRoots):
        fs_browse.list_dirs(str(root / "escape"))


def test_symlink_pointing_outside_a_root_is_not_listed(root, tmp_path):
    """An entry you are forbidden to open must not be offered."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    (root / "ok").mkdir()
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["ok"]


def test_missing_path_raises_not_found(root):
    with pytest.raises(fs_browse.NotFound):
        fs_browse.list_dirs(str(root / "nope"))


def test_file_path_raises_not_found(root):
    f = root / "f.txt"
    f.write_text("x")
    with pytest.raises(fs_browse.NotFound):
        fs_browse.list_dirs(str(f))


def test_broken_entry_is_skipped_not_fatal(root):
    """A dangling symlink must not break the whole listing."""
    (root / "dangling").symlink_to(root / "does-not-exist")
    (root / "ok").mkdir()
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["ok"]


def test_none_with_one_root_lists_that_root(root):
    (root / "child").mkdir()
    out = fs_browse.list_dirs(None)
    assert out["path"] == str(root)
    assert [e["name"] for e in out["entries"]] == ["child"]


def test_none_with_two_roots_lists_the_roots(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", f"{a}:{b}")
    out = fs_browse.list_dirs(None)
    assert out["path"] is None
    assert out["parent"] is None
    assert [e["path"] for e in out["entries"]] == [str(a), str(b)]


def test_home_root_is_labelled_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(tmp_path))
    assert fs_browse.list_dirs(None)["roots"] == [{"label": "~", "path": str(tmp_path)}]


def test_non_home_root_is_labelled_with_its_path(root):
    assert fs_browse.list_dirs(None)["roots"] == [{"label": str(root), "path": str(root)}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_fs_browse_list.py -v`
Expected: FAIL — `AttributeError: module 'coscience.fs_browse' has no attribute 'list_dirs'`

- [ ] **Step 3: Implement listing**

Append to `src/coscience/fs_browse.py`:

```python
def _label(p: Path) -> str:
    return "~" if p == Path.home().resolve() else str(p)


def _inside(p: Path, rs: list[Path]) -> bool:
    return any(p.is_relative_to(r) for r in rs)


def _checked(path: str) -> Path:
    """Resolve `path` and confine it to the configured roots.

    `resolve()` follows symlinks BEFORE the check, so a link pointing outside
    the roots is rejected rather than followed.
    """
    p = Path(os.path.expanduser(str(path))).resolve()
    if not _inside(p, roots()):
        raise OutsideRoots(str(p))
    return p


def list_dirs(path: str | None = None) -> dict:
    """Subdirectories of `path`.

    With `path` None: the single configured root's contents, or — when several
    roots are configured — a virtual root level whose entries are the roots.
    """
    rs = roots()
    root_rows = [{"label": _label(r), "path": str(r)} for r in rs]
    if path is None or not str(path).strip():
        if len(rs) > 1:
            return {"path": None, "parent": None, "roots": root_rows,
                    "entries": [{"name": _label(r), "path": str(r)} for r in rs]}
        path = str(rs[0])

    p = _checked(path)
    if not p.is_dir():
        raise NotFound(str(p))

    entries = []
    for child in sorted(p.iterdir(), key=lambda c: c.name):
        if child.name.startswith("."):
            continue
        try:
            if child.is_dir() and _inside(child.resolve(), rs):
                entries.append({"name": child.name, "path": str(child)})
        except OSError:
            continue                     # unreadable/dangling: skip, don't fail the listing

    parent = None if any(p == r for r in rs) else str(p.parent)
    return {"path": str(p), "parent": parent, "roots": root_rows, "entries": entries}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_fs_browse_list.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Prepare the commit (await approval before running)**

```bash
git add src/coscience/fs_browse.py tests/test_fs_browse_list.py
git commit -m "feat(fs-browse): list directories, confined to the configured roots"
```

---

### Task 3: Creating a folder

**Files:**
- Modify: `src/coscience/fs_browse.py`
- Test: `tests/test_fs_browse_mkdir.py`

**Interfaces:**
- Consumes: `_checked`, `roots`, `InvalidName`, `NotFound`, `AlreadyExists`, `OutsideRoots` from Tasks 1–2.
- Produces: `make_dir(parent: str, name: str) -> dict` returning `{"path": str}`. Task 4 wraps it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_browse_mkdir.py`:

```python
"""Creating a folder from the picker: one segment, inside the roots, no clobber."""
import pytest

from coscience import fs_browse


@pytest.fixture
def root(monkeypatch, tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(r))
    return r


def test_creates_the_directory(root):
    out = fs_browse.make_dir(str(root), "lf-work")
    assert out == {"path": str(root / "lf-work")}
    assert (root / "lf-work").is_dir()


def test_strips_surrounding_whitespace(root):
    out = fs_browse.make_dir(str(root), "  lf-work  ")
    assert out == {"path": str(root / "lf-work")}


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "a/b", "a\x00b"])
def test_rejects_invalid_names(root, name):
    with pytest.raises(fs_browse.InvalidName):
        fs_browse.make_dir(str(root), name)


def test_existing_folder_conflicts(root):
    (root / "taken").mkdir()
    with pytest.raises(fs_browse.AlreadyExists):
        fs_browse.make_dir(str(root), "taken")


def test_parent_outside_the_roots_is_rejected(root, tmp_path):
    with pytest.raises(fs_browse.OutsideRoots):
        fs_browse.make_dir(str(tmp_path), "sneaky")


def test_missing_parent_raises_not_found(root):
    with pytest.raises(fs_browse.NotFound):
        fs_browse.make_dir(str(root / "nope"), "child")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_fs_browse_mkdir.py -v`
Expected: FAIL — `AttributeError: module 'coscience.fs_browse' has no attribute 'make_dir'`

- [ ] **Step 3: Implement `make_dir`**

Append to `src/coscience/fs_browse.py`:

```python
def make_dir(parent: str, name: str) -> dict:
    """Create one directory under `parent`.

    `name` must be a single path segment, and the result must still fall inside
    the configured roots.
    """
    clean = str(name or "").strip()
    if clean in ("", ".", "..") or "/" in clean or "\0" in clean:
        raise InvalidName(str(name))

    base = _checked(parent)
    if not base.is_dir():
        raise NotFound(str(base))

    target = _checked(str(base / clean))
    if target.exists():
        raise AlreadyExists(str(target))
    target.mkdir()
    return {"path": str(target)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_fs_browse_mkdir.py -v`
Expected: PASS (11 tests, counting the 6 parametrised cases)

- [ ] **Step 5: Prepare the commit (await approval before running)**

```bash
git add src/coscience/fs_browse.py tests/test_fs_browse_mkdir.py
git commit -m "feat(fs-browse): create a folder inside the browse roots"
```

---

### Task 4: HTTP endpoints

**Files:**
- Modify: `src/coscience/http_api.py` (import at line 23; request model near `ProgramWorkdirIn` ~line 100; routes after the `@api.get("/usage")` block ~line 606)
- Test: `tests/test_http_fs_browse.py`

**Interfaces:**
- Consumes: `fs_browse.list_dirs`, `fs_browse.make_dir` and the exception classes.
- Produces: `GET /api/fs/dirs?path=…` → 200 listing; `POST /api/fs/dirs {parent, name}` → 201 `{"path": …}`. Task 5's `api.ts` calls both.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_http_fs_browse.py`:

```python
"""HTTP surface for directory browsing: status-code mapping."""
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.service import Service


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(root))
    c = TestClient(build_app(Service(tmp_path)))   # same shape as tests/test_http_api.py
    c.root = root
    return c


def test_list_returns_subdirectories(client):
    (client.root / "child").mkdir()
    r = client.get("/api/fs/dirs", params={"path": str(client.root)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(client.root)
    assert body["parent"] is None
    assert body["roots"] == [{"label": str(client.root), "path": str(client.root)}]
    assert body["entries"] == [{"name": "child", "path": str(client.root / "child")}]


def test_list_without_path_opens_at_the_root(client):
    r = client.get("/api/fs/dirs")
    assert r.status_code == 200
    assert r.json()["path"] == str(client.root)


def test_list_outside_the_roots_is_403(client, tmp_path):
    r = client.get("/api/fs/dirs", params={"path": str(tmp_path)})
    assert r.status_code == 403


def test_list_missing_path_is_404(client):
    r = client.get("/api/fs/dirs", params={"path": str(client.root / "nope")})
    assert r.status_code == 404


def test_create_returns_201_and_the_path(client):
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "made"})
    assert r.status_code == 201
    assert r.json() == {"path": str(client.root / "made")}
    assert (client.root / "made").is_dir()


def test_create_invalid_name_is_400(client):
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "a/b"})
    assert r.status_code == 400


def test_create_duplicate_is_409(client):
    (client.root / "taken").mkdir()
    r = client.post("/api/fs/dirs", json={"parent": str(client.root), "name": "taken"})
    assert r.status_code == 409


def test_create_outside_the_roots_is_403(client, tmp_path):
    r = client.post("/api/fs/dirs", json={"parent": str(tmp_path), "name": "sneaky"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_fs_browse.py -v`
Expected: FAIL — 404 on every request (routes not registered)

- [ ] **Step 3: Add the import, the request model, and the routes**

In `src/coscience/http_api.py`, change line 23 from:

```python
from coscience import auth
```

to:

```python
from coscience import auth, fs_browse
```

Add this request model immediately after the existing `ProgramWorkdirIn` class:

```python
class DirCreateIn(BaseModel):
    parent: str
    name: str
```

Add these routes immediately after the existing `@api.get("/usage")` block:

```python
    @api.get("/fs/dirs")
    def browse_dirs(path: str | None = Query(default=None)) -> dict:
        try:
            return fs_browse.list_dirs(path)
        except fs_browse.OutsideRoots as exc:
            raise HTTPException(status_code=403, detail=f"outside the allowed roots: {exc}")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=f"permission denied: {exc}")
        except fs_browse.NotFound as exc:
            raise HTTPException(status_code=404, detail=f"not a directory: {exc}")

    @api.post("/fs/dirs", status_code=201)
    def create_dir(body: DirCreateIn) -> dict:
        try:
            return fs_browse.make_dir(body.parent, body.name)
        except fs_browse.InvalidName as exc:
            raise HTTPException(status_code=400, detail=f"invalid folder name: {exc}")
        except fs_browse.AlreadyExists as exc:
            raise HTTPException(status_code=409, detail=f"already exists: {exc}")
        except fs_browse.OutsideRoots as exc:
            raise HTTPException(status_code=403, detail=f"outside the allowed roots: {exc}")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=f"permission denied: {exc}")
        except fs_browse.NotFound as exc:
            raise HTTPException(status_code=404, detail=f"not a directory: {exc}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest tests/test_http_fs_browse.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the whole backend suite**

Run: `/home/oleg/venvs/coscience/bin/python -m pytest -q`
Expected: exit 0, no failures (the suite was 643 passing before this plan; expect 643 + the new tests)

- [ ] **Step 6: Prepare the commit (await approval before running)**

```bash
git add src/coscience/http_api.py tests/test_http_fs_browse.py
git commit -m "feat(fs-browse): GET/POST /api/fs/dirs on the auth-gated router"
```

---

### Task 5: `DirectoryPickerModal` component

**Files:**
- Modify: `frontend/src/api.ts` (types near the other `export interface` block; methods next to `setProgramWorkdir`)
- Create: `frontend/src/components/DirectoryPickerModal.tsx`
- Test: `frontend/src/components/DirectoryPickerModal.test.tsx`

**Interfaces:**
- Consumes: `GET/POST /api/fs/dirs` from Task 4.
- Produces: `api.listDirs(path?: string | null) => Promise<DirListing>`, `api.createDir(parent: string, name: string) => Promise<{path: string}>`, and the default export `DirectoryPickerModal` with props `{ opened: boolean; initialPath?: string; onClose: () => void; onPick: (path: string) => void }`. Task 6 renders it.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/DirectoryPickerModal.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: { listDirs: vi.fn(), createDir: vi.fn() },
}));

import { api } from "../api";
import DirectoryPickerModal from "./DirectoryPickerModal";

// jsdom has no matchMedia; MantineProvider's color-scheme effect needs it.
beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const listing = (path: string, entries: string[], parent: string | null = null) => ({
  path,
  parent,
  roots: [{ label: "~", path: "/home/oleg" }],
  entries: entries.map((name) => ({ name, path: `${path}/${name}` })),
});

beforeEach(() => {
  vi.mocked(api.listDirs).mockReset();
  vi.mocked(api.createDir).mockReset();
  vi.mocked(api.listDirs).mockImplementation(async (p?: string | null) =>
    p === "/home/oleg/sync"
      ? listing("/home/oleg/sync", ["bmt-share"], "/home/oleg")
      : listing("/home/oleg", ["sync"]));
});

function renderPicker(onPick = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <DirectoryPickerModal opened initialPath="/home/oleg" onClose={() => {}} onPick={onPick} />
      </QueryClientProvider>
    </MantineProvider>,
  );
  return onPick;
}

describe("DirectoryPickerModal", () => {
  it("lists the folders returned for the current path", async () => {
    renderPicker();
    await waitFor(() => expect(screen.getByRole("button", { name: /sync/ })).toBeTruthy());
  });

  it("descends into a folder when it is clicked", async () => {
    renderPicker();
    fireEvent.click(await screen.findByRole("button", { name: /sync/ }));
    await waitFor(() => expect(api.listDirs).toHaveBeenCalledWith("/home/oleg/sync"));
    await waitFor(() => expect(screen.getByRole("button", { name: /bmt-share/ })).toBeTruthy());
  });

  it("calls onPick with the current path when confirmed", async () => {
    const onPick = renderPicker();
    await screen.findByRole("button", { name: /sync/ });
    fireEvent.click(screen.getByRole("button", { name: /use this folder/i }));
    expect(onPick).toHaveBeenCalledWith("/home/oleg");
  });

  it("creates a folder under the current path", async () => {
    vi.mocked(api.createDir).mockResolvedValue({ path: "/home/oleg/fresh" });
    renderPicker();
    await screen.findByRole("button", { name: /sync/ });
    fireEvent.click(screen.getByRole("button", { name: /new folder/i }));
    fireEvent.change(screen.getByLabelText("new folder name"), { target: { value: "fresh" } });
    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await waitFor(() => expect(api.createDir).toHaveBeenCalledWith("/home/oleg", "fresh"));
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/DirectoryPickerModal.test.tsx`
Expected: FAIL — cannot resolve `./DirectoryPickerModal`

- [ ] **Step 3: Add the API wrappers**

In `frontend/src/api.ts`, add these types alongside the other exported interfaces:

```ts
export interface DirEntry { name: string; path: string }
export interface DirRoot { label: string; path: string }
export interface DirListing {
  path: string | null; parent: string | null; roots: DirRoot[]; entries: DirEntry[];
}
```

and these two methods immediately after `setProgramWorkdir`:

```ts
  listDirs: (path?: string | null) =>
    fetch(`/api/fs/dirs${path ? `?path=${encodeURIComponent(path)}` : ""}`).then(j<DirListing>),
  createDir: (parent: string, name: string) =>
    fetch("/api/fs/dirs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent, name }),
    }).then(j<{ path: string }>),
```

- [ ] **Step 4: Write the component**

Create `frontend/src/components/DirectoryPickerModal.tsx`:

```tsx
import { ActionIcon, Button, Group, Modal, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";

interface Props {
  opened: boolean;
  initialPath?: string;
  onClose: () => void;
  onPick: (path: string) => void;
}

/** Browse the SERVER's filesystem to choose a folder. Confined server-side to
 *  the configured roots; `null` path means "the root level". */
export default function DirectoryPickerModal({ opened, initialPath, onClose, onPick }: Props) {
  const qc = useQueryClient();
  const [path, setPath] = useState<string | null>(initialPath?.trim() || null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (opened) { setPath(initialPath?.trim() || null); setCreating(false); setError(""); }
  }, [opened, initialPath]);

  const { data, error: qErr } = useQuery({
    queryKey: ["fs-dirs", path],
    queryFn: () => api.listDirs(path),
    enabled: opened,
    retry: false,
  });

  // A saved-but-unusable initialPath (typo, deleted, outside the roots) drops
  // the user at the root level instead of a dead modal.
  useEffect(() => { if (qErr && path !== null) setPath(null); }, [qErr, path]);

  const go = (p: string | null) => { setError(""); setCreating(false); setNewName(""); setPath(p); };

  const root = data?.roots.find((r) => data.path === r.path || data.path?.startsWith(`${r.path}/`));
  const rest = root && data?.path ? data.path.slice(root.path.length).split("/").filter(Boolean) : [];
  const canUp = !!data && (data.parent !== null || (data.path !== null && data.roots.length > 1));

  const create = async () => {
    setError("");
    if (!data?.path) { setError("Choose a folder first."); return; }
    try {
      await api.createDir(data.path, newName.trim());
      setCreating(false);
      setNewName("");
      qc.invalidateQueries({ queryKey: ["fs-dirs", path] });
    } catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Choose project folder" size="lg">
      <Stack gap="sm">
        <Group gap={4} wrap="wrap" align="center">
          {data && data.roots.length > 1 && (
            <button type="button" className="linklike" onClick={() => go(null)}>roots</button>
          )}
          {root && <button type="button" className="linklike" onClick={() => go(root.path)}>{root.label}</button>}
          {rest.map((seg, i) => (
            <span key={`${seg}-${i}`}>
              <span style={{ color: "var(--ink-faint)" }}> / </span>
              <button type="button" className="linklike"
                      onClick={() => go(`${root!.path}/${rest.slice(0, i + 1).join("/")}`)}>{seg}</button>
            </span>
          ))}
          <ActionIcon variant="subtle" size="sm" aria-label="up one folder"
                      disabled={!canUp} onClick={() => go(data?.parent ?? null)}>↑</ActionIcon>
        </Group>

        <ScrollArea.Autosize mah={320} type="auto">
          <Stack gap={2}>
            {data?.entries.length === 0 && <Text size="sm" c="dimmed">No subfolders here.</Text>}
            {data?.entries.map((e) => (
              <button key={e.path} type="button" className="linklike" style={{ textAlign: "left" }}
                      onClick={() => go(e.path)}>📁 {e.name}</button>
            ))}
          </Stack>
        </ScrollArea.Autosize>

        {creating ? (
          <Group gap={6}>
            <TextInput size="xs" aria-label="new folder name" placeholder="new folder name"
                       value={newName} autoFocus
                       onChange={(ev) => setNewName(ev.currentTarget.value)}
                       onKeyDown={(ev) => { if (ev.key === "Enter") void create(); }} />
            <Button size="xs" onClick={create}>Create</Button>
            <Button size="xs" variant="default"
                    onClick={() => { setCreating(false); setNewName(""); }}>Cancel</Button>
          </Group>
        ) : (
          <button type="button" className="linklike" style={{ textAlign: "left" }}
                  disabled={!data?.path} onClick={() => setCreating(true)}>＋ New folder</button>
        )}

        {error && <Text size="sm" c="red">{error}</Text>}

        <Text size="xs" className="mono" c="dimmed">{data?.path ?? "choose a root"}</Text>

        <Group justify="flex-end" gap={8}>
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button disabled={!data?.path}
                  onClick={() => { if (data?.path) { onPick(data.path); onClose(); } }}>
            Use this folder
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/DirectoryPickerModal.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 6: Prepare the commit (await approval before running)**

```bash
git add frontend/src/api.ts frontend/src/components/DirectoryPickerModal.tsx \
        frontend/src/components/DirectoryPickerModal.test.tsx
git commit -m "feat(fs-browse): DirectoryPickerModal for choosing a folder"
```

---

### Task 6: Wire the picker into both call sites

**Files:**
- Modify: `frontend/src/views/ProgramDetail.tsx` (the workdir `TextInput`, ~line 146)
- Modify: `frontend/src/components/NewProgramModal.tsx` (the Workdir field, line 47)
- Modify: `frontend/src/components/NewProgramModal.test.tsx` (mock gains `listDirs`; one new test)

**Interfaces:**
- Consumes: `DirectoryPickerModal` from Task 5, and the existing `saveWorkdir` in `ProgramDetail`.
- Produces: nothing downstream — this is the final task.

- [ ] **Step 1: Add the in-field trigger to `ProgramDetail`**

Add the import alongside the other component imports:

```tsx
import DirectoryPickerModal from "../components/DirectoryPickerModal";
```

Add the state next to the existing view state:

```tsx
  const [browsing, setBrowsing] = useState(false);
```

Replace the workdir `TextInput` block with:

```tsx
          <TextInput
            key={p.workdir}
            size="xs"
            className="mono"
            defaultValue={p.workdir}
            placeholder="control repo — set a path to run this program's agents there"
            style={{ minWidth: 380, flex: 1, maxWidth: 560 }}
            rightSectionPointerEvents="all"
            rightSection={
              <Tooltip label="Browse folders on the server" withArrow>
                <ActionIcon variant="subtle" size="sm" aria-label="browse folders"
                            onClick={() => setBrowsing(true)}>📁</ActionIcon>
              </Tooltip>
            }
            onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); }}
            onBlur={(e) => { if (e.currentTarget.value.trim() !== p.workdir) saveWorkdir(e.currentTarget.value); }}
          />
          <DirectoryPickerModal
            opened={browsing}
            initialPath={p.workdir}
            onClose={() => setBrowsing(false)}
            onPick={(picked) => saveWorkdir(picked)}
          />
```

`ActionIcon` and `Tooltip` are already imported in this file. Picking reuses the existing `saveWorkdir`, so the teal/yellow notifications and the `key`-driven remount of the field come for free.

> **Mantine 7 gotcha:** input sections are `pointer-events: none` by default. Without `rightSectionPointerEvents="all"` the icon renders but cannot be clicked. There is no prior `rightSection` use in this codebase, so this establishes the pattern.

- [ ] **Step 2: Add the in-field trigger to `NewProgramModal`**

Change the imports on line 1 to include `ActionIcon` and `Tooltip`:

```tsx
import { ActionIcon, Button, Modal, Stack, Textarea, TextInput, Tooltip } from "@mantine/core";
```

Add the import and state:

```tsx
import DirectoryPickerModal from "./DirectoryPickerModal";
```

```tsx
  const [browsing, setBrowsing] = useState(false);
```

Replace the Workdir `TextInput` (line 47) with:

```tsx
          <>
            <TextInput label="Workdir (optional)" value={workdir}
                       rightSectionPointerEvents="all"
                       rightSection={
                         <Tooltip label="Browse folders on the server" withArrow>
                           <ActionIcon variant="subtle" size="sm" aria-label="browse folders"
                                       onClick={() => setBrowsing(true)}>📁</ActionIcon>
                         </Tooltip>
                       }
                       onChange={(e) => setWorkdir(e.currentTarget.value)} />
            <DirectoryPickerModal opened={browsing} initialPath={workdir}
                                  onClose={() => setBrowsing(false)} onPick={setWorkdir} />
          </>
```

- [ ] **Step 3: Extend the `NewProgramModal` test**

In `frontend/src/components/NewProgramModal.test.tsx`, change the api mock to:

```tsx
vi.mock("../api", () => ({
  api: {
    createProgram: vi.fn().mockResolvedValue({ id: "p7" }),
    listDirs: vi.fn().mockResolvedValue({ path: null, parent: null, roots: [], entries: [] }),
    createDir: vi.fn(),
  },
}));
```

and add this test inside the existing `describe` block:

```tsx
  it("offers an in-field browse control under advanced", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /\+ advanced/i }));
    await waitFor(() => expect(screen.getByLabelText("browse folders")).toBeTruthy());
  });
```

- [ ] **Step 4: Run the frontend suite**

Run (from `frontend/`): `npx vitest run`
Expected: PASS — 10 test files, 42 tests (37 before this plan, +4 from Task 5, +1 here)

- [ ] **Step 5: Type-check and build**

Run (from `frontend/`): `npm run build`
Expected: exit 0, `✓ built in …` with no TypeScript errors

- [ ] **Step 6: Manual check against the running instance**

The backend is editable-installed, so restart it to load `fs_browse`, then hard-reload the dashboard:

```bash
pgrep -f "coscience-h[t]tp" | xargs -r kill
# relaunch as the session normally does, with COSCIENCE_REPO=~/sync/bmt-share/coscience
```

Verify: the folder icon sits inside the project-folder field on a program page; clicking it opens the modal at `~`; descending, `＋ New folder`, and `Use this folder` all work; the picked path saves and the existing notification fires.

- [ ] **Step 7: Prepare the commit (await approval before running)**

```bash
git add frontend/src/views/ProgramDetail.tsx frontend/src/components/NewProgramModal.tsx \
        frontend/src/components/NewProgramModal.test.tsx
git commit -m "feat(fs-browse): in-field browse trigger on both workdir fields"
```

---

## Notes for the implementer

- **Not in scope:** remote hosts. The spec designs the seam (`DirectorySource` protocol, `roots` as a list, an optional `host` param) but this plan builds local-only browsing. Do not add SSH, and do not change `Program.workdir` into a host-qualified value — that ripples into `worker._agent_cwd`, `pm_agent._resolve_workdir`, `chat_agent.resolve_workdir` and the agent launcher, and is a separate project.
- **Also not in scope:** listing files, and a "show hidden" toggle. Both were deliberately excluded as YAGNI for choosing a `workdir`.
- The `DirectorySource` protocol named in the spec is not needed by any behaviour this plan implements. Add it only if Task 2 or 3 becomes awkward without it; a premature protocol with one implementation is noise.
