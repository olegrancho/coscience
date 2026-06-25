from coscience.pm_agent import (StagedCycle, clear_staging, proposal_id,
                                read_staging, write_staging)
from coscience.pm_reasoner import PMCycleOutput, ProposedSprint


def test_proposal_id_format():
    assert proposal_id("p1", 3, "assay") == "p1-c3-assay"


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
