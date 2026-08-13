"""Global pause: one marker file the whole platform reads.

Three processes need to agree on whether the platform is paused — coscience-http, the
PM loop and the dispatch loop — so the flag lives in the substrate rather than in any
one process's memory, and survives a restart. A bare marker, because "paused" is one
bit: there is no schema to get wrong or migrate."""
from __future__ import annotations

from pathlib import Path


def _marker(repo_root) -> Path:
    return Path(repo_root) / ".coscience" / "paused"


def is_paused(repo_root) -> bool:
    """True when a human has paused the platform. No marker = running."""
    return _marker(repo_root).is_file()


def set_paused(repo_root, paused: bool) -> None:
    """Create or remove the marker. Idempotent in both directions, so a double-click
    on Pause and a Resume on an already-running platform are both no-ops."""
    path = _marker(repo_root)
    if paused:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    elif path.is_file():
        path.unlink()
