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
