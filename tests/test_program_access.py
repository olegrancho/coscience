"""O14: one plain list per server of which programs it runs, editable from either
side, and `local` too."""
import subprocess

import pytest
import yaml

from coscience.models import Program, ProgramStatus, ProgressState
from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service


def pool(d):
    return ResourcePool.from_dict(d)


# --- Host.allows / ResourcePool parsing (Step 1) ---------------------------

def test_absent_key_admits_every_program():
    p = pool({"cpu": 4, "hosts": {"a": {"ssh": "a"}}})
    assert p.host("local").allows("p1") and p.host("a").allows("p1") and p.host("a").allows(None)
    assert p.host("local").programs is None and p.host("a").programs is None


def test_programs_list_admits_only_those_listed():
    h = pool({"hosts": {"a": {"ssh": "a", "programs": ["p1"]}}}).host("a")
    assert h.allows("p1") and not h.allows("p2") and not h.allows(None)


def test_empty_programs_list_admits_nothing():
    h = pool({"hosts": {"a": {"ssh": "a", "programs": []}}}).host("a")
    assert not h.allows("p1") and not h.allows(None)


def test_exclude_programs_on_a_server_is_no_longer_supported():
    p = pool({"hosts": {"a": {"ssh": "a", "exclude_programs": ["p4"]}}})
    assert p.host("a") is None
    assert any("exclude_programs" in e and "no longer supported" in e for e in p.host_errors)


def test_local_takes_top_level_programs():
    p = pool({"cpu": 4, "programs": ["p1"]})
    assert p.host("local").allows("p1") and not p.host("local").allows("p2")
    assert p.capacity["cpu"] == 4                     # access keys are not amounts


def test_local_top_level_empty_programs_gives_local_nothing():
    p = pool({"cpu": 4, "programs": []})
    assert not p.host("local").allows("p1") and not p.host("local").allows(None)


def test_local_access_inside_a_resources_wrapper():
    p = pool({"resources": {"cpu": 4, "programs": ["p1"]}})
    assert p.host("local").allows("p1") and not p.host("local").allows("p2")


def test_malformed_local_programs_is_reported_and_leaves_local_open():
    p = pool({"cpu": 4, "programs": "p1"})
    assert p.host("local").allows("p2") and p.host("local").programs is None
    assert any("programs" in e for e in p.host_errors)


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


# --- set_host_programs (server side) ----------------------------------------

def test_set_host_programs_writes_the_list_leaving_the_rest_alone(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)

    svc.set_host_programs("a", ["p1"])
    e = _read(tmp_path)["hosts"]["a"]
    assert e["programs"] == ["p1"]
    assert (e["drain"], e["drained_at"], e["capacity"], e["notes"]) == \
        (True, 5.0, {"cpu": 8}, "nights only")
    assert e["gpus"] == [{"model": "X", "vram_gb": 11.0}]


