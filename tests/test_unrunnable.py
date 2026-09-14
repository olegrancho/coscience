"""N2: a request above the pool's total is named as unrunnable, not counted as waiting."""
from tests.conftest import FakeAgent

from coscience.dispatcher import Dispatcher
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool, describe_over_capacity, over_capacity
from coscience.scheduler import SchedulerPolicy


def test_over_capacity_names_only_amounts_above_the_total():
    pool = ResourcePool({"cpu": 16.0, "gpu": 1.0})
    assert over_capacity({"cpu": 24.0, "gpu": 1.0}, pool) == {"cpu": (24.0, 16.0)}
    assert over_capacity({"cpu": 16.0}, pool) == {}
    assert over_capacity({"tpu": 1.0}, pool) == {"tpu": (1.0, 0.0)}   # undeclared = none
    assert describe_over_capacity({"cpu": (24.0, 16.0)}) == "needs cpu 24 but capacity is 16"


def test_the_cycle_reports_an_impossible_request_separately_from_waiting(substrate):
    substrate.save_sprint(Sprint(id="big", status=SprintStatus.QUEUED, goals="g", plan=["x"],
                                 resources_required={"cpu": 24.0}))
    substrate.save_sprint(Sprint(id="fits", status=SprintStatus.QUEUED, goals="g", plan=["x"],
                                 resources_required={"gpu": 1.0}))
    substrate.save_sprint(Sprint(id="holder", status=SprintStatus.QUEUED, goals="g", plan=["x"],
                                 resources_required={"gpu": 1.0}, priority=9))
    disp = Dispatcher(substrate, FakeAgent(linger=50, finished=False),
                      ResourcePool({"cpu": 16.0, "gpu": 1.0}), SchedulerPolicy(aging_interval=0.0))

    report = disp.run_one_cycle(now=0.0)

    assert report.unrunnable == ["big"]
    assert report.waiting == 1          # "fits" waits for the GPU "holder" took


def test_the_sprint_page_says_why_it_can_never_start(substrate):
    from coscience.service import Service
    cos = substrate.repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text("cpu: 16\n")
    substrate.save_sprint(Sprint(id="big", status=SprintStatus.QUEUED, goals="g", plan=["x"],
                                 resources_required={"cpu": 24.0}))
    substrate.save_sprint(Sprint(id="old", status=SprintStatus.DONE, goals="g", plan=["x"],
                                 resources_required={"cpu": 24.0}))
    svc = Service(substrate.repo_root)
    assert svc.get_sprint("big")["unrunnable"] == "needs cpu 24 but capacity is 16"
    assert svc.get_sprint("old")["unrunnable"] == ""
    rows = {r["id"]: r["unrunnable"] for r in svc.list_sprints()}
    assert rows == {"big": "needs cpu 24 but capacity is 16", "old": ""}
