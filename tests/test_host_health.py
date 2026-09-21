"""O7: remote hosts are checked; a quiet host takes no new grants and keeps its work."""
import json
import shlex
import time

from coscience import host_health
from coscience.ledger import Ledger
from coscience.models import ProgressState, Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from tests.conftest import FakeAgent

POOL = {"cpu": 4, "workers": 4,
        "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 16}},
                  "gone": {"ssh": "gone", "capacity": {"cpu": 8}}}}


class ScriptRunner:
    def __init__(self, answers):
        self.answers, self.calls = dict(answers), []

    def __call__(self, argv, stdin, timeout):
        self.calls.append(list(argv))
        target = next(a for a in argv if a in self.answers)
        return self.answers[target]


def _sprint(sid, req, prio=0, status=SprintStatus.QUEUED):
    return Sprint(id=sid, status=status, goals="g", plan=["a"], resources_required=req, priority=prio)


def test_state_reads_unchecked_ok_failing_and_quiet():
    assert host_health.state(None, 100.0) == "unchecked"
    assert host_health.state({"checked_at": 90, "last_ok": 90, "fail_since": 0, "reason": ""}, 100.0) == "ok"
    failing = {"checked_at": 90, "last_ok": 0, "fail_since": 50, "reason": "timed out"}
    assert host_health.state(failing, 100.0) == "failing"
    assert host_health.state(failing, 50 + host_health.QUIET_AFTER) == "quiet"


