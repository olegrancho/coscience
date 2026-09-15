# O4 — A Sprint Request with the Shape of Real Compute Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a sprint's compute request say what real work needs — CPU cores, memory, whole GPUs or VRAM shares, and whether it may span hosts — and have the PM, the edit dialog and the sprint page all speak that shape.

**Architecture:** The stored request stays the flat `resources_required` map (no migration of existing sprints, HTTP, MCP or PM payloads); its shape is made explicit through four canonical keys — `cpu`, `memory_gb`, `gpu`, `gpu_vram_gb` — which placement already honours (memory is charged like cpu, GPUs per card from O3), and a new sprint-level `distributed: bool`. `distributed` is stored, editable and shown, but placement stays single-host until O6 builds cross-host placement. The PM's COMPUTE block becomes a per-host view with the one-host rule and the key vocabulary; the edit dialog gains compute fields; the sprint page and the Overview cost line read the keys in words.

**Tech Stack:** Python 3.12, pytest; React + Mantine + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§4 Hosts and GPU paragraph, §10 row O4, §11)

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python`, prefixed `PYTHONPATH=src` in a worktree. Frontend: run from `frontend/` with `npx vitest run …` and `npx tsc -b`.
- Canonical request keys are exactly `cpu` (cores), `memory_gb`, `gpu` (whole cards) and `gpu_vram_gb` (VRAM per card; shared cards; alone = one card). Any other key stays allowed and is shown as `name amount`.
- `distributed` defaults to `false`, is written to `sprint.md` only when `true`, and does not change placement in O4.
- A request's stored form stays `resources_required: {name: number}`; nothing rewrites existing sprints.
- The PM prompt keeps every existing sizing rule (reserve only what the heaviest step uses; a request no host can hold waits forever).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/models.py` | modify | `Sprint.distributed` |
| `src/coscience/substrate.py` | modify | read/write `distributed` |
| `src/coscience/service.py` | modify | submit/edit/list/get carry `distributed` |
| `src/coscience/http_api.py` | modify | `SprintSubmit.distributed`, `SprintPatch.distributed` |
| `src/coscience/mcp_server.py` | modify | `submit_sprint(distributed=)` |
| `src/coscience/pm_reasoner.py` | modify | `PMContext.compute_hosts`, `ProposedSprint.distributed` |
| `src/coscience/pm_agent.py` | modify | per-host `_compute`; proposals and sprint edits carry `distributed` |
| `src/coscience/pm_claude.py` | modify | per-host COMPUTE block; schema and parsing of `distributed` |
| `frontend/src/api.ts` | modify | `distributed` on `Sprint`, `SprintRow`, `SprintPatch`, submit body |
| `frontend/src/components/ui.tsx` | modify | `describeCompute`; `computeCost` reads a VRAM share as a card |
| `frontend/src/components/SprintEditModal.tsx` | modify | compute fields |
| `frontend/src/views/SprintDetail.tsx` | modify | compute card in words |
| `tests/test_request_shape.py` | create | `distributed` persistence and service |
| `tests/test_pm_compute.py` | modify | per-host COMPUTE and `distributed` |
| `frontend/src/components/ui.test.tsx` | modify | `describeCompute`, `computeCost` |
| `frontend/src/components/SprintEditModal.test.tsx` | create | compute fields |

---

### Task 1: A sprint records whether its work may span hosts

**Files:**
- Modify: `src/coscience/models.py`, `src/coscience/substrate.py`, `src/coscience/service.py`, `src/coscience/http_api.py`, `src/coscience/mcp_server.py`
- Test: `tests/test_request_shape.py` (create); `tests/test_http_api.py`, `tests/test_mcp_server.py` (append one test each)

**Interfaces:**
- Produces: `Sprint.distributed: bool = False`; `Service.submit_sprint(..., distributed=False)`; `Service.edit_sprint(..., distributed=None)`; `"distributed"` in `get_sprint` and each `list_sprints` row; `POST /api/sprints` and `PATCH /api/sprints/{id}` accept `distributed`; MCP `submit_sprint(distributed=)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_request_shape.py`:

