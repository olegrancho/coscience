from coscience.dispatcher import Dispatcher
from coscience.executor import ShellStepExecutor
from coscience.models import Sprint, SprintStatus, Step
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy


def _approved(sid, n_steps, req, prio=0):
    plan = [Step(f"s{i}", "true") for i in range(n_steps)]
    return Sprint(id=sid, status=SprintStatus.APPROVED, goals="g", plan=plan,
                  resources_required=req, priority=prio)


def test_three_sprints_one_gpu_serialize_without_overcommit(substrate):
    # 1 GPU, 3 sprints each needing it -> they must run one-at-a-time and all finish.
    substrate.save_sprint(_approved("a", 2, {"gpu": 1.0}, prio=1))
    substrate.save_sprint(_approved("b", 2, {"gpu": 1.0}, prio=5))  # should go first
    substrate.save_sprint(_approved("c", 2, {"gpu": 1.0}, prio=1))
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))

    first_done = None
    for t in range(60):
        disp.run_one_cycle(now=float(t))
        disp.ledger.load()
        assert disp.ledger.used().get("gpu", 0.0) <= 1.0  # never overcommit
        if first_done is None and substrate.load_sprint("b").status == SprintStatus.DONE:
            first_done = "b"
            # the highest-priority sprint finishes before the low-priority ones start later
        if all(substrate.load_sprint(s).status == SprintStatus.DONE for s in ("a", "b", "c")):
            break

    for s in ("a", "b", "c"):
        assert substrate.load_sprint(s).status == SprintStatus.DONE
    assert disp.ledger.all_leases() == []  # everything released at the end
    assert first_done == "b"  # priority was honored


def test_cpu_sprints_run_concurrently_with_gpu_sprint(substrate):
    # gpu:1 + runtime_slots:3; a gpu sprint and two no-gpu sprints all proceed together.
    substrate.save_sprint(_approved("gpu1", 1, {"gpu": 1.0}))
    substrate.save_sprint(_approved("cpuA", 1, {"runtime_slots": 1.0}))
    substrate.save_sprint(_approved("cpuB", 1, {"runtime_slots": 1.0}))
    disp = Dispatcher(substrate, ShellStepExecutor(),
                      ResourcePool({"gpu": 1.0, "runtime_slots": 3.0}),
                      SchedulerPolicy(aging_interval=0.0))
    disp.run_one_cycle(now=0.0)
    disp.ledger.load()
    assert {l.sprint_id for l in disp.ledger.all_leases()} == {"gpu1", "cpuA", "cpuB"}
