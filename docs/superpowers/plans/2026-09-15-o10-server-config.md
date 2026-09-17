# O10 Configure Each Server From Its Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every server card on Compute gets a Config button that opens the server dialog prefilled; a remote server can be re-probed and its configuration updated in place, and this machine gets a Detect step that reads its CPU, memory and GPUs so its capacity is declared from the hardware instead of hand-edited YAML.

**Architecture:** The backend gains `Service.update_host` (edit a remote host's entry in `resources.yaml`, keeping drain state; a new SSH target or run root needs a passing probe of those exact values), `host_probe.detect_local` + `Service.detect_local` (run the onboarding probe script locally with `bash -s`, no SSH, and return facts and a proposal without writing anything), and an optional `gpus` list on `set_capacity` so this machine's cards can be written with their VRAM. The frontend extends the existing `AddHostModal` into one dialog with three modes — add (as today), edit (a remote server), local (this machine) — and the servers card gets a Config button on every row. Programs stay a comma-separated field here; O14 replaces it.

**Tech Stack:** Python 3 (FastAPI, PyYAML, pytest), React + Mantine + TanStack Query (vitest).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§4 Hosts, §5 Onboarding); todo item O10.

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python`, prefixed `PYTHONPATH=src` in a worktree (the project's pytest addopts already has `-q`; never add another). Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Tests never run ssh, rsync, `nvidia-smi` or the probe script for real: they inject a runner.
- Re-probing a remote server stays behind `COSCIENCE_ALLOW_ONBOARDING` (it makes this backend ssh); editing values and detecting this machine do not reach another machine and are not gated.
- A new SSH target or run root is written only after a probe of exactly those values passed every check.
- Updating a server keeps its `drain` and `drained_at` and any key the dialog does not edit.
- Lowering capacity below what is leased is allowed and behaves like a drain (no new grants; running work keeps its lease); the dialog says so.
- Writing this machine's capacity keeps the platform keys (`workers`, `housekeepers`) and any other declared key the dialog does not show.
- Nothing changes for a deployment that never opens the dialog.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/host_probe.py` | modify | `detect_local(runner, now)` — the probe script run locally |
| `src/coscience/service.py` | modify | `update_host`, `detect_local`, `set_capacity(..., gpus=None)` |
| `src/coscience/http_api.py` | modify | `PUT /api/hosts/{name}`, `POST /api/hosts/local/detect`, `PUT /api/capacity` accepts `gpus` |
| `tests/test_host_config.py` | create | update, detect, capacity with cards, routes |
| `frontend/src/api.ts` | modify | `updateHost`, `detectLocal`, `setCapacity(capacity, gpus?)` |
| `frontend/src/components/AddHostModal.tsx` | modify | add / edit / local modes |
| `frontend/src/components/HostsCard.tsx` | modify | Config button per row |
| `frontend/src/components/AddHostModal.test.tsx`, `HostsCard.test.tsx` | modify | the new modes and button |

---

### Task 1: The backend updates a server and detects this machine

**Files:**
- Modify: `src/coscience/host_probe.py`, `src/coscience/service.py`, `src/coscience/http_api.py`
- Test: `tests/test_host_config.py` (create)

**Interfaces:**
- Consumes: `host_probe.PROBE_SCRIPT`, `parse_facts`, `propose`, `warnings_for`, `PROBE_TIMEOUT`, `subprocess_runner`, `DEFAULT_RUN_ROOT`; `Service._resources_hosts`, `_write_resources`, `_host_probe_path`, `ledger_status`; `resources._parse_host`, `ResourcePool`.
- Produces:
  - `host_probe.detect_local(runner=subprocess_runner, now=None) -> dict` → `{"ok", "error", "facts", "warnings", "proposal"}` (no checks; no SSH)
  - `Service.update_host(name, *, ssh=None, run_root=None, shared=None, programs=None, owner=None, notes=None, capacity=None, gpus=None, probed_at=None) -> dict` (ledger status); raises `NotFoundError` for an unknown or `local` name, `ValueError` for anything refused
  - `Service.detect_local(runner=None) -> dict`
  - `Service.set_capacity(capacity, gpus=None) -> dict` — `gpus` a list of `{model, vram_gb}` or `None` (keep what is there)
  - `PUT /api/hosts/{name}` body `{ssh?, run_root?, shared?, programs?, owner?, notes?, capacity?, gpus?, probed_at?}`; `POST /api/hosts/local/detect`; `PUT /api/capacity` body `{capacity, gpus?}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_host_config.py` (read `tests/test_host_onboarding.py` first for `FakeRunner` in `tests/host_probe_fakes.py`, `SAMPLE_OUTPUT`, and how a service is built):

