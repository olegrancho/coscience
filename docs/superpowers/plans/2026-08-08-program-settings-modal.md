# Program Settings Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a ⚙ Program settings modal gathering planner model, project folder, max proposed experiments, and standing instructions — and make the proposal cap a per-program setting instead of a global constant.

**Architecture:** `Program` gains `max_proposed` (0 = unset → the global `MAX_PROPOSED` of 4), persisted in `program.md` frontmatter like `pm_model`/`workdir`. Both places that read the cap — the PM prompt context and the apply-side enforcement — go through one `program_cap()` helper. The frontend adds a modal that seeds on open and posts only changed fields to the existing per-field endpoints; every inline control stays exactly where it is.

**Tech Stack:** Python 3 / FastAPI / pytest; React + Mantine + TanStack Query / vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-08-program-settings-modal-design.md`

## Global Constraints

- Never commit or push without explicit approval from the user — do the `git add`/`git commit` steps only if the user has approved committing; otherwise leave the work staged-but-uncommitted and say so.
- Runtime is Linux-only. Backend tests: `python3 -m pytest`. Frontend tests: `npm test` from `frontend/`.
- `max_proposed` accepts `0` (clear the override) or `1..20`. Negative or `>20` → 422.
- The inline header controls and the instructions card in `ProgramDetail.tsx` are **not** moved or removed by any task in this plan.
- Model IDs are exact strings, never date-suffixed: the new option is `claude-opus-4-8`.

---

### Task 1: Per-program cap on the model and in the substrate

**Files:**
- Modify: `src/coscience/models.py:195-206` (the `Program` dataclass)
- Modify: `src/coscience/substrate.py:288-308` (`load_program` / `save_program`)
- Test: `tests/test_program_max_proposed.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Program.max_proposed: int` (0 = unset), round-tripped through
  `Substrate.save_program` / `Substrate.load_program`. Task 2 and Task 3 read it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_program_max_proposed.py`:

```python
from coscience.frontmatter_io import parse
from coscience.models import Program
from coscience.substrate import Substrate


def test_max_proposed_defaults_to_unset(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x"))
    assert sub.load_program("p1").max_proposed == 0


def test_max_proposed_round_trips(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x", max_proposed=7))
    assert sub.load_program("p1").max_proposed == 7


def test_unset_max_proposed_writes_no_frontmatter_key(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="A", goals="x"))
    fm, _ = parse((sub.program_dir("p1") / "program.md").read_text())
    assert "max_proposed" not in fm
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_program_max_proposed.py -v`
Expected: FAIL — `TypeError: Program.__init__() got an unexpected keyword argument 'max_proposed'` (and an `AttributeError` on the first test).

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/models.py`, add the field to `Program` (after `workdir`):

```python
    workdir: str = ""                  # project folder this program's agents run in; "" = control repo
    max_proposed: int = 0              # cap on sprints awaiting review; 0 = use the global default
```

In `src/coscience/substrate.py`, `load_program` — add to the `Program(...)` construction:

```python
            workdir=str(fm.get("workdir", "")),
            max_proposed=int(fm.get("max_proposed", 0)),
```

and in `save_program`, beside the other optional keys:

```python
        if program.workdir:
            fm["workdir"] = program.workdir
        if program.max_proposed:
            fm["max_proposed"] = program.max_proposed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_program_max_proposed.py tests/test_program_substrate.py -v`
Expected: PASS (the existing `test_save_then_load_program` equality check still holds, because the new field defaults to 0 on both sides).

- [ ] **Step 5: Commit** *(only with the user's approval — see Global Constraints)*

```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_program_max_proposed.py
git commit -m "feat(models): per-program max_proposed, persisted when set"
```

---

### Task 2: The PM honours the per-program cap

**Files:**
- Modify: `src/coscience/pm_agent.py:20` (add the helper below `MAX_PROPOSED`), `:245` (`gather_context`), `:546` (apply-side enforcement)
- Test: `tests/test_program_max_proposed.py` (append)

**Interfaces:**
- Consumes: `Program.max_proposed` from Task 1.
- Produces: `pm_agent.program_cap(program) -> int`. Nothing later in this plan
  calls it; it exists so the two consumers can't drift.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_program_max_proposed.py`:

