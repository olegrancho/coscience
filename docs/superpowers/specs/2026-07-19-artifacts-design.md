# Artifacts Design

**Goal:** Give each program a set of first-class, versioned **artifacts** — the
deliverables of the research (reports/markdown, data files, figures, interactive
pages) — that a human can view, comment on, revert, discard, and download, and
that agents (sprints + chats) produce and evolve.

**Status:** Design. Build is phased (see Build Order). The Claude-artifacts
linking research is a **separate** follow-up spike, out of scope here.

---

## 1. What an artifact is

An artifact is a **program-level entity**: a curated deliverable that outlives
any single sprint and evolves across the program's life. It is distinct from:

- **Per-sprint working files** (scratchpad, logs, agent-produced files in a
  sprint dir — already surfaced by `list_sprint_files`, `kind="artifact"`).
  Those stay per-sprint; they are not the new entity.
- **Results** (`results/{id}.md`) — immutable, one per sprint. Unchanged.

An artifact has a **kind**: `md` | `data` | `figure` | `page`. The kind drives
rendering and download, not storage (storage is uniform).

## 2. Storage: an explicit versioned store

Artifacts live under the program, not in git-tracked markdown alone (program
data can be large/binary and is deliberately gitignored, so git history cannot
be the version mechanism — see `aish-sandbox-deploy` memory). The store is
explicit and uniform for text, binary, and large data.

```
programs/{pid}/artifacts/{aid}/
  meta.md            # frontmatter manifest (title, kind, current, lock, versions[], threads)
  work/              # mutable working copy — the live edit target while locked; absent when unlocked
  v1/ v2/ v3/ …      # immutable version folders; each holds the artifact's file(s)
```

- `aid` is a slug unique within the program (e.g. `manuscript`, `umap-figure`).
- A version folder holds one or more files (a `page` may be `index.html` +
  assets; a `figure` may be `plot.png`; `md` is `content.md`; `data` is the
  data file(s)). The store does not care how many files.
- `work/` exists **only** while the artifact is locked (an edit session is
  open). On unlock it is snapshotted to a new `vN` (if changed) and removed.

### `meta.md` frontmatter shape

```yaml
type: artifact
title: "Manuscript"
kind: md                     # md | data | figure | page
current: v3                  # the active leaf version id
lock:                        # {} when unlocked
  holder_kind: sprint        # sprint | chat
  holder_id: kg-biomed-c4-write-manuscript
  acquired_at: 1721000000.0
  last_activity: 1721000500.0   # bumped on each agent write; drives the chat inactivity reaper
versions:                    # the version tree, append-only
  - id: v1
    parent: ""               # "" = root
    created_at: 1720990000.0
    created_by: "kg-biomed-c2-draft"   # sprint id | chat id | "human"
    archived: false
    note: "first draft"
  - id: v2
    parent: v1
    created_at: 1720995000.0
    created_by: "chat:ab12cd34"
    archived: false
    note: "tightened intro"
  - id: v3
    parent: v1               # a BRANCH off v1, not off v2
    created_at: 1720999000.0
    created_by: "chat:ab12cd34"
    archived: false
    note: "alt intro for discussion"
threads: []                  # feedback threads (coscience.threads), target "pm"
```

The body of `meta.md` is a short human title/description line.

## 3. Versions are a tree

Versions form a **tree** via `parent`, not a line. `current` names the active
leaf being viewed/edited.

- **Cut a version** = snapshot `work/` → new `v{n+1}` whose `parent` is the
  version `work/` was seeded from (the `current` at lock time, or the version a
  revert selected). Bump `current` to the new node.
- **Dedup**: if `work/` is byte-identical to its parent version, cut nothing
  (no empty version). A lock→unlock that changed nothing produces no version.
- **Branch**: editing while `current` points at a non-leaf (e.g. after
  reverting to an older node, or picking an old node then editing) creates a
  new child of that node — a branch. Supports "keep three candidate figures,
  discuss, pick the best."
- **Revert / switch**: set `current` to any existing version `vN`. This is
  purely a pointer move — non-destructive, cuts no version, deletes nothing. If
  you then open an edit session from `vN` and change it, you branch from `vN`
  (a new child whose `created_by` is that sprint/chat).
