import time

from coscience.models import (PMState, Program, Result, Sprint, SprintStatus)
from coscience.pm_agent import gather_context


def test_gather_context_splits_open_and_completed(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure cancer"))
    substrate.save_pm_state(PMState(program_id="p1", cycle=2, proposed_ids=["p1-c0-a"]))
    substrate.save_sprint(Sprint(id="p1-open", status=SprintStatus.APPROVED,
                                 goals="assay", plan=["do it"], program="p1"))
    before = time.time()
    substrate.save_sprint(Sprint(id="p1-done", status=SprintStatus.DONE, goals="prior",
                                 plan=["do it"], program="p1",
                                 results=["p1-done-result"]))
    after = time.time()
    substrate.save_result(Result(id="p1-done-result", sprint="p1-done", summary="found X"))
    substrate.save_sprint(Sprint(id="other", status=SprintStatus.PROPOSED, goals="elsewhere",
                                 plan=["do it"], program="p2"))

    ctx = gather_context(substrate, "p1")
    assert ctx.goals == "cure cancer"
    assert ctx.cycle == 2
    assert ctx.prior_proposals == ["p1-c0-a"]
    assert [s["id"] for s in ctx.open_sprints] == ["p1-open"]
    # save_sprint backfills an unset title from goals and seeds status_history
    # with the real save time on first write (see Substrate.save_sprint), so
    # title/finished_at aren't the literal "" / 0.0 of a truly bare record —
    # check those two structurally instead of pinning a wall-clock value.
    assert len(ctx.completed) == 1
    completed = ctx.completed[0]
    assert {k: completed[k] for k in ("id", "goals", "result", "title")} == {
        "id": "p1-done", "goals": "prior", "result": "found X", "title": "prior"}
    assert before <= completed["finished_at"] <= after


def test_gather_context_done_without_result(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="g"))
    before = time.time()
    substrate.save_sprint(Sprint(id="p1-d", status=SprintStatus.DONE, goals="d",
                                 plan=["do it"], program="p1"))
    after = time.time()
    ctx = gather_context(substrate, "p1")
    assert len(ctx.completed) == 1
    completed = ctx.completed[0]
    assert {k: completed[k] for k in ("id", "goals", "result", "title")} == {
        "id": "p1-d", "goals": "d", "result": "", "title": "d"}
    assert before <= completed["finished_at"] <= after


def test_completed_sprints_carry_title_and_sort_oldest_first(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="g"))
    for sid, at in (("p1-late", 200.0), ("p1-early", 100.0)):
        s = Sprint(id=sid, status=SprintStatus.DONE, goals="g", plan=["x"],
                   program="p1", title=f"T-{sid}", results=[f"{sid}-r"])
        s.status_history = [{"status": "done", "at": at, "by": "", "action": ""}]
        substrate.save_sprint(s)
        substrate.save_result(Result(id=f"{sid}-r", sprint=sid, summary="found X"))

    ctx = gather_context(substrate, "p1")
    assert [s["id"] for s in ctx.completed] == ["p1-early", "p1-late"]
    assert ctx.completed[0]["title"] == "T-p1-early"
    assert ctx.completed[1]["finished_at"] == 200.0


def test_gather_context_includes_human_guidance(tmp_path):
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    from coscience import threads as _threads
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="t", goals="g"))
    sub.save_guidance("p1", [_threads.new_thread("pm", "focus on assays", "u", now=1.0, tid="a"),
                             _threads.new_thread("pm", "avoid mice", "u", now=2.0, tid="b")])
    ctx = gather_context(sub, "p1")
    assert ctx.human_guidance == ["focus on assays", "avoid mice"]
    assert len(ctx.guidance_feedback) == 2
    assert ctx.guidance_feedback[0]["thread_id"] == "a"
    assert ctx.guidance_feedback[0]["messages"][-1] == {"role": "human", "text": "focus on assays"}


def test_gather_context_excludes_completed_guidance_from_feedback(tmp_path):
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    from coscience import threads as _threads
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="t", goals="g"))
    th = _threads.new_thread("pm", "handled already", "u", now=1.0)
    th["status"] = "complete"
    sub.save_guidance("p1", [th])
    ctx = gather_context(sub, "p1")
    assert ctx.human_guidance == ["handled already"]  # still shown as standing guidance
    assert ctx.guidance_feedback == []                # but not as an open feedback item


def test_gather_context_empty_guidance(tmp_path):
    from coscience.substrate import Substrate
    from coscience.models import Program
    from coscience.pm_agent import gather_context
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="t", goals="g"))
    ctx = gather_context(sub, "p1")
    assert ctx.human_guidance == []
    assert ctx.guidance_feedback == []


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


def test_gather_context_walks_the_sprint_tree_once(substrate):
    """The PM loop beats every 5s and each walk re-parses every sprint.md in the
    substrate, so a second walk doubles the idle cost of the whole platform."""
    substrate.save_program(Program(id="p1", title="C", goals="g"))
    substrate.save_sprint(Sprint(id="p1-a", status=SprintStatus.PROPOSED, goals="a",
                                 plan=["do it"], program="p1"))
    walks = []
    real = substrate.iter_sprints
    substrate.iter_sprints = lambda *a, **k: (walks.append(1), real(*a, **k))[1]

    gather_context(substrate, "p1")
    assert len(walks) == 1
