"""O7: per-host health and use on the Compute page; drain and remove."""
import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.ledger import Ledger
from coscience.models import ProgressState, Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.service import REMOVE_AFTER_DRAIN, NotFoundError, Service

POOL_YAML = ("cpu: 4\nworkers: 4\nhosts:\n"
             "  big:\n    ssh: big\n    run_root: ~/runs\n    capacity: {cpu: 16, memory_gb: 64}\n")


@pytest.fixture
def client(tmp_path):
    # R8: no write_pool helper exists in tests/test_http_api.py's client fixture, so
    # this test file defines its own equivalent (same shape as that fixture).
    svc = Service(tmp_path)
    c = TestClient(build_app(svc))
    c.svc = svc
    return c


def _svc(tmp_path, text=POOL_YAML):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)
    return Service(tmp_path)


def _lease(tmp_path, sid, amounts, host):
    led = Ledger(ResourcePool.from_yaml(tmp_path / ".coscience" / "resources.yaml"),
                 tmp_path / ".coscience" / "leases.json")
    led.load()
    assert led.acquire(sid, amounts, now=0.0, ttl=1e9, host=host) is not None
    led.save()


def _host(status, name):
    return next(h for h in status["hosts"] if h["name"] == name)


def _backdate_drain(tmp_path, name, ago):
    path = tmp_path / ".coscience" / "resources.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["hosts"][name]["drained_at"] = time.time() - ago
    path.write_text(yaml.safe_dump(doc))


def test_each_host_reports_health_use_and_leases(tmp_path, every_host_placeable, monkeypatch):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 6, "memory_gb": 10}, "big")
    (tmp_path / ".coscience" / "host-health.json").write_text(json.dumps(
        {"big": {"checked_at": 5.0, "last_ok": 5.0, "fail_since": 0.0, "reason": ""}}))
    monkeypatch.setattr("time.time", lambda: 5.0)          # M3: a stale checked_at reads unchecked
    status = svc.ledger_status()
    big = _host(status, "big")
    assert big["health"]["state"] == "ok" and big["drain"] is False
    assert big["used"] == {"cpu": 6.0, "memory_gb": 10.0} and big["leases"] == 1
    assert _host(status, "local")["health"]["state"] == "local"
    assert status["stranded"] == []


def test_a_host_that_stopped_answering_long_ago_reads_quiet(tmp_path, every_host_placeable, monkeypatch):
    from coscience import host_health
    svc = _svc(tmp_path)
    (tmp_path / ".coscience" / "host-health.json").write_text(json.dumps(
        {"big": {"checked_at": 100.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "No route"}}))
    monkeypatch.setattr("time.time", lambda: 2.0 + host_health.QUIET_AFTER)
    assert _host(svc.ledger_status(), "big")["health"] == {
        "state": "quiet", "checked_at": 100.0, "last_ok": 0.0, "fail_since": 1.0, "reason": "No route"}


def test_draining_is_written_to_the_pool_file_and_undone(tmp_path):
    svc = _svc(tmp_path)
    assert _host(svc.set_host_drain("big", True), "big")["drain"] is True
    assert yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["big"]["drain"] is True
    assert _host(svc.set_host_drain("big", False), "big")["drain"] is False
    assert "drain" not in yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"]["big"]


def test_this_machine_and_unknown_hosts_cannot_be_drained_or_removed(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="Pause"):
        svc.set_host_drain("local", True)
    with pytest.raises(NotFoundError):
        svc.set_host_drain("nope", True)
    with pytest.raises(ValueError, match="Pause"):
        svc.remove_host("local")
    with pytest.raises(NotFoundError):
        svc.remove_host("nope")


def test_a_host_is_removed_only_when_drained_and_empty(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 2}, "big")          # granted before the drain: a drained host takes none
    with pytest.raises(ValueError, match="drain big before removing it"):
        svc.remove_host("big")
    svc.set_host_drain("big", True)
    with pytest.raises(ValueError, match="drained less than 2 minutes ago"):
        svc.remove_host("big")
    _backdate_drain(tmp_path, "big", REMOVE_AFTER_DRAIN + 1)
    with pytest.raises(ValueError, match="1 sprint still holds a lease on big"):
        svc.remove_host("big")
    (tmp_path / ".coscience" / "leases.json").write_text("[]")
    status = svc.remove_host("big")
    assert [h["name"] for h in status["hosts"]] == ["local"]
    assert "big" not in (yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text()).get("hosts") or {})


