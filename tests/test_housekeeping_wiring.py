"""The PM and wiki beats actually take a housekeeping slot.

G1's point is not that a slot pool exists but that the two uncontrolled call
sites respect it. These pin that a second launcher is refused while the first
holds the only slot, and — just as important — that the slot comes back, since a
pool that leaks is a platform that stops doing housekeeping entirely."""

from __future__ import annotations

import json

import pytest

from coscience import housekeeping, wiki, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus
from tests.test_wiki_beat import FakeWikiAgent


@pytest.fixture
def agent2():
    return FakeWikiAgent()


def _cap(substrate, n=1.0):
    d = substrate.repo_root / ".coscience"
    d.mkdir(parents=True, exist_ok=True)
    (d / "resources.yaml").write_text(f"workers: 1.0\nhousekeepers: {n}\n")


def _seed(substrate, pid, rid, sid):
    substrate.save_program(Program(id=pid, title="P", goals="g"))
    substrate.save_sprint(Sprint(id=sid, status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id=rid, sprint=sid, summary="s", completed_at=1.0))


def test_one_wiki_run_launches_and_the_second_program_waits(substrate, agent2):
    """Two programs both due an ingest, one slot: exactly one starts."""
    _cap(substrate, 1.0)
    _seed(substrate, "p1", "r1", "s1")
    _seed(substrate, "p2", "r2", "s2")

    lines = [wiki.beat(substrate, substrate.load_program(p), 100.0, agent2)
             for p in ("p1", "p2")]

    assert sum(1 for l in lines if "launched" in l) == 1
    assert len(agent2.launches) == 1
    assert len(housekeeping.held(substrate.repo_root)) == 1


def test_both_launch_when_the_pool_has_two_slots(substrate, agent2):
    _cap(substrate, 2.0)
    _seed(substrate, "p1", "r1", "s1")
    _seed(substrate, "p2", "r2", "s2")

    for p in ("p1", "p2"):
        wiki.beat(substrate, substrate.load_program(p), 100.0, agent2)

    assert len(agent2.launches) == 2


def test_collecting_a_run_gives_the_slot_back(substrate, agent2):
    _cap(substrate, 1.0)
    _seed(substrate, "p1", "r1", "s1")
    program = substrate.load_program("p1")
    wiki.beat(substrate, program, 100.0, agent2)
    run_dir = agent2.launches[0]["run_dir"]
    (run_dir / "agent.exit").write_text("0")
    (run_dir / "report.json").write_text(json.dumps({"objects": ["result:r1"]}))
    agent2.alive = False

    wiki.beat(substrate, program, 160.0, agent2)

    assert housekeeping.held(substrate.repo_root) == []


def test_a_failed_run_also_gives_the_slot_back(substrate, agent2):
    """The 08-30..09-01 shape. A slot leaked on the failure path would stop the
    wiki permanently after enough 429s — worse than the problem being fixed."""
    _cap(substrate, 1.0)
    _seed(substrate, "p1", "r1", "s1")
    program = substrate.load_program("p1")
    wiki.beat(substrate, program, 100.0, agent2)
    (agent2.launches[0]["run_dir"] / "agent.exit").write_text("1")
    agent2.alive = False

    wiki.beat(substrate, program, 160.0, agent2)

    assert housekeeping.held(substrate.repo_root) == []


def test_an_uncapped_substrate_behaves_exactly_as_before(substrate, agent2):
    """No `housekeepers` key: every program launches, as it always did."""
    _seed(substrate, "p1", "r1", "s1")
    _seed(substrate, "p2", "r2", "s2")
    for p in ("p1", "p2"):
        wiki.beat(substrate, substrate.load_program(p), 100.0, agent2)
    assert len(agent2.launches) == 2


def test_a_wiki_run_does_not_block_a_pm_cycle_when_slots_allow(substrate, agent2):
    _cap(substrate, 2.0)
    _seed(substrate, "p1", "r1", "s1")
    wiki.beat(substrate, substrate.load_program("p1"), 100.0, agent2)

    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner
    from tests.test_pm_beat import _out
    pm_beat(substrate, "p1", FakeReasoner([_out("a", "report-0")]), now=101.0)

    from coscience import usage_meter
    kinds = {c["kind"] for c in usage_meter.calls(substrate.repo_root)}
    assert "pm" in kinds


def test_a_pm_cycle_is_refused_while_the_only_slot_is_held(substrate, agent2):
    _cap(substrate, 1.0)
    _seed(substrate, "p1", "r1", "s1")
    wiki.beat(substrate, substrate.load_program("p1"), 100.0, agent2)

    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner
    from tests.test_pm_beat import _out
    # Same clock as the wiki beat above: a slot carries a TTL, so passing
    # wall-clock here would expire the wiki's lease before the PM even looked.
    reasoner = FakeReasoner([_out("a", "report-0")])
    pm_beat(substrate, "p1", reasoner, now=101.0)

    from coscience import usage_meter
    kinds = {c["kind"] for c in usage_meter.calls(substrate.repo_root)}
    assert "pm" not in kinds, "the PM must not reason while the slot is taken"


def test_the_pm_releases_its_slot_after_reasoning(substrate):
    _cap(substrate, 1.0)
    _seed(substrate, "p1", "r1", "s1")

    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner
    from tests.test_pm_beat import _out
    pm_beat(substrate, "p1", FakeReasoner([_out("a", "report-0")]))

    assert housekeeping.held(substrate.repo_root) == []


def test_the_pm_releases_its_slot_even_when_the_reasoner_raises(substrate):
    _cap(substrate, 1.0)
    _seed(substrate, "p1", "r1", "s1")

    from coscience.pm_agent import pm_beat

    class Boom:
        model = "claude-opus-5"
        last_cost = None
        last_prompt_bytes = 10

        def run(self, ctx):
            raise RuntimeError("bad json")

    with pytest.raises(Exception):
        pm_beat(substrate, "p1", Boom())

    assert housekeeping.held(substrate.repo_root) == []


def test_a_refused_pm_beat_returns_a_result_shape_not_a_string(substrate, agent2):
    """Regression, found in production 2026-09-04: a bare string here killed the
    PM loop with `string indices must be integers` on the first refusal."""
    _cap(substrate, 1.0)
    _seed(substrate, "p1", "r1", "s1")
    wiki.beat(substrate, substrate.load_program("p1"), 100.0, agent2)

    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner
    from tests.test_pm_beat import _out
    got = pm_beat(substrate, "p1", FakeReasoner([_out("a", "report-0")]), now=101.0)

    assert isinstance(got, dict)
    assert got["skipped"] is True and got["program"] == "p1"
    assert got["proposed"] == [] and got["submitted"] == []
