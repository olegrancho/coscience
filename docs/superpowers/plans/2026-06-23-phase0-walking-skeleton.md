# Co-Science Platform — Phase 0 (Walking Skeleton) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single Worker, driven by a home-grown heartbeat, picks up an approved sprint from a git-backed OKF substrate, executes its steps one per beat, checkpoints to `progress.md`, **survives being killed and resumes without redoing completed work**, handles a detached long-running job across beats, and writes an OKF result when done.

**Architecture:** Filesystem-only (no coordination service, scheduler, dashboard, or sandbox yet — those are Phases 1–3). The substrate is a directory of OKF markdown files in a git repo. A Python package `coscience` provides typed read/write of that substrate plus a `Worker.run_one_beat()` unit of work. Work is performed by a pluggable `StepExecutor`; Phase 0 ships a `ShellStepExecutor` (deterministic, testable) and a `ClaudeCodeExecutor` (launches a real headless Claude Code session). The heartbeat is a thin CLI loop that calls `run_one_beat()` repeatedly. All durable state lives on disk, so a fresh process re-hydrates from `progress.md` and continues.

**Tech Stack:** Python ≥3.11, PyYAML (frontmatter), pytest (TDD), git (durable history). No web framework, no database, no async — deliberately minimal.

## Global Constraints

- **Python ≥ 3.11** (uses `X | None` and `list[T]` builtins; `StrEnum` via `enum`).
- **Dependencies:** runtime → `pyyaml` only. Dev → `pytest` only. Do not add others.
- **Substrate format = OKF:** one concept per markdown file, YAML frontmatter + markdown body. Frontmatter keys are `snake_case`.
- **All durable state on disk** under the substrate repo root. No state in process memory between beats. A beat must be safe to kill at any point.
- **One beat = bounded work** = at most one sprint step executed (or one poll of a detached job). Never loop over all steps inside a single beat.
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit. Frequent commits — one per task minimum.
- **The substrate repo and the platform-code repo are two different git repos.** Platform code lives in the project root; the substrate is a separate directory/repo addressed by `repo_root`.

## Phase Roadmap (context — only Phase 0 is built by this plan)

| Phase | Delivers | Status |
|---|---|---|
| **0 — Walking skeleton** (this plan) | Worker + OKF substrate + heartbeat + checkpoint/resume + detached-job re-attach | **planned here** |
| 1 — Coordination service + scheduler | task queue, resource ledger, GPU/disk leases, MCP/API | later |
| 2 — PM + OC dashboard loop | program-manager agent, internal dashboard, ideas/decision flow | later |
| 3 — Scout + screening + sandbox hardening | capability enforcement, quarantine pipeline | later |

Design source: `docs/superpowers/specs/2026-06-23-co-science-platform-design.md`. Phase 0 implements §3 (substrate/data model — minimal subset), §4 (agent runtime: heartbeat, checkpoint/resume, detached jobs), and the autonomy primitive of "bounded beat." It explicitly does **not** implement §5 (sandbox), §6 (scheduler), §7 (dashboard), or search.

---

## File Structure

**Platform code (project root, its own git repo):**

```
pyproject.toml                 # project metadata, deps, pytest config
src/coscience/
  __init__.py
  frontmatter_io.py            # parse/serialize markdown+YAML frontmatter
  models.py                    # Step, StepResult, SprintStatus, Sprint, Result, ProgressState, BeatOutcome
  substrate.py                 # Substrate: load/save sprint, progress, result; git commit
  executor.py                  # StepExecutor protocol, ShellStepExecutor
  worker.py                    # Worker.run_one_beat()
  claude_executor.py           # ClaudeCodeExecutor (real headless agent)
  cli.py                       # `coscience worker --once | --loop`
tests/
  conftest.py                  # temp-substrate fixture
  test_frontmatter_io.py
  test_models.py
  test_substrate.py
  test_executor.py
  test_worker.py
  test_resume.py               # headline: kill/resume idempotency
  test_detached.py             # detached long-running job re-attach
  test_cli.py
  test_claude_executor.py
```

**Substrate repo (separate, created by tests as a temp dir; in real runs a git repo):**

```
<repo_root>/
  sprints/<sprint-id>/sprint.md      # frontmatter: status, goals, plan(list of {id,run})
  sprints/<sprint-id>/progress.md    # frontmatter: completed_steps, detached{step_id:pid}
  results/<result-id>.md             # frontmatter: sprint, type; body: summary
```

---

## Task 1: Project scaffold + toolchain