def test_check_asks_each_remote_placeable_host_and_records_the_answer(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict(POOL)
    runner = ScriptRunner({"big": (0, "", ""), "gone": (255, "", "ssh: connect to host gone: No route\n")})
    entries = host_health.check(tmp_path, pool, now=1000.0, runner=runner)
    assert entries["big"] == {"checked_at": 1000.0, "last_ok": 1000.0, "fail_since": 0.0, "reason": ""}
    assert entries["gone"] == {"checked_at": 1000.0, "last_ok": 0.0, "fail_since": 1000.0,
                               "reason": "ssh: connect to host gone: No route"}
    # A host with no run root is still asked how much space it has — the free-space
    # reading rides the same round trip as the liveness check (B1).
    assert len(runner.calls) == 2
    assert all(c[-1] == f"bash -c {shlex.quote(host_health._FREE_KB)}" for c in runner.calls)
    assert json.loads((tmp_path / ".coscience" / "host-health.json").read_text()) == entries
    assert "local" not in entries


def test_a_host_is_checked_at_most_once_a_minute_and_failing_keeps_its_start(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict(POOL)
    down = ScriptRunner({"big": (255, "", "timed out"), "gone": (255, "", "timed out")})
    host_health.check(tmp_path, pool, now=1000.0, runner=down)
    host_health.check(tmp_path, pool, now=1030.0, runner=down)
    assert len(down.calls) == 2
    entries = host_health.check(tmp_path, pool, now=1000.0 + host_health.CHECK_INTERVAL, runner=down)
    assert len(down.calls) == 4 and entries["big"]["fail_since"] == 1000.0
    back = ScriptRunner({"big": (0, "", ""), "gone": (255, "", "timed out")})
    entries = host_health.check(tmp_path, pool, now=1200.0, runner=back)
    assert entries["big"]["fail_since"] == 0.0 and entries["big"]["last_ok"] == 1200.0


def test_nothing_is_checked_while_remote_placement_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("COSCIENCE_ALLOW_REMOTE", raising=False)
    runner = ScriptRunner({})
    assert host_health.check(tmp_path, ResourcePool.from_dict(POOL), now=1000.0, runner=runner) == {}
    assert runner.calls == []


def test_a_removed_host_leaves_the_health_file(tmp_path, every_host_placeable):
    host_health.check(tmp_path, ResourcePool.from_dict(POOL), now=1000.0,
                      runner=ScriptRunner({"big": (0, "", ""), "gone": (0, "", "")}))
    smaller = ResourcePool.from_dict({"cpu": 4, "hosts": {"big": POOL["hosts"]["big"]}})
    assert set(host_health.check(tmp_path, smaller, now=2000.0, runner=ScriptRunner({"big": (0, "", "")}))) == {"big"}


def test_an_unreadable_health_file_reads_as_nothing_checked(tmp_path):
    (tmp_path / ".coscience").mkdir()
    (tmp_path / ".coscience" / "host-health.json").write_text("{not json")
    assert host_health.load(tmp_path) == {}


# --- M1: a malformed health entry never crashes ------------------------------------

def test_a_malformed_health_entry_reads_unchecked_or_ok_without_raising():
    assert host_health.state({"checked_at": 90, "fail_since": "soon"}, 100.0) == "ok"
    assert host_health.state({"checked_at": "boom", "fail_since": "soon"}, 1000.0) == "unchecked"


def test_check_survives_a_malformed_entry_on_disk(tmp_path, every_host_placeable):
    (tmp_path / ".coscience").mkdir()
    (tmp_path / ".coscience" / "host-health.json").write_text(json.dumps(
        {"big": {"checked_at": "oops", "last_ok": "oops", "fail_since": "soon", "reason": ""}}))
    pool = ResourcePool.from_dict(POOL)
    entries = host_health.check(tmp_path, pool, now=1000.0,
                                runner=ScriptRunner({"big": (255, "", "still down"), "gone": (0, "", "")}))
    assert entries["big"]["fail_since"] == 1000.0        # prev fail_since ("soon") coerced to 0.0, so now


# --- M3: stale health is not shown as current ---------------------------------------

def test_a_stale_ok_entry_reads_unchecked_not_ok():
    fresh_ok = {"checked_at": 0.0, "last_ok": 0.0, "fail_since": 0.0, "reason": ""}
    assert host_health.state(fresh_ok, host_health.STALE_AFTER - 1) == "ok"
    assert host_health.state(fresh_ok, host_health.STALE_AFTER + 1) == "unchecked"


def test_check_drops_entries_for_hosts_no_longer_placeable(tmp_path, monkeypatch):
    monkeypatch.delenv("COSCIENCE_ALLOW_REMOTE", raising=False)
    (tmp_path / ".coscience").mkdir()
    (tmp_path / ".coscience" / "host-health.json").write_text(json.dumps(
        {"big": {"checked_at": 500.0, "last_ok": 500.0, "fail_since": 0.0, "reason": ""}}))
    pool = ResourcePool.from_dict(POOL)
    assert not pool.host("big").placeable
    entries = host_health.check(tmp_path, pool, now=1000.0, runner=ScriptRunner({}))
    assert entries == {}


def test_drained_and_closed_hosts_take_no_new_grants(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 1, "hosts": {
        "big": {"ssh": "big", "capacity": {"cpu": 16}, "drain": True},
        "gone": {"ssh": "gone", "capacity": {"cpu": 8}}}})
    assert pool.host("big").drain and not pool.host("gone").drain
    pool.closed = {"gone": "quiet"}
    assert [h.name for h in pool.grantable_hosts(None)] == ["local"]
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    assert led.fit({"cpu": 4}) is None and not led.can_fit({"cpu": 4})
    assert led.fit({"cpu": 1}) == ("local", [])


def test_a_sprint_pinned_to_a_quiet_host_waits_and_its_lease_is_kept(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict(POOL)
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    assert led.acquire("running", {"cpu": 2}, now=0.0, ttl=60.0, host="big").host == "big"
    pool.closed = {"big": "quiet"}
    pol = SchedulerPolicy(aging_interval=0.0)
    assert pol.select_grants([_sprint("s1", {"cpu": 2})], {"s1": 0.0}, led, now=0.0, pinned={"s1": "big"}) == []
    assert led.lease_for("running").host == "big"


def test_no_yield_victim_is_chosen_on_a_closed_host(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 0, "workers": 4,
                                   "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 4}}}})
    led = Ledger(pool, tmp_path / "leases.json")
    led.load()
    led.acquire("low", {"cpu": 4}, now=0.0, ttl=60.0, priority=0)
    pool.closed = {"big": "quiet"}
    victims = SchedulerPolicy().select_yield_victims(_sprint("hi", {"cpu": 4}, prio=5), 5, led, {"low"})
    assert victims == []


def test_the_dispatcher_closes_a_quiet_host_for_the_cycle(substrate, every_host_placeable):
    from coscience.dispatcher import Dispatcher
    from tests.conftest import FakeAgent
    health = substrate.repo_root / ".coscience" / "host-health.json"
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text(json.dumps({"big": {"checked_at": 10.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "down"}}))
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict(POOL),
                      host_runner=ScriptRunner({"big": (255, "", "down"), "gone": (0, "", "")}))
    disp.run_one_cycle(now=1.0 + host_health.QUIET_AFTER)
    assert disp.ledger.pool.closed == {"big": "quiet"}


# --- fix round 1, issue 1: live work re-adopts on a quiet or drained host ---------
#
# LIVENESS re-adoption (a leaseless EXECUTING sprint whose agent or job is still
# physically running, e.g. after a dispatcher outage past the lease TTL) must not be
# blocked by drain or quiet: killing that job because its host merely stopped
# answering health checks — or was marked drain — would violate "no job is killed
# because a host stopped answering" (spec §7). New work still waits on such a host.

def _live_on_big(substrate):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"],
                                 resources_required={"cpu": 2}))
    substrate.save_progress(ProgressState(sprint_id="s1", host="big",
                                          job_token="big:4242:777:boot-1", job_host="big"))


