from coscience.pm_agent import (StagedCycle, clear_staging, proposal_id,
                                read_staging, write_staging)
from coscience.pm_reasoner import PMCycleOutput, ProposedSprint


def test_proposal_id_format():
    assert proposal_id("p1", 3, "assay") == "p1-c3-assay"


def test_proposal_id_strips_doubled_prefixes():
    # the model sometimes echoes the cycle and/or program prefix into the suffix
    assert proposal_id("p1", 2, "c2-heuristic-gap-forecast") == "p1-c2-heuristic-gap-forecast"
    assert proposal_id("p1", 3, "p1-c3-land-literature-rungs") == "p1-c3-land-literature-rungs"
    assert proposal_id("p1", 4, "p1-foo") == "p1-c4-foo"
    assert proposal_id("p1", 5, "  c5-bar  ") == "p1-c5-bar"


def test_staging_roundtrip_carries_cycle(substrate):
    out = PMCycleOutput(
        proposals=[ProposedSprint(suffix="a", goals="g", plan=[{"id": "s", "run": "true"}],
                                  priority=2, resources_required={"gpu": 1.0})],
        report="the report")
    assert read_staging(substrate, "p1") is None
    write_staging(substrate, "p1", 5, out)
    staged = read_staging(substrate, "p1")
    assert staged == StagedCycle(cycle=5, output=out)


def test_clear_staging(substrate):
    write_staging(substrate, "p1", 0, PMCycleOutput(report="r"))
    clear_staging(substrate, "p1")
    assert read_staging(substrate, "p1") is None
    clear_staging(substrate, "p1")  # no-op, no error
