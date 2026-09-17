# O14 Choose Where Each Program May Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Which programs a server takes is chosen from a list of programs with an "All programs" switch instead of typed ids, the same permission is editable from a program's settings as one checkbox per server, and this machine can be restricted like any other server.

**Architecture:** A server's access is one of three shapes in `resources.yaml`: no key (every program), `programs: [...]` (only these), or `exclude_programs: [...]` (every program but these). The last shape is new; it is what lets a program be kept off a server without freezing the list of every other program, so programs created later still run there. This machine carries the same two keys at the top level, beside its amounts. The backend gains `Service.set_host_programs` (one server's access, including `local`) and `Service.set_program_hosts` (one program's access across every server, translated into edits of each server's lists). Every access write returns the sprints now pinned to a server that no longer takes their program. The frontend gets one `ProgramAccessInput` used by the server dialog in all three modes, and a Servers section in Program settings.

**Tech Stack:** Python 3 (FastAPI, PyYAML, pytest), React + Mantine + TanStack Query (vitest).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§4 Hosts: servers can be reserved per program); todo item O14.

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python -m pytest`, prefixed `PYTHONPATH=src` in the worktree (the project's pytest addopts already has `-q`; never add another). Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Tests never run ssh, rsync or the probe script for real.
- Absent keys mean every program, exactly as today. A file that never uses the new keys behaves exactly as before.
- `programs` and `exclude_programs` on the same server (or both at the top level) is a configuration error, never silently resolved.
- No write ever produces an empty `programs:` list (a hand-written `programs: []` still parses as every program, as today). Removing the last program from an "only these" list is refused with a message naming the way out; it never flips the server to every program.
- A malformed top-level access key is reported in `host_errors` (shown on Compute) and leaves this machine unrestricted, the same way a malformed top-level `gpus:` is handled.
- Editing access never touches leases or running work. Sprints pinned to a server that loses their program are reported back to the caller and keep O7's unrunnable message; nothing moves or stops them.
- Access edits do not reach another machine and are not behind `COSCIENCE_ALLOW_ONBOARDING`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/resources.py` | modify | `Host.exclude_programs`, `Host.allows`, `_parse_access`, top-level access for `local` |
| `src/coscience/service.py` | modify | `set_host_programs`, `set_program_hosts`, `_cut_off_pins`; `ledger_status`, `set_capacity`, `probe_host`, `confirm_host`, `update_host` carry `exclude_programs` |
| `src/coscience/http_api.py` | modify | `PUT /api/hosts/{name}/programs`, `PUT /api/programs/{program_id}/hosts`; `exclude_programs` on probe and update bodies |
| `tests/test_program_access.py` | create | parsing, `allows`, both writers, cut-off report, routes |
| `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` | modify | an "O14 as built" paragraph |
| `frontend/src/api.ts` | modify | types and `setHostPrograms`, `setProgramHosts` |
| `frontend/src/components/programAccess.ts` | create | `Access` type, converters, `accessLabel`, `hostAllows` |
| `frontend/src/components/ProgramAccessInput.tsx` | create | the "All programs" switch plus program multi-select |
| `frontend/src/components/AddHostModal.tsx` | modify | access input in add, edit and local modes |
| `frontend/src/components/HostsCard.tsx` | modify | Programs column uses `accessLabel` |
| `frontend/src/components/ProgramSettingsModal.tsx` | modify | Servers section |
| tests beside each frontend file | modify/create | the behaviour below |

---

### Task 1: The backend stores, reads and edits access from either side

**Files:**
- Modify: `src/coscience/resources.py` (`Host` ~line 43, `allows` ~68, `from_dict` ~101, `_parse_host` ~161)
- Modify: `src/coscience/service.py` (`ledger_status` hosts list ~1752, `set_capacity` ~1795, `probe_host` ~1977, `confirm_host` ~2037, `update_host` ~2054)
- Modify: `src/coscience/http_api.py` (`HostProbeIn` ~143, `HostUpdateIn` ~163, host routes ~943-995)
- Create: `tests/test_program_access.py`
- Modify: the spec (append the paragraph in Step 9)