```python
from coscience.models import Sprint, SprintStatus
from coscience.pm_agent import MAX_PROPOSED, gather_context, pm_beat, write_staging
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint


def _prop(suffix):
    return ProposedSprint(suffix=suffix, goals="do " + suffix, plan=["step"])


def test_context_uses_the_global_default_when_unset(substrate):
    substrate.save_program(Program(id="p1", title="A", goals="x"))
    assert gather_context(substrate, "p1").max_proposed == MAX_PROPOSED


def test_context_uses_the_program_cap_when_set(substrate):
    substrate.save_program(Program(id="p1", title="A", goals="x", max_proposed=2))
    ctx = gather_context(substrate, "p1")
    assert ctx.max_proposed == 2 and ctx.free_slots == 2


def test_apply_enforces_the_program_cap(substrate):
    substrate.save_program(Program(id="p1", title="A", goals="x", max_proposed=1))
    out = PMCycleOutput(proposals=[_prop("a"), _prop("b")], report="r")
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == ["p1-c0-a"]
    assert summary["dropped"] == ["p1-c0-b"]


def test_cap_holds_on_a_resumed_staged_cycle(substrate):
    """The apply path has no PMContext when it resumes a staged cycle — it must
    load the program itself rather than falling back to the global constant."""
    substrate.save_program(Program(id="p1", title="A", goals="x", max_proposed=1))
    out = PMCycleOutput(proposals=[_prop("a"), _prop("b")], report="r")
    write_staging(substrate, "p1", 0, out)                 # already reasoned; only apply remains
    summary = pm_beat(substrate, "p1", FakeReasoner([]))   # the reasoner must not be consulted
    assert summary["submitted"] == ["p1-c0-a"]
    assert summary["dropped"] == ["p1-c0-b"]


def test_program_cap_below_the_existing_queue_proposes_nothing(substrate):
    substrate.save_program(Program(id="p1", title="A", goals="x", max_proposed=1))
    substrate.save_sprint(Sprint(id="p1-old", status=SprintStatus.PROPOSED,
                                 goals="g", plan=[], program="p1"))
    out = PMCycleOutput(proposals=[_prop("a")], report="r")
    summary = pm_beat(substrate, "p1", FakeReasoner([out]))
    assert summary["submitted"] == [] and summary["dropped"] == ["p1-c0-a"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_program_max_proposed.py -v`
Expected: the four new tests FAIL — the cap tests propose both sprints because the code still uses the global `MAX_PROPOSED` of 4; `test_context_uses_the_program_cap_when_set` fails on `assert 4 == 2`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/pm_agent.py`, below `MAX_PROPOSED = 4`:

```python
MAX_PROPOSED = 4


def program_cap(program) -> int:
    """How many sprints may await review for this program: its own setting, or the
    global default when unset. One helper so the prompt's number and the number the
    apply path enforces can never drift apart."""
    return program.max_proposed or MAX_PROPOSED
```

In `gather_context`, in the `PMContext(...)` construction, replace
`proposed_count=proposed_count, max_proposed=MAX_PROPOSED,` with:

```python
        proposed_count=proposed_count, max_proposed=program_cap(program),
```

In the apply path, replace `slots = MAX_PROPOSED - open_proposed` with:

```python
    slots = program_cap(substrate.load_program(program_id)) - open_proposed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_program_max_proposed.py tests/test_pm_ideas.py tests/test_parked.py tests/test_pm_adopt_artifacts.py -v`
Expected: PASS — the existing tests use programs with no cap set, so they still see `MAX_PROPOSED`.

- [ ] **Step 5: Commit** *(only with the user's approval)*

```bash
git add src/coscience/pm_agent.py tests/test_program_max_proposed.py
git commit -m "feat(pm): honour a per-program proposal cap in context and enforcement"
```

---

### Task 3: Service method, endpoint, and payload field

**Files:**
- Modify: `src/coscience/service.py:517-537` (`get_program`), `:547-554` (beside `set_program_model`)
- Modify: `src/coscience/http_api.py:111-121` (the `ProgramModelIn` neighbourhood), `:738-750` (beside the model/workdir routes)
- Test: `tests/test_http_max_proposed.py` (create)

**Interfaces:**
- Consumes: `Program.max_proposed` (Task 1).
- Produces:
  - `Service.set_program_max_proposed(program_id: str, n: int) -> dict` returning
    `{"id": str, "max_proposed": int}`; raises `ValueError` for out-of-range and
    `NotFoundError` for an unknown program.
  - `GET /api/programs/{id}` payload gains `"max_proposed": int`.
  - `POST /api/programs/{id}/max_proposed` with body `{"n": int}`. Task 4 calls it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_http_max_proposed.py`:

```python
import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.models import Program
from coscience.service import Service


@pytest.fixture
def client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="A", goals="x"))
    return TestClient(build_app(svc))


def test_program_payload_reports_the_cap(client):
    assert client.get("/api/programs/p1").json()["max_proposed"] == 0


def test_setting_the_cap_is_reflected_in_the_payload(client):
    r = client.post("/api/programs/p1/max_proposed", json={"n": 6})
    assert r.status_code == 200
    assert r.json() == {"id": "p1", "max_proposed": 6}
    assert client.get("/api/programs/p1").json()["max_proposed"] == 6


def test_zero_clears_the_override(client):
    client.post("/api/programs/p1/max_proposed", json={"n": 6})
    r = client.post("/api/programs/p1/max_proposed", json={"n": 0})
    assert r.status_code == 200 and r.json()["max_proposed"] == 0


@pytest.mark.parametrize("n", [-1, 21])
def test_out_of_range_is_rejected(client, n):
    assert client.post("/api/programs/p1/max_proposed", json={"n": n}).status_code == 422


def test_unknown_program_is_404(client):
    assert client.post("/api/programs/nope/max_proposed", json={"n": 2}).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_http_max_proposed.py -v`
Expected: FAIL — `KeyError: 'max_proposed'` on the payload test, 404s on the POST tests (no such route).

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/service.py`, add `max_proposed` to the `get_program` return dict, beside `pm_model`/`workdir`:

```python
            "pm_model": p.pm_model, "workdir": p.workdir,
            "max_proposed": p.max_proposed,
```

and add the setter after `set_program_model`:

```python
    def set_program_max_proposed(self, program_id: str, n: int) -> dict:
        """Cap how many sprints may await review for this program. 0 clears the
        override, putting the program back on the global default."""
        if not (self.substrate.program_dir(program_id) / "program.md").is_file():
            raise NotFoundError(program_id)
        n = int(n)
        if n < 0 or n > 20:
            raise ValueError("max_proposed must be between 0 and 20 (0 = default)")
        program = self.substrate.load_program(program_id)
        program.max_proposed = n
        self.substrate.save_program(program)
        return {"id": program_id, "max_proposed": program.max_proposed}
```

In `src/coscience/http_api.py`, add the request model beside `ProgramWorkdirIn`:

```python
class ProgramMaxProposedIn(BaseModel):
    n: int = 0                     # 0 clears the override
```

and the route after `set_program_workdir`:

```python
    @api.post("/programs/{program_id}/max_proposed")
    def set_program_max_proposed(program_id: str, body: ProgramMaxProposedIn) -> dict:
        try:
            return service.set_program_max_proposed(program_id, body.n)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_http_max_proposed.py tests/test_http_api.py tests/test_service_programs.py -v`
Expected: PASS

- [ ] **Step 5: Commit** *(only with the user's approval)*

```bash
git add src/coscience/service.py src/coscience/http_api.py tests/test_http_max_proposed.py
git commit -m "feat(api): set and report a program's max proposed experiments"
```

---

### Task 4: Frontend client — the cap endpoint and the Opus 4.8 option

**Files:**
- Modify: `frontend/src/api.ts:6-10` (the `Program` interface), `:178-188` (beside `setProgramWorkdir`)
- Modify: `frontend/src/components/ui.tsx:385-390` (`MODEL_OPTIONS`)
- Test: `frontend/src/api.test.ts` (append)

**Interfaces:**
- Consumes: the endpoint from Task 3.
- Produces: `api.setProgramMaxProposed(id: string, n: number)` returning
  `Promise<{ id: string; max_proposed: number }>`, and `Program.max_proposed: number`.
  Task 5 uses both.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/api.test.ts`, reusing the `mockFetch` helper already defined at
