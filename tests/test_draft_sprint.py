"""K5: the planner drafts a sprint proposal from a pool idea for a human to review."""

from coscience import usage_meter
from coscience.models import Idea, Program, ProgramStatus
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="cure", status=ProgramStatus.ACTIVE,
                                       pm_model="claude-opus-5"))
    return svc


def test_a_draft_fills_the_proposal_fields_and_writes_nothing_but_its_call(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_ideas("p1", "", [Idea(id="i1", text="rescore with waters")])
    seen = {}

    def fake(ctx, text):
        seen.update(text=text, model=ctx.model)
        return ({"suffix": "Waters rescore", "title": "T", "summary": "S", "goals": "G",
                 "plan": ["a"], "priority": 2, "rationale": "R"},
                {"total_cost_usd": 0.12, "num_turns": 1})

    draft = svc.draft_sprint_from_idea("p1", "i1", drafter=fake)

    assert seen == {"text": "rescore with waters", "model": "claude-opus-5"}
    assert draft == {"id": "p1-waters-rescore", "title": "T", "summary": "S", "goals": "G",
                     "plan": ["a"], "priority": 2, "rationale": "R"}
    assert [i.id for i in svc.substrate.load_ideas("p1")[1]] == ["i1"]   # still in the pool
    assert list(svc.substrate.iter_sprints()) == []
    (call,) = usage_meter.calls(svc.substrate.repo_root)
    assert call["kind"] == "pm-draft" and call["status"] == "ok" and call["cost"] == 0.12


def test_a_submitted_draft_keeps_its_title_summary_and_rationale(tmp_path):
    svc = _svc(tmp_path)
    svc.submit_sprint(id="p1-x", goals="g", plan=["a"], program="p1",
                      title="T", summary="S", rationale="R")
    s = svc.substrate.load_sprint("p1-x")
    assert (s.title, s.summary, s.rationale) == ("T", "S", "R")


def test_the_draft_prompt_carries_the_idea_and_the_program():
    from coscience.pm_claude import render_draft_prompt
    from coscience.pm_reasoner import PMContext
    prompt = render_draft_prompt(PMContext(program_id="p1", goals="cure", cycle=0), "rescore with waters")
    assert "rescore with waters" in prompt and "cure" in prompt and '"suffix"' in prompt
