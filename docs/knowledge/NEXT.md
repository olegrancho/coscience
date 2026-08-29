# Program wiki — pick up here

**Written:** 2026-08-27; revised 2026-08-28 after phase 4 (the graph) closed.
**Branch:** `feat/program-wiki`, 93 commits above `main` (`main` fully merged
in, nothing behind). Pushed nowhere, **not merged, not deployed** — the human
has explicitly said *not yet*. The remote `rancho/feat/program-wiki` is stale,
77 commits back; production has none of this.
**Suites:** backend 1254 passed (0 failed, 0 skipped), frontend 228 passed
across 24 files, `tsc --noEmit` clean. Measured directly, not estimated.
`npm run build` was **deliberately not run** for this handoff pass — this
machine serves a live dashboard from `frontend/dist` and a rebuild would swap
the bundle under whoever is using it. That leaves one real question open — see
§5 item 4.

Read this, then `docs/knowledge-charter.md` §2. Between them they are the whole
picture; nothing important lives only in a commit message.

---

## 0. Where this stands

**Phases 1, 2, 3 and 4 are all done.** The first shippable milestone — ingest +
browse — was proven against a real bundle in phases 1–2. Phase 3 (lint cadence
hardening, merge proposals, the maintenance view) is done and suite-verified,
21 commits, `42dfa3a..9867695`. **Phase 4 (the graph) is done and
suite-verified, 20 commits, `77115f6..ee741be`:** `wiki_graph.py` and its
content-keyed `.wiki/graph.json` cache, `GET /wiki/graph`, `WikiGraphView.tsx`
(force layout, tension lens, filters, focus mode), the neighbourhood pane in
`WikiView`, and provenance backlinks on `SprintDetail`/`ArtifactDetail`.

**Phase 5 (ask & research — wiki chat, research runs, `QUESTIONS.md`, MCP
tools) is next**, per the design's §15 row. There is no phase-5 design doc yet
— start with `superpowers:brainstorming`, then a written plan, the same order
every phase before it followed. Do not start implementing from this file; it
is a status document, not a spec.

**Phase 4's own design doc had three claims disproved during review**, and
both the design doc and the charter's §7 decision log have been corrected —
if you find the old reasoning quoted anywhere else (an old plan draft, a stale
comment), it is wrong, not a second data point:

1. `/wiki/graph` does **not** need to precede the `/wiki/*` catch-all in
   `App.tsx`. React Router v6 ranks sibling routes by path specificity, not
   declaration order — tested by moving the route after the catch-all, which
   changed nothing. The route's current position is unchanged and still fine;
   only the old justification was wrong.
2. `d3-force@3` does **not** use `Math.random` for its jitter — it uses a
   fixed-seed LCG (`node_modules/d3-force/src/lcg.js`), so it was already
   deterministic. The explicit node seeding in `graphLayout.ts` is kept
   anyway, for a different reason: it avoids relying on d3's `isNaN`-triggered
   fallback positioning, and makes the determinism ours rather than inherited
   from a dependency's internals.
3. The `/wiki/citations/{oid:path}` route needs `:path` because an id **can
   contain `/`**, not because an artifact oid contains `@` — a plain-`@` id
   routes fine under a bare `{oid}`. Testing during the phase confirmed this.

Phase 3's two open items were **closed on 2026-08-28** — the live cadence run
happened (twice), and the human ruled on the Activity question. §5 has both
rulings. What replaced them is one item, and it is worse than either:

