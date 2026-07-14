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


def test_demoted_idea_is_not_promotable(substrate):
    # A demoted idea must not be promoted into a sprint, even if the reasoner tries.
    _prog(substrate)
    substrate.save_ideas("p1", "", [Idea(id="seed", text="dead end", source="human", demoted=True)])
    out = PMCycleOutput(proposals=[_prop("from-seed", from_idea="seed")])
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == []                           # promotion blocked
    assert not (substrate.sprint_dir("p1-c0-from-seed") / "sprint.md").is_file()
    _s, ideas = substrate.load_ideas("p1")
    assert [i.id for i in ideas] == ["seed"]                    # idea stays in the pool
    assert ideas[0].protected is True                           # and PM can't delete it


def test_promotion_creates_sprint_and_removes_idea(substrate):
    _prog(substrate)
    substrate.save_ideas("p1", "", [Idea(id="seed", text="big idea", source="pm")])
    out = PMCycleOutput(proposals=[_prop("from-seed", from_idea="seed")])
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == ["p1-c0-from-seed"]
    assert substrate.load_sprint("p1-c0-from-seed").status == SprintStatus.PROPOSED
    _summary, ideas = substrate.load_ideas("p1")
    assert ideas == []                                  # the seed left the pool


def test_activation_log_records_trigger_and_submitted(substrate):
    _prog(substrate)
    # first cycle -> labelled "first cycle"
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(proposals=[_prop("a")], report="r")]))
    acts = substrate.load_pm_state("p1").activations
    assert len(acts) == 1
    assert acts[0]["triggers"] == ["first cycle"]
    assert acts[0]["submitted"] == ["p1-c0-a"]
    # add guidance -> next activation names guidance as the trigger
    from coscience import threads as _threads
    substrate.save_guidance("p1", [_threads.new_thread("pm", "focus on X", "u", now=1.0, tid="g1")])
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r2")]))
    acts = substrate.load_pm_state("p1").activations
    assert len(acts) == 2
    assert "guidance changed" in acts[1]["triggers"]


def test_activation_log_marks_manual_replan(substrate):
    _prog(substrate)
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r0")]))
    # nothing changed, but forced -> recorded as a manual replan
    out = pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r1")]), force=True)
    assert not out.get("skipped")
    acts = substrate.load_pm_state("p1").activations
    assert acts[-1]["triggers"] == ["manual replan"] and acts[-1]["forced"] is True


def test_force_reasons_even_when_nothing_changed(substrate):
    # "Replan now": an explicit human replan must reason even if the fingerprint is
    # unchanged (a normal beat would skip).
    _prog(substrate)
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r0")]))
    fake = FakeReasoner([PMCycleOutput(report="r1")])
    assert pm_beat(substrate, "p1", fake)["skipped"]           # unchanged -> skip
    fake2 = FakeReasoner([PMCycleOutput(report="r2")])
    out = pm_beat(substrate, "p1", fake2, force=True)          # forced -> reasons
    assert not out.get("skipped")
    assert len(fake2.calls) == 1


def test_concurrent_beat_returns_busy(substrate):
    # While one beat holds the per-program lock, a second returns a busy skip rather
    # than racing the staging commit (this is what makes on-demand replan safe).
    from coscience.pm_agent import _acquire_program_lock, _release_program_lock
    _prog(substrate)
    held = _acquire_program_lock(substrate, "p1")
    try:
        out = pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="x")]), force=True)
        assert out["busy"] is True and out["skipped"] is True
    finally:
        _release_program_lock(held)
    # lock free again -> a beat runs normally
    assert not pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="y")]), force=True).get("busy")


def test_human_idea_retriggers_pm(substrate):
    # An idle PM (unchanged fingerprint) must wake when a human adds an idea.
    _prog(substrate)
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r0")]))
    # nothing changed -> skipped
    assert pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r1")]))["skipped"]
    substrate.save_ideas("p1", "", [Idea(id="h", text="new human lead", source="human")])
    # human idea changes the fingerprint -> PM runs again
    assert not pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r2")]))["skipped"]


def test_context_surfaces_failed_sprints(substrate):
    from coscience.models import ProgressState
    _prog(substrate)
    substrate.save_sprint(Sprint(id="p1-f", status=SprintStatus.FAILED, goals="do x",
                                 plan=[], program="p1"))
    substrate.save_progress(ProgressState(sprint_id="p1-f", failures=3,
                                          last_error="ImportError: no sympy"))
    ctx = gather_context(substrate, "p1")
    assert ctx.failed == [{"id": "p1-f", "goals": "do x", "error": "ImportError: no sympy"}]


def test_failed_sprint_retriggers_pm(substrate):
    from coscience.models import ProgressState
    _prog(substrate)
    pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r0")]))
    assert pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r1")]))["skipped"]
    substrate.save_sprint(Sprint(id="p1-f", status=SprintStatus.FAILED, goals="g",
                                 plan=[], program="p1"))
    substrate.save_progress(ProgressState(sprint_id="p1-f", last_error="boom"))
    assert not pm_beat(substrate, "p1", FakeReasoner([PMCycleOutput(report="r2")]))["skipped"]


def _with_pm_note(sid, status, goals="g"):
    from coscience import threads
    s = Sprint(id=sid, status=status, goals=goals, plan=["a"], program="p1")
    s.threads.append(threads.new_thread("pm", "rework this", "u", now=1.0))
    return s


def test_pm_edits_proposed_sprint_from_feedback(substrate):
    _prog(substrate)
    substrate.save_sprint(_with_pm_note("p1-s", SprintStatus.PROPOSED, goals="old"))
    out = PMCycleOutput(sprint_edits=[{"sprint_id": "p1-s", "goals": "new goal", "plan": ["x", "y"]}])
    pm_beat(substrate, "p1", FakeReasoner([out]))
    sp = substrate.load_sprint("p1-s")
    assert sp.goals == "new goal" and sp.plan == ["x", "y"]


def test_pm_cannot_edit_once_approved(substrate):
    _prog(substrate)
    substrate.save_sprint(_with_pm_note("p1-s", SprintStatus.APPROVED, goals="locked"))
    out = PMCycleOutput(sprint_edits=[{"sprint_id": "p1-s", "goals": "hacked"}])
    pm_beat(substrate, "p1", FakeReasoner([out]))
    assert substrate.load_sprint("p1-s").goals == "locked"   # spec locked after approval


def test_context_surfaces_pm_feedback_only(substrate):
    from coscience import threads
    _prog(substrate)
    s = Sprint(id="p1-s", status=SprintStatus.PROPOSED, goals="g", plan=[], program="p1")
    s.threads.append(threads.new_thread("pm", "extend it", "u", now=1.0))
    s.threads.append(threads.new_thread("worker", "agent note", "u", now=2.0))
    substrate.save_sprint(s)
    fb = gather_context(substrate, "p1").sprint_feedback
    assert len(fb) == 1
    assert fb[0]["sprint_id"] == "p1-s" and fb[0]["editable"] is True
    assert fb[0]["messages"] == [{"role": "human", "text": "extend it"}]   # worker thread excluded


def test_context_exposes_pool_and_slots(substrate):
    _prog(substrate)
    _proposed(substrate, 1)
    substrate.save_ideas("p1", "", [Idea(id="a", text="lead", source="human")])
    ctx = gather_context(substrate, "p1")
    assert ctx.proposed_count == 1
    assert ctx.max_proposed == MAX_PROPOSED
    assert ctx.free_slots == MAX_PROPOSED - 1
    assert ctx.ideas[0]["protected"] is True
