# O5 — Onboard a Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human add a server by its SSH target, have the platform probe what it really has and check it can run work, review a proposed host entry, and confirm it into the pool.

**Architecture:** A new `host_probe` module runs one fixed read-only shell script over key-only SSH and four checks (run root writable, a detached job survives the session, rsync both ways, plus the SSH login itself), all through an injectable runner so tests never touch a network. It turns the output into facts, warnings and a proposed entry. The service stores each probe as `.coscience/host-probes/<name>.json` in the substrate and, on confirmation, writes the host into `resources.yaml` `hosts:` in the O2 format, keeping the rest of the file. Remote hosts stay unplaceable until O6, so onboarding cannot change live scheduling. The Compute page gains a Servers card and an Add-server dialog: declare, probe, review, confirm.

**Tech Stack:** Python 3.12, subprocess (ssh, rsync), pytest; React + Mantine + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§4 Hosts, §5 Onboarding, §10 row O5)

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python`, prefixed `PYTHONPATH=src` in a worktree. Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Access is SSH key only. Every ssh call uses `-o BatchMode=yes -o ConnectTimeout=10` and the default host-key policy; nothing adds keys to `known_hosts`.
- An SSH target is an alias, `user@host` or `user@host:port`; anything else — in particular a leading `-` — is refused before ssh runs.
- The probe script only reads. The checks write only under `<run_root>/.coscience-probe/` and start one `sleep 20` process that the check kills.
- Tests never run ssh or rsync: they inject a fake runner.
- A host name matches `[A-Za-z0-9._-]+` and is not `local`.
- Confirming writes `resources.yaml` atomically and keeps every other key; a confirmed remote host is not placeable (unchanged from O2).
- Nothing in stored probe records or host entries is a secret.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/host_probe.py` | create | ssh argv, probe script, fact parsing, checks, warnings, proposal |
| `src/coscience/resources.py` | modify | `Host.shared`, `Host.owner`, `Host.notes` |
| `src/coscience/service.py` | modify | `probe_host`, `list_host_probes`, `confirm_host`; hosts in `ledger_status` carry the new fields |
| `src/coscience/http_api.py` | modify | `POST /api/hosts/probe`, `GET /api/hosts/probes`, `POST /api/hosts` |
| `tests/host_probe_fakes.py` | create | sample probe output and a fake runner shared by tests |
| `tests/test_host_probe.py` | create | module tests |
| `tests/test_host_onboarding.py` | create | service tests |
| `tests/test_service_capacity.py` | modify | host dicts gain `shared`, `owner`, `notes` |
| `tests/test_http_api.py` | modify | onboarding routes |
| `frontend/src/api.ts` | modify | host, card and probe types; `probeHost`, `listHostProbes`, `confirmHost` |
| `frontend/src/components/HostsCard.tsx` | create | servers table, errors, Add-server button |
| `frontend/src/components/AddHostModal.tsx` | create | declare → probe → review → confirm |
| `frontend/src/views/Ledger.tsx` | modify | place the Servers card |
| `frontend/src/components/HostsCard.test.tsx` | create | card tests |
| `frontend/src/components/AddHostModal.test.tsx` | create | dialog tests |

---

### Task 1: The probe module

**Files:**
- Create: `src/coscience/host_probe.py`, `tests/host_probe_fakes.py`, `tests/test_host_probe.py`

**Interfaces:**
- Produces:
  - `Runner = Callable[[list[str], str | None, float], tuple[int, str, str]]`; `subprocess_runner`
  - `SSH_OPTIONS`, `DEFAULT_RUN_ROOT = "~/coscience-runs"`, `PROBE_SCRIPT`
  - `ssh_argv(target: str) -> list[str]` (raises `ValueError` for a bad target)
  - `remote_path(path: str) -> str` (shell-quoted, `~` expanded remotely)
  - `parse_facts(stdout: str, now: float) -> dict`
  - `run_checks(runner, target, run_root) -> list[dict]` — each `{"name", "ok", "detail"}`
  - `warnings_for(facts: dict, checks: list[dict], shared: bool) -> list[str]`
  - `propose(facts: dict, shared: bool) -> dict` — `{"capacity": {...}, "gpus": [{"model", "vram_gb"}]}`
  - `probe_host(target, run_root=DEFAULT_RUN_ROOT, *, shared=False, runner=subprocess_runner, now=None) -> dict` — `{"ok", "error", "facts", "checks", "warnings", "proposal"}`

- [ ] **Step 1: Create the shared fakes**

Create `tests/host_probe_fakes.py`:

```python
"""A canned probe run for onboarding tests: sample output and a fake runner that
answers the ssh and rsync calls `host_probe` makes, without a network."""
from pathlib import Path

SAMPLE_OUTPUT = """hostname=gpu-box
os=Example Linux 9
kernel=5.15.0
arch=x86_64
glibc=2.17
cpu_model=Example CPU
threads=12
sockets=1
cores_per_socket=6
load1=0.12
users=4
mem_total_kb=65000000
mem_available_kb=50000000
swap_total_kb=0
gpu=0|Example GPU 11GB|11019|460.39
cuda=11.2
gpu_processes=0
disk_free_kb=150000000
disk_used_pct=97
tool=rsync|/usr/bin/rsync
tool=setsid|/usr/bin/setsid
tool=nohup|/usr/bin/nohup
tool=python3|/usr/bin/python3
tool=git|/usr/bin/git
tool=conda|
tool=uv|
tool=docker|
tool=apptainer|
tool=claude|
python=3.6.8
internet=yes
boot_id=abc
clock=1000
"""


class FakeRunner:
    """Scripted answers keyed on what each call is doing. `overrides` maps a label
    ("probe", "write", "start", "alive", "rsync", "cleanup") to (code, stdout, stderr)."""

    def __init__(self, overrides=None):
        self.calls: list[list[str]] = []
        self.overrides = dict(overrides or {})
        self._uploaded = ""

    def __call__(self, argv, stdin, timeout):
        self.calls.append(list(argv))
        label = self._label(argv)
        if label in self.overrides:
            return self.overrides[label]
        if label == "probe":
            return 0, SAMPLE_OUTPUT, ""
        if label == "write":
            return 0, "ok\n", ""
        if label == "start":
            return 0, "4242\n", ""
        if label == "alive":
            return 0, "alive\n", ""
        if label == "rsync":
            src, dst = argv[-2], argv[-1]
            if Path(src).is_file():
                self._uploaded = Path(src).read_text()
            else:
                Path(dst).write_text(self._uploaded)
            return 0, "", ""
        return 0, "", ""

    @staticmethod
    def _label(argv):
        if argv[0] == "rsync":
            return "rsync"
        command = argv[-1]
        if argv[-2:] == ["bash", "-s"]:
            return "probe"
        if "touch" in command:
            return "write"
        if "setsid" in command:
            return "start"
        if "kill -0" in command:
            return "alive"
        if "rm -rf" in command:
            return "cleanup"
        return "other"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_host_probe.py`:

```python
"""O5: the onboarding probe reads a server over key-only SSH and proposes an entry."""
import pytest

from coscience import host_probe
from coscience.host_probe import (SSH_OPTIONS, parse_facts, probe_host, propose, remote_path,
                                  ssh_argv, warnings_for)
from tests.host_probe_fakes import SAMPLE_OUTPUT, FakeRunner


def test_ssh_argv_accepts_an_alias_a_user_and_a_port():
    assert ssh_argv("gpu1") == ["ssh", *SSH_OPTIONS, "gpu1"]
    assert ssh_argv("me@box.example") == ["ssh", *SSH_OPTIONS, "me@box.example"]
    assert ssh_argv("me@box:2222") == ["ssh", *SSH_OPTIONS, "-p", "2222", "me@box"]


@pytest.mark.parametrize("target", ["", "-oProxyCommand=x", "a b", "me@-x", "box;rm", "me@box:port"])
def test_ssh_argv_refuses_anything_else(target):
    with pytest.raises(ValueError, match="ssh target"):
        ssh_argv(target)


def test_ssh_options_never_prompt_and_never_accept_new_host_keys():
    assert "BatchMode=yes" in SSH_OPTIONS
    assert not any("StrictHostKeyChecking" in o for o in SSH_OPTIONS)


def test_remote_path_expands_home_on_the_server_and_quotes_the_rest():
    assert remote_path("~/coscience-runs") == '"$HOME"/coscience-runs'
    assert remote_path("~/runs dir") == "\"$HOME\"/'runs dir'"
    assert remote_path("/data/runs") == "/data/runs"


def test_parse_facts_reads_the_probe_output():
    facts = parse_facts(SAMPLE_OUTPUT, now=990.0)
    assert facts["threads"] == 12 and facts["cores"] == 6
    assert facts["mem_total_kb"] == 65000000
    assert facts["gpus"] == [{"index": 0, "model": "Example GPU 11GB", "vram_gb": 10.8,
                              "driver": "460.39"}]
    assert facts["tools"]["rsync"] == "/usr/bin/rsync" and facts["tools"]["conda"] == ""
    assert facts["clock_offset_s"] == 10
    assert facts["internet"] is True
    assert facts["disk_used_pct"] == 97


def test_propose_offers_all_threads_or_half_on_a_shared_server():
    facts = parse_facts(SAMPLE_OUTPUT, now=1000.0)
    assert propose(facts, shared=False) == {
        "capacity": {"cpu": 12.0, "memory_gb": 55.0},
        "gpus": [{"model": "Example GPU 11GB", "vram_gb": 10.8}]}
    assert propose(facts, shared=True)["capacity"]["cpu"] == 6.0


def test_warnings_name_what_will_bite():
    facts = parse_facts(SAMPLE_OUTPUT, now=1000.0)
    warnings = warnings_for(facts, [], shared=False)
    text = "\n".join(warnings)
    assert "97% full" in text
    assert "glibc 2.17" in text
    assert "CUDA 11.2" in text
    assert "4 users are logged in" in text


def test_a_probe_runs_the_script_and_every_check():
    runner = FakeRunner()
    result = probe_host("gpu1", runner=runner, now=1000.0)
    assert result["ok"] is True and result["error"] == ""
    assert [c["name"] for c in result["checks"]] == [
        "key-only SSH", "run root writable", "detached job survives", "rsync both ways"]
    assert all(c["ok"] for c in result["checks"])
    assert result["proposal"]["capacity"]["cpu"] == 12.0
    assert runner.calls[0][-2:] == ["bash", "-s"]
    assert any(call[-1].startswith("rm -rf") for call in runner.calls)     # scratch cleaned up


def test_an_unknown_host_key_is_explained():
    runner = FakeRunner({"probe": (255, "", "Host key verification failed.\r\n")})
    result = probe_host("gpu1", runner=runner, now=1000.0)
    assert result["ok"] is False
    assert "connect once by hand" in result["error"]
    assert len(runner.calls) == 1                                          # no checks after a failed login


def test_a_refused_key_is_explained():
    runner = FakeRunner({"probe": (255, "", "me@box: Permission denied (publickey).\r\n")})
    assert "public key" in probe_host("gpu1", runner=runner, now=1000.0)["error"]


def test_a_failed_check_becomes_a_warning():
    runner = FakeRunner({"alive": (1, "", "")})
    result = probe_host("gpu1", runner=runner, now=1000.0)
    detached = next(c for c in result["checks"] if c["name"] == "detached job survives")
    assert detached["ok"] is False
    assert any(w.startswith("check failed: detached job survives") for w in result["warnings"])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_probe.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.host_probe'`