**`substrate.commit()`'s repo-wide `git add -A` defeats the revert premise.**
The dispatch loop calls `commit()` every ~5 seconds, so it wins the race
against any wiki run longer than one beat. Observed live on lint run `r0002`:
four `dispatch cycle` commits (02:16:53–02:17:34) swept up the agent's edits
while it was still writing, and the run's own commit — `wiki wikitest: lint
r0002 ok`, 02:17:59 — contained only the filed report and the stream files.
Reverting the commit named after the run would undo none of its work. Since
the merge-autonomy ruling rests on "the commit is the undo", this is now
load-bearing, not cosmetic. See §5 item 3.

Also worth knowing: `Service.merge_wiki_pages` is non-atomic — a write failure
partway through the loop over the rewritten pages leaves some pages changed and
no commit. This matches the existing shape of `delete_wiki_page`; it is an
inherited property, not a new risk phase 3 introduced.

## 1. What phase 4 built

Delivered against the design's §10 and §15 row and the phase's own design doc
(`docs/superpowers/specs/2026-08-28-program-wiki-graph-ui-design.md`):

- **`wiki_graph.py`** — pure, no IO: parsed pages in, `{nodes, edges}` out.
  Nodes are pages of type `Concept`, `Entity`, `Synthesis`, excluding any page
  with `graph_excluded: true` (Source pages are therefore never nodes — this is
  already asserted by lint rule `src/is-concept`, do not re-derive it). Edges
  are typed (from `relations`, carrying `type`/`confidence`/`source`) and
  untyped (body markdown links between node pages, minus any pair already
  covered by a typed relation). Node metrics: in-degree, out-degree, `orphan`,
  connected-component `cluster`, plus `status`/`trust` for colouring.
  Materialized reverse `contradicts` edges are excluded from all four metrics
  (charter §7). Cached at `.wiki/graph.json`, keyed by a **content hash**, not
  `(path, mtime, size)` (charter §7 — the parent design's original key) — a
  mismatch rebuilds.
- **The endpoint** — `GET /api/programs/{pid}/wiki/graph`, on the gated `api`
  router in `http_api.py`, delegating to `Service.wiki_graph`.
- **`WikiGraphView.tsx`** — `@xyflow/react`, same as `LineageGraph`. Layout
  does not reuse `dagre`: `graphLayout.ts` gained `forceLayout()` (`d3-force`,
  new dependency) for the full-page view and `radialLayout()` (no dependency)
  for the neighbourhood pane, alongside the untouched `layout()` (dagre) that
  `LineageGraph` still uses. Colour splits hue (page type) from fill (trust) —
  charter §7. Has the tension lens, node/relation/trust filters, a "showing N
  of M" readout, and focus mode (`?focus=<slug>`).
- **The neighbourhood pane** in `WikiView`'s `.wiki-side` aside, using
  `radialLayout`, with a citing-pages fallback for Source pages (which have no
  graph neighbourhood).
- **Provenance backlinks** — `wiki_read.citing_pages`, `Service.wiki_citations`,
  `GET /wiki/citations/{oid:path}`, surfaced as chips on `SprintDetail` (its
  results) and `ArtifactDetail` (the version on screen, with a "not ingested"
  message for a non-current version).

One item flagged during review and not yet settled — needs a real build to
check, not more reading. See §5 item 4.

## 2. What already exists — do not rebuild these

Everything phases 1–4 built is done and tested. In particular, for anyone
tempted to touch merge, lint or graph code while building phase 5:

| Piece | Where |
|---|---|
| Merge planner (pure) | `wiki_merge.py` — `plan(winner, loser, others)` |
| Merge performer + commit | `Service.merge_wiki_pages` |
| Per-program merge policy | `Program.wiki_merge` (`models.py:207`), default `auto` |
| Merge proposal queue | `state["merge_proposals"]`, applied/queued in `wiki._handle_merges` |
| Refusal memory | `state["merges_refused"]` — checked before queueing or applying |
| Cleanup-after-merge marker | `merged_from` (frontmatter) → lint rule `page/unmerged-prose` |
| Audit trail | `state["runs"]`, `RUNS_KEPT = 500`, `Service.wiki_activity`, human merges included |
| Maintenance view | `WikiLintView.tsx` — proposals, activity, filed reports, findings by rule |
| Agent lint mode + cadence | `wiki_prompts.render_lint`, `ingests_since_lint`, `wiki.lint_every()` |
| Quarantine retry | `Service.unquarantine_wiki` + `WikiView` banner |
| Graph builder (pure) | `wiki_graph.py`, cached at `.wiki/graph.json` (content-keyed) |
| Graph endpoint | `Service.wiki_graph`, `GET /wiki/graph` |
| Force / radial layouts | `graphLayout.ts` — `forceLayout()`, `radialLayout()`, alongside the original `layout()` (dagre) |
| Full-page graph view | `WikiGraphView.tsx` — tension lens, filters, focus mode |
| Neighbourhood pane | `WikiNeighbourhood.tsx`, inside `WikiView`'s `.wiki-side` |
| Provenance backlinks | `wiki_read.citing_pages`, `Service.wiki_citations`, `GET /wiki/citations/{oid:path}` |

`wiki_lint` carries 23 rule ids today (a plain `grep 'Finding("'` undercounts:
at least one id sits on the line after the `Finding(` call and a single-line
grep misses it — count the rows in spec §9 instead, now that the table is
complete, or use a line-wrap-tolerant search — every prose count in these
docs, including this one, has been wrong at least once).

## 3. Rules that still bite

- **Two repos.** This one is CODE. The data — programs, sprints, results,
  artifacts, and the wiki bundle — is the **substrate**, a separate git repo at
  `$COSCIENCE_REPO`. Code deploys never touch it.
- **Never commit or push without explicit approval.** Every time.
- **Stage explicit paths, never `git add -A`.**
- **`python` is not on PATH.** Use `~/venvs/coscience/bin/python -m pytest`.
- **The unit suite never calls a live LLM.** `wiki_agent` is injectable and
  tests pass a fake. A test that can reach `agent.launch` is wrong.
- **`@testing-library/user-event` is not a dependency.** Use `fireEvent`.
- **Scope frontend assertions.** `getByText` in a three-pane view inside a page
  full of counts is a coin flip — query by role, title, or a scoped element.
- **Linux-only runtime** (`/proc`, `os.killpg`, `fcntl`). Python ≥3.11.
- **Frozen vocabularies.** 12 relation types, 5 page types. Adding one is a
  spec change, not an implementation detail.
- **`scripts/deploy.sh` does not run on Avatar** — the uv venv has `pip3`, no
  `pip`. Start the server by hand; check `local_setup_avatar.md`.
- **Nothing gates an automatic merge but git** (ruled, spec §9.1) — a wrong
  merge stays wrong until someone notices and reverts the commit. Do not add a
  second gate without a decision-log entry saying why the human changed their
  mind.
- **`substrate.commit()` is still repo-wide** (`git add -A`), deferred by
  decision — but the deferral is now known to be costly, not just untidy. The
  dispatch loop commits every ~5s and therefore beats any wiki run to its own
  changes, so the commit named after a run does not contain that run's work
  (observed on `r0002`, §0). Reverting it undoes nothing, and reverting the
  `dispatch cycle` commit instead sweeps back whatever else was dirty.

## 4. Where the record lives

- `docs/knowledge-charter.md` — the charter. §2 status (phase 3's delivery and
  the live-cadence-run gap), §4 rules, §6 seams, §7 **decision log**, §8
  invariants. Read §7 before overturning anything; each row is a decision
  already argued, including the phase-3 rulings on merge authority, the
  `merged_from`/`page/unmerged-prose` cleanup loop, `substrate.commit()`'s
  SHA-or-empty contract, and phase 4's three cache/colour/degree deviations
  plus the retracted route-ordering constraint.
- `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — **binding.**
  When a plan and the spec disagree, the spec wins. §10 is the graph section,
  §15 is the phasing table.
- `docs/superpowers/specs/2026-08-28-program-wiki-graph-ui-design.md` — phase
  4's own design, refining the parent's §10/§11.4. §8 lists its three
  deliberate deviations from the parent; §4 and §7 carry the corrected
  route-ordering and `{oid:path}` reasoning (§0 above).
- `docs/superpowers/plans/2026-08-21-program-wiki-phase-2.md` — phase 2's plan
  and execution record.
- `.superpowers/sdd/2026-08-27-program-wiki-phase-3/` — phase 3's plan, task
  briefs and reports (git-ignored SDD workspace; does not travel to another
  machine).
- `.superpowers/sdd/2026-08-28-program-wiki-phase-4/` — phase 4's plan, task
  briefs and reports, same caveat: git-ignored, this-machine-only.
- `docs/knowledge/phase-1-record/` — phase 1's decision log, whole-branch
  review, deferred items and fix reports.

## 5. Open questions for the human

All three phase-3 questions were **ruled on 2026-08-28** and are done, not
open — kept here as a record. Each ruling is also a row in the charter's §7
decision log; do not reverse one without adding a row.

1. **`RUNS_KEPT` — raised 50 → 500.** Done, in `wiki.py:21`. An entry is a few
   hundred bytes; 500 buys about a year instead of about a month.
2. **The live cadence run — done.** Two of them, on scratch copies:
   - A **forced** lint (`POST /wiki/run {"kind":"lint"}`) on `wikitest`, run
     `r0002`, 2026-08-28. Proved launch, collect, containment
     (`escaped: []`), the `ingests_since_lint` reset, report filing and the
     `state["runs"]` append.
   - An **organic** cadence run with `COSCIENCE_WIKI_LINT_EVERY=1`, where the
     dispatch loop launched the lint itself with nobody pressing anything.
     The code default stays **5**; only the env var was overridden.
   (The fourth phase-3 question — should a human-accepted merge appear in the
   Activity list — was ruled **YES** and is now implemented: `accept_wiki_merge`
   appends a `state["runs"]` entry marked `by: "human"`. Landed in phase 4,
   commit `ee741be`.)

One item survived phase 3 into phase 4 untouched, and it is the one that
matters most:

3. **`substrate.commit()`'s repo-wide `git add -A` now defeats the revert
   premise.** The dispatch loop commits every ~5s, so it wins the race against
   any wiki run longer than one beat: on `r0002` four `dispatch cycle` commits
   swept up the agent's edits and the run's own commit held only bookkeeping.
   The merge-autonomy ruling ("nothing gates an automatic merge but git")
   assumes the merge commit is the undo. It currently is not. Scope `commit()`
   to explicit paths; scheduling is the open part, not whether.

One item from phase 4 was **resolved in the whole-phase-review fix wave
(2026-08-28)**, not merely investigated:

4. **`d3-force` in the browse-view bundle — root cause fixed, build still
   unverified.** `graphLayout.ts` originally held `layout()` (dagre),
   `forceLayout()` (`d3-force`) and `radialLayout()` (no dependency) in one
   module, and `WikiNeighbourhood` — statically reachable from `App ->
   WikiView`, the most-opened view in the wiki — imported `radialLayout` from
   it. Rollup chunks per **module**, not per export, so tree-shaking could not
   separate `radialLayout` from its module-mates' imports; the browse view's
   eager chunk was pulling in both `d3-force` and, transitively, `dagre`. The
   fix: `radialLayout` (and its `RING` constant) now live in their own
   zero-import module, `frontend/src/components/radialLayout.ts`;
   `graphLayout.ts` keeps only `layout()` and `forceLayout()`. This is a
   structural fix, not a probabilistic one — `radialLayout.ts` has no runtime
   imports at all, so there is nothing left for a bundler to fail to
   tree-shake. What is **still** open: nobody has run `npm run build` since
   the split to confirm the emitted chunks actually reflect it (this pass
   deliberately did not — see the header/`CLAUDE.md`, a live dashboard is
   served from `frontend/dist`). Confirming it costs one build plus grepping
   `frontend/dist` for `forceSimulation`/`dagre` outside the lazy
   `WikiGraphView` chunk.

**Phase 4.1 — unrendered encoding channels.** The whole-phase review
(2026-08-28) found that the renderer implements three of the five node
channels design §5.1 specifies, and silently drops the rest. Nothing is
*wrong* — nothing crashes, nothing shows false information — the channels are
simply absent, which a reader would otherwise have to discover by diffing
§5.1 against `wikiGraphStyle.ts`/`WikiGraphView.tsx` by hand. Recorded here so
they don't have to:

- **No node labels at all.** A node's title is only a `title` attribute (a
  native hover tooltip) on its `<circle>`; there is no visible `<text>` label
  anywhere on the full graph view or the neighbourhood pane.
