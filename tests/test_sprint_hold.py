"""The planner's hold (E1): "not yet" said on an approved sprint, without spending the
human authorization the planner has no power to give back."""
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Program, Sprint, SprintStatus
from coscience.service import NotFoundError, Service


def _svc(substrate, status=SprintStatus.APPROVED, hold=None):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    substrate.save_sprint(Sprint(id="s1", status=status, goals="g", plan=["x"],
                                 program="p", hold=hold or {}))
    return Service(substrate.repo_root)


_HOLD = {"why": "waiting on the recovery run", "at": 1.0, "by": "pm"}


def test_the_hold_reaches_the_sprint_page(substrate):
    svc = _svc(substrate, hold=_HOLD)
    d = svc.get_sprint("s1")
    assert d["status"] == "approved"          # a hold never moves the sprint
    assert d["hold"]["why"] == "waiting on the recovery run"
    assert d["hold"]["by"] == "pm"


def test_the_hold_reaches_the_program_list(substrate):
    """So a held experiment is visible without opening it."""
    svc = _svc(substrate, hold=_HOLD)
    row = next(s for s in svc.get_program("p")["sprints"] if s["id"] == "s1")
    assert row["hold"]["why"] == "waiting on the recovery run"


def test_a_sprint_with_no_hold_reports_an_empty_one(substrate):
    assert _svc(substrate).get_sprint("s1")["hold"] == {}


def test_a_human_clears_the_hold_and_the_sprint_stays_approved(substrate):
    svc = _svc(substrate, hold=_HOLD)
    svc.clear_sprint_hold("s1", by="olegs")
    d = svc.get_sprint("s1")
    assert d["hold"] == {}
    assert d["status"] == "approved"           # clearing is not a lifecycle transition


def test_clearing_a_hold_writes_no_status_history(substrate):
    """The sprint did not move, so nothing belongs in its lifecycle timeline — and the
    program page must not highlight a row because a hold was lifted."""
    svc = _svc(substrate, hold=_HOLD)
    before = len(svc.get_sprint("s1")["status_history"])
    svc.clear_sprint_hold("s1", by="olegs")
    assert len(svc.get_sprint("s1")["status_history"]) == before


def test_clearing_a_hold_that_is_not_there_refuses(substrate):
    with pytest.raises(ValueError, match="is not held"):
        _svc(substrate).clear_sprint_hold("s1", by="olegs")


def test_clearing_a_hold_on_nothing_is_a_not_found(substrate):
    with pytest.raises(NotFoundError):
        _svc(substrate).clear_sprint_hold("nope", by="olegs")


def test_running_a_held_sprint_clears_the_hold(substrate):
    """A human overriding the hold is the answer to it, not a state to carry forward."""
    svc = _svc(substrate, hold=_HOLD)
    svc.run_sprint("s1", by="olegs")
    d = svc.get_sprint("s1")
    assert d["status"] == "queued" and d["hold"] == {}


def test_sending_a_held_sprint_back_clears_the_hold(substrate):
    """It is no longer approved, so there is nothing left to hold."""
    svc = _svc(substrate, hold=_HOLD)
    svc.send_back_sprint("s1", by="olegs")
    d = svc.get_sprint("s1")
    assert d["status"] == "proposed" and d["hold"] == {}


def test_a_hold_survives_a_save_and_load(substrate):
    svc = _svc(substrate, hold=_HOLD)
    assert substrate.load_sprint("s1").hold == _HOLD


def test_the_clear_route_returns_the_sprint(substrate):
    client = TestClient(build_app(_svc(substrate, hold=_HOLD)))
    r = client.post("/api/sprints/s1/hold/clear")
    assert r.status_code == 200
    assert r.json()["hold"] == {} and r.json()["status"] == "approved"


def test_the_clear_route_404s_and_422s(substrate):
    client = TestClient(build_app(_svc(substrate)))
    assert client.post("/api/sprints/nope/hold/clear").status_code == 404
    r = client.post("/api/sprints/s1/hold/clear")
    assert r.status_code == 422
    assert "not held" in r.json()["detail"]


# --- the planner has to be able to read its own hold -------------------------------