- **Archive (= discard)**: mark a version (or a whole subtree, or the whole
  artifact) `archived: true`. Hidden from the default view, never hard-deleted,
  reversible. A whole-artifact discard sets an `archived` flag on the artifact
  (a top-level `meta` field) and drops it from the default Artifacts list.

There is no hard delete anywhere in the artifact store.

## 4. Locking — the safeguard and the resource

A lock is an **exclusive editing hold** on an artifact: at most one holder
(a sprint or a chat) at a time. The lock lives in `meta.lock`.

**The lock makes an artifact a capacity-1 resource.** Because artifacts are
created at runtime, they are **not** static `resources.yaml` entries. Instead
the dispatcher's grant decision consults the artifact lock records directly:
a sprint that binds artifacts is granted an execute lease only when all its
artifacts are unlocked (or already locked by itself). This reuses the
existing queue/lease/priority machinery — the artifact is just another thing a
sprint must acquire before it runs.

Acquire / release:

| Event | Effect |
|---|---|
| Sprint bound to artifacts reaches `queued` | Nothing yet — queue is unbounded. |
| Dispatcher grants that sprint an execute lease | **Acquire** all its bound + created artifacts atomically (all-or-none; if any is locked by another holder, the grant does not happen and the sprint stays queued). Seed each `work/` from `current`. |
| Sprint completes or stops (done/failed/canceled/hibernated-release) | **Release** all its artifacts: cut a version each (dedup), remove `work/`, clear `lock`. |
| Human opens a **chat** bound to an artifact | **Acquire** it (reject with "artifact busy" if already locked). Seed `work/` from `current`. |
| Chat: "Save as version" | Cut a version now; keep the lock (session continues, `work/` persists). |
| Chat released (explicit) or 30-min inactivity | **Release**: cut final version (dedup), remove `work/`, clear `lock`. |

Notes:

- A **queued or hibernated** sprint that has already been granted holds its
  locks for the whole time it waits/sleeps — that is the intended safeguard
  ("this sprint owns this artifact"). The UI surfaces an owner banner with a
  cancel-to-release. (A sprint that is merely `queued` and never granted holds
  nothing.)
- **Atomic multi-lock** (sprint binding ≥1 artifact) avoids deadlock: acquire
  all-or-none in a single check under the same file discipline the dispatcher
  already uses; never hold some and block on the rest.