```python
"""O4: a sprint's request records whether its work may span hosts."""
from coscience.models import Sprint, SprintStatus
from coscience.service import Service


def test_distributed_defaults_off_and_is_not_written(substrate):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"]))
    assert substrate.load_sprint("s1").distributed is False
    assert "distributed" not in (substrate.sprint_dir("s1") / "sprint.md").read_text()


def test_distributed_round_trips(substrate):
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"],
                                 distributed=True))
    assert substrate.load_sprint("s1").distributed is True


def test_submit_edit_and_read_carry_distributed(tmp_path):
    svc = Service(tmp_path)
    svc.submit_sprint(id="s1", goals="g", plan=["a"], resources_required={"cpu": 64},
                      distributed=True)
    assert svc.get_sprint("s1")["distributed"] is True
    assert [row["distributed"] for row in svc.list_sprints()] == [True]
    svc.edit_sprint("s1", distributed=False)
    assert svc.get_sprint("s1")["distributed"] is False
```

Append to `tests/test_http_api.py` (it already has a `client` fixture):

```python
def test_submit_and_patch_accept_distributed(client):
    r = client.post("/api/sprints", json={"id": "span1", "goals": "g", "plan": ["a"],
                                           "distributed": True})
    assert r.status_code < 300
    assert client.get("/api/sprints/span1").json()["distributed"] is True
    r = client.patch("/api/sprints/span1", json={"distributed": False})
    assert r.status_code == 200 and r.json()["distributed"] is False
```

Append to `tests/test_mcp_server.py` (it already has `server` and `call`):

```python
def test_submit_sprint_accepts_distributed(server):
    status = call(server, "submit_sprint", {"id": "span1", "goals": "g", "plan": ["a"],
                                            "distributed": True})
    assert status["distributed"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_request_shape.py tests/test_http_api.py::test_submit_and_patch_accept_distributed tests/test_mcp_server.py::test_submit_sprint_accepts_distributed -q`
Expected: FAIL — `TypeError: Sprint.__init__() got an unexpected keyword argument 'distributed'` and missing `distributed` keys.

- [ ] **Step 3: Implement**

`src/coscience/models.py` — in `Sprint`, after `preemptible`:

```python
    distributed: bool = False           # may its work span hosts? recorded now; placed across hosts from O6
```

`src/coscience/substrate.py` — in `load_sprint`, after the `preemptible=...` argument:

```python
            distributed=bool(fm.get("distributed", False)),
```

and in `save_sprint`, after the `if not sprint.preemptible:` block:

```python
        if sprint.distributed:
            fm["distributed"] = True
```

`src/coscience/service.py`:
- `submit_sprint` gains a keyword `distributed: bool = False` (next to `preemptible`) and passes `distributed=bool(distributed)` to `Sprint(...)`.
- `edit_sprint` gains `distributed=None`; after the `preemptible` handling add:

```python
        if distributed is not None:
            sprint.distributed = bool(distributed)
```

- Add `"distributed": sprint.distributed,` next to `"resources_required"` in both the `list_sprints` row dict and the `get_sprint` dict.

`src/coscience/http_api.py`:
- `SprintSubmit`: add `distributed: bool = False` after `preemptible`.
- `SprintPatch`: add `distributed: bool | None = None` after `preemptible`.
- In the `submit_sprint` route's `service.submit_sprint(...)` call add `distributed=body.distributed,` after `preemptible=body.preemptible,`.

