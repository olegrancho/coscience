"""Remote job control over key-only SSH (O6).

The worker agent starts a remote job itself (O1); this module is what the platform
uses afterwards: read a job's identity on its host, tell alive from gone from lost
from unknown, stop its process group, and copy its declared outputs back. Every call
goes through an injectable runner, so tests never touch a network."""
from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from coscience.host_probe import Runner, ssh_argv, subprocess_runner

REMOTE_TIMEOUT = 30.0
COLLECT_TIMEOUT = 600.0
_SAFE_PATH = re.compile(r"^(~/[A-Za-z0-9._/-]+|/[A-Za-z0-9._/-]+)$")

# Each sleeping remote sprint's job_alive check reads the job's identity every beat
# (~5s), and an unreachable host makes that read a full ssh timeout (10-30s) —
# `dispatch --loop` builds a new Worker every beat, so a back-off on the Worker
# itself would be lost immediately. Keyed by ssh target so one dead host backs off
# on its own; other hosts are unaffected. Only read_identity uses this — terminate
# and collect are rare and matter enough to always try.
_unreachable_until: dict[str, float] = {}
UNREACHABLE_BACKOFF = 300.0


@dataclass(frozen=True)
class RemoteToken:
    host: str
    pid: int
    starttime: str       # "" when it could not be read at declare time
    boot_id: str         # "" when it could not be read at declare time

    def __str__(self) -> str:
        return f"{self.host}:{self.pid}:{self.starttime}:{self.boot_id}"


def parse_token(token: str) -> RemoteToken | None:
    """A remote token `<host>:<pid>:<starttime>:<boot_id>`, or None for a local
    `<pid>:<starttime>` token or anything malformed. Host names never contain ':'."""
    parts = str(token or "").split(":")
    if len(parts) != 4 or not parts[0]:
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    return RemoteToken(parts[0], pid, parts[2], parts[3]) if pid > 0 else None


def _run(runner: Runner, ssh_target: str, command: str) -> tuple[int, str, str]:
    return runner(ssh_argv(ssh_target) + [f"bash -c {shlex.quote(command)}"], None, REMOTE_TIMEOUT)


def read_identity(ssh_target: str, pid: int, runner: Runner = subprocess_runner, *,
                  respect_backoff: bool = True) -> tuple[str, str, str] | None:
    """(boot_id, state, starttime) of `pid` on the host — state and starttime "" when
    there is no such process — or None when the host could not be asked (including an
    invalid `ssh_target` — never raises). Each line the remote command prints is
    labelled, so a boot id that could not be read (blank after `boot=`) is never
    mistaken for a `proc=` line or for a missing answer.

    A host that just failed to answer is not asked again for `UNREACHABLE_BACKOFF`
    seconds — every sleeping sprint on it would otherwise pay its own full ssh
    timeout on every beat. A success (here, or from any other caller) clears the
    back-off at once. `respect_backoff=False` (make_token's declare-time read) skips
    the skip: a fresh declaration must always get a real answer, never a stale
    identity-less token that then never gets re-checked for the whole window."""
    if respect_backoff and time.monotonic() < _unreachable_until.get(ssh_target, 0.0):
        return None
    pid = int(pid)
    command = (
        'printf \'boot=%s\\n\' "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"; '
        f"if [ -r /proc/{pid}/stat ]; then "
        f"printf 'proc=%s\\n' \"$(sed 's/.*) //' /proc/{pid}/stat | cut -d' ' -f1,20)\"; fi"
    )
    try:
        code, out, _ = _run(runner, ssh_target, command)
    except ValueError:
        # An invalid ssh target (Fix C should have caught this at parse time; this is
        # defence in depth) — never raise mid-beat.
        return None
    if code != 0:
        _unreachable_until[ssh_target] = time.monotonic() + UNREACHABLE_BACKOFF
        return None
    boot_id = None
    state, starttime = "", ""
    for line in out.splitlines():
        if line.startswith("boot="):
            boot_id = line[len("boot="):].strip()
        elif line.startswith("proc="):
            fields = line[len("proc="):].strip().split()
            if len(fields) == 2 and fields[1].isdigit():
                state, starttime = fields
    if boot_id is None:
        _unreachable_until[ssh_target] = time.monotonic() + UNREACHABLE_BACKOFF
        return None
    _unreachable_until.pop(ssh_target, None)
    return boot_id, state, starttime


def make_token(host: str, ssh_target: str, pid: int, runner: Runner = subprocess_runner) -> tuple[str, str]:
    """(token, state) for a job the agent just declared. State is "alive", "gone" (not
    running on its host), or "unknown" (the host could not be asked; the token then
    carries no identity and liveness falls back to the pid). Bypasses the back-off
    (see read_identity): a declaration always gets a real answer."""
    identity = read_identity(ssh_target, pid, runner, respect_backoff=False)
    if identity is None:
        return str(RemoteToken(host, int(pid), "", "")), "unknown"
    boot_id, state, starttime = identity
    token = str(RemoteToken(host, int(pid), starttime, boot_id))
    if not starttime or state == "Z":
        return token, "gone"
    return token, "alive"


