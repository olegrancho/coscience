from tests.conftest import FakeAgent

from coscience import artifacts
from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _disp(substrate, capacity, agent=None):
    return Dispatcher(substrate, agent or FakeAgent(), ResourcePool(capacity),
                      SchedulerPolicy(aging_interval=0.0))


def _queued(substrate, sid, program, bound=None, create=None, prio=0):
    substrate.save_sprint(Sprint(
        id=sid, status=SprintStatus.QUEUED, goals="g", plan=["x"], program=program,
        resources_required={"cpu": 1.0}, priority=prio,
        artifacts_bound=bound or [], artifacts_create=create or []))


def test_bound_sprint_not_granted_while_artifact_locked(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=0.0)  # chat owns it
    _queued(substrate, "s1", "p", bound=["doc"])
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is None                       # stays queued
    assert substrate.load_sprint("s1").status == SprintStatus.QUEUED


def test_bound_sprint_granted_and_locks_artifact_when_free(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _queued(substrate, "s1", "p", bound=["doc"])
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is not None
    assert substrate.load_sprint("s1").status == SprintStatus.EXECUTING
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == "s1"
    assert (substrate.artifact_dir("p", "doc") / "work").is_dir()    # seeded


def test_create_target_instantiated_and_locked_on_grant(substrate):
    _queued(substrate, "s1", "p", create=[{"aid": "fig", "title": "Fig", "kind": "figure"}])
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    fig = substrate.load_artifact("p", "fig")
    assert fig.kind == "figure"
    assert fig.lock["holder_id"] == "s1"


def test_two_sprints_same_artifact_only_one_granted(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _queued(substrate, "s1", "p", bound=["doc"], prio=5)
    _queued(substrate, "s2", "p", bound=["doc"], prio=1)
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    # higher-priority s1 wins the artifact; s2 stays queued (leaseless)
    assert disp.ledger.lease_for("s1") is not None
    assert disp.ledger.lease_for("s2") is None
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == "s1"


def test_nonartifact_sprint_unaffected(substrate):
    _queued(substrate, "s1", "p")     # no artifacts
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is not None


def test_bound_missing_artifact_does_not_crash_cycle(substrate):
    # A sprint bound to an artifact id that does not exist must NOT crash the
    # dispatch cycle; the missing aid is simply not locked.
    _queued(substrate, "s1", "p", bound=["ghost"])
    disp = _disp(substrate, {"cpu": 4.0})
    disp.run_one_cycle(now=1.0)                       # must not raise
    disp.ledger.load()
    assert disp.ledger.lease_for("s1") is not None    # granted; missing aid ignored
