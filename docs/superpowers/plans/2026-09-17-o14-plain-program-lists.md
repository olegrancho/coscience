# O14 (rework) Plain Program Lists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A server stores one plain list: the programs it runs. A program's page ticks the servers it may run on. The two are transposes of each other, nothing in either view is ever disabled, and there is no "all programs" state and no exclusion list.

**Architecture:** `hosts.<name>.programs` (and the same key at the top level for this machine) becomes the whole model:
- key present — exactly those programs, and an empty list means the server takes none;
- key absent — the server's list has never been set, so it admits every program.

The absent case exists only so a deployment that never opens the dialog keeps placing work exactly as before. The dialogs never write it: a server with no list shows every program ticked, and the first save writes the list out. `exclude_programs` is removed everywhere (no server uses it). Creating a program asks which servers it may run on, defaulting to all, and writes the program into their lists right then — that is what replaces the old "all programs" toggle.

**Tech Stack:** Python 3 (FastAPI, PyYAML, pytest), React + Mantine + TanStack Query (vitest).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` §4 (servers can be reserved per program) and its "O14 as built" paragraph, which this plan replaces; todo item O14, returned to To Do after QC.

**Why the rework (Oleg, 2026-09-17):** the shipped design had three shapes (all / only these / all but these) and refused to untick a program that was a server's only one, which showed up as a disabled checkbox with no way forward. One plain list per server, with "select all" in the dropdown, does the same job without the concepts.

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python -m pytest`, prefixed `PYTHONPATH=src` when run from a worktree (addopts already has `-q`; never add another). Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Tests never run ssh, rsync or the probe script for real.
- `exclude_programs` is removed from the model, the writers, the API, the dashboard, the tests and the docs. A file that still carries the key is a configuration error for that server, named in `host_errors`, not silently honoured.
- A server whose `programs` key is absent admits every program. A server whose key is present admits exactly that list, and an empty list means none.
- No UI control is ever disabled because of what a list currently holds. Unticking the last program is allowed and leaves the server taking nothing, with a warning shown.
- Every write goes through the existing `resources.pool_file_lock` read-modify-write.
- Editing access never touches leases or running work; the existing `cut_off` report stays.
- Nothing in the live pool file is edited by this work.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/resources.py` | modify | `Host.programs: list[str] | None`; `allows`; parse `programs`; reject `exclude_programs`; drop `_parse_access`'s two-list handling |
| `src/coscience/service.py` | modify | `set_host_programs(name, programs)`; `set_program_hosts` reifies absent lists; `create_program(..., hosts)`; `ledger_status` drops `exclude_programs`; `update_host`/`confirm_host`/`probe_host` drop it |
| `src/coscience/http_api.py` | modify | `HostProgramsIn {programs}`; program-create body gains `hosts`; drop `exclude_programs` fields |
| `tests/test_program_access.py` | modify | rewritten for the one-list model |
| `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md`, `CLAUDE.md` | modify | replace the O14 as-built paragraph |
| `frontend/src/components/programAccess.ts` (+ test) | modify | one list; drop `Access`, `accessPayload`, `accessInvalid`, `onlyProgram` |
| `frontend/src/components/ProgramAccessInput.tsx` (+ test) | modify | plain multi-select with select-all / clear |
| `frontend/src/components/AddHostModal.tsx`, `HostsCard.tsx`, `ProgramSettingsModal.tsx` (+ tests) | modify | use it; no disabled checkbox |
| the program-create form (find `api.createProgram`'s caller) (+ test) | modify | server picker, all ticked by default |
| `frontend/src/api.ts` | modify | types and `createProgram` body |

---

### Task 1: One list per server, in the model, the writers and the API

**Files:** `src/coscience/resources.py`, `src/coscience/service.py`, `src/coscience/http_api.py`, `tests/test_program_access.py`, the spec and `CLAUDE.md`.

**Interfaces:**
- Produces: `Host.programs: list[str] | None = None` — `None` means the key is absent (admits every program), a list means exactly those. `Host.allows(program)` returns `True` when `programs is None`, else `program is not None and program in self.programs`.
- Produces: `Service.set_host_programs(name: str, programs: list[str]) -> dict` — writes the key always, including an empty list; returns `{**ledger_status(), "cut_off": [...]}` as today.
- Produces: `Service.set_program_hosts(program_id: str, hosts: list[str]) -> dict` — for every server in the pool: when its list is absent, reify it to every current program id first; then add or remove `program_id` so the server admits it exactly when its name is in `hosts`. One write, one commit, same return shape. No refusals beyond an unknown server or program.
- Produces: `Service.create_program(..., hosts: list[str] | None = None)` — after the program is created, for every server in the pool whose name is NOT in `hosts`, reify an absent list to every program id (which now includes this one) and remove this program from it; a server named in `hosts` with an explicit list gains the program. `None` means every server, and then nothing is written. Find the real signature of the existing create method and add the parameter to it.
- Produces: `ledger_status()["hosts"][i]["programs"]: list[str] | None`.
- Produces: `HostProgramsIn {programs: list[str]}`; the program-create body gains `hosts: list[str] | None = None`; `exclude_programs` is gone from `HostProbeIn`, `HostConfirmIn`, `HostUpdateIn` and from `update_host`, `confirm_host`, `probe_host`.

- [ ] **Step 1: Rewrite `tests/test_program_access.py`** for the new model, keeping the fixtures. Cases:
  - parsing: absent key → `allows("p1")` is True and `programs is None`; `programs: [p1]` → only p1, and `allows(None)` is False; `programs: []` → `allows("p1")` is False; `exclude_programs: [p4]` present → that server is dropped with a `host_errors` entry naming the key as no longer supported; top-level `programs: [p1]` restricts this machine; a top-level `programs: []` gives this machine nothing; malformed (`programs: "p1"`) is reported and leaves the server admitting everything.
  - `set_host_programs("a", ["p1"])` writes the list; `set_host_programs("a", [])` writes an empty list (the key stays, and the server then admits nothing); both keep `drain`, `capacity`, `gpus`, `notes`; an unknown server is `NotFoundError`; a no-op writes and commits nothing.
  - `set_host_programs("local", ["p1"])` writes the top-level key, inside a `resources:` wrapper when the file uses one, and keeps `cpu`, `gpus` and `hosts:`.
  - `set_program_hosts("p4", ["a"])` on a pool where `local` has no list and `a` has `[p1]`: `local` becomes every program except p4, `a` becomes `[p1, p4]`.
  - `set_program_hosts("p4", [])` on a server whose list is `[p4]`: the list becomes empty and the write succeeds — the old refusal is gone.
  - `set_program_hosts` reports `cut_off` for a sprint pinned to a server that loses its program, as before.
  - `create_program(..., hosts=["local"])` with servers `local` and `a`: `a`'s list loses the new program (reified from absent if needed), `local` keeps admitting it; `hosts=None` writes nothing to the pool.
  - routes: `PUT /api/hosts/{name}/programs` with `{"programs": [...]}`; the create route accepts `hosts`; no route accepts `exclude_programs` any more.

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: `resources.py`.** Change `Host.programs` to `list[str] | None = None`, update `allows`, parse the key as: absent → `None`; a list → `[str(p) for p in value]`; anything else → `ValueError`. Reject `exclude_programs` with `ValueError(f"{where}exclude_programs is no longer supported; list the programs the server runs under programs:")`. Remove the two-list logic from `_parse_access` (keep one helper if it still earns its place). Update the top-level parse for this machine the same way, reporting errors in `host_errors` and leaving `None` on error.

- [ ] **Step 4: `service.py`.** Rewrite `_apply_access` as a one-list write that always sets the key. Update `set_host_programs`, `set_program_hosts` (with the reify rule above) and the `cut_off` helper if it reads the old shape. Add the `hosts` parameter to the program-create method. Drop `exclude_programs` from `probe_host`'s `declared`, from `confirm_host`'s copy loop and from `update_host`. Keep every write inside `pool_file_lock`.

- [ ] **Step 5: `http_api.py`.** `HostProgramsIn` carries only `programs`. Add `hosts` to the program-create body. Remove `exclude_programs` from the three host bodies.

- [ ] **Step 6: Docs.** Replace the spec's "O14 as built" paragraph with:

```markdown
**O14 as built (reworked 2026-09-17).** A server stores one list: `programs:`, the programs
it runs. An empty list means it takes none; a server whose key has never been set admits
every program, and the dialog shows it with all of them ticked so the first save makes the
list explicit. There is no "all programs" state and no exclusion list. A program's settings
tick the servers it may run on, and creating a program asks which servers it may use,
defaulting to all; both write the same per-server list. Nothing in either view is disabled:
unticking a server's last program leaves it taking nothing, and the card says so.
```

  Check `CLAUDE.md` for any sentence describing program access and bring it in line.

- [ ] **Step 7: Run** `tests/test_program_access.py`, then `tests/test_resources_hosts.py tests/test_host_config.py tests/test_host_admin.py tests/test_host_removal.py tests/test_host_onboarding.py tests/test_remote_placement.py tests/test_service_capacity.py tests/test_service_programs.py tests/test_pm_compute.py tests/test_http_api.py`, then the full suite. Expected: PASS.

---

### Task 2: One dropdown on the server, one checklist on the program, and a server picker when a program is created

**Files:** `frontend/src/api.ts`, `frontend/src/components/programAccess.ts` (+ test), `ProgramAccessInput.tsx` (+ test), `AddHostModal.tsx` (+ test), `HostsCard.tsx` (+ test), `ProgramSettingsModal.tsx` (+ test), and the program-create form (+ test).

**Interfaces:**
- Consumes (Task 1): `LedgerHost.programs: string[] | null`; `PUT /api/hosts/{name}/programs` body `{programs}`; `PUT /api/programs/{id}/hosts` body `{hosts}`; the create-program body's `hosts`; no `exclude_programs` anywhere.
- Produces in `programAccess.ts`: `hostAllows(h, program)` (`h.programs === null || h.programs.includes(program)`), `accessLabel(h)` (`"all"` when null, `"none"` when empty, else the ids joined), `programsForEdit(h, allIds)` (`h.programs ?? allIds` — what the dialog shows ticked). `Access`, `accessPayload`, `accessInvalid`, `sameAccess` and `onlyProgram` are deleted along with their tests.

- [ ] **Step 1: `api.ts`.** `programs: string[] | null` on `LedgerHost`; drop `exclude_programs` from every type and fixture; `setHostPrograms(name, programs: string[])`; `createProgram`'s body gains `hosts?: string[]`.

- [ ] **Step 2: `ProgramAccessInput.tsx`, test first.** Props `{ value: string[]; onChange: (v: string[]) => void; programs: { id: string; title?: string }[] }`. It renders one `MultiSelect` labelled `Programs this server runs`, with `select all` and `clear` as small links beside the label, and the description `Empty means this server takes no work.` The data is every program plus any id already in `value` that is no longer a program. No switch, no error state, never disabled. Tests: seeding, select all, clear, an unknown id still removable.

- [ ] **Step 3: Server dialog and card.**
  - `AddHostModal` keeps one `string[]` state. In edit and local modes it seeds with `programsForEdit(host, allProgramIds)`, so a server with no list opens with every program ticked. In add mode it seeds with every program id. It sends `programs` in the probe body, in `confirmHost`, in `updateHost` and (local mode) through `setHostPrograms` when the list changed.
  - No submit is ever blocked by the program list.
  - `HostsCard` uses the new `accessLabel`.
  - Tests: edit mode on a server with no list shows all ticked and Update sends them all; unticking everything sends `[]`; the card reads `all`, `none` and `p1, p2` for the three cases.

- [ ] **Step 4: `ProgramSettingsModal`.** The Servers section keeps one checkbox per server, seeded with `hostAllows`. Nothing is disabled and no box carries a "the only program this server takes" description. Keep the warning when no server is ticked. Tests: unticking the server whose list is exactly this program saves `setProgramHosts` without that server and shows no disabled control.

- [ ] **Step 5: Program creation.** Find the form that calls `api.createProgram` (search `createProgram` under `frontend/src`). Add a `Servers` field below the existing fields: one checkbox per ledger server, all ticked by default, with the hint `Where this program's sprints may run. You can change this later in the program's settings.` Send `hosts` with the create call. When the ledger has only this machine, the field still shows it. Tests: default all ticked, unticking one sends the rest, and the created program appears as before.

- [ ] **Step 6: Run** the touched test files, then `npx vitest run`, then `npx tsc -b`. Expected: PASS.
