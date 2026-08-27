# SDD ledger — plan: docs/superpowers/plans/2026-08-27-program-wiki-phase-3.md

Branch: feat/program-wiki (NOT main — phases 1 and 2 live here; no new worktree).
BASE at start: 30180c8
Spec: docs/superpowers/specs/2026-08-20-program-wiki-design.md (read; binding).
Autonomous mode ON — commits pre-approved on this branch, no push/merge/deploy.

## Pre-flight scan

### Shared files between tasks

| Tasks | File | What each does | Finding |
|---|---|---|---|
| 3, 5 | `wiki_store.py` | 3: bundle CLAUDE.md template. 5: DEFAULT_STATE | Disjoint regions. Clean. |
| 4, 6, 7 | `service.py` | 4: merge_wiki_pages + _strip_md. 6: proposals/activity/policy + wiki_summary. 7: wiki_lint_report | Disjoint; 6 depends on 4. Order 4→6→7 respects it. Clean. |
| 5, 8 | — | 5: Program.wiki_merge. 8: reads it via Service | Clean. |
| 9, 12, 13 | `WikiView.test.tsx` | 9: mock fields. 12: findings tests. 13: policy tests | Sequential appends. Clean. |
| 10, 11, 12 | `WikiLintView.tsx` | Built incrementally: shell → proposals → findings | By design. Clean. |
| 12, 13 | `WikiView.tsx` | 12: findings strip (centre pane). 13: policy select + link (header) | Disjoint regions. Clean. |

### Interface agreement (produces → consumes)

| Producer | Consumer(s) | Interface | Finding |
|---|---|---|---|
| 1 | 4 | `plan(winner, loser, others) -> MergePlan{winner, rewritten, loser_path}` | Names match. Clean. |
| 4 | 5, 6, 8 | `merge_wiki_pages(pid, winner, loser) -> {winner, loser, rewritten}` | Clean. |
| 5 | 6, 8, 9 | state keys `merge_proposals`/`merges_refused`/`runs`; `RUNS_KEPT`; `Program.wiki_merge` | Clean. |
| 6 | 8 | 5 service methods | Clean. |
| 7 | 8, 10 | `wiki_lint_report()["reports"]` | Clean. |
| 8 | 9 | 6 routes | Clean. |
| 9 | 10-13 | `WikiRun.merged` = `[loser, winner]` pairs | Order pinned in both. Clean. |
| 2 | 12 | `page/unmerged-prose` finding | Rendered by the generic findings grouper. Clean. |

### Task-internal agreement

| Task | Finding |
|---|---|
| 1 | **DEFECT** — the given `plan()` mutates its `others` inputs in place. See Ruling 1. |
| 2 | Clean (entry point corrected to `wiki_lint.lint` / `_page_rules` before execution). |
| 3 | Clean — `_HOUSEKEEPING` is rendered into both `render_ingest` and `render_lint`, verified at `wiki_prompts.py:178,219`, so pinning the shape there reaches both documents as the tests assert. |
| 4 | Clean (test shells out to git; `Substrate` has no `git()` helper — verified). |
| 5 | **DEFECT** — `propose` mode can queue the same pair on every run. See Ruling 2. **HAZARD** — see Ruling 3. |
| 6 | Clean. |
| 7 | Clean. |
| 8 | Clean — new GETs registered before the `{slug:path}` catch-all, as the existing comment at `http_api.py:680` requires. |
| 9 | Clean. |
| 10 | Clean — React Router v6 ranks static above splat, so `/wiki/lint` wins over `/wiki/*` regardless of order; the plan's ordering instruction is belt-and-braces, not load-bearing. |
| 11 | Clean. |
| 12 | **DEFECT** — adding an unconditional `getWikiLint` query to WikiView breaks existing tests that never mocked it. See Ruling 4. |
| 13 | Clean. |
| 14 | Clean. |

## Rulings

Ruling 1 (Task 1): `wiki_merge.plan` must NOT mutate the pages passed in `others`;
it builds new Page objects for `rewritten`. The plan's own code sample mutates them.
Why: `wiki_merge` is declared pure, and a caller that reads `others` after calling
`plan` would silently see rewritten bodies. A reviewer would flag it and be right.
Cost if wrong: a copy per rewritten page — negligible, bundles are tens of pages.

Ruling 2 (Task 5): `_handle_merges` skips a pair that already sits in
`state["merge_proposals"]`, not only one in `merges_refused`. Why: as written, a
`propose` program queues a fresh proposal for the same pair on every lint run,
because nothing records that it is already pending — the human would face the same
merge five times. Cost if wrong: a genuinely re-proposed pair (e.g. after the first
was accepted and the pages diverged again) is skipped until the queue is cleared.

Ruling 3 (Tasks 4, 5): `Service.merge_wiki_pages` must NOT open
`wiki_store.state_guard`. Why: `_handle_merges` runs inside `beat`'s already-held
exclusive flock, and `fcntl.flock` on a second fd in the same process deadlocks
rather than re-entering. This is a live hazard, not a style point — carried into
both dispatches. Cost if wrong: the dispatch loop hangs on the lock forever, and
the symptom is a wiki that silently stops beating.

