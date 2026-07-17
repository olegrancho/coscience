from pathlib import Path

import pytest

import coscience.worker as worker_mod
from coscience.frontmatter_io import serialize
from coscience.substrate import Substrate


@pytest.fixture(autouse=True)
def _permissive_usage(monkeypatch):
    """Default the usage gate to 'ok' so worker/dispatcher tests don't shell out
    to the real usage script. Tests that exercise the gate pass usage_gate=... ."""
    monkeypatch.setattr(worker_mod, "claude_usage_ok", lambda *a, **k: True)


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


def write_raw_sprint(repo_root, sprint_id, status, goals, plan, body="notes"):
    """Write a sprint.md directly to disk (bypasses Substrate, for arrange steps)."""
    d = repo_root / "sprints" / sprint_id
    d.mkdir(parents=True, exist_ok=True)
    fm = {"status": status, "goals": goals, "plan": plan}
    (d / "sprint.md").write_text(serialize(fm, body))
