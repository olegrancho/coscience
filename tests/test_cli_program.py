from coscience.cli import main
from coscience.substrate import Substrate


def test_program_create_writes_program(tmp_path, capsys):
    rc = main(["program", "create", "--repo", str(tmp_path),
               "--id", "p1", "--title", "Cancer", "--goals", "cure it"])
    assert rc == 0
    assert "p1" in capsys.readouterr().out
    p = Substrate(tmp_path).load_program("p1")
    assert p.title == "Cancer" and p.goals == "cure it"