- [ ] **Step 4: Implement**

Create `src/coscience/host_probe.py`:

```python
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
kv internet "$(curl -sI -m 5 https://pypi.org >/dev/null 2>&1 && echo yes || echo no)"
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
                    facts["gpus"].append({"index": int(parts[0]), "model": parts[1],
                                          "vram_gb": round(float(parts[2]) / 1024, 1),
                                          "driver": parts[3]})
                except ValueError:
                    pass
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
    facts["internet"] = facts.get("internet") == "yes"
    return facts


def run_checks(runner: Runner, target: str, run_root: str) -> list[dict]:
    """The three checks after login: the run root takes files, a detached job outlives
    the ssh session that started it, and rsync carries a file up and back. They touch
    only `<run_root>/.coscience-probe/` and one `sleep` the check itself kills."""
    base = ssh_argv(target)
    scratch = remote_path(f"{run_root.rstrip('/')}/{_SCRATCH}")
    checks = []

    code, out, err = runner(base + [f"mkdir -p {scratch} && touch {scratch}/write-test"
                                    f" && rm -f {scratch}/write-test && echo ok"], None, CHECK_TIMEOUT)
    checks.append({"name": "run root writable", "ok": code == 0 and "ok" in out,
                   "detail": "" if code == 0 else (err.strip() or f"exit {code}")})

    code, out, err = runner(base + ["setsid nohup sleep 20 >/dev/null 2>&1 < /dev/null & echo $!"],
                            None, CHECK_TIMEOUT)
    pid = _int(out.strip().splitlines()[-1] if out.strip() else "")
    if code == 0 and pid:
        code, out, _ = runner(base + [f"kill -0 {pid} && kill {pid} && echo alive"], None, CHECK_TIMEOUT)
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

    runner(base + [f"rm -rf {scratch}"], None, CHECK_TIMEOUT)
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
    if not facts.get("internet"):
        out.append("no outbound internet: pip installs and model downloads will fail")
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
    gpus = [{"model": g["model"], "vram_gb": g["vram_gb"]} for g in facts.get("gpus", [])]
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
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_probe.py -q`
Expected: PASS

---

### Task 2: The service stores probes and confirms hosts

**Files:**
- Modify: `src/coscience/resources.py` (`Host`, `_parse_host`), `src/coscience/service.py`, `src/coscience/http_api.py`
- Test: `tests/test_host_onboarding.py` (create), `tests/test_service_capacity.py` (modify), `tests/test_http_api.py` (append)

**Interfaces:**
- Consumes: `host_probe.probe_host`, `host_probe.DEFAULT_RUN_ROOT`, `host_probe.subprocess_runner` (Task 1); `_parse_host`, `ResourcePool` (O2/O3).
- Produces:
  - `Host.shared: bool = False`, `Host.owner: str = ""`, `Host.notes: str = ""`, read from host entries; each host in `ledger_status()["hosts"]` gains `"shared"`, `"owner"`, `"notes"`.
  - `Service.probe_host(*, name, ssh, run_root="", shared=False, programs=None, owner="", notes="", runner=None) -> dict` — the stored record: `{"name", "declared": {"ssh", "run_root", "shared", "programs", "owner", "notes"}, "probed_at", "ok", "error", "facts", "checks", "warnings", "proposal"}`.
  - `Service.list_host_probes() -> list[dict]` (records sorted by name).
  - `Service.confirm_host(*, name, capacity, gpus=None) -> dict` (fresh `ledger_status`). Raises `NotFoundError` without a probe record and `ValueError` for a failed probe or an entry the parser refuses.
  - Routes: `POST /api/hosts/probe`, `GET /api/hosts/probes`, `POST /api/hosts`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_host_onboarding.py`:

```python
"""O5: a probed server is recorded, and a human confirms it into the pool."""
import json

import pytest
import yaml

from coscience.resources import ResourcePool
from coscience.service import NotFoundError, Service
from tests.host_probe_fakes import FakeRunner


def _write(tmp_path, text):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)


