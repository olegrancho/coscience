"""O5: a probed server is recorded, and a human confirms it into the pool."""
import json

import pytest
import yaml

from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service
from tests.host_probe_fakes import FakeRunner


def _write(tmp_path, text):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def test_a_probe_is_recorded_with_its_declaration(tmp_path):
    svc = Service(tmp_path)
    record = svc.probe_host(name="gpu1", ssh="gpu1", programs=["p2"], owner="ops",
                            runner=FakeRunner())
    assert record["ok"] is True
    assert record["declared"] == {"ssh": "gpu1", "run_root": "~/coscience-runs", "shared": False,
                                  "programs": ["p2"], "owner": "ops", "notes": ""}
    assert record["proposal"]["capacity"] == {"cpu": 12.0, "memory_gb": 55.0}
    stored = json.loads((tmp_path / ".coscience" / "host-probes" / "gpu1.json").read_text())
    assert stored["name"] == "gpu1"
    assert [r["name"] for r in svc.list_host_probes()] == ["gpu1"]


@pytest.mark.parametrize("name", ["", "local", "a b", "../x"])
def test_a_probe_refuses_a_bad_name(tmp_path, name):
    with pytest.raises(ValueError, match="name"):
        Service(tmp_path).probe_host(name=name, ssh="gpu1", runner=FakeRunner())


def test_a_probe_refuses_a_bad_ssh_target(tmp_path):
    with pytest.raises(ValueError, match="ssh target"):
        Service(tmp_path).probe_host(name="gpu1", ssh="-oProxyCommand=x", runner=FakeRunner())


def test_confirming_writes_the_host_and_keeps_the_rest_of_the_file(tmp_path):
    _write(tmp_path, "cpu: 4\nworkers: 2\n")
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1", programs=["p2"], runner=FakeRunner())

    status = svc.confirm_host(name="gpu1", capacity={"cpu": 10, "memory_gb": 50})

    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written["cpu"] == 4 and written["workers"] == 2
    assert written["hosts"]["gpu1"] == {
        "ssh": "gpu1", "run_root": "~/coscience-runs", "programs": ["p2"],
        "capacity": {"cpu": 10.0, "memory_gb": 50.0},
        "gpus": [{"model": "Example GPU 11GB", "vram_gb": 10.8}]}
    assert [h["name"] for h in status["hosts"]] == ["local", "gpu1"]
    assert status["host_errors"] == []
    assert next(h for h in status["hosts"] if h["name"] == "gpu1")["placeable"] is False


def test_confirming_needs_a_probe(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).confirm_host(name="gpu1", capacity={"cpu": 1})


def test_confirming_a_failed_probe_is_refused(tmp_path):
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1",
                   runner=FakeRunner({"probe": (255, "", "Host key verification failed.")}))
    with pytest.raises(ValueError, match="probe"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1})


def test_confirming_refuses_an_entry_the_pool_would_reject(tmp_path):
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner())
    with pytest.raises(ValueError, match="platform-wide"):
        svc.confirm_host(name="gpu1", capacity={"workers": 1})


def test_a_host_entry_carries_its_owner_notes_and_sharing():
    host = ResourcePool.from_dict({"cpu": 1, "hosts": {"gpu1": {
        "ssh": "gpu1", "shared": True, "owner": "ops", "notes": "nights only"}}}).host("gpu1")
    assert (host.shared, host.owner, host.notes) == (True, "ops", "nights only")


def test_probing_refuses_an_unsafe_run_root_before_writing_anything(tmp_path):
    with pytest.raises(ValueError, match="run root"):
        Service(tmp_path).probe_host(name="gpu1", ssh="gpu1", run_root="~/runs;rm -rf /",
                                     runner=FakeRunner())
    assert not (tmp_path / ".coscience" / "host-probes" / "gpu1.json").exists()


def test_confirming_a_server_whose_checks_failed_is_refused(tmp_path):
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner({"alive": (1, "", "")}))
    with pytest.raises(ValueError, match="checks failed on gpu1: detached job survives"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1})


def test_confirming_a_different_probe_than_the_one_reviewed_is_refused(tmp_path):
    svc = Service(tmp_path)
    record = svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner())
    with pytest.raises(ValueError, match="changed since it was reviewed"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1}, probed_at=record["probed_at"] - 5)


def test_a_whole_card_proposal_is_written_as_a_gpu_count(tmp_path):
    from tests.host_probe_fakes import SAMPLE_OUTPUT
    output = SAMPLE_OUTPUT.replace("gpu=0|Example GPU 11GB|11019|460.39", "gpu=0|Example GPU|[N/A]|470.00")
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner({"probe": (0, output, "")}))
    svc.confirm_host(name="gpu1", capacity={"cpu": 8})
    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written["hosts"]["gpu1"]["capacity"] == {"cpu": 8.0, "gpu": 1.0}
    assert "gpus" not in written["hosts"]["gpu1"]


def test_confirming_refuses_a_hosts_section_that_is_not_a_mapping(tmp_path):
    _write(tmp_path, "cpu: 4\nhosts:\n  - gpu0\n")
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner())
    with pytest.raises(ValueError, match="not a mapping"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1})
    assert "gpu0" in (tmp_path / ".coscience" / "resources.yaml").read_text()