`src/coscience/mcp_server.py` — `submit_sprint` tool gains `distributed: bool = False` after `resources_required`, passed through as `distributed=distributed`.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_request_shape.py tests/test_http_api.py tests/test_mcp_server.py tests/test_service_integration.py tests/test_substrate.py -q`
Expected: PASS (if a listed file does not exist, drop it from the command and say so).

---

### Task 2: The PM sees each host and can ask for the shape

**Files:**
- Modify: `src/coscience/pm_reasoner.py`, `src/coscience/pm_agent.py`, `src/coscience/pm_claude.py`
- Test: `tests/test_pm_compute.py` (modify + append)

**Interfaces:**
- Consumes: `Sprint.distributed` (Task 1); `Host.gpus`, `GPU_KEY`, `PLATFORM_KEYS`, `Ledger.used(host)` (O2/O3).
- Produces: `PMContext.compute_hosts: list[dict]` — each `{"name": str, "capacity": {key: amount} (no gpu), "gpus": [vram_gb or None per card], "held": {key: amount}}`; `_compute(substrate, program_id) -> (capacity, leased, hosts)`; `ProposedSprint.distributed: bool`; proposals and `sprint_edits` apply `distributed`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_pm_compute.py`:

1. Change the import line to `from coscience.pm_claude import parse_response, render_compute, render_prompt`.
2. In `test_the_context_carries_capacity_and_what_is_leased_without_platform_keys`, add at the end:

```python
    assert ctx.compute_hosts == [
        {"name": "local", "capacity": {"cpu": 24.0}, "gpus": [None], "held": {"cpu": 24.0}}]
```

3. Replace `test_the_prompt_states_the_pool_and_the_sizing_rule` with:

```python
def test_the_prompt_states_each_host_and_the_sizing_rule():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {"cpu": 24.0, "memory_gb": 62.0}, "gpus": [24.0, None],
         "held": {"cpu": 8.0, "gpu": 1.0}}])
    block = render_compute(ctx)
    assert ("- local: cpu 24, memory_gb 62; 2 GPU card(s): 24 GB, VRAM not declared"
            " — running sprints hold cpu 8, gpu 1") in block
    assert "A sprint runs on ONE host" in block
    assert "`gpu_vram_gb`" in block
    assert "Request only what the work actually needs" in block
    assert block in render_prompt(ctx)
```

4. In `test_a_program_is_told_only_the_hosts_it_may_use`, add:

```python
    assert [h["name"] for h in p2.compute_hosts] == ["local", "remote1"]
    assert [h["name"] for h in p5.compute_hosts] == ["local"]
```

5. Append:

```python
def test_undeclared_cards_are_named_as_whole_cards_only():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {"cpu": 4.0}, "gpus": [None], "held": {}}])
    assert "1 GPU card(s), VRAM not declared (whole cards only)" in render_compute(ctx)


def test_a_pool_with_nothing_declared_gets_the_plain_sizing_rule():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {}, "gpus": [], "held": {}}])
    assert render_compute(ctx).startswith("COMPUTE: no capacity is declared")


def test_a_proposal_may_ask_to_span_hosts():
    out = parse_response('{"proposals": [{"suffix": "x", "goals": "g", "plan": ["a"],'
                         ' "distributed": true}]}')
    assert out.proposals[0].distributed is True
    out = parse_response('{"proposals": [{"suffix": "y", "goals": "g", "plan": ["a"]}]}')
    assert out.proposals[0].distributed is False
```

6. Add two behaviour tests modelled on the existing ones: one in the style of `tests/test_pm_beat.py` (around the proposal whose `resources_required` carries prose) asserting that a proposal with `"distributed": true` creates a sprint whose `load_sprint(...).distributed is True`; one in the style of `tests/test_pm_resources_edit.py` asserting that a `sprint_edits` entry `{"sprint_id": ..., "distributed": true}` sets it on an editable sprint and that a non-boolean value (`"yes"`) leaves it unchanged. Put both at the end of `tests/test_pm_compute.py`, reusing the fixtures and helpers those files use.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_pm_compute.py -q`
Expected: FAIL — `PMContext` has no `compute_hosts`; `ProposedSprint` has no `distributed`.

- [ ] **Step 3: Implement**

`src/coscience/pm_reasoner.py`:
- `PMContext`: after `compute_leased`, add

```python
    # The same pool per host this program may be placed on: [{name, capacity, gpus, held}].
    compute_hosts: list[dict] = field(default_factory=list)
