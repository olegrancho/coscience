# Directory picker for a program's project folder

## Problem

A program's **project folder** (`Program.workdir`) is the directory its agents
`cd` into — the worker (`Worker._agent_cwd`), the PM reasoner
(`pm_agent._resolve_workdir`) and chat sessions (`chat_agent.resolve_workdir`)
all resolve it the same way: use it if set *and it exists on disk*, otherwise
fall back to the substrate root.

Today it is set by typing a raw absolute path into a free-text field, in two
places: the program page (`ProgramDetail`, blur-to-save) and the `+ advanced`
section of `NewProgramModal`. That is guesswork — the path lives on the
**server**, which is not the machine the browser is running on. A typo does not
fail loudly either: the value saves with a yellow "doesn't exist yet" warning
and the agents quietly keep running in the substrate.

## Goal

Let a user pick the project folder by browsing the server's filesystem from the
dashboard, and create a new folder while doing so.

Out of scope: browsing hosts other than the one running the backend (see
"Remote hosts" — the seam is designed, not built), listing files, and changing
`Program.workdir` from a bare path into anything host-qualified.

## Security context

The API router is auth-gated (`Depends(current_user)` in `http_api.py`), but
auth is **disabled when `.coscience/users.yaml` is absent**, which is the case
on the current local deployment — and the server binds `0.0.0.0`. A
filesystem-listing endpoint is therefore enumerable by anything on the LAN
until a user registry exists. This is why browsing is confined to configured
roots rather than starting at `/`.

## Design

### Configuration

`COSCIENCE_BROWSE_ROOTS` — colon-separated absolute paths, `~` expanded.
Defaults to `[Path.home()]`. Roots are resolved when read; non-existent ones are
dropped, and if that leaves the list empty we fall back to home so the picker is
never dead.

**The env var MUST be read at call time, not captured at import.** Module-level
capture makes the setting untestable without reimporting, and every root test
monkeypatches it against a `tmp_path`.

### Module — `src/coscience/fs_browse.py`

Standalone and pure; it does not depend on `Service` or `Substrate`. Browsing is
deliberately **not** scoped to a program, because `NewProgramModal` picks a
folder before a program exists.

- `roots() -> list[Path]` — parse and validate the configured roots.
- `list_dirs(path: str | None) -> dict` — subdirectories of `path`, or of the
  first root when `path` is `None`.
- `make_dir(parent: str, name: str) -> dict` — create one directory.
- `DirectorySource` protocol, with `LocalSource` as the only implementation.

**Confinement.** Both operations call one guard: `.resolve()` the candidate, then
reject it unless `.is_relative_to(root)` holds for some configured root. This is
the idiom already used in `service.py:1155` and `http_api.py:917`. Because
`resolve()` follows symlinks *before* the check, a symlink pointing outside the
roots is rejected rather than followed — the behaviour we want, for free.

**Listing rules.** Directories only, sorted by name. Dot-directories are
filtered out (no "show hidden" toggle). An entry that cannot be `stat`ed is
skipped rather than failing the whole listing, so one unreadable subdirectory
does not break the view.

**Name validation for `make_dir`.** Reject empty/whitespace-only, any name
containing `/` or a null byte, and exactly `.` or `..`. The resolved result must
still pass the confinement guard.

### Endpoints (`http_api.py`)

Both sit on the existing `api` router, so they inherit `Depends(current_user)`
and become authenticated the moment a user registry exists.

```
GET /api/fs/dirs?path=/home/oleg/sync        ->  200
{ "path":    "/home/oleg/sync",
  "parent":  "/home/oleg",                      // null when path is itself a root
  "roots":   [{"label": "~", "path": "/home/oleg"}],
  "entries": [{"name": "bmt-share", "path": "/home/oleg/sync/bmt-share"}] }

POST /api/fs/dirs   {"parent": "/home/oleg/sync", "name": "lf-work"}   ->  201
{ "path": "/home/oleg/sync/lf-work" }
```

`roots` is returned on **every** listing response so the modal can render
breadcrumbs and clamp "up" without a second call.

Status mapping:

| Condition | Status |
|---|---|
| Path resolves outside every configured root | `403` |
| `PermissionError` reading the path | `403` |
| Path missing, or exists but is not a directory | `404` |
| Invalid folder name | `400` |
| Target folder already exists | `409` |

`403` for out-of-root is deliberate rather than an existence-hiding `404`: the
root list is already public in every response, so there is nothing to hide.

### The root level, when more than one root is configured

`path=None` is resolved by an explicit rule, so the single-root and multi-root
cases are both unambiguous:

- **Exactly one root** — `path=None` resolves to that root directly. `parent` is
  null there and `↑` is disabled; there is no extra level to click through.
