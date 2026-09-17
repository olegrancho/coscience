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
        id=sid, status=SprintStatus.QUEUED, goals="g",
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


def test_dispatch_once_prints_beat_errors_when_any(tmp_path, monkeypatch, capsys):
    """R10(a): the --once summary line names how many sprints hit a beat error, so a
    failing beat is visible without digging into a sprint's error field."""
    from coscience.dispatcher import CycleReport, Dispatcher
    monkeypatch.setattr(Dispatcher, "run_one_cycle",
                        lambda self, now=None: CycleReport(granted=1, beat_errors=["sp1"]))
    code = main(["dispatch", "--repo", str(tmp_path), "--once"])
    assert code == 0
    out = capsys.readouterr().out
    assert "granted=1" in out and "beat errors: 1" in out


def test_dispatch_once_omits_beat_errors_when_none(tmp_path, monkeypatch, capsys):
    from coscience.dispatcher import CycleReport, Dispatcher
    monkeypatch.setattr(Dispatcher, "run_one_cycle", lambda self, now=None: CycleReport(granted=1))
    code = main(["dispatch", "--repo", str(tmp_path), "--once"])
    assert code == 0
    assert "beat errors" not in capsys.readouterr().out


def test_dispatch_once_prints_removed_hosts_when_any(tmp_path, monkeypatch, capsys):
    # M5 (fix round 1): a one-shot run that deletes a marked server must say so —
    # nothing else would surface it.
    from coscience.dispatcher import CycleReport, Dispatcher
    monkeypatch.setattr(Dispatcher, "run_one_cycle",
                        lambda self, now=None: CycleReport(granted=0, removed_hosts=["a", "b"]))
    code = main(["dispatch", "--repo", str(tmp_path), "--once"])
    assert code == 0
    assert "removed=a, b" in capsys.readouterr().out


def test_dispatch_once_omits_removed_hosts_when_none(tmp_path, monkeypatch, capsys):
    from coscience.dispatcher import CycleReport, Dispatcher
    monkeypatch.setattr(Dispatcher, "run_one_cycle", lambda self, now=None: CycleReport(granted=1))
    code = main(["dispatch", "--repo", str(tmp_path), "--once"])
    assert code == 0
    assert "removed=" not in capsys.readouterr().out


def test_dispatch_once_prints_a_removal_error(tmp_path, monkeypatch, capsys):
    # M2 (fix round 1): a pool-file error in the removal step used to be reduced to
    # a bare "beat errors: 1" count with the text nowhere in sight.
    from coscience.dispatcher import CycleReport, Dispatcher
    monkeypatch.setattr(Dispatcher, "run_one_cycle",
                        lambda self, now=None: CycleReport(granted=1, removal_error="hosts: is not a mapping"))
    code = main(["dispatch", "--repo", str(tmp_path), "--once"])
    assert code == 0
    assert "removal error: hosts: is not a mapping" in capsys.readouterr().out


def test_dispatch_loop_line_reports_removed_hosts_and_removal_errors(tmp_path, monkeypatch):
    from coscience.dispatcher import CycleReport, Dispatcher
    monkeypatch.setattr(Dispatcher, "run_one_cycle",
                        lambda self, now=None: CycleReport(
                            granted=0, removed_hosts=["a"], removal_error="hosts: is not a mapping"))
    lines = []
    monkeypatch.setattr(cli, "_status_loop",
                        lambda status, beat, interval, max_beats: lines.append(beat()))
    code = main(["dispatch", "--repo", str(tmp_path), "--loop"])
    assert code == 0
    line = lines[0][0]
    assert "removed a" in line and "removal error: hosts: is not a mapping" in line