**Files:**
- Create: `pyproject.toml`
- Create: `src/coscience/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_sanity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an installable `coscience` package and a green `pytest` run.

- [ ] **Step 1: Initialize the platform-code git repo**

Run:
```bash
git init
printf '%s\n' '__pycache__/' '*.pyc' '.pytest_cache/' '*.egg-info/' '.venv/' > .gitignore
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "coscience"
version = "0.0.0"
description = "Co-Science Platform — Phase 0 walking skeleton"
requires-python = ">=3.11"
dependencies = ["pyyaml>=6"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
coscience = "coscience.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Create empty package + test package markers**

```bash
mkdir -p src/coscience tests
: > src/coscience/__init__.py
: > tests/__init__.py
```

- [ ] **Step 4: Write a sanity test**

`tests/test_sanity.py`:
```python
import coscience


def test_package_imports():
    assert coscience is not None
```

- [ ] **Step 5: Create venv, install, run the sanity test (expect PASS)**

Run:
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest tests/test_sanity.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: scaffold coscience package and pytest toolchain"
```

---

## Task 2: Frontmatter parse/serialize

**Files:**
- Create: `src/coscience/frontmatter_io.py`
- Test: `tests/test_frontmatter_io.py`

**Interfaces:**
- Consumes: nothing (PyYAML only).
- Produces:
  - `parse(text: str) -> tuple[dict, str]` — returns `(frontmatter_dict, body)`. No frontmatter → `({}, text)`.
  - `serialize(frontmatter: dict, body: str) -> str` — emits `---\n<yaml>\n---\n\n<body>\n`.

- [ ] **Step 1: Write the failing tests**

`tests/test_frontmatter_io.py`:
```python
from coscience.frontmatter_io import parse, serialize


def test_parse_extracts_frontmatter_and_body():
    text = "---\nstatus: approved\ngoals: cure\n---\n\nhello body\n"
    fm, body = parse(text)
    assert fm == {"status": "approved", "goals": "cure"}
    assert body == "hello body\n"


def test_parse_no_frontmatter_returns_empty_dict():
    fm, body = parse("just text\n")
    assert fm == {}
    assert body == "just text\n"


def test_parse_keeps_triple_dash_inside_body():
    text = "---\na: 1\n---\n\nline\n---\nmore\n"
    fm, body = parse(text)
    assert fm == {"a": 1}
    assert "---\nmore" in body


def test_serialize_roundtrips():
    fm = {"status": "approved", "goals": "cure"}
    body = "some notes"
    out = serialize(fm, body)
    fm2, body2 = parse(out)
    assert fm2 == fm
    assert body2.strip() == "some notes"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_frontmatter_io.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.frontmatter_io'`.

- [ ] **Step 3: Implement `frontmatter_io.py`**

```python
"""Read/write markdown documents with a YAML frontmatter block."""
from __future__ import annotations

import yaml

_DELIM = "---"


def parse(text: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body)."""
    if text.startswith(_DELIM):
        parts = text.split(_DELIM, 2)
        # parts == ['', '\n<yaml>\n', '\n<body>'] for a well-formed doc
        if len(parts) == 3:
            frontmatter = yaml.safe_load(parts[1]) or {}
            body = parts[2]
            if body.startswith("\n"):
                body = body[1:]
            return frontmatter, body.lstrip("\n")
    return {}, text


def serialize(frontmatter: dict, body: str) -> str:
    """Emit a markdown doc with a YAML frontmatter block."""
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"{_DELIM}\n{fm}\n{_DELIM}\n\n{body.rstrip()}\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_frontmatter_io.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/frontmatter_io.py tests/test_frontmatter_io.py
git commit -m "feat: frontmatter parse/serialize"
```

---

## Task 3: Domain models

**Files:**
- Create: `src/coscience/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class SprintStatus(StrEnum)`: `PROPOSED, APPROVED, EXECUTING, DONE, CANCELED`.
  - `class BeatOutcome(StrEnum)`: `IDLE, PROGRESSED, COMPLETED`.
  - `@dataclass Step(id: str, run: str)` with `Step.from_dict(d: dict) -> Step`.
  - `@dataclass StepResult(step_id: str, completed: bool, output: str = "")`.
  - `@dataclass Sprint(id: str, status: SprintStatus, goals: str, plan: list[Step], program: str | None = None, results: list[str] = [])`.
  - `@dataclass Result(id: str, sprint: str, summary: str)`.
  - `@dataclass ProgressState(sprint_id: str, completed_steps: list[str] = [], detached: dict[str, int] = {})`.

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:
```python
from coscience.models import (
    BeatOutcome,
    ProgressState,
    Result,
    Sprint,
    SprintStatus,
    Step,
    StepResult,
)


def test_step_from_dict():
    step = Step.from_dict({"id": "s1", "run": "echo hi"})
    assert step == Step(id="s1", run="echo hi")


def test_sprint_defaults():
    sprint = Sprint(
        id="sp1",
        status=SprintStatus.APPROVED,
        goals="cure",
        plan=[Step("s1", "echo hi")],
    )
    assert sprint.program is None
    assert sprint.results == []


def test_progress_defaults_are_independent():
    a = ProgressState(sprint_id="sp1")
    b = ProgressState(sprint_id="sp2")
    a.completed_steps.append("s1")
    assert b.completed_steps == []  # no shared mutable default


def test_status_is_string_valued():
    assert SprintStatus.APPROVED == "approved"
    assert BeatOutcome.COMPLETED == "completed"


def test_stepresult_and_result_construct():
    assert StepResult("s1", True).output == ""
    assert Result("r1", "sp1", "did a thing").summary == "did a thing"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.models'`.

- [ ] **Step 3: Implement `models.py`**

```python
"""Typed domain models for the Phase 0 substrate."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SprintStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    DONE = "done"
    CANCELED = "canceled"


class BeatOutcome(StrEnum):
    IDLE = "idle"
    PROGRESSED = "progressed"
    COMPLETED = "completed"


@dataclass
class Step:
    id: str
    run: str

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(id=str(d["id"]), run=str(d["run"]))


@dataclass
class StepResult:
    step_id: str
    completed: bool
    output: str = ""


@dataclass
class Sprint:
    id: str
    status: SprintStatus
    goals: str
    plan: list[Step]
    program: str | None = None
    results: list[str] = field(default_factory=list)


@dataclass
class Result:
    id: str
    sprint: str
    summary: str


@dataclass
class ProgressState:
    sprint_id: str
    completed_steps: list[str] = field(default_factory=list)
    detached: dict[str, int] = field(default_factory=dict)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/models.py tests/test_models.py
git commit -m "feat: domain models"
```

---

## Task 4: Substrate — load/save sprints

**Files:**
- Create: `src/coscience/substrate.py`
- Create: `tests/conftest.py`
- Test: `tests/test_substrate.py`

**Interfaces:**
- Consumes: `frontmatter_io.parse/serialize`, `models.Sprint/Step/SprintStatus`.
- Produces (on `class Substrate`):
  - `Substrate(repo_root: pathlib.Path)`.
  - `sprint_dir(sprint_id: str) -> Path`.
  - `load_sprint(sprint_id: str) -> Sprint`.
  - `save_sprint(sprint: Sprint) -> None`.
  - `iter_sprints(status: SprintStatus | None = None) -> list[Sprint]` (sorted by id).
- `conftest.py` produces a pytest fixture `substrate(tmp_path) -> Substrate` and a helper `write_sprint(...)`.

- [ ] **Step 1: Write the shared fixture**

`tests/conftest.py`:
```python
import pytest

from coscience.frontmatter_io import serialize
from coscience.substrate import Substrate


@pytest.fixture
def substrate(tmp_path):
    return Substrate(tmp_path)


def write_raw_sprint(repo_root, sprint_id, status, goals, plan, body="notes"):
    """Write a sprint.md directly to disk (bypasses Substrate, for arrange steps)."""
    d = repo_root / "sprints" / sprint_id
    d.mkdir(parents=True, exist_ok=True)
    fm = {"status": status, "goals": goals, "plan": plan}
    (d / "sprint.md").write_text(serialize(fm, body))
```

- [ ] **Step 2: Write the failing tests**

`tests/test_substrate.py`:
```python
from coscience.models import Sprint, SprintStatus, Step
from coscience.substrate import Substrate
from tests.conftest import write_raw_sprint


def test_load_sprint_parses_plan(substrate):
    write_raw_sprint(
        substrate.repo_root, "sp1", "approved", "cure cancer",
        plan=[{"id": "s1", "run": "echo a"}, {"id": "s2", "run": "echo b"}],
    )
    sprint = substrate.load_sprint("sp1")
    assert sprint.id == "sp1"
    assert sprint.status == SprintStatus.APPROVED
    assert sprint.goals == "cure cancer"
    assert sprint.plan == [Step("s1", "echo a"), Step("s2", "echo b")]


def test_save_then_load_roundtrips(substrate):
    sprint = Sprint(
        id="sp2", status=SprintStatus.EXECUTING, goals="g",
        plan=[Step("s1", "echo a")], program="prog1",
    )
    substrate.save_sprint(sprint)
    loaded = substrate.load_sprint("sp2")
    assert loaded == sprint


def test_iter_sprints_filters_by_status(substrate):
    write_raw_sprint(substrate.repo_root, "sp1", "approved", "g", [{"id": "s", "run": "x"}])
    write_raw_sprint(substrate.repo_root, "sp2", "done", "g", [{"id": "s", "run": "x"}])
    write_raw_sprint(substrate.repo_root, "sp3", "approved", "g", [{"id": "s", "run": "x"}])
    approved = substrate.iter_sprints(status=SprintStatus.APPROVED)
    assert [s.id for s in approved] == ["sp1", "sp3"]


def test_iter_sprints_empty_when_no_dir(substrate):
    assert substrate.iter_sprints() == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_substrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.substrate'`.

- [ ] **Step 4: Implement `substrate.py` (sprint methods only for now)**

```python
"""Read/write the OKF substrate (a directory of markdown files)."""
from __future__ import annotations

from pathlib import Path

from coscience.frontmatter_io import parse, serialize
from coscience.models import Sprint, SprintStatus, Step


class Substrate:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    # --- sprints ---
    def sprint_dir(self, sprint_id: str) -> Path:
        return self.repo_root / "sprints" / sprint_id

    def load_sprint(self, sprint_id: str) -> Sprint:
        text = (self.sprint_dir(sprint_id) / "sprint.md").read_text()
        fm, _body = parse(text)
        plan = [Step.from_dict(d) for d in fm.get("plan", [])]
        return Sprint(
            id=sprint_id,
            status=SprintStatus(fm["status"]),
            goals=fm.get("goals", ""),
            plan=plan,
            program=fm.get("program"),
            results=list(fm.get("results", [])),
        )

    def save_sprint(self, sprint: Sprint) -> None:
        fm = {
            "status": str(sprint.status),
            "goals": sprint.goals,
            "plan": [{"id": s.id, "run": s.run} for s in sprint.plan],
        }
        if sprint.program is not None:
            fm["program"] = sprint.program
        if sprint.results:
            fm["results"] = sprint.results
        d = self.sprint_dir(sprint.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "sprint.md").write_text(serialize(fm, f"# Sprint {sprint.id}\n"))

    def iter_sprints(self, status: SprintStatus | None = None) -> list[Sprint]:
        sprints_dir = self.repo_root / "sprints"
        if not sprints_dir.is_dir():
            return []
        out = []
        for d in sorted(sprints_dir.iterdir()):
            if (d / "sprint.md").is_file():
                sprint = self.load_sprint(d.name)
                if status is None or sprint.status == status:
                    out.append(sprint)
        return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_substrate.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/coscience/substrate.py tests/conftest.py tests/test_substrate.py
git commit -m "feat: substrate load/save/iter sprints"
```

---

## Task 5: Substrate — progress, results, git commit

**Files:**
- Modify: `src/coscience/substrate.py`
- Test: `tests/test_substrate.py` (append)

**Interfaces:**
- Consumes: `models.ProgressState`, `models.Result`.
- Produces (added to `Substrate`):
  - `load_progress(sprint_id: str) -> ProgressState` (missing file → empty state for that id).
  - `save_progress(progress: ProgressState) -> None` (writes `sprints/<id>/progress.md`).
  - `save_result(result: Result) -> None` (writes `results/<id>.md` with frontmatter `type: result`, `sprint: <id>`).
  - `commit(message: str) -> None` (best-effort `git -C repo_root add -A && commit`; no-op if not a git repo).

- [ ] **Step 1: Write the failing tests (append to `tests/test_substrate.py`)**

```python
import subprocess

from coscience.models import ProgressState, Result


def test_load_progress_missing_returns_empty(substrate):
    p = substrate.load_progress("sp1")
    assert p == ProgressState(sprint_id="sp1")


def test_save_then_load_progress_roundtrips(substrate):
    p = ProgressState(sprint_id="sp1", completed_steps=["s1"], detached={"s2": 4242})
    substrate.save_progress(p)
    assert substrate.load_progress("sp1") == p


def test_save_result_writes_file(substrate):
    substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    text = (substrate.repo_root / "results" / "r1.md").read_text()
    assert "sprint: sp1" in text
    assert "found X" in text


def test_commit_is_noop_without_git(substrate):
    # repo_root (tmp_path) is not a git repo; must not raise.
    substrate.commit("nothing to see")


def test_commit_records_changes_in_git(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    s = Substrate(tmp_path)
    s.save_result(Result(id="r1", sprint="sp1", summary="x"))
    s.commit("add result")
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "add result" in log
```

Add the import line at the top of the file if not present: `from coscience.substrate import Substrate` is already there.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_substrate.py -v`
Expected: the new tests FAIL with `AttributeError: 'Substrate' object has no attribute 'load_progress'`.

- [ ] **Step 3: Implement the new methods (append to `Substrate` in `substrate.py`)**

Add these imports at the top of `substrate.py`:
```python
import subprocess
from coscience.models import ProgressState, Result
```

Add these methods to the `Substrate` class:
```python
    # --- progress ---
    def _progress_path(self, sprint_id: str) -> Path:
        return self.sprint_dir(sprint_id) / "progress.md"

    def load_progress(self, sprint_id: str) -> ProgressState:
        path = self._progress_path(sprint_id)
        if not path.is_file():
            return ProgressState(sprint_id=sprint_id)
        fm, _ = parse(path.read_text())
        return ProgressState(
            sprint_id=sprint_id,
            completed_steps=list(fm.get("completed_steps", [])),
            detached={str(k): int(v) for k, v in (fm.get("detached") or {}).items()},
        )

    def save_progress(self, progress: ProgressState) -> None:
        fm = {
            "completed_steps": progress.completed_steps,
            "detached": progress.detached,
        }
        d = self.sprint_dir(progress.sprint_id)
        d.mkdir(parents=True, exist_ok=True)
        self._progress_path(progress.sprint_id).write_text(
            serialize(fm, f"# Progress {progress.sprint_id}\n")
        )

    # --- results ---
    def save_result(self, result: Result) -> None:
        fm = {"type": "result", "sprint": result.sprint}
        d = self.repo_root / "results"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{result.id}.md").write_text(serialize(fm, result.summary))

    # --- git ---
    def commit(self, message: str) -> None:
        if not (self.repo_root / ".git").is_dir():
            return
        subprocess.run(["git", "-C", str(self.repo_root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_root), "commit", "-q", "-m", message],
            check=False,  # tolerate "nothing to commit"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_substrate.py -v`
Expected: all passed (8 total in this file).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/substrate.py tests/test_substrate.py
git commit -m "feat: substrate progress, results, git commit"
```

---

## Task 6: Shell step executor

**Files:**
- Create: `src/coscience/executor.py`
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `models.Step`, `models.StepResult`.
- Produces:
  - `class StepExecutor(Protocol)`: `run(self, step: Step) -> StepResult`.
  - `class ShellStepExecutor`: runs `step.run` via `subprocess`, `completed = (returncode == 0)`, `output = stdout + stderr`.

- [ ] **Step 1: Write the failing tests**

`tests/test_executor.py`:
```python
from coscience.executor import ShellStepExecutor
from coscience.models import Step


def test_successful_command_is_completed():
    r = ShellStepExecutor().run(Step("s1", "echo hello"))
    assert r.step_id == "s1"
    assert r.completed is True
    assert "hello" in r.output


def test_failing_command_is_not_completed():
    r = ShellStepExecutor().run(Step("s2", "exit 3"))
    assert r.completed is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_executor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.executor'`.

- [ ] **Step 3: Implement `executor.py`**

```python
"""Step executors: how a sprint step actually gets run."""
from __future__ import annotations

import subprocess
from typing import Protocol

from coscience.models import Step, StepResult


class StepExecutor(Protocol):
    def run(self, step: Step) -> StepResult:
        ...


class ShellStepExecutor:
    """Deterministic executor: runs the step's shell command."""

    def run(self, step: Step) -> StepResult:
        proc = subprocess.run(
            step.run, shell=True, capture_output=True, text=True
        )
        return StepResult(
            step_id=step.id,
            completed=proc.returncode == 0,
            output=(proc.stdout or "") + (proc.stderr or ""),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_executor.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/executor.py tests/test_executor.py
git commit -m "feat: shell step executor"
```

---

## Task 7: Worker — one beat

**Files:**
- Create: `src/coscience/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `Substrate`, `StepExecutor`, `models.*`.
- Produces:
  - `class Worker(substrate: Substrate, executor: StepExecutor)`.
  - `run_one_beat() -> BeatOutcome`. Semantics:
    1. Pick a sprint to work: first `EXECUTING` sprint (resume), else first `APPROVED` sprint (claim → set `EXECUTING`, save). None → `IDLE`.
    2. Load progress. Find the first plan step whose `id` is not in `completed_steps`.
    3. No such step → all done: write `Result(id=f"{sprint.id}-result", sprint=sprint.id, summary=...)`, set sprint `DONE`, save, `commit`, return `COMPLETED`.
    4. Else run exactly that one step via the executor. If `completed`, append its id to `completed_steps`, save progress, `commit`. Return `PROGRESSED`.

- [ ] **Step 1: Write the failing tests**

`tests/test_worker.py`:
```python
from coscience.executor import ShellStepExecutor
from coscience.models import BeatOutcome, Sprint, SprintStatus, Step
from coscience.worker import Worker


def _approved_sprint(sid, steps):
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=steps)


def test_idle_when_no_work(substrate):
    assert Worker(substrate, ShellStepExecutor()).run_one_beat() == BeatOutcome.IDLE


def test_first_beat_claims_approved_and_runs_one_step(substrate, tmp_path):
    marker = tmp_path / "ran.txt"
    substrate.save_sprint(_approved_sprint("sp1", [
        Step("s1", f"echo one >> {marker}"),
        Step("s2", f"echo two >> {marker}"),
    ]))
    outcome = Worker(substrate, ShellStepExecutor()).run_one_beat()
    assert outcome == BeatOutcome.PROGRESSED
    assert substrate.load_sprint("sp1").status == SprintStatus.EXECUTING
    assert substrate.load_progress("sp1").completed_steps == ["s1"]
    assert marker.read_text().count("one") == 1
    assert "two" not in marker.read_text()  # only ONE step per beat


def test_beats_complete_the_sprint_and_write_result(substrate, tmp_path):
    marker = tmp_path / "ran.txt"
    substrate.save_sprint(_approved_sprint("sp1", [
        Step("s1", f"echo one >> {marker}"),
        Step("s2", f"echo two >> {marker}"),
    ]))
    worker = Worker(substrate, ShellStepExecutor())
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED  # s1
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED  # s2
    assert worker.run_one_beat() == BeatOutcome.COMPLETED   # result
    assert substrate.load_sprint("sp1").status == SprintStatus.DONE
    result_text = (substrate.repo_root / "results" / "sp1-result.md").read_text()
    assert "sprint: sp1" in result_text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.worker'`.

- [ ] **Step 3: Implement `worker.py`**

```python
"""The Worker: one bounded unit of work per heartbeat."""
from __future__ import annotations

from coscience.executor import StepExecutor
from coscience.models import BeatOutcome, Result, SprintStatus
from coscience.substrate import Substrate


class Worker:
    def __init__(self, substrate: Substrate, executor: StepExecutor):
        self.substrate = substrate
        self.executor = executor

    def _claim_sprint(self):
        executing = self.substrate.iter_sprints(status=SprintStatus.EXECUTING)
        if executing:
            return executing[0]
        approved = self.substrate.iter_sprints(status=SprintStatus.APPROVED)
        if not approved:
            return None
        sprint = approved[0]
        sprint.status = SprintStatus.EXECUTING
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint.id}: start executing")
        return sprint

    def run_one_beat(self) -> BeatOutcome:
        sprint = self._claim_sprint()
        if sprint is None:
            return BeatOutcome.IDLE

        progress = self.substrate.load_progress(sprint.id)
        next_step = next(
            (s for s in sprint.plan if s.id not in progress.completed_steps), None
        )

        if next_step is None:
            result = Result(
                id=f"{sprint.id}-result",
                sprint=sprint.id,
                summary=f"Sprint {sprint.id} completed {len(sprint.plan)} steps.",
            )
            self.substrate.save_result(result)
            sprint.status = SprintStatus.DONE
            sprint.results = [result.id]
            self.substrate.save_sprint(sprint)
            self.substrate.commit(f"sprint {sprint.id}: done, result {result.id}")
            return BeatOutcome.COMPLETED

        step_result = self.executor.run(next_step)
        if step_result.completed:
            progress.completed_steps.append(next_step.id)
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: step {next_step.id} done")
        return BeatOutcome.PROGRESSED
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_worker.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/worker.py tests/test_worker.py
git commit -m "feat: worker run_one_beat (claim, step, checkpoint, complete)"
```

---

## Task 8: Kill/resume idempotency (headline deliverable)

**Files:**
- Test: `tests/test_resume.py`

**Interfaces:**
- Consumes: `Worker`, `Substrate`, `ShellStepExecutor` (no new production code expected — this proves the durable-state design). If a test fails, fix `worker.py`/`substrate.py`, not the test.

- [ ] **Step 1: Write the proving tests**

`tests/test_resume.py`:
```python
from coscience.executor import ShellStepExecutor
from coscience.models import ProgressState, Sprint, SprintStatus, Step
from coscience.worker import Worker


def _sprint_appending_to(marker, n_steps):
    return Sprint(
        id="sp1",
        status=SprintStatus.APPROVED,
        goals="g",
        plan=[Step(f"s{i}", f"echo s{i} >> {marker}") for i in range(n_steps)],
    )


def test_fresh_worker_resumes_without_redoing_steps(substrate, tmp_path):
    marker = tmp_path / "ran.txt"
    substrate.save_sprint(_sprint_appending_to(marker, 4))

    # First "process": run two beats, then it "dies" (we drop the object).
    Worker(substrate, ShellStepExecutor()).run_one_beat()
    Worker(substrate, ShellStepExecutor()).run_one_beat()
    assert substrate.load_progress("sp1").completed_steps == ["s0", "s1"]

    # Brand-new Worker objects (simulating restarts) finish the job.
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_one_beat()  # s2
    worker.run_one_beat()  # s3
    worker.run_one_beat()  # complete

    assert substrate.load_sprint("sp1").status == SprintStatus.DONE
    # Each step ran EXACTLY once across all restarts — no duplication.
    lines = marker.read_text().split()
    assert lines == ["s0", "s1", "s2", "s3"]


def test_resume_after_already_completed_step_is_recorded(substrate, tmp_path):
    marker = tmp_path / "ran.txt"
    substrate.save_sprint(_sprint_appending_to(marker, 2))
    # Pretend s0 already ran in a previous life: seed progress directly.
    substrate.save_progress(ProgressState(sprint_id="sp1", completed_steps=["s0"]))
    substrate.save_sprint(
        Sprint(id="sp1", status=SprintStatus.EXECUTING, goals="g",
               plan=_sprint_appending_to(marker, 2).plan)
    )

    Worker(substrate, ShellStepExecutor()).run_one_beat()  # must run s1, NOT s0
    assert marker.read_text().split() == ["s1"]
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_resume.py -v`
Expected: 2 passed (the design from Tasks 4–7 should already satisfy this). If they fail, the durable-state/idempotency logic is wrong — fix `worker.py`/`substrate.py` and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/test_resume.py
git commit -m "test: kill/resume idempotency proven (no step re-runs)"
```

---

## Task 9: Detached long-running job re-attach

**Files:**
- Modify: `src/coscience/executor.py`
- Modify: `src/coscience/worker.py`
- Test: `tests/test_detached.py`

**Interfaces:**
- Consumes: `Substrate.load_progress/save_progress` (the `detached` map), `models.Step`.
- Produces:
  - In `executor.py`: helpers `launch_detached(command: str) -> int` (spawns a shell process detached from the parent, returns its PID) and `is_running(pid: int) -> bool` (`os.kill(pid, 0)` probe).
  - In `worker.py`: a step whose `run` begins with the prefix `detached:` is treated as a long-running job — on first encounter the Worker launches it, records `progress.detached[step_id] = pid`, and returns `PROGRESSED` **without** marking the step complete. On later beats, if the step is in `detached` and its PID is still running, the Worker returns `PROGRESSED` (still waiting); once the PID is gone, it marks the step complete and removes it from `detached`.

- [ ] **Step 1: Write the failing tests**

`tests/test_detached.py`:
```python
import time

from coscience.executor import ShellStepExecutor, is_running, launch_detached
from coscience.models import BeatOutcome, Sprint, SprintStatus, Step
from coscience.worker import Worker


def test_launch_detached_and_is_running(tmp_path):
    pid = launch_detached(f"sleep 0.5; echo done > {tmp_path/'d.txt'}")
    assert is_running(pid) is True
    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False
    assert (tmp_path / "d.txt").read_text().strip() == "done"


def test_worker_waits_for_detached_then_completes(substrate, tmp_path):
    out = tmp_path / "out.txt"
    substrate.save_sprint(Sprint(
        id="sp1", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", f"detached: sleep 0.4; echo finished > {out}")],
    ))
    worker = Worker(substrate, ShellStepExecutor())

    # Beat 1: launches the job, records PID, NOT complete.
    assert worker.run_one_beat() == BeatOutcome.PROGRESSED
    prog = substrate.load_progress("sp1")
    assert "s1" in prog.detached
    assert prog.completed_steps == []

    # Re-attach across a simulated restart: keep beating with FRESH workers
    # until the job finishes and the step is marked complete.
    deadline = time.time() + 10
    while substrate.load_sprint("sp1").status != SprintStatus.DONE:
        assert time.time() < deadline, "detached job never completed"
        Worker(substrate, ShellStepExecutor()).run_one_beat()
        time.sleep(0.1)

    assert out.read_text().strip() == "finished"
    assert substrate.load_progress("sp1").completed_steps == ["s1"]
    assert substrate.load_progress("sp1").detached == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_detached.py -v`
Expected: FAIL with `ImportError: cannot import name 'launch_detached'`.

- [ ] **Step 3: Add detached helpers to `executor.py`**

Add these imports at the top of `executor.py`:
```python
import os
```

Append to `executor.py`:
```python
def launch_detached(command: str) -> int:
    """Start a shell command fully detached from this process; return its PID."""
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # survives parent death
    )
    return proc.pid


