"""Wiki runs appear in the Claude call log.

The wiki was the platform's only active Claude spender over 08-30..09-01 and
recorded nothing, so the Compute totals showed pm and worker spend alone and the
~$46 the wiki burned was invisible until someone read `agent.out` by hand. These
tests pin that a wiki run opens a call at launch and closes it at collect, with
the status the envelope actually reports."""

from __future__ import annotations

import json

import pytest

from coscience import usage_meter, wiki, wiki_agent, wiki_okf, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus
from tests.test_wiki_beat import FakeWikiAgent


@pytest.fixture
def agent2():
    return FakeWikiAgent()


def _seed(substrate, pid="p1"):
    substrate.save_program(Program(id=pid, title="P", goals="g",
                                   wiki_model="claude-opus-4-6"))
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id="r1", sprint="s1", summary="s", completed_at=1.0))


def _finish(run_dir, code, envelope=None):
    """Land what a real run leaves behind: an exit code and a stream."""
    (run_dir / "agent.exit").write_text(str(code))
    if envelope is not None:
        (run_dir / "agent.out").write_text(json.dumps(envelope) + "\n")
    if code == 0:
        (run_dir / "report.json").write_text(json.dumps({"objects": ["result:r1"]}))


def test_launching_a_run_opens_a_call(substrate, agent2):
    _seed(substrate)
    wiki.beat(substrate, substrate.load_program("p1"), 100.0, agent2)

    (call,) = usage_meter.calls(substrate.repo_root, now=110.0)
    assert call["kind"] == "wiki-ingest"
    assert call["program"] == "p1"
    assert call["model"] == "claude-opus-4-6"
    assert call["status"] == "running"
    assert call["started_at"] == 100.0


def test_a_killed_run_shows_as_lost_not_as_nothing(substrate, agent2):
    """The whole point of opening the call at launch: before this, a run that
    never came back left no trace at all."""
    _seed(substrate)
    wiki.beat(substrate, substrate.load_program("p1"), 100.0, agent2)

    (call,) = usage_meter.calls(substrate.repo_root, now=100_000.0)
    assert call["status"] == "lost"


def test_a_clean_run_closes_its_call_with_cost(substrate, agent2):
    _seed(substrate)
    program = substrate.load_program("p1")
    wiki.beat(substrate, program, 100.0, agent2)
    run_dir = agent2.launches[0]["run_dir"]
    _finish(run_dir, 0, {"type": "result", "total_cost_usd": 1.75,
                         "usage": {"input_tokens": 10, "output_tokens": 20}})
    agent2.alive = False
    wiki.beat(substrate, program, 160.0, agent2)

    (call,) = usage_meter.calls(substrate.repo_root)
    assert call["status"] == "ok"
    assert call["cost"] == 1.75
    assert call["ended_at"] == 160.0 and call["duration"] == 60.0


def test_a_rate_limited_run_is_recorded_as_such_with_its_cost(substrate, agent2):
    """The 08-30..09-01 shape: exit 1, but a full envelope with real spend. Must
    not read as `failed`, because B1 keys quarantine off exactly this distinction."""
    _seed(substrate)
    program = substrate.load_program("p1")
    wiki.beat(substrate, program, 100.0, agent2)
    _finish(agent2.launches[0]["run_dir"], 1,
            {"type": "result", "is_error": True, "api_error_status": 429,
             "total_cost_usd": 2.25,
             "result": "You've hit your session limit · resets 2am"})
    agent2.alive = False
    wiki.beat(substrate, program, 160.0, agent2)

    (call,) = usage_meter.calls(substrate.repo_root)
    assert call["status"] == "rate-limited"
    assert call["cost"] == 2.25


def test_a_lint_run_is_a_distinct_kind(substrate, agent2):
    _seed(substrate)
    program = substrate.load_program("p1")
    wiki_store.ensure_bundle(substrate, "p1")
    # A lint run is only due against a bundle with something in it.
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x" * 400))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingested"] = {"result:r1": {"hash": "x"}}
        state["ingests_since_lint"] = 99
    wiki.beat(substrate, program, 100.0, agent2)

    (call,) = usage_meter.calls(substrate.repo_root, now=110.0)
    assert call["kind"] == "wiki-lint"


def test_the_window_reading_is_captured_from_the_run_stream(substrate, agent2):
    _seed(substrate)
    program = substrate.load_program("p1")
    wiki.beat(substrate, program, 100.0, agent2)
    _finish(agent2.launches[0]["run_dir"], 1, None)
    (agent2.launches[0]["run_dir"] / "agent.out").write_text(
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "status": "rejected",
            "unifiedWindows": {"five_hour": {"utilization": 1.04,
                                             "resetsAt": 1788339600}}}}) + "\n"
        + json.dumps({"type": "result", "is_error": True,
                      "api_error_status": 429, "total_cost_usd": 1.0}) + "\n")
    agent2.alive = False
    wiki.beat(substrate, program, 160.0, agent2)

    (call,) = usage_meter.calls(substrate.repo_root)
    assert call["limits_after"]["pct"] == 104


# --- the envelope reader itself ---------------------------------------------

