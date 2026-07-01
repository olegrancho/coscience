from coscience.models import (PMState, Program, Result, Sprint, SprintStatus)
from coscience.pm_agent import gather_context


def test_gather_context_splits_open_and_completed(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure cancer"))
    substrate.save_pm_state(PMState(program_id="p1", cycle=2, proposed_ids=["p1-c0-a"]))
    substrate.save_sprint(Sprint(id="p1-open", status=SprintStatus.APPROVED,
                                 goals="assay", plan=["do it"], program="p1"))
    substrate.save_sprint(Sprint(id="p1-done", status=SprintStatus.DONE, goals="prior",
                                 plan=["do it"], program="p1",
                                 results=["p1-done-result"]))
    substrate.save_result(Result(id="p1-done-result", sprint="p1-done", summary="found X"))
    substrate.save_sprint(Sprint(id="other", status=SprintStatus.PROPOSED, goals="elsewhere",
                                 plan=["do it"], program="p2"))

    ctx = gather_context(substrate, "p1")
    assert ctx.goals == "cure cancer"
    assert ctx.cycle == 2
    assert ctx.prior_proposals == ["p1-c0-a"]
    assert [s["id"] for s in ctx.open_sprints] == ["p1-open"]
    assert ctx.completed == [{"id": "p1-done", "goals": "prior", "result": "found X"}]


def test_gather_context_done_without_result(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="g"))
    substrate.save_sprint(Sprint(id="p1-d", status=SprintStatus.DONE, goals="d",
                                 plan=["do it"], program="p1"))
    ctx = gather_context(substrate, "p1")
    assert ctx.completed == [{"id": "p1-d", "goals": "d", "result": ""}]


def test_gather_context_includes_human_guidance(tmp_path):
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="t", goals="g"))
    sub.save_guidance("p1", [{"id": "a", "text": "focus on assays", "added_at": 1.0},
                             {"id": "b", "text": "avoid mice", "added_at": 2.0}])
    ctx = gather_context(sub, "p1")
    assert ctx.human_guidance == ["focus on assays", "avoid mice"]


def test_gather_context_empty_guidance(tmp_path):
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="t", goals="g"))
    assert gather_context(sub, "p1").human_guidance == []


def test_gather_context_resolves_workdir_to_project_folder(tmp_path):
    # A program with a real project folder: the PM session must run there, so the
    # planner explores the same tree as its workers (not the loop's launch cwd).
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    project = tmp_path / "project-x"
    project.mkdir()
    sub = Substrate(tmp_path / "repo")
    sub.save_program(Program(id="p1", title="t", goals="g", workdir=str(project)))
    assert gather_context(sub, "p1").workdir == str(project)


def test_gather_context_workdir_falls_back_to_control_repo(tmp_path):
    # No (or missing) workdir -> the control repo, never an inherited/unknown cwd.
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    sub = Substrate(tmp_path / "repo")
    sub.save_program(Program(id="p1", title="t", goals="g", workdir="/no/such/dir"))
    assert gather_context(sub, "p1").workdir == str(sub.repo_root)