def is_running(pid: int) -> bool:
    """True if a process with this PID is alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours to signal
    return True
```

- [ ] **Step 4: Teach the Worker about detached steps**

Add this import near the top of `worker.py`:
```python
from coscience.executor import is_running, launch_detached
```

In `worker.py`, replace the step-execution tail of `run_one_beat` (everything from `step_result = self.executor.run(next_step)` to the end) with:
```python
        if next_step.run.startswith("detached:"):
            command = next_step.run[len("detached:"):].strip()
            pid = progress.detached.get(next_step.id)
            if pid is None:
                # First encounter: launch and record the PID.
                progress.detached[next_step.id] = launch_detached(command)
                self.substrate.save_progress(progress)
                self.substrate.commit(f"sprint {sprint.id}: step {next_step.id} launched")
                return BeatOutcome.PROGRESSED
            if is_running(pid):
                return BeatOutcome.PROGRESSED  # still waiting; re-attach on next beat
            # Job finished: mark complete, drop from detached.
            progress.completed_steps.append(next_step.id)
            del progress.detached[next_step.id]
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: detached step {next_step.id} done")
            return BeatOutcome.PROGRESSED

        step_result = self.executor.run(next_step)
        if step_result.completed:
            progress.completed_steps.append(next_step.id)
            self.substrate.save_progress(progress)
            self.substrate.commit(f"sprint {sprint.id}: step {next_step.id} done")
        return BeatOutcome.PROGRESSED
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_detached.py -v`
Expected: 2 passed.

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/coscience/executor.py src/coscience/worker.py tests/test_detached.py
git commit -m "feat: detached long-running job launch and re-attach across beats"
```

---

## Task 10: Heartbeat CLI

**Files:**
- Create: `src/coscience/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Substrate`, `Worker`, `ShellStepExecutor`, `BeatOutcome`.
- Produces:
  - `run_once(repo_root: Path) -> BeatOutcome` — builds a Substrate + Worker(ShellStepExecutor) and runs one beat.
  - `main(argv: list[str] | None = None) -> int` — CLI: `coscience worker --repo <path> [--once | --loop --interval <sec>] [--max-beats N]`. `--once` runs a single beat and prints the outcome. `--loop` repeats with `time.sleep(interval)` between beats (bounded by `--max-beats` when provided, for testability).

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:
```python
from coscience.cli import main, run_once
from coscience.models import BeatOutcome, Sprint, SprintStatus, Step
from coscience.substrate import Substrate


def test_run_once_idle(tmp_path):
    assert run_once(tmp_path) == BeatOutcome.IDLE


def test_main_once_progresses_and_returns_zero(tmp_path, capsys):
    Substrate(tmp_path).save_sprint(Sprint(
        id="sp1", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "true")],
    ))
    code = main(["worker", "--repo", str(tmp_path), "--once"])
    assert code == 0
    assert "progressed" in capsys.readouterr().out.lower()


def test_main_loop_runs_sprint_to_done(tmp_path):
    Substrate(tmp_path).save_sprint(Sprint(
        id="sp1", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "true"), Step("s2", "true")],
    ))
    code = main(["worker", "--repo", str(tmp_path),
                 "--loop", "--interval", "0", "--max-beats", "5"])
    assert code == 0
    assert Substrate(tmp_path).load_sprint("sp1").status == SprintStatus.DONE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.cli'`.

- [ ] **Step 3: Implement `cli.py`**

```python
"""Home-grown heartbeat: a thin CLI loop around Worker.run_one_beat()."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from coscience.executor import ShellStepExecutor
from coscience.models import BeatOutcome
from coscience.substrate import Substrate
from coscience.worker import Worker


