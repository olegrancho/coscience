# Figure artifacts carry a description

**Date:** 2026-08-04
**Status:** approved, ready to plan

## Problem

A `figure` artifact renders as the image and nothing else — on its own page, in
the chat preview that edits it, and as a 44px thumbnail on the program card.
`Service._artifact_excerpt` short-circuits `kind == "figure"` to `""` on the
grounds that a figure has nothing to quote. So a reader gets a plot with no
axes explanation, no units, and no statement of what it is supposed to show;
the knowledge stays with whichever agent drew it.

## The convention

A figure artifact's version holds **`description.md`** next to the image.

That exact filename, nothing else. `README.md`, `notes.md` and friends do not
render — one predictable name is easier to state in an agent prompt than a
precedence list, and the cost of a wrong name is a visible "no description yet"
line, not silence.

The description lives inside the version directory (and its `work/` copy), so:

- it versions with the image instead of drifting from it — a new plot cuts a
  new version carrying its own text;
- downloads and the substrate's git history already include it, with no change
  to the zip/download route;
- no model or API field is added.

The filename is a single constant per side: a module-level constant in
`service.py`, and one exported from `frontend/src/components/ui.tsx` beside
`isImageName`.

## Backend — `src/coscience/`

### 1. The program card's excerpt (`service.py`)

`_artifact_excerpt` stops treating figures as unquotable. For
`art.kind == "figure"` the candidate list is exactly `["description.md"]`; every
other kind keeps today's `_excerpt_candidates` behaviour (prose first, skipping
images, `__pycache__` and opaque suffixes). The existing read loop — binary
guard, `_THUMB_CHARS` truncation, `NotFoundError`/`OSError` tolerance — is
reused unchanged.

A figure without a description still yields `""`, so its card looks exactly as
it does today.

### 2. Prompt guidance — three sites, no enforcement

Agents are told to produce the description; nothing validates it. A version cut
without one still cuts, and a mid-work save is never blocked.

- **`claude_executor.build_instructions`** — in the "Artifacts to produce"
  section, a spec whose `kind` is `figure` gets an extra sentence: write
  `description.md` beside the image saying what the figure shows, its axes and
  units, and what a reader should conclude from it.
- **`service.send_chat_message`** — the `[ARTIFACT]` preamble prepended to a
  bound chat turn adds the same sentence when any bound artifact is a figure.
  (The kinds are loaded from the substrate at that point; the artifacts are
  already being locked and bumped there.)
- **`pm_claude.py`** — the artifacts guidance states that a figure artifact is
  an image plus a `description.md`, so sprint goals and artifact specs the PM
  writes ask for both.

## Frontend

### 3. Artifact page (`views/ArtifactDetail.tsx`)

`CurrentVersion`'s `kind === "figure"` branch renders the image as it does now,
then the description below it:

- a `useQuery` on `readArtifactFile(pid, aid, current, "description.md")`,
  enabled only when `files` contains the name — so a figure without one costs
  no request;
- rendered through `<Md>` in the same `report-leaf` wrapper the md/text branch
  uses, so a description reads like every other document on the platform;
- passing the same `resolveSrc` the text branch passes (added in `9c00f0e`), so
  a description that references the figure inline — `![](plot.png)`, or a
  second panel in `figures/` — resolves against the version's raw route instead
  of the dashboard route and 404ing;
- when the file is absent, one dimmed line naming `description.md`, so the gap
  is legible rather than silent.

### 4. Chat preview (`views/ChatView.tsx`)

The sticky bound-artifact pane does the same against the live working copy:
`readArtifactWorkFile(id, aid, "description.md")`, keyed on
`work.dataUpdatedAt` exactly as the image's cache-buster is, so an agent that
edits the description shows it on the next poll. It renders under the image,
inside the same scroll container, with the pane's work-raw `resolveSrc`, and
only when the work listing contains the file.

### 5. Program card (`views/ProgramDetail.tsx`)

No change. The card already puts `a.excerpt` in its `title` tooltip; the
backend change fills it for figures.

## Testing

**Python**

- `_artifact_excerpt` returns the opening of `description.md` for a figure whose
  version holds one, and `""` for a figure without one
  (`tests/test_http_artifacts_read.py:94` already asserts the empty case — its
  fixture ships no `description.md`, so that assertion stands unchanged).
- `_artifact_excerpt` for a figure ignores a `README.md` that is not
  `description.md`.
- `claude_executor.build_instructions` includes the description sentence for a
  `figure` spec and omits it when every spec is another kind.
- The bound-chat `[ARTIFACT]` preamble carries the sentence for a figure and
  not for an `md` artifact.

**Frontend** (existing view tests gain cases)

- `ArtifactDetail.test.tsx` — a figure with `description.md` renders both image
  and description; without it, the dimmed missing-description line; a relative
  image path inside the description resolves to the version's raw route (the
  case `9c00f0e` added for text kinds, now for figures).
- `ChatView.test.tsx` — the bound preview shows the live description under the
  image.

## Out of scope

- Any hard requirement or validation that a figure version contain a
  description.
- Backfilling descriptions for figures that already exist.
- A description for non-figure kinds (md artifacts are already prose; `data`
  and `page` are unchanged).
