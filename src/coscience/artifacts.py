"""Operations on the versioned artifact store: create, cut a version (with the
dedup rule), seed/snapshot the `work/` copy, revert, archive, and the exclusive
lock (acquire/release + stale reap). All filesystem + tree logic lives here so
substrate.py stays a thin persistence seam. Substrate holds meta.md I/O."""
from __future__ import annotations

import fcntl
import shutil
from contextlib import contextmanager
from pathlib import Path

from coscience.models import Artifact, ArtifactVersion


def create_artifact(substrate, program_id: str, aid: str, title: str,
                    kind: str) -> Artifact:
    if (substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
        raise ValueError(f"artifact {aid!r} already exists in {program_id!r}")
    art = Artifact(id=aid, program=program_id, title=title, kind=kind)
    substrate.save_artifact(art)
    return art


def seed_work(substrate, program_id: str, aid: str) -> Path:
    """(Re)create `work/` as a fresh copy of the current version (empty if the
    artifact has no versions yet). Any pre-existing `work/` is discarded first."""
    art = substrate.load_artifact(program_id, aid)
    d = substrate.artifact_dir(program_id, aid)
    work = d / "work"
    if work.exists():
        shutil.rmtree(work)
    if art.current:
        shutil.copytree(d / art.current, work)
    else:
        work.mkdir(parents=True, exist_ok=True)
    return work


def _next_vid(art: Artifact) -> str:
    n = 0
    for v in art.versions:
        try:
            n = max(n, int(v.id[1:]))
        except (ValueError, IndexError):
            continue
    return f"v{n + 1}"


def _dirs_identical(a: Path, b: Path) -> bool:
    if not (a.is_dir() and b.is_dir()):
        return False
    fa = {p.relative_to(a): p for p in a.rglob("*") if p.is_file()}
    fb = {p.relative_to(b): p for p in b.rglob("*") if p.is_file()}
    if set(fa) != set(fb):
        return False
    return all(fa[rel].read_bytes() == fb[rel].read_bytes() for rel in fa)


def cut_version(substrate, program_id: str, aid: str, created_by: str,
                now: float, note: str = "") -> str | None:
    """Snapshot `work/` into a new immutable version whose parent is `current`,
    then move `current` to it. Returns the new version id, or None when there is
    no `work/`, when it is byte-identical to `current` (the dedup rule), or when
    it is the first version but `work/` holds no files."""
    d = substrate.artifact_dir(program_id, aid)
    work = d / "work"
    if not work.is_dir():
        return None
    art = substrate.load_artifact(program_id, aid)
    parent = art.current
    if parent:
        if _dirs_identical(work, d / parent):
            return None
    elif not any(p.is_file() for p in work.rglob("*")):
        return None                                # empty first version -> nothing to cut
    vid = _next_vid(art)
    shutil.copytree(work, d / vid)
    art.versions.append(ArtifactVersion(
        id=vid, parent=parent, created_at=now, created_by=created_by, note=note))
    art.current = vid
    substrate.save_artifact(art)
    return vid
