from coscience.models import PMState, Program, ProgramStatus
from coscience.substrate import Substrate


def test_save_then_load_program(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="Cancer", goals="cure it"))
    loaded = sub.load_program("p1")
    assert loaded == Program(id="p1", title="Cancer", goals="cure it",
                             status=ProgramStatus.ACTIVE)


def test_iter_programs_empty_and_filtered(tmp_path):
    sub = Substrate(tmp_path)
    assert sub.iter_programs() == []
    sub.save_program(Program(id="a", title="A", goals="x"))
    sub.save_program(Program(id="b", title="B", goals="y", status=ProgramStatus.PAUSED))
    assert [p.id for p in sub.iter_programs()] == ["a", "b"]
    assert [p.id for p in sub.iter_programs(status=ProgramStatus.ACTIVE)] == ["a"]


def test_next_program_id_empty_repo(tmp_path):
    assert Substrate(tmp_path).next_program_id() == "p1"


def test_next_program_id_increments(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x"))
    assert sub.next_program_id() == "p2"


def test_next_program_id_ignores_non_pn_dirs(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x"))
    sub.save_program(Program(id="legacy", title="B", goals="y"))
    assert sub.next_program_id() == "p2"


def test_report_roundtrip_and_default(tmp_path):
    sub = Substrate(tmp_path)
    assert sub.load_report("p1") == ""
    sub.save_program(Program(id="p1", title="A", goals="x"))
    sub.save_report("p1", "# Status\nall good")
    assert "all good" in sub.load_report("p1")


def test_pm_state_default_and_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    assert sub.load_pm_state("p1") == PMState(program_id="p1")
    sub.save_program(Program(id="p1", title="A", goals="x"))
    sub.save_pm_state(PMState(program_id="p1", cycle=3, last_run=12.0,
                              proposed_ids=["p1-c0-a"], log=["cycle 0"]))
    assert sub.load_pm_state("p1") == PMState(program_id="p1", cycle=3, last_run=12.0,
                                              proposed_ids=["p1-c0-a"], log=["cycle 0"])
