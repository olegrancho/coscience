"""Onboarding probe: what a server really has, read over key-only SSH (O5).

A fixed read-only shell script and four checks, run through an injectable runner so
tests never touch a network. Nothing here writes the pool: it returns facts, check
results, warnings and a proposed host entry for a human to confirm."""
from __future__ import annotations

import math
import re
import shlex
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable

# (argv, stdin text or None, timeout seconds) -> (exit code, stdout, stderr)
Runner = Callable[[list[str], "str | None", float], tuple[int, str, str]]

# Never prompt, never hang on a dead address, and keep the default host-key policy:
# an unknown key fails the probe rather than being accepted silently.
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
DEFAULT_RUN_ROOT = "~/coscience-runs"
PROBE_TIMEOUT = 60.0
CHECK_TIMEOUT = 30.0
_TARGET = re.compile(r"^(?!-)(?:[A-Za-z0-9._-]+@)?(?!-)[A-Za-z0-9._-]+(?::\d{1,5})?$")
_RUN_ROOT = re.compile(r"^(~|~/[A-Za-z0-9._/-]*|/[A-Za-z0-9._/-]*)$")
_SCRATCH = ".coscience-probe"
_WANTED_TOOLS = ("rsync", "setsid", "python3")

PROBE_SCRIPT = r'''
set +e
kv() { printf '%s=%s\n' "$1" "$2"; }
kv hostname "$(hostname 2>/dev/null)"
kv os "$(. /etc/os-release 2>/dev/null; printf '%s' "$PRETTY_NAME")"
kv kernel "$(uname -r 2>/dev/null)"
kv arch "$(uname -m 2>/dev/null)"
kv glibc "$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$')"
kv cpu_model "$(LC_ALL=C lscpu 2>/dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -1)"
kv threads "$(nproc 2>/dev/null)"
kv sockets "$(LC_ALL=C lscpu 2>/dev/null | sed -n 's/^Socket(s):[[:space:]]*//p' | head -1)"
kv cores_per_socket "$(LC_ALL=C lscpu 2>/dev/null | sed -n 's/^Core(s) per socket:[[:space:]]*//p' | head -1)"
kv load1 "$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
kv users "$(who 2>/dev/null | cut -d' ' -f1 | sort -u | grep -c .)"
kv mem_total_kb "$(sed -n 's/^MemTotal:[[:space:]]*\([0-9]*\).*/\1/p' /proc/meminfo)"
kv mem_available_kb "$(sed -n 's/^MemAvailable:[[:space:]]*\([0-9]*\).*/\1/p' /proc/meminfo)"
kv swap_total_kb "$(sed -n 's/^SwapTotal:[[:space:]]*\([0-9]*\).*/\1/p' /proc/meminfo)"
if command -v nvidia-smi >/dev/null 2>&1; then
  kv nvidia_smi present
  nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader,nounits 2>/dev/null \
    | while IFS=, read -r idx name mem drv; do kv gpu "$(echo $idx)|$(echo $name)|$(echo $mem)|$(echo $drv)"; done
  kv cuda "$(nvidia-smi 2>/dev/null | grep -oE 'CUDA Version: [0-9.]+' | grep -oE '[0-9.]+$')"
  kv gpu_processes "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -c .)"
fi
root="$RUN_ROOT"; case "$root" in "~"*) root="$HOME${root#\~}";; esac
probe="$root"; while [ ! -d "$probe" ] && [ "$probe" != "/" ]; do probe="$(dirname "$probe")"; done
kv disk_free_kb "$(df -Pk "$probe" 2>/dev/null | awk 'NR==2 {print $4}')"
kv disk_used_pct "$(df -Pk "$probe" 2>/dev/null | awk 'NR==2 {gsub("%",""); print $5}')"
for t in rsync setsid nohup python3 git conda uv docker apptainer claude; do kv tool "$t|$(command -v $t 2>/dev/null)"; done
kv python "$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?')"
if command -v curl >/dev/null 2>&1; then kv internet "$(curl -sI -m 5 https://pypi.org >/dev/null 2>&1 && echo yes || echo no)"; else kv internet unknown; fi
kv boot_id "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"
kv clock "$(date +%s)"
'''


def subprocess_runner(argv: list[str], stdin: str | None, timeout: float) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:g}s"
    except OSError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def ssh_argv(target: str) -> list[str]:
    """`ssh` argv for an alias, `user@host` or `user@host:port`. Anything else is
    refused — in particular a leading "-", which ssh would read as an option."""
    if not _TARGET.match(target or ""):
        raise ValueError(f"ssh target must be an alias, user@host or user@host:port, not {target!r}")
    host, port = target, ""
    if re.search(r":\d{1,5}$", target):
        host, port = target.rsplit(":", 1)
    argv = ["ssh", *SSH_OPTIONS]
    if port:
        argv += ["-p", port]
    return argv + [host]


