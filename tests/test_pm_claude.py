import json
import re
import stat
from pathlib import Path

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


def test_reasoner_reports_prompt_size_and_resets_stale_cost():
    """last_prompt_bytes must be set before the call, so a raising call still
    reports what it sent; last_cost must be cleared so a failure cannot report the
    PREVIOUS call's cost as its own."""
    r = ClaudeCodeReasoner(invoke=lambda p, m="", c="": '{"report": "ok"}')
    r.run(_ctx())
    assert r.last_prompt_bytes == len(render_prompt(_ctx()))

    r.last_cost = {"cost": 9.99, "tokens": 1}       # stale value from the good call
    def _boom(p, m="", c=""):
        raise PMReasonerError("claude exited 1")
    r._invoke = _boom
    with pytest.raises(PMReasonerError):
        r.run(_ctx())
    assert r.last_cost is None                       # not 9.99
    assert r.last_prompt_bytes == len(render_prompt(_ctx()))


def _done(i, goals="G", result="R"):
    return {"id": f"p1-c{i}-x", "title": f"Sprint {i}", "goals": goals,
            "result": result, "finished_at": float(i)}


def test_recent_results_are_clipped_not_inlined_whole():
    ctx = _ctx()
    ctx.completed = [_done(1, result="R" * 5000)]
    p = render_prompt(ctx)
    assert "R" * 800 in p
    assert "R" * 900 not in p
    assert "clipped" in p              # the PM is told text was withheld, so it can go read it


def test_a_clipped_result_names_the_file_that_holds_the_rest():
    """Every production result is clipped, so the marker is the PM's only route to the
    other ~80% of it. A pointer it cannot resolve is not an escape hatch."""
    ctx = _ctx()
    ctx.results_dir = "/substrate/results"
    ctx.completed = [dict(_done(1, result="R" * 5000), result_id="p1-c1-x-result")]
    p = render_prompt(ctx)
    assert "/substrate/results/p1-c1-x-result.md" in p
    # ...and the "don't hunt up the tree" rule must exempt that directory, or the
    # prompt tells the PM to read a file it also forbids it to go and read.
    preamble = p.split("PROGRAM GOALS:")[0]
    assert "/substrate/results" in preamble


def test_the_path_offered_for_a_clipped_result_is_the_real_file(substrate):
    # End to end against a real substrate: the path the PM is handed must open, and
    # hold the text that was cut. p3's session cwd is outside the substrate entirely,
    # so nothing about this is reachable by guessing.
    from coscience.models import Program, Result, Sprint, SprintStatus
    from coscience.pm_agent import gather_context

    substrate.save_program(Program(id="p1", title="P", goals="g"))
    summary = "FINDING: the ladder holds. " + "z" * 5000
    substrate.save_result(Result(id="p1-c0-a-result", sprint="p1-c0-a", summary=summary))
    substrate.save_sprint(Sprint(id="p1-c0-a", status=SprintStatus.DONE, goals="g",
                                 plan=["x"], program="p1", results=["p1-c0-a-result"]))

    p = render_prompt(gather_context(substrate, "p1"))
    m = re.search(r"full text: (\S+\.md)", p)
    assert m, "a clipped result offered the PM no path at all"
    path = Path(m.group(1))
    assert path.is_file(), f"{path} does not exist"
    assert summary in path.read_text()          # the WHOLE summary, not the excerpt


def test_older_completed_sprints_collapse_to_one_line_but_keep_their_ids():
    ctx = _ctx()
    ctx.completed = [_done(i, goals="G" * 4000, result="R" * 5000) for i in range(20)]
    p = render_prompt(ctx)
    # The oldest survives as an id + title only — ids must stay resolvable for the
    # lineage graph and for release_ids/reopen_ids.
    assert "p1-c0-x" in p
    assert "Sprint 0" in p
    # ...but its bulk is gone, while the newest keeps its (clipped) detail.
    assert p.count("R" * 800) == 8
    assert p.count("G" * 400) == 8


def test_failed_sprints_are_clipped_the_same_way():
    ctx = _ctx()
    ctx.failed = [{"id": "p1-c1-x", "title": "T", "goals": "G" * 4000,
                   "error": "E" * 5000, "finished_at": 1.0}]
    p = render_prompt(ctx)
    assert "E" * 800 in p
    assert "E" * 900 not in p


def test_prior_proposals_are_windowed_in_the_prompt():
    ctx = _ctx()
    ctx.prior_proposals = [f"p1-c{i}-x" for i in range(60)]
    p = render_prompt(ctx)
    assert "p1-c59-x" in p            # newest kept
    assert "p1-c0-x" not in p         # oldest dropped from the prompt only
    assert "40 earlier" in p          # the PM is told the list was trimmed


def test_prompt_does_not_grow_with_program_history():
    """The whole point of Phase 2: a program that has finished 100 sprints must not
    cost meaningfully more per beat than one that has finished 20."""
    def ctx_with(n):
        c = _ctx()
        c.completed = [{"id": f"p1-c{i}-x", "title": f"Sprint {i}",
                        "goals": "G" * 4000, "result": "R" * 5000,
                        "finished_at": float(i)} for i in range(n)]
        # A distinct id namespace from `completed`: history-collapse deliberately
        # keeps every completed sprint's id visible (see _history_block), so reusing
        # those ids here would make a prior_proposals containment check meaningless.
        c.prior_proposals = [f"prior-{i}" for i in range(n)]
        return c

    small_prompt = render_prompt(ctx_with(20))
    large_prompt = render_prompt(ctx_with(100))
    small, large = len(small_prompt), len(large_prompt)
    assert large - small < 8_000, f"prompt grew {large - small:,} B over 80 sprints"
    assert large < 60_000, f"prompt is {large:,} B with 100 sprints of history"
    # Sprint ids are ~10 B each, dwarfed by the goals/results budget above, so an
    # un-windowed prior_proposals list wouldn't trip either byte assertion above.
    # Pin PRIOR_SHOWN directly so this test alone still catches that regression.
    assert "prior-0" not in large_prompt, (
        "byte budgets passed but the oldest prior-proposal id leaked into the "
        "prompt — PRIOR_SHOWN windowing regressed")
    assert "prior-99" in large_prompt, "newest prior-proposal id missing from the prompt"


