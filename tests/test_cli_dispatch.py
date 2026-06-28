import pytest
from tests.conftest import FakeAgent

from coscience import cli
from coscience.cli import dispatch_once, main
from coscience.models import Sprint, SprintStatus
from coscience.substrate import Substrate


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch):
    # never launch a real claude in the dispatch/worker CLI paths
    monkeypatch.setattr(cli, "ClaudeAgent", FakeAgent)


def _seed(repo, sid, req=None):
    Substrate(repo).save_sprint(Sprint(
        id=sid, status=SprintStatus.APPROVED, goals="g",
        plan=["do the work"], resources_required=req or {}))


def _write_pool(repo, yaml_text):
    d = repo / ".coscience"
    d.mkdir(parents=True, exist_ok=True)
    (d / "resources.yaml").write_text(yaml_text)


def test_dispatch_once_returns_report(tmp_path):
    _write_pool(tmp_path, "resources:\n  gpu: 1\n")
    _seed(tmp_path, "sp1", req={"gpu": 1.0})
    report = dispatch_once(tmp_path)
    assert report.granted == 1


def test_main_dispatch_loop_completes_sprints(tmp_path):
    _write_pool(tmp_path, "resources:\n  gpu: 1\n")
    _seed(tmp_path, "a", req={"gpu": 1.0})
    _seed(tmp_path, "b", req={"gpu": 1.0})
    code = main(["dispatch", "--repo", str(tmp_path),
                 "--loop", "--interval", "0", "--max-beats", "12"])
    assert code == 0
    assert Substrate(tmp_path).load_sprint("a").status == SprintStatus.DONE
    assert Substrate(tmp_path).load_sprint("b").status == SprintStatus.DONE


def test_worker_subcommand_still_works(tmp_path):
    _seed(tmp_path, "sp1")
    code = main(["worker", "--repo", str(tmp_path), "--once"])
    assert code == 0
