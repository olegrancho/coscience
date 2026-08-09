from coscience.frontmatter_io import parse
from coscience.models import Program
from coscience.substrate import Substrate


def test_max_proposed_defaults_to_unset(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x"))
    assert sub.load_program("p1").max_proposed == 0


def test_max_proposed_round_trips(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x", max_proposed=7))
    assert sub.load_program("p1").max_proposed == 7


def test_unset_max_proposed_writes_no_frontmatter_key(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x"))
    fm, _ = parse((sub.program_dir("p1") / "program.md").read_text())
    assert "max_proposed" not in fm