def test_reasoner_reports_the_token_split(monkeypatch):
    """last_cost carries the per-component usage, not just the sum — the sum alone
    can't be read as cost (a cache read bills at a tenth of fresh input). Drives
    the real _default_invoke, since that is what parses the envelope; an injected
    invoke returns raw text and never populates last_cost."""
    from types import SimpleNamespace
    import coscience.pm_claude as pm_claude

    envelope = json.dumps({
        "result": '{"report": "ok"}',
        "total_cost_usd": 0.9, "num_turns": 6,
        "usage": {"input_tokens": 2, "output_tokens": 5,
                  "cache_creation_input_tokens": 8570,
                  "cache_read_input_tokens": 8073,
                  "output_tokens_details": {"thinking_tokens": 3}},
    })
    monkeypatch.setattr(pm_claude.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=envelope, stderr=""))

    r = ClaudeCodeReasoner()
    r.run(_ctx())

    assert r.last_cost["tokens"] == 16650
    assert r.last_cost["usage"]["cache_read_input_tokens"] == 8073
    assert r.last_cost["usage"]["thinking_tokens"] == 3
    assert r.last_cost["turns"] == 6


# --- PM transcript instrumentation -------------------------------------------------
# The PM is a tool-enabled session and `num_turns` was the ONLY thing recorded about
# it, so the axis that dominates PM cost (turns, not prompt bytes) was invisible:
# nothing said whether 12 turns was 12 file reads or the planner going in circles.
# stream-json gives the same envelope as `json` plus the per-turn events behind it.
from types import SimpleNamespace


def _stream(events) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


_RESULT_EVENT = {"type": "result", "subtype": "success", "result": '{"report": "ok"}',
                 "total_cost_usd": 0.5, "num_turns": 9,
                 "usage": {"input_tokens": 1, "output_tokens": 2,
                           "cache_creation_input_tokens": 30, "cache_read_input_tokens": 70}}


def test_default_invoke_asks_for_a_stream_json_transcript(monkeypatch):
    import coscience.pm_claude as m
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=_stream([_RESULT_EVENT]), stderr="")

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    ClaudeCodeReasoner()._default_invoke("prompt")
    assert "stream-json" in captured["cmd"]
    # stream-json in print mode is rejected without --verbose
    assert "--verbose" in captured["cmd"]


def test_default_invoke_reads_cost_from_the_stream_result_event(monkeypatch):
    import coscience.pm_claude as m
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0,
        stdout=_stream([{"type": "system", "subtype": "init"},
                        {"type": "assistant", "message": {"content": []}},
                        _RESULT_EVENT]),
        stderr=""))
    r = ClaudeCodeReasoner()
    assert r._default_invoke("prompt") == '{"report": "ok"}'
    assert r.last_cost["turns"] == 9
    assert r.last_cost["cost"] == 0.5
    assert r.last_cost["tokens"] == 103


def test_run_writes_the_transcript_next_to_the_programs_lock(monkeypatch, tmp_path):
    import coscience.pm_claude as m
    stream = _stream([{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {}}]}}, _RESULT_EVENT])
    monkeypatch.setattr(m.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=stream, stderr=""))

    ClaudeCodeReasoner(transcript_dir=tmp_path).run(_ctx())      # _ctx() is program p1

    assert (tmp_path / "pm-p1.out").read_text() == stream


def test_a_crashed_claude_still_leaves_its_transcript(monkeypatch, tmp_path):
    """The run you most want the event feed for is the one that died. Writing the feed
    after the returncode check meant a crash raised first and left nothing to read."""
    import coscience.pm_claude as m
    stream = _stream([{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {}}]}}])      # no result event
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=stream, stderr="boom"))

    with pytest.raises(m.PMReasonerError):
        ClaudeCodeReasoner(transcript_dir=tmp_path).run(_ctx())     # _ctx() is program p1

    assert (tmp_path / "pm-p1.out").read_text() == stream


def test_transcript_is_optional(monkeypatch):
    """The transcript is a debugging convenience, not a dependency: constructed with
    no transcript_dir (as tests and embedders do — both production callers pass one),
    the reasoner must still reason."""
    import coscience.pm_claude as m
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=_stream([_RESULT_EVENT]), stderr=""))
    assert ClaudeCodeReasoner().run(_ctx()).report == "ok"


def test_transcripts_are_kept_out_of_the_substrates_git_history(monkeypatch, tmp_path):
    """The substrate commits with `git add -A`, and a PM transcript is REWRITTEN every
    beat (unlike a sprint's agent.out, written once). Committing ~150 KB per beat would
    bloat the data repo with pure debugging noise, so the dir ignores its own feeds."""
    import coscience.pm_claude as m
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=_stream([_RESULT_EVENT]), stderr=""))

    ClaudeCodeReasoner(transcript_dir=tmp_path).run(_ctx())

    assert "*.out" in (tmp_path / ".gitignore").read_text()