def test_the_planner_sees_a_sprint_it_is_already_holding(substrate):
    """Found at QC. Without this the planner cannot tell a sprint it is waiting on from
    one it has never considered, so it re-holds blindly or forgets and releases early —
    and a human clearing a hold is invisible to it."""
    from coscience.pm_agent import gather_context
    from coscience.pm_claude import render_prompt

    svc = _svc(substrate, hold={"why": "waiting on the recovery run", "at": 1.0, "by": "pm"})
    ctx = gather_context(svc.substrate, "p")
    row = next(s for s in ctx.open_sprints if s["id"] == "s1")
    assert row["hold"] == "waiting on the recovery run"
    assert 'HELD by PM: "waiting on the recovery run"' in render_prompt(ctx)


def test_an_unheld_sprint_says_nothing_about_a_hold(substrate):
    from coscience.pm_agent import gather_context
    from coscience.pm_claude import render_prompt

    svc = _svc(substrate)
    ctx = gather_context(svc.substrate, "p")
    assert next(s for s in ctx.open_sprints if s["id"] == "s1")["hold"] == ""
    assert "HELD by PM" not in render_prompt(ctx)


def test_a_cleared_hold_disappears_from_the_planners_view(substrate):
    """A human's override has to reach the planner, or it re-applies the hold."""
    from coscience.pm_agent import gather_context
    from coscience.pm_claude import render_prompt

    svc = _svc(substrate, hold={"why": "waiting on the recovery run", "at": 1.0, "by": "pm"})
    svc.clear_sprint_hold("s1", by="olegs")
    assert "HELD by PM" not in render_prompt(gather_context(svc.substrate, "p"))


def test_a_hold_does_not_wake_a_cycle(substrate):
    """Holding is the planner's own note, not new input — it must not re-trigger the
    planner and buy itself another call."""
    from coscience.pm_agent import context_fingerprint, gather_context

    svc = _svc(substrate)
    before = context_fingerprint(gather_context(svc.substrate, "p"))
    s = substrate.load_sprint("s1")
    s.hold = {"why": "waiting on the recovery run", "at": 1.0, "by": "pm"}
    substrate.save_sprint(s)
    assert context_fingerprint(gather_context(svc.substrate, "p")) == before


# --- the rationale is one sentence, and the platform guarantees it ------------------

@pytest.mark.parametrize("written,kept", [
    # everything past the first sentence is dropped
    ("Waiting on checkpoint recovery. It also needs the seed ensemble. And more.",
     "Waiting on checkpoint recovery."),
    # no full stop at all is still one sentence
    ("Waiting on the recovery run", "Waiting on the recovery run"),
    # line breaks and runs of whitespace collapse
    ("  Waiting on\n  the recovery run.  Second dropped.  ", "Waiting on the recovery run."),
    # an abbreviation is not a sentence end: a bare [.!?]\s cut this at "i.e."
    ("Held because Lead Finder (i.e. the baseline) has not reported yet.",
     "Held because Lead Finder (i.e. the baseline) has not reported yet."),
    # nor is a decimal point
    ("The hit-rate is 0.847 and that settles it.", "The hit-rate is 0.847 and that settles it."),
    ("Why bother? The result already answers it.", "Why bother?"),
])
def test_a_hold_rationale_is_trimmed_to_one_sentence(written, kept):
    from coscience.pm_agent import hold_reason
    assert hold_reason(written) == kept


def test_a_very_long_single_sentence_is_still_capped():
    from coscience.pm_agent import HOLD_REASON_MAX, hold_reason
    assert len(hold_reason("x" * 900)) == HOLD_REASON_MAX


def test_the_apply_path_stores_only_the_first_sentence(substrate):
    """The prompt asks for one sentence; this is what makes it true."""
    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner, PMCycleOutput

    svc = _svc(substrate)
    pm_beat(substrate, "p", FakeReasoner([PMCycleOutput(report="r", holds=[
        {"id": "s1", "why": "Waiting on the recovery run. Plus a second thought."}])]),
        force=True)
    assert substrate.load_sprint("s1").hold["why"] == "Waiting on the recovery run."


def test_the_prompt_asks_for_exactly_one_sentence(substrate):
    from coscience.pm_agent import gather_context
    from coscience.pm_claude import render_prompt
    # The prompt wraps, so compare on collapsed whitespace rather than raw text.
    p = " ".join(render_prompt(gather_context(_svc(substrate).substrate, "p")).split())
    assert "EXACTLY ONE SENTENCE, no more" in p
    assert "Anything past the first sentence is discarded" in p
    assert "Re-state it each cycle it stays held" in p