def test_a_probe_is_recorded_with_its_declaration(tmp_path):
    svc = Service(tmp_path)
    record = svc.probe_host(name="gpu1", ssh="gpu1", programs=["p2"], owner="ops",
                            runner=FakeRunner())
    assert record["ok"] is True
    assert record["declared"] == {"ssh": "gpu1", "run_root": "~/coscience-runs", "shared": False,
                                  "programs": ["p2"], "owner": "ops", "notes": ""}
    assert record["proposal"]["capacity"] == {"cpu": 12.0, "memory_gb": 55.0}
    stored = json.loads((tmp_path / ".coscience" / "host-probes" / "gpu1.json").read_text())
    assert stored["name"] == "gpu1"
    assert [r["name"] for r in svc.list_host_probes()] == ["gpu1"]


@pytest.mark.parametrize("name", ["", "local", "a b", "../x"])
def test_a_probe_refuses_a_bad_name(tmp_path, name):
    with pytest.raises(ValueError, match="name"):
        Service(tmp_path).probe_host(name=name, ssh="gpu1", runner=FakeRunner())


def test_a_probe_refuses_a_bad_ssh_target(tmp_path):
    with pytest.raises(ValueError, match="ssh target"):
        Service(tmp_path).probe_host(name="gpu1", ssh="-oProxyCommand=x", runner=FakeRunner())


def test_confirming_writes_the_host_and_keeps_the_rest_of_the_file(tmp_path):
    _write(tmp_path, "cpu: 4\nworkers: 2\n")
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1", programs=["p2"], runner=FakeRunner())

    status = svc.confirm_host(name="gpu1", capacity={"cpu": 10, "memory_gb": 50})

    written = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert written["cpu"] == 4 and written["workers"] == 2
    assert written["hosts"]["gpu1"] == {
        "ssh": "gpu1", "run_root": "~/coscience-runs", "programs": ["p2"],
        "capacity": {"cpu": 10.0, "memory_gb": 50.0},
        "gpus": [{"model": "Example GPU 11GB", "vram_gb": 10.8}]}
    assert [h["name"] for h in status["hosts"]] == ["local", "gpu1"]
    assert status["host_errors"] == []
    assert next(h for h in status["hosts"] if h["name"] == "gpu1")["placeable"] is False


def test_confirming_needs_a_probe(tmp_path):
    with pytest.raises(NotFoundError):
        Service(tmp_path).confirm_host(name="gpu1", capacity={"cpu": 1})


def test_confirming_a_failed_probe_is_refused(tmp_path):
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1",
                   runner=FakeRunner({"probe": (255, "", "Host key verification failed.")}))
    with pytest.raises(ValueError, match="probe"):
        svc.confirm_host(name="gpu1", capacity={"cpu": 1})


def test_confirming_refuses_an_entry_the_pool_would_reject(tmp_path):
    svc = Service(tmp_path)
    svc.probe_host(name="gpu1", ssh="gpu1", runner=FakeRunner())
    with pytest.raises(ValueError, match="platform-wide"):
        svc.confirm_host(name="gpu1", capacity={"workers": 1})


def test_a_host_entry_carries_its_owner_notes_and_sharing():
    host = ResourcePool.from_dict({"cpu": 1, "hosts": {"gpu1": {
        "ssh": "gpu1", "shared": True, "owner": "ops", "notes": "nights only"}}}).host("gpu1")
    assert (host.shared, host.owner, host.notes) == (True, "ops", "nights only")
