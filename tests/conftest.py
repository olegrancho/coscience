import pytest

from coscience.frontmatter_io import serialize
from coscience.substrate import Substrate


class FakeAgent:
    """Deterministic stand-in for ClaudeAgent in tests — no real claude, no real
    processes. By default an agent 'finishes' immediately: beat 1 launches it,
    beat 2 sees it done and collects. Set linger>0 to keep it 'running' for that
    many is_running polls (for preemption/reconcile tests); stop() ends it."""

    def __init__(self, result="agent findings", status="ok", linger=0):
        self.result = result
        self.status = status
        self.linger = linger
        self.started: list[str] = []     # sprint ids launched
        self.stopped: list[str] = []     # tokens stopped
        self._left: dict[str, int] = {}
        self._n = 0

    def start(self, sprint, context, sprint_dir, repo_root=None):
        self._n += 1
        token = f"fake:{self._n}"
        self.started.append(sprint.id)
        self._left[token] = self.linger
        return token

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
