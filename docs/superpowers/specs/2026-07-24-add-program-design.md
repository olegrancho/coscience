# Add Program from the dashboard

## Problem

A program is the top-level research direction a user hands to the AI. Today a
program can be created **only** from the CLI:

```
coscience program create --id … --title … --goals …
```

The dashboard can list, open, pause/close, and re-model programs, but not create
one — its empty state literally instructs the user to drop to a terminal. This
breaks the "you never touch the command line" promise for the very first step.

## Goal

Let a user create a program from the dashboard: a **New Program** button opens a
modal (title, goals, optional workdir); on success the program is persisted to
the substrate and the UI navigates to the new program's page.

Out of scope: editing goals/title after creation, deleting programs, and setting
`pm_model` at creation (it stays settable later via the existing program-page
UI).

## Design

### Program ID

IDs follow the existing convention: `p1`, `p2`, … Sprint IDs are prefixed with
the program id and split on the first dash (`progOf` in the frontend does
`id.slice(0, id.indexOf("-"))`), so a program id **must not contain a dash** —
the `pN` scheme guarantees this.

The id is assigned **server-side** at create time, never supplied by the client:

- `Substrate.next_program_id() -> str`: scan `programs/`, collect ids matching
  `^p\d+$`, take the max numeric suffix (0 if none), return `p{max+1}`.
- On an empty repo this yields `p1`; with `p1` present, `p2`.
- Non-conforming existing dir names (if any) are ignored for the max — they
  can't collide with a `pN` id.

### Backend

**`Service.create_program(title, goals, workdir="") -> dict`** (in `service.py`)
- `title` and `goals` are required; blank (after strip) → `ValueError`.
- Compute the id via `next_program_id()`.
- Build `Program(id=…, title=title.strip(), goals=goals.strip(), workdir=workdir.strip())`
  (status defaults to `active`), `substrate.save_program(program)`.
- Return the same dict shape as `get_program` so the client can route immediately.

**`POST /programs`** (in `http_api.py`)
- Request model `ProgramCreateIn(title: str, goals: str, workdir: str = "")`,
  mirroring the existing `ProgramStatusIn` pattern.
- Calls `service.create_program(...)`; maps `ValueError` → `HTTPException(400)`.
- Returns `201` with the program dict.

### Frontend

**`api.createProgram({title, goals, workdir})`** (in `api.ts`)
- `POST /api/programs`, returns `Program`.

**`NewProgramModal`** (new component, mirroring `ProposeSprintModal`)
- Fields: `TextInput` **Title** (required), `Textarea` **Goals** (required),
  `TextInput` **Workdir** (optional) shown under an "Advanced" toggle so the
  common path stays two fields.
- Client-side validation: blank title or goals → inline error, no request.
- On success: invalidate the `["programs"]` query, close the modal, and
  `navigate('/programs/{id}')` using the returned id.
- Server errors surface inline in the modal (same pattern as ProposeSprintModal).

**`ProgramsOverview` wiring**
- A **New Program** button top-right of the "Programs" heading (always visible).
- The empty state's CTA becomes this button instead of the CLI command string.

## Testing

**Backend**
- `next_program_id`: `p1` on an empty repo; `p2` when `p1` exists; ignores a
  non-`pN` dir.
- `create_program`: persists a loadable program with the assigned id and given
  title/goals/workdir; rejects blank title and blank goals with `ValueError`.
- `POST /programs`: `201` + program dict on success; `400` on blank title/goals.

**Frontend**
- `NewProgramModal`: blocks submit and shows an error when title or goals is
  blank; calls `api.createProgram` with the entered values on a valid submit
  (following existing `*.test.tsx` mocking patterns).

## Files touched

- `src/coscience/substrate.py` — `next_program_id()`
- `src/coscience/service.py` — `create_program()`
- `src/coscience/http_api.py` — `ProgramCreateIn`, `POST /programs`
- `frontend/src/api.ts` — `createProgram`
- `frontend/src/components/NewProgramModal.tsx` — new modal
- `frontend/src/views/ProgramsOverview.tsx` — button + empty-state CTA
- Tests alongside each layer.
