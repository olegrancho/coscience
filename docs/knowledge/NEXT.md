# Program wiki — pick up here

You are taking over phase 1 of the program wiki. This file tells you exactly
where the work stands and what to do next. Read it in full before touching
anything; it takes two minutes and will save you from re-deriving decisions that
are already made and recorded.

**Written:** 2026-08-20, by the instance that executed phase 1.
**Updated:** 2026-08-21 — Steps 1, 2 and 3 below are **done**. Read §0 first;
the rest of this file is kept for its reasoning, not as a to-do list.
**Branch:** `feat/program-wiki`, HEAD `4840531`. Pushed to `rancho` (backup only),
**not merged, not deployed.**

---

## 0. Where this actually stands (2026-08-21)

Phase 1 is **done**. 25 commits above `71c2aba`, suite green at **1037 tests**.

- **Step 1 done.** All three Important findings fixed — `fce6a48` (escaped runs
  count against the failure threshold; `report.json`'s `objects` reconciled against
  the dispatched batch) and `60a4c1d` (the agent is forbidden from changing git
  state). The scoped re-review then found that Fix 2 had made `objects`
  authoritative while the prompt never defined it; `4840531` fixes that. Full
  write-up with `file:line` and the residual risks:
  `phase-1-record/final-fix-report.md`.
- **Step 2 done.** Live end-to-end run performed and verified on 2026-08-21
  against a scratch copy of the dev substrate. 18 pages, lint 5 errors → 0 after
  `--fix`, opens as an Obsidian vault with links resolving. What it produced and
  what it revealed is recorded in `docs/knowledge-charter.md` §2.
- **Step 3 done.** The charter's status table and header are current.
- **Step 4 — merge and deploy — is the only step left, and both halves need the
  human's explicit go-ahead.**

**The live run exposed three defects; one is fixed, two are open.** All are
recorded in the charter's §2.

- *Fixed:* the wikilink autofix matched only a bare slug while agents write
  `[[dir/slug]]`, so all 19 were skipped and `--fix` reported success having
  changed nothing. `wiki_lint._wikilink_index` now accepts slug, path, and path
  without `.md`; re-verified on the run's own bundle, 19 warnings → 0.
- *Open:* the agent wrote into the protected `# Human notes` section and lint did
  not flag it.
- *Open:* `substrate.commit()` is repo-wide (`substrate.py:549` does `git add -A`),
  so a wiki run's commit records whatever else happened to be dirty.

Neither open item blocks the merge, but both want deciding before phase 2 builds a
UI on top.

Also worth one line on `main` eventually: `pyproject.toml:10` pins `mcp>=1.2`, and
`mcp` 2.0.0 removed `mcp.server.fastmcp`, so a fresh install of that extra breaks
collection on three test files.

---

## 1. State when this file was first written (2026-08-20, historical)

All 15 tasks of the phase-1 implementation plan are **implemented, committed and
individually reviewed**. 21 commits on `feat/program-wiki` above `71c2aba`, 32
files, +8640/-39, suite green at **1029 tests**. A whole-branch review then ran
and returned **CHANGES REQUESTED** with **3 Important findings**, which are
**not yet fixed** — that is your first job. Nothing is half-written: every commit
is complete and the suite passes at HEAD. The phase's own definition of done (one
live agent run producing a real bundle) is **unverified**, deliberately, because
it spends the human's Claude quota and needs their explicit go-ahead.

## 2. Do these, in this order

### Step 1 — the three-fix wave (do this first)

Read `docs/knowledge/phase-1-record/open-fix-brief.md`. It is a complete,
ready-to-execute brief for all three Important findings, with a binding ruling on
each and the tests to add. It was written to be handed straight to an implementer.

Summary of what's wrong and what was decided:

| # | Finding | Ruling |
|---|---|---|
| 1 | Escaped runs (agent wrote outside the bundle) bypass `state["failures"]` and quarantine entirely — `wiki.py:189-207` returns before the counter. The only unbounded failure mode in the state machine; a deterministically-escaping batch retries forever. | Increment the **shared** failures counter on the escaped branch; let escapes quarantine at the same threshold. No escaped-only counter. |
| 2 | `report.json`'s `objects` field has **no consumer**. `_collect` marks the whole *dispatched* batch ingested on any exit-0 run — but the prompt tells the agent "do fewer objects well and say so", so honestly-skipped objects are marked done and never resurface. Silent permanent under-ingestion. Spec §8.6 claims lint catches this; it does not. | **Reconcile**: ingest only `set(batch) & set(report["objects"])`. Absent/malformed/non-list `objects` falls back to the whole batch (preserves §8.6 and every existing test). |
| 3 | The escape guard diffs `git status --porcelain` before/after, so it sees uncommitted changes only. The agent has unrestricted bash and nothing forbids `git commit` — an agent that commits makes out-of-bundle writes invisible. | **Prompt-level only this phase**: forbid `git commit/add/stash/checkout/reset` in `_PROHIBITIONS`. A sandbox or tool allow-list is a real change to the agent seam and needs its own task. Record the residual risk honestly. |

Then run one scoped re-review of the fix diff against those three findings.

### Step 2 — the live end-to-end run (needs the human's explicit go-ahead)

This is Task 15's Step 7, deliberately held back. **Do not run it without asking.**
It launches a real `claude -p` process and spends the human's Claude quota.

When authorised, follow Step 7 of `.superpowers/sdd/.../task-15-brief.md` (also
reproduced in the plan). The non-negotiable part: point `COSCIENCE_REPO` at a
**scratch copy** of the substrate, never the live one. Verify by hand that the
bundle has `sources/`, `concepts/` and a populated `index.md`, that `log.md`
gained a line, that nothing outside `programs/<pid>/` changed, and that
`.wiki/state.json` lists the batch under `ingested`. Then open
`programs/<pid>/wiki` as an Obsidian vault and confirm the links resolve.

Land fixes 1 and 2 before this run. Fix 2 in particular changes what gets marked
ingested, and you want the first real run exercising the corrected path.

### Step 3 — Task 15's Step 8, the charter status update

`docs/knowledge-charter.md` §2 is **stale** — it still says "Nothing has been
implemented." Its status table needs phase 1 marked done and phase 2 marked next.
This was deliberately not done earlier because Step 8 is defined as "note anything
the end-to-end run revealed", so it cannot honestly precede Step 2 above. Update
it once the live run has happened, and record what the run actually taught you
(prompt weaknesses, batch size, model choice).

### Step 4 — merge and deploy (both need the human's explicit go-ahead)

Use `superpowers:finishing-a-development-branch`. Deployment is `bash scripts/deploy.sh`
and is **the human's call, separately**. Read the project `CLAUDE.md` deploy rules
first — notably that `npm run build` runs on every deploy even for Python-only
changes, or the dashboard shows a false version-drift warning.

## 3. Where the record lives

The SDD workspace at `.superpowers/sdd/2026-08-20-program-wiki-phase-1/` is
**git-ignored** — it does not travel to another machine, and `git clean -fdx`
destroys it. The four documents that matter have been copied into
`docs/knowledge/phase-1-record/`, which is tracked:

- **`decision-log.md`** — the full execution ledger. Every `Ruling:` line is a
  decision already made, with its reasoning and its cost-if-wrong. **Read this
  before overturning anything.** Rediscovering a ruling wastes your time;
  overturning one with new information is legitimate.
- **`final-review.md`** — the whole-branch review, 462 lines: 3 Important and 3
  Minor findings with failure scenarios, two cross-seam traces, and a verdict on
  every deferred item.
- **`deferred-items.md`** — 10 design questions and 7 coverage gaps found during
  execution and parked by ruling. The final review **ratified all 10** (6 correctly
  deferred, 4 not actually a problem) and overturned none. Treat them as settled
  for this phase unless you have new information.
- **`open-fix-brief.md`** — your Step 1.

Per-task briefs and reports (`task-N-brief.md`, `task-N-report.md`) stay in the
git-ignored workspace. They are recoverable context, not authority — the code is
committed and reviewed, and the rulings are in the decision log.

## 4. Orientation — read these, in this order

