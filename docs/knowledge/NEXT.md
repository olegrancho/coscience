# Program wiki — pick up here

**Written:** 2026-08-27, replacing the phase-3 handoff this file used to be.
**Branch:** `feat/program-wiki`, 65 commits above `main`. Pushed nowhere,
**not merged, not deployed** — the human has explicitly said *not yet*.
**Suites:** backend 1208, frontend 184, `tsc --noEmit` clean.

Read this, then `docs/knowledge-charter.md` §2. Between them they are the whole
picture; nothing important lives only in a commit message.

---

## 0. Where this stands

**Phases 1, 2 and 3 are all done.** The first shippable milestone — ingest +
browse — was proven against a real bundle in phases 1–2. Phase 3 (lint cadence
hardening, merge proposals, the maintenance view) is done and suite-verified,
21 commits, `42dfa3a..9867695`.

**Phase 4 — the graph — is the work in progress**, per the design's §15 row:
`wiki_graph`, its endpoint, `WikiGraphView`, `d3-force`, provenance backlinks on
`SprintDetail` and `ArtifactDetail`.

Two things from phase 3 are open, honestly, not as defects — read them before
you touch merges or the activity view:

1. **The live cadence run has never happened.** No test can prove
   `ingests_since_lint` fires a lint pass on a real substrate — the suite never
   launches an agent, by design. It spends the human's Claude quota and needs
   their explicit go-ahead. It is still outstanding; do not assume it happened
   because the suite is green. If you get the go-ahead, point `COSCIENCE_REPO`
   at a **scratch copy**, never the live substrate.
2. **A human-accepted merge does not appear in the Activity list.**
   `Service.accept_wiki_merge` (`src/coscience/service.py:1820`) calls
   `merge_wiki_pages` directly and never appends to `state["runs"]` — only the
   beat-driven `_collect` path does (`wiki.py:343`, `:370`). This matches spec
   §11.3 as written (Activity is defined as what *runs* did), so it is a
   completeness question, not a bug: should the audit trail record human
   actions too? Left for whoever picks up phase 4 or a human to rule on — see
   §5 below.

Also worth knowing: `Service.merge_wiki_pages` is non-atomic — a write failure
partway through the loop over the rewritten pages leaves some pages changed and
no commit. This matches the existing shape of `delete_wiki_page`; it is an
inherited property, not a new risk phase 3 introduced.

## 1. What phase 4 has to build

Per the design's §10 and §15 row:

- **`wiki_graph.py`** — pure, no IO: parsed pages in, `{nodes, edges}` out.
  Nodes are pages of type `Concept`, `Entity`, `Synthesis`, excluding any page
  with `graph_excluded: true` (Source pages are therefore never nodes — this is
  already asserted by lint rule `src/is-concept`, do not re-derive it). Edges
  are typed (from `relations`, carrying `type`/`confidence`/`source`) and
  untyped (body markdown links between node pages, minus any pair already
  covered by a typed relation). Node metrics: in-degree, out-degree, `orphan`,
  connected-component `cluster`, plus `status`/`trust` for colouring. Cached at
  `.wiki/graph.json`, keyed by a digest over every page's `(path, mtime, size)`;
  a mismatch rebuilds.
- **An endpoint** serving that graph — `GET /api/programs/{pid}/wiki/graph` per
  the design's §11.1. Not present in `http_api.py` yet; nothing to wire around.
- **`WikiGraphView.tsx`** — rendering stays `@xyflow/react`, same as
  `LineageGraph`. Layout does **not** reuse `dagre`: the design is explicit
  that dagre is hierarchical and wrong for a knowledge graph, which has no root
  and is not a DAG. Add **`d3-force`** (not yet a dependency — check
  `frontend/package.json`, only `dagre` is there today) behind the existing
  `graphLayout.ts` seam, so `LineageGraph` itself is untouched.
- **Provenance backlinks** on `SprintDetail` and `ArtifactDetail` — a result or
  artifact version should be able to show which wiki pages cite it, the
  reverse direction of the `cited from` chips phase 2 built.

Start with `superpowers:brainstorming`, then a written plan. Do not start
implementing from this file — it is a status document, not a spec.

## 2. What already exists — do not rebuild these

Everything phases 1–3 built is done and tested. In particular, for anyone
tempted to touch merge or lint code while building the graph:

| Piece | Where |
|---|---|
| Merge planner (pure) | `wiki_merge.py` — `plan(winner, loser, others)` |
| Merge performer + commit | `Service.merge_wiki_pages` |
| Per-program merge policy | `Program.wiki_merge` (`models.py:207`), default `auto` |
| Merge proposal queue | `state["merge_proposals"]`, applied/queued in `wiki._handle_merges` |
| Refusal memory | `state["merges_refused"]` — checked before queueing or applying |
| Cleanup-after-merge marker | `merged_from` (frontmatter) → lint rule `page/unmerged-prose` |
| Audit trail | `state["runs"]`, `RUNS_KEPT = 50`, `Service.wiki_activity` |
| Maintenance view | `WikiLintView.tsx` — proposals, activity, filed reports, findings by rule |
| Agent lint mode + cadence | `wiki_prompts.render_lint`, `ingests_since_lint`, `wiki.lint_every()` |
| Quarantine retry | `Service.unquarantine_wiki` + `WikiView` banner |

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
  decision. It matters more now than when it was first deferred: reverting a
  merge commit sweeps back whatever else was dirty at the time.

## 4. Where the record lives

- `docs/knowledge-charter.md` — the charter. §2 status (phase 3's delivery and
  the live-cadence-run gap), §4 rules, §6 seams, §7 **decision log**, §8
  invariants. Read §7 before overturning anything; each row is a decision
  already argued, including the phase-3 rulings on merge authority, the
  `merged_from`/`page/unmerged-prose` cleanup loop, and `substrate.commit()`'s
  SHA-or-empty contract.
- `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — **binding.**
  When a plan and the spec disagree, the spec wins. §10 is the graph section,
  §15 is the phasing table.
- `docs/superpowers/plans/2026-08-21-program-wiki-phase-2.md` — phase 2's plan
  and execution record.
- `.superpowers/sdd/2026-08-27-program-wiki-phase-3/` — phase 3's plan, task
  briefs and reports (git-ignored SDD workspace; does not travel to another
  machine).
- `docs/knowledge/phase-1-record/` — phase 1's decision log, whole-branch
  review, deferred items and fix reports.

## 5. Open questions for the human

Carried forward from phase 3, plus one new one:

1. **Should a human-accepted merge appear in the Activity list?**
   `accept_wiki_merge` doesn't write to `state["runs"]` today (§0 above). Spec
   §11.3 defines Activity as what runs did, so this is arguably correct as
   built — but if the intent was "show everything that changed the wiki," it
   needs a decision before phase 4 builds more UI around `state["runs"]`.
2. **`RUNS_KEPT = 50`.** Carried from phase 3, unresolved: fifty runs is
   roughly a month at the current cadence. Say so before it needs changing —
   it is one constant, but it is the whole memory of what the wiki did to
   itself.
3. **The live cadence run** (§0.1) needs the human's go-ahead before it can be
   crossed off, on a scratch substrate copy.

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
