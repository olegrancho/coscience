# Final review fix wave — three Important findings

Branch `feat/program-wiki`, base `bd9622f`. Full reasoning for each finding is in
`final-review.md`; read the "Important" section there before starting. The
controller's rulings below are binding where they narrow the reviewer's options.

Local commits on this branch are pre-authorised. Never push, never merge, never
`git add -A`. Stage only the paths each fix names. These carried-over paths must
never enter a commit: `frontend/src/styles.css`, `frontend/src/views/ProgramDetail.tsx`,
`frontend/src/views/SprintDetail.tsx`, `frontend/src/components/PageToc.tsx`,
`frontend/.coscience/`, `docs/_tmp_wiki/`.

Tests: `~/venvs/coscience/bin/python -m pytest` (plain `python` is not on PATH).
Suite is 1029 green at `bd9622f`; it must stay green.

---

## Fix 1 — escaped runs must be bounded by the failure counter

**File:** `src/coscience/wiki.py` (`_collect`, the `status == "ok" and escaped`
branch around lines 189-207). **Test:** `tests/test_wiki_beat_collect.py`.

An escaped run returns before `state["failures"] += 1`, so it neither records
its batch nor counts against `COSCIENCE_WIKI_MAX_FAILURES`. The batch stays
pending forever and a deterministic trigger re-launches it every eligible beat.

**Ruling: increment the SHARED `state["failures"]` counter on the escaped branch
and let escaped runs participate in quarantine at the same threshold.** Do not
add a separate escaped-only counter. The spec deliberately uses one shared
counter (see the Task 10 ruling in `progress.md`, which rejected per-kind
counters for the same reason); an escape is a failure of the run, and the whole
point of the threshold is that a batch which keeps going wrong stops being
retried. Keep the existing behaviour that an escaped run does NOT mark its batch
ingested — that part is deliberate and correct.

Mirror the `status == "failed"` branch's quarantine handling so that once the
threshold is hit the batch is quarantined and `failures` resets, exactly as the
failed path does. Preserve the existing return string and the
`state["last_run"]["status"] = "escaped"` marker — the dispatcher and the CLI
both surface them.

**Tests to add:** a second consecutive escape increments `failures` to 2; an
escape at `COSCIENCE_WIKI_MAX_FAILURES` quarantines the batch and resets
`failures` to 0; the existing
`test_writes_outside_the_bundle_block_recording` still passes unchanged
(`state["ingested"] == {}` and the escaped marker) — extend it to assert
`failures == 1` rather than writing a near-duplicate test.

## Fix 2 — reconcile `report.json`'s `objects` against the dispatched batch

**File:** `src/coscience/wiki.py` (`_collect`'s ingestion loop, around lines
209-216). **Test:** `tests/test_wiki_beat_collect.py`.

`batch` is the list the platform dispatched, not the agent's account of what it
covered. On any exit-0 run every dispatched object is marked ingested — including
on the *intended* path, since `_PROHIBITIONS` explicitly tells the agent "do
fewer objects well and say so in the report". Those uncovered objects never
resurface in `pending_objects()`. Silent, permanent under-ingestion.

**Ruling: reconcile — mark ingested only `set(batch) & set(report["objects"])`,
and leave the remainder pending.** Do not take the reviewer's alternative of
deleting the `objects` field and amending the spec: the field is already produced,
already documented in the prompt's schema, and the failure it guards against is
silent data loss that no lint rule can detect. Re-ingesting an object costs one
extra agent pass; missing one costs a permanent hole in the wiki that nothing
reports. Asymmetric — take the cheap side.

**Backward compatibility is mandatory.** Spec §8.6 says a missing `report.json`
still yields a status of `ok` with unknown counts. So:
- `objects` absent, or not a list, or the whole report absent/malformed → fall
  back to the current behaviour, marking the **entire** `batch` ingested. This
  preserves every existing test and the documented missing-report path.
- `objects` present and a list → intersect with `batch`. Ignore any oid in the
  report that was not dispatched (the agent does not get to expand its own
  mandate). Tolerate non-string entries without raising — OKF tolerance applies
  to this parser too.
- If the intersection is empty while `objects` was a non-empty list, still treat
  the run as `ok`; nothing is marked ingested and the batch stays pending. Do NOT
  route this to the failure counter — the honest "I covered nothing" report is not
  an error, and Fix 1 already bounds the genuinely stuck cases.

**Tests to add:** a report covering 2 of 4 dispatched objects marks exactly those
2 ingested and leaves the other 2 in `pending_objects()`; a report with no
`objects` key marks all 4 (regression guard for §8.6); a report whose `objects`
names an oid that was never dispatched ingests only the intersection; a report
with `objects: "r1"` (a string, not a list) falls back to the whole batch rather
than raising.

## Fix 3 — forbid the agent from committing

**File:** `src/coscience/wiki_prompts.py` (`_PROHIBITIONS`, around lines 53-66).

`_escaped()` and `_dirty_paths()` compare `git status --porcelain` before and
after the run, so they see uncommitted working-tree changes only. The agent runs
with `--dangerously-skip-permissions` and unrestricted bash; if it commits its own
changes, the working tree is clean at collect time and an out-of-bundle write
becomes invisible to the guard.

**Ruling: prompt-level fix only, in this phase.** Add an explicit prohibition on
running `git commit`, `git add`, `git stash`, `git checkout`, `git reset` or any
other command that alters the substrate's git state — the platform commits on the
agent's behalf, which is already how the seam is designed. Do NOT attempt a
sandbox, a tool allow-list, or a pre/post commit-SHA comparison here: that is a
real design change to the agent seam and it belongs in its own task with its own
review, not in a fix wave. Note the residual honestly in your report — a prompt
is guidance, not an enforcement boundary, and this leaves the guard defeatable by
a sufficiently confused agent.

Match the surrounding prohibitions' voice and formatting exactly. `--verbose` and
the rest of the launch line are not yours to touch.

**Test:** extend whichever existing `tests/test_wiki_prompts.py` case asserts on
`_PROHIBITIONS` content to cover the new line. Do not add a new test file.

---

## Done when

- All three fixes landed, each as its own commit staging only the files it names.
- New tests written for Fix 1 and Fix 2 as specified above; the prompts test
  extended for Fix 3.
- Full suite green (was 1029 at `bd9622f`).
- Report written to `final-fix-report.md` in this directory: what changed with
  `file:line`, the test output, any deviation from the rulings above and why, and
  the residual risk you are knowingly leaving behind on Fix 3.