**Interfaces:**
- Produces: `Host.exclude_programs: list[str]`; `Host.allows(program) -> bool`.
- Produces: `Service.set_host_programs(name: str, programs: list[str], exclude_programs: list[str]) -> dict` and `Service.set_program_hosts(program_id: str, hosts: list[str]) -> dict`. Both return `{**ledger_status(), "cut_off": [{"sprint_id": str, "host": str}, ...]}`. `update_host` returns the same shape and accepts `exclude_programs: list | None = None`.
- Produces: `ledger_status()["hosts"][i]["exclude_programs"]: list[str]` beside the existing `"programs"`.
- Produces routes: `PUT /api/hosts/{name}/programs` with body `{"programs": [...], "exclude_programs": [...]}` (both default `[]`); `PUT /api/programs/{program_id}/hosts` with body `{"hosts": [...]}`. 404 for an unknown server or program, 422 for any `ValueError`.
- Produces: `HostProbeIn.exclude_programs: list[str] = []`, `HostUpdateIn.exclude_programs: list[str] | None = None`; a probe's `declared` carries `exclude_programs`, and `confirm_host` writes it when non-empty.

- [ ] **Step 1: Write the failing tests** in `tests/test_program_access.py`. Follow the fixtures in `tests/test_host_config.py` (a `Service` over a tmp substrate with `.coscience/resources.yaml` written directly, and the FastAPI `TestClient` pattern for routes). Programs used by `set_program_hosts` must exist in the substrate: create them the way `tests/test_service_programs.py` does. Cases:

```python
from coscience.resources import ResourcePool

def pool(d):
    return ResourcePool.from_dict(d)

def test_absent_keys_admit_every_program():
    p = pool({"cpu": 4, "hosts": {"a": {"ssh": "a"}}})
    assert p.host("local").allows("p1") and p.host("a").allows("p1") and p.host("a").allows(None)

def test_only_list_admits_listed_programs():
    h = pool({"hosts": {"a": {"ssh": "a", "programs": ["p1"]}}}).host("a")
    assert h.allows("p1") and not h.allows("p2") and not h.allows(None)

def test_exclude_list_admits_everyone_else():
    h = pool({"hosts": {"a": {"ssh": "a", "exclude_programs": ["p4"]}}}).host("a")
    assert not h.allows("p4") and h.allows("p1") and h.allows("p-created-later") and h.allows(None)

def test_both_lists_on_a_server_is_an_error():
    p = pool({"hosts": {"a": {"ssh": "a", "programs": ["p1"], "exclude_programs": ["p4"]}}})
    assert p.host("a") is None
    assert any("programs and exclude_programs" in e for e in p.host_errors)

def test_local_takes_top_level_access():
    p = pool({"cpu": 4, "exclude_programs": ["p4"]})
    assert not p.host("local").allows("p4") and p.host("local").allows("p1")
    assert p.capacity["cpu"] == 4                     # access keys are not amounts

def test_local_access_inside_a_resources_wrapper():
    p = pool({"resources": {"cpu": 4, "programs": ["p1"]}})
    assert p.host("local").allows("p1") and not p.host("local").allows("p2")

def test_malformed_local_access_is_reported_and_leaves_local_open():
    p = pool({"cpu": 4, "programs": "p1"})
    assert p.host("local").allows("p2")
    assert any("programs" in e for e in p.host_errors)

def test_hand_written_empty_only_list_still_means_every_program():
    assert pool({"hosts": {"a": {"ssh": "a", "programs": []}}}).host("a").allows("p9")
```

Service and route cases (write them with the fixtures above; each asserts on the YAML read back from `.coscience/resources.yaml` and on the returned dict):