def job_state(ssh_target: str, token: RemoteToken, runner: Runner = subprocess_runner) -> str:
    """"alive"; "gone" (exited, or its pid now belongs to another process); "lost" (the
    host rebooted since the job was declared); or "unknown" (the host could not be
    asked — never to be treated as dead)."""
    identity = read_identity(ssh_target, token.pid, runner)
    if identity is None:
        return "unknown"
    boot_id, state, starttime = identity
    if token.boot_id and boot_id and boot_id != token.boot_id:
        return "lost"
    if not starttime or state == "Z":
        return "gone"
    if token.starttime and starttime != token.starttime:
        return "gone"
    return "alive"


def terminate(ssh_target: str, token: RemoteToken, grace: float = 2.0,
              runner: Runner = subprocess_runner) -> bool:
    """Stop the job's process group on its host — TERM, then KILL after `grace` — but
    only while its pid still carries the declared start time AND is the leader of
    its own process group (pid == pgid, true for anything launched the documented
    way). With no start time to verify, it never signals: returns False without
    calling the runner. Also False when the host could not be asked (including an
    invalid `ssh_target` — never raises), or when the pid is alive but leads a group
    it did not create (never touched)."""
    if not token.starttime.isdigit():
        return False
    pid = int(token.pid)
    same_process = f'[ "$(sed "s/.*) //" /proc/{pid}/stat | cut -d" " -f20)" = "{token.starttime}" ] || exit 0; '
    # The documented launch form (setsid nohup ... & echo $!) makes the job its own
    # group leader, so pid == pgid. Refuse to signal any group this pid merely
    # belongs to (exit 3) — a wrong-but-live pid must never take down someone
    # else's whole process group (a tmux server, another user's notebook).
    same_leader = f'[ "$pg" = "{pid}" ] || exit 3; '
    # The process group (pgrp) is field 3 once "pid (comm) " is stripped, same as
    # the starttime read above (field 20) — read from /proc directly rather than
    # via `ps`, which is not guaranteed to be installed on every host.
    command = (f"[ -r /proc/{pid}/stat ] || exit 0; {same_process}"
               f'pg=$(sed "s/.*) //" /proc/{pid}/stat | cut -d" " -f3); [ -n "$pg" ] || exit 0; '
               f'{same_leader}'
               f'kill -TERM -- -"$pg" 2>/dev/null; sleep {float(grace):g}; '
               f'kill -KILL -- -"$pg" 2>/dev/null; exit 0')
    try:
        code, _, _ = _run(runner, ssh_target, command)
    except ValueError:
        return False
    return code == 0


def check_remote_path(path: str) -> str:
    """A path the platform may copy from a host: under ~/ or absolute, in safe
    characters, no `..`, and never the whole home directory or root."""
    value = str(path or "").strip()
    trimmed = value.rstrip("/")
    # A "." or ".." component (or a doubled "/") would resolve to the parent or to
    # the path's own root — `~/.` and `~/./` both mean "all of home" to rsync.
    if not _SAFE_PATH.match(trimmed) or any(p in ("", ".", "..") for p in trimmed.split("/")[1:]):
        raise ValueError(f"collect path {path!r} must be under ~/ or absolute, using only letters, "
                         "digits, '.', '_', '-' and '/'")
    return trimmed


def collect(ssh_target: str, paths: list[str], local_dir, runner: Runner = subprocess_runner) -> list[dict]:
    """Copy each declared path from the host into `local_dir`, never deleting anything
    on either side. All paths land flat in `local_dir` (the worker's wake note names
    `<dest>/<last component>`), so a path whose last component repeats an earlier one
    in the same call is refused — it would silently overwrite that path's files. One
    {"path", "ok", "detail"} per path, in order."""
    local = Path(local_dir)
    local.mkdir(parents=True, exist_ok=True)
    try:
        base = ssh_argv(ssh_target)
    except ValueError:
        # An invalid ssh target (defence in depth, as in read_identity/terminate) —
        # report every path as not copied rather than raise mid-beat.
        detail = f"invalid ssh target {ssh_target!r}: could not reach the host"
        return [{"path": str(p), "ok": False, "detail": detail} for p in paths]
    shell = " ".join(shlex.quote(a) for a in base[:-1])
    results = []
    seen_names: set[str] = set()
    for raw in paths:
        try:
            path = check_remote_path(raw)
        except ValueError as exc:
            results.append({"path": str(raw), "ok": False, "detail": str(exc)})
            continue
        name = path.rsplit("/", 1)[-1]
        if name in seen_names:
            results.append({"path": path, "ok": False,
                            "detail": f"another collect path already uses the folder name {name!r}; "
                                      "give each a different last folder name"})
            continue
        seen_names.add(name)
        code, _, err = runner(["rsync", "-a", "-e", shell, f"{base[-1]}:{path}", f"{local}/"],
                              None, COLLECT_TIMEOUT)
        lines = [line for line in err.strip().splitlines() if line.strip()]
        last = lines[-1] if lines else f"rsync exited {code}"
        # 24: some source files vanished while being copied (e.g. a scratch file the
        # job itself deleted mid-run) — the copy that DID happen is still usable.
        # 23: a partial transfer from a real error — that one is a failure.
        if code == 0:
            ok, detail = True, ""
        elif code == 24:
            ok, detail = True, "some files vanished during the copy"
        elif code == 23:
            ok, detail = False, f"partially copied: {last}"
        else:
            ok, detail = False, last
        results.append({"path": path, "ok": ok, "detail": detail})
    return results
