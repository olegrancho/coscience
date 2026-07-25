"""Browse the server's filesystem so the dashboard can pick a program's project
folder (`Program.workdir`).

Standalone by design: no Service/Substrate dependency, and not scoped to a
program — the create-program flow picks a folder before a program exists.

Browsing is confined to configured roots because the API is reachable on the LAN
and auth is inactive until `.coscience/users.yaml` exists.
"""
from __future__ import annotations

import os
from pathlib import Path


class BrowseError(Exception):
    """Base for browse failures the HTTP layer maps to status codes."""


class OutsideRoots(BrowseError):
    """Path resolved outside every configured root."""


class NotFound(BrowseError):
    """Path is missing, or exists but is not a directory."""


class InvalidName(BrowseError):
    """Proposed folder name is not a single usable path segment."""


class AlreadyExists(BrowseError):
    """A folder of that name is already there."""


def roots() -> list[Path]:
    """The directories browsing is confined to, from COSCIENCE_BROWSE_ROOTS
    (colon-separated, `~` expanded); defaults to the server user's home.

    Read at call time, never captured at import: tests monkeypatch the env var
    against a tmp_path. Entries that don't exist are dropped, and an empty
    result falls back to home so a typo can't leave the picker dead.
    """
    raw = os.environ.get("COSCIENCE_BROWSE_ROOTS", "")
    candidates = [Path(os.path.expanduser(p.strip())).resolve()
                  for p in raw.split(":") if p.strip()]
    if not candidates:
        candidates = [Path.home().resolve()]
    found = [p for p in candidates if p.is_dir()]
    return found or [Path.home().resolve()]


def _label(p: Path) -> str:
    return "~" if p == Path.home().resolve() else str(p)


def _inside(p: Path, rs: list[Path]) -> bool:
    return any(p.is_relative_to(r) for r in rs)


def _checked(path: str) -> Path:
    """Resolve `path` and confine it to the configured roots.

    `resolve()` follows symlinks BEFORE the check, so a link pointing outside
    the roots is rejected rather than followed.
    """
    p = Path(os.path.expanduser(str(path))).resolve()
    if not _inside(p, roots()):
        raise OutsideRoots(str(p))
    return p


def list_dirs(path: str | None = None) -> dict:
    """Subdirectories of `path`.

    With `path` None: the single configured root's contents, or — when several
    roots are configured — a virtual root level whose entries are the roots.
    """
    rs = roots()
    root_rows = [{"label": _label(r), "path": str(r)} for r in rs]
    if path is None or not str(path).strip():
        if len(rs) > 1:
            return {"path": None, "parent": None, "roots": root_rows,
                    "entries": [{"name": _label(r), "path": str(r)} for r in rs]}
        path = str(rs[0])

    p = _checked(path)
    if not p.is_dir():
        raise NotFound(str(p))

    entries = []
    for child in sorted(p.iterdir(), key=lambda c: c.name):
        if child.name.startswith("."):
            continue
        try:
            if child.is_dir() and _inside(child.resolve(), rs):
                entries.append({"name": child.name, "path": str(child)})
        except OSError:
            continue                     # unreadable/dangling: skip, don't fail the listing

    parent = None if any(p == r for r in rs) else str(p.parent)
    return {"path": str(p), "parent": parent, "roots": root_rows, "entries": entries}


def make_dir(parent: str, name: str) -> dict:
    """Create one directory under `parent`.

    `name` must be a single path segment, and the result must still fall inside
    the configured roots.
    """
    clean = str(name or "").strip()
    if clean in ("", ".", "..") or "/" in clean or "\0" in clean:
        raise InvalidName(str(name))

    base = _checked(parent)
    if not base.is_dir():
        raise NotFound(str(base))

    target = _checked(str(base / clean))
    try:
        target.mkdir()
    except FileExistsError:
        raise AlreadyExists(str(target))
    return {"path": str(target)}
