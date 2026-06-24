from coscience.models import Lease, Sprint, SprintStatus, Step
from coscience.substrate import Substrate


def test_lease_construct_defaults():
    lease = Lease(id="L1", sprint_id="sp1", amounts={"gpu": 1.0},
                  granted_at=100.0, expires_at=200.0)
    assert lease.priority == 0
    assert lease.preemptible is True


def test_sprint_scheduling_defaults():
    s = Sprint(id="sp1", status=SprintStatus.APPROVED, goals="g", plan=[])
    assert s.resources_required == {}
    assert s.priority == 0
    assert s.preemptible is True


def test_substrate_roundtrips_scheduling_fields(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(
        id="sp1", status=SprintStatus.APPROVED, goals="g",
        plan=[Step("s1", "echo hi")],
        resources_required={"gpu_24gb": 1}, priority=5, preemptible=False,
    )
    sub.save_sprint(s)
    loaded = sub.load_sprint("sp1")
    assert loaded.resources_required == {"gpu_24gb": 1.0}
    assert loaded.priority == 5
    assert loaded.preemptible is False


def test_substrate_defaults_when_fields_absent(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_sprint(Sprint(id="sp2", status=SprintStatus.APPROVED, goals="g",
                           plan=[Step("s1", "echo hi")]))
    loaded = sub.load_sprint("sp2")
    assert loaded.resources_required == {}
    assert loaded.priority == 0
    assert loaded.preemptible is True
