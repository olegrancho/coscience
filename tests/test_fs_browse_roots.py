"""COSCIENCE_BROWSE_ROOTS parsing: the confinement boundary for directory browsing."""
from pathlib import Path

from coscience import fs_browse


def test_roots_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("COSCIENCE_BROWSE_ROOTS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert fs_browse.roots() == [tmp_path.resolve()]


def test_roots_parses_colon_separated_paths(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", f"{a}:{b}")
    assert fs_browse.roots() == [a.resolve(), b.resolve()]


def test_roots_expands_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", "~")
    assert fs_browse.roots() == [tmp_path.resolve()]


def test_roots_drops_nonexistent_entries(monkeypatch, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", f"{a}:{tmp_path / 'nope'}")
    assert fs_browse.roots() == [a.resolve()]


def test_roots_falls_back_to_home_when_all_invalid(monkeypatch, tmp_path):
    """A typo in the env var must not leave the picker dead."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(tmp_path / "gone"))
    assert fs_browse.roots() == [tmp_path.resolve()]


def test_roots_is_read_at_call_time(monkeypatch, tmp_path):
    """Not captured at import: changing the env var changes the next call."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(a))
    assert fs_browse.roots() == [a.resolve()]
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(b))
    assert fs_browse.roots() == [b.resolve()]
