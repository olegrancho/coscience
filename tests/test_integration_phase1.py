from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _approved(sid, req, prio=0):
    return Sprint(id=sid, status=SprintStatus.QUEUED, goals="g", plan=["work"],
                  resources_required=req, priority=prio)


def test_three_sprints_one_gpu_serialize_without_overcommit(substrate):
    # 1 GPU, 3 sprints each needing it -> run one-at-a-time and all finish.
    substrate.save_sprint(_approved("a", {"gpu": 1.0}, prio=1))
    substrate.save_sprint(_approved("b", {"gpu": 1.0}, prio=5))  # highest -> first
    substrate.save_sprint(_approved("c", {"gpu": 1.0}, prio=1))
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool({"gpu": 1.0}),
                      SchedulerPolicy(aging_interval=0.0))

    first_done = None
    for t in range(60):
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0          # never overcommit
        if first_done is None and substrate.load_sprint("b").status == SprintStatus.DONE:
            first_done = "b"
        if all(substrate.load_sprint(s).status == SprintStatus.DONE for s in ("a", "b", "c")):
            break

    for s in ("a", "b", "c"):
        assert substrate.load_sprint(s).status == SprintStatus.DONE
    assert disp.ledger.all_leases() == []                         # all released
    assert first_done == "b"                                      # priority honored


def test_cpu_sprints_run_concurrently_with_gpu_sprint(substrate):
    substrate.save_sprint(_approved("gpu1", {"gpu": 1.0}))
    substrate.save_sprint(_approved("cpuA", {"runtime_slots": 1.0}))
    substrate.save_sprint(_approved("cpuB", {"runtime_slots": 1.0}))
    disp = Dispatcher(substrate, FakeAgent(),
                      ResourcePool({"gpu": 1.0, "runtime_slots": 3.0}),
                      SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert {l.sprint_id for l in disp.ledger.all_leases()} == {"gpu1", "cpuA", "cpuB"}