def run_once(repo_root: Path) -> BeatOutcome:
    worker = Worker(Substrate(repo_root), ShellStepExecutor())
    return worker.run_one_beat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coscience")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("worker", help="run the heartbeat worker")
    w.add_argument("--repo", required=True, type=Path)
    mode = w.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--max-beats", type=int, default=None)

    args = parser.parse_args(argv)
    if args.command != "worker":
        parser.error("unknown command")

    if args.once or not args.loop:
        outcome = run_once(args.repo)
        print(outcome.value)
        return 0

    beats = 0
    while args.max_beats is None or beats < args.max_beats:
        outcome = run_once(args.repo)
        print(outcome.value, flush=True)
        beats += 1
        if args.max_beats is None or beats < args.max_beats:
            time.sleep(args.interval)
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/cli.py tests/test_cli.py
git commit -m "feat: heartbeat CLI (--once / --loop)"
```

---

## Task 11: ClaudeCodeExecutor (real agent) + manual acceptance

**Files:**
- Create: `src/coscience/claude_executor.py`
- Test: `tests/test_claude_executor.py`
- Create: `docs/superpowers/plans/phase0-manual-acceptance.md`

**Interfaces:**
- Consumes: `models.Step`, `models.StepResult`.
- Produces:
  - `class ClaudeCodeExecutor(claude_bin: str = "claude")` implementing the `StepExecutor` protocol.
  - `build_command(step: Step) -> list[str]` — returns `[claude_bin, "-p", step.run, "--output-format", "text"]`. (Phase 0: the step's `run` text is handed to a headless Claude Code session as the prompt.)
  - `run(step: Step) -> StepResult` — invokes the command, `completed = (returncode == 0)`, `output = stdout`.

This keeps the skeleton honest (it can "walk" with a real agent) while staying unit-testable via a fake `claude` on PATH. Live integration is the manual acceptance below.

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_executor.py`:
```python
import os
import stat

from coscience.claude_executor import ClaudeCodeExecutor
from coscience.models import Step


def test_build_command_uses_prompt_flag():
    cmd = ClaudeCodeExecutor(claude_bin="claude").build_command(Step("s1", "say hi"))
    assert cmd == ["claude", "-p", "say hi", "--output-format", "text"]


def test_run_invokes_fake_claude(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/usr/bin/env bash\necho \"AGENT:$2\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    r = ClaudeCodeExecutor(claude_bin=str(fake)).run(Step("s1", "do research"))
    assert r.completed is True
    assert "AGENT:do research" in r.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_claude_executor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.claude_executor'`.

