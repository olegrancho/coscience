# Phase 3 final findings — fix report

All nine findings (F1-F4, F6-F10; F5 deferred by design) addressed. Backend
1214 tests pass, frontend 185/19-relevant pass, `tsc --noEmit` clean,
`npm run build` clean.

## F1 — `_relink` never applied to `merged.body`

**File:** `src/coscience/wiki_merge.py`

`plan()` now runs `merged.body = _relink(body, loser, winner)` after
`_merge_notes`/`_merge_bodies`, so a markdown link or `[[wikilink]]` the
*winner* already held to the loser is rewritten too, not just links inside
`others`. Matches spec §9.1's body-links row, which is unconditional.

**Test:** `tests/test_wiki_merge.py::test_the_winners_own_body_links_to_the_loser_are_rewritten`
— winner body contains `[job lease](/concepts/b.md)` and `[[b]]`; asserts
both are gone from `out.winner.body` and both now point at `/concepts/a.md`
/ `[[a]]`.

**Break used to verify:** ran the test against the pre-fix code (i.e. `plan()`
without the added `_relink` call) — failed with the link/wikilink still
present, exactly the "dead link + dead wikilink" scenario in the finding.
Confirmed, then applied the fix.

## F2 — loser's preamble (or headless body) discarded

**File:** `src/coscience/wiki_merge.py`

