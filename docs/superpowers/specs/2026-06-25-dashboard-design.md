# Co-Science Platform — Oversight Dashboard Design

**Status:** approved design, ready for planning
**Date:** 2026-06-25
**Scope:** the **oversight dashboard** — a human control surface over the platform. One combined spec, built in two layers within a single plan: a backend *control surface* (new tested `Service` mutations + JSON endpoints) first, then a React SPA that consumes it.

## 1. Purpose

The platform autonomously turns research **programs** into **sprints** that **workers** execute into **results**, with a **PM agent** proposing the next sprints each cycle (propose-only). Everything below is built and tested; what is missing is the human-facing surface that lets the oversight committee **see** the whole state and **take the actions that gate and steer** the autonomous loop.

The **dashboard** is that surface. It is the committee's day-to-day tool: view every program/sprint/result/ledger state, approve or reject proposals, adjust and pause work, and feed standing guidance to the PM.

## 2. Architecture

The `Service` is the single seam. Both the JSON API and the SPA go through it; correctness-critical bookkeeping lives in tested Python, and the SPA is a thin consumer.

```
React + Mantine SPA  ──HTTP/JSON──>  FastAPI  ──>  Service  ──>  git substrate
   (oversight UI)                    (/api/*)     (tested)      (programs, sprints, results, leases)
```

Three things this adds to the platform:

1. **New `Service` mutations** (tested, deterministic): reject sprint, edit sprint, set program status (pause/resume/close), program guidance CRUD — alongside the existing approve and submit.
2. **A human→PM steer**: guidance notes stored per program, fed into the PM's reasoning context every cycle. The only change to PM code — additive, read-only input behind the existing reasoner seam; staging, idempotency, and propose-only are untouched.
3. **The SPA**: a static bundle served by the same FastAPI process.

**Structural change — `/api` prefix.** The JSON API moves under `/api/*` so the SPA owns the root URL (`/` → dashboard; `/programs/42` is a client-side route that does not collide with `GET /api/programs/42`). The HTTP API is days old and its only consumer is the PM acceptance runbook (the MCP transport is a separate sibling, untouched), so the move is cheap now and is the correct long-term shape. The dashboard lives at `http://host:8000/`.

**No auth** in this iteration — an internal tool on a trusted network, matching the current HTTP/MCP surface. Explicitly out of scope (a conscious decision, not an oversight).

## 3. Backend control surface

All new methods live on `Service`, mutate the substrate, return JSON-serialisable data, and are built TDD with the existing pattern. Error mapping stays consistent with the current API: `NotFoundError → 404`, status-guard `ValueError → 422`, duplicate `ValueError → 409`.

### 3.1 Sprint actions

- **`reject_sprint(sprint_id)`** — transitions `proposed → canceled`. Guard: only valid from `proposed`; any other status raises `ValueError` (→ 422). The record stays on disk: auditable, and the PM's deterministic-id existence check means it will not recreate that id.
- **`edit_sprint(sprint_id, *, goals=None, plan=None, priority=None, resources_required=None, preemptible=None)`** — partial patch; only the provided fields change. Status guards:
  - `goals`, `plan` — editable **only while `proposed`**.
  - `priority`, `resources_required`, `preemptible` — editable while `proposed`, `approved`, or `executing`.
  - `done`, `canceled` — fully read-only (any edit raises `ValueError` → 422).
  - A field-for-status combination that is not allowed raises `ValueError` (→ 422). `plan`, when provided, must be non-empty (else `ValueError` → 422).
  - **Caveat (documented behavior, not a bug):** editing `priority`/`resources_required` on an *executing* sprint affects only the scheduler's **future** grant/preemption decisions (next dispatch cycle). The lease already held was fixed at grant time and does not change.
- **`approve_sprint(sprint_id)`** — existing; `proposed → approved`.
- **`submit_sprint(...)`** — existing; human-authored propose (writes a sprint as `proposed`).

### 3.2 Program actions

- **`set_program_status(program_id, status)`** — sets `active | paused | closed` (pause / resume / close). The PM runner already beats **only `active`** programs, so pausing immediately removes a program from the PM loop with no new wiring; resuming re-includes it. Invalid status string raises `ValueError` (→ 422); unknown program raises `NotFoundError` (→ 404).

### 3.3 Program guidance (the human→PM steer)