- **No orphan ring.** `nodeStyle()` computes `outline: "2px dotted #8a8f98"`
  for an orphan node, but the `<circle>` in `WikiGraphView.tsx` never reads
  `st.outline` — CSS `outline` has no SVG rendering path via an inline style
  object on a `<circle>` in any case, so this channel cannot reach the screen
  as currently wired.
- **No deprecated strike-through.** `nodeStyle()` computes `textDecoration:
  "line-through"` for `status: "deprecated"`, but `<circle>` has no text to
  strike, and nothing else renders it. (Deprecated dimming via `opacity` DOES
  render — only the strike-through half of the pair is missing.)
- **No edge direction arrows.** Design §5.1 specifies edges "default to thin
  grey with a direction arrow." Edges render as plain `<line>` elements, no
  `marker-end`, in both the full graph and the neighbourhood pane.
- **`nodeStyle()` ignores the lens.** Its second parameter is `_lens: Lens`
  (underscore-prefixed, unused). Design §5.2 says the tension lens should dim
  "nodes touching no tension edge"; only edges restyle today, so a node
  isolated from all tension edges looks identical to one at the centre of a
  contradiction.
- **Materialized `contradicts` edges render as two overlapping lines**, not
  the "drawn once, double-headed" edge design §5.2 specifies — `wiki_graph.py`
  correctly materializes the reverse edge for display and excludes it from
  metrics (charter §7), but the renderer treats it as an ordinary second edge
  rather than pairing it with its mirror into one double-headed line.

