import subprocess

from coscience.models import Sprint, SprintStatus, Step, ProgressState, Result
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
