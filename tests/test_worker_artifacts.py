from tests.conftest import FakeAgent

from coscience import artifacts
from coscience.models import Sprint, SprintStatus
from coscience.worker import Worker


def _executing_bound(substrate, sid="s1"):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    s = Sprint(id=sid, status=SprintStatus.EXECUTING, goals="g", plan=["x"],
               program="p", artifacts_bound=["doc"])
    substrate.save_sprint(s)
    artifacts.acquire_for_sprint(substrate, s, now=0.0)   # simulate the grant
    return s


def test_done_cuts_artifact_version_and_unlocks(substrate):
    agent = FakeAgent(finished=True)            # writes finished.json on launch
    s = _executing_bound(substrate)
    w = Worker(substrate, agent)
    w.run_sprint_beat(s)                        # beat 1: launch agent
    # agent "produced" a deliverable into the working copy
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("final")
    w.run_sprint_beat(s)                        # beat 2: collect -> DONE
    assert substrate.load_sprint("s1").status == SprintStatus.DONE
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"
    assert a.lock == {}
    assert (substrate.artifact_dir("p", "doc") / "v1" / "c.md").read_text() == "final"


def test_failed_releases_artifact_lock(substrate):
    # A real-failure agent (nonzero exit, no finished.json): the worker relaunches
    # then collects each beat, so it takes ~2 beats per failure to reach the cap.
    agent = FakeAgent(status="failed", finished=False)
    s = _executing_bound(substrate)
    w = Worker(substrate, agent)
    for _ in range(20):
        w.run_sprint_beat(s)
        s = substrate.load_sprint("s1")
        if s.status == SprintStatus.FAILED:
            break
    assert s.status == SprintStatus.FAILED
    assert substrate.load_artifact("p", "doc").lock == {}


def test_done_dedup_cuts_no_version_when_work_untouched(substrate):
    agent = FakeAgent(finished=True)
    s = _executing_bound(substrate)             # work/ seeded empty, no version yet
    w = Worker(substrate, agent)
    w.run_sprint_beat(s)
    w.run_sprint_beat(s)                        # DONE with an untouched empty work/
    a = substrate.load_artifact("p", "doc")
    assert a.versions == []                     # no spurious version
    assert a.lock == {}
