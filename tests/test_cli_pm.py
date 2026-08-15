import coscience.cli as cli
from coscience.models import Program
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint
from coscience.substrate import Substrate


def _seed_program(tmp_path):
    Substrate(tmp_path).save_program(Program(id="p1", title="C", goals="cure"))


def _fake_reasoner_factory(outputs):
    return lambda *a: FakeReasoner(list(outputs))


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


def test_pm_loop_reasoner_writes_transcripts_into_the_substrate(tmp_path):
    """The loop's reasoner keeps each program's event feed beside its pm lock — without
    a transcript dir the PM's turns (what drives its cost) leave no trace at all."""
    _seed_program(tmp_path)
    reasoner = cli._make_pm_reasoner(Substrate(tmp_path))
    assert reasoner.transcript_dir == tmp_path / ".coscience"


def test_the_pm_loop_says_when_a_human_paused_it(tmp_path, monkeypatch, capsys):
    """'paused — Claude usage exhausted' and a human pause are different situations;
    the log has to tell them apart or a deliberate pause reads like an exhausted
    window that will fix itself after the reset."""
    from coscience import pause
    _seed_program(tmp_path)
    pause.set_paused(tmp_path, True)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)   # no real sleeping

    def _boom(*a):
        raise AssertionError("the reasoner must not be built while paused")

    monkeypatch.setattr(cli, "_make_pm_reasoner", _boom)

    rc = cli.main(["pm", "--repo", str(tmp_path), "--loop", "--max-rounds", "1"])

    assert rc == 0
    assert "paused by human" in capsys.readouterr().out