- `set_host_programs("a", ["p1"], [])` writes `programs: [p1]` and removes any `exclude_programs`; `set_host_programs("a", [], ["p4"])` writes `exclude_programs: [p4]` and removes `programs`; `set_host_programs("a", [], [])` removes both keys. `drain`, `drained_at`, `capacity`, `gpus`, `notes` on the entry are unchanged after each.
- `set_host_programs("local", [], ["p4"])` writes top-level `exclude_programs: [p4]`, keeps `cpu`, `gpus` and `hosts:`; with a `resources:` wrapper it writes inside the wrapper.
- `set_host_programs("a", ["p1"], ["p4"])` raises `ValueError`; an unknown server raises `NotFoundError`.
- `set_program_hosts("p4", ["a"])` with `local` unrestricted and `a` holding `programs: [p1]`: local gets `exclude_programs: [p4]`, `a` gets `programs: [p1, p4]`.
- `set_program_hosts("p4", ["local"])` with `a` holding `exclude_programs: [p2]`: `a` gets `exclude_programs: [p2, p4]`; local stays unrestricted (no key written).
- `set_program_hosts("p4", ["local", "a"])` with local holding `exclude_programs: [p4]`: the key is removed (not left as `[]`).
- `set_program_hosts("p4", [])` with `a` holding `programs: [p4]` raises `ValueError` whose message contains `only program` and the file is unchanged.
- `set_program_hosts("p4", ["nope"])` raises `ValueError` naming `nope`; an unknown program raises `NotFoundError`.
- `set_program_hosts` with nothing to change writes nothing and makes no substrate commit (compare the substrate's `git rev-parse HEAD` before and after).
- Cut-off report: a p4 sprint in status `running` whose progress has `host: a`; after `set_program_hosts("p4", ["local"])` the result's `cut_off == [{"sprint_id": <id>, "host": "a"}]`. A DONE p4 sprint pinned to `a` is not reported. `set_host_programs` and `update_host` report the same way.
- `update_host("a", exclude_programs=["p4"], programs=[])` writes `exclude_programs` and drops `programs`.
- `ledger_status()["hosts"]` carries `exclude_programs` for every host, `[]` when absent.
- `set_capacity({"cpu": 8})` on a file with top-level `exclude_programs: [p4]` keeps that key; `set_capacity({"programs": 1})` and `set_capacity({"exclude_programs": 1})` raise `ValueError` (reserved names).
- `probe_host(..., exclude_programs=["p4"])` records it in `declared`; `confirm_host` writes it (use the fake runner from `tests/host_probe_fakes.py` as `tests/test_host_onboarding.py` does).
- Routes: `PUT /api/hosts/a/programs` 200 with `cut_off` in the body; 422 for both lists; 404 for an unknown server. `PUT /api/programs/p4/hosts` 200; 422 for the only-program case; 404 for an unknown program. Neither route needs `COSCIENCE_ALLOW_ONBOARDING`.

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_program_access.py`
Expected: FAIL (no `exclude_programs`, no `set_host_programs`).

- [ ] **Step 3: The model and parser in `resources.py`**

```python
# on Host, beside `programs`
    programs: list[str] = field(default_factory=list)          # only these; [] = no "only" list
    exclude_programs: list[str] = field(default_factory=list)  # every program but these

    def allows(self, program: str | None) -> bool:
        if self.programs:
            return program is not None and program in self.programs
        return program not in self.exclude_programs
```

```python
ACCESS_KEYS = ("programs", "exclude_programs")


def _parse_access(where: str, spec: dict) -> tuple[list[str], list[str]]:
    """A server's program access: `programs` (only these), `exclude_programs` (all but
    these), or neither (every program). `where` prefixes messages, e.g. "hosts.a.". """
    lists = []
    for key in ACCESS_KEYS:
        raw = spec.get(key)
        if raw is None:
            lists.append([])
            continue
        if not isinstance(raw, list):
            raise ValueError(f"{where}{key}: must be a list of program ids")
        lists.append([str(p) for p in raw])
    programs, excluded = lists
    if programs and excluded:
        raise ValueError(f"{where}programs and exclude_programs can't both be set; "
                         "use one: only these programs, or every program but these")
    return programs, excluded
```

In `_parse_host`, replace the `programs = spec.get("programs") or []` block with `programs, excluded = _parse_access(f"hosts.{name}.", spec)` and pass `programs=programs, exclude_programs=excluded` to `Host(...)`.

In `from_dict`, pop both access keys out of `raw` before `flat` is built, the same way `gpus` is popped (and read from `d` when `raw is not d`, as `gpu_specs` does), then:

```python
        local_access = {k: v for k, v in access_specs.items() if v is not None}
        local_programs: list[str] = []
        local_excluded: list[str] = []
        if local_access:
            try:
                local_programs, local_excluded = _parse_access("", local_access)
            except ValueError as exc:
                host_errors.append(str(exc))       # reported on Compute; local stays open
        hosts = [Host(LOCAL, local_capacity, gpus=local_gpus,
                      programs=local_programs, exclude_programs=local_excluded)]
```

- [ ] **Step 4: Service readers**

In `ledger_status`'s host dict add `"exclude_programs": list(h.exclude_programs)` after `"programs"`.

In `set_capacity`: add `"programs"` and `"exclude_programs"` to the reserved-name checks (message: `f"'{key}' is reserved for program access and can't be a resource name"`), and when the file is read, carry each access key found at the top level (or inside the `resources:` wrapper) into `out` unchanged, the way `hosts` is carried.

In `probe_host`, accept `exclude_programs: list | None = None` and record `"exclude_programs": [str(p) for p in (exclude_programs or [])]` in `declared` beside `programs`. In `confirm_host`, add `"exclude_programs"` to the `for key in (...)` copy loop.

- [ ] **Step 5: The writers**

```python
    def _apply_access(self, holder: dict, programs: list, excluded: list) -> None:
        """Set one server's access keys on its YAML mapping (a `hosts:` entry, or the
        top level for this machine). Empty lists remove their key: no key is every
        program, and an empty `programs:` would read as every program too."""
        programs = [str(p) for p in programs]
        excluded = [str(p) for p in excluded]
        if programs and excluded:
            raise ValueError("programs and exclude_programs can't both be set; use one: "
                             "only these programs, or every program but these")
        for key, value in (("programs", programs), ("exclude_programs", excluded)):
            if value:
                holder[key] = value
            else:
                holder.pop(key, None)

    def _local_holder(self, loaded: dict) -> dict:
        """The mapping this machine's amounts live in: the `resources:` wrapper when
        the file uses one, else the top level."""
        wrapped = loaded.get("resources")
        return wrapped if isinstance(wrapped, dict) else loaded

    def _cut_off_pins(self) -> list[dict]:
        """Unfinished sprints pinned to a server that no longer takes their program.
        They keep their work where it is; O7's unrunnable message says what to do."""
        pool = self.pool                  # read live from resources.yaml, so it sees the write
        out = []
        for sprint in self.substrate.iter_sprints():
            if sprint.status in (SprintStatus.DONE, SprintStatus.CANCELED, SprintStatus.FAILED):
                continue
            pinned = self.substrate.load_progress(sprint.id).host
            host = pool.host(pinned) if pinned else None
            if host is not None and not host.allows(sprint.program):
                out.append({"sprint_id": sprint.id, "host": pinned})
        return out
```

Use whatever this service already calls to get the parsed pool (for example the ledger's `pool`, as `ledger_status` does). `_load_pool` above stands for that call; do not add a second YAML reader.

`set_host_programs(name, programs, exclude_programs)`: load with `self._resources_hosts()`; for `LOCAL`, apply to `self._local_holder(loaded)`, then check `ResourcePool.from_dict(loaded).host_errors` for an access error and raise it; for a remote name that is not in `hosts`, raise `NotFoundError(f"no server {name!r} in the pool")`; otherwise apply to a copy of the entry and validate with `_parse_host(name, entry)`. Write with `self._write_resources(loaded)`, commit `f"server {name} program access updated"`, return `{**self.ledger_status(), "cut_off": self._cut_off_pins()}`.

`set_program_hosts(program_id, hosts)`: raise `NotFoundError` if the program does not exist (use the substrate's program lookup, as the other program setters do). Parse the current pool; any name in `hosts` that is not a server in it raises `ValueError(f"no server named {name!r}")`. For every server in the pool, `want = name in hosts`, `has = host.allows(program_id)`; skip when equal. Otherwise edit that server's mapping (`_local_holder(loaded)` for local):

| server's current shape | `want` true (allow) | `want` false (disallow) |
|---|---|---|
| `programs: [...]` | append `program_id` | remove it; if the list would be empty raise `ValueError(f"{program_id} is the only program {name} takes; let another program use {name} first, or remove the server")` |
| `exclude_programs: [...]` | remove `program_id` (drop the key when empty) | append `program_id` |
| neither | cannot happen (already allowed) | set `exclude_programs: [program_id]` |

Validate every edit before writing anything (raise before the single `_write_resources`). If no server changed, return `{**self.ledger_status(), "cut_off": self._cut_off_pins()}` without writing or committing. Otherwise write once, commit `f"program {program_id} server access updated"`, and return the same shape.

`update_host`: add `exclude_programs: list | None = None`. Replace the `if programs is not None:` block with: when either `programs` or `exclude_programs` is not None, `self._apply_access(new_entry, programs or [], exclude_programs or [])`. Return `{**self.ledger_status(), "cut_off": self._cut_off_pins()}`.

- [ ] **Step 6: Routes in `http_api.py`**

```python
class HostProgramsIn(BaseModel):
    programs: list[str] = Field(default_factory=list)
    exclude_programs: list[str] = Field(default_factory=list)


class ProgramHostsIn(BaseModel):
    hosts: list[str] = Field(default_factory=list)
```

Add `exclude_programs: list[str] = Field(default_factory=list)` to `HostProbeIn` and `exclude_programs: list[str] | None = None` to `HostUpdateIn`. Add `PUT /hosts/{name}/programs` calling `service.set_host_programs(name, body.programs, body.exclude_programs)` and `PUT /programs/{program_id}/hosts` calling `service.set_program_hosts(program_id, body.hosts)`, each mapping `NotFoundError` to 404 and `ValueError` to 422 as `update_host`'s route does. Neither calls `_require_onboarding()`.

- [ ] **Step 7: Run the new tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_program_access.py`
Expected: PASS.

- [ ] **Step 8: Run the related suites**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_resources_hosts.py tests/test_host_config.py tests/test_host_onboarding.py tests/test_host_admin.py tests/test_remote_placement.py tests/test_service_capacity.py tests/test_ledger_hosts.py tests/test_http_api.py tests/test_pm_compute.py`
Expected: PASS. A test that asserted the old `update_host` return shape exactly may need `cut_off` added; change nothing else in existing tests.

- [ ] **Step 9: Spec paragraph.** Append to the spec, after the last "as built" paragraph:

```markdown
**O14 as built.** A server's program access has three shapes: no key (every program),
`programs:` (only these) and `exclude_programs:` (every program but these). The third
keeps a program off a server without freezing the list of every other program, so a
program created later still runs there. This machine takes the same two keys at the
top level of `resources.yaml`. Both are edited from the server dialog and from a
program's settings (one checkbox per server); both write the same keys. Removing the
last program from an "only these" list is refused rather than read as every program.
Sprints pinned to a server that loses their program are reported back to whoever made
the change and stay where they are.
```

---

### Task 2: The dashboard edits access from the server dialog and from Program settings

**Files:**
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/components/programAccess.ts`, `frontend/src/components/programAccess.test.ts`
- Create: `frontend/src/components/ProgramAccessInput.tsx`
- Modify: `frontend/src/components/AddHostModal.tsx`, `AddHostModal.test.tsx`
- Modify: `frontend/src/components/HostsCard.tsx`, `HostsCard.test.tsx`
- Modify: `frontend/src/components/ProgramSettingsModal.tsx`, `ProgramSettingsModal.test.tsx`

**Interfaces:**
- Consumes (Task 1): `LedgerHost.exclude_programs`; `PUT /api/hosts/{name}/programs` body `{programs, exclude_programs}`; `PUT /api/programs/{id}/hosts` body `{hosts}`; both return the ledger plus `cut_off: {sprint_id, host}[]`; `PUT /api/hosts/{name}` returns the same shape and accepts `exclude_programs`; the probe body accepts `exclude_programs`.
- Produces: `api.setHostPrograms(name: string, body: { programs: string[]; exclude_programs: string[] }): Promise<Ledger & { cut_off: CutOff[] }>`, `api.setProgramHosts(id: string, hosts: string[]): Promise<Ledger & { cut_off: CutOff[] }>`.

- [ ] **Step 1: Types and client in `api.ts`.** Add `exclude_programs: string[]` to `LedgerHost` and to `HostDeclaration`; `exclude_programs?: string[]` to `HostUpdate`; `export interface CutOff { sprint_id: string; host: string }`; the two methods above, following `updateHost`'s fetch/`j<...>` pattern. `updateHost` now resolves to `Ledger & { cut_off: CutOff[] }`.

- [ ] **Step 2: Pure helpers in `programAccess.ts`, test first.**

```ts
import type { CutOff, LedgerHost } from "../api";

/** The dialog's view of a server's access. `list` is the exceptions when `all`, the
 *  allowed programs otherwise. */
export interface Access { all: boolean; list: string[] }

export const accessFromHost = (h?: Pick<LedgerHost, "programs" | "exclude_programs">): Access =>
  h?.programs?.length ? { all: false, list: [...h.programs] }
    : { all: true, list: [...(h?.exclude_programs ?? [])] };

export const accessPayload = (a: Access) =>
  a.all ? { programs: [], exclude_programs: a.list } : { programs: a.list, exclude_programs: [] };

/** "Only these programs" with none picked would read as every program server-side. */
export const accessInvalid = (a: Access) => !a.all && a.list.length === 0;

export const sameAccess = (a: Access, b: Access) =>
  a.all === b.all && [...a.list].sort().join("\n") === [...b.list].sort().join("\n");

export const accessLabel = (h: Pick<LedgerHost, "programs" | "exclude_programs">) =>
  h.programs.length ? h.programs.join(", ")
    : h.exclude_programs?.length ? `all except ${h.exclude_programs.join(", ")}` : "all";

export const hostAllows = (h: Pick<LedgerHost, "programs" | "exclude_programs">, program: string) =>
  h.programs.length ? h.programs.includes(program) : !(h.exclude_programs ?? []).includes(program);

/** The one program an "only these" server takes — unchecking it would be refused. */
export const onlyProgram = (h: Pick<LedgerHost, "programs">, program: string) =>
  h.programs.length === 1 && h.programs[0] === program;

export const cutOffMessage = (cut: CutOff[]) =>
  cut.map((c) => `${c.sprint_id} is pinned to ${c.host === "local" ? "this machine" : c.host}`).join("; ")
  + ". It keeps its work there and waits until the program is allowed back on that server or the sprint is stopped.";
```

Tests (`programAccess.test.ts`) cover each export: the three host shapes through `accessFromHost`, `accessPayload` round trip, `accessInvalid`, `sameAccess` ignoring order, the three `accessLabel` strings (`"p1, p2"`, `"all except p4"`, `"all"`), `hostAllows` on all three shapes, `onlyProgram`, and `cutOffMessage` naming "this machine" for `local`.

- [ ] **Step 3: `ProgramAccessInput.tsx`.**

Props: `{ value: Access; onChange: (a: Access) => void; programs: { id: string; title?: string }[] }`. Renders a Mantine `Switch` labelled `All programs` (checked = `value.all`) and a `MultiSelect` below it:
- when `all`: label `Except`, placeholder `no exceptions`;
- otherwise: label `Only these programs`, and when `list` is empty an error text `Pick at least one program, or turn on All programs`.

The select's data is every program (value `id`, label `title ? \`${title} (${id})\` : id`) plus any id already in `value.list` that is not a known program (so a closed or hand-typed id still shows and can be removed). Flipping the switch clears the list (`onChange({ all: !value.all, list: [] })`): exceptions and an allow-list are different lists. Program rows come from the caller; use the query key the dashboard already uses for `api.listPrograms` (search `listPrograms` for the existing `useQuery`) so the cache is shared.

- [ ] **Step 4: Server dialog (`AddHostModal.tsx`).**

- Replace the `programs` text state with `access: Access`. It is seeded on open: `accessFromHost(host)` in edit mode; `accessFromHost(localHost)` in local mode, where the local row is what `HostsCard` passes as `host` (it does today: `host?.gpus`); `{ all: true, list: [] }` in add mode. Keep `initialAccess` in a ref for the local-mode change check.
- Replace the "Programs allowed" `TextInput` with `ProgramAccessInput` in add and edit modes, still wrapped by `declare(...)` so add mode keeps invalidating the probe on change. Add it to local mode too, below the card rows.
- The probe body sends `...accessPayload(access)` in place of `programs`. `submitEdit` sends `...accessPayload(access)`.
- `submitLocal`: after `setCapacity` resolves, if `!sameAccess(access, initialAccess.current)`, call `api.setHostPrograms("local", accessPayload(access))`.
- Block Probe (add), Add to the pool, and Update configuration while `accessInvalid(access)`.
- When the resolved `updateHost` or `setHostPrograms` result has a non-empty `cut_off`, show `notifications.show({ color: "yellow", title: "Pinned work is cut off", message: cutOffMessage(result.cut_off) })` before closing.

Tests (`AddHostModal.test.tsx`, following its existing mocks of `api`):
- edit mode on a host with `exclude_programs: ["p4"]` shows the switch on and `p4` selected; Update sends `programs: [], exclude_programs: ["p4"]`;
- turning All programs off with nothing picked disables Update and shows the error text;
- local mode with access unchanged does not call `setHostPrograms`; local mode after picking an exception calls it with `{ programs: [], exclude_programs: ["p4"] }` after `setCapacity`;
- add mode sends `exclude_programs` in the probe body;
- a `cut_off` in the update result raises the notification (mock `@mantine/notifications` as `ProgramSettingsModal.test.tsx` does, or assert on the rendered notification if the test harness mounts a `Notifications` provider).
- Update the existing test at line ~251 that sends `programs: ["p2", "p5"]` to drive the new input instead of typing ids.

- [ ] **Step 5: Servers card.** `HostsCard.tsx` line ~133 renders `accessLabel(h)`. Test: the three labels appear for three hosts of the three shapes (extend the fixtures with `exclude_programs`).

- [ ] **Step 6: Program settings (`ProgramSettingsModal.tsx`).**

Add a Servers section after Project folder:
- `Text` "Servers" (`size="sm" fw={500}`), dimmed `Text size="xs"` "Where this program's sprints may run."
- One `Checkbox` per `ledger.hosts` row, label `this machine` for `local` and the server name otherwise, `aria-label={\`may run on ${name}\`}`.
- A checkbox that is checked and `onlyProgram(h, program.id)` is disabled, with description `the only program this server takes`.
- When no box is checked, dimmed orange text: `No server takes this program's work; its sprints will wait.` Saving is still allowed.

Read the ledger with `useQuery({ queryKey: ["ledger"], queryFn: api.getLedger, enabled: opened })`. Seed the checked set once per open, the first time ledger data is present while open (a ref holding the seeded set, reset on close). This follows the existing rule of seeding on open only, so a background refetch never discards a click. In `save`, when the checked set differs from the seeded one, call `api.setProgramHosts(program.id, [...checked])`. Then invalidate `["ledger"]` with `useQueryClient`. When the result has a non-empty `cut_off`, the success notification is yellow and its message is `cutOffMessage(result.cut_off)`. A 422 lands in the existing "Couldn't save settings" notification.

Tests (`ProgramSettingsModal.test.tsx`):
- boxes are seeded from a mocked ledger with hosts of the three shapes;
- unchecking an allowed server and saving calls `setProgramHosts` with the remaining names;
- saving with no change does not call it;
- the only-program box is disabled;
- a `cut_off` result produces the yellow message.

- [ ] **Step 7: Run the frontend checks**

Run (from `frontend/`): `npx vitest run src/components/programAccess.test.ts src/components/AddHostModal.test.tsx src/components/HostsCard.test.tsx src/components/ProgramSettingsModal.test.tsx src/views/ProgramDetail.test.tsx src/views/Ledger.test.tsx` then `npx tsc -b`
Expected: PASS, no type errors.
