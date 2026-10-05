"""Catch-up reports (todo I1): due weekly when enough sprints finished, written as a
read-only chat with the planner, listed on the catch-up page."""
import time

import pytest

from coscience import catchup
from coscience.dispatcher import Dispatcher
from coscience.models import Program, Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from coscience.service import Service

DAY = catchup.DAY


def _program(substrate, finished=0, at=None, every=7.0, need=3):
    substrate.save_program(Program(id="p1", title="P", goals="g",
                                   catchup_every_days=every, catchup_min_sprints=need))
    at = time.time() - DAY if at is None else at
    for i in range(finished):
        substrate.save_sprint(Sprint(id=f"p1-c{i}-x", status=SprintStatus.DONE, goals="g",
                                     program="p1", title=f"Sprint {i}",
                                     status_history=[{"status": "done", "at": at + i}]))
    return substrate.load_program("p1")


def _launch(**kw):
    return "123:456"


def test_not_due_until_enough_sprints_finished(substrate):
    p = _program(substrate, finished=2)
    assert catchup.due(substrate, p, time.time())[0] is False
    p = _program(substrate, finished=3)
    is_due, _since, done = catchup.due(substrate, p, time.time())
    assert is_due and len(done) == 3


def test_a_sprint_older_than_the_first_window_does_not_count(substrate):
    p = _program(substrate, finished=3, at=time.time() - 30 * DAY)
    assert catchup.due(substrate, p, time.time())[0] is False


def test_off_when_the_period_is_zero(substrate):
    p = _program(substrate, finished=5, every=0)
    assert catchup.due(substrate, p, time.time())[0] is False


def test_a_report_opens_a_read_only_chat_holding_the_request(substrate):
    _program(substrate, finished=3)
    out = catchup.start(substrate, "p1", by="oleg", trigger="on demand", launch=_launch)
    t = substrate.load_chat_thread("p1", out["id"])
    assert t.scope == "read" and t.pending and t.title.startswith("Catch-up ")
    assert t.catchup["sprints"] == ["p1-c0-x", "p1-c1-x", "p1-c2-x"]
    assert t.catchup["trigger"] == "on demand" and t.catchup["by"] == "oleg"
    request = t.messages[0]["text"]
    assert "**Bottom line**" in request and "**What to do next**" in request
    # I3: a narrative per day from the last report to now, two paragraphs at most.
    assert "**Day by day**" in request and "at most two short paragraphs" in request
    assert "[Sprint 0](/sprints/p1-c0-x)" in request


def test_after_a_report_the_next_waits_a_period_and_one_at_a_time(substrate):
    _program(substrate, finished=3)
    now = time.time()
    catchup.start(substrate, "p1", by="", trigger="schedule", now=now, launch=_launch)
    p = substrate.load_program("p1")
    assert catchup.due(substrate, p, now + DAY)[0] is False          # still being written
    with pytest.raises(ValueError):
        catchup.start(substrate, "p1", by="", trigger="on demand", launch=_launch)
    [t] = catchup.reports(substrate, "p1")
    t.pending = False
    substrate.save_chat_thread("p1", t)
    assert catchup.due(substrate, p, now + DAY)[0] is False          # within the period
    assert catchup.due(substrate, p, now + 8 * DAY)[0] is False      # nothing new finished


def test_the_page_lists_reports_with_the_first_reply_and_the_schedule(substrate):
    _program(substrate, finished=3)
    out = catchup.start(substrate, "p1", by="", trigger="schedule", launch=_launch)
    t = substrate.load_chat_thread("p1", out["id"])
    t.pending = False
    t.messages.append({"role": "pm", "text": "**Bottom line.** Things moved.", "at": 1.0, "by": ""})
    substrate.save_chat_thread("p1", t)
    page = Service(substrate.repo_root).catchup_page("p1")
    [r] = page["reports"]
    assert r["text"].startswith("**Bottom line.**") and len(r["sprints"]) == 3
    assert page["schedule"]["every_days"] == 7.0 and page["schedule"]["min_sprints"] == 3


def test_the_schedule_is_saved_and_defaults_stay_out_of_program_md(substrate):
    _program(substrate, every=7.0, need=10)
    assert "catchup" not in (substrate.program_dir("p1") / "program.md").read_text()
    Service(substrate.repo_root).set_catchup_schedule("p1", 14, 5)
    p = substrate.load_program("p1")
    assert (p.catchup_every_days, p.catchup_min_sprints) == (14.0, 5)
    with pytest.raises(ValueError):
        Service(substrate.repo_root).set_catchup_schedule("p1", -1, 5)


def test_the_dispatch_loop_starts_a_due_report_only_past_the_usage_gate(substrate, agent, monkeypatch):
    _program(substrate, finished=3)
    started = []
    monkeypatch.setattr(catchup, "start", lambda *a, **kw: started.append(kw["trigger"]))
    closed = Dispatcher(substrate, agent, ResourcePool(), SchedulerPolicy(),
                        catchup_gate=lambda: False)
    closed.run_one_cycle()
    assert started == []
    opened = Dispatcher(substrate, agent, ResourcePool(), SchedulerPolicy(),
                        catchup_gate=lambda: True)
    opened.run_one_cycle()
    opened.run_one_cycle()                   # checked at most every ten minutes
    assert started == ["schedule"]