def test_a_host_with_unfinished_work_cannot_be_removed(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    svc.set_host_drain("big", True)
    _backdate_drain(tmp_path, "big", REMOVE_AFTER_DRAIN + 1)
    sub = svc.substrate

    # An EXECUTING sprint physically on big (host + job_host), no lease: removing
    # would orphan the job — reconcile can't kill it (host gone) and can't re-adopt it.
    sub.save_sprint(Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"]))
    sub.save_progress(ProgressState(sprint_id="s1", host="big", job_token="big:4242:777:b", job_host="big"))
    with pytest.raises(ValueError, match="unfinished sprint.*s1"):
        svc.remove_host("big")

    # Finish s1; a QUEUED sprint pinned to big alone also blocks.
    sub.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", plan=["a"]))
    sub.save_sprint(Sprint(id="s2", status=SprintStatus.QUEUED, goals="g", plan=["a"]))
    sub.save_progress(ProgressState(sprint_id="s2", host="big"))
    with pytest.raises(ValueError, match="unfinished sprint.*s2"):
        svc.remove_host("big")

    # A DONE sprint with host=big is a leftover, not unfinished work — doesn't block.
    sub.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", plan=["a"]))
    status = svc.remove_host("big")
    assert [h["name"] for h in status["hosts"]] == ["local"]


def test_a_hand_written_non_bool_drain_does_not_let_remove_through(tmp_path):
    # M4/round 2: a hand-edited resources.yaml with `drain: "yes"` (a string, not a
    # real bool) must never be treated as "drained" by remove_host — read with
    # `is True`, so it is refused exactly like an undrained host, and the host
    # survives untouched either way.
    svc = _svc(tmp_path, text=("cpu: 4\nworkers: 4\nhosts:\n"
                               "  big:\n    ssh: big\n    drain: \"yes\"\n"
                               "    capacity: {cpu: 16}\n"))
    with pytest.raises(ValueError, match="drain big before removing it"):
        svc.remove_host("big")
    doc = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert "big" in doc["hosts"]             # never removed


def test_a_null_host_entry_is_a_422_not_a_500(tmp_path):
    svc = _svc(tmp_path, text="cpu: 4\nworkers: 4\nhosts:\n  big:\n")
    with pytest.raises(ValueError, match="is not a mapping"):
        svc.set_host_drain("big", True)
    with pytest.raises(ValueError, match="is not a mapping"):
        svc.remove_host("big")


def test_draining_writes_a_numeric_drained_at_and_take_back_clears_it(tmp_path):
    svc = _svc(tmp_path)
    before = time.time()
    svc.set_host_drain("big", True)
    doc = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    drained_at = doc["hosts"]["big"]["drained_at"]
    assert isinstance(drained_at, (int, float)) and drained_at >= before
    svc.set_host_drain("big", False)
    doc = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert "drained_at" not in doc["hosts"]["big"]


def test_finished_sprints_list_the_run_directories_they_left(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    sub = svc.substrate
    for sid, status in (("s1", SprintStatus.DONE), ("s2", SprintStatus.EXECUTING), ("s3", SprintStatus.FAILED)):
        sub.save_sprint(Sprint(id=sid, status=status, goals="g", plan=["a"]))
        sub.save_progress(ProgressState(sprint_id=sid, host="big"))
    assert _host(svc.ledger_status(), "big")["leftover"] == [
        {"sprint_id": "s1", "status": "done", "path": "~/runs/s1"},
        {"sprint_id": "s3", "status": "failed", "path": "~/runs/s3"}]


def test_a_lease_on_a_removed_host_is_listed_as_stranded(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 2}, "big")
    (tmp_path / ".coscience" / "resources.yaml").write_text("cpu: 4\nworkers: 4\n")
    assert svc.ledger_status()["stranded"] == [{"sprint_id": "s1", "host": "big", "listed": False}]


def test_a_lease_on_a_host_that_is_merely_not_placeable_is_stranded_and_listed(tmp_path, monkeypatch):
    from coscience.models import Lease
    monkeypatch.delenv("COSCIENCE_ALLOW_REMOTE", raising=False)
    svc = _svc(tmp_path)
    led = Ledger(ResourcePool.from_yaml(tmp_path / ".coscience" / "resources.yaml"),
                 tmp_path / ".coscience" / "leases.json")
    led.load()
    led._leases["s1"] = Lease(id="l1", sprint_id="s1", amounts={"cpu": 2.0},
                              granted_at=0.0, expires_at=600.0, priority=0, preemptible=True, host="big")
    led._keys_ever_leased.update(led._leases["s1"].amounts.keys())
    led.save()
    assert svc.ledger_status()["stranded"] == [{"sprint_id": "s1", "host": "big", "listed": True}]


def test_a_sprint_pinned_to_a_draining_host_says_so(tmp_path, every_host_placeable):
    # R9: _unrunnable(sprint, pool) takes the pool; the service reads it live via
    # svc.pool, so the test passes it explicitly rather than the brief's no-arg call.
    svc = _svc(tmp_path)
    svc.set_host_drain("big", True)
    sprint = Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["a"], resources_required={"cpu": 2})
    svc.substrate.save_sprint(sprint)
    svc.substrate.save_progress(ProgressState(sprint_id="s1", host="big"))
    assert "big is draining" in svc._unrunnable(sprint, svc.pool)


def test_the_http_routes_drain_and_remove(client, every_host_placeable):
    (client.svc.repo_root / ".coscience").mkdir(parents=True, exist_ok=True)
    (client.svc.repo_root / ".coscience" / "resources.yaml").write_text(POOL_YAML)
    r = client.put("/api/hosts/big/drain", json={"drain": True})
    assert r.status_code == 200 and _host(r.json(), "big")["drain"] is True
    assert client.delete("/api/hosts/nope").status_code == 404
    assert client.put("/api/hosts/local/drain", json={"drain": True}).status_code == 422
    assert client.delete("/api/hosts/big").status_code == 422   # drained moments ago (Fix B)
    _backdate_drain(client.svc.repo_root, "big", REMOVE_AFTER_DRAIN + 1)
    r = client.delete("/api/hosts/big")
    assert r.status_code == 200 and [h["name"] for h in r.json()["hosts"]] == ["local"]
