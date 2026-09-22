"""A call whose end was never written must stop reading as `running` (B3).

The outage of 2026-09-20 left three PM calls shown as in flight eight hours later. A call
is inferred `running` while the process named by its token is alive — and the PM's token
is the loop's own pid, which outlives every cycle, so once an end event was lost the row
could never retire.
"""
import os

from coscience import usage_meter
from coscience.executor import process_token


def open_pm_call(root, program: str, token: str, now: float) -> str:
    return usage_meter.start_call(root, "pm", program=program, token=token, now=now)


def status_of(root, rid: str, now: float) -> str:
    return next(c["status"] for c in usage_meter.calls(root, now=now) if c["id"] == rid)


def test_this_loop_retires_its_own_unfinished_call(tmp_path):
    """The whole bug: the loop is alive, so the row is `running` forever."""
    me = process_token(os.getpid())
    stale = open_pm_call(tmp_path, "p1", me, now=1000.0)
    later = 1000.0 + 8 * 3600
    assert status_of(tmp_path, stale, later) == "running"      # what the dashboard showed

    assert usage_meter.retire_open_calls(tmp_path, kind="pm", token=me, program="p1") == [stale]
    assert status_of(tmp_path, stale, later) == "lost"


def test_a_call_that_ended_is_left_alone(tmp_path):
    me = process_token(os.getpid())
    done = open_pm_call(tmp_path, "p1", me, now=1000.0)
    usage_meter.finish_call(tmp_path, done, status="ok", now=1100.0)

    assert usage_meter.retire_open_calls(tmp_path, kind="pm", token=me, program="p1") == []
    assert status_of(tmp_path, done, 2000.0) == "ok"


def test_another_program_keeps_its_live_call(tmp_path):
    """One loop process serves every program in turn, so the token alone is too
    coarse: retiring on it would close the cycle running for another program."""
    me = process_token(os.getpid())
    mine = open_pm_call(tmp_path, "p1", me, now=1000.0)
    theirs = open_pm_call(tmp_path, "p2", me, now=1001.0)

    assert usage_meter.retire_open_calls(tmp_path, kind="pm", token=me, program="p1") == [mine]
    assert status_of(tmp_path, theirs, 1002.0) == "running"


def test_another_process_keeps_its_live_call(tmp_path):
    """Two platforms can share a substrate; a loop retires only its own leftovers."""
    me = process_token(os.getpid())
    other = open_pm_call(tmp_path, "p1", "999999:1", now=1000.0)
    usage_meter.retire_open_calls(tmp_path, kind="pm", token=me, program="p1")
    # Untouched by us — and read on its own merits: that pid is not alive here.
    assert status_of(tmp_path, other, 1002.0) == "lost"


def test_a_worker_call_is_not_retired_by_the_planner(tmp_path):
    me = process_token(os.getpid())
    worker = usage_meter.start_call(tmp_path, "worker", sprint="p1-c1", token=me, now=1000.0)
    assert usage_meter.retire_open_calls(tmp_path, kind="pm", token=me, program="p1") == []
    assert status_of(tmp_path, worker, 1002.0) == "running"


def test_retiring_without_a_token_does_nothing(tmp_path):
    """An untokened start is left to the age rule; nothing identifies it as ours."""
    rid = usage_meter.start_call(tmp_path, "pm", program="p1", now=1000.0)
    assert usage_meter.retire_open_calls(tmp_path, kind="pm", token="", program="p1") == []
    assert status_of(tmp_path, rid, 1002.0) == "running"       # inside the grace window


def test_the_run_of_stale_calls_clears_in_one_pass(tmp_path):
    """Three cycles in a row lost their end — the shape the outage left behind."""
    me = process_token(os.getpid())
    rids = [open_pm_call(tmp_path, "p1", me, now=1000.0 + i) for i in range(3)]
    assert sorted(usage_meter.retire_open_calls(tmp_path, kind="pm",
                                                token=me, program="p1")) == sorted(rids)
    assert {status_of(tmp_path, r, 9000.0) for r in rids} == {"lost"}
