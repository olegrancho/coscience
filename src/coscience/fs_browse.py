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
