"""O14: which programs a server takes, editable from either side, and `local` too."""
import subprocess

import pytest
import yaml

from coscience.models import Program, ProgramStatus, ProgressState
from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service


def pool(d):
    return ResourcePool.from_dict(d)


# --- Host.allows / ResourcePool parsing (Step 1) ---------------------------

def test_absent_keys_admit_every_program():
    p = pool({"cpu": 4, "hosts": {"a": {"ssh": "a"}}})
    assert p.host("local").allows("p1") and p.host("a").allows("p1") and p.host("a").allows(None)


def test_only_list_admits_listed_programs():
    h = pool({"hosts": {"a": {"ssh": "a", "programs": ["p1"]}}}).host("a")
    assert h.allows("p1") and not h.allows("p2") and not h.allows(None)


def test_exclude_list_admits_everyone_else():
    h = pool({"hosts": {"a": {"ssh": "a", "exclude_programs": ["p4"]}}}).host("a")
    assert not h.allows("p4") and h.allows("p1") and h.allows("p-created-later") and h.allows(None)


def test_both_lists_on_a_server_is_an_error():
    p = pool({"hosts": {"a": {"ssh": "a", "programs": ["p1"], "exclude_programs": ["p4"]}}})
    assert p.host("a") is None
    assert any("programs and exclude_programs" in e for e in p.host_errors)


def test_local_takes_top_level_access():
    p = pool({"cpu": 4, "exclude_programs": ["p4"]})
    assert not p.host("local").allows("p4") and p.host("local").allows("p1")
    assert p.capacity["cpu"] == 4                     # access keys are not amounts


def test_local_access_inside_a_resources_wrapper():
    p = pool({"resources": {"cpu": 4, "programs": ["p1"]}})
    assert p.host("local").allows("p1") and not p.host("local").allows("p2")


def test_malformed_local_access_is_reported_and_leaves_local_open():
    p = pool({"cpu": 4, "programs": "p1"})
    assert p.host("local").allows("p2")
    assert any("programs" in e for e in p.host_errors)


def test_hand_written_empty_only_list_still_means_every_program():
    assert pool({"hosts": {"a": {"ssh": "a", "programs": []}}}).host("a").allows("p9")


# --- Service / routes fixtures ---------------------------------------------

def _write(tmp_path, text):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def _read(tmp_path):
    return yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())


def _svc_with_program(tmp_path, resources_yaml, *program_ids):
    _write(tmp_path, resources_yaml)
    svc = Service(tmp_path)
    for pid in program_ids:
        svc.substrate.save_program(Program(id=pid, title=pid, goals="g", status=ProgramStatus.ACTIVE))
    return svc


A_WITH_PROGRAMS = ("cpu: 4\nhosts:\n  a:\n    ssh: a\n    programs: [p1]\n"
                  "    capacity: {cpu: 8}\n    drain: true\n    drained_at: 5.0\n"
                  "    notes: nights only\n    gpus:\n      - {model: X, vram_gb: 11}\n")


# --- set_host_programs (server side) ---------------------------------------

def test_set_host_programs_writes_and_removes_keys_leaving_the_rest_alone(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)

    svc.set_host_programs("a", ["p1"], [])
    e = _read(tmp_path)["hosts"]["a"]
    assert e["programs"] == ["p1"] and "exclude_programs" not in e
    assert (e["drain"], e["drained_at"], e["capacity"], e["notes"]) == \
        (True, 5.0, {"cpu": 8}, "nights only")
    assert e["gpus"] == [{"model": "X", "vram_gb": 11.0}]

    svc.set_host_programs("a", [], ["p4"])
    e = _read(tmp_path)["hosts"]["a"]
    assert e["exclude_programs"] == ["p4"] and "programs" not in e
    assert (e["drain"], e["drained_at"], e["capacity"], e["notes"]) == \
        (True, 5.0, {"cpu": 8}, "nights only")

    svc.set_host_programs("a", [], [])
    e = _read(tmp_path)["hosts"]["a"]
    assert "programs" not in e and "exclude_programs" not in e


def test_set_host_programs_local_top_level_keeps_cpu_gpus_and_hosts(tmp_path):
    text = ("cpu: 4\ngpus:\n  - {model: X, vram_gb: 11}\n"
            "hosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n")
    svc = _svc_with_program(tmp_path, text)
    svc.set_host_programs("local", [], ["p4"])
    data = _read(tmp_path)
    assert data["exclude_programs"] == ["p4"]
    assert data["cpu"] == 4 and data["gpus"] == [{"model": "X", "vram_gb": 11.0}]
    assert "a" in data["hosts"]


def test_set_host_programs_local_inside_a_resources_wrapper(tmp_path):
    svc = _svc_with_program(tmp_path, "resources:\n  cpu: 4\n")
    svc.set_host_programs("local", [], ["p4"])
    data = _read(tmp_path)
    assert data["resources"]["exclude_programs"] == ["p4"]
    assert "exclude_programs" not in data