- **More than one root** — `path=None` returns a *virtual root level*: `path` is
  null, `parent` is null, and `entries` is the configured roots themselves. `↑`
  from any real root navigates back to this level (i.e. `parent` is null at a
  root, and the modal treats that as "go to the virtual level" only when more
  than one root exists).

A root's `label` is `~` when it is the home directory, and the full path
otherwise.

### Frontend

**`api.ts`** — two wrappers in the existing style: `listDirs(path?)` and
`createDir(parent, name)`.

**`components/DirectoryPickerModal.tsx`** — one component serving both call
sites.

```
props: { opened, initialPath, onClose, onPick(path: string) }
state: path — starts at initialPath when it is a usable directory, else null (-> first root)
data:  useQuery(["fs-dirs", path], () => api.listDirs(path))
```

Layout: breadcrumb across the top with an `↑` control (disabled when
`parent === null`, i.e. at a root); a scrollable list of folders where a click
descends; the resolved path in mono at the bottom; `[Cancel] [Use this folder]`.

A `＋ New folder` button reveals an inline text input; on create it invalidates
`["fs-dirs", path]` so the new folder appears in place. Errors render inline in
the modal (matching `ProposeSprintModal` / `NewProgramModal`) rather than firing
toasts — the modal is already the user's focus.

**The trigger is inside the field, not beside it.** Both call sites keep their
existing `TextInput` and add a subtle `ActionIcon` (folder glyph, `variant="subtle"`)
in the input's `rightSection`, with a tooltip. No standalone button anywhere.

> Mantine 7 sets `pointer-events: none` on input sections by default, so the
> input needs `rightSectionPointerEvents="all"` or the icon will not be
> clickable. There is no existing `rightSection` usage in this codebase, so this
> establishes the pattern.

**Call site — `ProgramDetail`.** Picking calls the *existing* `saveWorkdir(path)`,
inheriting the current save and its teal/yellow notifications unchanged. The
field is uncontrolled (`defaultValue` + `key={p.workdir}`), so the post-save
`refresh()` changes the `key`, remounting the input with the new path — no extra
state required.

**Call site — `NewProgramModal`.** Picking sets the local `workdir` state. This
nests a modal inside a modal; a plain nested Mantine `<Modal>` is expected to
stack correctly, with `Modal.Stack` as the fallback if focus-trapping misbehaves.

### Remote hosts (designed, not implemented)

The eventual goal is a host selector in the picker, with `workdir` becoming
host-qualified (e.g. `aish-sandbox:/data/lf`). This spec keeps that reachable
without building it:

- `fs_browse` exposes the `DirectorySource` protocol; an SSH implementation
  becomes a new class rather than a rewrite.
- `roots` is a **list** in the response, so it can later be scoped per host
  without changing the response shape or the modal.
- The endpoints may take an optional `host` param that currently accepts only
  `local` and rejects anything else as unknown.

`Program.workdir` stays a bare path. Making it host-qualified ripples into the
three cwd resolvers and the agent launcher; that is a separate project with its
own spec.

## Testing

**Backend** (pytest, plus the `TestClient` already used in `tests/`)

- `roots()`: default is home when the env var is unset; colon-separated parsing;
  `~` expansion; non-existent roots dropped; fallback to home when all are invalid.
- `list_dirs`: directories only and sorted; dot-directories skipped; `parent` is
  null at a root; `..` escape rejected; **a symlink pointing outside a root
  rejected**; an unreadable entry skipped rather than fatal; missing path errors.
- `list_dirs(None)`: with one root configured, returns that root's contents;
  with two roots configured, returns the virtual root level listing both.
- `make_dir`: happy path creates the directory; rejects `""`, `a/b`, `.`, `..`,
  and a null byte; duplicate conflicts; outside-roots forbidden.
- HTTP: a thin pass asserting the 200/201/400/403/404/409 mapping above.

**Frontend** (vitest, mocking `api` as the existing `*.test.tsx` do)

- Renders the folders returned by `listDirs`.
- Clicking a folder refetches at the child path.
- `Use this folder` calls `onPick` with the current path.
- `New folder` calls `api.createDir` with the parent and the entered name.

## Files touched

- `src/coscience/fs_browse.py` — new module (roots, listing, mkdir, confinement)
- `src/coscience/http_api.py` — `GET /api/fs/dirs`, `POST /api/fs/dirs`
- `frontend/src/api.ts` — `listDirs`, `createDir`
- `frontend/src/components/DirectoryPickerModal.tsx` — new modal
- `frontend/src/views/ProgramDetail.tsx` — in-field trigger
- `frontend/src/components/NewProgramModal.tsx` — in-field trigger
- Tests alongside each layer.