- Stored in `programs/<id>/guidance.md` — frontmatter holding a list of notes `{id, text, added_at}`. Kept separate from `program.md` so human-authored goals stay clean.
- Substrate gains `load_guidance(program_id) -> list[dict]` and `save_guidance(program_id, notes)`.
- Service methods:
  - **`add_guidance(program_id, text) -> dict`** — appends a note with a short stable `id` and an `added_at` timestamp; returns the new note.
  - **`remove_guidance(program_id, note_id)`** — deletes one note by id; an unknown `note_id` is an idempotent no-op (returns normally, notes unchanged); a missing program raises `NotFoundError` (→ 404). The DELETE endpoint returns 204 in both the deleted and already-absent cases.
  - **`list_guidance(program_id) -> list[dict]`** — returns the program's notes.

### 3.4 HTTP endpoints (all under `/api`)

```
# new
POST   /api/sprints/{id}/reject
PATCH  /api/sprints/{id}                      # edit_sprint (partial body)
POST   /api/programs/{id}/status              # {"status": "paused"}
GET    /api/programs/{id}/guidance
POST   /api/programs/{id}/guidance            # {"text": "..."} -> note
DELETE /api/programs/{id}/guidance/{note_id}

# existing, relocated under /api
GET    /api/health
GET    /api/sprints                           GET /api/sprints/{id}
POST   /api/sprints                           POST /api/sprints/{id}/approve
GET    /api/results                           GET /api/results/{id}
GET    /api/programs                          GET /api/programs/{id}
GET    /api/ledger
```

## 4. PM steer integration

The only change to PM code; purely additive, read-only input to the existing reasoner seam.

- **`PMContext`** gains `human_guidance: list[str]` (the note texts for that program).
- **`gather_context(substrate, program_id)`** loads the program's guidance notes and populates `human_guidance` (empty list when none).
- **`pm_claude.render_prompt(context)`** embeds the guidance in a clearly-labelled section ("Human guidance (standing direction from the oversight committee — weigh these in your proposals):") listing each note; the section is omitted when there is no guidance.
- **No change** to `pm_beat`, staging, idempotency, or the runner. Guidance is consumed fresh each cycle as context; the PM never writes it, so there is nothing to stage or replay. `FakeReasoner`-based tests stay deterministic.

Net lifecycle: a human adds a note in the dashboard → the next PM cycle reads it in its prompt and reasons under it → the human deletes it once it is addressed.

## 5. The SPA

**Stack:** React 18 + TypeScript + Vite + Mantine. Data via **TanStack Query** (caching + light polling — refetch ~every 10s and on window focus — so the dashboard reflects the background PM/dispatcher loops without manual reload). A thin typed `api.ts` client wraps `/api/*`.

**Serving:** `npm run build` produces a static bundle; FastAPI mounts it at `/` with a catch-all returning `index.html` for client routes. In dev, the Vite dev server proxies `/api` → FastAPI. One process in production; the Dockerfile gains a Node build stage that emits the bundle into the image.

**Views (React Router):**

- **`/` — Programs overview.** Table/cards of programs: title, status badge (active/paused/closed), PM cycle, sprint counts by status. The committee's landing page.
- **`/programs/:id` — Program detail (the centerpiece).** The PM's `report.md` rendered as Markdown at the top (the decision aid); the program's sprints grouped by status with inline actions; a **guidance** panel (list/add/delete notes); program controls (pause/resume/close); a "propose sprint" button.
- **`/sprints/:id` — Sprint detail.** Goals, plan steps, priority/resources, progress (completed steps, outputs), live lease info. An action bar driven by status: **Approve** / **Reject** when proposed; **Edit** with fields gated exactly as the backend allows; read-only when done/canceled.
- **`/results/:id` — Result detail.** Rendered summary, link back to its sprint (also reachable inline from sprints).
- **`/ledger` — Resources.** Capacity vs used vs available, and the table of active leases — "what is consuming compute right now".

**Forms:** a propose/edit sprint modal (goals, plan steps, priority, resources) and an add-guidance input, Mantine components throughout. Edit forms disable fields the sprint's status forbids, mirroring the backend guards so the UI never offers an action the API will reject.

## 6. Testing & acceptance

