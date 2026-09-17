"""Taking servers out of the pool. A human marks a server (`remove: true`); the
dispatcher deletes it at the start of a cycle, before it grants, once nothing is on
it. The dispatcher is the only process that grants, so deleting there cannot race a
grant."""
from __future__ import annotations

from coscience.models import SprintStatus
from coscience.resources import pool_file_hosts, pool_file_lock, write_pool_file

_FINISHED = (SprintStatus.DONE, SprintStatus.CANCELED, SprintStatus.FAILED)


def blockers_by_host(substrate, ledger, names) -> dict[str, list[dict]]:
    """`blockers()` for every server in `names`, in one pass over every sprint and
    progress file (fix round 1, M7) — a caller with more than one marked or drained
    server (a dispatcher cycle with several, or a Compute page listing all of them)
    costs O(sprints), not O(sprints * servers)."""
    names = set(names)
    out: dict[str, list[dict]] = {name: [] for name in names}
    seen: dict[str, set[str]] = {name: set() for name in names}
    statuses = {s.id: s for s in substrate.iter_sprints()}
    for lease in ledger.all_leases():
        if lease.host in names and lease.sprint_id not in seen[lease.host]:
            s = statuses.get(lease.sprint_id)
            out[lease.host].append({"sprint_id": lease.sprint_id,
                                    "status": s.status.value if s else "unknown",
                                    "reason": "holds a lease here"})
            seen[lease.host].add(lease.sprint_id)
    for sid, s in statuses.items():
        if s.status in _FINISHED:
            continue
        progress = None  # loaded at most once per sprint, only if some server needs it
        for name in names:
            if sid in seen[name]:
                continue
            if progress is None:
                progress = substrate.load_progress(sid)
            if progress.host == name or progress.job_host == name:
                out[name].append({"sprint_id": sid, "status": s.status.value, "reason": "its work is here"})
                seen[name].add(sid)
            elif progress.reallocate_to == name:
                # A pending `reallocate` answer pins the sprint's next grant here
                # (dispatcher.py pins on `reallocate_to or host`) even though nothing
                # has landed yet — deleting the target out from under it would leave
                # it pinned to a server that no longer exists (fix round 1, I1).
                out[name].append({"sprint_id": sid, "status": s.status.value, "reason": "moving here"})
                seen[name].add(sid)
    return out


def blockers(substrate, ledger, name: str) -> list[dict]:
    """What a marked (or drained) server `name` is still waiting on before it can
    go: a lease holder first, then any unfinished sprint whose work is physically
    there or is about to move there — each sprint listed at most once, lease reason
    preferred. Thin wrapper over `blockers_by_host` for a single server."""
    return blockers_by_host(substrate, ledger, (name,))[name]


def remove_marked(substrate, ledger) -> list[str]:
    """Delete every `remove: true` server with no blockers. Under the pool file
    lock, so this cannot race a human's Remove/Keep or a capacity/host edit."""
    repo_root = substrate.repo_root
    removed = []
    # The blocker scan reads every sprint and progress file; computing it while
    # holding the lock would block every other writer of resources.yaml (a human's
    # Remove/Keep, a capacity or host edit) for that long (fix round 1, M7). Read
    # the currently-marked names first, outside the lock.
    _loaded, hosts_before_lock = pool_file_hosts(repo_root)
    marked_before_lock = {name for name, entry in hosts_before_lock.items()
                          if isinstance(entry, dict) and entry.get("remove") is True}
    blocked = blockers_by_host(substrate, ledger, marked_before_lock)
    with pool_file_lock(repo_root):
        loaded, hosts = pool_file_hosts(repo_root)
        for name, entry in list(hosts.items()):
            if not (isinstance(entry, dict) and entry.get("remove") is True):
                continue
            # A server marked after the pre-lock read above has no entry in
            # `blocked` — never treat that as "no blockers"; it waits for the next
            # cycle, which will scan it.
            if name not in blocked or blocked[name]:
                continue
            del hosts[name]
            removed.append(name)
        if removed:
            write_pool_file(repo_root, loaded)
    return removed
