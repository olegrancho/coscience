"""Delegated approval (todo M1): a person lends the planner the authority to approve
proposed sprints until a limit, and the limit is enforced at every approval."""
import time

import pytest

from coscience import grant
from coscience.models import Program, Sprint, SprintStatus
from coscience.pm_agent import gather_context, pm_beat
from coscience.pm_claude import render_prompt
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput
from coscience.service import Service

NOW = 1_000_000.0


def test_a_grant_needs_a_limit_that_ends_it():
    with pytest.raises(ValueError):
        grant.new("oleg", "forever")
    with pytest.raises(ValueError):
        grant.new("oleg", "sprints", sprints=0)
    with pytest.raises(ValueError):
        grant.new("oleg", "until", until=NOW - 1, now=NOW)
    with pytest.raises(ValueError):
        grant.new("oleg", "window5h", now=NOW, windows={})


def test_each_limit_ends_the_grant_and_says_why():
    g = grant.new("oleg", "sprints", sprints=2, now=NOW)
    assert grant.is_live(g, NOW)
    g["approved"] = ["a", "b"]
    assert "approved 2" in grant.end_reason(g, NOW)

    g = grant.new("oleg", "until", until=NOW + 3600, now=NOW)
    assert grant.is_live(g, NOW + 3599) and "deadline" in grant.end_reason(g, NOW + 3600)

    windows = {"5h": {"pct": 40, "resets_at": NOW + 7200}}
    g = grant.new("oleg", "window5h", now=NOW, windows=windows)
    assert grant.is_live(g, NOW + 10, windows)
    assert "reached 80%" in grant.end_reason(g, NOW + 10, {"5h": {"pct": 80}})
    assert "reset" in grant.end_reason(g, NOW + 7200, windows)


def _program_with(substrate, g=None, sprints=("p1-a", "p1-b", "p1-c")):
    substrate.save_program(Program(id="p1", title="P", goals="g", approval_grant=g or {}))
    for sid in sprints:
        substrate.save_sprint(Sprint(id=sid, status=SprintStatus.PROPOSED, goals="g",
                                     plan=["x"], program="p1"))


def test_without_a_grant_the_planner_cannot_approve(substrate):
    _program_with(substrate)
    out = pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r", approve_ids=["p1-a"])]),
                  force=True)
    assert substrate.load_sprint("p1-a").status == SprintStatus.PROPOSED
    assert "no live approval grant" in out["approve_skipped"][0]["why"]
    assert "approve_ids" not in render_prompt(gather_context(substrate, "p1"))


def test_under_a_grant_it_approves_up_to_the_limit_and_the_grant_ends_loudly(substrate):
    _program_with(substrate, grant.new("oleg", "sprints", sprints=2, now=time.time()))
    ctx = gather_context(substrate, "p1")
    assert ctx.grant and "2 of 2 approvals left" in render_prompt(ctx)
    out = PMCycleOutput(report="r", approve_ids=["p1-a", "p1-b", "p1-c"], release_ids=["p1-a"])
    res = pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    assert res["approved"] == ["p1-a", "p1-b"]
    assert [s["id"] for s in res["approve_skipped"]] == ["p1-c"]
    assert substrate.load_sprint("p1-a").status == SprintStatus.QUEUED     # approved, then released
    b = substrate.load_sprint("p1-b")
    assert b.status == SprintStatus.APPROVED
    assert b.status_history[-1]["by"] == "pm" and b.status_history[-1]["action"] == "approve (grant)"
    g = substrate.load_program("p1").approval_grant
    assert g["approved"] == ["p1-a", "p1-b"] and g["ended_at"] and "approved 2" in g["end_reason"]
    assert gather_context(substrate, "p1").grant == {}


def test_a_new_grant_wakes_the_planner(substrate):
    from coscience.pm_agent import context_fingerprint
    _program_with(substrate)
    before = context_fingerprint(gather_context(substrate, "p1"))
    Service(substrate.repo_root).grant_approval("p1", "oleg", "sprints", sprints=3)
    assert context_fingerprint(gather_context(substrate, "p1")) != before


def test_revoke_and_dismiss(substrate):
    _program_with(substrate)
    svc = Service(substrate.repo_root)
    view = svc.grant_approval("p1", "oleg", "until", until=time.time() + 3600)
    assert view["live"] and "deadline" in view["remaining"]
    with pytest.raises(ValueError):
        svc.grant_approval("p1", "oleg", "sprints", sprints=1)      # one at a time
    with pytest.raises(ValueError):
        svc.dismiss_grant_notice("p1")                               # a live one is revoked, not hidden
    view = svc.revoke_approval("p1", "oleg")
    assert not view["live"] and view["end_reason"] == "revoked by oleg"
    assert svc.get_program("p1")["approval_grant"]["end_reason"] == "revoked by oleg"
    svc.dismiss_grant_notice("p1")
    assert svc.get_program("p1")["approval_grant"] is None


# --- M3: a grant paced to the week ---------------------------------------------------

def _week(used, elapsed_pct, now=NOW):
    """A weekly reading with `used`% spent and `elapsed_pct`% of the window gone."""
    return {"week": {"pct": used, "resets_at": now + grant.WEEK * (1 - elapsed_pct / 100)}}


def test_a_paced_grant_approves_only_while_usage_is_behind_the_week():
    g = grant.new("oleg", "paced", now=NOW)
    assert grant.is_live(g, NOW, _week(90, 10))              # never ends on its own...
    assert grant.hold_reason(g, NOW, _week(30, 40)) == ""     # ...behind the week: may approve
    assert "ahead of the week" in grant.hold_reason(g, NOW, _week(45, 40))
    assert "no weekly usage reading" in grant.hold_reason(g, NOW, {})
    assert "on pace" in grant.remaining(g, NOW, _week(30, 40))
    assert grant.hold_reason(grant.new("oleg", "sprints", sprints=1, now=NOW), NOW, _week(99, 1)) == ""


def test_ahead_of_pace_the_planner_is_offered_nothing_and_approvals_are_refused(substrate, monkeypatch):
    _program_with(substrate, grant.new("oleg", "paced", now=time.time()))
    reading = {}
    monkeypatch.setattr(grant, "_windows", lambda: reading)
    reading.update(_week(60, 50, time.time()))                # ahead of the week
    assert "approve_ids" not in render_prompt(gather_context(substrate, "p1"))
    out = pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r", approve_ids=["p1-a"])]),
                  force=True)
    assert substrate.load_sprint("p1-a").status == SprintStatus.PROPOSED
    assert "paused" in out["approve_skipped"][0]["why"]
    assert grant.is_live(substrate.load_program("p1").approval_grant, time.time())   # not ended

    reading.update(_week(30, 50, time.time()))                # the week has caught up
    prompt = render_prompt(gather_context(substrate, "p1"))
    assert "approve_ids" in prompt and "paced to the week" in prompt
    out = pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r", approve_ids=["p1-a"])]),
                  force=True)
    assert out["approved"] == ["p1-a"]


def test_the_dashboard_shows_the_pace(substrate, monkeypatch):
    monkeypatch.setattr(grant, "_windows", lambda: _week(30, 50, time.time()))
    svc = Service(substrate.repo_root)
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    view = svc.grant_approval("p1", "oleg", "paced")
    assert view["live"] and view["held"] == ""
    assert round(view["pace"]["used"]) == 30 and round(view["pace"]["elapsed"]) == 50