def test_set_host_programs_empty_list_is_written_and_the_key_stays(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    svc.set_host_programs("a", [])
    e = _read(tmp_path)["hosts"]["a"]
    assert e["programs"] == []
    assert (e["drain"], e["drained_at"], e["capacity"], e["notes"]) == \
        (True, 5.0, {"cpu": 8}, "nights only")


def test_set_host_programs_local_top_level_keeps_cpu_gpus_and_hosts(tmp_path):
    text = ("cpu: 4\ngpus:\n  - {model: X, vram_gb: 11}\n"
            "hosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n")
    svc = _svc_with_program(tmp_path, text)
    svc.set_host_programs("local", ["p1"])
    data = _read(tmp_path)
    assert data["programs"] == ["p1"]
    assert data["cpu"] == 4 and data["gpus"] == [{"model": "X", "vram_gb": 11.0}]
    assert "a" in data["hosts"]


def test_set_host_programs_local_inside_a_resources_wrapper(tmp_path):
    svc = _svc_with_program(tmp_path, "resources:\n  cpu: 4\n")
    svc.set_host_programs("local", ["p1"])
    data = _read(tmp_path)
    assert data["resources"]["programs"] == ["p1"]
    assert "programs" not in data


def test_set_host_programs_local_ignores_unrelated_pre_existing_host_errors(tmp_path):
    # `bad` is missing `ssh`, an unrelated host_errors entry that must not block
    # editing local's own access.
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  bad:\n    capacity: {cpu: 1}\n")
    svc.set_host_programs("local", ["p1"])
    assert _read(tmp_path)["programs"] == ["p1"]


def test_set_host_programs_local_ignores_another_servers_broken_programs(tmp_path):
    # The other server's error mentions `programs` too; it still must not block local.
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  b:\n    ssh: b\n    programs: bad\n")
    svc.set_host_programs("local", ["p1"])
    assert _read(tmp_path)["programs"] == ["p1"]


def test_set_host_programs_with_nothing_to_change_makes_no_commit(tmp_path):
    _git_repo(tmp_path)
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    svc.set_host_programs("a", ["p1"])
    head = lambda: subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout
    before = head()
    svc.set_host_programs("a", ["p1"])
    assert head() == before and before


def test_set_host_programs_unknown_server_raises(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    with pytest.raises(NotFoundError):
        svc.set_host_programs("nope", ["p1"])


# --- set_program_hosts (program side) ---------------------------------------

def test_set_program_hosts_reifies_absent_and_extends_an_explicit_list(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    programs: [p1]\n"
                            "    capacity: {cpu: 8}\n", "p1", "p4")
    result = svc.set_program_hosts("p4", ["a"])
    data = _read(tmp_path)
    assert data.get("programs") == ["p1"]          # local: every program except p4
    assert data["hosts"]["a"]["programs"] == ["p1", "p4"]
    assert result["cut_off"] == []


def test_set_program_hosts_empty_list_on_a_single_program_server_succeeds(tmp_path):
    text = "cpu: 4\nhosts:\n  a:\n    ssh: a\n    programs: [p4]\n    capacity: {cpu: 8}\n"
    svc = _svc_with_program(tmp_path, text, "p4")
    svc.set_program_hosts("p4", [])
    assert _read(tmp_path)["hosts"]["a"]["programs"] == []


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

    result = svc.set_host_programs("a", [])
    assert result["cut_off"] == [{"sprint_id": "s-running", "host": "a"}]


def test_update_host_reports_cut_off_pins(tmp_path):
    from tests.conftest import write_raw_sprint
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n", "p4")
    write_raw_sprint(tmp_path, "s-running", "executing", "g", ["x"], program="p4")
    svc.substrate.save_progress(ProgressState(sprint_id="s-running", host="a"))

    result = svc.update_host("a", programs=[])
    assert result["cut_off"] == [{"sprint_id": "s-running", "host": "a"}]


# --- update_host --------------------------------------------------------------

def test_update_host_writes_programs(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    svc.update_host("a", programs=["p4"])
    e = _read(tmp_path)["hosts"]["a"]
    assert e["programs"] == ["p4"]


# --- ledger_status ------------------------------------------------------------

def test_ledger_status_carries_programs_for_every_host(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    programs: [p4]\n"
                            "    capacity: {cpu: 8}\n  b:\n    ssh: b\n    capacity: {cpu: 8}\n")
    status = svc.ledger_status()
    by_name = {h["name"]: h for h in status["hosts"]}
    assert by_name["local"]["programs"] is None
    assert by_name["a"]["programs"] == ["p4"]
    assert by_name["b"]["programs"] is None


# --- set_capacity ---------------------------------------------------------------

def test_set_capacity_keeps_top_level_programs(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nprograms: [p4]\n")
    svc.set_capacity({"cpu": 8})
    assert _read(tmp_path)["programs"] == ["p4"]


def test_set_capacity_refuses_reserved_access_names(tmp_path):
    svc = Service(tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        svc.set_capacity({"programs": 1})


# --- probe_host / confirm_host -----------------------------------------------

def test_probe_and_confirm_record_and_write_programs(tmp_path):
    from tests.host_probe_fakes import FakeRunner
    svc = Service(tmp_path)
    record = svc.probe_host(name="gpu1", ssh="gpu1", programs=["p4"], runner=FakeRunner())
    assert record["declared"]["programs"] == ["p4"]
    assert "exclude_programs" not in record["declared"]

    svc.confirm_host(name="gpu1", capacity={"cpu": 10})
    written = _read(tmp_path)
    assert written["hosts"]["gpu1"]["programs"] == ["p4"]


def test_confirm_hosts_own_program_list_wins_over_the_declaration(tmp_path):
    # The Add form sends the list the human sees, which may differ from the probe's
    # declaration; an empty list means the server takes no work.
    from tests.host_probe_fakes import FakeRunner
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1", programs=["p4"], runner=FakeRunner())

    svc.confirm_host(name="gpu1", capacity={"cpu": 10}, programs=["p1", "p2"])
    assert _read(tmp_path)["hosts"]["gpu1"]["programs"] == ["p1", "p2"]

    svc.confirm_host(name="gpu1", capacity={"cpu": 10}, programs=[])
    assert _read(tmp_path)["hosts"]["gpu1"]["programs"] == []


# --- create_program with hosts -----------------------------------------------

def test_create_program_with_hosts_restricts_other_servers(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n")
    detail = svc.create_program("Title", "goals", hosts=["local"])
    data = _read(tmp_path)
    assert "programs" not in data                  # local keeps admitting everything
    assert data["hosts"]["a"]["programs"] == []     # reified from absent, new program excluded
    assert not ResourcePool.from_dict(data).host("a").allows(detail["id"])
    assert ResourcePool.from_dict(data).host("local").allows(detail["id"])


def test_create_program_with_hosts_none_writes_nothing(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n")
    svc.create_program("Title", "goals")
    data = _read(tmp_path)
    assert "programs" not in data


def test_create_program_without_hosts_holds_the_lock_across_the_program_write(tmp_path, monkeypatch):
    """Fix round 2, New Issue 1 (superseding the fix-round-1 test, New Issue 2):
    `create_program`'s program-file write must happen under `pool_file_lock` on
    *every* path, including the default `hosts=None` one — not only when `hosts`
    is given. `hosts=None` is used deliberately here: with no explicit `hosts`,
    `create_program` performs no access write of its own, so nothing can
    self-correct the outcome regardless of how the lock is scoped — only the
    *other* call's reification timing decides it, which is exactly the danger
    Finding 3 described.

    The hook below pauses *before* `save_program`'s real write (not after, unlike
    the superseded test — that ordering made the new program already exist on
    disk by the time the second thread could possibly run, so the assertions held
    regardless of locking and the test could never fail). Pausing first means a
    concurrent `set_program_hosts` call's `iter_programs()` snapshot genuinely
    races the write: whether it can complete before the new program exists is
    exactly what the widened lock is supposed to prevent.

    No `sleep` is used to coordinate the threads. `pool_file_lock` is a real
    `fcntl.flock`, and `Thread.join(timeout=...)` observes whether the second
    thread is still blocked on it: in the fixed code it provably cannot finish
    before the first thread's lock is released (an `fcntl.flock` acquisition
    either blocks or doesn't — there is no timing window to get unlucky in), and
    in the unfixed code its own work (a few in-memory computations and one file
    write, no I/O to speak of) completes so far inside the bound that there is no
    realistic scheduling scenario where it doesn't."""
    import threading

    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n", "p1")
    entered_write = threading.Event()
    release_write = threading.Event()
    real_save_program = svc.substrate.save_program

    def paused_save_program(program):
        entered_write.set()             # about to do the real write — not done it yet
        assert release_write.wait(timeout=5)
        real_save_program(program)

    monkeypatch.setattr(svc.substrate, "save_program", paused_save_program)

    created: dict = {}

    def do_create():
        created["detail"] = svc.create_program("New", "goals")     # hosts=None

    creator = threading.Thread(target=do_create)
    creator.start()
    assert entered_write.wait(timeout=5)    # creator is paused right before the write

    # A concurrent, unrelated edit that reifies the same absent-list server "a"
    # for the pre-existing p1. Started now, while the new program does not yet
    # exist on disk: on unfixed code this call's own (correctly-taken)
    # `pool_file_lock` succeeds immediately and it runs to completion on a stale
    # snapshot before the new program ever appears — permanently excluding it
    # from "a".
    excluder = threading.Thread(target=lambda: svc.set_program_hosts("p1", []))
    excluder.start()
    excluder.join(timeout=1)
    assert excluder.is_alive()              # true only when the fix correctly blocks it here

    release_write.set()
    creator.join(timeout=5)
    excluder.join(timeout=5)
    assert not creator.is_alive() and not excluder.is_alive()

    new_id = created["detail"]["id"]
    a_programs = _read(tmp_path)["hosts"]["a"]["programs"]
    assert new_id in a_programs         # would be silently dropped forever without the fix
    assert "p1" not in a_programs


# --- HTTP routes -----------------------------------------------------------

def _client(tmp_path, svc=None):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app
    return TestClient(build_app(svc or Service(tmp_path)))


def test_route_set_host_programs(tmp_path):
    svc = _svc_with_program(tmp_path, A_WITH_PROGRAMS)
    client = _client(tmp_path, svc)

    r = client.put("/api/hosts/a/programs", json={"programs": ["p1"]})
    assert r.status_code == 200 and "cut_off" in r.json()
    assert _read(tmp_path)["hosts"]["a"]["programs"] == ["p1"]

    # exclude_programs is no longer part of the body: a client still sending it is
    # simply ignored, not honored.
    r = client.put("/api/hosts/a/programs", json={"programs": ["p2"], "exclude_programs": ["p4"]})
    assert r.status_code == 200
    assert _read(tmp_path)["hosts"]["a"]["programs"] == ["p2"]

    r = client.put("/api/hosts/nope/programs", json={"programs": []})
    assert r.status_code == 404


def test_route_set_program_hosts(tmp_path):
    text = "cpu: 4\nhosts:\n  a:\n    ssh: a\n    programs: [p4]\n    capacity: {cpu: 8}\n"
    svc = _svc_with_program(tmp_path, text, "p4")
    client = _client(tmp_path, svc)

    r = client.put("/api/programs/p4/hosts", json={"hosts": ["local", "a"]})
    assert r.status_code == 200 and "cut_off" in r.json()
    assert _read(tmp_path)["hosts"]["a"]["programs"] == ["p4"]

    r = client.put("/api/programs/p4/hosts", json={"hosts": []})
    assert r.status_code == 200
    assert _read(tmp_path)["hosts"]["a"]["programs"] == []

    r = client.put("/api/programs/nope-program/hosts", json={"hosts": ["local"]})
    assert r.status_code == 404


def test_route_create_program_accepts_hosts(tmp_path):
    svc = _svc_with_program(tmp_path, "cpu: 4\nhosts:\n  a:\n    ssh: a\n    capacity: {cpu: 8}\n")
    client = _client(tmp_path, svc)
    r = client.post("/api/programs", json={"title": "T", "goals": "g", "hosts": ["local"]})
    assert r.status_code == 201
    assert _read(tmp_path)["hosts"]["a"]["programs"] == []
