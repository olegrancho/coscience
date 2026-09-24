"""P13: scripts/bump-version raises the last number, once per deploy."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bump-version"


def _bump(path):
    return subprocess.run([sys.executable, str(SCRIPT), str(path)],
                          capture_output=True, text=True)


def test_it_raises_the_last_number_and_says_so(tmp_path):
    v = tmp_path / "VERSION"
    v.write_text("0.1.9\n")
    out = _bump(v)
    assert out.returncode == 0 and out.stdout.strip() == "0.1.10"
    assert v.read_text() == "0.1.10\n"


def test_it_refuses_a_file_that_is_not_a_version(tmp_path):
    v = tmp_path / "VERSION"
    v.write_text("banana\n")
    out = _bump(v)
    assert out.returncode != 0 and "expected a version like 0.1.1" in out.stderr
    assert v.read_text() == "banana\n"                      # left alone


def test_the_repo_starts_at_a_real_version():
    text = (SCRIPT.parents[1] / "VERSION").read_text().strip()
    assert [p.isdigit() for p in text.split(".")] == [True, True, True]
