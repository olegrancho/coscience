from coscience import artifacts
from coscience.models import Sprint, SprintStatus


def _sprint(**kw):
    return Sprint(id=kw.pop("id", "s1"), status=SprintStatus.EXECUTING,
                  goals="g", program=kw.pop("program", "p"), **kw)


def test_sprint_aids_bound_plus_create():
    s = _sprint(artifacts_bound=["a"], artifacts_create=[{"aid": "b", "kind": "md"}])
    assert artifacts.sprint_aids(s) == ["a", "b"]


def test_acquire_for_sprint_creates_targets_and_locks(substrate):
    artifacts.create_artifact(substrate, "p", "a", "A", "md")
    s = _sprint(artifacts_bound=["a"], artifacts_create=[{"aid": "b", "title": "B", "kind": "figure"}])
    ok = artifacts.acquire_for_sprint(substrate, s, now=1.0)
    assert ok is True
    # create-target instantiated with its kind, and both locked to this sprint
    b = substrate.load_artifact("p", "b")
    assert b.kind == "figure"
    assert substrate.load_artifact("p", "a").lock["holder_id"] == "s1"
    assert b.lock["holder_id"] == "s1"
    assert (substrate.artifact_dir("p", "a") / "work").is_dir()


def test_acquire_for_sprint_blocked_returns_false_and_locks_none(substrate):
    artifacts.create_artifact(substrate, "p", "a", "A", "md")
    artifacts.acquire_lock(substrate, "p", ["a"], "chat", "chat:x", now=0.0)   # busy
    s = _sprint(artifacts_bound=["a"], artifacts_create=[{"aid": "b", "kind": "md"}])
    ok = artifacts.acquire_for_sprint(substrate, s, now=1.0)
    assert ok is False
    # 'a' stays with the chat; 'b' was instantiated but NOT locked (all-or-none)
    assert substrate.load_artifact("p", "a").lock["holder_id"] == "chat:x"
    assert substrate.load_artifact("p", "b").lock == {}


def test_release_for_sprint_cuts_versions_and_unlocks(substrate):
    artifacts.create_artifact(substrate, "p", "a", "A", "md")
    s = _sprint(artifacts_bound=["a"])
    artifacts.acquire_for_sprint(substrate, s, now=1.0)
    (substrate.artifact_dir("p", "a") / "work" / "c.md").write_text("done")
    vids = artifacts.release_for_sprint(substrate, s, now=2.0)
    assert vids == ["v1"]
    a = substrate.load_artifact("p", "a")
    assert a.lock == {}
    assert a.current == "v1"


def test_sprint_blocked_detects_other_holder(substrate):
    artifacts.create_artifact(substrate, "p", "a", "A", "md")
    s = _sprint(artifacts_bound=["a"])
    assert artifacts.sprint_blocked(substrate, s) is False
    artifacts.acquire_lock(substrate, "p", ["a"], "chat", "chat:x", now=0.0)
    assert artifacts.sprint_blocked(substrate, s) is True
    # held by itself -> not blocked
    s2 = _sprint(id="chat:x")   # holder id matches
    assert artifacts.sprint_blocked(substrate, s2) is False


def test_helpers_noop_without_program():
    s = _sprint(program=None, artifacts_bound=["a"])
    assert artifacts.sprint_blocked(None, s) is False
    # acquire/release short-circuit before touching substrate
    assert artifacts.acquire_for_sprint(None, s, now=1.0) is True
    assert artifacts.release_for_sprint(None, s, now=1.0) == []