Do not implement any of these without a design/plan pass first — they were
deliberately deferred, not missed by accident, and some (the arrow, the
tension-lens node dimming) touch shared style functions other views could
plausibly start depending on if patched in a hurry.

Also recorded as deferred minors from the same review, all real but none
urgent:

- **`wiki_graph.py:37-44` has no self-loop guard on typed relations.** The
  untyped body-link loop right below it (`:48`) skips `dst == p.path`; the
  typed-relations loop does not, so a page whose frontmatter names a relation
  targeting itself gets a self-loop edge in the graph.
- **Edge ids are not unique if a page repeats an identical relation.** A typed
  edge's id is `f"t:{p.path}->{dst}:{r.type}"` — two relations on the same
  page with the same target and type collide on id. Cosmetic today (nothing
  keys off edge id uniqueness yet); would bite a future feature that does
  (e.g. React key warnings, or a per-edge annotation store).
- **`wiki_read.citing_pages` (`:181`) normalises a source reference with a
  bare `.lstrip("/")`.** This is a third, weaker copy of the "resolve a link
  target" logic that `wiki_graph._resolve` and `wiki_lint`'s own resolver each
  implement more carefully (stripping a `#fragment` first). A `resource` value
  carrying a fragment would fail to match here where the other two resolvers
  would handle it correctly.

## 6. Budget

Check before spending: `python3 ~/.claude/skills/usage/usage.py`. A live wiki
run is ~6 minutes of wall clock and a real slice of the window. Prefer one
well-briefed dispatch over several exploratory ones, and hand large artifacts
to subagents as **file paths**, never pasted into a prompt.

## 7. If you are not on this machine

Host paths, venv and remotes live in `local_setup.md`, which points at
`local_setup_<hostname>.md`. Those are untracked — if yours is missing, no
setup is recorded for your machine, so ask rather than assume. The branch
itself travels normally; the git-ignored SDD workspace does not, which is why
§4 exists.