```python
"""O10: update a server's configuration in place, and detect this machine's hardware."""
import pytest
import yaml

from coscience import host_probe
from coscience.service import NotFoundError, Service
from tests.host_probe_fakes import SAMPLE_OUTPUT, FakeRunner

POOL = ("cpu: 4\nworkers: 3\nhousekeepers: 2\nhosts:\n"
        "  big:\n    ssh: big\n    run_root: ~/runs\n    capacity: {cpu: 16, memory_gb: 64}\n"
        "    drain: true\n    drained_at: 5.0\n    owner: ops\n")


def _svc(tmp_path, text=POOL):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)
    return Service(tmp_path)


def _entry(tmp_path, name="big"):
    return yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["hosts"][name]


def test_values_update_in_place_and_drain_is_kept(tmp_path):
    svc = _svc(tmp_path)
    svc.update_host("big", capacity={"cpu": 8, "memory_gb": 32}, notes="nights only", shared=True,
                    programs=["p4"])
    e = _entry(tmp_path)
    assert e["capacity"] == {"cpu": 8.0, "memory_gb": 32.0}
    assert (e["notes"], e["shared"], e["programs"], e["owner"]) == ("nights only", True, ["p4"], "ops")
    assert (e["drain"], e["drained_at"], e["ssh"], e["run_root"]) == (True, 5.0, "big", "~/runs")


def test_a_new_ssh_target_needs_a_passing_probe_of_it(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="probe big with the new SSH target"):
        svc.update_host("big", ssh="big2")
    svc.probe_host(name="big", ssh="big2", run_root="~/runs", runner=FakeRunner({"alive": (1, "", "")}))
    with pytest.raises(ValueError, match="checks failed"):
        svc.update_host("big", ssh="big2")
    record = svc.probe_host(name="big", ssh="big2", run_root="~/runs", runner=FakeRunner())
    svc.update_host("big", ssh="big2", probed_at=record["probed_at"])
    assert _entry(tmp_path)["ssh"] == "big2"


def test_a_new_run_root_needs_a_probe_of_that_run_root(tmp_path):
    svc = _svc(tmp_path)
    svc.probe_host(name="big", ssh="big", run_root="~/runs", runner=FakeRunner())
    with pytest.raises(ValueError, match="probe big with the new run root"):
        svc.update_host("big", run_root="~/other")


def test_cards_are_replaced_or_removed(tmp_path):
    svc = _svc(tmp_path)
    svc.update_host("big", gpus=[{"model": "X", "vram_gb": 24}])
    assert _entry(tmp_path)["gpus"] == [{"model": "X", "vram_gb": 24.0}]
    svc.update_host("big", gpus=[])
    assert "gpus" not in _entry(tmp_path)


def test_unknown_local_and_invalid_updates_are_refused(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(NotFoundError):
        svc.update_host("nope", notes="x")
    with pytest.raises(NotFoundError):
        svc.update_host("local", notes="x")
    with pytest.raises(ValueError):
        svc.update_host("big", capacity={"cpu": -1})
    assert _entry(tmp_path)["capacity"] == {"cpu": 16, "memory_gb": 64}      # unchanged


def test_detect_local_runs_the_probe_script_without_ssh():
    runner = FakeRunner({"probe": (0, SAMPLE_OUTPUT, "")})
    result = host_probe.detect_local(runner=runner, now=1000.0)
    assert result["ok"] and result["facts"]["threads"] and "capacity" in result["proposal"]
    assert all("ssh" not in call[0] for call in runner.calls)
    assert runner.calls[0][:2] == ["bash", "-s"]


def test_this_machine_gets_cards_with_vram_and_keeps_platform_keys(tmp_path):
    svc = _svc(tmp_path, "cpu: 4\ngpu: 1\nworkers: 3\nhousekeepers: 2\n")
    svc.set_capacity({"cpu": 24, "workers": 3, "housekeepers": 2},
                     gpus=[{"model": "RTX", "vram_gb": 24}])
    data = yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())
    assert data["gpus"] == [{"model": "RTX", "vram_gb": 24.0}] and "gpu" not in data
    assert (data["workers"], data["housekeepers"], data["cpu"]) == (3.0, 2.0, 24.0)
    svc.set_capacity({"cpu": 24, "workers": 3, "housekeepers": 2})            # gpus=None keeps the cards
    assert yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())["gpus"]


def test_routes_update_and_detect(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app
    svc = _svc(tmp_path)
    monkeypatch.setattr(host_probe, "subprocess_runner", FakeRunner({"probe": (0, SAMPLE_OUTPUT, "")}))
    client = TestClient(build_app(svc))
    r = client.put("/api/hosts/big", json={"notes": "n"})
    assert r.status_code == 200 and _entry(tmp_path)["notes"] == "n"
    assert client.put("/api/hosts/nope", json={"notes": "n"}).status_code == 404
    assert client.put("/api/hosts/big", json={"capacity": {"cpu": -1}}).status_code == 422
    r = client.post("/api/hosts/local/detect")
    assert r.status_code == 200 and r.json()["ok"]
```

