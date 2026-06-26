import json

import pytest

from coscience.pm_claude import (ClaudeCodeReasoner, PMReasonerError,
                                 parse_response, render_prompt)
from coscience.pm_reasoner import PMContext


def _ctx():
    return PMContext(
        program_id="p1", goals="cure cancer", cycle=2,
        open_sprints=[{"id": "p1-open", "status": "approved", "goals": "assay X"}],
        completed=[{"id": "p1-c0-a", "goals": "prior", "result": "found Y"}],
        prior_proposals=["p1-c0-a"])


def test_render_prompt_includes_state_and_json_instruction():
    p = render_prompt(_ctx())
    assert "cure cancer" in p
    assert "assay X" in p            # open sprint
    assert "found Y" in p            # completed result
    assert "p1-c0-a" in p            # prior proposal (don't repeat)
    assert "JSON" in p
    assert "proposals" in p and "suffix" in p   # schema cues


def test_parse_response_plain_json():
    text = json.dumps({"report": "looks good", "proposals": [
        {"suffix": "a", "goals": "do a", "plan": [{"id": "s", "run": "true"}],
         "priority": 3, "resources_required": {"gpu": 1}, "rationale": "because"}]})
    out = parse_response(text)
    assert out.report == "looks good"
    assert len(out.proposals) == 1
    p = out.proposals[0]
    assert (p.suffix, p.goals, p.priority) == ("a", "do a", 3)
    assert p.plan == [{"id": "s", "run": "true"}]
    assert p.resources_required == {"gpu": 1}
    assert p.rationale == "because"


def test_parse_response_fenced_json_and_optional_defaults():
    text = ("Here is my plan:\n```json\n"
            + json.dumps({"report": "r", "proposals": [
                {"suffix": "b", "goals": "g", "plan": [{"id": "s", "run": "true"}]}]})
            + "\n```\nThanks!")
    out = parse_response(text)
    assert out.proposals[0].priority == 0
    assert out.proposals[0].resources_required is None
    assert out.proposals[0].rationale == ""


def test_parse_response_no_json_raises():
    with pytest.raises(PMReasonerError):
        parse_response("I could not decide. No JSON here.")


def test_parse_response_invalid_json_raises():
    with pytest.raises(PMReasonerError):
        parse_response("{ not valid json )")


def test_parse_response_missing_required_field_raises():
    with pytest.raises(PMReasonerError):
        parse_response(json.dumps({"report": "r", "proposals": [{"goals": "g"}]}))


def test_run_uses_injected_invoke():
    canned = json.dumps({"report": "ok", "proposals": []})
    seen = {}

    def fake_invoke(prompt: str) -> str:
        seen["prompt"] = prompt
        return canned

    reasoner = ClaudeCodeReasoner(invoke=fake_invoke)
    out = reasoner.run(_ctx())
    assert out.report == "ok"
    assert "cure cancer" in seen["prompt"]   # render_prompt was used


def test_parse_response_handles_prose_with_braces_after_json():
    text = ('```json\n{"report": "r", "proposals": '
            '[{"suffix": "a", "goals": "g", "plan": [{"id": "s", "run": "true"}], '
            '"resources_required": {"gpu": 1}}]}\n```\n'
            'Note: consider {edge cases} later.')   # stray braces in trailing prose
    out = parse_response(text)
    assert out.proposals[0].suffix == "a"
    assert out.proposals[0].resources_required == {"gpu": 1}   # nested object intact


def test_parse_response_takes_first_of_multiple_blocks():
    text = ('```json\n{"report": "real", "proposals": []}\n```\n'
            'and an unrelated example:\n```json\n{"foo": "bar"}\n```')
    assert parse_response(text).report == "real"


def test_render_prompt_includes_guidance():
    from coscience.pm_claude import render_prompt
    from coscience.pm_reasoner import PMContext
    ctx = PMContext(program_id="p1", goals="g", cycle=0,
                    human_guidance=["focus on assays"])
    assert "focus on assays" in render_prompt(ctx)


def test_render_prompt_omits_guidance_when_empty():
    from coscience.pm_claude import render_prompt
    from coscience.pm_reasoner import PMContext
    ctx = PMContext(program_id="p1", goals="g", cycle=0)
    assert "HUMAN GUIDANCE" not in render_prompt(ctx)
