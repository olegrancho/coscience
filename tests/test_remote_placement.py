"""O6: remote hosts take work only when the deployment allows it, and a sprint stays on its host."""
from coscience.ledger import Ledger
from coscience.models import ProgressState, Sprint, SprintStatus
from coscience.resources import ResourcePool, over_capacity
from coscience.scheduler import SchedulerPolicy
from coscience.service import Service

POOL = {"cpu": 4, "workers": 2, "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 16}}}}
POOL_YAML = "cpu: 4\nworkers: 2\nhosts:\n  big:\n    ssh: big\n    capacity: {cpu: 16}\n"


def _sprint(sid, req, prio=0, status=SprintStatus.QUEUED):
    return Sprint(id=sid, status=status, goals="g", plan=["a"], resources_required=req, priority=prio)


def _ledger(tmp_path):
    led = Ledger(ResourcePool.from_dict(POOL), tmp_path / "leases.json")
    led.load()
    return led


def test_a_remote_host_takes_work_only_when_remote_placement_is_on(monkeypatch):
    monkeypatch.delenv("COSCIENCE_ALLOW_REMOTE", raising=False)
    assert not ResourcePool.from_dict(POOL).host("big").placeable
    monkeypatch.setenv("COSCIENCE_ALLOW_REMOTE", "1")
    pool = ResourcePool.from_dict(POOL)
    assert pool.host("big").placeable
    assert pool.capacity["cpu"] == 20.0


def test_a_pinned_sprint_is_granted_only_on_its_host(tmp_path, every_host_placeable):
    led, pol = _ledger(tmp_path), SchedulerPolicy(aging_interval=0.0)
    granted = pol.select_grants([_sprint("s1", {"cpu": 2})], {"s1": 0.0}, led, now=0.0,
                                pinned={"s1": "big"})
    assert [s.id for s in granted] == ["s1"]
    assert led.acquire("s1", {"cpu": 2}, now=0.0, ttl=60.0, host="big").host == "big"


def test_a_pinned_sprint_waits_rather_than_moving(tmp_path, every_host_placeable):
    led, pol = _ledger(tmp_path), SchedulerPolicy(aging_interval=0.0)
    assert led.acquire("hog", {"cpu": 16}, now=0.0, ttl=60.0).host == "big"
    assert pol.select_grants([_sprint("s1", {"cpu": 2})], {"s1": 0.0}, led, now=0.0,
                             pinned={"s1": "big"}) == []
    assert [s.id for s in pol.select_grants([_sprint("s1", {"cpu": 2})], {"s1": 0.0}, led, now=0.0)] == ["s1"]


def test_yield_victims_come_only_from_the_pinned_host(tmp_path, every_host_placeable):
    led, pol = _ledger(tmp_path), SchedulerPolicy()
    assert led.acquire("local-lo", {"cpu": 4}, now=0.0, ttl=60.0, priority=0).host == "local"
    assert led.acquire("big-lo", {"cpu": 16}, now=1.0, ttl=60.0, priority=0).host == "big"
    cand = _sprint("hi", {"cpu": 4}, prio=5)
    victims = pol.select_yield_victims(cand, 5, led, {"local-lo", "big-lo"}, pinned_host="big")
    assert [v.sprint_id for v in victims] == ["big-lo"]


def test_over_capacity_can_be_judged_on_one_host(every_host_placeable):
    pool = ResourcePool.from_dict(POOL)
    assert over_capacity({"cpu": 8}, pool) == {}
    assert over_capacity({"cpu": 8}, pool, only_host="local") == {"cpu": (8.0, 4.0)}


def test_progress_remembers_the_host_and_the_remote_job(substrate):
    substrate.save_progress(ProgressState(sprint_id="s1", host="big", job_host="big",
                                          job_collect=["~/runs/s1/work"], collect_note="copied"))
    prog = substrate.load_progress("s1")
    assert (prog.host, prog.job_host, prog.job_collect, prog.collect_note) == (
        "big", "big", ["~/runs/s1/work"], "copied")
    assert substrate.load_progress("never").host == ""


def _write(tmp_path, text):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def test_the_sprint_page_names_the_closest_host_when_several_take_work(tmp_path, every_host_placeable):
    _write(tmp_path, POOL_YAML)
    svc = Service(tmp_path)
    svc.substrate.save_sprint(_sprint("s1", {"cpu": 32}))
    assert svc.get_sprint("s1")["unrunnable"] == "needs cpu 32 but capacity is 16 (closest host: big)"


def test_a_sprint_pinned_to_a_host_that_left_the_pool_says_so(tmp_path):
    _write(tmp_path, "cpu: 4\n")
    svc = Service(tmp_path)
    svc.substrate.save_sprint(_sprint("s1", {"cpu": 1}))
    svc.substrate.save_progress(ProgressState(sprint_id="s1", host="gone1"))
    assert svc.get_sprint("s1")["unrunnable"] == (
        "its work is on host gone1, which is not in the pool or not taking work")


def test_a_sprint_pinned_to_a_host_that_dropped_its_program_says_so(tmp_path, every_host_placeable):
    _write(tmp_path, "cpu: 4\nhosts:\n  big:\n    ssh: big\n    programs: [p2]\n"
                     "    capacity: {cpu: 16}\n")
    svc = Service(tmp_path)
    sprint = _sprint("s1", {"cpu": 1})
    sprint.program = "p5"                # "big" only admits p2
    svc.substrate.save_sprint(sprint)
    svc.substrate.save_progress(ProgressState(sprint_id="s1", host="big"))
    assert svc.get_sprint("s1")["unrunnable"] == (
        "its work is on host big, which no longer takes work for this program")


def test_ledger_status_offers_this_machines_amounts_for_editing(tmp_path, every_host_placeable):
    _write(tmp_path, POOL_YAML)
    status = Service(tmp_path).ledger_status()
    assert status["capacity"]["cpu"] == 20.0
    assert status["local_capacity"] == {"workers": 2.0, "cpu": 4.0}
