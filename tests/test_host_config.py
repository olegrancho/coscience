"""O10: update a server's configuration in place, and detect this machine's hardware."""
import pytest
import yaml

from coscience import host_probe
from coscience.service import NotFoundError, Service
from tests.host_probe_fakes import SAMPLE_OUTPUT, FakeRunner

POOL = ("cpu: 4\nworkers: 3\nhousekeepers: 2\nhosts:\n"
        "  big:\n    ssh: big\n    run_root: ~/runs\n    capacity: {cpu: 16, memory_gb: 64}\n"
        "    drain: true\n    drained_at: 5.0\n    owner: ops\n")

POOL_WITH_CARD = ("cpu: 4\nworkers: 3\nhousekeepers: 2\nhosts:\n"
                  "  big:\n    ssh: big\n    run_root: ~/runs\n"
                  "    capacity: {cpu: 8, gpu: 1}\n"
                  "    gpus:\n      - {model: A, vram_gb: 11}\n")


def _svc(tmp_path, text=POOL):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)
    return Service(tmp_path)


def _entry(tmp_path, name="big"):
    return yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"][name]


def test_values_update_in_place_and_drain_is_kept(tmp_path):
    svc = _svc(tmp_path)
    svc.update_host("big", capacity={"cpu": 8, "memory_gb": 32}, notes="nights only", shared=True,
                    programs=["p4"])
    e = _entry(tmp_path)
    assert e["capacity"] == {"cpu": 8.0, "memory_gb": 32.0}
    assert (e["notes"], e["shared"], e["programs"], e["owner"]) == ("nights only", True, ["p4"], "ops")
    assert (e["drain"], e["drained_at"], e["ssh"], e["run_root"]) == (True, 5.0, "big", "~/runs")


def test_a_new_ssh_target_needs_a_passing_probe_of_it(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="probe big with the new SSH target"):
        svc.update_host("big", ssh="big2")
    svc.probe_host(name="big", ssh="big2", run_root="~/runs", runner=FakeRunner({"alive": (1, "", "")}))
    with pytest.raises(ValueError, match="checks failed"):
        svc.update_host("big", ssh="big2")
    record = svc.probe_host(name="big", ssh="big2", run_root="~/runs", runner=FakeRunner())
    svc.update_host("big", ssh="big2", probed_at=record["probed_at"])
    assert _entry(tmp_path)["ssh"] == "big2"


def test_a_new_run_root_needs_a_probe_of_that_run_root(tmp_path):
    svc = _svc(tmp_path)
    svc.probe_host(name="big", ssh="big", run_root="~/runs", runner=FakeRunner())
    with pytest.raises(ValueError, match="probe big with the new run root"):
        svc.update_host("big", run_root="~/other")


def test_cards_are_replaced_or_removed(tmp_path):
    svc = _svc(tmp_path)
    svc.update_host("big", gpus=[{"model": "X", "vram_gb": 24}])
    assert _entry(tmp_path)["gpus"] == [{"model": "X", "vram_gb": 24.0}]
    svc.update_host("big", gpus=[])
    assert "gpus" not in _entry(tmp_path)


def test_editing_cards_on_a_host_that_already_has_cards_succeeds(tmp_path):
    """The dialog echoes host.capacity, which carries a derived gpu: <old count> —
    that stale count must not fight the new gpus list in _parse_host."""
    svc = _svc(tmp_path, POOL_WITH_CARD)
    svc.update_host("big", capacity={"cpu": 8, "gpu": 1},
                    gpus=[{"model": "A", "vram_gb": 11}, {"model": "B", "vram_gb": 24}])
    e = _entry(tmp_path)
    assert e["gpus"] == [{"model": "A", "vram_gb": 11.0}, {"model": "B", "vram_gb": 24.0}]
    assert e.get("capacity", {}).get("gpu") in (None, 2)


def test_removing_all_cards_leaves_no_phantom_gpu_count(tmp_path):
    svc = _svc(tmp_path, POOL_WITH_CARD)
    svc.update_host("big", capacity={"cpu": 8, "gpu": 1}, gpus=[])
    e = _entry(tmp_path)
    assert "gpus" not in e
    assert "gpu" not in e.get("capacity", {})


def test_unknown_local_and_invalid_updates_are_refused(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(NotFoundError):
        svc.update_host("nope", notes="x")
    with pytest.raises(NotFoundError):
        svc.update_host("local", notes="x")
    with pytest.raises(ValueError):
        svc.update_host("big", capacity={"cpu": -1})
    assert _entry(tmp_path)["capacity"] == {"cpu": 16, "memory_gb": 64}      # unchanged


def test_detect_local_runs_the_probe_script_without_ssh():
    runner = FakeRunner({"probe": (0, SAMPLE_OUTPUT, "")})
    result = host_probe.detect_local(runner=runner, now=1000.0)
    assert result["ok"] and result["facts"]["threads"] and "capacity" in result["proposal"]
    assert all("ssh" not in call[0] for call in runner.calls)
    assert runner.calls[0][:2] == ["bash", "-s"]


def test_this_machine_gets_cards_with_vram_and_keeps_platform_keys(tmp_path):
    svc = _svc(tmp_path, "cpu: 4\ngpu: 1\nworkers: 3\nhousekeepers: 2\n")
    svc.set_capacity({"cpu": 24, "workers": 3, "housekeepers": 2},
                     gpus=[{"model": "RTX", "vram_gb": 24}])
    data = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert data["gpus"] == [{"model": "RTX", "vram_gb": 24.0}] and "gpu" not in data
    assert (data["workers"], data["housekeepers"], data["cpu"]) == (3.0, 2.0, 24.0)
    svc.set_capacity({"cpu": 24, "workers": 3, "housekeepers": 2})            # gpus=None keeps the cards
    assert yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["gpus"]


def test_routes_update_and_detect(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app
    svc = _svc(tmp_path)
    monkeypatch.setattr(host_probe, "subprocess_runner", FakeRunner({"probe": (0, SAMPLE_OUTPUT, "")}))
    client = TestClient(build_app(svc))
    r = client.put("/api/hosts/big", json={"notes": "n"})
    assert r.status_code == 200 and _entry(tmp_path)["notes"] == "n"
    assert client.put("/api/hosts/nope", json={"notes": "n"}).status_code == 404
    assert client.put("/api/hosts/big", json={"capacity": {"cpu": -1}}).status_code == 422
    r = client.post("/api/hosts/local/detect")
    assert r.status_code == 200 and r.json()["ok"]