```

- `ProposedSprint`: after `resources_required`, add `distributed: bool = False`.

`src/coscience/pm_agent.py`:
- Replace `_compute` with:

```python
def _compute(substrate, program_id: str) -> tuple[dict, dict, list[dict]]:
    """(capacity, currently leased, per-host view) of what a sprint in this program can
    request — only the hosts it may be placed on, so a reserved machine is never
    planned around by a program that will not get it. The per-host view is what the PM
    sizes against: a request must fit on one host."""
    from coscience.ledger import Ledger
    from coscience.resources import GPU_KEY, PLATFORM_KEYS, load_pool
    pool = load_pool(substrate.repo_root)
    hosts = pool.placeable_hosts(program_id)
    capacity: dict[str, float] = {}
    for h in hosts:
        for k, v in h.capacity.items():
            capacity[k] = capacity.get(k, 0.0) + v
    try:
        ledger = Ledger(pool, substrate.repo_root / ".coscience" / "leases.json")
        ledger.load()
    except (OSError, ValueError, TypeError, KeyError):
        ledger = None
    leased: dict[str, float] = {}
    per_host: list[dict] = []
    for h in hosts:
        held = ({k: v for k, v in ledger.used(h.name).items() if k not in PLATFORM_KEYS and v}
                if ledger is not None else {})
        for k, v in held.items():
            leased[k] = leased.get(k, 0.0) + v
        per_host.append({"name": h.name,
                         "capacity": {k: v for k, v in h.capacity.items() if k != GPU_KEY},
                         "gpus": [g.vram_gb for g in h.gpus],
                         "held": held})
    leased = {k: v for k, v in leased.items() if k in capacity and v}
    return capacity, leased, per_host