def test_outcome_reads_cost_and_status_from_the_envelope(tmp_path):
    (tmp_path / "agent.out").write_text(json.dumps(
        {"type": "result", "total_cost_usd": 3.2, "num_turns": 41,
         "modelUsage": {"claude-opus-4-6": {}},
         "usage": {"input_tokens": 5, "output_tokens": 7,
                   "output_tokens_details": {"thinking_tokens": 3}}}) + "\n")
    got = wiki_agent.read_outcome(tmp_path)
    assert got["cost"] == 3.2 and got["turns"] == 41
    assert got["model"] == "claude-opus-4-6"
    assert got["usage"]["thinking_tokens"] == 3


def test_outcome_names_the_model_that_did_the_work_not_the_first_listed(tmp_path):
    """F9: p2 r0012 listed Haiku ($0.001, a Claude Code side call) ahead of Opus
    ($2.32), and Compute labelled the whole run Haiku."""
    (tmp_path / "agent.out").write_text(json.dumps(
        {"type": "result", "total_cost_usd": 2.323,
         "modelUsage": {"claude-haiku-4-5-20251001": {"costUSD": 0.001},
                        "claude-opus-4-6": {"costUSD": 2.322}}}) + "\n")
    assert wiki_agent.read_outcome(tmp_path)["model"] == "claude-opus-4-6"


def test_outcome_of_a_missing_stream_is_empty_not_an_error(tmp_path):
    assert wiki_agent.read_outcome(tmp_path) == {}


def test_outcome_ignores_a_torn_final_line(tmp_path):
    """A killed process leaves a half-written line; the last COMPLETE result wins."""
    (tmp_path / "agent.out").write_text(
        json.dumps({"type": "result", "total_cost_usd": 1.0}) + "\n{\"type\": \"resu")
    assert wiki_agent.read_outcome(tmp_path)["cost"] == 1.0


def test_a_wiki_run_refreshes_the_host_rate_limit_cache(substrate, agent2, monkeypatch, tmp_path):
    """F3: chat, worker and PM all feed `record_limits`; the wiki did not, so the
    busiest Claude consumer on the box contributed no readings and every budget
    check fell back to shelling out to usage.py."""
    monkeypatch.setenv("COSCIENCE_LIMITS_CACHE", str(tmp_path / "rl.json"))
    _seed(substrate)
    program = substrate.load_program("p1")
    wiki.beat(substrate, program, 100.0, agent2)
    run_dir = agent2.launches[0]["run_dir"]
    (run_dir / "agent.exit").write_text("1")
    (run_dir / "agent.out").write_text(json.dumps(
        {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed",
            "unifiedWindows": {"five_hour": {"utilization": 0.42,
                                             "resetsAt": 1788339600}}}}) + "\n"
        + json.dumps({"type": "result", "total_cost_usd": 1.0}) + "\n")
    agent2.alive = False
    wiki.beat(substrate, program, 160.0, agent2)

    got = usage_meter.read_limits(now=160.0)
    assert got and got["windows"]["5h"]["pct"] == 42


def test_the_run_stamps_both_ends_of_the_window_it_spent(substrate, agent2):
    """The 09-09 ingest logged `limits_after: 24%` and no `before` at all.

    Nothing was wrong with the run: the launch stamp asks the usage script, that
    script needs a live OAuth token, and on a box idle for two days the token had
    expired — so it served its own two-day-old cache, which `read_budget` rightly
    refuses. Meanwhile the run's stream had carried `utilization: 0` five lines
    in. Read both ends of the stream and the column fills itself, with no
    credentials and nothing that can go stale."""
    _seed(substrate)
    program = substrate.load_program("p1")
    wiki.beat(substrate, program, 100.0, agent2)
    run_dir = agent2.launches[0]["run_dir"]
    (run_dir / "agent.exit").write_text("0")
    (run_dir / "report.json").write_text(json.dumps({"objects": ["result:r1"]}))
    (run_dir / "agent.out").write_text("\n".join([
        json.dumps({"type": "system", "session_id": "s-1"}),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed",
            "unifiedWindows": {"five_hour": {"utilization": 0.0,
                                             "resetsAt": 1789030200}}}}),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed",
            "unifiedWindows": {"five_hour": {"utilization": 0.24,
                                             "resetsAt": 1789030200}}}}),
        json.dumps({"type": "result", "total_cost_usd": 1.46}),
    ]) + "\n")
    agent2.alive = False
    wiki.beat(substrate, program, 160.0, agent2)

    (call,) = usage_meter.calls(substrate.repo_root)
    assert call["limits_before"]["pct"] == 0
    assert call["limits_after"]["pct"] == 24


def test_outcome_reports_the_opening_reading_separately(tmp_path):
    (tmp_path / "agent.out").write_text("\n".join([
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "unifiedWindows": {"five_hour": {"utilization": 0.05}}}}),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "unifiedWindows": {"five_hour": {"utilization": 0.61}}}}),
        json.dumps({"type": "result", "total_cost_usd": 1.0}),
    ]) + "\n")
    got = wiki_agent.read_outcome(tmp_path)
    assert got["limits_before"]["pct"] == 5
    assert got["limits"]["pct"] == 61


def test_a_single_reading_is_not_pretended_to_be_two(tmp_path):
    """One reading means the run ended where it started as far as we can tell —
    stamping it on both ends is honest; inventing a zero is not."""
    (tmp_path / "agent.out").write_text("\n".join([
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "unifiedWindows": {"five_hour": {"utilization": 0.61}}}}),
        json.dumps({"type": "result", "total_cost_usd": 1.0}),
    ]) + "\n")
    got = wiki_agent.read_outcome(tmp_path)
    assert got["limits_before"] == got["limits"] == {"pct": 61, "resets": "?"}
