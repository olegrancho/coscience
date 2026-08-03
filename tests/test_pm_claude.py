import json
import stat

import pytest

from coscience.pm_claude import (ClaudeCodeReasoner, PMReasonerError, chat_reply,
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
        {"suffix": "a", "goals": "do a", "plan": ["true"],
         "priority": 3, "resources_required": {"gpu": 1}, "rationale": "because"}]})
    out = parse_response(text)
    assert out.report == "looks good"
    assert len(out.proposals) == 1
    p = out.proposals[0]
    assert (p.suffix, p.goals, p.priority) == ("a", "do a", 3)
    assert p.plan == ["true"]
    assert p.resources_required == {"gpu": 1}
    assert p.rationale == "because"


def test_parse_response_fenced_json_and_optional_defaults():
    text = ("Here is my plan:\n```json\n"
            + json.dumps({"report": "r", "proposals": [
                {"suffix": "b", "goals": "g", "plan": ["true"]}]})
            + "\n```\nThanks!")
    out = parse_response(text)
    assert out.proposals[0].priority == 0
    assert out.proposals[0].resources_required == {}  # coerced to a clean dict
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
            '[{"suffix": "a", "goals": "g", "plan": ["true"], '
            '"resources_required": {"gpu": 1}}]}\n```\n'
            'Note: consider {edge cases} later.')   # stray braces in trailing prose
    out = parse_response(text)
    assert out.proposals[0].suffix == "a"
    assert out.proposals[0].resources_required == {"gpu": 1}   # nested object intact


def test_parse_response_takes_first_of_multiple_blocks():
    text = ('```json\n{"report": "real", "proposals": []}\n```\n'
            'and an unrelated example:\n```json\n{"foo": "bar"}\n```')
    assert parse_response(text).report == "real"


def test_parse_response_reads_reopen_ids():
    out = parse_response(json.dumps({"report": "r", "proposals": [],
                                     "reopen_ids": ["p1-c0-a", "p1-c1-b"]}))
    assert out.reopen_ids == ["p1-c0-a", "p1-c1-b"]


def test_parse_response_reads_release_ids():
    out = parse_response(json.dumps({"report": "r", "proposals": [],
                                     "release_ids": ["p1-c0-a"]}))
    assert out.release_ids == ["p1-c0-a"]


def test_render_prompt_explains_the_approved_queue():
    p = render_prompt(_ctx())
    assert "release_ids" in p and "APPROVED" in p


def test_render_prompt_maps_every_action_to_its_field():
    # A cycle once narrated a release/prune/adopt in `report` while leaving the action
    # lists empty, so nothing happened. The prompt must state the mechanism, not just the
    # policy: each action names the field that performs it.
    p = render_prompt(_ctx())
    for field in ("release_ids", "reopen_ids", "sprint_edits", "proposals",
                  "delete_idea_ids", "new_ideas", "thread_replies",
                  "adopt_artifacts", "artifact_tasks", "edge_ops"):
        assert f'"{field}"' in p, field
    assert "Prose is not an action" in p                  # report is never parsed
    assert "FAILED cycle" in p                            # claiming an unsubmitted action


def test_render_prompt_notes_report_structure():
    p = render_prompt(_ctx())
    assert "Findings" in p and "Rationale" in p          # report must always carry these


def test_run_threads_workdir_to_invoke():
    seen = {}

    def fake_invoke(prompt, model="", cwd=""):
        seen["cwd"] = cwd
        return json.dumps({"report": "ok", "proposals": []})

    ctx = _ctx()
    ctx.workdir = "/tmp/project-x"
    ClaudeCodeReasoner(invoke=fake_invoke).run(ctx)
    assert seen["cwd"] == "/tmp/project-x"


def test_default_invoke_runs_claude_in_workdir(monkeypatch):
    import coscience.pm_claude as m
    captured = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps({"result": "hi", "usage": {}})
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cwd"] = kw.get("cwd")
        return _Proc()

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    ClaudeCodeReasoner()._default_invoke("prompt", cwd="/tmp/proj")
    assert captured["cwd"] == "/tmp/proj"


def test_chat_reply_runs_in_workdir(monkeypatch):
    import coscience.pm_claude as m
    captured = {}

    class _Proc:
        returncode = 0
        stdout = "reply"
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cwd"] = kw.get("cwd")
        return _Proc()

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    ctx = _ctx()
    ctx.workdir = "/tmp/proj"
    assert m.chat_reply(ctx, [], "hi?") == "reply"
    assert captured["cwd"] == "/tmp/proj"


def test_render_prompt_notes_working_directory():
    assert "working directory" in render_prompt(_ctx())


# Linux caps a SINGLE argv string at MAX_ARG_STRLEN (32 pages = 128 KiB), regardless
# of the much larger total ARG_MAX. A busy program's PM prompt goes past that, and
# passing it as `claude -p <prompt>` made execve fail with E2BIG ("Argument list too
# long") on every beat. The prompt must travel on stdin instead.
_OVER_MAX_ARG_STRLEN = 200_000


def _stdin_counting_claude(tmp_path):
    """A stand-in `claude` that reports how many bytes it received on stdin."""
    fake = tmp_path / "claude"
    fake.write_text('#!/usr/bin/env bash\nn=$(wc -c | tr -d " ")\n'
                    'printf \'{"result":"%s","usage":{}}\' "$n"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def test_default_invoke_passes_a_prompt_over_the_argv_limit_on_stdin(tmp_path):
    fake = _stdin_counting_claude(tmp_path)
    prompt = "x" * _OVER_MAX_ARG_STRLEN
    out = ClaudeCodeReasoner(claude_bin=str(fake))._default_invoke(prompt)
    assert out == str(_OVER_MAX_ARG_STRLEN)


def test_chat_reply_passes_a_prompt_over_the_argv_limit_on_stdin(tmp_path):
    fake = _stdin_counting_claude(tmp_path)
    ctx = _ctx()
    ctx.goals = "g" * _OVER_MAX_ARG_STRLEN          # blows past the single-arg cap
    reply = chat_reply(ctx, [], "hi?", claude_bin=str(fake))
    assert int(json.loads(reply)["result"]) > _OVER_MAX_ARG_STRLEN


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