def _stub_worker_for_readoption(disp, monkeypatch):
    """No real agent/job control: the beat step must not run for real, and the
    sprint must read as live (agent_running False, a live job_token) without
    touching the network."""
    monkeypatch.setattr(disp.worker, "run_sprint_beat", lambda sprint: "idle")
    monkeypatch.setattr(disp.worker, "_job_alive", lambda token: True)
    monkeypatch.setattr(disp.worker, "agent_running", lambda sprint_id: False)


def test_live_work_on_a_quiet_host_is_readopted_not_reaped(substrate, every_host_placeable, monkeypatch):
    from coscience.dispatcher import Dispatcher
    health = substrate.repo_root / ".coscience" / "host-health.json"
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text(json.dumps({"big": {"checked_at": 10.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "down"}}))
    _live_on_big(substrate)
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict(POOL),
                      host_runner=ScriptRunner({"big": (255, "", "down"), "gone": (0, "", "")}))
    _stub_worker_for_readoption(disp, monkeypatch)
    now = 1.0 + host_health.QUIET_AFTER
    report = disp.run_one_cycle(now=now)
    assert disp.ledger.pool.closed == {"big": "quiet"}          # host really is quiet
    assert report.reconciled == 0
    assert disp.ledger.lease_for("s1").host == "big"
    assert substrate.load_progress("s1").job_token == "big:4242:777:boot-1"


def test_live_work_on_a_drained_host_is_readopted_not_reaped(substrate, every_host_placeable, monkeypatch):
    from coscience.dispatcher import Dispatcher
    drained = {"cpu": 4, "workers": 4, "hosts": {
        "big": {"ssh": "big", "capacity": {"cpu": 16}, "drain": True},
        "gone": {"ssh": "gone", "capacity": {"cpu": 8}}}}
    _live_on_big(substrate)
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict(drained),
                      host_runner=ScriptRunner({"big": (0, "", ""), "gone": (0, "", "")}))
    _stub_worker_for_readoption(disp, monkeypatch)
    assert disp.ledger.pool.host("big").drain
    report = disp.run_one_cycle(now=0.0)
    assert report.reconciled == 0
    assert disp.ledger.lease_for("s1").host == "big"
    assert substrate.load_progress("s1").job_token == "big:4242:777:boot-1"