```

- In `gather_context`: `capacity, leased, hosts = _compute(substrate, program_id)` and pass `compute_hosts=hosts` to `PMContext(...)`.
- In the proposal save (`substrate.save_sprint(Sprint(... resources_required=coerce_resources(prop.resources_required),`) add `distributed=bool(prop.distributed),`.
- In `_context_payload`'s proposal dict add `"distributed": p.distributed,` after `"resources_required"`.
- In the sprint-edits loop, after the `resources_required` edit:

```python
        if isinstance(edit.get("distributed"), bool):
            sp.distributed = edit["distributed"]
```

`src/coscience/pm_claude.py`:
- `parse_response`: add `distributed=p.get("distributed") is True,` to `ProposedSprint(...)`.
- Proposal schema line `"priority": <int>, "resources_required": {{}} or null,` becomes `"priority": <int>, "resources_required": {{}} or null, "distributed": false,`.
- `sprint_edits` schema line `"resources_required": {{}} or null,` becomes `"resources_required": {{}} or null, "distributed": <true|false, optional>,`.
- In the sizing paragraph, change `(e.g. {{"cpu": 1}} or {{"gpu": 2}})` to `(e.g. {{"cpu": 4, "memory_gb": 16}}, {{"gpu": 1}} or {{"gpu_vram_gb": 12}})`, and `never above the\nCOMPUTE totals (see COMPUTE above)` to `never above what ONE host in COMPUTE holds (see COMPUTE above)`. Keep the rest of the paragraph.
- Replace `render_compute` with:

```python
def _cards(vrams: list) -> str:
    if not vrams:
        return ""
    if all(v is None for v in vrams):
        return f"{len(vrams)} GPU card(s), VRAM not declared (whole cards only)"
    return f"{len(vrams)} GPU card(s): " + ", ".join(
        "VRAM not declared" if v is None else f"{v:g} GB" for v in vrams)


def render_compute(context: PMContext) -> str:
    """The hosts a proposal's `resources_required` draws on, and the rule for sizing it.

    Over-asking is the failure this exists for: on 09-13 the PM gave four sprints
    `cpu: 24` against a capacity of 16, so they could never start; once capacity
    was raised, one of them reserved all 24 CPUs while using under 2% of one. Hosts
    are listed one by one because a request must fit on one of them — a summed total
    would invite requests no single machine can hold."""
    hosts = [h for h in context.compute_hosts if h.get("capacity") or h.get("gpus")]
    if not hosts:
        return ("COMPUTE: no capacity is declared for this environment. Request only what a "
                "sprint's heaviest step actually uses at once.")
    lines = []
    for h in hosts:
        parts = [p for p in (_amounts(h["capacity"]) if h["capacity"] else "", _cards(h["gpus"])) if p]
        lines.append(f"- {h['name']}: {'; '.join(parts)} — running sprints hold {_amounts(h['held'])}")
    return ("COMPUTE: the hosts this program's sprints can be placed on:\n" + "\n".join(lines) + """
A sprint runs on ONE host: every amount in its resources_required must fit on a single host
above (`distributed` records that the work could span hosts, but work is not yet split across
them). Use these keys: `cpu` (cores), `memory_gb`, `gpu` (whole cards — nothing else runs on
them) and `gpu_vram_gb` (VRAM per card — the cards are shared with other work; alone it means
one card). A sprint's resources_required is RESERVED for as long as the sprint runs — other
sprints cannot use it, even while this one sits idle between steps. Request only what the work
actually needs: the cores, memory or VRAM its heaviest step uses at once, not the size of the
machine. A request no single host can hold can never be granted, and the sprint waits forever.""")
```

- [ ] **Step 4: Run the PM tests**

Run: `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_pm_compute.py tests/test_pm_claude.py tests/test_pm_beat.py tests/test_pm_resources_edit.py tests/test_pm_staging.py tests/test_pm_claude_e2e.py -q`
Expected: PASS

---

### Task 3: The dialog, the sprint page and the cost line read the shape

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/components/ui.tsx`, `frontend/src/components/SprintEditModal.tsx`, `frontend/src/views/SprintDetail.tsx`
- Test: `frontend/src/components/ui.test.tsx` (append), `frontend/src/components/SprintEditModal.test.tsx` (create)

**Interfaces:**
- Consumes: `distributed` on sprint payloads and `PATCH` (Task 1).
- Produces: `describeCompute(resources: Record<string, number>, distributed?: boolean): string[]` in `components/ui.tsx`.

**Setup note:** a worktree has no `frontend/node_modules`; the controller links it before dispatch. Run frontend commands from `frontend/`.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/ui.test.tsx` (merge the import with the file's existing `./ui` import):

```tsx
import { computeCost, describeCompute } from "./ui";

describe("describeCompute", () => {
  it("reads the canonical keys in words", () => {
    expect(describeCompute({ cpu: 8, memory_gb: 32, gpu: 2, gpu_vram_gb: 16 }, false))
      .toEqual(["8 CPU cores", "32 GB memory", "2 GPUs × 16 GB VRAM each (shared)", "one host"]);
  });
  it("names whole cards, other resources and a request that may span hosts", () => {
    expect(describeCompute({ gpu: 1, tpu: 2 }, true)).toEqual(["1 whole GPU", "tpu 2", "may span hosts"]);
  });
  it("reads a bare VRAM share as one card", () => {
    expect(describeCompute({ gpu_vram_gb: 8 })).toEqual(["1 GPU × 8 GB VRAM each (shared)", "one host"]);
  });
  it("is empty for an empty request", () => {
    expect(describeCompute({})).toEqual([]);
  });
});

describe("computeCost with a VRAM share", () => {
  it("counts the share as one card, not as a resource of its own", () => {
    expect(computeCost({ gpu_vram_gb: 8 }, { gpu: 1 }).text).toBe("1 of 1 gpu");
  });
});
```

Create `frontend/src/components/SprintEditModal.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({ api: { editSprint: vi.fn().mockResolvedValue({}) } }));

import { api, type Sprint } from "../api";
import SprintEditModal from "./SprintEditModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function sprint(over: Partial<Sprint> = {}): Sprint {
  return {
    id: "s1", status: "proposed", title: "", summary: "", goals: "g", priority: 0,
    preemptible: true, resources_required: { cpu: 4, tpu: 2 }, distributed: false,
    rationale: "", plan: ["a"], program: "p1", results: [], threads: [],
    agent_running: false, started_at: null, error: "", lease: null, model: "m",
    activity: null, votes: { up: 0, down: 0, mine: 0 }, ...over,
  } as Sprint;
}

