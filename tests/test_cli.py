import pytest
from tests.conftest import FakeAgent

from coscience import cli
from coscience.cli import main, run_once
from coscience.models import BeatOutcome, Sprint, SprintStatus
from coscience.substrate import Substrate


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch):
    # never launch a real claude in the worker CLI paths
    monkeypatch.setattr(cli, "ClaudeAgent", FakeAgent)


def _approved(repo, sid, plan=("do the work",)):
    Substrate(repo).save_sprint(Sprint(
        id=sid, status=SprintStatus.QUEUED, goals="g", plan=list(plan)))


def test_run_once_idle(tmp_path):
    assert run_once(tmp_path) == BeatOutcome.IDLE


def test_main_once_progresses_and_returns_zero(tmp_path, capsys):
    _approved(tmp_path, "sp1")
    code = main(["worker", "--repo", str(tmp_path), "--once"])
    assert code == 0
    assert "progressed" in capsys.readouterr().out.lower()


def test_main_loop_runs_sprint_to_done(tmp_path):
    _approved(tmp_path, "sp1")
    code = main(["worker", "--repo", str(tmp_path),
                 "--loop", "--interval", "0", "--max-beats", "5"])
    assert code == 0
    assert Substrate(tmp_path).load_sprint("sp1").status == SprintStatus.DONE