- **Crash safety**: a lock is released by the same worker lifecycle events that
  already end a sprint (the worker beat's stop/complete path, and the
  dispatcher's stop on hibernation/cancel). The chat inactivity reaper is the
  backstop for chats. There is no separate lock daemon.
- The **inactivity reaper** runs inside the existing dispatch/pm loop beat: any
  chat lock whose `last_activity` is older than 30 min is released (final
  version cut, `work/` removed, lock cleared, chat marked idle).

## 5. Mode 1 — sprint (heavy work)

A sprint can bind and/or create artifacts. Two new sprint fields:

```python
artifacts_bound: list[str]              # existing artifact ids this sprint edits (the resource)
artifacts_create: list[dict]            # [{"aid": "...", "title": "...", "kind": "md"}] new artifacts to produce
```

- Settable by a human at sprint creation ("write a manuscript, make it an
  artifact" → one `artifacts_create` entry).
- Emittable by the PM (see §7).
- On grant: created artifacts are instantiated (empty `meta.md`, locked to this
  sprint, empty `work/`); bound artifacts have `work/` seeded from `current`.
- The worker's instructions name each artifact and its `work/` path; the worker
  writes into `work/…`. On completion each artifact cuts a version.
- Artifact sprints are **heavy by design and always proposed / human-gated** —
  they ride the normal propose→approve→run pipeline unchanged. No auto-run path.

The worker completion contract (`finished.json`) is unchanged; version cutting
happens in the substrate on the sprint's stop/complete transition, not inside
the agent.

## 6. Mode 2 — chat (interactive work)

Reuses the existing full-scope PM chat machinery (`ChatThread` scope `full`,
`chat_agent`), with the chat **bound to an artifact**:

- `ChatThread` gains `artifacts: list[str]` (sized 1 for now — **do not
  hard-code single**; the door stays open to multi-bind later).
- Starting a bound chat acquires the artifact lock (reject if busy) and seeds
  `work/`. The chat's cwd/tooling points the agent at `work/` so its edits land
  on the working copy.
- The UI shows a **split view**: conversation beside a live render of `work/`.
  The render re-polls and updates as the agent writes (each write bumps
  `meta.lock.last_activity`).
- **Save as version** (explicit UI action) cuts a version mid-session.
- **Release** (explicit) or 30-min inactivity cuts the final version and
  unlocks.

## 7. PM triggers

The PM reasoner stays **propose-only**; it never edits artifacts. It reacts to
artifact comments and can propose artifact sprints.

- **Artifact comments** are feedback threads on the artifact, `target:"pm"`
  (reusing `coscience.threads`). A new human message enters the PM context via
  a new `artifact_feedback` payload category (same shape as `sprint_feedback`:
  keyed on `(artifact_id, thread_id, last-human-text)` so a new comment
  re-triggers the PM). The current artifact list (id/title/kind) is also fed
  into `PMContext` as background.
- **PM output** gains an action:
  `artifact_task: [{artifact_ids: [...], create: [{title, kind}], instructions, from_thread}]`.
  Machinery turns each into a **proposed** sprint with `artifacts_bound` /
  `artifacts_create` set and the instructions folded into `goals`, and posts a
  `pm` reply on the originating thread ("proposed sprint X to address this").
- This covers both original asks: "comment → PM updates artifact" (bind) and
  "ask PM to create a new artifact" (create). Both land as proposed sprints you
  approve — consistent with everything else the PM does.

## 8. UI surfaces

Implementation details will be ironed out during build; the surfaces are:

- **Artifacts tab** on `ProgramDetail`: list/grid of the program's non-archived
  artifacts — title, kind, current version, figure thumbnail, lock/owner badge.
- **Artifact detail** view:
  - Renders `current` by kind: `md` via the existing `<Md>` component; `figure`
    as `<img>`; `data` as a small table / head preview; `page` in a
    **sandboxed iframe** (see §9).
  - **Version-tree** sidebar: the tree from `meta.versions`; switch / revert /
    archive a node; shows `created_by` and `note`.
  - Comment thread panel (reuses `FeedbackThread`, target `pm`).
  - **Save as version**, **Download** (current / a chosen version), **Open
    chat** (starts a bound chat), **Discard** (archive).
  - Owner banner when locked ("sprint X / chat Y owns this — cancel to release").
- **Cross-links** (derived, not double-stored):
  - Artifact page lists sprints whose `artifacts_bound`/`artifacts_create`
    include it (scan sprints, like results).
  - `SprintDetail` lists the artifacts it binds/creates (from its own fields).
- `SprintEditModal` / `ProposeSprintModal`: fields to bind existing artifacts
  and to declare artifacts to create.

## 9. Download and interactive-page rendering

- **Download**: `GET /programs/{pid}/artifacts/{aid}/versions/{vN}/download`
  (and a `current` alias). A single-file version streams the raw file; a
  multi-file version streams a server-built zip of the version folder.
- **Interactive pages**: a `page` artifact's version folder is served to a
  **sandboxed iframe** — `sandbox` without `allow-same-origin`, a strict CSP,
  and bytes served **only** from that artifact's version path (path-guarded like
  `read_sprint_file`, no traversal). This is the same untrusted-HTML posture we
  would apply to any agent-authored page; it also informs the separate
  Claude-artifacts research.

## 10. Data model, service, API (backend)

- **Models** (`models.py`): `Artifact` (id, program, title, kind, current,
  lock, versions, threads, archived) and a `ArtifactVersion` dataclass for the
  version entries; `Sprint` gains `artifacts_bound`, `artifacts_create`;
  `ChatThread` gains `artifacts`.
- **Substrate** (`substrate.py`): `artifact_dir`, `load_artifact`,
  `save_artifact`, `iter_artifacts(program)`, version cut / dedup / archive
  helpers, `work/` seed + snapshot. All writes go through the substrate (the
  reasoner never writes), and `commit()` records `meta.md` changes for the
  audit trail (version file bytes may be gitignored when large).
- **Service** (`service.py`): list/get artifacts, comment (thread), revert,
  archive, save-as-version, start-bound-chat, download; lock acquire/release
  helpers used by the dispatcher and chat.
- **Dispatcher** (`dispatcher.py`): grant path consults artifact locks;
  atomic multi-acquire on grant; release on stop/complete.
- **HTTP** (`http_api.py`): routes under `/programs/{pid}/artifacts/...`.
- **PM** (`pm_agent.py`, `pm_reasoner.py`): `artifact_feedback` in context,
  artifact list in context, `artifact_task` output + idempotent apply.
- **Frontend**: `api.ts`, a new `ArtifactsView` + `ArtifactDetail`, version-tree
  component, bound-chat split view, cross-link lists, sprint modals.

## 11. Error handling

- **Lock contention**: chat-start on a busy artifact → rejected with a clear
  "artifact busy (owned by X)" error. A sprint binding a busy artifact simply
  stays `queued` (no error — it waits, exactly like waiting for CPU).
- **Dedup**: a no-change edit session cuts no version (byte-compare `work/` to
  parent).
- **Atomic multi-lock**: all-or-none; a partial acquire is never left behind.
- **Crash / kill**: locks are released by the existing sprint stop/complete
  lifecycle and the chat inactivity reaper; no orphaned lock survives a normal
  stop. A stale lock whose holder no longer exists is reaped on the next beat
  (holder-liveness check, mirroring `_job_alive`).
- **Missing / archived artifact** referenced by a binding: skipped with a logged
  note (the sprint proceeds on its remaining artifacts), never a crash.

## 12. Testing

- **Store**: version cut + dedup; tree parent/branch; revert (pointer move +
  branch-on-edit); archive (version, subtree, whole artifact); `work/` seed and
  snapshot round-trip.
- **Lock**: acquire/release; exclusive rejection; atomic multi-acquire (partial
  busy → none acquired); stale-lock reap.
- **Dispatcher**: a sprint binding a locked artifact is not granted and stays
  queued; released artifact → granted next cycle; multi-bind atomicity.
- **Chat**: bound chat acquires + seeds `work/`; save-as-version cuts; inactivity
  reaper releases + cuts final; multi-artifact door (list field) not hard-capped.
- **PM**: artifact comment enters context and re-triggers; `artifact_task` →
  proposed sprint with the right `artifacts_bound`/`artifacts_create`; idempotent
  re-apply; thread reply posted.
- **Service/API**: list/get/revert/archive/save/download; download zip vs raw;
  page-serve path guard (no traversal, archived hidden).
- **Frontend**: version-tree actions; sprint-modal artifact fields; cross-link
  rendering; `sprintActions`-style unit tests.

## 13. Build order (phased)

Each phase yields working, testable software. Later phases depend on earlier.

1. **Store + versioning + lock** — models, substrate, version tree, dedup,
   archive, lock acquire/release + reaper. Backend only, fully unit-tested. No
   UI, no sprint/PM wiring yet.
2. **Sprint ↔ artifact** — `artifacts_bound`/`artifacts_create`; dispatcher
   grant consults locks + atomic acquire; worker writes `work/`, versions cut on
   stop/complete. Mode 1 works end-to-end headless.
3. **Artifact UI** — Artifacts tab, detail view, version tree, download, page
   iframe, cross-links, sprint modals. Mode 1 usable from the dashboard.
4. **Chat mode** — bound chat, live `work/` render, save-as-version, inactivity
   reaper wired to the loop. Mode 2.
5. **PM triggers** — `artifact_feedback` + artifact list in context,
   `artifact_task` output + apply, comment→proposed-sprint.

**Separate follow-up (own spec):** Claude-artifacts linking research — can a
co-science artifact (esp. a `page`) be linked to / synced with a claude.ai
Artifact; mechanism, auth, limits. Research doc, no code dependency on the above.
