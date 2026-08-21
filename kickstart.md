# Kickstart — program wiki

You are picking up phase 1 of the program wiki on branch `feat/program-wiki`
(HEAD `bd9622f`, 1029 tests green). The code is written and reviewed. It is not
merged, not deployed, and three review findings are open.

## Read these, in this order

1. `docs/knowledge/NEXT.md` — **the running order.** What to do, in sequence.
2. `docs/knowledge/phase-1-record/open-fix-brief.md` — your first job, ready to execute.
3. `docs/knowledge/phase-1-record/final-review.md` — why those three fixes exist.
4. `docs/knowledge-charter.md` §4, §6, §8 — the rules, the seams, the invariants.
5. `docs/superpowers/specs/2026-08-20-program-wiki-design.md` — the binding authority when anything disagrees.

If you need to know *why* something is the way it is before changing it:
`docs/knowledge/phase-1-record/decision-log.md`. Every `Ruling:` line is a
decision already made, with its cost if wrong.

## Start here

Land the three Important fixes in `open-fix-brief.md`, then run one scoped
re-review of that diff. Everything after that is in `NEXT.md`.

## Before you touch anything

- **Never `git add -A`.** These are someone else's in-flight files and must stay
  out of every commit: `frontend/src/styles.css`, `frontend/src/views/ProgramDetail.tsx`,
  `frontend/src/views/SprintDetail.tsx`, `frontend/src/components/PageToc.tsx`,
  `frontend/.coscience/`, `docs/_tmp_wiki/`. Stage explicit paths.
- **Never commit or push without Oleg's explicit approval.**
- Two repos: this one is code; the data lives in a separate substrate repo at
  `COSCIENCE_REPO`.
- Tests: `~/venvs/coscience/bin/python -m pytest` — plain `python` is not on PATH.
- The live end-to-end agent run and any deploy need Oleg's explicit go-ahead;
  they spend his Claude quota (was at 96% of the weekly allowance on 2026-08-20).
