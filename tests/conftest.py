from pathlib import Path

import pytest

import coscience.cli as cli_mod
import coscience.worker as worker_mod
from coscience.frontmatter_io import serialize
from coscience.substrate import Substrate


@pytest.fixture(autouse=True)
def _permissive_usage(monkeypatch):
    """Default the usage gate to 'ok' so worker/dispatcher/cli tests don't shell out
    to the real usage script. Tests that exercise the gate pass usage_gate=... .

    coscience.cli does `from coscience.worker import claude_usage_ok`, which binds
    its own name in the cli module's namespace — patching worker_mod alone leaves
    that binding pointing at the real function, so CLI-driven tests (e.g. the PM
    loop) would still shell out for real. Patch both bindings so isolation actually
    covers every caller, not just the one this module happens to import."""
    monkeypatch.setattr(worker_mod, "claude_usage_ok", lambda *a, **k: True)
    monkeypatch.setattr(cli_mod, "claude_usage_ok", lambda *a, **k: True)


class FakeAgent:
    """Deterministic stand-in for ClaudeAgent in tests — no real claude, no real
    processes. By default an agent 'finishes' immediately: beat 1 launches it,
    beat 2 sees it done and collects. Set linger>0 to keep it 'running' for that
    many is_running polls (for preemption/reconcile tests); stop() ends it.

    A well-behaved worker signals completion by writing finished.json; this stand-in
    writes it on a clean ('ok') launch by default. Pass finished=False to model the
    premature-completion trap (clean exit with no done signal), which drives the
    worker's resume-to-ask path."""

    def __init__(self, result="agent findings", status="ok", linger=0,
                 finished=True, session_id="fake-sess"):
        self.result = result
        self.status = status
        self.linger = linger
        self.finished = finished          # write finished.json on a clean launch
        self.session_id = session_id
        self.started: list[str] = []     # sprint ids launched
        self.stopped: list[str] = []     # tokens stopped
        self.resumed: list[str] = []     # session ids resumed
        self._left: dict[str, int] = {}
        self._n = 0

    def start(self, sprint, context, sprint_dir, repo_root=None):
        self._n += 1
        token = f"fake:{self._n}"
        self.started.append(sprint.id)
        self._left[token] = self.linger
        sprint_dir = Path(sprint_dir)
        sprint_dir.mkdir(parents=True, exist_ok=True)
        if self.finished and self.status == "ok":
            (sprint_dir / "finished.json").write_text("{}")
        return token

    def resume(self, session_id, sprint_dir, nudge, model_slug="", repo_root=None):
        self._n += 1
        token = f"fake:{self._n}"
        self.resumed.append(session_id)
        self._left[token] = self.linger
        return token

    def read_session_id(self, sprint_dir):
        return self.session_id

    def is_running(self, token):
        if not token or token in self.stopped:
            return False
        left = self._left.get(token, 0)
        if left > 0:
            self._left[token] = left - 1
            return True
        return False

    def stop(self, token):
        self.stopped.append(token)

    def collect(self, sprint_dir):
        return self.result, self.status


@pytest.fixture
def substrate(tmp_path):
    return Substrate(tmp_path)


@pytest.fixture
def agent():
    return FakeAgent()


def write_raw_sprint(repo_root, sprint_id, status, goals, plan, body="notes", program=None):
    """Write a sprint.md directly to disk (bypasses Substrate, for arrange steps)."""
    d = repo_root / "sprints" / sprint_id
    d.mkdir(parents=True, exist_ok=True)
    fm = {"status": status, "goals": goals, "plan": plan}
    if program is not None:
        fm["program"] = program
    (d / "sprint.md").write_text(serialize(fm, body))


@pytest.fixture
def wiki_bundle(substrate):
    """A program with an initialised, empty wiki bundle. Returns (substrate, program_id)."""
    from coscience import wiki_store
    from coscience.models import Program
    substrate.save_program(Program(id="p1", title="P1", goals="goals"))
    wiki_store.ensure_bundle(substrate, "p1")
    return substrate, "p1"
