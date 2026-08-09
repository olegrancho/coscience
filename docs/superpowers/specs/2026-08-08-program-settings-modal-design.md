# Program settings modal + per-program proposal cap

**Date:** 2026-08-08

Program settings live inline on the Program page: the planner model select and the
project-folder input in the header, the standing-instructions editor in its own card.
There is no per-program cap on proposals — `MAX_PROPOSED = 4` in `pm_agent.py:20` is a
global constant.

This adds a **⚙ Settings** modal that gathers all four settings in one dialog, and makes
the proposal cap a per-program setting. The inline controls **stay exactly as they are** —
the modal is a second door to the same state, not a replacement.

Also: add **Opus 4.8** (`claude-opus-4-8`) to the model list, which serves both the PM
planner and the sprint worker.

## Backend — the cap becomes per-program

`Program.max_proposed: int = 0` in `models.py`. `0` means unset → fall back to the global
`MAX_PROPOSED` (4). Persisted in `program.md` frontmatter only when non-zero, matching how
`pm_model` and `workdir` are written (`substrate.py:300`). No migration: existing programs
load `0` and behave exactly as today.

Two consumers, both must use the program's value:

1. `gather_context` (`pm_agent.py:245`) passes it into `PMContext.max_proposed`, so the
   prompt tells the PM the right number and `free_slots` is computed against it.
2. The apply-side enforcement (`pm_agent.py:546`) currently reads the module constant.
   It must load the program and use the same value — `context` is not in scope there when
   a staged cycle is resumed, so it loads via `substrate.load_program(program_id)`.

Service and API, mirroring `set_program_model` / `set_program_workdir`:

- `service.set_program_max_proposed(program_id, n)` — accepts `0` (clear the override, back
  to the default) or `1..20`; raises `ValueError` for negatives and anything above 20
  (→ 422), `NotFoundError` for an unknown program. Returns `{"id", "max_proposed"}`.
- `get_program` payload gains `"max_proposed"` (the raw stored value; `0` = unset).
- `POST /programs/{program_id}/max_proposed` with body `{"n": int}`.

## Frontend — a settings modal alongside the existing controls

New `frontend/src/components/ProgramSettingsModal.tsx`, opened by a ⚙ button in the
`ProgramDetail` header action group. Four fields:

| Field | Control | Endpoint on save |
|---|---|---|
| Planner model | `ModelSelect` | `POST /programs/{id}/model` |
| Project folder | `TextInput` + 📁 `DirectoryPickerModal` | `POST /programs/{id}/workdir` |
| Max proposed experiments | `NumberInput`, min 1 max 20, placeholder `4 (default)` | `POST /programs/{id}/max_proposed` |
| Standing instructions | `Textarea`, autosize | `POST /programs/{id}/instructions` |

**Seeding.** Fields are seeded from the program on the `false → true` open transition only,
using the `wasOpened` ref guard from `CapacityModal.tsx:29-40`. Gating on `opened` alone —
or putting the program in the deps — would re-seed on every background poll and discard
whatever the user is mid-typing.

**Saving.** One **Save** button. It diffs each field against the seeded value and posts
only what changed, each to its own endpoint, sequentially; then refreshes the program query
and closes. Cancel discards. A failed post shows the error notification and leaves the modal
open with the edits intact.

**Empty max-proposed** posts `0`, clearing the per-program override back to the default.

Nothing inline is moved or removed: the header model select, the folder input, and the
instructions card keep working and edit the same underlying state. Opening the modal after
an inline edit shows the fresh value, because seeding reads from the same query.

## Model list

`MODEL_OPTIONS` in `ui.tsx:385` gains `{ value: "claude-opus-4-8", label: "Opus 4.8" }`,
placed after Opus 5. The list is shared by `ModelSelect`, which serves both the program
planner model and the sprint worker model, so one entry covers both. Nothing backend-side
validates the model string, so no other change is needed.

## Tests

**pytest**
- Unset cap → `PMContext.max_proposed == 4` and enforcement drops the 5th proposal.
- Cap set to 2 → context reports 2, and a reasoner returning 3 proposals gets 2 written,
  1 dropped.
- Cap honoured on a *resumed staged cycle* (no `context` in scope) — the regression this
  design's second consumer exists to prevent.
- `set_program_max_proposed` round-trips through the substrate; `0` omits the frontmatter key.
- Endpoint accepts `0` (clears the override); returns 422 for `-1` and `21`; 404 for an
  unknown program.

**vitest** (`ProgramSettingsModal.test.tsx`)
- Seeds all four fields from the program on open.
- Save posts only the touched field; untouched fields issue no request.
- A re-render with new program data while open does not clobber in-progress edits.
- Cancel posts nothing.
