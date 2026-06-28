import coscience.cli as cli
from coscience.models import Program
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint
from coscience.substrate import Substrate


def _seed_program(tmp_path):
    Substrate(tmp_path).save_program(Program(id="p1", title="C", goals="cure"))


def _fake_reasoner_factory(outputs):
    return lambda: FakeReasoner(list(outputs))


def test_pm_once_proposes(tmp_path, monkeypatch, capsys):
    _seed_program(tmp_path)
    out = PMCycleOutput(proposals=[ProposedSprint(suffix="a", goals="do a",
                                                 plan=[{"id": "s", "run": "true"}])],
                        report="r")
    monkeypatch.setattr(cli, "_make_pm_reasoner", _fake_reasoner_factory([out]))

    rc = cli.main(["pm", "--repo", str(tmp_path), "--once"])
    assert rc == 0
    sprint = Substrate(tmp_path).load_sprint("p1-c0-a")
    assert sprint.goals == "do a"
    assert "p1" in capsys.readouterr().out          # printed a summary line


def test_pm_loop_runs_max_rounds(tmp_path, monkeypatch):
    _seed_program(tmp_path)
    outs = [PMCycleOutput(report="r1"), PMCycleOutput(report="r2")]
    monkeypatch.setattr(cli, "_make_pm_reasoner", _fake_reasoner_factory(outs))
    # avoid real sleeping between rounds
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    rc = cli.main(["pm", "--repo", str(tmp_path), "--loop", "--max-rounds", "2"])
    assert rc == 0
    # event-driven: round 1 reasons (cycle -> 1); round 2 sees no change and skips
    assert Substrate(tmp_path).load_pm_state("p1").cycle == 1