def remote_path(path: str) -> str:
    """A path for a remote shell command: `~` expands on the server, the rest is quoted."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def check_run_root(run_root: str) -> str:
    """A run root reaches the rsync remote path unquoted (newer rsync clients escape
    remote arguments themselves, so quoting it here would just add literal characters
    to the path) — so it is validated up front instead of quoted: `~`, a path under
    `~/`, or an absolute path, in a restricted character set, with no `..` segment.
    Returns it with any trailing "/" removed (but keeps "~" and "/" as they are)."""
    value = run_root or ""
    trimmed = value if value in ("~", "/") else value.rstrip("/")
    parts = trimmed.split("/")
    # A "." or ".." component (or a doubled "/") would resolve to the parent or to
    # the run root's own root — `~/.` and `~/./` both mean "all of home" to rsync.
    # "~" and "/" alone are the legitimate whole-root values and skip this check.
    # A run of slashes with nothing else ("//", "///", ...) rstrips to "" — that is
    # never a legitimate value (unlike the single "/") and must be refused too.
    valid = (bool(_RUN_ROOT.match(value)) and trimmed != "" and ".." not in parts
             and (trimmed in ("~", "/") or not any(p in ("", ".", "..") for p in parts[1:])))
    if not valid:
        raise ValueError("run root must be ~, a path under ~/ or an absolute path, using only "
                         "letters, digits, '.', '_', '-' and '/'")
    return trimmed


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _version(text) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", str(text or ""))[:3])


def parse_facts(stdout: str, now: float) -> dict:
    """The probe script's `key=value` lines as typed facts."""
    facts: dict = {"gpus": [], "tools": {}}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if key == "gpu":
            parts = [p.strip() for p in value.split("|")]
            if len(parts) == 4:
                try:
                    index = int(parts[0])
                except ValueError:
                    continue
                vram = _float(parts[2])
                facts["gpus"].append({"index": index, "model": parts[1],
                                      "vram_gb": None if vram is None else round(vram / 1024, 1),
                                      "driver": parts[3]})
        elif key == "tool":
            name, _, path = value.partition("|")
            facts["tools"][name] = path
        else:
            facts[key] = value
    for key in ("threads", "sockets", "cores_per_socket", "users", "mem_total_kb",
                "mem_available_kb", "swap_total_kb", "gpu_processes", "disk_free_kb",
                "disk_used_pct", "clock"):
        facts[key] = _int(facts.get(key))
    facts["load1"] = _float(facts.get("load1"))
    facts["cores"] = (facts["sockets"] * facts["cores_per_socket"]
                      if facts["sockets"] and facts["cores_per_socket"] else None)
    facts["clock_offset_s"] = facts["clock"] - int(now) if facts["clock"] is not None else None
    internet = facts.get("internet")
    facts["internet"] = True if internet == "yes" else False if internet == "no" else None
    return facts


def run_checks(runner: Runner, target: str, run_root: str) -> list[dict]:
    """The three checks after login: the run root takes files, a detached job outlives
    the ssh session that started it, and rsync carries a file up and back. They touch
    only `<run_root>/.coscience-probe/` and one `sleep` the check itself kills."""
    run_root = check_run_root(run_root)
    base = ssh_argv(target)
    scratch = remote_path(f"{run_root.rstrip('/')}/{_SCRATCH}")
    checks = []

    def remote(command: str) -> list[str]:
        return base + [f"bash -c {shlex.quote(command)}"]

    try:
        code, out, err = runner(remote(f"mkdir -p {scratch} && touch {scratch}/write-test"
                                        f" && rm -f {scratch}/write-test && echo ok"), None, CHECK_TIMEOUT)
        checks.append({"name": "run root writable", "ok": code == 0 and "ok" in out,
                       "detail": "" if code == 0 else (err.strip() or f"exit {code}")})

        code, out, err = runner(remote("setsid nohup sleep 20 >/dev/null 2>&1 < /dev/null & echo $!"),
                                None, CHECK_TIMEOUT)
        pid = _int(out.strip().splitlines()[-1] if out.strip() else "")
        if code == 0 and pid:
            code, out, _ = runner(remote(f"sleep 2; kill -0 {pid} && kill {pid} && echo alive"),
                                  None, CHECK_TIMEOUT)
            survived = code == 0 and "alive" in out
            checks.append({"name": "detached job survives", "ok": survived,
                           "detail": "" if survived else "the job was gone once its ssh session closed"})
        else:
            checks.append({"name": "detached job survives", "ok": False,
                           "detail": err.strip() or "could not start a detached job"})

        host = base[-1]
        shell = " ".join(shlex.quote(a) for a in base[:-1])
        remote_file = f"{host}:{run_root.rstrip('/')}/{_SCRATCH}/rsync-test"
        with tempfile.TemporaryDirectory() as tmp:
            up, down = Path(tmp) / "up", Path(tmp) / "down"
            token = f"coscience-probe {uuid.uuid4().hex}"
            up.write_text(token)
            code_up, _, err_up = runner(["rsync", "-e", shell, str(up), remote_file], None, CHECK_TIMEOUT)
            code_down, _, err_down = runner(["rsync", "-e", shell, remote_file, str(down)], None, CHECK_TIMEOUT)
            both = code_up == 0 and code_down == 0 and down.is_file() and down.read_text() == token
            checks.append({"name": "rsync both ways", "ok": both,
                           "detail": "" if both else (err_up.strip() or err_down.strip()
                                                      or "the file did not come back intact")})
    finally:
        runner(remote(f"rm -rf {scratch}"), None, CHECK_TIMEOUT)
    return checks


