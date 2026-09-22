"""A cycle's reasoning must survive the next cycle (E2).

`report.md` is overwritten every cycle, so the why behind any planner decision was gone
as soon as the next one ran: a status change nobody could explain, hours later, with the
prose that explained it already replaced. Two halves — the whole report is kept per
cycle, and each sprint the cycle acted on carries its own share of it.
"""
from coscience.models import Sprint, SprintStatus
from coscience.pm_agent import (SPRINT_NOTES_KEPT, record_sprint_notes, sprint_share,
                                touched_sprints)

REPORT = (
    "Released p1-c4 because the docking baseline it depends on finally landed. "
    "p1-c9 stays held: its plan still assumes the old scoring function. "
    "Nothing else changed this cycle."
)


def test_a_report_is_kept_per_cycle_and_the_latest_still_reads_plainly(substrate):
    substrate.save_report("p1", "first pass", cycle=0)
    substrate.save_report("p1", "second pass", cycle=1)

    assert substrate.load_report("p1") == "second pass\n"          # the current one
    assert substrate.load_report("p1", cycle=0) == "first pass\n"  # not erased
    assert substrate.report_cycles("p1") == [1, 0]                 # newest first


def test_a_report_saved_without_a_cycle_is_not_archived(substrate):
    """Nothing but a planner cycle has a cycle number to file under."""
    substrate.save_report("p1", "ad hoc")
    assert substrate.load_report("p1") == "ad hoc\n"
    assert substrate.report_cycles("p1") == []


def test_a_sprints_share_is_the_sentences_that_name_it(substrate):
    assert sprint_share(REPORT, "p1-c4").startswith("Released p1-c4 because")
    assert "old scoring function" in sprint_share(REPORT, "p1-c9")
    assert sprint_share(REPORT, "p1-c9").startswith("p1-c9 stays held")


def test_a_sprint_the_report_never_mentions_keeps_nothing(substrate):
    """Better silent than carrying an explanation nobody wrote about it."""
    assert sprint_share(REPORT, "p1-c77") == ""


def test_a_longer_id_that_starts_the_same_is_a_different_sprint(substrate):
    text = "Released p1-c4-followup after the rerun."
    assert sprint_share(text, "p1-c4") == ""
    assert sprint_share(text, "p1-c4-followup").startswith("Released")


def test_every_action_counts_as_touching_a_sprint(substrate):
    """The lists do not agree on shape: applied actions hold bare ids, skipped ones
    {id, why}, and an answered escalation a (id, action) pair."""
    actions = {"released": ["a"], "held": ["b"], "submitted": ["c"],
               "release_skipped": [{"id": "d", "why": "no"}],
               "escalations_answered": [("e", "answer")], "ideas_added": 3}
    assert touched_sprints(actions) == ["a", "b", "c", "e", "d"]


def test_a_sprint_touched_twice_is_listed_once(substrate):
    assert touched_sprints({"released": ["a"], "submitted": ["a", "b"]}) == ["a", "b"]


def test_an_unfamiliar_action_shape_does_not_take_down_the_cycle(substrate):
    """Annotating a cycle must never be the thing that fails it."""
    assert touched_sprints({"released": [None, 7, {"no_id": 1}, ""]}) == []


def test_the_touched_sprints_carry_the_cycles_words(substrate):
    for sid in ("p1-c4", "p1-c9"):
        substrate.save_sprint(Sprint(id=sid, status=SprintStatus.APPROVED, goals="g",
                                     plan=["x"], program="p1"))
    written = record_sprint_notes(substrate, REPORT,
                                  {"released": ["p1-c4"], "held": ["p1-c9"]},
                                  cycle=7, at=1000.0)

    assert sorted(written) == ["p1-c4", "p1-c9"]
    note = substrate.load_sprint("p1-c4").pm_notes[-1]
    assert (note["cycle"], note["at"]) == (7, 1000.0)
    assert "docking baseline" in note["text"]


def test_notes_accumulate_across_cycles_and_stay_bounded(substrate):
    substrate.save_sprint(Sprint(id="p1-c4", status=SprintStatus.APPROVED, goals="g",
                                 plan=["x"], program="p1"))
    for cycle in range(SPRINT_NOTES_KEPT + 4):
        record_sprint_notes(substrate, f"Released p1-c4 on cycle {cycle}.",
                            {"released": ["p1-c4"]}, cycle=cycle, at=float(cycle))

    notes = substrate.load_sprint("p1-c4").pm_notes
    assert len(notes) == SPRINT_NOTES_KEPT
    assert notes[-1]["cycle"] == SPRINT_NOTES_KEPT + 3      # the newest is kept
    assert notes[0]["cycle"] == 4                            # the oldest is dropped


def test_one_note_per_sprint_per_cycle(substrate):
    substrate.save_sprint(Sprint(id="p1-c4", status=SprintStatus.APPROVED, goals="g",
                                 plan=["x"], program="p1"))
    record_sprint_notes(substrate, REPORT, {"released": ["p1-c4"]}, cycle=1, at=1.0)
    record_sprint_notes(substrate, REPORT, {"held": ["p1-c4"]}, cycle=1, at=2.0)
    assert len(substrate.load_sprint("p1-c4").pm_notes) == 1


def test_a_sprint_that_is_gone_does_not_stop_the_cycle(substrate):
    """Ids come from the planner's output; one can name a sprint that was dropped."""
    assert record_sprint_notes(substrate, "Released ghost-c1 today.",
                               {"released": ["ghost-c1"]}, cycle=1, at=1.0) == []


def test_notes_survive_a_round_trip_through_the_substrate(substrate):
    sp = Sprint(id="p1-c4", status=SprintStatus.APPROVED, goals="g", plan=["x"],
                program="p1", pm_notes=[{"cycle": 2, "at": 5.0, "text": "because"}])
    substrate.save_sprint(sp)
    assert substrate.load_sprint("p1-c4").pm_notes == [{"cycle": 2, "at": 5.0,
                                                        "text": "because"}]