def test_new_work_pinned_to_the_same_quiet_host_still_waits_while_the_live_sprint_readopts(
        substrate, every_host_placeable, monkeypatch):
    """The readopt bypass is per-candidate, not per-cycle: a QUEUED sprint pinned to
    the same quiet host (e.g. an earlier launch attempt that never started an agent
    or job) must still wait, alongside the live re-adoption succeeding."""
    from coscience.dispatcher import Dispatcher
    health = substrate.repo_root / ".coscience" / "host-health.json"
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text(json.dumps({"big": {"checked_at": 10.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "down"}}))
    _live_on_big(substrate)
    substrate.save_sprint(Sprint(id="newcomer", status=SprintStatus.QUEUED, goals="g", plan=["a"],
                                 resources_required={"cpu": 1}))
    substrate.save_progress(ProgressState(sprint_id="newcomer", host="big"))  # pinned, not live
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict(POOL),
                      host_runner=ScriptRunner({"big": (255, "", "down"), "gone": (0, "", "")}))
    _stub_worker_for_readoption(disp, monkeypatch)
    now = 1.0 + host_health.QUIET_AFTER
    report = disp.run_one_cycle(now=now)
    assert disp.ledger.lease_for("s1").host == "big"             # readopted
    assert disp.ledger.lease_for("newcomer") is None              # fresh work still waits
    assert report.waiting == 1


# --- fix round 1, issue 2: due hosts are checked concurrently, not serially -------

class SlowScriptRunner:
    def __init__(self, answers, delay=0.3):
        self.answers, self.calls, self.delay = dict(answers), [], delay

    def __call__(self, argv, stdin, timeout):
        time.sleep(self.delay)
        target = next(a for a in argv if a in self.answers)
        self.calls.append(target)
        return self.answers[target]


def test_due_hosts_are_checked_concurrently(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 4, "hosts": {
        "h1": {"ssh": "h1", "capacity": {"cpu": 1}},
        "h2": {"ssh": "h2", "capacity": {"cpu": 1}},
        "h3": {"ssh": "h3", "capacity": {"cpu": 1}},
        "h4": {"ssh": "h4", "capacity": {"cpu": 1}}}})
    runner = SlowScriptRunner({"h1": (0, "", ""), "h2": (255, "", "h2 down"),
                               "h3": (0, "", ""), "h4": (255, "", "h4 down")}, delay=0.3)
    start = time.monotonic()
    entries = host_health.check(tmp_path, pool, now=1000.0, runner=runner)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"4 due hosts at 0.3s each took {elapsed:.2f}s — not concurrent"
    assert len(runner.calls) == 4 and set(runner.calls) == {"h1", "h2", "h3", "h4"}
    assert entries["h1"] == {"checked_at": 1000.0, "last_ok": 1000.0, "fail_since": 0.0, "reason": ""}
    assert entries["h3"] == {"checked_at": 1000.0, "last_ok": 1000.0, "fail_since": 0.0, "reason": ""}
    assert entries["h2"] == {"checked_at": 1000.0, "last_ok": 0.0, "fail_since": 1000.0, "reason": "h2 down"}
    assert entries["h4"] == {"checked_at": 1000.0, "last_ok": 0.0, "fail_since": 1000.0, "reason": "h4 down"}


# --- O20: the same check lists what the run root actually holds ---------------------

RUNS_POOL = {"cpu": 4, "hosts": {"big": {"ssh": "big", "run_root": "~/runs",
                                         "capacity": {"cpu": 16}}}}


