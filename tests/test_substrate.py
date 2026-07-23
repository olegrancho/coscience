import subprocess

from coscience.models import Sprint, SprintStatus, ProgressState, Result
from coscience.substrate import Substrate
from tests.conftest import write_raw_sprint


def test_load_sprint_parses_plan(substrate):
    write_raw_sprint(
        substrate.repo_root, "sp1", "approved", "cure cancer",
        plan=["scan the primes", "tabulate the gaps"],
    )
    sprint = substrate.load_sprint("sp1")
    assert sprint.id == "sp1"
    assert sprint.status == SprintStatus.APPROVED
    assert sprint.goals == "cure cancer"
    assert sprint.plan == ["scan the primes", "tabulate the gaps"]


def test_save_sprint_backfills_empty_title_from_goals(substrate):
    substrate.save_sprint(Sprint(
        id="sp-notitle", status=SprintStatus.PROPOSED,
        goals="Draft the manuscript introduction.", program="p"))
    s = substrate.load_sprint("sp-notitle")
    assert s.title == "Draft the manuscript introduction."


def test_save_sprint_truncates_long_goals_title(substrate):
    goals = "x" * 200
    substrate.save_sprint(Sprint(
        id="sp-long", status=SprintStatus.PROPOSED, goals=goals, program="p"))
    s = substrate.load_sprint("sp-long")
    assert len(s.title) == 81 and s.title.endswith("…")


def test_save_sprint_title_falls_back_to_id_when_no_goals(substrate):
    substrate.save_sprint(Sprint(
        id="sp-bare", status=SprintStatus.PROPOSED, goals="", program="p"))
    s = substrate.load_sprint("sp-bare")
    assert s.title == "sp-bare"


def test_save_sprint_keeps_explicit_title(substrate):
    substrate.save_sprint(Sprint(
        id="sp-titled", status=SprintStatus.PROPOSED, title="My title",
        goals="whatever", program="p"))
    s = substrate.load_sprint("sp-titled")
    assert s.title == "My title"


def test_legacy_dict_plan_entries_coerce_to_strings(substrate):
    # tolerate old sprint files whose plan was [{id, run}]
    write_raw_sprint(substrate.repo_root, "old", "approved", "g",
                     plan=[{"id": "s1", "run": "echo a"}])
    assert substrate.load_sprint("old").plan == ["echo a"]


def test_save_then_load_roundtrips(substrate):
    sprint = Sprint(
        id="sp2", status=SprintStatus.EXECUTING, goals="g",
        plan=["do the work"], program="prog1",
    )
    substrate.save_sprint(sprint)
    loaded = substrate.load_sprint("sp2")
    assert loaded == sprint


def test_iter_sprints_filters_by_status(substrate):
    write_raw_sprint(substrate.repo_root, "sp1", "approved", "g", ["x"])
    write_raw_sprint(substrate.repo_root, "sp2", "done", "g", ["x"])
    write_raw_sprint(substrate.repo_root, "sp3", "approved", "g", ["x"])
    approved = substrate.iter_sprints(status=SprintStatus.APPROVED)
    assert [s.id for s in approved] == ["sp1", "sp3"]


def test_iter_sprints_empty_when_no_dir(substrate):
    assert substrate.iter_sprints() == []


def test_load_progress_missing_returns_empty(substrate):
    p = substrate.load_progress("sp1")
    assert p == ProgressState(sprint_id="sp1")


def test_save_then_load_progress_roundtrips(substrate):
    p = ProgressState(sprint_id="sp1", agent_token="4242:123456", started_at=12.5)
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


def test_guidance_round_trip(tmp_path):
    from coscience.substrate import Substrate
    from coscience import threads
    sub = Substrate(tmp_path)
    assert sub.load_guidance("p1") == []
    guidance_threads = [threads.new_thread("pm", "focus on assays", "u", now=1.0)]
    sub.save_guidance("p1", guidance_threads)
    assert sub.load_guidance("p1") == guidance_threads


def test_legacy_guidance_notes_adapt_to_threads(tmp_path):
    from coscience.substrate import Substrate
    sub = Substrate(tmp_path)
    # write a guidance.md with the OLD notes shape, no threads key
    d = sub.program_dir("p1"); d.mkdir(parents=True, exist_ok=True)
    (d / "guidance.md").write_text(
        "---\ntype: guidance\nnotes:\n  - id: a1\n    text: legacy note\n    added_at: 5.0\n"
        "---\n# Guidance p1\n")
    got = sub.load_guidance("p1")
    assert len(got) == 1
    assert got[0]["target"] == "pm"
    assert got[0]["messages"][0]["text"] == "legacy note"
