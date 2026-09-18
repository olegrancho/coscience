"""O15: a human marks a server for removal (`remove: true`); the dispatcher
deletes it at the start of a cycle, before granting, once nothing is on it."""
import fcntl
import threading
import time

import pytest
import yaml

from coscience import host_removal
from coscience.dispatcher import Dispatcher
from coscience.ledger import Ledger
from coscience.models import Program, ProgressState, Sprint, SprintStatus
from coscience.resources import ResourcePool, pool_file_lock
from coscience.service import Service
from tests.conftest import FakeAgent
from tests.host_probe_fakes import FakeRunner
from tests.test_host_health import ScriptRunner, _live_on_big, _stub_worker_for_readoption

POOL_YAML = ("cpu: 4\nworkers: 4\nhosts:\n"
             "  a:\n    ssh: a\n    run_root: ~/runs\n    capacity: {cpu: 16}\n")


def _write_pool(repo_root, text=POOL_YAML):
    cos = repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def _mark_removed(repo_root, name="a"):
    path = repo_root / ".coscience" / "resources.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["hosts"][name]["remove"] = True
    path.write_text(yaml.safe_dump(doc))


def _ledger(repo_root):
    led = Ledger(ResourcePool.from_yaml(repo_root / ".coscience" / "resources.yaml"),
                 repo_root / ".coscience" / "leases.json")
    led.load()
    return led


def _lease(repo_root, sid, amounts, host):
    led = _ledger(repo_root)
    assert led.acquire(sid, amounts, now=0.0, ttl=1e9, host=host) is not None
    led.save()


# --- blockers ----------------------------------------------------------------

