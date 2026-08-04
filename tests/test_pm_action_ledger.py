"""A PM cycle must leave evidence of what it actually did.

Regression cover for a live failure: a cycle wrote a report saying it had released a
sprint, pruned the idea pool and adopted an artifact, while every action list came back
empty. Nothing happened, and no log line, state field or report section showed it — the
sprint simply sat in `approved` and every later beat printed "idle — no input changed".
"""
from coscience.models import Program, Sprint, SprintStatus
from coscience.pm_agent import actions_ledger, pm_beat, unbacked_claims
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput


def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def _approved(substrate, sid="p1-a"):
    substrate.save_sprint(Sprint(id=sid, status=SprintStatus.APPROVED, goals="g",
                                 plan=["x"], program="p1"))


# --- the ledger: what the platform applied, written by the platform ---

def test_release_is_recorded_everywhere(substrate):
    _prog(substrate)
    _approved(substrate)
    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="all good", release_ids=["p1-a"])]), force=True)

    assert summary["released"] == ["p1-a"]
    assert "Released: p1-a" in substrate.load_report("p1")      # under the prose
    pm = substrate.load_pm_state("p1")
    assert "released ['p1-a']" in pm.log[-1]                    # travels with the substrate
    assert pm.activations[-1]["released"] == ["p1-a"]


def test_idle_cycle_says_so_rather_than_nothing(substrate):
    _prog(substrate)
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="nothing to do")]),
            force=True)
    assert "No actions submitted this cycle." in substrate.load_report("p1")


# --- unresolvable ids: the release that silently vanished ---

def test_unresolvable_release_id_is_reported_not_swallowed(substrate):
    # The reasoner emitting a suffix instead of the full id used to hit a bare
    # `continue`, which looks exactly like a deliberate hold.
    _prog(substrate)
    _approved(substrate)
    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="released it", release_ids=["a"])]), force=True)

    assert summary["released"] == []
    assert summary["release_skipped"] == [{"id": "a", "why": "no such sprint"}]
    assert "Release FAILED: `a` — no such sprint" in substrate.load_report("p1")
    assert substrate.load_sprint("p1-a").status == SprintStatus.APPROVED   # untouched
    assert "FAILED to release ['a']" in substrate.load_pm_state("p1").log[-1]


def test_release_of_a_non_approved_sprint_names_the_status(substrate):
    _prog(substrate)
    substrate.save_sprint(Sprint(id="p1-b", status=SprintStatus.PROPOSED, goals="g",
                                 plan=["x"], program="p1"))
    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", release_ids=["p1-b"])]), force=True)
    assert summary["release_skipped"] == [
        {"id": "p1-b", "why": "status is proposed, not approved"}]


def test_reopen_skips_are_reported_too(substrate):
    _prog(substrate)
    summary = pm_beat(substrate, "p1", FakeReasoner([
        PMCycleOutput(report="r", reopen_ids=["ghost"])]), force=True)
    assert summary["reopen_skipped"] == [{"id": "ghost", "why": "no such sprint"}]
    assert "Reopen FAILED: `ghost`" in substrate.load_report("p1")


# --- the claim check: prose that describes an action nobody submitted ---

def test_report_claiming_a_release_that_never_happened_is_flagged(substrate):
    _prog(substrate)
    _approved(substrate)
    summary = pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(
        report="Manuscript-draft **released into production**.")]), force=True)

    assert summary["unbacked_claims"] == ["released an approved sprint"]
    assert "no such action was submitted" in substrate.load_report("p1")
    assert substrate.load_pm_state("p1").activations[-1]["unbacked_claims"] == [
        "released an approved sprint"]


def test_a_backed_claim_is_not_flagged(substrate):
    _prog(substrate)
    _approved(substrate)
    summary = pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(
        report="Released the manuscript sprint.", release_ids=["p1-a"])]), force=True)
    assert summary["unbacked_claims"] == []


def test_claim_check_covers_prune_and_adopt():
    assert unbacked_claims("Idea pool pruned 14 -> 11.", {"ideas_removed": 0}) == [
        "pruned the idea pool"]
    assert unbacked_claims("Idea pool pruned 14 -> 11.", {"ideas_removed": 3}) == []
    assert unbacked_claims("Adopted the AUROC tables.", {"adopted": []}) == [
        "adopted an artifact"]
    assert unbacked_claims("Adopted the AUROC tables.", {"adopted": ["a1"]}) == []


# --- the loop's beat line ---

def test_beat_line_distinguishes_release_outcomes():
    from coscience.cli import pm_beat_line

    idle = [{"submitted": [], "skipped": True}]
    assert pm_beat_line(idle, reasoned=0) == "idle — no input changed"
    assert pm_beat_line([{"submitted": []}], reasoned=1) == "reasoned — no new proposals"

    assert pm_beat_line([{"submitted": [], "released": ["p1-a"]}], reasoned=1) == \
        "released p1-a"
    assert pm_beat_line([{"submitted": [],
                          "release_skipped": [{"id": "a", "why": "no such sprint"}]}],
                        reasoned=1) == "SKIPPED a (no such sprint)"
    assert "WARNING report claims it released an approved sprint" in pm_beat_line(
        [{"submitted": [], "unbacked_claims": ["released an approved sprint"]}], reasoned=1)


def test_beat_line_names_a_program_whose_beat_raised():
    # pm_run_once catches a per-program failure so the other programs still beat; the
    # summary then looks like a skip, and a broken program hid behind "idle" for hours.
    from coscience.cli import pm_beat_line
    line = pm_beat_line([{"program": "kg", "submitted": [], "skipped": True,
                          "error": "while parsing a block mapping"}], reasoned=0)
    assert line == "ERROR kg: while parsing a block mapping"


def test_beat_line_still_reports_throttling():
    from coscience.cli import pm_beat_line
    line = pm_beat_line([{"submitted": [], "skipped": True, "throttled": True}], reasoned=0)
    assert line.startswith("paused — Claude usage exhausted")


def test_ledger_lists_each_kind_of_action():
    text = actions_ledger({"released": ["s1"], "reopened": ["s2"], "submitted": ["s3"],
                           "adopted": ["a1"], "dropped": ["s4"], "ideas_added": 2,
                           "ideas_removed": 1, "release_skipped": [], "reopen_skipped": [],
                           "unbacked_claims": []})
    for expected in ("Released: s1", "Reopened: s2", "Proposed: s3", "Adopted: a1",
                     "Not proposed (over the cap): s4", "Ideas added: 2", "Ideas pruned: 1"):
        assert expected in text