def test_the_check_lists_the_run_directories_the_host_holds(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict(RUNS_POOL)
    runner = ScriptRunner({"big": (0, "s1\ns2\n\n", "")})
    entries = host_health.check(tmp_path, pool, now=1000.0, runner=runner)
    assert entries["big"]["run_dirs"] == ["s1", "s2"]
    command = runner.calls[0][-1]
    assert command.startswith("bash -c ") and "~/runs/*/" in command
    # Still a liveness check: the listing must not decide whether the host answered.
    assert entries["big"]["fail_since"] == 0.0 and entries["big"]["last_ok"] == 1000.0


def test_an_empty_run_root_is_recorded_as_empty_not_as_unknown(tmp_path, every_host_placeable):
    # "" and a missing key are different facts: nothing there vs. nobody looked.
    pool = ResourcePool.from_dict(RUNS_POOL)
    entries = host_health.check(tmp_path, pool, now=1000.0, runner=ScriptRunner({"big": (0, "", "")}))
    assert entries["big"]["run_dirs"] == []


def test_a_host_that_stops_answering_keeps_the_listing_it_last_gave(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict(RUNS_POOL)
    host_health.check(tmp_path, pool, now=1000.0, runner=ScriptRunner({"big": (0, "s1\n", "")}))
    entries = host_health.check(tmp_path, pool, now=1100.0,
                                runner=ScriptRunner({"big": (255, "", "timed out")}))
    assert entries["big"]["run_dirs"] == ["s1"]      # last known, not dropped on one failure
    assert entries["big"]["fail_since"] == 1100.0


def test_a_host_with_no_run_root_is_only_asked_whether_it_answers(tmp_path, every_host_placeable):
    pool = ResourcePool.from_dict({"cpu": 4, "hosts": {"big": {"ssh": "big", "capacity": {"cpu": 1}}}})
    runner = ScriptRunner({"big": (0, "unexpected\n", "")})
    entries = host_health.check(tmp_path, pool, now=1000.0, runner=runner)
    assert runner.calls[0][-1] == f"bash -c {shlex.quote(host_health._FREE_KB)}"
    assert "run_dirs" not in entries["big"]


def test_a_run_root_the_platform_will_not_name_is_never_pasted_into_a_command(
        tmp_path, every_host_placeable):
    # Same rule as a collect path: under ~/ or absolute, safe characters, no `..`.
    pool = ResourcePool.from_dict({"cpu": 4, "hosts": {
        "big": {"ssh": "big", "run_root": "~/runs; rm -rf ~", "capacity": {"cpu": 1}}}})
    runner = ScriptRunner({"big": (0, "", "")})
    entries = host_health.check(tmp_path, pool, now=1000.0, runner=runner)
    assert runner.calls[0][-1] == f"bash -c {shlex.quote(host_health._FREE_KB)}"
    assert "run_dirs" not in entries["big"]


# --- O7 task 2: stranded leases, an isolated beat, and a stop that failed is named -

def test_a_lease_on_a_removed_host_is_stranded_and_leaves_pool_wide_use(tmp_path, every_host_placeable):
    led = Ledger(ResourcePool.from_dict(POOL), tmp_path / "leases.json")
    led.load()
    led.acquire("s1", {"cpu": 8, "workers": 1}, now=0.0, ttl=600.0, host="gone")
    led.save()
    smaller = Ledger(ResourcePool.from_dict({"cpu": 4, "workers": 4, "hosts": {"big": POOL["hosts"]["big"]}}),
                     tmp_path / "leases.json")
    smaller.load()
    assert [l.sprint_id for l in smaller.stranded()] == ["s1"]
    assert smaller.used()["cpu"] == 0.0 and smaller.used()["workers"] == 1.0
    assert smaller.available()["cpu"] == 20.0


def test_a_lease_on_a_not_placeable_host_is_stranded_and_leaves_pool_wide_use(tmp_path, monkeypatch):
    """R11: a lease on a remote host that merely isn't placeable right now (remote
    launch turned off, COSCIENCE_ALLOW_REMOTE unset) is stranded exactly as one on a
    host removed from the pool — kept, but no longer counted against pool-wide use."""
    from coscience.models import Lease
    monkeypatch.delenv("COSCIENCE_ALLOW_REMOTE", raising=False)
    led = Ledger(ResourcePool.from_dict(POOL), tmp_path / "leases.json")
    led.load()
    led._leases["s1"] = Lease(id="l1", sprint_id="s1", amounts={"cpu": 8.0, "workers": 1.0},
                              granted_at=0.0, expires_at=600.0, priority=0, preemptible=True, host="big")
    led._keys_ever_leased.update(led._leases["s1"].amounts.keys())
    led.save()

    fresh = Ledger(ResourcePool.from_dict(POOL), tmp_path / "leases.json")
    fresh.load()
    assert not fresh.pool.host("big").placeable
    assert [l.sprint_id for l in fresh.stranded()] == ["s1"]
    assert fresh.used()["cpu"] == 0.0 and fresh.used()["workers"] == 1.0


def test_one_failing_beat_does_not_stop_the_others(substrate, monkeypatch):
    from coscience.dispatcher import Dispatcher
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict({"cpu": 4}))
    for sid in ("s1", "s2"):
        substrate.save_sprint(_sprint(sid, {"cpu": 1}, status=SprintStatus.EXECUTING))
        disp.ledger.acquire(sid, {"cpu": 1}, now=0.0, ttl=600.0)
    disp.ledger.save()
    beaten = []

    def beat(sprint):
        beaten.append(sprint.id)
        if sprint.id == "s1":
            raise RuntimeError("boom")
        return "idle"
    monkeypatch.setattr(disp.worker, "run_sprint_beat", beat)
    report = disp.run_one_cycle(now=1.0)
    assert sorted(beaten) == ["s1", "s2"] and report.beat_errors == ["s1"]
    assert "boom" in substrate.load_progress("s1").last_error
    assert disp.ledger.lease_for("s1") is not None


# --- Fix D: beat errors clear, and a beat that always fails gives up ---------------

def test_a_beat_that_fails_once_then_succeeds_clears_the_error(substrate, monkeypatch):
    from coscience.dispatcher import Dispatcher
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict({"cpu": 4}))
    substrate.save_sprint(_sprint("s1", {"cpu": 1}, status=SprintStatus.EXECUTING))
    disp.ledger.acquire("s1", {"cpu": 1}, now=0.0, ttl=600.0)
    disp.ledger.save()

    calls = {"n": 0}

    def beat(sprint):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return "idle"
    monkeypatch.setattr(disp.worker, "run_sprint_beat", beat)

    report1 = disp.run_one_cycle(now=1.0)
    assert report1.beat_errors == ["s1"]
    progress = substrate.load_progress("s1")
    assert progress.beat_failures == 1 and progress.last_error.startswith("beat failed:")

    report2 = disp.run_one_cycle(now=2.0)
    assert report2.beat_errors == []
    progress = substrate.load_progress("s1")
    assert progress.beat_failures == 0 and progress.last_error == ""
    assert disp.ledger.lease_for("s1") is not None