- [ ] **Step 3: Implement `claude_executor.py`**

```python
"""Executor that delegates a step to a headless Claude Code session."""
from __future__ import annotations

import subprocess

from coscience.models import Step, StepResult


class ClaudeCodeExecutor:
    def __init__(self, claude_bin: str = "claude"):
        self.claude_bin = claude_bin

    def build_command(self, step: Step) -> list[str]:
        return [self.claude_bin, "-p", step.run, "--output-format", "text"]

    def run(self, step: Step) -> StepResult:
        proc = subprocess.run(
            self.build_command(step), capture_output=True, text=True
        )
        return StepResult(
            step_id=step.id,
            completed=proc.returncode == 0,
            output=proc.stdout or "",
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_claude_executor.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Write the manual-acceptance runbook**

`docs/superpowers/plans/phase0-manual-acceptance.md`:
```markdown
# Phase 0 — Manual Acceptance Runbook

Proves the walking skeleton runs end-to-end with a real Claude Code agent and
survives a kill. Run on avatar (or any host with the `claude` CLI logged in).

## Setup
1. Create a substrate repo:
   - `mkdir -p /tmp/coscience-demo && cd /tmp/coscience-demo && git init`
   - `git config user.email demo@local && git config user.name demo`
2. Create `sprints/demo/sprint.md`:
   ```
   ---
   status: approved
   goals: Produce a one-paragraph literature-style note on a trivial topic.
   plan:
     - id: s1
       run: "Write the file note.md in the current directory containing one paragraph about why checkpointing matters. Then print DONE."
   ---
   # Sprint demo
   ```

