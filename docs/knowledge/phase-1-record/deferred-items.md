# Deferred items carried to the final whole-branch review

Every item below was found during a per-task review, judged out of that task's
scope, and parked by ruling. They are listed here so the final reviewer does not
have to rediscover them — and so it can say which ones actually matter now that
the whole branch exists. **These are not new findings to re-litigate; they are
questions with a known answer that the final review either ratifies or overturns.**

Rank them: which of these would you fix before this branch merges, and which are
correctly deferred to a later phase?

## Design questions (the ones that actually need a verdict)

1. **Uncommitted autofix writes in `beat()`'s launch half** — `wiki.py:_lint_report`
   calls `wiki_lint.run_lint(substrate, program.id, fix=True)`, which writes
   autofixed pages to the substrate, and then `agent.launch(...)` runs with no
   `substrate.commit(...)` in between. A lint run that is launched but never
   collected (crash, host reboot, quarantine) leaves those writes uncommitted
   indefinitely. Both the Task 14 implementer and its reviewer independently
   confirmed the behaviour and independently judged it bounded: the writes stay
   inside the bundle, the next successful collect sweeps them into its commit, and
   the `dirty_before` reordering stops them being misclassified as escaped writes.
   Two resolutions were identified — give those writes their own commit before
   launch, or accept "eventually committed, never lost, occasionally comingled with
   the agent's own changes" as intentional. **Which?**

2. **`rel/unknown-type` is overloaded** (`wiki_lint.py`) — it reports both an
   out-of-vocabulary relation *type* and an out-of-vocabulary *confidence* value,
   so `render_report` groups two unrelated problems under one heading. Splitting
   them needs a 21st rule code; the spec's §9 table freezes the set at 20.
   Is the overload acceptable, or does the vocabulary need to grow?

3. **`_is_linked`'s bare-slug match** (`wiki_lint.py`, from the Task 12 fix) — the
   orphan check treats `target == page.slug` as a link for *all* link targets, not
   only wikilink-shaped ones. A markdown href that happens to equal a page's
   filename stem (`[t](a)`) would also suppress an `page/orphan` finding. This is a
   broadened false-negative surface only; no test exercises it either way.

4. **`previous_bodies` spawns one `git show` per page** (`wiki_store.py`) — a lint
   run costs N subprocesses. Bundles are small in phase 1; a single
   `git cat-file --batch` would be premature. Still on the hot path of every lint.

5. **The usage gate is evaluated before the pending check** (`wiki.py`, inherited
   verbatim from the plan) — a live deployment shells out to the usage script every
   beat, per enabled active program, even with nothing to ingest.

6. **`agent.launch()` (a subprocess spawn) runs inside `state_guard`'s repo-wide
   flock** — serialising wiki launches across all programs in a dispatch cycle.
   Inherited from the plan's lock design.

7. **`state_guard` is not reentrant** — self-deadlock if nested. Inherent to the
   `artifacts._lock_guard` idiom it copies; harmless under the beat's single-writer
   design, but the beat is the only thing keeping it harmless.

8. **Every clean `state_guard` exit writes `state.json`** even when nothing mutated
   (write-on-no-change), exactly as the brief specified.

9. **Wiki Claude runs do not increment `report.beaten`** (`dispatcher.py`), which
   `cli.py` feeds to `LoopStatus` as `claude_calls` — so wiki runs are missing from
   the rolling "1h: N claude" counter. The run *is* visible instantaneously in the
   beat's status line; only the numeric accounting under-counts. Flagged for a
   phase-2 look.

10. **`wiki_model` is written to frontmatter on every save** once resolved to
    `DEFAULT_MODEL`, so an unrelated re-save adds the key to a legacy `program.md`.
    Pre-existing `pm_model` behaves identically — a shared follow-up if
    byte-identical persistence ever has to be guaranteed.

## Test-coverage gaps (each verified by hand, none covered by an assertion)

- No regression test for unknown-key survival in `state.json` (OKF tolerance is a
  binding spec requirement, and this is the one place it is only manually verified).
- No test drives `collect()`'s garbage-`agent.exit` branch
  (`except (ValueError, OSError): code = 1`).
- No test asserts `default_usage_gate`'s wired parameters (threshold / weekly /
  `fail_open` / `repo_root`); verified by manual cross-check against `worker.py`.
- No test covers the `[:200]` truncation boundary on the dispatcher's wiki error
  line — the error test's message is short, so the test passes identically if the
  slice is deleted.
- No test asserts the artifact-branch slug/resource format in `program_objects`;
  only the result branch is covered.
- `render_lint`'s prohibitions block has no direct test (it shares `_PROHIBITIONS`
  with `render_ingest`, which is tested).
- `run_dir` has no direct test (one-line path composition).

## Cosmetic (listed only so the reviewer does not spend a finding on them)

- `agent_stream.py`'s docstring forward-references the wiki-agent caller.
- `StreamResult.usage` is typed as a bare `dict` rather than `dict[str, Any]`.
- `tests/test_wiki_objects.py` has an unused `import time` (carried from the brief).
- `object_hash`'s docstring opens with an embedded empty-string literal.
- `pm_beat_line` marks failures with uppercase `ERROR`; the wiki line uses
  lowercase `wiki: error — `.
- `task-4-report.md` claims `wiki_store.py` is 226 lines; it was 202 at the time.

## Known incomplete, by ruling — not a finding

Task 15's **Step 7** (the live end-to-end run against a scratch copy of the real
substrate) and **Step 8** (the `docs/knowledge-charter.md` §2 status update, whose
content is defined as "what the end-to-end run revealed") were deliberately NOT
executed. Step 7 launches a real `claude -p` process against production-derived
data and spends the human's Claude quota, which is a side effect outside the
worktree that requires explicit human authorisation. Step 8 cannot honestly precede
it. So: `docs/knowledge-charter.md` §2 still shows phase 1 in progress, and the
phase's stated definition of done — "one `--once` beat on a real program produces a
bundle that opens as an Obsidian vault" — is **unverified**. Do not report either
as a defect; do report anything in the code that would make that run fail.
