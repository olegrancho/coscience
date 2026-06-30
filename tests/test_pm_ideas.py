from coscience.models import Idea, Program, Sprint, SprintStatus
from coscience.pm_agent import MAX_PROPOSED, gather_context, pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint


def _prog(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure"))


def _proposed(substrate, n):
    for i in range(n):
        substrate.save_sprint(Sprint(id=f"p1-h{i}", status=SprintStatus.PROPOSED,
                                     goals="g", plan=[], program="p1"))


def _prop(suffix, **kw):
    return ProposedSprint(suffix=suffix, goals="do " + suffix, plan=["step"], **kw)


# --- sprint cap (PM-gated) ---

def test_cap_blocks_proposing_when_full(substrate):
    _prog(substrate)
    _proposed(substrate, MAX_PROPOSED)                 # already at the cap
    out = PMCycleOutput(proposals=[_prop("new")], report="r")
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == []
    assert summary["dropped"] == ["p1-c0-new"]
    assert not (substrate.sprint_dir("p1-c0-new") / "sprint.md").is_file()


def test_cap_allows_only_free_slots(substrate):
    _prog(substrate)
    _proposed(substrate, MAX_PROPOSED - 1)             # one slot free
    out = PMCycleOutput(proposals=[_prop("a"), _prop("b")], report="r")
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == ["p1-c0-a"]         # only the first fits
    assert summary["dropped"] == ["p1-c0-b"]


# --- idea pool ---

def test_new_ideas_are_recorded_as_pm_source(substrate):
    _prog(substrate)
    out = PMCycleOutput(new_ideas=["try a wheel sieve", "look at OEIS tables"],
                        ideas_summary="two leads on faster enumeration")
    pm_beat(substrate, "p1", FakeReasoner([out]))
    summary, ideas = substrate.load_ideas("p1")
    assert summary == "two leads on faster enumeration"
    assert sorted(i.text for i in ideas) == ["look at OEIS tables", "try a wheel sieve"]
    assert all(i.source == "pm" for i in ideas)


def test_new_ideas_not_duplicated_by_text(substrate):
    _prog(substrate)
    substrate.save_ideas("p1", "", [Idea(id="x", text="dup", source="pm")])
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(new_ideas=["dup"])]))
    _summary, ideas = substrate.load_ideas("p1")
    assert [i.text for i in ideas] == ["dup"]          # no duplicate added


def test_pm_deletes_only_unprotected_own_ideas(substrate):
    _prog(substrate)
    substrate.save_ideas("p1", "", [
        Idea(id="a", text="prunable", source="pm"),
        Idea(id="b", text="pinned", source="pm", pinned=True),
        Idea(id="c", text="human", source="human"),
    ])
    out = PMCycleOutput(delete_idea_ids=["a", "b", "c"])
    pm_beat(substrate, "p1", FakeReasoner([out]))
    _summary, ideas = substrate.load_ideas("p1")
    assert sorted(i.id for i in ideas) == ["b", "c"]   # only 'a' pruned


def test_promotion_creates_sprint_and_removes_idea(substrate):
    _prog(substrate)
    substrate.save_ideas("p1", "", [Idea(id="seed", text="big idea", source="pm")])
    out = PMCycleOutput(proposals=[_prop("from-seed", from_idea="seed")])
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == ["p1-c0-from-seed"]
    assert substrate.load_sprint("p1-c0-from-seed").status == SprintStatus.PROPOSED
    _summary, ideas = substrate.load_ideas("p1")
    assert ideas == []                                  # the seed left the pool


def test_human_idea_retriggers_pm(substrate):
    # An idle PM (unchanged fingerprint) must wake when a human adds an idea.
    _prog(substrate)
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r0")]))
    # nothing changed -> skipped
    assert pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r1")]))["skipped"]
    substrate.save_ideas("p1", "", [Idea(id="h", text="new human lead", source="human")])
    # human idea changes the fingerprint -> PM runs again
    assert not pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r2")]))["skipped"]


def test_context_exposes_pool_and_slots(substrate):
    _prog(substrate)
    _proposed(substrate, 1)
    substrate.save_ideas("p1", "", [Idea(id="a", text="lead", source="human")])
    ctx = gather_context(substrate, "p1")
    assert ctx.proposed_count == 1
    assert ctx.max_proposed == MAX_PROPOSED
    assert ctx.free_slots == MAX_PROPOSED - 1
    assert ctx.ideas[0]["protected"] is True