the top of that file (the file's `afterEach(() => vi.restoreAllMocks())` handles cleanup):

```ts
describe("setProgramMaxProposed", () => {
  it("posts the cap to the program's max_proposed endpoint", async () => {
    const fetchMock = mockFetch(200, { id: "p1", max_proposed: 6 });

    await expect(api.setProgramMaxProposed("p1", 6)).resolves.toEqual({ id: "p1", max_proposed: 6 });
    expect(fetchMock).toHaveBeenCalledWith("/api/programs/p1/max_proposed", expect.objectContaining({
      method: "POST", body: JSON.stringify({ n: 6 }),
    }));
  });
});

describe("MODEL_OPTIONS", () => {
  it("offers Opus 4.8", () => {
    expect(MODEL_OPTIONS).toContainEqual({ value: "claude-opus-4-8", label: "Opus 4.8" });
  });
});
```

`vi`, `describe`, `expect` and `it` are already imported at the top of that file. Add one
import for the model list:

```ts
import { MODEL_OPTIONS } from "./components/ui";
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: FAIL — `api.setProgramMaxProposed is not a function`, and the `MODEL_OPTIONS` assertion fails on the missing entry.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/api.ts`, add to the `Program` interface:

```ts
  instructions: string;   // standing house rules, in every PM prompt
  max_proposed: number;   // cap on sprints awaiting review; 0 = platform default
```

and the client method beside `setProgramWorkdir`:

```ts
  setProgramMaxProposed: (id: string, n: number) =>
    fetch(`/api/programs/${id}/max_proposed`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ n }),
    }).then(j<{ id: string; max_proposed: number }>),
```

In `frontend/src/components/ui.tsx`, add the option after Opus 5:

```ts
export const MODEL_OPTIONS = [
  { value: "claude-sonnet-5", label: "Sonnet 5" },
  { value: "claude-opus-5", label: "Opus 5" },
  { value: "claude-opus-4-8", label: "Opus 4.8" },
  { value: "claude-sonnet-4-6", label: "Sonnet 4.6" },
  { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
];
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/api.test.ts && npx tsc --noEmit`
Expected: PASS, and no type errors.

- [ ] **Step 5: Commit** *(only with the user's approval)*

```bash
git add frontend/src/api.ts frontend/src/api.test.ts frontend/src/components/ui.tsx
git commit -m "feat(frontend): max-proposed client call and an Opus 4.8 model option"
```

---

### Task 5: The settings modal

**Files:**
- Create: `frontend/src/components/ProgramSettingsModal.tsx`
- Create: `frontend/src/components/ProgramSettingsModal.test.tsx`
- Modify: `frontend/src/views/ProgramDetail.tsx:151-165` (add the ⚙ button to the header action group) and its import/`useState` block

**Interfaces:**
- Consumes: `api.setProgramMaxProposed`, `Program.max_proposed` (Task 4); the
  existing `api.setProgramModel` / `setProgramWorkdir` / `setProgramInstructions`;
  `ModelSelect` from `./ui`; `DirectoryPickerModal` from `./DirectoryPickerModal`.
- Produces: `<ProgramSettingsModal opened onClose={...} program={...} onSaved={...} />`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ProgramSettingsModal.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: {
    setProgramModel: vi.fn().mockResolvedValue({}),
    setProgramWorkdir: vi.fn().mockResolvedValue({}),
    setProgramMaxProposed: vi.fn().mockResolvedValue({}),
    setProgramInstructions: vi.fn().mockResolvedValue({}),
  },
}));

import { api } from "../api";
import ProgramSettingsModal from "./ProgramSettingsModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const program = {
  id: "p1", title: "A", status: "active", goals: "x",
  report: "", cycle: 0, sprints: [], pm_model: "claude-opus-5",
  workdir: "/tmp/proj", instructions: "be careful", max_proposed: 6,
  activations: [], last_run: null,
};

