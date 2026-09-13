"""H4: a program's default worker model, inherited by sprints when they are proposed."""

from __future__ import annotations

from coscience.models import DEFAULT_MODEL, Program


def _svc(substrate, **program):
    from coscience.service import Service
    substrate.save_program(Program(id="p1", title="P", goals="g", **program))
    return Service(substrate.repo_root)


def test_an_unset_worker_model_is_the_platform_default(substrate):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    assert substrate.load_program("p1").worker_model == DEFAULT_MODEL


def test_a_human_proposal_inherits_the_program_worker_model(substrate):
    svc = _svc(substrate, worker_model="claude-opus-5")
    svc.submit_sprint(id="p1-h1", goals="g", plan=["x"], program="p1")
    assert substrate.load_sprint("p1-h1").model == "claude-opus-5"


def test_a_sprint_outside_any_program_keeps_the_platform_default(substrate):
    from coscience.service import Service
    Service(substrate.repo_root).submit_sprint(id="loose", goals="g", plan=["x"])
    assert substrate.load_sprint("loose").model == DEFAULT_MODEL


def test_changing_the_default_leaves_existing_sprints_alone(substrate):
    svc = _svc(substrate)
    svc.submit_sprint(id="p1-h1", goals="g", plan=["x"], program="p1")
    assert svc.set_program_worker_model("p1", "claude-opus-5")["worker_model"] == "claude-opus-5"
    assert substrate.load_sprint("p1-h1").model == DEFAULT_MODEL
    svc.submit_sprint(id="p1-h2", goals="g", plan=["x"], program="p1")
    assert substrate.load_sprint("p1-h2").model == "claude-opus-5"


def test_clearing_a_sprint_model_returns_it_to_the_program_default(substrate):
    svc = _svc(substrate, worker_model="claude-opus-5")
    svc.submit_sprint(id="p1-h1", goals="g", plan=["x"], program="p1")
    svc.edit_sprint("p1-h1", model="claude-sonnet-5")
    svc.edit_sprint("p1-h1", model="")
    assert substrate.load_sprint("p1-h1").model == "claude-opus-5"


def test_a_pm_proposal_without_a_model_inherits_the_default_and_one_with_keeps_it(substrate):
    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner
    from tests.test_pm_beat import _out
    substrate.save_program(Program(id="p1", title="P", goals="g", worker_model="claude-opus-5"))
    out = _out("a", "report-0")
    out.proposals.append(type(out.proposals[0])(**{**vars(out.proposals[0]),
                                                   "suffix": "b", "model": "claude-haiku-4-5"}))
    pm_beat(substrate, "p1", FakeReasoner([out]))
    models = sorted(s.model for s in substrate.iter_sprints() if s.program == "p1")
    assert models == ["claude-haiku-4-5", "claude-opus-5"]
