"""O7: per-host health and use on the Compute page. O15: mark a server for removal
(Remove) and take the mark back (Keep) — the dispatcher does the actual deleting
(see tests/test_host_removal.py)."""
import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.ledger import Ledger
from coscience.models import ProgressState, Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service

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


def test_each_host_reports_health_use_and_leases(tmp_path, every_host_placeable, monkeypatch):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 6, "memory_gb": 10}, "big")
    (tmp_path / ".coscience" / "host-health.json").write_text(json.dumps(
        {"big": {"checked_at": 5.0, "last_ok": 5.0, "fail_since": 0.0, "reason": ""}}))
    monkeypatch.setattr("time.time", lambda: 5.0)          # M3: a stale checked_at reads unchecked
    status = svc.ledger_status()
    big = _host(status, "big")
    assert big["health"]["state"] == "ok" and big["drain"] is False and big["removing"] is False
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


def test_this_machine_cannot_be_marked_or_kept_and_an_unknown_host_is_not_found(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="Pause"):
        svc.remove_host("local")
    with pytest.raises(ValueError, match="nothing to keep"):
        svc.keep_host("local")
    with pytest.raises(NotFoundError):
        svc.remove_host("nope")
    with pytest.raises(NotFoundError):
        svc.keep_host("nope")


def test_remove_host_marks_keeps_every_other_key_and_is_idempotent(tmp_path):
    svc = _svc(tmp_path)
    status = svc.remove_host("big")
    big = _host(status, "big")
    assert big["removing"] is True
    doc = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert doc["hosts"]["big"] == {"ssh": "big", "run_root": "~/runs",
                                   "capacity": {"cpu": 16, "memory_gb": 64}, "remove": True}
    # A second call is harmless: still marked, nothing else changes.
    status = svc.remove_host("big")
    assert _host(status, "big")["removing"] is True


def test_keep_host_clears_remove_drain_and_drained_at(tmp_path):
    svc = _svc(tmp_path, text=("cpu: 4\nworkers: 4\nhosts:\n"
                               "  big:\n    ssh: big\n    remove: true\n    drain: true\n"
                               "    drained_at: 1.0\n    capacity: {cpu: 16}\n"))
    status = svc.keep_host("big")
    big = _host(status, "big")
    assert big["removing"] is False and big["drain"] is False and big["drained_at"] == 0.0
    doc = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    entry = doc["hosts"]["big"]
    assert "remove" not in entry and "drain" not in entry and "drained_at" not in entry


def test_a_hand_written_non_bool_remove_is_a_pool_parse_error(tmp_path):
    # A hand-edited `remove: "yes"` (a string, not a real bool) must never be read as
    # true by the parser — the same treatment `drain` gets — so the entry is dropped
    # with a reported error rather than silently marked for removal.
    svc = _svc(tmp_path, text=("cpu: 4\nworkers: 4\nhosts:\n"
                               "  big:\n    ssh: big\n    remove: \"yes\"\n"
                               "    capacity: {cpu: 16}\n"))
    errors = svc.pool.host_errors
    assert any("remove" in e and "must be true or false" in e for e in errors)


def test_a_null_host_entry_is_a_422_not_a_500(tmp_path):
    svc = _svc(tmp_path, text="cpu: 4\nworkers: 4\nhosts:\n  big:\n")
    with pytest.raises(ValueError, match="is not a mapping"):
        svc.remove_host("big")
    with pytest.raises(ValueError, match="is not a mapping"):
        svc.keep_host("big")


def test_ledger_status_shows_waiting_on_for_a_marked_host_with_a_lease(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 2}, "big")
    svc.remove_host("big")
    big = _host(svc.ledger_status(), "big")
    assert big["waiting_on"] == [{"sprint_id": "s1", "status": "unknown", "reason": "holds a lease here"}]


def test_ledger_status_waiting_on_is_empty_for_an_unmarked_host(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    _lease(tmp_path, "s1", {"cpu": 2}, "big")
    big = _host(svc.ledger_status(), "big")
    assert big["waiting_on"] == []


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
    # A hand-written `drain: true` still keeps new work off a server (there is no
    # service call to set it any more — only Keep clears it).
    svc = _svc(tmp_path, text=("cpu: 4\nworkers: 4\nhosts:\n"
                               "  big:\n    ssh: big\n    drain: true\n"
                               "    capacity: {cpu: 16, memory_gb: 64}\n"))
    sprint = Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["a"], resources_required={"cpu": 2})
    svc.substrate.save_sprint(sprint)
    svc.substrate.save_progress(ProgressState(sprint_id="s1", host="big"))
    # M6 (fix round 1): the button is now Keep, not "take back" — the wording follows.
    assert svc._unrunnable(sprint, svc.pool) == (
        "big is draining and this sprint is pinned there: keep the server or stop the sprint")


def test_a_sprint_pinned_to_a_marked_host_says_so(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    svc.remove_host("big")
    sprint = Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["a"], resources_required={"cpu": 2})
    svc.substrate.save_sprint(sprint)
    svc.substrate.save_progress(ProgressState(sprint_id="s1", host="big"))
    assert svc._unrunnable(sprint, svc.pool) == (
        "big is being removed and this sprint's work is there: stop the sprint, or keep the server")


def test_a_sprint_with_a_pending_reallocate_to_a_missing_server_is_reported(tmp_path, every_host_placeable):
    # I1 (fix round 1): the reallocate target is deleted (say, the dispatcher
    # removed it) before the answer's grant landed — `_unrunnable` now pins on
    # `reallocate_to or host`, the same as the dispatcher's grant step, so this
    # sprint is reported instead of silently waiting forever with no reason.
    svc = _svc(tmp_path)
    sprint = Sprint(id="s1", status=SprintStatus.QUEUED, goals="g", plan=["a"], resources_required={"cpu": 2})
    svc.substrate.save_sprint(sprint)
    svc.substrate.save_progress(ProgressState(sprint_id="s1", reallocate_to="gone"))
    assert svc._unrunnable(sprint, svc.pool) == (
        "its work is on host gone, which is not in the pool or not taking work")


def test_the_http_routes_remove_and_keep(client, every_host_placeable):
    (client.svc.repo_root / ".coscience").mkdir(parents=True, exist_ok=True)
    (client.svc.repo_root / ".coscience" / "resources.yaml").write_text(POOL_YAML)
    r = client.delete("/api/hosts/big")
    assert r.status_code == 200 and _host(r.json(), "big")["removing"] is True
    assert client.delete("/api/hosts/nope").status_code == 404
    assert client.delete("/api/hosts/local").status_code == 422
    r = client.post("/api/hosts/big/keep")
    assert r.status_code == 200 and _host(r.json(), "big")["removing"] is False
    # The route is gone: PUT .../drain no longer matches anything.
    assert client.put("/api/hosts/big/drain", json={"drain": True}).status_code in (404, 405)
