"""Creating a folder from the picker: one segment, inside the roots, no clobber."""
import pytest

from coscience import fs_browse


@pytest.fixture
def root(monkeypatch, tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    monkeypatch.setenv("COSCIENCE_BROWSE_ROOTS", str(r))
    return r


def test_creates_the_directory(root):
    out = fs_browse.make_dir(str(root), "lf-work")
    assert out == {"path": str(root / "lf-work")}
    assert (root / "lf-work").is_dir()


def test_strips_surrounding_whitespace(root):
    out = fs_browse.make_dir(str(root), "  lf-work  ")
    assert out == {"path": str(root / "lf-work")}


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "a/b", "a\x00b"])
def test_rejects_invalid_names(root, name):
    with pytest.raises(fs_browse.InvalidName):
        fs_browse.make_dir(str(root), name)


def test_existing_folder_conflicts(root):
    (root / "taken").mkdir()
    with pytest.raises(fs_browse.AlreadyExists):
        fs_browse.make_dir(str(root), "taken")


def test_parent_outside_the_roots_is_rejected(root, tmp_path):
    with pytest.raises(fs_browse.OutsideRoots):
        fs_browse.make_dir(str(tmp_path), "sneaky")


def test_missing_parent_raises_not_found(root):
    with pytest.raises(fs_browse.NotFound):
        fs_browse.make_dir(str(root / "nope"), "child")


def test_dangling_symlink_conflicts(root):
    """A symlink pointing to an existing target should raise AlreadyExists
    when trying to create a directory with that name. Regression test: the old
    exists() check would return False for a dangling symlink, but mkdir() would
    still fail with FileExistsError when the symlink itself exists."""
    # Create a symlink pointing to an existing directory in the root
    (root / "target-dir").mkdir()
    symlink = root / "link-to-target"
    symlink.symlink_to(root / "target-dir")
    # When we try to create a directory with the symlink's name,
    # _checked() resolves it to the target, mkdir() tries to create the target,
    # and fails because the target exists. This should be caught as AlreadyExists.
    with pytest.raises(fs_browse.AlreadyExists):
        fs_browse.make_dir(str(root), "link-to-target")