function renderModal(s: Sprint) {
  return render(
    <MantineProvider>
      <SprintEditModal sprint={s} opened onClose={() => {}} onDone={() => {}} />
    </MantineProvider>,
  );
}

describe("SprintEditModal compute fields", () => {
  beforeEach(() => vi.clearAllMocks());

  it("pre-fills the compute fields from the request", () => {
    renderModal(sprint({ resources_required: { cpu: 4, gpu: 1, gpu_vram_gb: 12 } }));
    expect((screen.getByLabelText("CPU cores") as HTMLInputElement).value).toBe("4");
    expect((screen.getByLabelText("GPUs") as HTMLInputElement).value).toBe("1");
    expect((screen.getByLabelText("VRAM per GPU (GB)") as HTMLInputElement).value).toBe("12");
  });

  it("sends the reshaped request and keeps other resources", async () => {
    renderModal(sprint());
    fireEvent.change(screen.getByLabelText("Memory (GB)"), { target: { value: "16" } });
    fireEvent.click(screen.getByLabelText("May split across hosts"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.editSprint).toHaveBeenCalled());
    expect(api.editSprint).toHaveBeenCalledWith(
      "s1", { resources_required: { cpu: 4, memory_gb: 16, tpu: 2 }, distributed: true });
  });

  it("sends nothing about compute when nothing changed", async () => {
    renderModal(sprint());
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.editSprint).toHaveBeenCalled());
    expect(api.editSprint).toHaveBeenCalledWith("s1", {});
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/ui.test.tsx src/components/SprintEditModal.test.tsx`
Expected: FAIL — `describeCompute` is not exported; the compute fields do not exist.

- [ ] **Step 3: Implement**

`frontend/src/api.ts`:
- `SprintRow`: add `distributed?: boolean;` after `resources_required`.
- `Sprint`: add `distributed: boolean;` after `resources_required`.
- `SprintPatch`: add `distributed?: boolean;`.
- `submitSprint` body type: add `distributed?: boolean;` after `resources_required`.

`frontend/src/components/ui.tsx`:
- In `computeCost`, read a VRAM share as a card before anything else:

```tsx
export function computeCost(resources: Record<string, number>, capacity: Record<string, number>) {
  // A VRAM share is a card that others may also use: judge it as one card of the pool,
  // never as a resource of its own that capacity does not list.
  const req: Record<string, number> = { ...(resources ?? {}) };
  if (req.gpu_vram_gb && !req.gpu) req.gpu = 1;
  delete req.gpu_vram_gb;
  const keys = Object.keys(req);
```

  and use `req` instead of `resources` in the rest of the function.

- After `computeCost`, add:

```tsx
const COMPUTE_KEYS = ["cpu", "memory_gb", "gpu", "gpu_vram_gb"];

/** A request in words, one line per part: cores, memory, whole cards or VRAM shares,
 *  any other resource as "name amount", then whether it may span hosts. */
export function describeCompute(resources: Record<string, number>, distributed = false): string[] {
  const r = resources ?? {};
  const lines: string[] = [];
  if (r.cpu) lines.push(`${num(r.cpu)} CPU ${r.cpu === 1 ? "core" : "cores"}`);
  if (r.memory_gb) lines.push(`${num(r.memory_gb)} GB memory`);
  const vram = r.gpu_vram_gb ?? 0;
  const cards = r.gpu ? Math.ceil(r.gpu) : (vram > 0 ? 1 : 0);
  if (cards && vram > 0) lines.push(`${cards} ${cards === 1 ? "GPU" : "GPUs"} × ${num(vram)} GB VRAM each (shared)`);
  else if (cards) lines.push(`${cards} whole ${cards === 1 ? "GPU" : "GPUs"}`);
  for (const [k, v] of Object.entries(r)) {
    if (!COMPUTE_KEYS.includes(k)) lines.push(`${k} ${num(v)}`);
  }
  if (lines.length) lines.push(distributed ? "may span hosts" : "one host");
  return lines;
}
```

`frontend/src/components/SprintEditModal.tsx` — keep every existing field and behaviour; add the compute fields (enabled by `f.resources`) and send only what changed:

```tsx
const COMPUTE_KEYS = ["cpu", "memory_gb", "gpu", "gpu_vram_gb"];
type Amount = number | "";
const sortKeys = (o: Record<string, number>) =>
  JSON.stringify(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));
```

inside the component, beside the existing state:

```tsx
  const r = sprint.resources_required ?? {};
  const [cpu, setCpu] = useState<Amount>(r.cpu ?? "");
  const [memory, setMemory] = useState<Amount>(r.memory_gb ?? "");
  const [gpus, setGpus] = useState<Amount>(r.gpu ?? "");
  const [vram, setVram] = useState<Amount>(r.gpu_vram_gb ?? "");
  const [distributed, setDistributed] = useState<boolean>(sprint.distributed ?? false);
  const amount = (set: (v: Amount) => void) => (v: string | number) => set(v === "" ? "" : Number(v));

  const requestFromFields = (): Record<string, number> => {
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(r)) if (!COMPUTE_KEYS.includes(k)) out[k] = v;
    if (cpu !== "" && cpu > 0) out.cpu = cpu;
    if (memory !== "" && memory > 0) out.memory_gb = memory;
    if (gpus !== "" && gpus > 0) out.gpu = gpus;
    if (vram !== "" && vram > 0) out.gpu_vram_gb = vram;
    return out;
  };
```

in `save`, before the `try`:

```tsx
    if (f.resources) {
      const next = requestFromFields();
      if (sortKeys(next) !== sortKeys(r)) patch.resources_required = next;
      if (distributed !== (sprint.distributed ?? false)) patch.distributed = distributed;
    }
```

and in the JSX, after the Preemptible switch:

```tsx
        <NumberInput label="CPU cores" min={0} value={cpu} disabled={!f.resources}
                     onChange={amount(setCpu)} />
        <NumberInput label="Memory (GB)" min={0} value={memory} disabled={!f.resources}
                     onChange={amount(setMemory)} />
        <NumberInput label="GPUs" min={0} value={gpus} disabled={!f.resources}
                     onChange={amount(setGpus)} />
        <NumberInput label="VRAM per GPU (GB)" min={0} value={vram} disabled={!f.resources}
                     description="Empty takes whole cards; a value shares cards with other work"
                     onChange={amount(setVram)} />
        <Switch label="May split across hosts" checked={distributed} disabled={!f.resources}
                onChange={(e) => setDistributed(e.currentTarget.checked)} />
```

`frontend/src/views/SprintDetail.tsx`:
- Import `describeCompute` from `../components/ui` (add it to the existing import from that module).
- Replace `const resources = Object.entries(s.resources_required);` with `const compute = describeCompute(s.resources_required, s.distributed);`.
- In the compute card, replace the `resources.length ? (...) : (...)` block with:

```tsx
          {compute.length ? (
            <Stack gap={6}>
              {compute.map((line) => (
                <Text key={line} size="sm" className="mono">{line}</Text>
              ))}
            </Stack>
          ) : <Text size="sm" c="dimmed">Minimal — no reserved resources.</Text>}
```

- [ ] **Step 4: Run the frontend tests and type check**

Run (from `frontend/`): `npx vitest run src/components/ui.test.tsx src/components/SprintEditModal.test.tsx src/views/SprintDetail.test.tsx src/views/Overview.test.tsx` (drop a file that does not exist) and `npx tsc -b`.
Expected: PASS, and no type errors.

---

### Task 4: Whole suites, docs, todo

Run by the controller.

- [ ] **Step 1:** full Python suite (`PYTHONPATH=src … -m pytest -q`) and full frontend suite (`npx vitest run`) plus `npx tsc -b` → all pass.
- [ ] **Step 2:** spec §4 gains one sentence on the canonical request keys and `distributed`; §11 marks the "PM told summed capacity" O6 precondition and the O4 PM-sizing note as done.
- [ ] **Step 3:** move O4 to To QC.
