"""Creating a folder from the picker: one segment, inside the roots, no clobber."""
from pathlib import Path

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


def test_concurrent_create_race(root, monkeypatch):
    """A FileExistsError from mkdir must surface as AlreadyExists because the HTTP layer maps that to 409 and has no mapping for a bare OSError."""
    def mock_mkdir(self, *args, **kwargs):
        raise FileExistsError("File exists")

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    with pytest.raises(fs_browse.AlreadyExists):
        fs_browse.make_dir(str(root), "concurrent-create")


def test_rejects_a_name_whose_utf8_encoding_exceeds_255_bytes(root):
    """A byte limit, not a character count: a real filesystem (ext4, xfs)
    rejects a NAME_MAX-exceeding component with ENAMETOOLONG, which must not
    reach mkdir() and escape as a raw OSError/500."""
    with pytest.raises(fs_browse.InvalidName):
        fs_browse.make_dir(str(root), "a" * 300)


def test_name_at_exactly_255_bytes_is_accepted(root):
    out = fs_browse.make_dir(str(root), "a" * 255)
    assert out == {"path": str(root / ("a" * 255))}


def test_mkdir_oserror_other_than_exists_becomes_create_failed(root, monkeypatch):
    """EROFS/ENOSPC/... must not escape as a raw OSError (-> 500); they become
    a BrowseError the HTTP layer can map to a 4xx."""
    def mock_mkdir(self, *args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    with pytest.raises(fs_browse.CreateFailed):
        fs_browse.make_dir(str(root), "no-room")


def test_mkdir_permission_error_is_not_swallowed_by_create_failed(root, monkeypatch):
    """PermissionError is an OSError subclass; the CreateFailed fallback must
    not catch it, so it stays a bare PermissionError and the HTTP layer's
    existing PermissionError -> 403 mapping still fires."""
    def mock_mkdir(self, *args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    with pytest.raises(PermissionError):
        fs_browse.make_dir(str(root), "no-access")