```

In `tests/test_service_capacity.py`, `test_ledger_status_lists_every_host`: add `"shared": False, "owner": "", "notes": ""` to both expected host dicts.

Append to `tests/test_http_api.py` (it has a `client` fixture):

```python
def test_onboarding_routes_probe_list_and_confirm(client, monkeypatch):
    from coscience import host_probe
    from tests.host_probe_fakes import FakeRunner
    monkeypatch.setattr(host_probe, "subprocess_runner", FakeRunner())

    r = client.post("/api/hosts/probe", json={"name": "gpu1", "ssh": "gpu1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert [p["name"] for p in client.get("/api/hosts/probes").json()] == ["gpu1"]
    r = client.post("/api/hosts", json={"name": "gpu1", "capacity": {"cpu": 8}})
    assert r.status_code == 200
    assert "gpu1" in [h["name"] for h in r.json()["hosts"]]
    assert client.post("/api/hosts/probe", json={"name": "local", "ssh": "gpu1"}).status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_onboarding.py tests/test_http_api.py::test_onboarding_routes_probe_list_and_confirm -q`
Expected: FAIL — `AttributeError: 'Service' object has no attribute 'probe_host'`.

- [ ] **Step 3: Implement the host fields**

In `src/coscience/resources.py`, add to `Host` after `gpus`:

```python
    shared: bool = False                                 # other people use this machine too
    owner: str = ""                                      # who to ask about it
    notes: str = ""                                      # usage rules, e.g. hours or longest job
```

and in `_parse_host`'s final `return Host(...)` add `shared=bool(spec.get("shared", False)), owner=str(spec.get("owner") or ""), notes=str(spec.get("notes") or "")`.

- [ ] **Step 4: Implement the service**

In `src/coscience/service.py`:

1. In `ledger_status`, add `"shared": h.shared, "owner": h.owner, "notes": h.notes,` to each host dict.
2. Move the atomic write at the end of `set_capacity` into a helper and use it there:

```python
    def _write_resources(self, data: dict) -> None:
        path = self.repo_root / ".coscience" / "resources.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique per call: PUT /api/capacity is a sync route, so FastAPI runs it
        # in a threadpool and concurrent calls are genuinely concurrent. A shared
        # tmp name lets one thread's os.replace pull the file out from under
        # another thread's write/replace.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            tmp.write_text(yaml.safe_dump(data, sort_keys=True))
            os.replace(tmp, path)  # atomic: a dispatcher reading it never sees a partial file
        finally:
            tmp.unlink(missing_ok=True)
```

3. Add the onboarding methods (near the ledger section):

```python
    # --- onboarding (O5) ---
    _HOST_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

    def _host_probe_path(self, name: str) -> Path:
        return self.repo_root / ".coscience" / "host-probes" / f"{name}.json"

    def probe_host(self, *, name: str, ssh: str, run_root: str = "", shared: bool = False,
                   programs: list | None = None, owner: str = "", notes: str = "",
                   runner=None) -> dict:
        """Probe a server and record what was found, for a human to confirm. Nothing
        enters the pool here."""
        from coscience import host_probe
        name = str(name or "").strip()
        if not self._HOST_NAME.match(name) or name == "local":
            raise ValueError("a host name uses letters, digits, '.', '_' or '-' and is not 'local'")
        ssh = str(ssh or "").strip()
        host_probe.ssh_argv(ssh)                           # refuses a bad target before anything runs
        declared = {"ssh": ssh, "run_root": str(run_root or "").strip() or host_probe.DEFAULT_RUN_ROOT,
                    "shared": bool(shared), "programs": [str(p) for p in (programs or [])],
                    "owner": str(owner or ""), "notes": str(notes or "")}
        result = host_probe.probe_host(ssh, declared["run_root"], shared=declared["shared"],
                                       runner=runner or host_probe.subprocess_runner)
        record = {"name": name, "declared": declared, "probed_at": time.time(), **result}
        path = self._host_probe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2))
        self.substrate.commit(f"host {name} probed")
        return record

    def list_host_probes(self) -> list[dict]:
        folder = self.repo_root / ".coscience" / "host-probes"
        records = []
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                records.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        return records

    def confirm_host(self, *, name: str, capacity: dict, gpus: list | None = None) -> dict:
        """Write a probed server into `resources.yaml` `hosts:`, keeping the rest of the
        file. The entry is checked by the same parser the pool uses, so what is written
        is what loads."""
        from coscience.resources import _parse_host
        path = self._host_probe_path(str(name or ""))
        if not self._HOST_NAME.match(str(name or "")) or not path.is_file():
            raise NotFoundError(f"no probe recorded for host {name!r}")
        record = json.loads(path.read_text())
        if not record.get("ok"):
            raise ValueError(f"the last probe of {name} failed; probe it again before adding it")
        declared = record["declared"]
        entry: dict = {"ssh": declared["ssh"], "run_root": declared["run_root"],
                       "capacity": {str(k): float(v) for k, v in (capacity or {}).items()},
                       "gpus": gpus if gpus is not None else record["proposal"].get("gpus", [])}
        if not entry["gpus"]:
            del entry["gpus"]
        for key in ("programs", "shared", "owner", "notes"):
            if declared.get(key):
                entry[key] = declared[key]
        _parse_host(name, entry)                           # raises ValueError for a bad entry

        resources = self.repo_root / ".coscience" / "resources.yaml"
        loaded = yaml.safe_load(resources.read_text()) if resources.is_file() else {}
        loaded = loaded if isinstance(loaded, dict) else {}
        wrapped = loaded.get("resources")
        holder = wrapped if isinstance(wrapped, dict) and "hosts" in wrapped else loaded
        hosts = holder.get("hosts") if isinstance(holder.get("hosts"), dict) else {}
        hosts[name] = entry
        holder["hosts"] = hosts
        self._write_resources(loaded)
        self.substrate.commit(f"host {name} added to the pool")
        return self.ledger_status()
```

Add `import json`, `import re` and `import time` at the top of `service.py` if they are not already imported, and `from pathlib import Path` likewise.

- [ ] **Step 5: Add the routes**

In `src/coscience/http_api.py`, add models next to `CapacityUpdate`:

```python
class HostProbeIn(BaseModel):
    name: str
    ssh: str
    run_root: str = ""
    shared: bool = False
    programs: list[str] = Field(default_factory=list)
    owner: str = ""
    notes: str = ""


class HostConfirmIn(BaseModel):
    name: str
    capacity: dict[str, float] = Field(default_factory=dict)
    gpus: list[dict] | None = None