def test_blockers_lists_a_lease_holder_and_pinned_queued_work(substrate, every_host_placeable):
    _write_pool(substrate.repo_root)
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s1", host="a"))
    _lease(substrate.repo_root, "s1", {"cpu": 2}, "a")

    substrate.save_sprint(Sprint(id="s2", status=SprintStatus.QUEUED, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s2", host="a"))

    out = host_removal.blockers(substrate, _ledger(substrate.repo_root), "a")
    assert out == [
        {"sprint_id": "s1", "status": "executing", "reason": "holds a lease here"},
        {"sprint_id": "s2", "status": "queued", "reason": "its work is here"},
    ]


def test_blockers_ignores_done_work_and_lists_a_dual_role_sprint_once(substrate, every_host_placeable):
    _write_pool(substrate.repo_root)
    # s1 both holds a lease on "a" and has progress.host == "a": listed once, as a
    # lease holder (that reason takes priority).
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s1", host="a"))
    _lease(substrate.repo_root, "s1", {"cpu": 2}, "a")

    # s2 is DONE but still pinned to "a": a leftover, not a blocker.
    substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s2", host="a"))

    out = host_removal.blockers(substrate, _ledger(substrate.repo_root), "a")
    assert out == [{"sprint_id": "s1", "status": "executing", "reason": "holds a lease here"}]


def test_blockers_matches_job_host_even_when_host_differs(substrate, every_host_placeable):
    # M3 (fix round 1): host != job_host is the actual shape of a live detached job
    # (the sprint's own progress.host may still name an older machine); the
    # progress.host == progress.job_host fixtures elsewhere in this file never
    # exercised the job_host branch on its own.
    _write_pool(substrate.repo_root)
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s1", host="b", job_host="a"))
    out = host_removal.blockers(substrate, _ledger(substrate.repo_root), "a")
    assert out == [{"sprint_id": "s1", "status": "executing", "reason": "its work is here"}]


def test_blockers_lists_a_pending_reallocate_target_as_moving_here(substrate, every_host_placeable):
    # I1 (fix round 1): the dispatcher's grant step pins a sprint on
    # `reallocate_to or host` (dispatcher.py) — a pending reallocate answer must
    # block the *target*, even though the sprint's work (and any lease) is still on
    # its old host.
    _write_pool(substrate.repo_root)
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s1", host="b", reallocate_to="a"))
    out = host_removal.blockers(substrate, _ledger(substrate.repo_root), "a")
    assert out == [{"sprint_id": "s1", "status": "executing", "reason": "moving here"}]


def test_blockers_by_host_computes_every_server_in_one_pass(substrate, every_host_placeable):
    # M7 (fix round 1): blockers_by_host is the one-pass primitive remove_marked and
    # ledger_status now use; check it directly for more than one server at once, and
    # that the single-host `blockers` wrapper agrees with it.
    text = ("cpu: 4\nworkers: 4\nhosts:\n"
            "  a:\n    ssh: a\n    capacity: {cpu: 16}\n"
            "  b:\n    ssh: b\n    capacity: {cpu: 16}\n")
    _write_pool(substrate.repo_root, text)
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s1", host="a"))
    substrate.save_sprint(Sprint(id="s2", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s2", host="b"))
    substrate.save_sprint(Sprint(id="s3", status=SprintStatus.DONE, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s3", host="a"))   # finished: not a blocker

    ledger = _ledger(substrate.repo_root)
    out = host_removal.blockers_by_host(substrate, ledger, {"a", "b"})
    assert out == {
        "a": [{"sprint_id": "s1", "status": "executing", "reason": "its work is here"}],
        "b": [{"sprint_id": "s2", "status": "executing", "reason": "its work is here"}],
    }
    assert host_removal.blockers(substrate, ledger, "a") == out["a"]
    assert host_removal.blockers(substrate, ledger, "b") == out["b"]


# --- remove_marked -------------------------------------------------------------

def test_remove_marked_deletes_an_empty_marked_server_and_returns_its_name(substrate, every_host_placeable):
    _write_pool(substrate.repo_root)
    _mark_removed(substrate.repo_root)
    removed = host_removal.remove_marked(substrate, _ledger(substrate.repo_root))
    assert removed == ["a"]
    doc = yaml.safe_load((substrate.repo_root / ".coscience" / "resources.yaml").read_text())
    assert "a" not in doc["hosts"]


def test_remove_marked_leaves_a_marked_server_with_a_blocker(substrate, every_host_placeable):
    _write_pool(substrate.repo_root)
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s1", host="a"))
    _lease(substrate.repo_root, "s1", {"cpu": 2}, "a")   # granted before the mark: still holds it
    _mark_removed(substrate.repo_root)

    removed = host_removal.remove_marked(substrate, _ledger(substrate.repo_root))
    assert removed == []
    doc = yaml.safe_load((substrate.repo_root / ".coscience" / "resources.yaml").read_text())
    assert "a" in doc["hosts"]


def test_remove_marked_leaves_a_marked_server_targeted_by_a_pending_reallocate(substrate, every_host_placeable):
    # I1 (fix round 1). No lease (it was lost before Worker.relocate ran, the
    # scenario the review reproduced), and progress.host is wherever the sprint
    # used to run — not "a" — so only reallocate_to pins it here.
    _write_pool(substrate.repo_root)
    _mark_removed(substrate.repo_root)
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    substrate.save_progress(ProgressState(sprint_id="s1", reallocate_to="a"))

    removed = host_removal.remove_marked(substrate, _ledger(substrate.repo_root))
    assert removed == []
    doc = yaml.safe_load((substrate.repo_root / ".coscience" / "resources.yaml").read_text())
    assert "a" in doc["hosts"]


def test_remove_marked_leaves_an_unmarked_idle_server(substrate, every_host_placeable):
    _write_pool(substrate.repo_root)
    removed = host_removal.remove_marked(substrate, _ledger(substrate.repo_root))
    assert removed == []
    doc = yaml.safe_load((substrate.repo_root / ".coscience" / "resources.yaml").read_text())
    assert "a" in doc["hosts"]


def test_remove_marked_deletes_inside_a_resources_wrapper(substrate, every_host_placeable):
    text = ("resources:\n  cpu: 4\n  workers: 4\n  hosts:\n"
            "    a:\n      ssh: a\n      capacity: {cpu: 16}\n      remove: true\n")
    _write_pool(substrate.repo_root, text)
    removed = host_removal.remove_marked(substrate, _ledger(substrate.repo_root))
    assert removed == ["a"]
    doc = yaml.safe_load((substrate.repo_root / ".coscience" / "resources.yaml").read_text())
    assert "a" not in doc["resources"]["hosts"]


def test_remove_marked_does_not_rewrite_the_file_when_nothing_is_deleted(substrate, every_host_placeable):
    _write_pool(substrate.repo_root)
    path = substrate.repo_root / ".coscience" / "resources.yaml"
    before = path.stat().st_mtime_ns
    time.sleep(0.01)
    removed = host_removal.remove_marked(substrate, _ledger(substrate.repo_root))
    assert removed == []
    assert path.stat().st_mtime_ns == before


# --- pool_file_lock -------------------------------------------------------------

def test_pool_file_lock_is_exclusive(tmp_path):
    with pool_file_lock(tmp_path):
        with open(tmp_path / ".coscience" / "resources.lock") as fd2:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _assert_blocks_on_pool_lock(repo_root, call):
    """True, by assertion, that the zero-arg `call` blocks while `repo_root`'s pool
    file lock is held elsewhere, and completes once it is released — the guarantee
    `pool_file_lock` exists to give every writer of resources.yaml (fix round 1,
    M3: dropping a writer's `with pool_file_lock(...)` used to fail no test)."""
    done = threading.Event()
    errors: list[BaseException] = []

    def run():
        try:
            call()
        except BaseException as exc:          # surfaced below, not swallowed
            errors.append(exc)
        finally:
            done.set()

    with pool_file_lock(repo_root):
        t = threading.Thread(target=run)
        t.start()
        blocked_while_held = not done.wait(timeout=0.2)
    t.join(timeout=2.0)
    assert blocked_while_held, "the writer did not block while the pool file lock was held"
    assert done.is_set(), "the writer never finished after the lock was released"
    if errors:
        raise errors[0]


def test_set_capacity_blocks_on_the_pool_file_lock(tmp_path):
    _write_pool(tmp_path, "cpu: 4\nworkers: 4\n")
    svc = Service(tmp_path)
    _assert_blocks_on_pool_lock(tmp_path, lambda: svc.set_capacity({"cpu": 8}))


def test_update_host_blocks_on_the_pool_file_lock(tmp_path):
    _write_pool(tmp_path)
    svc = Service(tmp_path)
    _assert_blocks_on_pool_lock(tmp_path, lambda: svc.update_host("a", owner="ops"))


def test_confirm_host_blocks_on_the_pool_file_lock(tmp_path):
    _write_pool(tmp_path, "cpu: 4\nworkers: 4\n")
    svc = Service(tmp_path)
    svc.probe_host(name="b", ssh="b", runner=FakeRunner())
    _assert_blocks_on_pool_lock(tmp_path, lambda: svc.confirm_host(name="b", capacity={"cpu": 4}))


def test_set_host_programs_blocks_on_the_pool_file_lock(tmp_path):
    _write_pool(tmp_path)
    svc = Service(tmp_path)
    _assert_blocks_on_pool_lock(tmp_path, lambda: svc.set_host_programs("a", ["p1"]))


def test_set_program_hosts_blocks_on_the_pool_file_lock(tmp_path):
    _write_pool(tmp_path)
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P1", goals="g"))
    _assert_blocks_on_pool_lock(tmp_path, lambda: svc.set_program_hosts("p1", []))


def test_remove_host_blocks_on_the_pool_file_lock(tmp_path):
    _write_pool(tmp_path)
    svc = Service(tmp_path)
    _assert_blocks_on_pool_lock(tmp_path, lambda: svc.remove_host("a"))


def test_keep_host_blocks_on_the_pool_file_lock(tmp_path):
    _write_pool(tmp_path)
    _mark_removed(tmp_path)
    svc = Service(tmp_path)
    _assert_blocks_on_pool_lock(tmp_path, lambda: svc.keep_host("a"))


def test_remove_marked_blocks_on_the_pool_file_lock(substrate, every_host_placeable):
    _write_pool(substrate.repo_root)
    _mark_removed(substrate.repo_root)
    _assert_blocks_on_pool_lock(
        substrate.repo_root,
        lambda: host_removal.remove_marked(substrate, _ledger(substrate.repo_root)))


# --- the dispatcher cycle --------------------------------------------------------

def test_a_marked_idle_server_is_removed_before_granting(substrate, every_host_placeable):
    pool_dict = {"cpu": 4, "workers": 4,
                 "hosts": {"a": {"ssh": "a", "capacity": {"cpu": 16}, "remove": True}}}
    _write_pool(substrate.repo_root, yaml.safe_dump(pool_dict))

    # Only "a" (cpu 16) can hold this; local has cpu 4. Never launched, so nothing of
    # its "work" is on "a" yet — it only fits there, which is not a blocker.
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["a"],
                                 resources_required={"cpu": 8}))

    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict(pool_dict),
                      host_runner=ScriptRunner({"a": (0, "", "")}))
    report = disp.run_one_cycle(now=0.0)

    assert report.removed_hosts == ["a"]
    assert report.granted == 0
    assert disp.ledger.lease_for("s1") is None
    assert disp.ledger.pool.host("a") is None
    doc = yaml.safe_load((substrate.repo_root / ".coscience" / "resources.yaml").read_text())
    assert "a" not in doc["hosts"]


def test_removal_and_reload_happen_before_the_grant_step(substrate, every_host_placeable):
    # M3 (fix round 1): the previous version of this ordering check marked "a" in
    # the pool object the Dispatcher was built with too, so `grantable_hosts`
    # already excluded it — granted == 0 held even if removal ran (or the pool was
    # reloaded) AFTER the grant step. Here the in-memory pool is stale and
    # UNMARKED, as if read moments before a human clicked Remove; only the file
    # (what remove_marked and the reload both read) is marked. If either step moved
    # after the grants, this stale pool would let s1 land on "a".
    stale_unmarked = {"cpu": 4, "workers": 4,
                      "hosts": {"a": {"ssh": "a", "capacity": {"cpu": 16}}}}
    _write_pool(substrate.repo_root, yaml.safe_dump(
        {"cpu": 4, "workers": 4,
         "hosts": {"a": {"ssh": "a", "capacity": {"cpu": 16}, "remove": True}}}))

    # Only "a" (cpu 16) can hold this; local has cpu 4.
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["a"],
                                 resources_required={"cpu": 8}))

    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict(stale_unmarked),
                      host_runner=ScriptRunner({"a": (0, "", "")}))
    report = disp.run_one_cycle(now=0.0)

    assert report.removed_hosts == ["a"]
    assert disp.ledger.lease_for("s1") is None
    assert disp.ledger.pool.host("a") is None


