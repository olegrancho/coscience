# Program wiki — pick up here

**Written:** 2026-08-26, replacing the phase-1 handoff this file used to be.
**Branch:** `feat/program-wiki`, 41 commits above `main`. Pushed nowhere,
**not merged, not deployed** — the human has explicitly said *not yet*.
**Suites:** backend 1129, frontend 156, `tsc --noEmit` clean.

Read this, then `docs/knowledge-charter.md` §2. Between them they are the whole
picture; nothing important lives only in a commit message.

---

## 0. Where this stands

**Phases 1 and 2 are done.** The first shippable milestone — ingest + browse —
is complete and proven against a real bundle twice over: a live ingest in phase 1,
a human reading and curating what it produced in phase 2. Everything the two live
passes exposed is fixed except one item deferred by decision (the substrate's
repo-wide `commit()`, which is platform behaviour, not wiki behaviour).

**Phase 3 — lint runs — is the work in progress.** Its scope is smaller than the
spec's §15 row implies, because phase 1 built most of the machinery to get the
ingest beat working and the lint half came along with it.

## 1. What phase 3 actually has to build

Already on this branch, working, exercised by the suite — do not rebuild these:

| Piece | Where |
|---|---|
| Agent lint mode | `wiki_prompts.render_lint`; `wiki.beat` launches `kind="lint"` |
| The machine report handed to that run | `wiki._lint_report` → `wiki_lint.run_lint(fix=True)` |
| The cadence | `ingests_since_lint` in `_collect`, threshold `wiki.lint_every()` |
| Filing the agent's summary | `wiki._file_lint_report` → `.wiki/lint/<date>.md` |
| Quarantine retry | `Service.unquarantine_wiki`, its endpoint, the `WikiView` banner |
| Forcing a lint run by hand | `Service.run_wiki(kind="lint")`, "Lint now" in the header |

What is missing:

1. **The report UI.** `GET /programs/{pid}/wiki/lint` and `api.wikiLintReport`
   both exist and are **wired to nothing**. The header shows `lint NE / NW`, so a
   reader learns that findings exist and never what they are. There is also no way
   to read the filed `.wiki/lint/<date>.md` summaries from the dashboard.
2. **Proof the cadence fires on a real substrate.** No test can give this — the
   suite never launches an agent, by design. It needs a live run, which spends
   quota and therefore needs the human's go-ahead.
3. Whatever the design's §9 lint table promises that the implemented rules do not
   yet deliver. `wiki_lint` now carries **22** rule ids — the spec's 20 plus the
   two `human-notes/` rules added in `b33be05`. Diff the table against the code
   before planning rather than trusting either prose count; both this file and the
   phase-1 plan have been wrong about it.

Start with `superpowers:brainstorming`, then a written plan. Do not start
implementing from this file — it is a status document, not a spec.

## 2. Rules that still bite

- **Two repos.** This one is CODE. The data — programs, sprints, results,
  artifacts, and the wiki bundle — is the **substrate**, a separate git repo at
  `$COSCIENCE_REPO`. Code deploys never touch it.
- **Never commit or push without explicit approval.** Every time.
- **Stage explicit paths, never `git add -A`.**
- **`python` is not on PATH.** Use `~/venvs/coscience/bin/python -m pytest`.
- **The unit suite never calls a live LLM.** `wiki_agent` is injectable and tests
  pass a fake. A test that can reach `agent.launch` is wrong.
- **`@testing-library/user-event` is not a dependency.** Use `fireEvent`; the
  behaviour differs (per-keystroke vs one change event) and installing it would
  disturb `node_modules`.
- **Scope frontend assertions.** `getByText` in a three-pane view inside a page
  full of counts is a coin flip — query by role, title, or a scoped element.
  Every phase-2 test defect was this one mistake.
- **Linux-only runtime** (`/proc`, `os.killpg`, `fcntl`). Python ≥3.11.
- **Frozen vocabularies.** 12 relation types, 5 page types. Adding one is a spec
  change, not an implementation detail.
- **`scripts/deploy.sh` does not run on Avatar** — the uv venv has `pip3`, no
  `pip`. Start the server by hand; check `local_setup_avatar.md`.

## 3. Where the record lives

- `docs/knowledge-charter.md` — the charter. §2 status and phase-3 gap, §4 rules,
  §6 seams, §7 **decision log**, §8 invariants. Read §7 before overturning
  anything; each row is a decision already argued.
- `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — **binding.** When
  the plan and the spec disagree, the spec wins. §9 is the 20-rule lint table,
  which is what phase 3 is about.
- `docs/superpowers/plans/2026-08-21-program-wiki-phase-2.md` — phase 2's plan and
  its execution record, including what the plan got wrong and what it cost.
- `docs/knowledge/phase-1-record/` — phase 1's decision log, whole-branch review,
  deferred items and fix reports. The SDD workspace it was copied from is
  git-ignored and does not travel.

## 4. Budget

Check before spending: `python3 ~/.claude/skills/usage/usage.py`. A live wiki run
is ~6 minutes of wall clock and a real slice of the window. Prefer one
well-briefed dispatch over several exploratory ones, and hand large artifacts to
subagents as **file paths**, never pasted into a prompt.

## 5. If you are not on this machine

Host paths, venv and remotes live in `local_setup.md`, which points at
`local_setup_<hostname>.md`. Those are untracked — if yours is missing, no setup
is recorded for your machine, so ask rather than assume. The branch itself travels
normally; the git-ignored SDD workspace does not, which is why §3 exists.
