"""Is each remote host answering? (O7)

The dispatch loop asks every placeable remote host `bash -c true` over key-only SSH at
most once a minute and records the answer in `.coscience/host-health.json`, which the
HTTP server reads too. A host failing for QUIET_AFTER is quiet: it takes no new grants.
Nothing here kills a job or releases a lease (spec §7)."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from coscience.host_probe import Runner, ssh_argv, subprocess_runner

HEALTH_FILE = ".coscience/host-health.json"
CHECK_INTERVAL = 60.0
QUIET_AFTER = 1800.0
CHECK_TIMEOUT = 20.0
# M3: an entry not refreshed in this long is no longer trustworthy as "still ok now" —
# read as unchecked instead, rather than showing a possibly very old all-clear as current.
STALE_AFTER = 600.0
default_runner: Runner = subprocess_runner


def _num(v) -> float:
    """M1: a malformed health entry (hand-edited or corrupted) must never crash a
    beat — anything that isn't a real number reads as 0.0."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _path(repo_root) -> Path:
    return Path(repo_root) / HEALTH_FILE


def load(repo_root) -> dict[str, dict]:
    try:
        data = json.loads(_path(repo_root).read_text())
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(repo_root, entries: dict[str, dict]) -> None:
    path = _path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(entries, indent=2, sort_keys=True))
    os.replace(tmp, path)


def state(entry: dict | None, now: float) -> str:
    if not entry:
        return "unchecked"
    since = _num(entry.get("fail_since"))
    if not since:
        # No failure on record: "ok" only while that clean check is still recent —
        # a check from long ago is stale, not current (M3).
        checked_at = _num(entry.get("checked_at"))
        if now - checked_at > STALE_AFTER:
            return "unchecked"
        return "ok"
    return "quiet" if now - since >= QUIET_AFTER else "failing"


def quiet(entries: dict[str, dict], now: float) -> set[str]:
    return {name for name, entry in entries.items() if state(entry, now) == "quiet"}


def _ask(host, runner: Runner) -> tuple[int, str, str]:
    try:
        return runner(ssh_argv(host.ssh) + ["bash -c true"], None, CHECK_TIMEOUT)
    except ValueError as exc:
        return 255, "", str(exc)


def check(repo_root, pool, now: float, runner: Runner | None = None) -> dict[str, dict]:
    """Ask every placeable remote host that is due; keep entries only for hosts still
    in the pool. Returns the entries after this round.

    Due hosts are asked concurrently (bounded pool of threads), not one after
    another: N unreachable hosts would otherwise block a cycle up to N * CHECK_TIMEOUT
    (each ssh call blocks on its own network I/O, so this is a plain thread pool, not
    async)."""
    runner = runner or default_runner
    old = load(repo_root)
    # M3: a host that is no longer placeable (remote launch turned off, or a stale
    # entry left over from before) is never re-checked below, so its old entry would
    # sit there unrefreshed forever, read as current — drop it instead.
    names = {h.name for h in pool.hosts if h.ssh and h.placeable}
    entries = {name: entry for name, entry in old.items() if name in names}
    due = [host for host in pool.hosts if host.ssh and host.placeable
           and not (entries.get(host.name)
                    and now - _num(entries[host.name].get("checked_at")) < CHECK_INTERVAL)]
    if due:
        with ThreadPoolExecutor(max_workers=min(8, len(due))) as pool_exec:
            answers = zip(due, pool_exec.map(lambda h: _ask(h, runner), due))
        for host, (code, _, err) in answers:
            prev = entries.get(host.name, {})
            if code == 0:
                entries[host.name] = {"checked_at": now, "last_ok": now, "fail_since": 0.0, "reason": ""}
            else:
                lines = [line for line in str(err).strip().splitlines() if line.strip()]
                entries[host.name] = {
                    "checked_at": now, "last_ok": _num(prev.get("last_ok")),
                    "fail_since": _num(prev.get("fail_since")) or now,
                    "reason": lines[-1] if lines else f"ssh exited {code}"}
    if entries != old:
        _save(repo_root, entries)
    return entries