Read `tests/host_probe_fakes.py` before writing these: use its actual `FakeRunner` label for the probe script call (the brief calls it `"probe"` and a detached-job check `"alive"` — confirm and adjust the labels only). If `detect_local`'s runner call shape differs from `["bash", "-s"]` because of how `FakeRunner` matches, keep the assertion that no call starts with `ssh`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_config.py`
Expected: FAIL — missing `update_host`, `detect_local`, `set_capacity(gpus=)`, routes.

- [ ] **Step 3: Implement**

`src/coscience/host_probe.py`:

```python
def detect_local(runner: Runner = subprocess_runner, now: float | None = None) -> dict:
    """This machine's facts and a proposed capacity, from the same script the onboarding
    probe runs over SSH — run locally with bash, no SSH and no checks. Nothing is written."""
    now = time.time() if now is None else now
    code, out, err = runner(["bash", "-s"], f"RUN_ROOT={shlex.quote(DEFAULT_RUN_ROOT)}\n{PROBE_SCRIPT}",
                            PROBE_TIMEOUT)
    if code != 0:
        lines = [l for l in str(err).strip().splitlines() if l.strip()]
        return {"ok": False, "error": lines[-1] if lines else f"probe script exited {code}",
                "facts": {}, "warnings": [], "proposal": {}}
    facts = parse_facts(out, now)
    return {"ok": True, "error": "", "facts": facts,
            "warnings": [w for w in warnings_for(facts, [], shared=False) if "GPU" in w or "nvidia" in w],
            "proposal": propose(facts, shared=False)}
```

`src/coscience/service.py`:

1. `set_capacity(self, capacity: dict, gpus: list | None = None)`: when `gpus` is a list, validate it with `ResourcePool.from_dict({"gpus": gpus})` (no `host_errors`, else `ValueError` naming the first error); write `out["gpus"] = gpus` (floats for `vram_gb`) and drop `gpu`; an empty list removes `gpus` (and keeps whatever `gpu` count the payload carries). `None` keeps today's behaviour (the existing `gpus` preserved from the file).
2. `detect_local(self, runner=None) -> dict`: `return host_probe.detect_local(runner=runner or host_probe.subprocess_runner)`.
3. `update_host(...)`:
   - `name == LOCAL` or not in `hosts` → `NotFoundError(f"no remote host {name!r} in the pool")`; an entry that is not a mapping → `ValueError` (as O7's M8a).
   - `new_ssh = ssh if ssh is not None else entry["ssh"]`, `new_root = run_root if run_root is not None else entry.get("run_root", DEFAULT_RUN_ROOT)`. When either differs from the entry: load the probe record for `name` (`_host_probe_path`); refuse unless it exists, `record["declared"]["ssh"] == new_ssh` and `record["declared"]["run_root"] == new_root` (`f"probe {name} with the new SSH target first"` when ssh differs, `f"probe {name} with the new run root first"` when only the run root differs), `record["ok"]`, every check passed (`f"checks failed on {name}: …"` — same wording as `confirm_host`), and `probed_at` (when given) matches the record within 1e-6.
   - Build the new entry from a copy of the old one: set `ssh`, `run_root`; set `shared`/`owner`/`notes` when given (drop falsy `owner`/`notes`/`shared` keys the way `confirm_host` omits them); `programs` when given (list of str; empty removes the key); `capacity` when given (floats); `gpus` when given (list → set; `[]` → remove the key). Keep every other key (`drain`, `drained_at`, anything unknown).
   - `_parse_host(name, new_entry)` (raises `ValueError`), assign, `_write_resources(loaded)`, commit `f"host {name} configuration updated"`, return `self.ledger_status()`.

`src/coscience/http_api.py`: `class HostUpdateIn(BaseModel)` with all optional fields (`ssh: str | None = None`, `run_root: str | None = None`, `shared: bool | None = None`, `programs: list[str] | None = None`, `owner: str | None = None`, `notes: str | None = None`, `capacity: dict[str, float] | None = None`, `gpus: list[dict] | None = None`, `probed_at: float | None = None`); `PUT /hosts/{name}` → `service.update_host(name, **body.model_dump())`, 404/422 mapping; `POST /hosts/local/detect` → `service.detect_local()` (declare it before `/hosts/{name}` routes if FastAPI would otherwise match `local` as a name). `CapacityUpdate` gains `gpus: list[dict] | None = None` and the route passes it.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_config.py tests/test_host_onboarding.py tests/test_host_probe.py tests/test_service_capacity.py tests/test_host_admin.py tests/test_http_api.py`
Expected: PASS

---

### Task 2: The dialog edits a server and this machine; every card has Config

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/components/AddHostModal.tsx`, `frontend/src/components/HostsCard.tsx`
- Test: `frontend/src/components/AddHostModal.test.tsx`, `frontend/src/components/HostsCard.test.tsx`

**Interfaces:**
- Consumes: Task 1's routes; `LedgerHost` (`name, ssh, run_root, programs, shared, owner, notes, capacity, gpus[{index, model, vram_gb}], drain`), `Ledger.local_capacity`.
- Produces: `api.updateHost(name, body)`, `api.detectLocal()`, `api.setCapacity(capacity, gpus?)`; `AddHostModal({ opened, onClose, host?, local?, localCapacity? })`.

- [ ] **Step 1: Write the failing tests**

Read both test files and extend them with the existing mocking pattern (`vi.mock("../api", …)`), adding `updateHost`, `detectLocal` to the mock.

`AddHostModal.test.tsx` — add:

1. **Edit mode prefills and updates values without a probe:** render with `host={REMOTE}` (ssh `gpu1`, run_root `~/coscience-runs`, programs `["p2"]`, notes, capacity `{cpu: 10, memory_gb: 50}`, one card 10.8 GB). The title reads "Configure gpu1"; Name is shown read-only; SSH target, run root, programs, owner, notes, CPU cores, memory and the card's VRAM are prefilled. Change notes and CPU cores, click **Update configuration** → `api.updateHost("gpu1", expect.objectContaining({ notes: "…", capacity: { cpu: 12, memory_gb: 50 } }))` and `onClose`.
2. **A changed SSH target needs a probe:** change SSH target to `gpu2` → **Update configuration** is disabled and "Probe the new SSH target before updating" is shown; after a successful probe (mocked `probeHost` with passing checks) it is enabled and sends `ssh: "gpu2"` and `probed_at`.
3. **Re-probe shows checks and facts in edit mode** and a probe error (e.g. the 403 onboarding-off message) is shown as the error text.
4. **Lowering below use says so:** with `host.used = {cpu: 8}`, setting CPU cores to 4 shows "8 CPU cores in use — lowering below that lets running work finish and blocks new grants."
5. **Local mode:** render with `local` and `localCapacity={cpu: 24, gpu: 1, workers: 3, housekeepers: 2}` and `host={LOCAL}` (card `vram_gb: null`). Title "Configure this machine"; no SSH/run root/probe fields; **Detect** calls `api.detectLocal` and fills CPU cores, memory and one card `NVIDIA GeForce RTX 4090` 24 GB from the mocked proposal; **Update configuration** calls `api.setCapacity({ cpu: 32, memory_gb: 56, workers: 3, housekeepers: 2 }, [{ model: "NVIDIA GeForce RTX 4090", vram_gb: 24 }])` — the platform keys come through untouched and `gpu` is not sent when cards are listed.
6. **Add mode is unchanged:** the existing add tests still pass.

`HostsCard.test.tsx` — add: every row (local and remote) has a **Config** button labelled `Configure <name>`; clicking the remote one opens the dialog titled "Configure gpu1", the local one "Configure this machine". `HostsCard` receives `localCapacity` (pass `l.local_capacity ?? l.capacity` from `Ledger.tsx`).

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/AddHostModal.test.tsx src/components/HostsCard.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `api.ts`: `updateHost: (name, body) => fetch(\`/api/hosts/${encodeURIComponent(name)}\`, { method: "PUT", … }).then(j<Ledger>)`; `detectLocal: () => fetch("/api/hosts/local/detect", { method: "POST" }).then(j<HostProbe>)` (the response has `ok, error, facts, warnings, proposal`; `checks` absent — make the type tolerate that or add a `LocalDetect` type); `setCapacity(capacity, gpus?)` sends `{ capacity, gpus }` only when `gpus` is given. `LedgerHost` gains `owner`, `notes` if missing and `used`.
- `AddHostModal.tsx`:
  - Props `host?: LedgerHost; local?: boolean; localCapacity?: Record<string, number>`. Mode = `local ? "local" : host ? "edit" : "add"`.
  - On open, seed fields from `host` in edit and local modes (name, ssh, run_root, shared, `programs.join(", ")`, owner, notes, CPU cores from `capacity.cpu`, memory from `capacity.memory_gb`, cards from `host.gpus` as editable `{model, vram_gb}` rows) — same open-transition guard as today.
  - Card rows: model text + VRAM (GB) number + ✕ remove; an "+ add card" link. Detect / probe results replace the rows with the proposal's cards.
  - Edit mode: Name read-only; the declared-field `edit` wrapper keeps invalidating a probe, but no longer clears CPU/memory/cards (those are the server's current values); **Re-probe** (same call as Probe); **Update configuration** enabled unless (SSH target or run root differs from `host`) and no passing probe of the current values is shown — then disabled with "Probe the new SSH target before updating". It sends `updateHost(name, { ssh, run_root, shared, programs, owner, notes, capacity, gpus, probed_at? })` with `capacity` built like `confirm` (cpu, memory_gb when > 0, plus the host's other capacity keys unchanged) and `gpus` from the rows (`[]` when all removed).
  - Local mode: hide name, SSH, run root, shared, programs, owner, notes and the probe; show CPU cores, Memory (GB, optional) and card rows; **Detect** calls `detectLocal` and fills them from `proposal` (`capacity.cpu`, `capacity.memory_gb`, `gpus`), showing `warnings` and an error; **Update configuration** calls `setCapacity({ ...localCapacity without cpu/memory_gb/gpu, cpu, memory_gb? }, rows)`.
  - In edit and local modes, when a lowered value is below `host.used` for cpu or memory, show the "in use — lowering below that lets running work finish and blocks new grants" line (the wording `CapacityModal` uses).
  - Titles: "Add a server", "Configure <name>", "Configure this machine". Invalidate `["ledger"]` on success.
- `HostsCard.tsx`: prop `localCapacity`; a **Config** button (`aria-label="Configure <name>"`) first in the actions cell for every row (local included); one dialog instance whose `host`/`local` follow the clicked row. `Ledger.tsx` passes `localCapacity`.

- [ ] **Step 4: Run the tests**

Run (from `frontend/`): `npx vitest run src/components/AddHostModal.test.tsx src/components/HostsCard.test.tsx src/views/Ledger.test.tsx`, then `npx vitest run` and `npx tsc -b`.
Expected: PASS, tsc clean.

---

## Self-review notes

- Todo O10: Config on every card ✓; dialog prefilled with Re-probe and Update configuration ✓; edits SSH target, run folder, programs, shared, owner, notes, capacity and cards via the host parser, keeping drain ✓; new SSH target needs a passing probe ✓; lowering below leased behaves like a drain and says so ✓; this machine's Detect reads GPU/CPU (and memory) locally ✓; re-probe gated, edits not ✓.
- Not here: replacing the separate capacity editor (it still owns `workers`/`housekeepers` and custom keys; O12 reviews the page), the programs picker (O14), one-button removal (O15), agent survey (O11). Declaring memory on this machine through Detect makes `memory_gb` requestable here; the default reservation for sprints that ask none is O16.