def test_a_beat_that_always_fails_gives_up_after_the_cap(substrate, monkeypatch):
    from coscience.dispatcher import Dispatcher
    from coscience.worker import MAX_AGENT_FAILURES
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict({"cpu": 4}))
    substrate.save_sprint(_sprint("s1", {"cpu": 1}, status=SprintStatus.EXECUTING))
    disp.ledger.acquire("s1", {"cpu": 1}, now=0.0, ttl=600.0)
    disp.ledger.save()

    def beat(sprint):
        raise RuntimeError("boom")
    monkeypatch.setattr(disp.worker, "run_sprint_beat", beat)

    # Round 2, gap 5: record stop_sprint calls (real behavior underneath, so the
    # lease/sprint/lock state still ends up right) and spy on ledger.renew so a
    # released lease is provably never renewed again.
    stopped = []
    real_stop = disp.worker.stop_sprint
    def stop_sprint(sprint, **kw):
        stopped.append(sprint.id)
        return real_stop(sprint, **kw)
    monkeypatch.setattr(disp.worker, "stop_sprint", stop_sprint)

    renewed = []
    real_renew = disp.ledger.renew
    def renew(sprint_id, *a, **kw):
        renewed.append(sprint_id)
        return real_renew(sprint_id, *a, **kw)
    monkeypatch.setattr(disp.ledger, "renew", renew)

    for i in range(MAX_AGENT_FAILURES - 1):
        report = disp.run_one_cycle(now=float(i + 1))
        assert report.beat_errors == ["s1"]
        assert disp.ledger.lease_for("s1") is not None
        assert substrate.load_sprint("s1").status == SprintStatus.EXECUTING

    renewed.clear()
    report = disp.run_one_cycle(now=float(MAX_AGENT_FAILURES + 1))
    assert report.beat_errors == ["s1"]
    assert disp.ledger.lease_for("s1") is None
    sprint = substrate.load_sprint("s1")
    assert sprint.status == SprintStatus.FAILED
    progress = substrate.load_progress("s1")
    assert "boom" in progress.last_error
    assert stopped == ["s1"]              # stop_sprint called exactly once for s1
    assert "s1" not in renewed            # the just-released lease is never renewed