**Backend — full TDD, hermetic pytest (the proven pattern):**
- `reject_sprint`: proposed→canceled; rejecting a non-proposed sprint raises; record persists.
- `edit_sprint`: the full field × status guard matrix (goals/plan proposed-only; priority/resources/preemptible through executing; done/canceled read-only); empty-plan rejected; partial patches leave untouched fields intact.
- `set_program_status`: each transition; a paused program is skipped by `pm_run_once` (ties the pause to real PM behavior).
- guidance: add returns a stable id; list round-trips; delete removes one note without disturbing others; substrate `load/save_guidance` round-trip.
- `gather_context` populates `human_guidance`; `render_prompt` includes notes when present and omits the section when empty.
- HTTP: each new route's success + error-status mapping (404/422/409); the `/api` prefix move (existing route tests repointed).

**Frontend — light, contract-focused (the JSON API is the seam, not the UI):**
- `vitest` component/smoke tests: the typed `api.ts` client; the status→available-actions logic (so the UI guard mirrors the backend); the report renders; forms disable forbidden fields. No heavyweight e2e.

**Manual acceptance runbook** (sibling doc, like the PM one): build the SPA → open `/` → see a program with its report → approve a proposed sprint from the UI → add a guidance note → run a real PM cycle → confirm the note shaped the next proposals → pause the program → confirm the PM skips it. The end-to-end "a human can drive the loop from the browser" proof.

## 7. Decomposition (one plan, ordered)

Built subagent-driven, one agent at a time, TDD, same method as Phase 1b/2. Backend (1–4) ships green and fully usable via `/api` before any frontend exists; the SPA (5–6) is a pure consumer.

1. **`/api` prefix move** — relocate existing routes under `/api`; repoint route tests and the PM acceptance runbook. Small, isolated; unblocks the SPA URL model.
2. **Sprint mutations** — `reject_sprint`, `edit_sprint` + endpoints (the guard matrix; the bulk of new backend tests).
3. **Program mutations** — `set_program_status` + endpoint; the pause↔PM-skip test.
4. **Guidance + PM steer** — substrate storage, Service CRUD, endpoints, `PMContext`/`gather_context`/`render_prompt` wiring.
5. **SPA scaffold + serving** — Vite/React/Mantine app, typed `api.ts`, TanStack Query, FastAPI static mount + Dockerfile build stage, Programs overview view.
6. **SPA detail views + actions** — program detail (report, sprints, guidance, controls), sprint detail (action bar + edit/propose modals), result + ledger views.
7. **Acceptance runbook + final whole-branch review.**

## 8. Key decisions (resolved during brainstorming)

| Decision | Choice | Why |
|---|---|---|
| Spec shape | One combined spec; backend layer before frontend within one plan | The SPA is hollow without the endpoints; a single ordered plan keeps it coherent. |
| Surface | Full control surface, not read-only | The committee approves/rejects/pauses/edits/steers from one place. |
| Reject semantics | `proposed → canceled` (reuse existing enum) | Auditable, no new status, PM won't recreate the id. |
| Edit scope | goals/plan proposed-only; priority/resources/preemptible through executing | Editing a running job's plan would corrupt it; scheduler inputs can still be tuned. |
| PM steer | Standing guidance list per program | The PM reasons under current notes every cycle; human prunes them. |
| Stack | React + TypeScript + Vite + Mantine | Safe-to-hand-off default; Mantine gives a polished data-dashboard look with minimal CSS. |
| API location | Move JSON API under `/api/*` | SPA owns root URL; clean separation; cheap while the API is young. |
| Auth | None this iteration | Internal tool, trusted network; matches current HTTP/MCP surface. |

## 9. Risks & mitigations

- **`/api` move breaks the existing HTTP consumer** → only the PM acceptance runbook references the old paths; it is repointed in Increment 1, and the MCP transport is independent.
- **UI offers an action the API rejects** → the SPA derives available actions from the same status rules the backend enforces; backend guards are authoritative and tested, the UI mirrors them.
- **Editing an executing sprint surprises the user** → documented behavior: priority/resources changes affect future scheduling only, never the held lease; surfaced in the edit form copy.
- **Guidance breaks PM kill-safety** → guidance is read-only reasoner input; it is never staged or written by the PM, so resume/idempotency are unaffected.
- **Frontend correctness drift** → correctness lives in the tested backend; the JSON API is the contract; frontend tests stay light and contract-focused rather than chasing UI coverage.