def test_a_pool_file_error_sets_removal_error_not_beat_errors(substrate, every_host_placeable):
    # M2 (fix round 1): the removal step's own error must be visible in its own
    # field — not folded into `beat_errors`, whose comment says it lists sprint ids.
    _write_pool(substrate.repo_root, "cpu: 4\nworkers: 4\nhosts:\n  - a\n")   # hosts: is a list
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["a"]))

    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict({"cpu": 4, "workers": 4}),
                      host_runner=ScriptRunner({}))
    report = disp.run_one_cycle(now=0.0)

    assert report.removed_hosts == []
    assert report.beat_errors == []
    assert "hosts: is not a mapping" in report.removal_error
    # The rest of the cycle still ran: the malformed hosts section doesn't stop the
    # local-only pool from granting.
    assert report.granted == 1


def test_a_marked_server_with_a_blocker_keeps_its_live_work_but_grants_no_new_sprint(
        substrate, every_host_placeable, monkeypatch):
    removing = {"cpu": 4, "workers": 4, "hosts": {
        "big": {"ssh": "big", "capacity": {"cpu": 16}, "remove": True},
        "gone": {"ssh": "gone", "capacity": {"cpu": 8}}}}
    _write_pool(substrate.repo_root, yaml.safe_dump(removing))
    _live_on_big(substrate)                              # s1: EXECUTING, live job on "big"

    # s2 is pinned to "big" too, but has no live agent or job: new work, kept off it.
    substrate.save_sprint(Sprint(id="s2", status=SprintStatus.QUEUED, goals="g", plan=["a"],
                                 resources_required={"cpu": 8}))
    substrate.save_progress(ProgressState(sprint_id="s2", host="big"))

    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict(removing),
                      host_runner=ScriptRunner({"big": (0, "", ""), "gone": (0, "", "")}))
    _stub_worker_for_readoption(disp, monkeypatch)
    report = disp.run_one_cycle(now=0.0)

    assert report.removed_hosts == []
    assert disp.ledger.pool.host("big") is not None
    assert disp.ledger.lease_for("s1").host == "big"      # re-adopted: live work stays
    assert disp.ledger.lease_for("s2") is None             # no new grant onto a marked host
    doc = yaml.safe_load((substrate.repo_root / ".coscience" / "resources.yaml").read_text())
    assert doc["hosts"]["big"].get("remove") is True