1. `docs/knowledge-charter.md` — the subsystem charter. §2's status table is stale
   (see Step 3), but §4 "Rules that will bite you", §6 "The seams", and §8
   "Invariants" are current and worth your time.
2. `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — **the binding
   authority.** When the plan and the spec disagree, the spec wins. §9 holds the
   20-rule lint table; §8.6 is the one place the spec is known to be wrong (see
   finding 2).
3. `docs/superpowers/plans/2026-08-20-program-wiki-phase-1.md` — the plan. Its
   Global Constraints section binds all work on this branch. Note: its prose says
   the linter has 19 rules; it has **20**. The spec's table is correct.
4. The code, in dependency order: `wiki_okf.py` (page model) → `wiki_store.py`
   (paths, IO, state) → `wiki_prompts.py` → `wiki_agent.py` (the only
   process-launching seam) → `wiki.py` (orchestrator, the beat state machine) →
   `wiki_lint.py` (20 rules + autofix) → `cli.py`'s `wiki` subcommand.

## 5. Constraints that will bite you

These are not style preferences. Each one has already caused or nearly caused a
problem in this work.

- **Two repos.** This repo is CODE. The data — programs, sprints, results,
  artifacts, and the wiki bundle itself — lives in a **separate git repo**, the
  substrate, pointed to by `COSCIENCE_REPO`. Code deploys never touch it.
- **Never `git add -A` on this branch.** These paths are **someone else's
  uncommitted work** carried over from `main` and must never enter a wiki commit:
  `frontend/src/styles.css`, `frontend/src/views/ProgramDetail.tsx`,
  `frontend/src/views/SprintDetail.tsx`, `frontend/src/components/PageToc.tsx`,
  `frontend/.coscience/`, `docs/_tmp_wiki/`. Stage explicit paths, always.
- **Never commit or push without the human's explicit approval** (project
  `CLAUDE.md`). Local commits on `feat/program-wiki` were pre-authorised for the
  plan's execution; that authorisation does not extend to pushing or merging.
- **`python` is not on PATH.** Use `~/venvs/coscience/bin/python -m pytest`.
- **The unit suite never calls a live LLM.** `wiki_agent` is injectable and tests
  pass a fake. If a test you write can reach `agent.launch`, it is wrong.
- **Runtime is Linux-only** (`/proc`, `os.killpg`, `fcntl`). Do not add Windows
  fallbacks.
- **Python ≥3.11**: `X | None`, `StrEnum`, `from __future__ import annotations` at
  the top of every new module.
- **The seam rule.** Logic modules are pure with no IO; all substrate writes happen
  in the orchestrator `wiki.py`; the only process-launching seam is
  `wiki_agent.py`. `wiki_lint.run_lint` is the single explicit documented
  exception. Breaking this is how the tests stop being runnable offline.
- **Frozen vocabularies.** 12 relation types (`is_a, part_of, requires, enables,
  implements, exemplifies, measures, causally_precedes, contradicts, refines,
  replaces, extends`) and 5 page types (`Concept, Entity, Synthesis, Source,
  Question`). Adding one is a spec change, not an implementation detail.
- **OKF v0.2 conformance.** Parsers tolerate unknown types, unknown keys and
  broken links — they never raise and never drop unknown keys. Round-tripping a
  page must not silently discard frontmatter the writer did not recognise.
- **Do not touch the frontend in this phase.** The browse UI is phase 2.

## 6. Budget

The human's Claude usage was at **96% of the weekly allowance** when this was
written (resets Sunday 23:00). That is why the fix wave was not dispatched and why
the live run is waiting. Check before spending: `python3 ~/.claude/skills/usage/usage.py`.
Prefer one well-briefed dispatch over several exploratory ones, and hand large
artifacts to subagents as **file paths**, never pasted into the prompt.

## 7. If you are not on this machine

Host-specific paths, venv location and git remotes live in `local_setup.md`, which
points at a per-host file (`local_setup_<hostname>.md`). Those files are untracked
— if yours is not there, no setup is recorded for your machine and you should ask
rather than assume paths. `scripts/deploy.sh` assumes the production host's layout
and is not portable.

The branch itself is the source of truth and travels normally. What does not travel
is the git-ignored SDD workspace, which is why §3 exists.