def warnings_for(facts: dict, checks: list[dict], shared: bool) -> list[str]:
    """What will bite a sprint on this server, in the words a human acts on."""
    out: list[str] = []
    pct, free = facts.get("disk_used_pct"), facts.get("disk_free_kb")
    if pct is not None and pct >= 90:
        out.append(f"the run root's disk is {pct}% full")
    if free is not None and free < 50 * 1024 * 1024:
        out.append(f"only {free / 1024 / 1024:.0f} GB free under the run root")
    glibc = facts.get("glibc")
    if glibc and _version(glibc) < (2, 28):
        out.append(f"glibc {glibc} is old: many current Python wheels and binaries will not run")
    cuda = facts.get("cuda")
    if facts.get("gpus") and cuda and _version(cuda) < (12,):
        driver = facts["gpus"][0].get("driver", "")
        out.append(f"GPU driver {driver} supports CUDA {cuda} at most: current PyTorch builds need a newer driver")
    for tool in _WANTED_TOOLS:
        if tool in facts.get("tools", {}) and not facts["tools"][tool]:
            out.append(f"{tool} is not installed")
    offset = facts.get("clock_offset_s")
    if offset is not None and abs(offset) > 60:
        out.append(f"the clock is {abs(offset)}s off from this machine")
    if facts.get("internet") is False:
        out.append("no outbound internet: pip installs and model downloads will fail")
    if facts.get("nvidia_smi") == "present" and not facts.get("gpus"):
        out.append("nvidia-smi is installed but reported no GPUs: check the driver")
    if any(g.get("vram_gb") is None for g in facts.get("gpus", [])):
        out.append("a GPU's VRAM could not be read: it will be lent whole until its VRAM is declared")
    users = facts.get("users") or 0
    if not shared and users > 1:
        out.append(f"{users} users are logged in on a server declared dedicated")
    if facts.get("gpu_processes"):
        out.append(f"{facts['gpu_processes']} process(es) already use the GPUs")
    for check in checks:
        if not check["ok"]:
            out.append(f"check failed: {check['name']}" + (f" — {check['detail']}" if check["detail"] else ""))
    return out


def propose(facts: dict, shared: bool) -> dict:
    """A host entry to start from: every thread (half on a shared server), 90% of the
    memory, and one card per GPU with its VRAM. The human adjusts before confirming."""
    threads = facts.get("threads") or 0
    cpu = threads // 2 if shared else threads
    mem_kb = facts.get("mem_total_kb") or 0
    memory_gb = math.floor(mem_kb / 1024 / 1024 * 0.9) if mem_kb else 0
    capacity: dict[str, float] = {}
    if cpu:
        capacity["cpu"] = float(cpu)
    if memory_gb:
        capacity["memory_gb"] = float(memory_gb)
    cards = facts.get("gpus", [])
    if cards and any(g["vram_gb"] is None for g in cards):
        gpus: list[dict] = []
        capacity["gpu"] = float(len(cards))
    else:
        gpus = [{"model": g["model"], "vram_gb": g["vram_gb"]} for g in cards]
    return {"capacity": capacity, "gpus": gpus}


def _login_error(target: str, code: int, err: str) -> str:
    if "Host key verification failed" in err:
        return (f"the server's host key is not known yet: connect once by hand (ssh {target}), "
                "accept the key, then probe again")
    if "Permission denied" in err:
        return "key-only login was refused: install this account's public key on the server"
    if code == 124:
        return "the probe timed out"
    lines = [l for l in err.strip().splitlines() if l.strip()]
    return lines[-1] if lines else f"ssh exited {code}"


def probe_host(target: str, run_root: str = DEFAULT_RUN_ROOT, *, shared: bool = False,
               runner: Runner = subprocess_runner, now: float | None = None) -> dict:
    """Log in, read the server, run the checks, and propose an entry. A failed login
    returns at once with the reason; no check runs without a working login."""
    now = time.time() if now is None else now
    run_root = check_run_root(run_root)
    argv = ssh_argv(target) + ["bash", "-s"]
    code, out, err = runner(argv, f"RUN_ROOT={shlex.quote(run_root)}\n{PROBE_SCRIPT}", PROBE_TIMEOUT)
    if code != 0:
        return {"ok": False, "error": _login_error(target, code, err), "facts": {},
                "checks": [], "warnings": [], "proposal": {}}
    facts = parse_facts(out, now)
    checks = [{"name": "key-only SSH", "ok": True, "detail": "logged in without a password"}]
    checks += run_checks(runner, target, run_root)
    return {"ok": True, "error": "", "facts": facts, "checks": checks,
            "warnings": warnings_for(facts, checks, shared), "proposal": propose(facts, shared)}