```

and routes after `PUT /capacity`:

```python
    @api.post("/hosts/probe")
    def probe_host(body: HostProbeIn) -> dict:
        try:
            return service.probe_host(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.get("/hosts/probes")
    def list_host_probes() -> list[dict]:
        return service.list_host_probes()

    @api.post("/hosts")
    def confirm_host(body: HostConfirmIn) -> dict:
        try:
            return service.confirm_host(**body.model_dump())
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_onboarding.py tests/test_host_probe.py tests/test_service_capacity.py tests/test_http_api.py tests/test_http_capacity.py tests/test_resources_hosts.py tests/test_gpu_pool.py -q`
Expected: PASS

---

### Task 3: The Compute page onboards a server

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/views/Ledger.tsx`
- Create: `frontend/src/components/HostsCard.tsx`, `frontend/src/components/AddHostModal.tsx`, `frontend/src/components/HostsCard.test.tsx`, `frontend/src/components/AddHostModal.test.tsx`

**Interfaces:**
- Consumes: `GET /api/ledger` `hosts` (with `gpus`, `shared`, `owner`, `notes`) and `host_errors`; `POST /api/hosts/probe`; `POST /api/hosts` (Task 2).
- Produces: `LedgerHost`, `LedgerCard`, `HostProbe`, `HostCheck` types; `api.probeHost`, `api.listHostProbes`, `api.confirmHost`; `HostsCard` (default export) and `hostOffer(host)`; `AddHostModal` (default export).

**Setup note:** `frontend/node_modules` is linked into the worktree already. Run frontend commands from `frontend/`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/HostsCard.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({ api: { probeHost: vi.fn(), confirmHost: vi.fn() } }));

import type { LedgerHost } from "../api";
import HostsCard, { hostOffer } from "./HostsCard";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const LOCAL: LedgerHost = {
  name: "local", ssh: "", placeable: true, programs: [], run_root: "",
  capacity: { cpu: 24, gpu: 1 }, available: {},
  gpus: [{ index: 0, model: "", vram_gb: null, whole: true, shared_gb: 0 }],
};
const REMOTE: LedgerHost = {
  name: "gpu1", ssh: "gpu1", placeable: false, programs: ["p2"], run_root: "~/coscience-runs",
  capacity: { cpu: 10, memory_gb: 50, gpu: 1 }, available: {},
  gpus: [{ index: 0, model: "X", vram_gb: 10.8, whole: false, shared_gb: 0 }],
};

function renderCard(errors: string[] = []) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <HostsCard hosts={[LOCAL, REMOTE]} errors={errors} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("hostOffer", () => {
  it("reads cores, memory and each card", () => {
    expect(hostOffer(LOCAL)).toBe("24 CPU cores · GPU (VRAM not declared)");
    expect(hostOffer(REMOTE)).toBe("10 CPU cores · 50 GB memory · 10.8 GB GPU");
  });
});

describe("HostsCard", () => {
  it("lists every server with how it is reached, what it offers and whether it takes work", () => {
    renderCard();
    expect(screen.getByText("this machine")).toBeTruthy();
    expect(screen.getByText("10 CPU cores · 50 GB memory · 10.8 GB GPU")).toBeTruthy();
    expect(screen.getByText("p2")).toBeTruthy();
    expect(screen.getByText("waits for remote launch")).toBeTruthy();
  });

  it("shows host errors from the pool file", () => {
    renderCard(["hosts.typo: needs ssh (an ssh alias or user@host)"]);
    expect(screen.getByText("hosts.typo: needs ssh (an ssh alias or user@host)")).toBeTruthy();
  });

  it("opens the add-server dialog", async () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(await screen.findByLabelText("SSH target")).toBeTruthy();
  });
});
```

Create `frontend/src/components/AddHostModal.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({ api: { probeHost: vi.fn(), confirmHost: vi.fn().mockResolvedValue({}) } }));

import { api } from "../api";
import AddHostModal from "./AddHostModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const OK_PROBE = {
  name: "gpu1", ok: true, error: "", facts: {}, probed_at: 1,
  declared: { ssh: "gpu1", run_root: "~/coscience-runs", shared: false, programs: [], owner: "", notes: "" },
  checks: [{ name: "rsync both ways", ok: true, detail: "" }],
  warnings: ["glibc 2.17 is old: many current Python wheels and binaries will not run"],
  proposal: { capacity: { cpu: 12, memory_gb: 55 }, gpus: [{ model: "X", vram_gb: 10.8 }] },
};

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <AddHostModal opened onClose={() => {}} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function declare() {
  fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "gpu1" } });
  fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu1" } });
}

