import pytest

from coscience.models import Program, SprintStatus
from coscience.pm_agent import pm_beat, read_staging, write_staging
from coscience.pm_reasoner import PMCycleOutput, ProposedSprint


class BoomReasoner:
    """Fails if run() is called — proves resume does not re-reason."""
    def run(self, context):
        raise AssertionError("reasoner must not be called on resume")


def _out(suffix="a", report="r"):
    return PMCycleOutput(
        proposals=[ProposedSprint(suffix=suffix, goals="do " + suffix,
                                  plan=[{"id": "s", "run": "true"}], priority=1)],
        report=report)


def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def test_fresh_beat_proposes_and_reports(substrate):
    _prog(substrate)
    from coscience.pm_reasoner import FakeReasoner
    summary = pm_beat(substrate, "p1", FakeReasoner([_out("a", "report-0")]))

    assert summary["submitted"] == ["p1-c0-a"]
    sprint = substrate.load_sprint("p1-c0-a")
    assert sprint.status == SprintStatus.PROPOSED      # propose-only
    assert sprint.program == "p1"
    assert sprint.priority == 1
    assert "report-0" in substrate.load_report("p1")
    pm = substrate.load_pm_state("p1")
    assert pm.cycle == 1                                # bumped
    assert pm.proposed_ids == ["p1-c0-a"]
    assert read_staging(substrate, "p1") is None        # cleared


def test_beat_tolerates_non_numeric_resources(substrate):
    # The LLM sometimes emits prose in resources_required (e.g. a wall-clock note).
    # The beat must coerce — keep numeric amounts, drop the rest — not crash.
    _prog(substrate)
    from coscience.pm_reasoner import FakeReasoner
    out = PMCycleOutput(proposals=[ProposedSprint(
        suffix="x", goals="g", plan=[{"id": "s", "run": "true"}],
        resources_required={"gpu": 2, "note": "CPU-bound; ~30 min wall clock"})])
    pm_beat(substrate, "p1", FakeReasoner([out]))
    assert substrate.load_sprint("p1-c0-x").resources_required == {"gpu": 2.0}


def test_second_beat_uses_next_cycle(substrate):
    _prog(substrate)
    from coscience.pm_reasoner import FakeReasoner
    fake = FakeReasoner([_out("a"), _out("b")])
    pm_beat(substrate, "p1", fake)
    substrate.save_program(Program(id="p1", title="C", goals="cure FAST"))  # goal edit -> re-reason
    summary = pm_beat(substrate, "p1", fake)
    assert summary["submitted"] == ["p1-c1-b"]
    assert substrate.load_pm_state("p1").cycle == 2
    assert substrate.load_pm_state("p1").proposed_ids == ["p1-c0-a", "p1-c1-b"]


def test_beat_skips_when_nothing_changed(substrate):
    # The core of event-driven: with no new input, the reasoner is NOT called and
    # no proposals pile up.
    _prog(substrate)
    from coscience.pm_reasoner import FakeReasoner
    pm_beat(substrate, "p1", FakeReasoner([_out("a")]))   # cycle 0: proposes, records fingerprint
    summary = pm_beat(substrate, "p1", BoomReasoner())     # would raise if it reasoned
    assert summary["skipped"] is True
    assert summary["submitted"] == []
    assert substrate.load_pm_state("p1").cycle == 1        # no new cycle


def test_beat_reasons_again_after_a_result_lands(substrate):
    from coscience.models import Result
    from coscience.pm_reasoner import FakeReasoner
    _prog(substrate)
    pm_beat(substrate, "p1", FakeReasoner([_out("a")]))
    # a worker finishes the sprint and writes a result -> input changed
    sp = substrate.load_sprint("p1-c0-a")
    sp.status = SprintStatus.DONE
    sp.results = ["r1"]
    substrate.save_sprint(sp)
    substrate.save_result(Result(id="r1", sprint="p1-c0-a", summary="found"))
    summary = pm_beat(substrate, "p1", FakeReasoner([_out("b")]))
    assert summary["skipped"] is False
    assert summary["submitted"] == ["p1-c1-b"]


def test_beat_reasons_again_after_idea_comment(substrate):
    # Regression: a human comment on an idea must re-trigger the PM. gather_context
    # flattens idea comments to plain strings, so context_fingerprint must index
    # them as strings — it previously did comment["text"] and raised TypeError,
    # crashing every beat (the PM froze silently the moment any idea got a comment).
    from coscience.models import Idea
    from coscience.pm_reasoner import FakeReasoner
    _prog(substrate)
    pm_beat(substrate, "p1", FakeReasoner([_out("a")]))    # records fingerprint (no comments)
    substrate.save_ideas("p1", "", [Idea(id="i1", text="try X", source="pm",
        comments=[{"id": "c1", "text": "promising — pursue it", "added_at": 1.0}])])
    summary = pm_beat(substrate, "p1", FakeReasoner([_out("b")]))
    assert summary["skipped"] is False
    assert summary["submitted"] == ["p1-c1-b"]


def test_pm_reopens_approved_only(substrate):
    # The PM may pull an APPROVED sprint back to PROPOSED (obsolete), but must not
    # touch a QUEUED one (a human deliberately released it).
    from coscience.models import Sprint
    from coscience.pm_reasoner import FakeReasoner
    _prog(substrate)
    substrate.save_sprint(Sprint(id="p1-appr", status=SprintStatus.APPROVED,
                                 goals="g", plan=["x"], program="p1"))
    substrate.save_sprint(Sprint(id="p1-queued", status=SprintStatus.QUEUED,
                                 goals="g", plan=["x"], program="p1"))
    out = PMCycleOutput(report="r", reopen_ids=["p1-appr", "p1-queued"])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    assert substrate.load_sprint("p1-appr").status == SprintStatus.PROPOSED
    assert substrate.load_sprint("p1-queued").status == SprintStatus.QUEUED  # untouched


def test_rerun_same_cycle_is_idempotent(substrate):
    # Stage a cycle, then run twice from the same staged state (simulating a
    # crash before clear). The reasoner must NOT be called, and no duplicate.
    _prog(substrate)
    write_staging(substrate, "p1", 0, _out("a", "staged-report"))
    s1 = pm_beat(substrate, "p1", BoomReasoner())   # resumes from staging
    assert s1["submitted"] == ["p1-c0-a"]
    # Re-stage the same cycle 0 (as if the bump didn't persist) and re-run:
    write_staging(substrate, "p1", 0, _out("a", "staged-report"))
    s2 = pm_beat(substrate, "p1", BoomReasoner())
    assert s2["submitted"] == []                     # already exists -> skipped
    assert len([s for s in substrate.iter_sprints() if s.id == "p1-c0-a"]) == 1


def test_resume_after_cycle_bump_does_not_shift_ids(substrate):
    # Simulate: staged cycle 0 fully applied + pm.cycle already bumped to 1,
    # but staging not yet cleared. Resume must replay cycle-0 ids, not cycle-1.
    _prog(substrate)
    from coscience.models import PMState
    write_staging(substrate, "p1", 0, _out("a"))
    substrate.save_pm_state(PMState(program_id="p1", cycle=1,
                                    proposed_ids=["p1-c0-a"]))
    summary = pm_beat(substrate, "p1", BoomReasoner())
    assert summary["cycle"] == 0
    assert summary["submitted"] == []                # p1-c0-a already proposed
    assert substrate.load_sprint("p1-c0-a").status == SprintStatus.PROPOSED
    assert read_staging(substrate, "p1") is None
