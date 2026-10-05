"""K3: planner sprints get a per-program number, `<program>-s<n>-<suffix>`, so no two
sprints in a program share a short form — the old `-c<cycle>-` gave every proposal of
one cycle the same one."""
from coscience.models import Program, Sprint, SprintStatus
from coscience.pm_agent import (SprintIds, pm_beat,
                                proposal_suffix, write_staging)
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint


def _out(*suffixes):
    return PMCycleOutput(report="r", proposals=[
        ProposedSprint(suffix=s, goals="do " + s, plan=["x"], priority=1) for s in suffixes])


def _prog(substrate, cap=10):
    substrate.save_program(Program(id="p1", title="C", goals="g", max_proposed=cap))


def test_one_cycle_numbers_each_proposal_apart(substrate):
    _prog(substrate)
    summary = pm_beat(substrate, "p1", FakeReasoner([_out("a", "b", "c")]))
    assert summary["submitted"] == ["p1-s1-a", "p1-s2-b", "p1-s3-c"]
    assert substrate.load_sprint("p1-s2-b").proposed_cycle == 0


def test_numbers_start_above_the_legacy_cycle_ids(substrate):
    _prog(substrate)
    for sid in ("p1-c41-old", "p1-c7-older", "p1-manual-one"):
        substrate.save_sprint(Sprint(id=sid, status=SprintStatus.DONE, goals="g",
                                     plan=["x"], program="p1"))
    assert SprintIds(substrate, "p1").mint(50, "new") == "p1-s42-new"


def test_reapplying_a_staged_cycle_finds_what_it_made(substrate):
    # A crash after the sprints were written but before staging was cleared: the
    # resumed apply must name the same sprints, not mint fresh numbers for them.
    _prog(substrate)
    write_staging(substrate, "p1", 3, _out("a", "b"))
    first = SprintIds(substrate, "p1")
    ids = [first.mint(3, "a"), first.mint(3, "b")]
    for sid in ids:
        substrate.save_sprint(Sprint(id=sid, status=SprintStatus.PROPOSED, goals="g",
                                     plan=["x"], program="p1", proposed_cycle=3))
    again = SprintIds(substrate, "p1")
    assert [again.mint(3, "a"), again.mint(3, "b")] == ids
    assert again.mint(4, "a") == "p1-s3-a"        # same suffix, later cycle: a new sprint


def test_a_cycle_staged_before_the_change_keeps_its_legacy_ids(substrate):
    _prog(substrate)
    substrate.save_sprint(Sprint(id="p1-c9-a", status=SprintStatus.PROPOSED, goals="g",
                                 plan=["x"], program="p1"))
    assert SprintIds(substrate, "p1").mint(9, "a") == "p1-c9-a"


def test_an_echoed_prefix_is_stripped():
    assert proposal_suffix("p1", "p1-s14-foo") == "foo"
    assert proposal_suffix("p1", "c2-s3-bar") == "bar"