def test_set_host_programs_local_ignores_unrelated_pre_existing_host_errors(tmp_path):
    # `bad` is missing `ssh`, an unrelated host_errors entry that must not block
    # editing local's own access.
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  bad:\n    capacity: {cpu: 1}\n")
    svc.set_host_programs("local", [], ["p4"])
    assert _read(tmp_path)["exclude_programs"] == ["p4"]


def test_set_host_programs_local_ignores_another_servers_broken_programs(tmp_path):
    # The other server's error mentions `programs` too; it still must not block local.
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  b:\n    ssh: b\n    programs: bad\n")
    svc.set_host_programs("local", [], ["p1"])
    assert _read(tmp_path)["exclude_programs"] == ["p1"]


def test_set_host_programs_with_nothing_to_change_makes_no_commit(tmp_path):
    _git_repo(tmp_path)
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    svc.set_host_programs("local", [], ["p4"])
    head = lambda: subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout
    before = head()
    svc.set_host_programs("local", [], ["p4"])
    assert head() == before and before


def test_set_host_programs_both_lists_is_an_error(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    with pytest.raises(ValueError):
        svc.set_host_programs("a", ["p1"], ["p4"])


def test_set_host_programs_unknown_server_raises(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    with pytest.raises(NotFoundError):
        svc.set_host_programs("nope", ["p1"], [])


# --- set_program_hosts (program side) ---------------------------------------

def test_set_program_hosts_allow_new_server_appends_and_excludes_local(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    programs: [p1]\n"
                            "    capacity: {cpu: 8}\n", "p4")
    result = svc.set_program_hosts("p4", ["a"])
    data = _read(tmp_path)
    assert data.get("exclude_programs") == ["p4"]
    assert data["hosts"]["a"]["programs"] == ["p1", "p4"]
    assert result["cut_off"] == []


def test_set_program_hosts_restrict_to_local_appends_to_exclude_list(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    exclude_programs: [p2]\n"
                            "    capacity: {cpu: 8}\n", "p4")
    svc.set_program_hosts("p4", ["local"])
    data = _read(tmp_path)
    assert data["hosts"]["a"]["exclude_programs"] == ["p2", "p4"]
    assert "programs" not in data and "exclude_programs" not in data   # local stays unrestricted


def test_set_program_hosts_allow_everywhere_removes_the_key_not_leaves_it_empty(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nexclude_programs: [p4]\nhosts:\n  a:\n    ssh: a\n"
                            "    capacity: {cpu: 8}\n", "p4")
    svc.set_program_hosts("p4", ["local", "a"])
    data = _read(tmp_path)
    assert "exclude_programs" not in data
    assert "programs" not in data


def test_set_program_hosts_removing_the_only_program_is_refused(tmp_path):
    text = "cpu: 4\nhosts:\n  a:\n    ssh: a\n    programs: [p4]\n    capacity: {cpu: 8}\n"
    svc = _svc_with_program(tmp_path, text, "p4")
    before = (tmp_path / ".coscience" / "resources.yaml").read_text()
    with pytest.raises(ValueError, match="only program"):
        svc.set_program_hosts("p4", [])
    assert (tmp_path / ".coscience" / "resources.yaml").read_text() == before


def test_set_program_hosts_unknown_server_and_unknown_program(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n", "p4")
    with pytest.raises(ValueError, match="nope"):
        svc.set_program_hosts("p4", ["nope"])
    with pytest.raises(NotFoundError):
        svc.set_program_hosts("nope-program", ["local"])


def test_set_program_hosts_refuses_a_server_deleted_since_before_the_call(tmp_path):
    # I2 (fix round 1): the unknown-server check and the edit are now both computed
    # from the document read under the lock, not from an earlier unlocked `self.pool`
    # read — so a server gone by the time this runs (e.g. the dispatcher deleted it
    # moments ago) is a clean "no server named" 422, never a recreated bare entry
    # (which used to fail deep inside `_parse_host` with a baffling "needs ssh").
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n", "p4")
    (tmp_path / ".coscience" / "resources.yaml").write_text("cpu: 4\n")   # "a" is gone
    with pytest.raises(ValueError, match="no server named 'a'"):
        svc.set_program_hosts("p4", ["a"])
    assert "a" not in (_read(tmp_path).get("hosts") or {})


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)


def _head(tmp_path):
    return subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_set_program_hosts_with_nothing_to_change_writes_and_commits_nothing(tmp_path):
    _git_repo(tmp_path)
    svc = _svc_with_program(tmp_path, "cpu: 4\n", "p4")
    svc.substrate.commit("initial")
    before = _head(tmp_path)
    result = svc.set_program_hosts("p4", ["local"])          # local already allows everything
    assert _head(tmp_path) == before
    assert result["cut_off"] == []


# --- cut-off reporting -------------------------------------------------------

def test_cut_off_pins_are_reported_and_done_sprints_are_not(tmp_path):
    from tests.conftest import write_raw_sprint
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n", "p4")
    write_raw_sprint(tmp_path, "s-running", "executing", "g", ["x"], program="p4")
    svc.substrate.save_progress(ProgressState(sprint_id="s-running", host="a"))
    write_raw_sprint(tmp_path, "s-done", "done", "g", ["x"], program="p4")
    svc.substrate.save_progress(ProgressState(sprint_id="s-done", host="a"))

    result = svc.set_program_hosts("p4", ["local"])
    assert result["cut_off"] == [{"sprint_id": "s-running", "host": "a"}]


def test_set_host_programs_reports_cut_off_pins(tmp_path):
    from tests.conftest import write_raw_sprint
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n", "p4")
    write_raw_sprint(tmp_path, "s-running", "executing", "g", ["x"], program="p4")
    svc.substrate.save_progress(ProgressState(sprint_id="s-running", host="a"))

    result = svc.set_host_programs("a", [], ["p4"])
    assert result["cut_off"] == [{"sprint_id": "s-running", "host": "a"}]


def test_update_host_reports_cut_off_pins(tmp_path):
    from tests.conftest import write_raw_sprint
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n", "p4")
    write_raw_sprint(tmp_path, "s-running", "executing", "g", ["x"], program="p4")
    svc.substrate.save_progress(ProgressState(sprint_id="s-running", host="a"))

    result = svc.update_host("a", exclude_programs=["p4"], programs=[])
    assert result["cut_off"] == [{"sprint_id": "s-running", "host": "a"}]


# --- update_host --------------------------------------------------------------

def test_update_host_writes_exclude_programs_and_drops_programs(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    svc.update_host("a", exclude_programs=["p4"], programs=[])
    e = _read(tmp_path)["hosts"]["a"]
    assert e["exclude_programs"] == ["p4"] and "programs" not in e


# --- ledger_status ------------------------------------------------------------

def test_ledger_status_carries_exclude_programs_for_every_host(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    exclude_programs: [p4]\n"
                            "    capacity: {cpu: 8}\n  b:\n    ssh: b\n    capacity: {cpu: 8}\n")
    status = svc.ledger_status()
    by_name = {h["name"]: h for h in status["hosts"]}
    assert by_name["local"]["exclude_programs"] == []
    assert by_name["a"]["exclude_programs"] == ["p4"]
    assert by_name["b"]["exclude_programs"] == []


# --- set_capacity ---------------------------------------------------------------

def test_set_capacity_keeps_top_level_exclude_programs(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nexclude_programs: [p4]\n")
    svc.set_capacity({"cpu": 8})
    assert _read(tmp_path)["exclude_programs"] == ["p4"]


def test_set_capacity_refuses_reserved_access_names(tmp_path):
    svc = Service(tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        svc.set_capacity({"programs": 1})
    with pytest.raises(ValueError, match="reserved"):
        svc.set_capacity({"exclude_programs": 1})


# --- probe_host / confirm_host ---------------------------------------------

def test_probe_and_confirm_record_and_write_exclude_programs(tmp_path):
    from tests.host_probe_fakes import FakeRunner
    svc = Service(tmp_path)
    record = svc.probe_host(name="gpu1", ssh="gpu1", exclude_programs=["p4"], runner=FakeRunner())
    assert record["declared"]["exclude_programs"] == ["p4"]

    svc.confirm_host(name="gpu1", capacity={"cpu": 10})
    written = _read(tmp_path)
    assert written["hosts"]["gpu1"]["exclude_programs"] == ["p4"]


# --- HTTP routes -----------------------------------------------------------

def _client(tmp_path, svc=None):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app
    return TestClient(build_app(svc or Service(tmp_path)))


def test_route_set_host_programs(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    client = _client(tmp_path, svc)

    r = client.put("/api/hosts/a/programs", json={"programs": ["p1"], "exclude_programs": []})
    assert r.status_code == 200 and "cut_off" in r.json()
    assert _read(tmp_path)["hosts"]["a"]["programs"] == ["p1"]

    r = client.put("/api/hosts/a/programs", json={"programs": ["p1"], "exclude_programs": ["p4"]})
    assert r.status_code == 422

    r = client.put("/api/hosts/nope/programs", json={"programs": [], "exclude_programs": []})
    assert r.status_code == 404


def test_route_set_program_hosts(tmp_path):
    text = "cpu: 4\nhosts:\n  a:\n    ssh: a\n    programs: [p4]\n    capacity: {cpu: 8}\n"
    svc = _svc_with_program(tmp_path, text, "p4")
    client = _client(tmp_path, svc)

    r = client.put("/api/programs/p4/hosts", json={"hosts": ["local", "a"]})
    assert r.status_code == 200 and "cut_off" in r.json()

    r = client.put("/api/programs/p4/hosts", json={"hosts": []})
    assert r.status_code == 422

    r = client.put("/api/programs/nope-program/hosts", json={"hosts": ["local"]})
    assert r.status_code == 404
