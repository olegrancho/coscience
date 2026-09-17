# O9 Keep Per-Program Host Notes the PM Maintains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each program keeps one notes page per server: its environments, what the server is good for in this program's work, and its quirks. Every worker agent placed on that server reads it. Worker agents report what they learned about their server when they finish or escalate; the PM folds those reports into the notes, and a human can read and edit the notes on the program page.

**Architecture:** A note is a markdown file at `programs/<id>/hosts/<host>.md`; `local` names this machine. Reports that have not yet been folded in wait in `programs/<id>/hosts/reports.json`. Sources of reports:
- a worker's `finished.json` with a `host_notes` string;
- an escalation's existing `host_notes` field (O8).

The PM sees the program's notes and its pending reports beside COMPUTE. Its cycle output gains `host_notes: [{host, text?}]`. An entry clears that server's pending reports, and a `text` also rewrites the note. The worker's instructions carry the note for the server its lease is on, rendered separately from the server entry's machine-wide `notes` in `resources.yaml`.

**Tech Stack:** Python 3 (FastAPI, pytest), React + Mantine + TanStack Query (vitest).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` §9 (notes: `programs/<id>/hosts/<host>.md`; writers are the PM's `host_notes` cycle field, the worker's `host_notes` in finished.json or an escalation, and human edits on the dashboard; readers are the worker agent's instructions on that host and the PM beside COMPUTE); todo item O9.

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python -m pytest`, prefixed `PYTHONPATH=src` in the worktree (addopts already has `-q`; never add another). Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Tests never launch Claude, ssh or rsync.
- A host name in a notes path must match `^[A-Za-z0-9._-]+$`; anything else is a `ValueError`, never a path under another directory.
- A note is the program's own knowledge. The server entry's `notes` in `resources.yaml` stay machine-wide, and neither is ever merged into the other or written from the other.
- The PM may write a note only for `local` or a server in the pool whose access admits this program. Any other entry is skipped and reported in the cycle's actions as `Host note FAILED: <host>: <why>`, never raised.
- A report is cleared only by a PM `host_notes` entry naming its server, or by a human saving that server's note. Nothing else drops reports.
- New reports wake the PM once: the PM fingerprint covers the ids of pending reports, not the note texts.
- A missing or malformed `finished.json` `host_notes` is treated as no report. Completion behaves exactly as today.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/substrate.py` | modify | `host_notes_dir`, `load_host_note`, `save_host_note`, `list_host_notes`, `load_host_reports`, `add_host_report`, `clear_host_reports` |
| `src/coscience/executor.py` | modify | `ExecutionContext.program_host_notes` |
| `src/coscience/claude_executor.py` | modify | render the program note for the sprint's server; tell the agent about `host_notes` in finished.json |
| `src/coscience/worker.py` | modify | `_build_context` fills the note; `_read_finished_json` returns `host_notes`; completion files a report |
| `src/coscience/escalation.py` | modify | an escalation carrying `host_notes` files a report |
| `src/coscience/service.py`, `src/coscience/http_api.py` | modify | `get_host_notes`, `set_host_note`; `GET /api/programs/{id}/host-notes`, `PUT /api/programs/{id}/host-notes/{host}` |
| `src/coscience/pm_reasoner.py` | modify | `PMContext.host_notes`, `PMContext.host_reports`, `PMCycleOutput.host_notes` |
| `src/coscience/pm_agent.py` | modify | gather notes and reports; fingerprint; serialise/parse; the staged apply |
| `src/coscience/pm_claude.py` | modify | HOST NOTES block beside COMPUTE; output schema and parse |
| `tests/test_host_notes.py`, `tests/test_pm_host_notes.py` | create | the behaviour below |
| `frontend/src/api.ts`, `frontend/src/components/HostNotesCard.tsx` (+ test), `frontend/src/views/ProgramDetail.tsx` | modify/create | the Server notes card |
| the spec | modify | "O9 as built" paragraph |

---

### Task 1: Notes and reports are stored, read by workers, filed by workers and editable over HTTP

**Files:** substrate, executor, claude_executor, worker, escalation, service, http_api, `tests/test_host_notes.py`, the spec.

**Interfaces:**
- Produces, on `Substrate`:
  - `host_notes_dir(program_id) -> Path` (= `program_dir(program_id) / "hosts"`);
  - `load_host_note(program_id, host) -> str` (`""` when absent);
  - `save_host_note(program_id, host, text)` (strips; an empty text deletes the file);
  - `list_host_notes(program_id) -> dict[str, str]` (every `*.md` in the dir; the stem is the host);
  - `load_host_reports(program_id) -> list[dict]`, each item `{"id": str, "sprint_id": str, "host": str, "text": str, "source": "finished" | "escalation", "at": float}`;
  - `add_host_report(program_id, *, sprint_id, host, text, source, now) -> dict` (id = `f"{sprint_id}:{source}:{int(now)}"`; an empty text adds nothing and returns `{}`);
  - `clear_host_reports(program_id, host) -> int` (removes that server's reports, returns how many).
  - Every method validates `host` against `^[A-Za-z0-9._-]+$` and raises `ValueError` otherwise. `reports.json` is written atomically (tmp + `os.replace`), as other substrate JSON files are.
- Produces: `ExecutionContext.program_host_notes: str = ""`.
- Produces: `Service.get_host_notes(program_id) -> {"notes": {host: text}, "reports": [...]}` and `Service.set_host_note(program_id, host, text) -> same shape`. Saving clears that server's reports and commits `f"program {program_id}: notes on {host} updated"`. Routes: `GET /api/programs/{program_id}/host-notes` and `PUT /api/programs/{program_id}/host-notes/{host}` with body `{"text": str}`; 404 for an unknown program, 422 for a bad host name.

- [ ] **Step 1: Failing tests** in `tests/test_host_notes.py`. Use a tmp substrate with a program, following `tests/test_service_programs.py`; worker tests follow `tests/test_worker_detached_job.py` or whichever test drives a sprint to completion through `finished.json`.
  - Substrate:
    - round trip of a note;
    - saving an empty text removes the file;
    - `list_host_notes` returns every note;
    - `host="../x"` raises `ValueError`;
    - reports: add two for `a` and one for `local`, `clear_host_reports("p1", "a") == 2`, and the `local` one stays;
    - an empty report text adds nothing.
  - Worker instructions:
    - a context on remote host `a` with `host_notes="machine rule"` and `program_host_notes="use conda env torch2"` renders both, the program note under the heading below, after the machine notes;
    - a context on `local` with a program note renders the local section;
    - with no program note, neither the heading nor the local section appears.
  - `_build_context` sets `program_host_notes` from `substrate.load_host_note(sprint.program, <lease host or "local">)`.
  - Completion: a sprint whose `finished.json` is `{"summary": "done", "host_notes": "CUDA 11 only"}` completes as before and leaves one report `{sprint_id, host: <its lease host or "local">, text: "CUDA 11 only", source: "finished"}`. A `finished.json` with `host_notes: 5` or none leaves no report.
  - Escalation: raising an escalation whose record has `host_notes: "disk full at /scratch"` files a report with `source: "escalation"`. Find where O8 writes `progress.escalation` from an agent's `escalate.json`, and file the report there once per escalation.
  - HTTP:
    - `GET` returns notes and reports;
    - `PUT .../host-notes/a` writes the note, clears `a`'s reports and commits;
    - `PUT` with an empty text deletes the note;
    - 422 for `host = "a b"`, 404 for an unknown program.

- [ ] **Step 2: Run them to see them fail.** `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_notes.py`

- [ ] **Step 3: Substrate methods** as in Interfaces. Mirror how `save_instructions` writes a program file for the note, and the substrate's existing atomic JSON writes for `reports.json`.

- [ ] **Step 4: Worker instructions.** In `claude_executor.build_instructions`:
  - Inside the remote host section, after `{facts}{notes}`, add `program_notes = f"\nThis program's notes on this host (kept from earlier sprints' reports; trust them over guesses):\n{context.program_host_notes}" if context.program_host_notes else ""`.
  - When `not context.host_ssh and context.program_host_notes`, add a section:

```
## This program's notes on this machine
Kept from earlier sprints' reports; trust them over guesses:
<program_host_notes>
```

  - Where the instructions describe `finished.json`, add one sentence: `Add "host_notes": "<what the next sprint on this host should know: environments that work, quirks, what it is good or bad for>" when you learned something about the host worth keeping; leave it out otherwise.` Find the existing escalate.json description and make sure its `host_notes` field is described the same way; O8 may already say it.

- [ ] **Step 5: Worker.**
  - `_build_context` sets `program_host_notes`. The host is the lease's host name, `"local"` when the lease is local or absent; `_WorkerSlots.host(sprint.id)["name"]` already gives it.
  - `_read_finished_json` returns `{"summary": summary, "host_notes": notes}`, where `notes` is the stripped string, or `""` for a missing or non-string value.
  - Where completion is processed (the 3b branch), file `add_host_report(sprint.program, sprint_id=sprint.id, host=<that host>, text=notes, source="finished", now=time.time())` when the sprint has a program.

- [ ] **Step 6: Escalation.** Where an escalation record is stored on the sprint's progress, file a report with `source="escalation"` when `host_notes` is non-empty and the sprint has a program. The host is `progress.host or "local"`.

- [ ] **Step 7: Service and routes** as in Interfaces. Validate the host with the substrate's check, mapping `ValueError` to 422. `set_host_note` calls `save_host_note`, then `clear_host_reports`, then commits.

- [ ] **Step 8: Run** `tests/test_host_notes.py`, the worker and escalation test files (`grep -l "finished.json\|escalat" tests/*.py`), `tests/test_http_api.py`, then the full suite. Expected: PASS.

- [ ] **Step 9: Spec paragraph**, appended at the end of the spec:

```markdown
**O9 as built.** A program keeps one markdown note per server at
`programs/<id>/hosts/<host>.md` (`local` is this machine), separate from the server
entry's machine-wide `notes`. A worker placed on a server reads its program's note there.
A worker reports what it learned in `finished.json` (`host_notes`), and an escalation's
`host_notes` is a report too. Reports wait in `programs/<id>/hosts/reports.json` until the
PM folds them in: its cycle output `host_notes: [{host, text?}]` clears a server's reports,
and a `text` rewrites the note. The PM writes notes only for servers this program may use.
A human reads and edits the notes on the program page, and saving a note clears that
server's reports.
```

---

### Task 2: The PM sees the notes and pending reports and folds reports into the notes

**Files:** `src/coscience/pm_reasoner.py`, `src/coscience/pm_agent.py`, `src/coscience/pm_claude.py`, `tests/test_pm_host_notes.py`.

**Interfaces:**
- Consumes (Task 1): `substrate.list_host_notes`, `load_host_reports`, `save_host_note`, `clear_host_reports`.
- Produces: `PMContext.host_notes: dict[str, str]`, `PMContext.host_reports: list[dict]`, `PMCycleOutput.host_notes: list[dict]` (`[{host, text?}]`).

- [ ] **Step 1: Failing tests** in `tests/test_pm_host_notes.py`. Follow `tests/test_pm_action_ledger.py` or the escalation PM tests (`tests/test_escalation_pm.py`) for a `FakeReasoner` cycle over a tmp substrate with a pool file.
  - `gather_context` puts the program's notes in `host_notes` and its pending reports in `host_reports`.
  - The fingerprint changes when a report is added, and does not change when only a note's text changes.
  - `render_compute` output (or the prompt, whichever the block lands in) contains `HOST NOTES`, the note text under its server name, and each report as `- <sprint_id> on <host> (<source>): <text>`. With no notes and no reports, neither heading appears.
  - Apply:
    - a cycle output `host_notes=[{"host": "a", "text": "torch 2.3 in env t23"}]` for a server that admits the program writes the note and clears `a`'s reports;
    - `[{"host": "a"}]` clears the reports and leaves the note unchanged;
    - `[{"host": "b"}]` for a server whose access excludes the program, or not in the pool, writes nothing and appears in the actions summary as `Host note FAILED: b: <why>`;
    - `{"host": "local", "text": ""}` deletes local's note;
    - a malformed entry (not a dict, no host) is skipped silently, like malformed `escalation_answers`.
  - Round trip: the staged output serialises and parses `host_notes` (the existing serialise/parse pair at pm_agent ~472/~505).
  - `pm_claude`'s parser reads `host_notes` from the model's JSON.

- [ ] **Step 2: Context.** In `pm_reasoner.py`, add the three fields. In `pm_agent.gather_context`, fill `host_notes = substrate.list_host_notes(program_id)` and `host_reports = substrate.load_host_reports(program_id)`. Add `payload["host_reports"] = sorted(r["id"] for r in context.host_reports)` to the fingerprint, only when non-empty. Follow the existing rule at ~157: a program with nothing new must not gain a new fingerprint. Add a wake reason `"host_reports": "a sprint reported on a host"` beside `"escalations": "sprint escalated"`.

- [ ] **Step 3: Prompt.** In `pm_claude.py`, render after the COMPUTE block (where the prompt assembles it) when either field is non-empty:

```
HOST NOTES: this program's own notes per server. Workers placed on a server read its note;
keep each one short, current and specific to this program's work (environments that work,
what the server is good or bad for, quirks).
### <host>
<text>
HOST REPORTS: what finished or escalated sprints learned about a server, not yet in its note.
- <sprint_id> on <host> (<source>): <text>
For every server with reports, add one entry to host_notes: with "text" (the whole new
note) to fold them in, or without "text" to mark them read with no change.
```

  Add `host_notes` to the output schema text (beside `escalation_answers` at ~283 and ~319) as `"host_notes": [{"host": "<server name or local>", "text": "<optional: the whole new note>"}]`, and parse it at ~553 as a list of dicts, as `escalation_answers` is.

- [ ] **Step 4: Apply.** In `pm_agent`'s staged apply, after escalation answers, loop `staged.output.host_notes`:
  - skip non-dicts and entries without a string `host`;
  - validate the host with the substrate's check (an invalid one goes to `host_note_skipped` with why `invalid server name`);
  - load the pool (`load_pool(substrate.repo_root)`), and require `host == "local"` with the local host allowing the program, or `pool.host(host)` existing and `allows(program_id)` (otherwise why `this program may not use <host>`);
  - when `"text"` is present, `save_host_note(program_id, host, str(text))`;
  - then `clear_host_reports(program_id, host)`.

  Record `host_notes_updated` (hosts whose text was written) and `host_note_skipped`. Add them to the actions summary in the same shape as the escalation keys at ~112-116: `Host notes updated: a, local` and `Host note FAILED: b: <why>`. Do not add a report-claim regex for host notes.

- [ ] **Step 5: Run** `tests/test_pm_host_notes.py`, every `tests/test_pm_*.py`, `tests/test_escalation_pm.py`, then the full suite. Expected: PASS.

---

### Task 3: The program page shows and edits the server notes

**Files:** `frontend/src/api.ts`, `frontend/src/components/HostNotesCard.tsx`, `frontend/src/components/HostNotesCard.test.tsx`, `frontend/src/views/ProgramDetail.tsx`.

**Interfaces:**
- Consumes (Task 1): `GET /api/programs/{id}/host-notes` returns `{ notes: Record<string, string>; reports: HostReport[] }`; `PUT /api/programs/{id}/host-notes/{host}` with body `{ text }` returns the same shape.
- Produces: `export interface HostReport { id: string; sprint_id: string; host: string; text: string; source: "finished" | "escalation"; at: number }`, `api.getHostNotes(id)`, `api.setHostNote(id, host, text)`.

- [ ] **Step 1: `api.ts`** — the type and the two methods, following `setProgramInstructions`' fetch pattern.

- [ ] **Step 2: `HostNotesCard.tsx`, test first.** Props: `{ programId: string }`.
  - Read the notes with `useQuery({ queryKey: ["host-notes", programId], queryFn: () => api.getHostNotes(programId) })` and the servers with the existing `["ledger"]` query.
  - Rows are every ledger server that admits the program (`hostAllows` from `./programAccess`), plus any server that has a note or reports but no longer admits it (label suffix ` (no longer used by this program)`). Order: `local` first (label `this machine`), then by name.
  - Each row shows:
    - the note, rendered with `Md` (`../components/Md`), or dimmed `No notes yet.`;
    - its pending reports as dimmed lines `from <sprint_id> (<source>): <text>`;
    - an `Edit` button (aria-label `Edit notes on <label>`) that turns the row into a `Textarea` seeded with the note, plus `Save` and `Cancel`.
  - `Save` calls `api.setHostNote`, sets the query data to the result, and leaves edit mode. An error shows in red on the row.
  - Card eyebrow: `server notes — what each server is for in this program`. When there are no rows, the card is not rendered.
  - Tests:
    - rows and labels from a mocked ledger plus notes;
    - reports render;
    - Edit, then Save calls `setHostNote` with the typed text and shows the new note;
    - Cancel discards;
    - a server with a note that no longer admits the program shows the suffix.

- [ ] **Step 3: `ProgramDetail.tsx`.** Render `<HostNotesCard programId={id} />` after the status-report card, with `id="sec-host-notes"` on its Card. Follow how the other section cards take `cardStyle`: pass it, or import it the way they do. Update `ProgramDetail.test.tsx` mocks so `getHostNotes` and `getLedger` exist.

- [ ] **Step 4: Run** from `frontend/`: `npx vitest run src/components/HostNotesCard.test.tsx src/views/ProgramDetail.test.tsx`, then `npx vitest run`, then `npx tsc -b`. Expected: PASS.