describe("AddHostModal", () => {
  beforeEach(() => vi.clearAllMocks());

  it("probes with what the human declared", async () => {
    vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
    renderModal();
    declare();
    fireEvent.change(screen.getByLabelText(/^Programs allowed/), { target: { value: "p2, p5" } });
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    await waitFor(() => expect(api.probeHost).toHaveBeenCalled());
    expect(api.probeHost).toHaveBeenCalledWith({
      name: "gpu1", ssh: "gpu1", run_root: "~/coscience-runs", shared: false,
      programs: ["p2", "p5"], owner: "", notes: "" });
  });

  it("shows the checks and warnings, and confirms the adjusted offer", async () => {
    vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
    renderModal();
    declare();
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    expect(await screen.findByText(/rsync both ways/)).toBeTruthy();
    expect(screen.getByText(/glibc 2.17 is old/)).toBeTruthy();
    expect((screen.getByLabelText("CPU cores offered") as HTMLInputElement).value).toBe("12");
    fireEvent.change(screen.getByLabelText("CPU cores offered"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to the pool" }));
    await waitFor(() => expect(api.confirmHost).toHaveBeenCalled());
    expect(api.confirmHost).toHaveBeenCalledWith({ name: "gpu1", capacity: { cpu: 10, memory_gb: 55 } });
  });

  it("explains a failed probe and offers nothing to confirm", async () => {
    vi.mocked(api.probeHost).mockResolvedValue({
      ...OK_PROBE, ok: false, checks: [], warnings: [], proposal: {},
      error: "the server's host key is not known yet: connect once by hand (ssh gpu1), accept the key, then probe again",
    } as never);
    renderModal();
    declare();
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    expect(await screen.findByText(/connect once by hand/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/HostsCard.test.tsx src/components/AddHostModal.test.tsx`
Expected: FAIL — the components do not exist.

- [ ] **Step 3: Implement**

`frontend/src/api.ts` — add the types (near `Ledger`) and extend `Ledger`:

```ts
export interface LedgerCard { index: number; model: string; vram_gb: number | null; whole: boolean; shared_gb: number }
export interface LedgerHost {
  name: string; ssh: string; placeable: boolean; programs: string[]; run_root: string;
  capacity: Record<string, number>; available: Record<string, number>; gpus: LedgerCard[];
  shared?: boolean; owner?: string; notes?: string;
}
export interface HostCheck { name: string; ok: boolean; detail: string }
export interface HostDeclaration {
  ssh: string; run_root: string; shared: boolean; programs: string[]; owner: string; notes: string;
}
export interface HostProbe {
  name: string; declared: HostDeclaration; probed_at: number; ok: boolean; error: string;
  facts: Record<string, unknown>; checks: HostCheck[]; warnings: string[];
  proposal: { capacity?: Record<string, number>; gpus?: { model: string; vram_gb: number }[] };
}
```

and in `Ledger` add `hosts?: LedgerHost[]; host_errors?: string[];`. In the `api` object add:

```ts
  probeHost: (body: { name: string } & HostDeclaration) =>
    fetch("/api/hosts/probe", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(j<HostProbe>),
  listHostProbes: () => fetch("/api/hosts/probes").then(j<HostProbe[]>),
  confirmHost: (body: { name: string; capacity: Record<string, number> }) =>
    fetch("/api/hosts", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(j<Ledger>),
```

(Follow the file's existing `fetch(...).then(j<T>)` pattern and its helper name if it differs.)

Create `frontend/src/components/HostsCard.tsx`:

```tsx
import { Button, Card, Group, Table, Text } from "@mantine/core";
import { useState } from "react";
import type { LedgerHost } from "../api";
import AddHostModal from "./AddHostModal";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

/** What a server offers the pool, in words: cores, memory, then each card. */
export function hostOffer(host: LedgerHost): string {
  const parts: string[] = [];
  if (host.capacity.cpu) parts.push(`${host.capacity.cpu} CPU cores`);
  if (host.capacity.memory_gb) parts.push(`${host.capacity.memory_gb} GB memory`);
  if (host.gpus.length) {
    parts.push(host.gpus.map((g) => (g.vram_gb ? `${g.vram_gb} GB GPU` : "GPU (VRAM not declared)")).join(", "));
  }
  return parts.join(" · ") || "nothing declared";
}

export default function HostsCard({ hosts, errors }: { hosts: LedgerHost[]; errors: string[] }) {
  const [adding, setAdding] = useState(false);
  return (
    <Card padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" style={{ marginBottom: 12 }}>
        <div className="eyebrow">servers · {hosts.length}</div>
        <Button size="xs" variant="default" onClick={() => setAdding(true)}>Add server</Button>
      </Group>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Server</Table.Th><Table.Th>Reached by</Table.Th><Table.Th>Offers</Table.Th>
            <Table.Th>Programs</Table.Th><Table.Th>Status</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {hosts.map((h) => (
            <Table.Tr key={h.name}>
              <Table.Td className="mono">{h.name}</Table.Td>
              <Table.Td className="mono">{h.ssh || "this machine"}</Table.Td>
              <Table.Td>{hostOffer(h)}</Table.Td>
              <Table.Td>{h.programs.length ? h.programs.join(", ") : "all"}</Table.Td>
              <Table.Td>{h.placeable ? "takes work" : "waits for remote launch"}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {errors.map((e) => (
        <Text key={e} size="sm" c="red" style={{ marginTop: 8 }}>{e}</Text>
      ))}
      <AddHostModal opened={adding} onClose={() => setAdding(false)} />
    </Card>
  );
}
```

Create `frontend/src/components/AddHostModal.tsx`:

```tsx
import { Alert, Badge, Button, Group, Modal, NumberInput, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type HostProbe } from "../api";

interface Props { opened: boolean; onClose: () => void }
type Amount = number | "";
const DEFAULT_RUN_ROOT = "~/coscience-runs";

export default function AddHostModal({ opened, onClose }: Props) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [ssh, setSsh] = useState("");
  const [runRoot, setRunRoot] = useState(DEFAULT_RUN_ROOT);
  const [shared, setShared] = useState(false);
  const [programs, setPrograms] = useState("");
  const [owner, setOwner] = useState("");
  const [notes, setNotes] = useState("");
  const [probe, setProbe] = useState<HostProbe | null>(null);
  const [cpu, setCpu] = useState<Amount>("");
  const [memory, setMemory] = useState<Amount>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const wasOpened = useRef(false);

  // Start clean on each open, never on a background refresh while it is open.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      setName(""); setSsh(""); setRunRoot(DEFAULT_RUN_ROOT); setShared(false);
      setPrograms(""); setOwner(""); setNotes(""); setProbe(null);
      setCpu(""); setMemory(""); setError("");
    }
    wasOpened.current = opened;
  }, [opened]);

  const runProbe = async () => {
    setBusy(true); setError(""); setProbe(null);
    try {
      const result = await api.probeHost({
        name: name.trim(), ssh: ssh.trim(), run_root: runRoot.trim(), shared,
        programs: programs.split(",").map((p) => p.trim()).filter(Boolean),
        owner: owner.trim(), notes: notes.trim(),
      });
      setProbe(result);
      setCpu(result.proposal?.capacity?.cpu ?? "");
      setMemory(result.proposal?.capacity?.memory_gb ?? "");
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const confirm = async () => {
    setBusy(true); setError("");
    const capacity: Record<string, number> = {};
    if (cpu !== "" && cpu > 0) capacity.cpu = cpu;
    if (memory !== "" && memory > 0) capacity.memory_gb = memory;
    try {
      await api.confirmHost({ name: name.trim(), capacity });
      qc.invalidateQueries({ queryKey: ["ledger"] });
      onClose();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const cards = probe?.proposal?.gpus ?? [];
  return (
    <Modal opened={opened} onClose={onClose} title="Add a server" size="lg">
      <Stack>
        <TextInput label="Name" description="How the platform refers to it" value={name}
                   onChange={(e) => setName(e.currentTarget.value)} />
        <TextInput label="SSH target" description="An alias from ~/.ssh/config, user@host or user@host:port — key login only"
                   value={ssh} onChange={(e) => setSsh(e.currentTarget.value)} />
        <TextInput label="Run root" description="Where sprint work goes on the server; not inside a synced folder"
                   value={runRoot} onChange={(e) => setRunRoot(e.currentTarget.value)} />
        <Switch label="Shared with other people" checked={shared}
                onChange={(e) => setShared(e.currentTarget.checked)} />
        <TextInput label="Programs allowed" description="Comma-separated program ids; empty lets every program use it"
                   value={programs} onChange={(e) => setPrograms(e.currentTarget.value)} />
        <TextInput label="Owner or contact" value={owner} onChange={(e) => setOwner(e.currentTarget.value)} />
        <TextInput label="Notes" description="Usage rules, e.g. hours or longest job" value={notes}
                   onChange={(e) => setNotes(e.currentTarget.value)} />
        <Button onClick={runProbe} loading={busy && !probe} disabled={!name.trim() || !ssh.trim()}>Probe</Button>

        {probe && !probe.ok && <Alert color="red" title="Probe failed">{probe.error}</Alert>}

        {probe?.ok && (
          <>
            <Stack gap={4}>
              {probe.checks.map((c) => (
                <Group key={c.name} gap="xs">
                  <Badge color={c.ok ? "teal" : "red"}>{c.ok ? "ok" : "failed"}</Badge>
                  <Text size="sm">{c.name}{c.detail ? ` — ${c.detail}` : ""}</Text>
                </Group>
              ))}
            </Stack>
            {probe.warnings.map((w) => <Text key={w} size="sm" c="orange">{w}</Text>)}
            <NumberInput label="CPU cores offered" min={0} value={cpu}
                         onChange={(v) => setCpu(v === "" ? "" : Number(v))} />
            <NumberInput label="Memory offered (GB)" min={0} value={memory}
                         onChange={(v) => setMemory(v === "" ? "" : Number(v))} />
            <Text size="sm" c="dimmed">
              {cards.length ? `${cards.map((g) => `${g.vram_gb} GB ${g.model}`).join(", ")}. ` : "No GPUs found. "}
              A server added now waits for remote launch before it takes work.
            </Text>
            <Button onClick={confirm} loading={busy}>Add to the pool</Button>
          </>
        )}
        {error && <Text size="sm" c="red">{error}</Text>}
      </Stack>
    </Modal>
  );
}
```

`frontend/src/views/Ledger.tsx`: import `HostsCard` from `../components/HostsCard` and render, directly after the "capacity in use" card:

```tsx
      <HostsCard hosts={l.hosts ?? []} errors={l.host_errors ?? []} />
```

- [ ] **Step 4: Run the frontend tests and type check**

Run (from `frontend/`): `npx vitest run src/components/HostsCard.test.tsx src/components/AddHostModal.test.tsx src/views/Ledger.test.tsx` and `npx tsc -b`
Expected: PASS, and no type errors. (If a label query with a regex is needed because Mantine puts a description inside the label, use a regex anchored at the start of the label text, as the tests above do.)

---

### Task 4: Whole suites, docs, todo

Run by the controller.

- [ ] **Step 1:** full Python suite, full vitest suite, `npx tsc -b` → pass.
- [ ] **Step 2:** spec §5 gains: the probe is deterministic code, not an agent; probe records live in `.coscience/host-probes/`; an unknown host key fails with an instruction instead of being accepted.
- [ ] **Step 3:** move O5 to To QC, with a check a human runs against one real server.