function renderModal(overrides = {}) {
  const onSaved = vi.fn();
  const view = render(
    <MantineProvider>
      <ProgramSettingsModal opened onClose={() => {}} onSaved={onSaved}
        program={{ ...program, ...overrides } as never} />
    </MantineProvider>,
  );
  return { ...view, onSaved };
}

describe("ProgramSettingsModal", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("seeds every field from the program", () => {
    renderModal();
    expect((screen.getByLabelText("project folder") as HTMLInputElement).value).toBe("/tmp/proj");
    expect((screen.getByLabelText("max proposed experiments") as HTMLInputElement).value).toBe("6");
    expect((screen.getByLabelText("standing instructions") as HTMLTextAreaElement).value).toBe("be careful");
  });

  it("shows an unset cap as blank", () => {
    renderModal({ max_proposed: 0 });
    expect((screen.getByLabelText("max proposed experiments") as HTMLInputElement).value).toBe("");
  });

  it("posts only the field that changed", async () => {
    const { onSaved } = renderModal();
    fireEvent.change(screen.getByLabelText("max proposed experiments"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.setProgramMaxProposed).toHaveBeenCalledWith("p1", 3));
    expect(api.setProgramWorkdir).not.toHaveBeenCalled();
    expect(api.setProgramInstructions).not.toHaveBeenCalled();
    expect(api.setProgramModel).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalled();
  });

  it("clearing the cap posts zero", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("max proposed experiments"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setProgramMaxProposed).toHaveBeenCalledWith("p1", 0));
  });

  it("posts nothing on cancel", () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("standing instructions"), { target: { value: "new rules" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.setProgramInstructions).not.toHaveBeenCalled();
  });

  it("keeps in-progress edits when the program is refetched while open", () => {
    const { rerender } = renderModal();
    fireEvent.change(screen.getByLabelText("standing instructions"), { target: { value: "mine" } });
    rerender(
      <MantineProvider>
        <ProgramSettingsModal opened onClose={() => {}} onSaved={() => {}}
          program={{ ...program, instructions: "server copy" } as never} />
      </MantineProvider>,
    );
    expect((screen.getByLabelText("standing instructions") as HTMLTextAreaElement).value).toBe("mine");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/ProgramSettingsModal.test.tsx`
Expected: FAIL — cannot resolve `./ProgramSettingsModal`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/ProgramSettingsModal.tsx`:

```tsx
import { ActionIcon, Button, Group, Modal, NumberInput, Stack, Textarea, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useEffect, useRef, useState } from "react";
import { api, type Program } from "../api";
import DirectoryPickerModal from "./DirectoryPickerModal";
import { ModelSelect } from "./ui";

interface Props {
  opened: boolean;
  onClose: () => void;
  program: Program;
  onSaved: () => void;
}

/** Every program setting in one dialog. The same settings stay editable inline on
 *  the program page — this is a second door to the same state, not a replacement. */
export default function ProgramSettingsModal({ opened, onClose, program, onSaved }: Props) {
  const [model, setModel] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [maxProposed, setMaxProposed] = useState<number | string>("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const seeded = useRef({ model: "", workdir: "", maxProposed: 0, instructions: "" });
  const wasOpened = useRef(false);

  // Seed on the false->true open transition only. The program is refetched by a
  // background poll every few seconds; re-seeding on every render (or listing
  // `program` in the deps) would silently discard whatever the user is mid-typing.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      setModel(program.pm_model);
      setWorkdir(program.workdir);
      setMaxProposed(program.max_proposed || "");
      setInstructions(program.instructions);
      seeded.current = {
        model: program.pm_model, workdir: program.workdir,
        maxProposed: program.max_proposed, instructions: program.instructions,
      };
    }
    wasOpened.current = opened;
  }, [opened]);

  const save = async () => {
    const was = seeded.current;
    const cap = maxProposed === "" ? 0 : Number(maxProposed);
    const folder = workdir.trim();
    setSaving(true);
    try {
      if (model !== was.model) await api.setProgramModel(program.id, model);
      if (folder !== was.workdir) await api.setProgramWorkdir(program.id, folder);
      if (cap !== was.maxProposed) await api.setProgramMaxProposed(program.id, cap);
      if (instructions !== was.instructions) await api.setProgramInstructions(program.id, instructions);
      onSaved();
      onClose();
    } catch (e) {
      notifications.show({ color: "red", title: "Couldn't save settings", message: String(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Program settings" size="lg">
      <Stack gap="md">
        <Group gap={8} align="center">
          <ModelSelect value={model} onChange={setModel} label="planner model" />
        </Group>

        <TextInput
          label="Project folder"
          aria-label="project folder"
          className="mono"
          value={workdir}
          placeholder="control repo — set a path to run this program's agents there"
          onChange={(e) => setWorkdir(e.currentTarget.value)}
          rightSectionPointerEvents="all"
          rightSection={
            <Tooltip label="Browse folders on the server" withArrow>
              <ActionIcon variant="subtle" size="sm" aria-label="browse folders"
                          onClick={() => setBrowsing(true)}>📁</ActionIcon>
            </Tooltip>
          }
        />

        <NumberInput
          label="Max proposed experiments"
          aria-label="max proposed experiments"
          description="How many experiments may wait for your review at once. Blank = 4."
          min={1}
          max={20}
          value={maxProposed}
          onChange={setMaxProposed}
        />

        <Textarea
          label="Standing instructions"
          aria-label="standing instructions"
          description="House rules the planner follows every cycle."
          autosize
          minRows={4}
          value={instructions}
          onChange={(e) => setInstructions(e.currentTarget.value)}
        />

        <Group justify="flex-end" gap={8}>
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button color="machine" loading={saving} onClick={save}>Save</Button>
        </Group>
      </Stack>

      <DirectoryPickerModal
        opened={browsing}
        initialPath={workdir}
        onClose={() => setBrowsing(false)}
        onPick={(picked) => setWorkdir(picked)}
      />
    </Modal>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/ProgramSettingsModal.test.tsx`
Expected: PASS

- [ ] **Step 5: Wire the ⚙ button into the program header**

In `frontend/src/views/ProgramDetail.tsx`, add the import beside the other component imports:

```tsx
import ProgramSettingsModal from "../components/ProgramSettingsModal";
```

add the state beside the other `useState` flags in the component (e.g. next to `const [browsing, setBrowsing] = useState(false);`):

```tsx
  const [settingsOpen, setSettingsOpen] = useState(false);
```

and add the button to the header action `<Group>`, immediately before the chat `Tooltip`:

```tsx
            <Tooltip label="Program settings" withArrow>
              <ActionIcon variant="light" color="gray" size="lg" radius="md"
                          onClick={() => setSettingsOpen(true)} aria-label="program settings">
                ⚙
              </ActionIcon>
            </Tooltip>
```

and render the modal just after the closing `</Group>` of that header row's outer group, before `<Group gap={10} mt={9} align="center">`:

```tsx
        <ProgramSettingsModal
          opened={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          program={p}
          onSaved={refresh}
        />
```

- [ ] **Step 6: Run the full frontend check**

Run: `cd frontend && npm test && npx tsc --noEmit && npm run build`
Expected: all tests pass, no type errors, build succeeds.

- [ ] **Step 7: Verify in the running app**

Run the app, open a program, click ⚙. Confirm: the four fields show current values; changing only the cap and saving updates the number and leaves the folder and instructions alone; the inline header model select and the instructions card still work and show the new values after a save.

- [ ] **Step 8: Commit** *(only with the user's approval)*

```bash
git add frontend/src/components/ProgramSettingsModal.tsx frontend/src/components/ProgramSettingsModal.test.tsx frontend/src/views/ProgramDetail.tsx
git commit -m "feat(frontend): program settings modal"
```

---

## Full-suite check

After Task 5, run the whole suite before calling the feature done:

```bash
python3 -m pytest -q
cd frontend && npm test && npm run build
```

Deploying to the production host is `bash scripts/deploy.sh` — it always rebuilds the
bundle, which rule 1 in `CLAUDE.md` requires even for python-only changes. Do not deploy
without the user asking.
