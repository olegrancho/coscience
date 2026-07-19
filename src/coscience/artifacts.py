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


def revert(substrate, program_id: str, aid: str, vid: str) -> None:
    """Set `current` to an existing version. Pointer move only — no version is
    cut and nothing is deleted; a later edit branches from `vid`."""
    art = substrate.load_artifact(program_id, aid)
    if vid not in {v.id for v in art.versions}:
        raise ValueError(f"no version {vid!r} in artifact {aid!r}")
    art.current = vid
    substrate.save_artifact(art)


def children(art: Artifact, vid: str) -> list[str]:
    return [v.id for v in art.versions if v.parent == vid]


def is_leaf(art: Artifact, vid: str) -> bool:
    return not children(art, vid)


def archive_version(substrate, program_id: str, aid: str, vid: str,
                    archived: bool = True) -> None:
    art = substrate.load_artifact(program_id, aid)
    for v in art.versions:
        if v.id == vid:
            v.archived = archived
    substrate.save_artifact(art)


def archive_subtree(substrate, program_id: str, aid: str, vid: str,
                    archived: bool = True) -> None:
    art = substrate.load_artifact(program_id, aid)
    kids: dict[str, list[str]] = {}
    for v in art.versions:
        kids.setdefault(v.parent, []).append(v.id)
    seen: set[str] = set()
    stack = [vid]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(kids.get(cur, []))
    for v in art.versions:
        if v.id in seen:
            v.archived = archived
    substrate.save_artifact(art)


def archive_artifact(substrate, program_id: str, aid: str,
                     archived: bool = True) -> None:
    art = substrate.load_artifact(program_id, aid)
    art.archived = archived
    substrate.save_artifact(art)


@contextmanager
def _lock_guard(substrate):
    """Repo-level exclusive flock so multi-artifact acquire/release is atomic across the
    dispatcher and HTTP processes. Like pm_agent's per-program lock but blocking (LOCK_EX) rather than non-blocking — callers here wait for the guard rather than reporting 'busy'."""
    lockdir = substrate.repo_root / ".coscience"
    lockdir.mkdir(parents=True, exist_ok=True)
    f = open(lockdir / "artifacts.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def acquire_lock(substrate, program_id: str, aids: list[str], holder_kind: str,
                 holder_id: str, now: float) -> bool:
    with _lock_guard(substrate):
        arts = [substrate.load_artifact(program_id, aid) for aid in aids]
        for art in arts:
            if art.lock and art.lock.get("holder_id") != holder_id:
                return False                       # any busy -> acquire none
        for art in arts:
            if art.lock.get("holder_id") == holder_id:
                continue                           # already held -> no-op (keep work/ + age)
            art.lock = {"holder_kind": holder_kind, "holder_id": holder_id,
                        "acquired_at": now, "last_activity": now}
            substrate.save_artifact(art)
            seed_work(substrate, program_id, art.id)
        return True


def release_lock(substrate, program_id: str, aids: list[str], now: float,
                 created_by: str) -> list[str | None]:
    out: list[str | None] = []
    with _lock_guard(substrate):
        for aid in aids:
            art = substrate.load_artifact(program_id, aid)
            if not art.lock:
                out.append(None)
                continue
            vid = cut_version(substrate, program_id, aid, created_by, now)
            work = substrate.artifact_dir(program_id, aid) / "work"
            if work.is_dir():
                shutil.rmtree(work)
            art = substrate.load_artifact(program_id, aid)   # reload (cut_version saved)
            art.lock = {}
            substrate.save_artifact(art)
            out.append(vid)
    return out


def bump_activity(substrate, program_id: str, aid: str, now: float) -> None:
    with _lock_guard(substrate):
        art = substrate.load_artifact(program_id, aid)
        if art.lock:
            art.lock["last_activity"] = now
            substrate.save_artifact(art)


def reap_stale_chat_locks(substrate, program_id: str, now: float,
                          timeout: float = 1800.0, holder_alive=None) -> list[str]:
    released: list[str] = []
    for art in substrate.iter_artifacts(program_id, include_archived=True):
        lock = art.lock
        if not lock or lock.get("holder_kind") != "chat":
            continue
        idle = now - float(lock.get("last_activity", lock.get("acquired_at", now)))
        dead = holder_alive is not None and not holder_alive(lock.get("holder_id", ""))
        if idle >= timeout or dead:
            release_lock(substrate, program_id, [art.id], now,
                         created_by=lock.get("holder_id", ""))
            released.append(art.id)
    return released