## Run with the real agent
- From the coscience project: edit `run_once` (or add a flag) to use
  `ClaudeCodeExecutor()` instead of `ShellStepExecutor()`, OR run a short
  Python REPL:
  ```python
  from pathlib import Path
  from coscience.substrate import Substrate
  from coscience.worker import Worker
  from coscience.claude_executor import ClaudeCodeExecutor
  w = Worker(Substrate(Path("/tmp/coscience-demo")), ClaudeCodeExecutor())
  print(w.run_one_beat())  # PROGRESSED
  print(w.run_one_beat())  # COMPLETED
  ```

## Acceptance checks
- [ ] `results/demo-result.md` exists and the sprint status is `done`.
- [ ] `git log` in the substrate shows a commit per checkpoint.
- [ ] **Kill test:** add a second long step (`detached: sleep 60; ...`), run one
      beat, `kill -9` the python process, start a fresh REPL, keep beating —
      confirm the sprint completes and no step ran twice.
```

- [ ] **Step 7: Commit**

```bash
git add src/coscience/claude_executor.py tests/test_claude_executor.py docs/superpowers/plans/phase0-manual-acceptance.md
git commit -m "feat: ClaudeCodeExecutor + Phase 0 manual acceptance runbook"
```

---

## Self-Review

**Spec coverage (Phase 0 subset of the design doc):**
- §3 substrate/OKF (sprints, progress, results, frontmatter) → Tasks 2,3,4,5 ✓
- §4 heartbeat/bounded beat → Tasks 7,10 ✓
- §4 checkpoint + idempotent resume → Tasks 7,8 ✓
- §4 detached long-running jobs + re-attach (the user-flagged "run a pipeline for a week" case) → Task 9 ✓
- §4 agent = Claude Code session (home-grown launcher) → Tasks 10,11 ✓
- git durable history → Task 5 ✓
- Explicitly deferred (not in this plan, by design): §5 sandbox, §6 scheduler/leases/disk, §7 dashboard, search, program manager, resource manager.

**Placeholder scan:** every code step contains complete, runnable code; every test step contains real assertions; no "TBD"/"add error handling"/"similar to Task N". ✓

**Type consistency:** `Step(id, run)`, `StepResult(step_id, completed, output)`, `SprintStatus`, `BeatOutcome`, `Sprint(id, status, goals, plan, program, results)`, `Result(id, sprint, summary)`, `ProgressState(sprint_id, completed_steps, detached)`, `Substrate.{load_sprint,save_sprint,iter_sprints,load_progress,save_progress,save_result,commit}`, `Worker(substrate, executor).run_one_beat()`, `StepExecutor.run`, `launch_detached`/`is_running` — names and signatures are consistent across Tasks 3–11. ✓

**Known Phase 0 simplifications (intentional, revisited in later phases):**
- Checkpoint granularity is one step per beat; `progress.md` is committed per checkpoint (fine at PoC volume).
- A single Worker processes the first eligible sprint; concurrency/assignment is Phase 1.
- The `detached:` prefix is a Phase 0 convention; the scheduler/lease model (Phase 1) replaces ad-hoc PID tracking with proper leases.