def test_the_beat_cap_failure_is_committed_that_cycle(substrate, monkeypatch):
    from coscience.dispatcher import Dispatcher
    from coscience.worker import MAX_AGENT_FAILURES
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict({"cpu": 4}))
    substrate.save_sprint(_sprint("s1", {"cpu": 1}, status=SprintStatus.EXECUTING))
    disp.ledger.acquire("s1", {"cpu": 1}, now=0.0, ttl=600.0)
    disp.ledger.save()

    def beat(sprint):
        raise RuntimeError("boom")
    monkeypatch.setattr(disp.worker, "run_sprint_beat", beat)

    for i in range(MAX_AGENT_FAILURES - 1):
        disp.run_one_cycle(now=float(i + 1))

    calls = []
    monkeypatch.setattr(substrate, "commit", lambda msg: calls.append(msg))
    disp.run_one_cycle(now=float(MAX_AGENT_FAILURES + 1))
    assert calls, "the cycle that FAILs the sprint must commit"


def test_a_raising_stop_sprint_does_not_abort_the_cycle(substrate, monkeypatch):
    from coscience.dispatcher import Dispatcher
    from coscience.worker import MAX_AGENT_FAILURES
    disp = Dispatcher(substrate, FakeAgent(), ResourcePool.from_dict({"cpu": 4}))
    for sid in ("s1", "s2"):
        substrate.save_sprint(_sprint(sid, {"cpu": 1}, status=SprintStatus.EXECUTING))
        disp.ledger.acquire(sid, {"cpu": 1}, now=0.0, ttl=600.0)
    disp.ledger.save()

    beaten = []
    def beat(sprint):
        beaten.append((sprint.id))
        if sprint.id == "s1":
            raise RuntimeError("boom")
        return "idle"
    monkeypatch.setattr(disp.worker, "run_sprint_beat", beat)

    def stop_sprint(sprint, **kw):
        raise RuntimeError("cannot stop it")
    monkeypatch.setattr(disp.worker, "stop_sprint", stop_sprint)

    for i in range(MAX_AGENT_FAILURES - 1):
        disp.run_one_cycle(now=float(i + 1))

    beaten.clear()
    report = disp.run_one_cycle(now=float(MAX_AGENT_FAILURES + 1))   # must not raise
    assert sorted(beaten) == ["s1", "s2"]           # s2's beat still ran this cycle
    assert report.beat_errors == ["s1"]
    sprint = substrate.load_sprint("s1")
    assert sprint.status == SprintStatus.FAILED
    assert disp.ledger.lease_for("s1") is None
    progress = substrate.load_progress("s1")
    assert "boom" in progress.last_error
    assert "stopping it also failed" in progress.last_error
    assert "cannot stop it" in progress.last_error
    assert disp.ledger.lease_for("s2") is not None   # the other sprint is unaffected