Added `_preamble(body)` — text before the first `# ` heading, or the whole
body if there is none. `_merge_bodies` now folds it into the winner's
`# Definition` section (the schema's own first section, spec §6) *after* the
existing per-heading loop, reading against the already-updated `body` rather
than the original `winner` (first attempt clobbered the preamble because it
read `winner.section("Definition")` before the loop's own edit to
"Definition" overwrote it — caught by my own test, fixed by reordering).

**Tests:**
- `test_a_loser_with_no_heading_at_all_is_not_discarded`
- `test_a_losers_preamble_before_the_first_heading_is_carried_over`

**Break used to verify:** these are literally the pre-fix behaviour — ran
them against the original `_merge_bodies` (no `_preamble` call at all) and
both failed with the preamble text simply absent from `out.winner.body`.
Confirmed, then applied the fix.

## F3 — `_names_a_source` doesn't normalise the `.md` spelling

**File:** `src/coscience/wiki.py`

`_names_a_source` now reads `wiki_store.read_page(substrate, program_id,
f"{_strip_md(path)}.md")`, mirroring `merge_wiki_pages`'s normalisation
(`service._strip_md`), instead of passing the agent's raw proposal path
straight through.

**Test:** `tests/test_wiki_merge_beat.py::test_a_source_page_named_without_the_md_suffix_is_refused_under_propose`
— proposes `{"loser": "sources/result-r1", ...}` (no `.md`) under `propose`
policy; asserts it lands in `merges_refused`, not `merge_proposals`.

**Break used to verify:** ran against the pre-fix `_names_a_source` (raw
`wiki_store.read_page(substrate, program_id, path)`) — failed because the
pair was queued into `merge_proposals` instead of refused, exactly the
probe in the finding ("QUEUED under propose").

## F4 — `_handle_merges` catches bare `Exception`

**File:** `src/coscience/wiki.py`

Narrowed `except Exception` to `except (NotFoundError, ValueError)` in
`_handle_merges`, mirroring `Service.accept_wiki_merge`'s existing split.
Anything else (OSError, CalledProcessError from a concurrent
`.git/index.lock`, etc.) now propagates instead of permanently blacklisting
the pair. It propagates out through `_collect` → `beat()`, which is already
caught by `Dispatcher._wiki_beat`'s catch-all (`wiki.py` runs inside
`wiki_store.state_guard`, which only calls `save_state` on a clean exit — so
an unexpected error also means this beat's state mutations, including the
earlier `state["run"] = None`, are not persisted; the next beat will find
`run` still set and retry collection, which is the intended "let it be
retried" behaviour rather than a silent requeue trick).

**Test:** `tests/test_wiki_merge_beat.py::test_a_non_judgement_error_does_not_blacklist_the_pair`
— monkeypatches `Service.merge_wiki_pages` to raise `OSError("index.lock")`,
calls `wiki._handle_merges` directly, asserts `pytest.raises(OSError)` and
that `merges_refused` was never populated.

**Break used to verify:** ran against the original bare `except Exception`
— failed with "DID NOT RAISE OSError" (the exception was swallowed and the
pair silently blacklisted).

## F6 — `_next_merge_id` reuses ids once a proposal leaves the pending queue

**File:** `src/coscience/wiki.py`

Added a monotonic `state["merge_seq"]` high-water mark, incremented and
persisted every time an id is issued, in addition to the existing max-over-
pending scan (kept so any pre-existing queue entries above the current
counter still push it forward). Mirrors `_next_run_id`'s reasoning exactly.

**Test:** `tests/test_wiki_merge_beat.py::test_a_merge_id_is_never_reused_once_its_proposal_is_accepted`
— queues `m0001`, accepts it (removing it from the queue via
`Service.accept_wiki_merge`), queues an unrelated pair, asserts the new id
is not `m0001`.

**Break used to verify:** ran against the original `max()`-over-pending
implementation — failed with `'m0001' != 'm0001'` (the id was reused the
moment the queue emptied), exactly the two-tabs scenario in the finding.

## F7 — vacuous test in `WikiLintView.test.tsx`

**File:** `frontend/src/views/WikiLintView.test.tsx`

`"says nothing is waiting when there are no proposals"` now does
`await screen.findByText(/nothing has run/i)` before asserting
`queryByTestId("merge-proposal")` is null, so the assertion runs after the
activity/lint queries have actually resolved instead of while the component
is still rendering `<Loader/>`.

**Break used to verify:** temporarily changed `WikiLintView.tsx` to render
every proposal *unconditionally* — `{true && (...)}` instead of
`{proposals.length > 0 && (...)}`, and mapped over
`[...proposals, {id: "fake", ...}]` instead of `proposals` alone (this is
the literal defect the finding names). With the awaited test this failed
(a `merge-proposal` element was found); reverted the injected defect
immediately after confirming.

## F8 — spec §9 rule table missing `human-notes/machine-written`

**File:** `docs/superpowers/specs/2026-08-20-program-wiki-design.md`

Added exactly one row after `human-notes/removed`:
`| \`human-notes/machine-written\` | error | no | content appeared under the
protected \`# Human notes\` section that was empty in the previous revision |`

**Note/disagreement:** while verifying, I found the table is also missing
`human-notes/footnote-definition` (`wiki_lint.py:449`, same commit b33be05 as
`machine-written`) — code implements 23 distinct rule ids against the spec's
now-22, not the 21-vs-22 the finding describes. The task instructions for F8
say to make exactly the one described change and nothing else, so I did not
add a second row; flagging it here as a real remaining gap rather than
silently expanding scope.

## F9 — spec §8.3 state example still shows the old `merged` array shape

**File:** `docs/superpowers/specs/2026-08-20-program-wiki-design.md`

Changed the one example block at (old) line 435 from
`"merged": [["concepts/job-lease.md", "concepts/compute-lease.md"]]` to
`"merged": [{"loser": "concepts/job-lease.md", "winner":
"concepts/compute-lease.md", "commit": "a1b2c3d"}]`, matching the dict shape
`_collect`/`_handle_merges` actually produce (`wiki.py:300`) and what §11.3
already describes. No other text touched.

## F10 — accepted-merge commit SHA discarded by the UI

**Files:** `frontend/src/api.ts`, `frontend/src/views/WikiLintView.tsx`

- `api.acceptWikiMerge`'s return type now declares `commit?: string`
  (the backend already sends it — `accept_wiki_merge` returns
  `{"applied": True, **out}` and `out` includes `commit`).
- `WikiLintView`'s `accept` mutation's `onSuccess` now shows a
  `notifications.show` toast with the short SHA when `applied && commit`,
  same pattern/library `ProgramDetail.tsx` already uses elsewhere for action
  confirmations. No new state, no spec change, per the finding's guidance.

**Test:** `WikiLintView.test.tsx::"surfaces the merge commit once Accept resolves"`
— spies on `notifications.show` (same pattern as
`ProgramDetail.test.tsx`, since the toast portal isn't mounted in this test
harness), mocks `acceptWikiMerge` to resolve with a `commit`, clicks Accept,
asserts the last `notifications.show` call's message contains the short SHA.

**Break used to verify:** ran the test against the pre-fix code (mutation
only calling `invalidateProposals`, api type lacking `commit`) — failed,
`notifications.show` was never called.

## Full verification

- Backend: `~/venvs/coscience/bin/python -m pytest` → **1214 passed**
- Frontend: `npx vitest run` → **185 passed** (19 in `WikiLintView.test.tsx`)
- `npx tsc --noEmit` → clean
- `npm run build` → clean (pre-existing chunk-size warning only, unrelated)

## Follow-up correction — F8 was two rows, not one

The coordinator re-verified with a line-wrap-tolerant regex and confirmed my
disagreement above: their original grep (`Finding\("[a-z-]+/[a-z-]+"`) is
single-line and missed `human-notes/footnote-definition`, whose id sits on
the line after the `Finding(` call at `wiki_lint.py:448-449`. Corrected,
doc-only, three edits:

1. **`docs/superpowers/specs/2026-08-20-program-wiki-design.md`** — added the
   second missing §9 table row:
   `| \`human-notes/footnote-definition\` | error | no | a footnote
   definition (\`[^id]: ...\`) sits inside the protected \`# Human notes\`
   section — move it to \`# References\` |`, matching what
   `wiki_lint.py:446-451` actually detects and the neighbouring rows' style.
2. **`docs/knowledge/NEXT.md`** — rule count 22 → 23, and replaced the
   `grep 'Finding("'` verification instruction (which produced the original
   undercount) with: count spec §9's rows now that the table is complete, or
   use a line-wrap-tolerant search.
3. **`docs/knowledge-charter.md`** — rule count 22 → 23, no other text
   touched.

Verified programmatically: extracting every `Finding(...)` rule id from
`wiki_lint.py` (line-wrap tolerant) against every `` `rule-id` `` in spec
§9's table now gives **23 vs 23**, empty set difference both directions.

Backend re-run after this doc-only change: **1214 passed** (unchanged from
before).
