from coscience.frontmatter_io import parse
from coscience.models import Program
from coscience.substrate import Substrate


def test_max_proposed_defaults_to_unset(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x"))
    assert sub.load_program("p1").max_proposed == 0


def test_max_proposed_round_trips(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x", max_proposed=7))
    assert sub.load_program("p1").max_proposed == 7


def test_unset_max_proposed_writes_no_frontmatter_key(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x"))
    fm, _ = parse((sub.program_dir("p1") / "program.md").read_text())
    assert "max_proposed" not in fm


from coscience.models import Sprint, SprintStatus
from coscience.pm_agent import MAX_PROPOSED, gather_context, pm_beat, write_staging
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint


def _prop(suffix):
    return ProposedSprint(suffix=suffix, goals="do " + suffix, plan=["step"])


def test_context_uses_the_global_default_when_unset(substrate):
    substrate.save_program(Program(id="p1", title="A", goals="x"))
    assert gather_context(substrate, "p1").max_proposed == MAX_PROPOSED


def test_context_uses_the_program_cap_when_set(substrate):
    substrate.save_program(Program(id="p1", title="A", goals="x", max_proposed=2))
    ctx = gather_context(substrate, "p1")
    assert ctx.max_proposed == 2 and ctx.free_slots == 2


def test_apply_enforces_the_program_cap(substrate):
    substrate.save_program(Program(id="p1", title="A", goals="x", max_proposed=1))
    out = PMCycleOutput(proposals=[_prop("a"), _prop("b")], report="r")
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == ["p1-c0-a"]
    assert summary["dropped"] == ["p1-c0-b"]


def test_cap_holds_on_a_resumed_staged_cycle(substrate):
    """The apply path has no PMContext when it resumes a staged cycle — it must
    load the program itself rather than falling back to the global constant."""
    substrate.save_program(Program(id="p1", title="A", goals="x", max_proposed=1))
    out = PMCycleOutput(proposals=[_prop("a"), _prop("b")], report="r")
    write_staging(substrate, "p1", 0, out)                 # already reasoned; only apply remains
    summary = pm_beat(substrate, "p1", FakeReasoner([]))   # the reasoner must not be consulted
    assert summary["submitted"] == ["p1-c0-a"]
    assert summary["dropped"] == ["p1-c0-b"]


def test_program_cap_below_the_existing_queue_proposes_nothing(substrate):
    substrate.save_program(Program(id="p1", title="A", goals="x", max_proposed=1))
    substrate.save_sprint(Sprint(id="p1-old", status=SprintStatus.PROPOSED,
                                 goals="g", plan=[], program="p1"))
    out = PMCycleOutput(proposals=[_prop("a")], report="r")
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == [] and summary["dropped"] == ["p1-c0-a"]