Ruling 4 (Task 12): the `getWikiLint` mock goes in `WikiView.test.tsx`'s shared
`beforeEach`, not only in the new tests. Why: an unconditional query in the view
makes every existing WikiView test hit unmocked fetch. Cost if wrong: none — it is
where the other shared mocks already live.

## Progress

Task 1: implemented (commit 42dfa3a, 18 tests: 17 from brief + 1 the implementer
  added for Ruling 1 — none of the brief's own tests would have caught the sample
  code's in-place mutation). Task review dispatched.
Task 1: review — spec ✅; quality approved with 2 Important (both missing-regression-test,
  implementation traced correct), 2 Minor, 1 ⚠️.
Task 1: minor (deferred): copies are shallow one level — a nested mutable inside
  extra/generated/Relation.extra is still shared with the caller. Flat scalars in
  practice, so low risk.
Task 1: minor (deferred): _relink does not rewrite a link carrying a fragment
  (/concepts/b.md#section). Not in the spec table; agents do not currently write them.
Task 1: Ruling: the ⚠️ "cannot verify merged_from round-trip from diff" is a real gap
  but a cheap one — resolved by folding a render→parse test into fix round 1 rather
  than deferring it to Task 4's disk-level test. Cost if wrong: one redundant test.
Task 1: fix round 1/5 dispatched (3 findings: negative link-rewrite test,
  tags/generated aliasing test, merged_from round-trip test).
Task 1: fix commit e3f8450, 22 tests. Scoped re-review dispatched (haiku — tests-only diff).
Ruling (budget): the 5h window moved 36%->54% on Task 1 alone, so 14 one-per-task
  dispatch pairs will not fit one window. Adapting per the skill's own guidance:
  (a) batch small same-shape tasks into single dispatches — Tasks 2+3 first
  (both are small, independent, complete-code edits touching disjoint files);
  (b) cheapest tier for transcription-plus-test implementers and small scoped
  re-reviews; (c) mid-tier for integration tasks, most capable only for the final
  whole-branch review. Cost if wrong: a batched review is one gate, so a finding in
  one half delays the other; acceptable at this task size, and I will not batch
  anything that needs its own judgement or its own review surface.
Task 1: fix round 1/5 (3 addressed, 0 open; commits 42dfa3a..e3f8450)
Task 1: complete (commits 30180c8..e3f8450, review clean)
Tasks 2+3: BASE e3f8450, batched dispatch.
Tasks 2+3: implemented (c5b1167 lint rule, 6 tests; ed12c84 prompts, 5 tests).
  Implementer flagged one deviation: it reordered _HOUSEKEEPING's paragraphs and
  dropped inner quotes from render_lint step 4's `"merges"` so that the plan's
  proximity test (split on first literal "merges", scan 400 chars) would land on the
  shape example. Prompt prose bent to fit a brittle test — sent to review with that
  question put squarely. Batched review dispatched.
Budget: 5h at 65% after 3 tasks. ~10 points per batched task-pair. Will hit the 85%
  pause threshold within roughly two more batches; resume scheduled at that point.
Tasks 2+3: review — Task 2 spec ✅ quality Approved (no changes). Task 3 spec ✅,
  1 Important: the deviation was resolved the wrong way round — prompt prose was
  reordered to satisfy a brittle test, breaking correspondence with the JSON key
  order in the same block. Reviewer independently reached the same conclusion the
  controller had. Fix round 1/5 dispatched: restore brief's paragraph order, rewrite
  the test to assert on the shape block directly. Quote-removal half kept (it matches
  how `objects` is already referenced unquoted nearby).
Tasks 2+3: minor (deferred): cross-site phrasing drift — wiki_store.py:105 quotes
  `"merges"` where wiki_prompts.py:223 does not, and the bundle template omits the
  "nothing to propose, write []" line. Field names/types/examples identical, so no
  contract disagreement.
Tasks 2+3: fix commit 80a29d6, 14 tests. Scoped re-review dispatched.
Tasks 2+3: fix round 1/5 (1 addressed, 0 open; commits ed12c84..80a29d6)
Task 2: complete (commit c5b1167, review clean)
Task 3: complete (commits ed12c84..80a29d6, review clean)
Task 4: BASE 80a29d6, solo dispatch (integration + Ruling 3 deadlock hazard).
Task 4: implemented (commit d1aa5b3, 6 tests; full -k wiki 261 passed). Implementer
  confirmed by re-grepping the diff that merge_wiki_pages never touches state_guard
  or .wiki/state.json — Ruling 3 holds.
Task 4: PLAN DEFECT found by the implementer — the shared `substrate` fixture is a
  bare tmp_path with no git repo, so Substrate.commit is a no-op there and the
  brief's test_the_merge_is_its_own_commit_naming_both_pages would have passed
  vacuously against ANY implementation. It git-inits within that one test, citing
  precedent at tests/test_wiki_lint_sources.py::test_previous_bodies_reads_head.
  Sent to review for verification rather than accepted on the implementer's word.
  NOTE FOR LATER TASKS: any test asserting on substrate git state needs the same
  treatment — relevant to Task 5 (which asserts merges happen at collect).
Task 4: review dispatched. 5h at 74%.
Task 4: review — spec ✅, quality Approved, no findings at any severity. Reviewer
  independently verified all three fixture claims: the substrate fixture is non-git
  (conftest.py:90-91), Substrate.commit is a no-op without .git (substrate.py:546-548),
  the cited precedent is real and identical in shape (test_wiki_lint_sources.py:107-114),
  and the amended test now genuinely fails on a wrong commit message.
Task 4: backlog note (NOT a task-4 defect): merge_wiki_pages is non-atomic — a
  write_page failure mid-loop leaves some pages rewritten, the loser still present,
  and no commit. Identical shape to the pre-existing delete_wiki_page, so it matches
  the file's existing risk posture rather than adding to it; nothing commits, so the
  dirty tree is visible in git status and recoverable by hand. Atomic bundle writes
  are worth their own task someday. Surfaced to the human at branch end.
Task 4: complete (commits 80a29d6..d1aa5b3, review clean)
Task 5: implemented (commit 53e5bb0, 10 tests; full suite green). DONE_WITH_CONCERNS.
  Four implementer concerns, all sent to review rather than accepted on its word:
  (a) it modified substrate.py, outside the brief's file list — PLAN DEFECT I missed:
      Program.wiki_merge had no frontmatter round-trip, so the setting silently
      reverted to "auto" on every reload. Failing toward the destructive policy is
      the worst direction, so this needed catching.
  (b) it kept a separate `queued` set rather than folding pending pairs into
      merges_refused — arguably better than my Ruling 2 implied, since "already
      pending" and "a human said no" are different facts with different lifetimes.
  (c) PLAN DEFECT: the brief's tests, run verbatim, never trigger a run at all —
      the lint cadence is never due, so beat() returns early and the tests would
      have proven nothing. Fixed in the test arrange step.
  (d) added a 10th test for the already-queued dedup (Ruling 2).
Task 5: review dispatched. 5h at 80% — pausing after this review lands.
Task 5: review — spec ❌ on ONE item, quality Approved with 1 Important.
  All four implementer judgement calls verified sound and kept:
  (a) substrate.py round-trip confirmed necessary — reviewer verified wiki_merge
      would revert to the destructive default on every Program reload without it;
      the fix mirrors wiki_model/wiki_enabled exactly (substrate.py:312,314,317).
  (b) separate `queued` set is the RIGHT call, better than my Ruling 2 implied:
      folding pending pairs into merges_refused would mark a pair "refused" before
      a human decided, and would need cleanup on accept/reject that nothing provided.
      Deriving `queued` fresh from merge_proposals each call self-corrects instead.
  (c) test-arrange fix verified — beat()'s launch gate never fires without
      ingests_since_lint >= lint_every(), so the brief's tests would have crashed on
      state["run"]["id"] being None. Matches the existing pattern in test_wiki_beat.py.
  (d) 10th test asserts the proposal list is unchanged, not merely its length.
  SPEC VIOLATION (my brief's defect, inherited verbatim): `propose` mode never
  refuses a Source page. Spec §9.1:657 requires refusal under BOTH policies; the
  refusal path only exists inside the auto branch's except-handler. A source-page
  pair would be queued, shown to a human as a real choice, and fail later.
Task 5: fix round 1/5 dispatched — validate the pair ahead of the policy branch so
  both policies refuse identically; 2 new tests.
Task 5: fix commit 39298b8, 12 tests, full suite green. Scoped re-review dispatched.
PAUSE: 5h window at 84% (resets 03:30 PDT, ~30 min out). One-shot resume scheduled
  for 03:37 PDT 2026-08-27 (cron job 382eb540, session-only). Resume prompt tells the
  next turn to read THIS ledger first and start at the first task with no completion
  line. All 14 briefs are already generated in this workspace — do not regenerate.
  Remaining: Tasks 6-14 (Service proposals/activity/policy, filed lint reports, HTTP,
  api.ts, WikiLintView, proposals UI, findings surfaces, merge-policy control,
  charter+NEXT.md), then the final whole-branch review on the most capable model.
Task 5: fix round 1/5 (1 addressed, 0 open; commits 53e5bb0..39298b8). Re-review
  confirmed the Source check sits at wiki.py:70-73 ahead of the policy split, the
  auto-path except backstop survives at :83, and _names_a_source reads bundle files
  only — no new lock surface inside beat's held flock.
Task 5: complete (commits d1aa5b3..39298b8, review clean)

=== RESUME HERE: Task 6 ===
Next: Task 6 (Service proposals/activity/policy), BASE 39298b8.
Tasks 1-5 are DONE — do not re-dispatch them. Verify with git log.

=== RESUMED 2026-08-27 03:37 PDT — 5h window reset to 0% ===
Verified: HEAD 39298b8 matches ledger, tree clean, Tasks 1-5 complete.
Ruling (batching, fresh window): remaining 9 tasks go out as 5 dispatches —
  6+7 (both Service, disjoint regions), 8+9 (HTTP routes + the api.ts client that
  consumes them, tightly coupled by route shape), 10+11 (WikiLintView shell then its
  proposals section, same file), 12+13 (both touch WikiView.tsx), 14 solo (docs).
  Cost if wrong: a batched review is one gate, so a finding in one half delays the
  other. Judged acceptable — none of these pairs needs independent judgement, and
  the pairs share a file or a contract, so reviewing them together is more coherent
  than splitting them.
Tasks 6+7: BASE 39298b8, batched dispatch.
Tasks 6+7: implemented (6e1d575 Service proposals/activity/policy, 8 tests;
  f805207 filed lint reports, 4 tests). Full suite green. Implementer reported no
  deviations and explicitly checked the vacuous-test pattern: neither brief asserts
  on commit history, all assertions read page/state content off disk, so both files
  can genuinely fail. Claim spot-checked by the reviewer rather than taken on trust.
  Batched review dispatched. 5h at 4% (fresh window, resets Thu 8:29).
Tasks 6+7: review — Task 6 spec ✅ Approved, Task 7 spec ✅ Approved (no issues).
  Locking trace verified correct: guard closes before merge_wiki_pages, second guard
  only in the except branch, no nesting (service.py:1817-1834). Concurrency verified:
  two accepts serialize on flock, the loser reloads fresh state and gets a clean
  NotFoundError — no double-apply. Pair storage order-insensitive at :1826 and :1832.
Task 6: Ruling: the reviewer's "uncaught-exception proposal loss" was labelled
  out-of-brief and follow-up-not-blocker; I am fixing it now rather than deferring.
  Why: if merge_wiki_pages raises anything outside NotFoundError/ValueError after the
  proposal is popped and saved, the proposal is neither applied, refused nor requeued
  — it vanishes with no trace. Spec §9.1 rules nothing gates an automatic merge but
  git, so "you can see what happened" IS this design's entire safety story, and a
  silent disappearance attacks it directly. An OSError is not a judgement that the
  merge was wrong, so the fix requeues and re-raises rather than recording a refusal.
  Cost if wrong: a slightly wider except clause and one more test; the risk of NOT
  doing it is an invisible loss in the exact place the human is meant to have sight.
Tasks 6+7: minor (folded into the same round, not a loop extension):
  test_rejecting_remembers_the_pair asserted sorted(refused[0]) == [a, b], which
  passes trivially because a < b already — the assertion's own sorted() did the work.
Tasks 6+7: fix round 1/5 dispatched (2 findings).
Tasks 6+7: fix round 1/5 (2 addressed, 0 open; commits f805207..c8b93dc). Re-review
  verified the requeue guard opens only after the first closes and never wraps
  merge_wiki_pages; merges_refused untouched on an unexpected error; the reject test
  now queues in reversed order so it genuinely exercises order-insensitive storage.
  Implementer verified the new OSError test fails without the fix by stashing it.
Task 6: complete (commits 39298b8..c8b93dc, review clean)
Task 7: complete (commit f805207, review clean)
Tasks 8+9: BASE c8b93dc, batched dispatch (HTTP routes + the api.ts client).
Tasks 8+9: implemented (dd547f7 HTTP endpoints, 9 tests; cf815db api.ts client,
  23 tests, frontend 160/160, tsc clean). Two implementer notes:
  (1) the brief's `.mock.calls.at(-1)!` does not typecheck under this repo's ES2020
      lib target — replaced with index arithmetic, no tsconfig or dependency change.
  (2) OBSERVATION WORTH KEEPING: the brief claimed WikiView.test.tsx's `summary` mock
      would stop typechecking once the payload grew. That is FALSE — every use is
      cast `as never`, which bypasses type checking entirely. The implementer calls
      it "a type-check version of the test-proves-nothing pattern". If true, the
      frontend mocks are unchecked against the real API types and could silently
      drift from what the server sends. Sent to review to confirm the reading and
      establish whether it is pre-existing (phase 2) or introduced here. NOT being
      refactored as part of this plan — out of scope — but it bears on how much
      tasks 10-13's frontend tests are actually worth, and the human should hear it.
Tasks 8+9: batched review dispatched. 5h at 14%.
Tasks 8+9: review — Task 8 spec ✅ Approved, Task 9 spec ✅ Approved. Two Important:
  (1) MY BRIEF WAS WRONG about the route-ordering hazard. The reviewer built a
      minimal FastAPI app registering the catch-all FIRST and got a clean 200:
      Starlette matches the literal `pages` segment, and /wiki/merges + /wiki/activity
      never share the `wiki/pages/` prefix, so NO registration order can swallow them.
      The test named ..._not_swallowed_by_the_page_catch_all passes either way — a
      test whose name lies. The real hazard in the existing :680 comment concerns
      /wiki/pages (literal) vs /wiki/pages/{slug:path}, which DO share a prefix.
      Fix: keep the test as a resolution guard, rename it, correct its rationale.
  (2) The stale-proposal path (200 + applied:false, spec §9.1's deliberate choice
      that a stale proposal is not a user error) has zero HTTP coverage. Correctness
      rests entirely on the service method's internal try/except.
  404 paths verified genuine: no NotFoundError exception_handler exists and TestClient
  raises server exceptions, so removing an except clause would error, not silently 404.
Tasks 8+9: `as never` observation CONFIRMED and dated — introduced at phase-2 commit
  6cd6c85, not by this plan. tsc cannot detect a missing, renamed or mistyped field
  in any mock cast that way, so a mock can drift silently from the real payload.
  Tasks 10-13 extend the same idiom, so their "tsc clean" carries the same caveat for
  any field not directly asserted on. NOT refactoring — out of scope, and widening the
  branch to cover a pre-existing suite-wide pattern is the human's call. REPORT AT END.
Tasks 8+9: fix round 1/5 dispatched (2 findings).
Tasks 8+9: fix round 1/5 (2 addressed, 0 open; commits cf815db..ad65862, tests-only).
  Test renamed to test_the_literal_merge_and_activity_routes_resolve with an honest
  docstring, and expanded to cover /wiki/activity too; stale-proposal test verified
  to reach the applied:False branch rather than 404-ing on an unknown id.
Task 8: complete (commits c8b93dc..ad65862, review clean)
Task 9: complete (commit cf815db, review clean)
Tasks 10+11: BASE ad65862, batched dispatch (WikiLintView shell, then proposals).
Tasks 10+11: implemented (e3596bf WikiLintView shell, 6 tests; 3d75f85 proposals,
  11 tests; suite 171/171, tsc clean). Implementer verified its tests can fail by
  deliberately breaking render and link order, then reverting.
  Route ordering (point 6): CONFIRMED EXPERIMENTALLY — React Router v6 ranks the
  static /wiki/lint segment above the /wiki/* splat regardless of declaration order.
  My instinct was right here, unlike the FastAPI case; a permanent regression test
  was added rather than leaving it on assertion.
Task 10/11: Ruling (section order): my own spec §11.3 and my own Task-11 brief
  CONTRADICT each other — the spec enumerates Proposals as the fourth section, the
  brief says render it above Activity. The implementer correctly followed the spec
  per the override rule. I am ruling the BRIEF right and amending the spec: proposals
  render FIRST when non-empty. Why: it is the only actionable section on the page —
  everything else is a record of what already happened — and a human arriving with
  merges awaiting decision should not scroll past two logs to find them. The spec's
  "fourth" was prose enumeration, not a layout mandate. Amending §11.3 so the
  contradiction does not outlive this plan. Cost if wrong: one section order, trivially
  reversible, and the empty case is unchanged since the section hides when empty.
Task 10/11: SPEC GAP I CREATED — §11.3 promises each merge row "links to the substrate
  commit that performed it", but the WikiRun payload I specified in Task 9 carries no
  commit reference (id/kind/status/at/pages_created/pages_updated/merged only). The
  implementer linked the surviving page instead, the best available with that data.
  This is load-bearing, not cosmetic: git-as-only-guard means the commit IS the undo,
  and substrate.commit() is repo-wide (deferred item), so finding the right commit by
  hand is harder than it sounds. Ruling pending the reviewer's independent read.
Tasks 10+11: review — both spec ✅ Approved. Two Important + one Minor:
  (a) the test named "...links to the substrate commit" asserts only textContent, so
      it would pass if the row were inert text; only the winner is a <Link> and
      neither fact is tested.
  (b) accept/reject tests assert the mutation was CALLED, never that the page changed
      afterwards — an accept leaving a stale proposal card on screen would pass.
      The invalidation code itself is correct (both queryKeys invalidated).
  (c) Minor deferred: no isError on the queries, no onError on the mutations — both
      match WikiView.tsx's existing idiom exactly, so pre-existing, not new.
  Reviewer independently reached MY ruling on section order (proposals first).
Task 10/11: fix round 1/5 dispatched (proposals-first + spec §11.3 amendment, plus
  the two test fixes).
Ruling (COMMIT-LINK GAP -> NEW TASK 15): the reviewer confirmed this is deeper than
  the frontend. substrate.commit() (substrate.py:551-557) returns None; no SHA is
  captured anywhere in the merge path, including Service.merge_wiki_pages, which
  commits and discards the hash. No remaining brief (12/13/14) touches it, so the gap
  would survive to the end of phase 3. Spec §9.1 says outright that the §8.3 audit
  trail and the §11.3 page "exist because of this choice, not beside it" — so shipping
  an audit trail that cannot point at the undo is shipping the promise unfulfilled.
  Adding Task 15 after Task 13 and before Task 14, so the charter describes a finished
  state: commit() returns its SHA (additive, no caller breaks since it returns None
  today), merge_wiki_pages passes it up, _collect and accept_wiki_merge record it,
  WikiRun carries it, the merge row links it. Cost if wrong: one extra task's work on
  a branch that is not merged; the risk of NOT doing it is an audit trail that names a
  merge it cannot help you undo.
Tasks 10+11: fix round 1/5 (3 addressed, 0 open; commits 3d75f85..5a74a45).
  Re-review confirmed: spec §11.3 amended and no other spec text touched; the link
  test now queries a real anchor and asserts its href; the accept test would fail if
  the component stopped invalidating (the second mock would never be consumed and the
  card would remain). Implementer verified each fix load-bearing by breaking it first.
Task 10: complete (commits ad65862..5a74a45, review clean)
Task 11: complete (commits 3d75f85..5a74a45, review clean)
Tasks 12+13: BASE 5a74a45, batched dispatch (findings surfaces + merge-policy control).
Tasks 12+13: implemented (94346c4 findings surfaces, 44 tests; eb69163 merge-policy
  control, 182/182 frontend, 959 backend, tsc + npm run build clean). Implementer ran
  8 falsification checks (3 task-12, 5 task-13), breaking code and confirming failure.
  Two deviations:
  (1) header test used the shared fixture's real wiki_merge value rather than the
      brief's literal example — correct, that is what I told it to do.
  (2) ANOTHER READ-PATH GAP I CREATED, same class as Task 5's substrate round-trip:
      wiki_merge was missing from the backend get_program payload and from the
      frontend Program type, so ProgramSettingsModal would always seed the control to
      "auto" no matter the program's real policy. Note the failure direction — a
      program actually set to "propose" would DISPLAY as "auto", i.e. the UI would
      tell a human that unattended destructive merging is on when they had turned it
      off. Fix is outside both briefs' file lists; sent to review to confirm it is
      correct AND minimal rather than accepted on the implementer's word.
Tasks 12+13: batched review dispatched. 5h at 49%.
Tasks 12+13: review — both spec ✅ Approved. The wiki_merge read-path fix VERIFIED
  true and minimal: service.py:529 mirrors the adjacent wiki_model/wiki_enabled line,
  api.ts:34 mirrors the same interface, and the reviewer checked for other affected
  call sites and found none. Per-page findings strip verified to filter on exact
  stripped-path equality (WikiView.tsx:317), not a loose substring, and to render
  nothing on a clean page. Policy control verified to lock during a run, matching
  ModelSelect's pattern textually.
  ONE Important — SIXTH instance of the plan's recurring pattern, and the subtlest:
  the "puts errors before warnings" test (WikiLintView.test.tsx:330-334) passes under
  a descending-count comparator, a no-op comparator, and even with NO sort call,
  because Array.sort is stable and the fixture's insertion order already matches the
  expected result. It only fails against a specifically ASCENDING comparator. The
  implementation is correct; the test would not notice if the severity term were
  deleted. The reviewer found this by tracing the comparator rather than trusting the
  implementer's falsification report — which is the lesson: falsification proves a
  test CAN fail, not that it fails for the RIGHT reason. The substituted break has to
  be the specific mistake the test exists to catch.
Tasks 12+13: fix round 1/5 dispatched — rebuild the fixture so severity and count
  DISAGREE (warn group first in insertion order and with a higher count), so only
  severity-primary ordering yields the expected result.
Tasks 12+13: fix round 1/5 (1 addressed, 0 open; commits eb69163..cf4d372, tests-only).
  Re-review computed the assertion under all three comparators: severity-primary
  passes, count-descending fails, insertion-order fails. Fixture now discriminating.
Task 12: complete (commits 5a74a45..cf4d372, review clean)
Task 13: complete (commits eb69163..cf4d372, review clean)
Task 15: BASE cf4d372, brief written by hand at task-15-brief.md (not from the plan).
Task 15: Ruling (SPEC WAS UNIMPLEMENTABLE): §11.3 promised each merge row "links to
  the substrate commit that performed it". There is no URL to link to — the substrate
  is a local git repo and on a real deployment has NO REMOTE (local_setup_avatar.md:39
  -40). My spec promised something that cannot exist. The honest design is to NAME the
  commit: short SHA shown, full SHA on hover, so the reader can `git -C <substrate>
  show <sha>`. Amending that one sentence of §11.3 as part of this task.
Task 15: Ruling (NEVER RETURN A STALE HEAD): substrate.commit() returns early with no
  .git and passes check=False to tolerate "nothing to commit", so there are two paths
  where no commit is made. commit() must return "" in those cases, never the previous
  HEAD — naming an unrelated commit as the one that performed a merge would send
  someone reverting the wrong thing, which is strictly worse than reporting nothing.
  Cost if wrong: a merge whose commit is genuinely unrecorded shows no SHA rather than
  a wrong one; that is the safe direction to fail in.
Task 15: review — spec ✅ Approved, no critical or important issues. All four
  commit() paths traced: no .git -> ""; zero-commit repo -> before is "" so the first
  commit is reported correctly; normal commit -> new SHA; nothing staged -> "".
  ~50 other call sites across the platform verified to discard the return value, so
  None->str is genuinely additive. accept_wiki_merge confirmed to carry the commit
  through too, not just the auto path. §11.3's two same-day amendments confirmed
  non-overlapping (5a74a45 ordering, 9867695 wording) with no relitigation.
Task 15: complete (commits cf4d372..9867695, review clean)
Task 15: Ruling (DECLINED to expand scope): the reviewer flagged that
  accept_wiki_merge never appends to state["runs"], so a merge a HUMAN accepts under
  the propose policy never appears in the Activity list at all. This is real, and I am
  NOT fixing it. Reasoning: spec §11.3 defines Activity as "what recent runs did,
  newest first, from state['runs']", and a human accept is not a run — so unlike the
  commit-SHA gap (where the spec promised something undelivered), there is no
  unfulfilled promise here. It is a genuine design question about whether the audit
  trail should record human actions alongside agent runs, which is new scope and the
  human's call, not mine. Recommending it rather than doing it. Cost if wrong: a
  human-accepted merge is findable only via git log, not via the dashboard — but the
  human who clicked accept already knows it happened, which is why this is a
  completeness question rather than a safety one.
Task 14: BASE 9867695, final task (charter + NEXT.md).
Task 14: complete (commit bfdf526, review folded into the final whole-branch review —
  docs-only task, and the final reviewer reads both files anyway).
  Measured truth: backend 1208 passed, frontend 184 passed, tsc clean.
Task 14: CORRECTION to an earlier ledger/charter claim of mine. I previously said
  wiki_lint carried 22 rule ids BEFORE phase 3. Wrong — an anchored grep on the
  Finding(" constructor gives 21 at base 30180c8 and 22 at HEAD, with exactly one
  added (page/unmerged-prose) and nothing lost. My earlier figure came from a looser
  grep that matched a non-rule quoted string. The docs as committed say "22 today",
  which is correct, and NEXT.md tells the reader to verify by grepping rather than
  trusting prose.
Task 14: PRE-EXISTING DRIFT FOUND (not introduced by this plan) — the spec's §9 rule
  table lists 21 rules; the code implements 22. The one missing from the table is
  `human-notes/machine-written`, added in commit b33be05 during the phase-2 fix wave
  and never written into the binding table. Nothing in the table is unimplemented.
  Folding this one-row addition into the final-review fix wave.
=== ALL 15 TASKS COMPLETE. Final whole-branch review next. ===

=== FINAL WHOLE-BRANCH REVIEW (opus) — verdict: NOT READY AS-IS ===
No Critical. 7 Important, 11 Minor. Locking verified CLEAN (all six state_guard sites
traced, no nesting, Ruling 3 holds). substrate.commit verified genuinely additive
across ~50 call sites. Frontend/backend contract verified by hand against real
payloads despite the `as never` hole — NO drift found.

FIX WAVE (dispatch ONE subagent with all of these):
F1 (Important) wiki_merge.py:117 — _relink is applied only to `others` (:141), never
   to merged.body. Spec §9.1's body-links row is unconditional. Confirmed by probe: a
   winner linking [job lease](/concepts/job-lease.md) and [[job-lease]] keeps both
   after the merge, then the loser is unlinked in the same commit — dead link + dead
   wikilink. Worse: link/wikilink is an AUTOFIX rule, so the next run_lint(fix=True)
   materialises [[job-lease]] into a real link to a deleted page — the platform
   writing a permanent link/broken into the page. Fix: relink merged.body too.
F2 (Important) wiki_merge.py:69 — _merge_bodies iterates only _headings(loser.body),
   so any loser content BEFORE the first `# ` heading, or a loser with no heading at
   all, is silently discarded and the loser is deleted in the same commit. DATA LOSS
   on the one operation declared irreversible-except-git, and stub pages (the usual
   merge losers) are the most likely to be heading-less. Fix: carry the preamble.
F3 (Important) wiki.py:244 vs service.py:1890 — _names_a_source passes the agent's RAW
   path to wiki_store.read_page (which needs `concepts/a.md`), while merge_wiki_pages
   normalises via _strip_md and accepts both spellings. Probe: "sources/result-r1"
   (no .md) -> refused under auto, QUEUED under propose. So Task 5's source-page fix
   closed only the .md spelling; §9.1's "refused under both policies" is still open for
   exactly the sloppy spelling _proposals was written to tolerate. Fix: normalise once
   at the boundary, or read f"{_strip_md(path)}.md".
F4 (Important) wiki.py:296-298 — _handle_merges catches bare Exception and appends to
   merges_refused. Task 6's fix wave deliberately split this in accept_wiki_merge
   (NotFoundError/ValueError -> refuse; anything else -> requeue and re-raise) and the
   reasoning was never carried back. Trigger is routine, not exotic: substrate.commit
   runs `git add -A` with check=True, so ANY concurrent git process holding
   .git/index.lock (the PM loop, another beat, a worker, a human running git status)
   raises CalledProcessError. Result: half-merged bundle on disk, pair PERMANENTLY
   blacklisted, and _collect's own commit at wiki.py:377 then sweeps the half-merge
   into a run-named commit — so §9.1's "each merge is its own commit and that commit
   is the undo" fails exactly in the failure case. Same shape without any error if the
   process dies between the merge commit and the state.json write (deploy.sh restarts
   loops routinely). Fix: mirror accept_wiki_merge's narrow except.
F6 (Important) wiki.py:234 — _next_merge_id takes max() over CURRENTLY PENDING
   proposals only, so ids are reused once a proposal leaves the queue (contrast
   _next_run_id at :64, which derives from last_run precisely to avoid this). Two
   tabs/users: tab A accepts m0001; a later run queues a DIFFERENT pair also as m0001;
   tab B, still showing the old card, clicks Accept and applies a destructive merge on
   a pair that human never saw. `busy` guards only within one component instance.
   Fix: monotonic high-water mark.
F7 (Important) WikiLintView.test.tsx:185-189 — EIGHTH vacuous test. No await, so the
   component is still rendering <Loader/> when queryByTestId runs; passes against an
   implementation that renders every proposal unconditionally. Fix: await a resolved
   section first.
F8 (must-fix doc) spec §9 rule table lists 21 rules, code implements 22. Missing row:
   human-notes/machine-written (error, not auto-fixable, wiki_lint.py:427, origin
   b33be05, pre-phase-3). Verified set difference is EXACTLY that one, both directions.
F9 (must-fix doc) spec §8.3's state example at :435 still shows the pre-Task-15
   "merged": [[loser, winner]] array shape. §11.3 was amended twice this phase; §8.3
   was left behind.
F10 (cheap, reclassified by the reviewer from "human's call" to should-fix)
   service.py:1800 already returns the merge commit SHA on the accept path and the
   HTTP route passes it through, but WikiLintView.tsx:66-67 discards it. So on the
   PROPOSE path — the one where a human personally authorises a destructive operation
   — the SHA Task 15 exists to capture reaches the browser and is thrown away.
   Surface it in the accept confirmation. No new state shape, no spec change.

DEFERRED WITH RULINGS (do NOT widen the wave for these):
D1: F5 from the review — a chained proposal set ({a<-b},{b<-c}) permanently refuses its
    own tail, because the first merge deletes b and the second raises NotFoundError on
    the now-missing winner. Real, but fixing it needs chain detection/retargeting,
    which is new design rather than a correction. Narrowing the except (F4) does not
    fix it. Ruling: defer to phase 4 with a note. Cost if wrong: an orphaned duplicate
    that can never be merged; visible as a persistent page/near-duplicate finding.
D2: the 11 Minors. Reviewer triaged them; none merge-blocking.
RE-LABEL (not fix): substrate.commit's repo-wide `git add -A` stays deferred, but the
    charter/NEXT.md notes must stop calling it merely untidy — phase 3 is the first
    thing that PROMISES a specific commit as an undo, so a merge commit that can
    contain a concurrent sprint's work is a known limit on the undo guarantee.

SEQUENCING RULING: do the fix wave BEFORE the human's live agent run, not after. F4 is
    most likely to fire under exactly first-live-run conditions (loops restarting, a
    human watching git status), and its symptom — permanent refusal + half-merged
    bundle + an audit entry saying nothing merged — is precisely the silent failure
    §9.1's design exists to prevent.

PAUSE: 5h at 93% (over the 85% threshold), resets 08:30 PDT. Resume scheduled 08:37.
NEXT ACTION ON RESUME: dispatch ONE fix subagent with F1,F2,F3,F4,F6,F7,F8,F9,F10 —
    not one fixer per finding — then ONE scoped re-review of the fix range, then
    adjudicate residuals, then report the rulings list to the human.

=== FIX WAVE (resumed 08:37 PDT, fresh window) ===
Fix wave complete: 4 commits f939215 (F1,F2), ce45df5 (F3,F4,F6), 9c691c4 (F8,F9),
  1d8692d (F7,F10). Backend 1214, frontend 185, tsc + build clean.
CORRECTION TO MY OWN VERIFICATION: the fixer reported F8's premise was wrong — the
  spec table was missing TWO rows, not one. It was right and I was wrong. My check
  used a single-line grep on `Finding("`, and human-notes/footnote-definition is
  emitted at wiki_lint.py:448-449 with the id on the line AFTER the constructor, so
  the grep never saw it. Re-verified with a wrap-tolerant regex: code 23 rule ids,
  spec table 22, missing = human-notes/footnote-definition, nothing unimplemented.
  Note this also means my EARLIER "correction" was the error: the original count of 22
  pre-phase-3 was right, and I revised it down to 21 on the strength of the bad grep.
  The fixer flagged rather than expanded scope, which is why this was caught at all.
  Sent back to the SAME fix agent (completing the wave, not opening a second one):
  add the missing table row, correct "22 rule ids" to 23 in charter + NEXT.md, and
  fix the NEXT.md instruction that told readers to verify with the grep that misled me.
