"""Listing directories: confinement, filtering, and the root level."""
from pathlib import Path

import pytest

from coscience import fs_browse


@pytest.fixture
def root(monkeypatch, tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(r))
    return r


def test_lists_only_directories_sorted(root):
    (root / "beta").mkdir()
    (root / "alpha").mkdir()
    (root / "a-file.txt").write_text("x")
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["alpha", "beta"]
    assert out["entries"][0]["path"] == str(root / "alpha")


def test_hidden_directories_are_skipped(root):
    (root / ".git").mkdir()
    (root / "visible").mkdir()
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["visible"]


def test_parent_is_null_at_a_root(root):
    assert fs_browse.list_dirs(str(root))["parent"] is None


def test_parent_is_set_below_a_root(root):
    (root / "child").mkdir()
    out = fs_browse.list_dirs(str(root / "child"))
    assert out["parent"] == str(root)


def test_dotdot_escape_is_rejected(root):
    with pytest.raises(fs_browse.OutsideRoots):
        fs_browse.list_dirs(str(root / ".." / ".."))


def test_symlink_pointing_outside_a_root_is_rejected(root, tmp_path):
    """resolve() follows the link BEFORE the confinement check."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    with pytest.raises(fs_browse.OutsideRoots):
        fs_browse.list_dirs(str(root / "escape"))


def test_symlink_pointing_outside_a_root_is_not_listed(root, tmp_path):
    """An entry you are forbidden to open must not be offered."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    (root / "ok").mkdir()
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["ok"]


def test_null_byte_in_path_raises_not_found(root):
    """Path.resolve() raises a raw ValueError on a null byte; _checked() must
    convert that into a BrowseError so it doesn't escape as a 500 over HTTP."""
    with pytest.raises(fs_browse.NotFound):
        fs_browse.list_dirs(str(root) + "\0bad")


def test_missing_path_raises_not_found(root):
    with pytest.raises(fs_browse.NotFound):
        fs_browse.list_dirs(str(root / "nope"))


def test_file_path_raises_not_found(root):
    f = root / "f.txt"
    f.write_text("x")
    with pytest.raises(fs_browse.NotFound):
        fs_browse.list_dirs(str(f))


def test_broken_entry_is_skipped_not_fatal(root):
    """A dangling symlink must not break the whole listing. (This exercises
    Path.is_dir() returning False for a dangling link, not the `except
    OSError` around resolve() below — see
    test_entry_raising_on_resolve_is_skipped_not_fatal for that path.)"""
    (root / "dangling").symlink_to(root / "does-not-exist")
    (root / "ok").mkdir()
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["ok"]


def test_entry_raising_on_resolve_is_skipped_not_fatal(root, monkeypatch):
    """A real directory that raises when resolve() is called on it (a TOCTOU
    race, or a permission change mid-listing) must not fail the whole
    listing either. is_dir() alone can't exercise this: it only returns
    False, it never raises, so the `except OSError` around resolve() needs
    its own case to be genuinely reachable."""
    (root / "flaky").mkdir()
    (root / "ok").mkdir()
    real_resolve = Path.resolve

    def flaky_resolve(self, *args, **kwargs):
        if self.name == "flaky":
            raise OSError("gone mid-resolve")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", flaky_resolve)
    out = fs_browse.list_dirs(str(root))
    assert [e["name"] for e in out["entries"]] == ["ok"]


def test_iterdir_permission_error_propagates(root, monkeypatch):
    """PermissionError from iterdir() is not caught here: the HTTP layer maps
    a bare PermissionError to 403, and that mapping needs an unwrapped
    exception to trigger on."""
    def raise_permission_denied(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", raise_permission_denied)
    with pytest.raises(PermissionError):
        fs_browse.list_dirs(str(root))


def test_symlink_loop_raises_not_found(root):
    """Path.resolve() raises RuntimeError on a symlink loop; _checked() must
    convert that into a BrowseError so it doesn't escape as a 500 over HTTP."""
    (root / "a").symlink_to(root / "b")
    (root / "b").symlink_to(root / "a")
    with pytest.raises(fs_browse.NotFound):
        fs_browse.list_dirs(str(root / "a"))


def test_outside_roots_error_carries_the_requested_path_not_the_resolved_one(root, tmp_path):
    """OutsideRoots must not disclose a resolved path (e.g. a symlink's real
    target, or the server's CWD after `~unknownuser` fails to expand) that
    the caller didn't already know."""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside)
    with pytest.raises(fs_browse.OutsideRoots) as exc_info:
        fs_browse.list_dirs(str(link))
    assert str(exc_info.value) == str(link)
    assert str(outside) not in str(exc_info.value)


def test_none_with_one_root_lists_that_root(root):
    (root / "child").mkdir()
    out = fs_browse.list_dirs(None)
    assert out["path"] == str(root)
    assert [e["name"] for e in out["entries"]] == ["child"]


def test_none_with_two_roots_lists_the_roots(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", f"{a}:{b}")
    out = fs_browse.list_dirs(None)
    assert out["path"] is None
    assert out["parent"] is None
    assert [e["path"] for e in out["entries"]] == [str(a), str(b)]


def test_home_root_is_labelled_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(tmp_path))
    assert fs_browse.list_dirs(None)["roots"] == [{"label": "~", "path": str(tmp_path)}]


def test_non_home_root_is_labelled_with_its_path(root):
    assert fs_browse.list_dirs(None)["roots"] == [{"label": str(root), "path": str(root)}]
