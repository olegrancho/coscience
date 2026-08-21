# SDD ledger — plan: docs/superpowers/plans/2026-08-20-program-wiki-phase-1.md

Spec: docs/superpowers/specs/2026-08-20-program-wiki-design.md (reachable, read).
Branch: feat/program-wiki (not a worktree — branch on the primary checkout, not main).

## Ruling: commit authorization (pre-flight)

Ruling: local commits on `feat/program-wiki` are authorized for the duration of this
plan's execution — the human explicitly chose subagent-driven execution, which is
defined as per-task commits with no human in the loop between tasks. No push, no
branch merge, no `git add -A`; each commit stages only the exact paths its task's
commit step names. The plan's Global Constraint "ask the human first, once per task"
is superseded for local commits only.
Why: the project rule exists to stop unreviewed history reaching a shared remote;
nothing here leaves the local branch, and every commit is reversible.
Cost if wrong: the human resets `feat/program-wiki`; no shared state is touched.

## Pre-flight conflict scan

### Shared-file pairs (producer vs consumer)

| Tasks | Shared file / interface | Produces → consumes | Finding |
|---|---|---|---|
| T1 → T8 | `agent_stream.parse_stream` | T1 defines `parse_stream(raw, *, require_text=True) -> StreamResult\|None`; T8 lists it in Consumes | consistent |
| T1 → T1 | `claude_executor._unwrap_envelope`, `chat_agent.collect_turn` | both rewired in the same task; existing tests must pass unmodified | consistent |
| T2 → T9 | `Program.wiki_model` / `wiki_enabled` | T2 adds fields; T9 reads `program.wiki_model` and skips when `not wiki_enabled` | consistent |
| T3 → T4 | `wiki_okf.parse_page` / `render_page` | T4's `read_page`/`write_page` wrap them | consistent |
| T3 → T12 | `wiki_okf.Page`, `RESERVED`, `body_links` | T12 Consumes exactly those names | consistent |
| T3 → T13 | `RELATION_TYPES`, `CONFIDENCE`, `wikilinks` | all defined in T3 | consistent |
| T4 → T5 → T6 → T14 | `wiki_store.py` (create, then three appends) | strictly ordered appends, no rewrites | consistent |
| T4 → T7 | `wiki_store.BUNDLE_CLAUDE_MD` | T4 defines the template constant; T7 Consumes it | consistent |
| T5 → T7 | `wiki_store.WikiObject` | T5 defines the dataclass; T7 renders `(WikiObject, hash)` pairs | consistent |
| T5 → T10 | `program_objects`, `object_hash` | T10's collect re-derives hashes from `program_objects` | consistent |
| T6 → T9/T10 | `load_state`/`save_state`/`state_guard`, `DEFAULT_STATE` | beat halves read/write the same keys (`run`, `ingested`, `quarantined`, `ingests_since_lint`, `failures`) | consistent |
| T7 → T8 | `render_ingest` / `render_lint` / `kickoff` | T8 writes `instructions.md` from them and passes `kickoff(kind, run_dir)` to `claude -p` | consistent |
| T8 → T9/T10 | `WikiAgent.launch/is_running/collect/read_lint_report` | T9 calls `launch`, T10 calls `is_running`/`collect`, `_file_lint_report` calls `read_lint_report` | consistent |
| T9 → T10 | `wiki.py` (`_collect`, `_dirty_paths` stubs) | T9 ships `_dirty_paths` as a `return []` stub, T10 implements it | consistent (stub is explicit, not a placeholder) |
| T9 → T14 | `wiki.py` `_lint_report` stub | T9 ships `return ""`; T14 Step 5 replaces the whole function | consistent — see finding A |
| T11 → T15 | `cli.py` | T11 touches `dispatch_once` (:79-84) and the dispatch beat (:200-216); T15 adds a new `wiki` subparser | disjoint regions |
| T12 → T13 → T14 | `wiki_lint.py` (create, then two appends) | `lint()` extended each time; `Finding` shape fixed in T12 | consistent |
| T14 → T15 | `wiki_lint.run_lint`, `render_report` | T15's `--lint`/`--fix` call exactly those | consistent |

### Per-task self-agreement (tests vs code, files created vs later touched)

| Task | Finding |
|---|---|
| T1 | tests name `parse_stream` and `StreamResult` fields as implemented; `require_text` covered both ways |
| T2 | test asserts `wiki_enabled` absent from frontmatter when True — matches the "write only when False" rule |
| T3 | tests cover unknown-key preservation and `bad_yaml=True`; no raise paths asserted |
| T4 | `ensure_bundle` idempotence tested; `wiki_bundle` fixture added here and used by later lint tests |
| T5 | ordering `(at, oid)` asserted; `pending_objects` covers never-ingested and drifted in one path |
| T6 | flock test uses a real `tmp_path` repo; `load_state` never-raises asserted |
| T7 | pure rendering; asserts absolute read paths, precomputed `origin_hash`, and the do-not-hash prohibition |
| T8 | `_invocation` string asserted verbatim; `collect` tri-state asserted |
| T9 | launch-half tests inject a fake agent; gate/pause/disabled skips each asserted |
| T10 | collect-half tests cover running / ok / failed / grace-expiry / escaped / quarantine |
| T11 | dispatcher tests monkeypatch `dmod.wiki.beat`; error path returns a truncated string, asserted |
| T12 | 19 rules total across T12–T14; T12's own count asserted as 19 |
| T13 | `autofix` restricted to `{"link/wikilink", "rel/no-link"}`; test asserts `rel/no-link` gone after fix |
| T14 | `previous_bodies` test explicitly `git init`s because the `substrate` fixture has no repo |
| T15 | `--lint` exit 1 on errors asserted; manual end-to-end step uses a scratch substrate copy |

### Findings and rulings

**A. T9's stub comment says "Task 12 replaces this stub"; the replacing task is 14.**
Ruling: leave the stub text verbatim as the plan writes it — T14 Step 5 replaces the
entire function including the comment, so the stale reference never survives to the
final branch. No dispatch change.
Cost if wrong: one stale comment for the span of five tasks, removed by T14 anyway.

**B. Plan Global Constraints mandate asking before each commit; the skill mandates
continuous execution.** Ruling: see "Ruling: commit authorization" above.

No other conflicts found. Every task's own text agrees with itself.

## Progress

MERGE_BASE (branch point from main): 71c2aba
Docs commit (spec + charter + plan): 61cd46f
Task 1: BASE 61cd46f, implementer dispatched (sonnet).
Task 1: complete (commits 61cd46f..079daff, review clean — 889/889 passing, no Critical/Important findings)
Task 1: minor (deferred): agent_stream.py docstring forward-references the not-yet-existing wiki-agent caller
Task 1: minor (deferred): StreamResult.usage typed as bare `dict` rather than `dict[str, Any]`
Task 2: BASE 079daff, implementer dispatched (haiku)
Task 2: implementer DONE (commit 12e6b6e, 893/893 passing); reviewer dispatched (sonnet).
Task 2: complete (commits 079daff..12e6b6e, review clean)
Task 2: minor (deferred): wiki_model is written to frontmatter on every save once resolved to DEFAULT_MODEL, so an unrelated re-save adds the key to a legacy program.md. Pre-existing pm_model behaves identically; a shared follow-up if byte-identical persistence must tighten.
Task 3: BASE 12e6b6e, implementer dispatched (sonnet)
Task 3: implementer DONE_WITH_CONCERNS (commit 436f6fa, 901/901 passing). Concern: delegated frontmatter parsing to coscience.frontmatter_io.parse instead of the brief's inline `text.split("---", 2)` block.
Task 3: Ruling: the delegation stands. The plan's own Task 3 Interfaces block lists `coscience.frontmatter_io.parse(text) -> (dict, str)` under Consumes, so the inline split in the code block was the plan contradicting itself, and the controller's dispatch note independently instructed delegation. Reusing the repo's single frontmatter parser also keeps wiki pages and program.md on identical YAML semantics.
  Why: one parser, one behaviour; a second naive splitter would drift.
  Cost if wrong: if frontmatter_io.parse ever diverges from OKF's expectations (e.g. raises where the brief's splitter tolerated), wiki page parsing changes with it — the reviewer is asked to verify the never-raises / bad_yaml=True contract explicitly.
Task 3: reviewer dispatched (sonnet).
Task 3: complete (commits 12e6b6e..436f6fa, review clean — Approved, 0 Critical/Important)
Task 3: minor RESOLVED by controller: the "1 pre-existing warning" claim is confirmed true. Ran the suite; the sole warning is StarletteDeprecationWarning raised at import of ~/venvs/coscience/lib/python3.12/site-packages/fastapi/testclient.py — an installed dependency, not repo code, so nothing on this branch can have introduced it.
Task 3: minor RESOLVED by controller: "run ruff before merge" is moot — pyproject.toml declares no linter ([tool.*] sections are setuptools + pytest only); this repo has no ruff/flake8/black config.
Task 4: BASE 436f6fa, implementer dispatched (sonnet)
Env note for dispatches: `python` is not on PATH on this host; the venv interpreter is ~/venvs/coscience/bin/python.
Task 4: implementer DONE (commit 63e309a, 909/909 passing, no new warnings); reviewer dispatched (sonnet).
Task 4: review ❌ 1 Important (plan-mandated): _INDEX_MD string-templates the program title into YAML via .format(); a title containing ':' (or a leading YAML-significant char) yields unparseable index.md frontmatter.
Task 4: Ruling: fix it — the plan's Step-4 code block is wrong and the spec wins. The spec's binding OKF v0.2 requirement is that index.md carries parseable frontmatter with okf_version "0.2"; a hand-rolled YAML template cannot guarantee that, and research program titles with colons are ordinary. index.md's frontmatter must be built as a dict and serialized through coscience.frontmatter_io.serialize, the same path every other bundle page uses. Any other hand-templated YAML in the bundle skeleton gets the same treatment.
  Why: one serializer, one escaping story; the alternative is silently corrupt bundles for a common title shape.
  Cost if wrong: index.md's frontmatter key order may differ from the plan's literal template — cosmetic, and the plan's own OKF tolerance rule requires consumers not to care.
Task 4: minor (deferred): run_dir has no direct test (one-line path composition; consumer lands in Task 6).
Task 4: minor (deferred): task-4-report.md claims wiki_store.py is 226 lines; it is 202.
Task 4: fix round 1/5 dispatched to the original implementer; fix commit 20e40ef (910/910 passing, colon-title regression test added). Scoped re-review dispatched (haiku), FIX_BASE 63e309a.
Task 4: fix round 1/5 (1 addressed, 0 open; commits 63e309a..20e40ef)
Task 4: complete (commits 436f6fa..20e40ef, review clean after 1 fix round)
Task 5: BASE 20e40ef, implementer dispatched (sonnet)
Task 5: implementer DONE_WITH_CONCERNS (commit 199f570, 918/918 passing). Concern: the brief's test `test_pending_excludes_ingested_but_returns_drifted` mutated a result with a raw `write_text("edited\n")`, which destroys the result's frontmatter including its `sprint:` key — so program_objects correctly drops it and it can never be returned as pending. Implementer changed that arrange step to edit via `Substrate.save_result`; production code unchanged.
Task 5: Ruling: the test edit stands. The plan's test was self-defeating — its own sibling test `test_result_with_missing_sprint_is_skipped` asserts that exact drop behaviour, so as written the two tests contradicted each other. The test's *intent* is "content drifted since ingest => pending returns it", and editing through save_result preserves the sprint link while still changing the content hash, which is the behaviour under test.
  Why: a test that can only pass if a documented rule is broken is a defect in the test, not in the code.
  Cost if wrong: the drift path would be verified only for results edited through the API rather than by hand. Mitigated by hash_file/hash_dir having their own direct tests; the reviewer is asked to confirm the amended test still fails if drift detection is removed.
Task 5: reviewer dispatched (sonnet).
Task 5: complete (commits 20e40ef..199f570, review clean — Approved, 0 Critical/Important). Reviewer independently confirmed the amended drift test still discriminates: both ways of neutralising the hash comparison in pending_objects break one of its two assertions.
Task 5: minor (deferred): tests/test_wiki_objects.py has an unused `import time` (carried verbatim from the brief).
Task 5: minor (deferred): object_hash's docstring opens with an embedded empty-string literal (`""""" when …`) — cosmetic, from the brief.
Task 5: minor (deferred): no test asserts the artifact-branch slug/resource format; only the result branch is covered. Matters if a later task consumes artifact `resource`/`slug`.
Task 6: BASE 199f570, implementer dispatched (sonnet)
Task 6: implementer DONE (commit e32b821, 925/925 passing, no concerns); reviewer dispatched (sonnet).
Task 6: complete (commits 199f570..e32b821, review clean — Approved, 0 Critical/Important). Reviewer verified all three controller resolutions empirically: pure append (zero `-` lines in wiki_store.py), load_state never raises and returns a non-aliasing copy, unknown keys survive round-trip.
Task 6: minor (deferred): no automated regression test for unknown-key survival in state.json (verified manually only; the brief's test list omitted it).
Task 6: minor (deferred): state_guard is not reentrant (self-deadlock if nested) — inherent to the copied artifacts._lock_guard idiom, harmless under the beat's single-writer design.
Task 6: minor (deferred): every clean state_guard exit writes state.json even with no mutation (write-on-no-change), exactly as the brief specifies.
Task 7: BASE e32b821, implementer dispatched (sonnet)
Task 7: implementer DONE (commit afa3cbe, 933/933 passing, no concerns); reviewer dispatched (sonnet).
Task 7: complete (commits e32b821..afa3cbe, review clean — Approved, 0 Critical/Important). Reviewer diffed the landed module and tests byte-for-byte against the brief (identical), confirmed no IO, and cross-checked that BUNDLE_CLAUDE_MD's vocabulary matches wiki_okf's frozen constants exactly (12/5/3, same order).
Task 7: minor (deferred): render_lint's prohibitions block has no direct test (shares _PROHIBITIONS with render_ingest, which is tested).
Task 8: BASE afa3cbe, implementer dispatched (sonnet)
Task 8: implementer DONE (commit 1e6609c, 942/942 passing) with 3 flagged concerns.
Task 8: Ruling: the unused `parse_stream` import line in Task 8's Interfaces block is a stale plan line, and the implementation (collect reads only `report.json`, never `agent.out`) is correct. The plan's own Task 8 prose says verbatim "The beat never inspects `agent.out` itself", and every Task 8/10 test asserts against `report.json`. Task 1's extraction remains justified on its own terms — it deduped the two existing stream-json scanners in claude_executor and chat_agent.
  Why: agent.out is a raw transcript log for humans debugging a run; the machine-readable contract is the agent-authored report.json. Parsing both would give two disagreeing sources of truth for one run's outcome.
  Cost if wrong: per-run usage/cost telemetry (which only agent.out carries) is not recorded in phase 1. Nothing consumes it yet; a later phase that wants cost accounting adds one parse_stream call in collect.
Task 8: Ruling: `collect()` deliberately does NOT check liveness — its "running" state means only "agent.exit absent". This is the brief's spec and differs from ClaudeAgent.collect()'s "interrupted" case. The reaping duty belongs to the beat: Task 10 must call `is_running(token)` and apply COSCIENCE_WIKI_COLLECT_GRACE, or a process killed without writing the sentinel reads as running forever. Carried into the Task 10 dispatch as a required check.
  Why: keeping liveness out of collect leaves the seam a pure file-state reader, which is what makes it testable without launching a process.
  Cost if wrong: if Task 10 omits the is_running/grace path, a program's wiki beat wedges permanently after one killed run. This is exactly what the Task 10 review must confirm.
Task 8: reviewer dispatched (sonnet).
Task 8: complete (commits afa3cbe..1e6609c, review clean — Approved, 0 Critical/Important). Both rulings verified in consequence: agent.out IS still captured to disk (`> agent.out 2>&1`) so the human debugging transcript is not lost, and no second stream parser exists; `is_running(token)` delegates to executor.process_token()'s "<pid>:<starttime>" read fresh from /proc each call, so the token is a genuine CROSS-PROCESS liveness handle (a later dispatch process can reap a run an earlier one launched) and a recycled PID reads as not-running.
Task 8: minor (deferred): no test drives collect()'s garbage-`agent.exit` branch (`except (ValueError, OSError): code = 1`).
Task 8: NOTE for Task 9/10 reviewers: wiki_agent.launch() only mkdirs the bundle root — it does NOT create concepts/, entities/ etc. or CLAUDE.md. The beat MUST call wiki_store.ensure_bundle before launching. Confirm that ordering.
Task 9: BASE 1e6609c, implementer dispatched (sonnet)
Task 9: implementer DONE (commit f159909, 953/953 passing) with 1 deviation + 1 flag.
Task 9: Ruling: the conftest.py extension stands. `tests/conftest.py` was not in the brief's file list, but wiki.py does `from coscience.worker import claude_usage_ok`, which binds the name in wiki's own namespace — so the existing `_permissive_usage` fixture (which already patches cli.py the same way, and says so in its own comment) could not reach it. Without the patch the launch tests shelled out to the real usage script and their result depended on the developer's live Claude usage. A test whose outcome depends on the machine's API quota is not a test.
  Why: the alternative — leaving the fixture unpatched — makes the suite nondeterministic and machine-dependent, which is worse than touching a file the brief did not enumerate.
  Cost if wrong: a shared fixture now patches one more module; if a future test WANTS the real gate it must opt out explicitly. Reviewer asked to confirm the patch is narrow and does not mask the gate in the tests that assert the gate.
Task 9: flag carried to reviewer (implementer's, non-blocking, pre-existing pattern): beat()'s explicit is_paused check and claude_usage_ok's internal is_paused check are both unlocked reads.
Task 9: reviewer dispatched (sonnet).
Task 9: complete (commits 1e6609c..f159909, review clean — Approved, 0 Critical/Important). Reviewer walked every guard by hand and by test-removal: disabled / non-active / paused / gate-false / nothing-pending each independently block launch, and ensure_bundle runs unconditionally before BOTH the ingest and lint launch branches (Task 8 carry-forward satisfied). Confirmed the conftest patch does not mask the gate tests — test_usage_gate_blocks_launch injects usage_gate=lambda: False directly, and test_pause_blocks_launch is protected ONLY by beat()'s explicit is_paused check, so deleting that check fails it.
Task 9: reviewer's verdict on the double is_paused read: acceptable pre-existing pattern, not a race. beat()'s explicit check is load-bearing — it is what honours pause when a caller injects a custom usage_gate that doesn't check pause itself.
Task 9: NOTE for Task 10: with the stub _collect, state["run"] is never cleared, so a program that launches once routes to _collect forever and can never relaunch. Task 10 owns both clearing `run` and bounding retries via max_failures — the launch half has no retry bound of its own.
Task 9: minor (deferred): the usage gate is evaluated BEFORE checking whether anything is pending, so a live deployment shells out to the usage script every beat per enabled active program even with nothing to do. Inherited verbatim from the plan; a dispatch-loop perf item, not a task defect.
Task 9: minor (deferred): agent.launch() (subprocess spawn) runs inside state_guard's repo-wide flock, serialising wiki launches across all programs in a cycle. Inherited from the plan's lock design.
Task 9: minor (deferred): no test asserts default_usage_gate's wired parameters (threshold / weekly / fail_open / repo_root); verified by manual cross-check against worker.py only.
Controller usage checkpoint after Task 9: 5h window 78% (resets Thu 11:39), week 82%.
Task 10: BASE f159909, implementer dispatched (sonnet)
Task 10: implementer DONE (commit 4756eb4, 963/963 passing), flagging that lint-kind failures increment state["failures"] unboundedly because quarantine is guarded by `and batch` and lint has no batch.
Task 10: controller investigated the flag against the spec (spec lines 460-465). The spec's pseudocode is `failures += 1; if failures >= WIKI_MAX_FAILURES: quarantine state.run.batch; failures = 0` — the reset is UNCONDITIONAL. wiki.py:216 guards the whole branch with `and batch`, so after a failing lint run the counter is left at/above the threshold and the NEXT ingest failure quarantines an innocent batch on its FIRST failure. That is a real cross-contamination bug, not a cosmetic divergence.
Task 10: Ruling: fix it — make the `failures = 0` reset unconditional, exactly as the spec's pseudocode has it, keeping the quarantine of `batch` itself guarded (quarantining an empty lint batch is a no-op either way). Do NOT introduce separate per-kind counters: the spec deliberately uses one shared counter, and splitting it is a design change this plan has no mandate for.
  Why: the shared counter is the spec's design; the missing reset is the implementer's guard placement, and its consequence is that a poison lint silently lowers the quarantine bar for unrelated ingest work.
  Cost if wrong: with one shared counter a mixed sequence (2 lint failures then 1 ingest failure) still quarantines at the third failure. That residue is the spec's own accepted behaviour; anything better is a phase-2 design change.
Task 10: NOT a defect — a repeatedly failing lint relaunching every beat is spec-intended: "the counter resets when the run collects ok, not here: a lint run that fails must still be owed" (spec line 470-471).
Task 10: reviewer dispatched (sonnet), with the failures-reset finding named for independent verification.
Task 10: review ❌ Changes Requested — 1 Critical. Reviewer REPRODUCED the bug against real wiki.beat() calls (not mocks): with MAX_FAILURES=2, one failed lint leaves failures=1, then a freshly-launched ingest batch is quarantined on its FIRST failure ("wiki: ingest quarantined 1", result:r0 never got its chances). Under a sustained lint outage the counter grows unboundedly past the threshold, so the next ingest batch to fail is quarantined instantly regardless of its own history. No existing test catches it — all 10 collect tests exercise ingest-only or lint-only sequences.
Task 10: CORRECTION to my earlier ledger note: I wrote that the spec resets `failures` unconditionally and the implementation deviated. That is wrong — the reviewer read spec §8.4 lines 459-464 and the spec's pseudocode carries the SAME `and batch`-shaped guard. This is a defect in the design's own pseudocode, faithfully implemented, not an implementer deviation. The fix is unchanged; the blame is not.
Task 10: Ruling stands (unconditional reset on threshold, quarantine action still gated on non-empty batch, no per-kind counters) and the reviewer independently endorsed it as correct and sufficient. Because the defect originates in the spec, the fix also amends the spec's pseudocode so phases 2-5 do not re-implement the bug.
  Why: leaving the design doc asserting the buggy shape guarantees the next phase reproduces it.
  Cost if wrong: the spec diverges from the plan text (which still shows the old shape); the plan is spent after this phase, the spec is not.
Task 10: fix round 1/5 dispatched to the original implementer, FIX_BASE 4756eb4.
Task 10: fix commit 1cd12aa (964/964 passing; 963 baseline + 1 interleaved-kinds regression test). Implementer verified the new test genuinely catches the bug by reverting the fix (test failed with `2 == 0`), then restoring it. The fix also amends spec §8.4's pseudocode so later phases don't re-implement the defect.
Task 10: STATUS — fix landed, scoped re-review still OWED. Review package already generated at `.superpowers/sdd/2026-08-20-program-wiki-phase-1/review-4756eb4..1cd12aa.diff`.

Task 10: fix round 1/5 re-review ALL ADDRESSED (haiku) — reset unconditional with quarantine still batch-gated, the interleaved regression test provably fails without the fix (it asserts quarantined == [] after a first ingest failure that would otherwise cross the threshold), no per-kind counters, spec §8.4 edit is pseudocode-only. No new issues.
Task 10: complete (commits f159909..1cd12aa, review clean after 1 fix round)
RESUMED 2026-08-20 11:50 PDT — 5h window reset (4%), weekly 84% is now the binding budget.
Task 11: BASE 1cd12aa, implementer dispatched (sonnet)
Task 11: implementer DONE (commit 6afec27, 967/967 passing) with 2 flagged concerns.
Task 11: Ruling: the ACTIVE-status filter in the dispatcher stands. The brief contradicts itself — its prose says "don't duplicate the active/enabled check wiki.beat already does", but its own given test monkeypatches wiki.beat (bypassing the internal check) and asserts a CLOSED program is never passed to it. The test states the stronger, more useful contract. The prose's real intent is "don't re-implement the pause check or the usage gate", which the implementer did not do.
  Why: a per-program boolean is free, while constructing a WikiAgent and entering the repo-wide flock for a closed program is not; skipping closed programs also keeps the dispatch pass cheap on a substrate with a long tail of finished programs.
  Cost if wrong: one redundant status check. If wiki.beat's own notion of "eligible" ever widens beyond ACTIVE, the dispatcher gate would silently narrow it — the reviewer is asked to confirm the two checks agree today.
Task 11: flag carried to reviewer (implementer's): no per-call timeout around wiki.beat / lazy WikiAgent() construction. Same risk profile as the existing chat-lock reaper it sits beside; pre-existing pattern, not new.
Task 11: reviewer dispatched (sonnet).
Task 11: complete (commits 1cd12aa..6afec27, review clean — Approved, 0 Critical/Important). Ruling's consequence verified: wiki.beat's own gate is `if program.status != ACTIVE or not program.wiki_enabled: return ""`, so the dispatcher's ACTIVE filter is the identical condition on the status axis and narrows nothing. wiki_enabled, pause and the usage gate stay solely inside beat() — no duplicated gating.
Task 11: reviewer independently confirmed the no-timeout flag is benign: WikiAgent.launch uses executor.launch_detached and is_running/collect are non-blocking file/process checks, so beat() returns promptly; state_guard's repo-wide flock is held only around the short load/launch/save critical section, never around subprocess completion; _dirty_paths' git call has its own 30s timeout.
Task 11: minor (deferred, worth a phase-2 look): a wiki-launched Claude run does NOT increment report.beaten, which cli.py feeds to LoopStatus as claude_calls — so wiki runs are missing from the rolling "1h: N claude" counter. The run IS visible instantaneously in the beat's status line; only the numeric accounting under-counts. Out of the brief's scope.
Task 11: minor (deferred): no test covers the `[:200]` truncation boundary — the error test's message is short, so the test would pass identically if the slice were deleted.
Task 11: minor (deferred): pm_beat_line marks failures with uppercase ERROR; the wiki line uses lowercase "wiki: error — ". Cosmetic.
Task 12: BASE 6afec27, implementer dispatched (sonnet)
Task 12: implementer DONE (commit 3eabb4e, 986/986 passing, 19 new tests) with 3 flagged concerns, all about false-positive risk.
Task 12: CARRY TO TASK 13 (the implementer's third concern, and the important one): `wiki_okf.body_links` does not exclude fenced code blocks and does not see `[[wikilinks]]`. Task 13 builds the containment rule `rel/no-link` on top of link extraction, so both gaps land directly on the invariant's correctness — a relation whose target is linked only as a wikilink, or a link that exists only inside a code fence, must not be judged wrongly. Task 13's dispatch must state which behaviour is intended and its tests must pin it.
Task 12: reviewer dispatched (sonnet), with all three false-positive concerns named for verification.
Task 12: review ❌ Changes Requested — 2 Important, both reproduced.
Task 12: Ruling (Important #1): fix page/orphan to consult `wiki_okf.wikilinks()` alongside `body_links()`. A page linked only as `[[slug]]` is currently reported orphaned, in the plain-lint mode the dashboard health badge uses. Approach C treats wikilinks as first-class link substrate — that is the whole premise of the design — so a link-detection rule that cannot see them is simply wrong.
  Why: the top false-positive risk on the rule most likely to be shown to a human.
  Cost if wrong: orphan detection becomes slightly more permissive; an info-severity rule reports less, never more.
Task 12: Ruling (Important #2): make `wiki_store.page_paths`' RESERVED filtering ROOT-SCOPED. Today it drops any file whose bare name is in RESERVED at any depth, so a stray `concepts/index.md` never reaches iter_pages and the documented, Task-13-autofixable `okf/index-frontmatter` rule is unreachable through the real pipeline — exercisable only by hand-building a Page. Spec §4's layout has no legitimate non-root index.md, so such a file is precisely the anomaly the rule exists to surface.
  Why: a rule that cannot fire in production is worse than no rule — it reads as coverage that does not exist, and Task 13 is about to build autofix on top of it.
  Cost if wrong: a stray reserved-named file inside a page directory (concepts/log.md, concepts/CLAUDE.md) now gets linted instead of silently ignored. That is the intended behaviour, but it does widen what the linter sees; Task 4's existing page_paths tests must be updated deliberately, not incidentally.
Task 12: also folding in review minors #4 (a page's own self-link currently suppresses its orphan finding), #6 (no test enumerates the frozen rule-code vocabulary) and #7 (nothing pins the `continue` that stops bad-yaml pages also reporting missing-type). Deferring #8 (defensive coercion for hand-built Page objects — latent only, parse_page is the sole producer and always normalises).
Task 12: review NOTE — the implementer's own concern #1 (cross-directory same-filename suppression) did NOT reproduce: _is_linked matches the full `dir/file.md` path, so concepts/replication.md and sources/replication.md do not cross-suppress under the current flat layout.
Task 12: fix round 1/5 dispatched to the original implementer, FIX_BASE 3eabb4e.
Task 12: fix commit 0e36078 (990 passing; 986 baseline + 4 net new). Implementer reports both Importants fixed per the rulings, minors #4/#6/#7 folded in, the three out-of-scope items untouched, and `tests/test_wiki_store.py` left unchanged because no pre-existing Task 4 test asserted the old depth-independent RESERVED filtering. Scoped re-review dispatched (sonnet), FIX_BASE 3eabb4e -> 0e36078.
Task 12: fix round 1/5 re-review ALL ADDRESSED (sonnet). Verified: `_linked_targets()` unions body_links+wikilinks and feeds both the index and per-page link sets; `page_paths` no longer filters RESERVED inside PAGE_DIRS (root-scoping is now structural — the walk never visits bundle root, so root reserved files still cannot leak in) and the new test drives a stray `concepts/index.md` through write_page -> iter_pages -> lint rather than a hand-built Page; self-links no longer suppress a page's own orphan finding; the 8-code vocabulary test and the bad-yaml `continue` test both landed. Grep confirmed no other caller depended on the removed filter. The "test_wiki_store.py needed no change" claim independently HOLDS. The three out-of-scope items are untouched.
Task 12: minor (deferred): `_is_linked`'s new bare-slug match (`target == page.slug`) is applied to ALL link targets, not only wikilink-shaped ones, so a markdown href that happens to equal a page's filename stem (`[t](a)`) would also suppress an orphan finding. Broadened false-negative surface only, unexercised either way; for the final whole-branch review.
Task 12: complete (commits 6afec27..0e36078, review clean after 1 fix round)
Task 13: pre-dispatch brief scan (controller, before dispatching).
Task 13: Ruling: `AUTOFIXABLE` is the TWO-element set `{"link/wikilink", "rel/no-link"}`. The brief contradicts itself — its Interfaces block lists a three-element frozenset including `okf/index-frontmatter`, while its Step-3 code, its `test_autofixable_set`, and an explanatory comment all agree on two. The stale Interfaces line loses; auto-stripping a page's frontmatter is how you lose content, and `write_page` has no raw-write path for it anyway. Cost if wrong: `okf/index-frontmatter` stays agent-fixed rather than mechanically fixed — reported either way, never silently dropped.
Task 13: Ruling: the brief's "Consumes: `wiki_okf.render_page`" line is stale (same shape of defect as Task 8's). Nothing in the task's code renders a page — `autofix` mutates `Page.body` and returns Page objects; persistence stays with the caller. Implementer told to ignore the line, not to invent a call for it. Cost if wrong: none — an unused import would have been flagged at review anyway.
Task 13: Ruling on the Task-12 carry-forward (code fences + wikilinks vs the containment invariant): (a) wikilinks DO satisfy nothing on their own for `rel/no-link` — containment requires a real markdown link, which is exactly what `autofix` creates by rewriting `[[slug]]` first and only then testing containment, so the ordering inside `autofix` is load-bearing and must be pinned by a test; (b) fenced code blocks are NOT excluded from link extraction in phase 1. Uniformity is the point: `body_links`/`wikilinks` are used identically by `page/orphan`, `link/broken`, `rel/no-link` and `autofix`, and a fence-aware extractor that only some rules use is worse than a consistently naive one. Cost if wrong: a wikilink written inside a fenced example gets rewritten into a markdown link — visible, reversible, and caught by the agent's own lint run. Pinned by a test documenting the limitation rather than left implicit.
Task 13: Ruling: implementer model = cheapest tier. The brief carries the complete implementation and the complete test file; this is transcription plus a test run, per the skill's "plan text contains the complete code to write" rule.

## (historical) STOPPED — 5h usage window exhausted (2026-08-20 08:29 PDT)

5h: 98% (resets Thu 2026-08-20 11:39 PDT). Week: 84% (resets Sun 22:59).
Autonomous mode was never confirmed by the human, so per the global rule this
session surfaces and waits rather than self-scheduling a resume.

### EXACT RESUME POINT

Branch `feat/program-wiki` at 1cd12aa, 11 commits ahead of main (71c2aba).
Tasks 1-9 complete and reviewed clean. Task 10 implemented + fixed, re-review owed.

1. Dispatch the scoped re-review of Task 10's fix (cheap/mid model is fine — small
   single-concern diff): brief `task-10-brief.md`, report `task-10-report.md`
   ("Fix round 1" section), diff `review-4756eb4..1cd12aa.diff`. It must confirm
   (a) the reset is now unconditional on crossing the threshold while quarantine
   stays gated on a non-empty batch, (b) the interleaved lint-then-ingest test
   genuinely fails without the fix, (c) no per-kind counters were introduced, and
   (d) the spec §8.4 pseudocode edit matches the code and touched pseudocode only.
2. If clean: `Task 10: complete (commits f159909..1cd12aa, review clean after 1 fix round)`.
3. Then Tasks 11-15, same loop. Briefs already generated for 11
   (`task-11-brief.md`, 179 lines). Task 11 = Dispatcher wiring (tests monkeypatch
   `dmod.wiki.beat`; the error path returns a truncated string and is asserted).
   Tasks 12-14 = `wiki_lint.py` (19 rules, autofix restricted to
   `{"link/wikilink", "rel/no-link"}`, then `run_lint`); T14 Step 5 replaces
   `wiki.py`'s `_lint_report` stub entirely. Task 15 = `coscience wiki` CLI;
   its manual end-to-end step MUST point COSCIENCE_REPO at a SCRATCH COPY of the
   substrate, never the live one.
4. Then the final whole-branch review on the most capable model:
   `scripts/review-package <plan> 71c2aba HEAD`, using
   `superpowers:requesting-code-review/code-reviewer.md`, pointed at every
   `minor (deferred)` line in this ledger.
5. One fix wave + one scoped re-review, adjudicate residuals, then delete this
   workspace and use `superpowers:finishing-a-development-branch`.

Env reminder: `python` is not on PATH; use `~/venvs/coscience/bin/python -m pytest -q`.
Nothing has been pushed. The pre-existing in-flight frontend work
(styles.css, ProgramDetail.tsx, SprintDetail.tsx, PageToc.tsx, frontend/.coscience/,
docs/_tmp_wiki/) remains untracked/modified and untouched by every commit on this branch.
Task 13: BASE 0e36078, implementer dispatched (haiku — brief carries complete code + tests), with rulings 1-4 carried in the dispatch.
Task 13: implementer DONE (commit e3ce9dd, 1007 passing = 990 baseline + 17 new). Reviewer dispatched (sonnet), with four named risks for focused checks: _cycle_nodes DFS correctness (self-loop, path/stack sync, path.index slice), autofix in-place mutation + idempotence, rel/no-source conflating two conditions into one message, and _resolve being applied to BOTH sides of the containment comparison.
Task 13: review Approved with 1 Important (plan-mandated) + 4 Minors. Reviewer hand-traced _cycle_nodes through a self-loop, a 3-node cycle with an incoming acyclic edge, and a branching case: path/stack stay in sync, colour==1 always implies membership in path (so path.index can't raise), self-loops detected and terminating. _resolve confirmed applied symmetrically to both sides of the containment comparison. All four pre-dispatch rulings verifiably implemented; exactly 7 new rule codes; exactly the 2 authorized extra tests; commit touches only wiki_lint.py + test_wiki_lint_links.py.
Task 13: Ruling (Important): fix `rel/no-source`. `if not r.source or (source_ids and r.source not in source_ids)` short-circuits whenever a page declares NO sources at all, so a relation citing `source: c1` on a source-less page passes the very rule whose job is citation integrity. Drop the `source_ids and` guard so the membership test is unconditional. This is strictly a superset of what already errors (a relation with no source at all already fires), so it cannot contradict any existing passing case. Inherited verbatim from the brief's own Step-3 code — a plan defect faithfully implemented, not an implementer deviation.
  Why: sources are per-page frontmatter in this design; an empty `sources:` block is not a licence to cite, it is the strongest evidence the citation is bogus.
  Cost if wrong: pages that omit `sources:` while their relations name source ids start reporting `rel/no-source` errors, which Task 15's `--lint` will exit 1 on. That is the intended signal, and it is agent-fixable — but if a legitimate ingest pattern turns out to populate relation sources before the sources block, this becomes noisy and the guard comes back.
Task 13: Ruling: the fix also amends the plan's Task 13 Step-3 snippet, so the plan and the branch do not diverge on a defect the plan authored. Same precedent as Task 10's spec amendment.
Task 13: folding in Minors #1 (split rel/no-source's one message into two — "names no source" vs "names unknown source id"; natural since the fix touches that branch), #3 (autofix appends a duplicate link/wikilink Finding when the same slug appears twice, because body.replace already replaced all occurrences on the first hit — dedupe the slug iteration) and #4 (test idempotence of the `# Related` containment-append path, which today is only hand-traced).
Task 13: minor (deferred): `rel/unknown-type` is overloaded to report both an out-of-vocabulary relation type and an out-of-vocabulary confidence value, so render_report groups two unrelated problems under one heading. Splitting it needs a 20th rule code, which the frozen 19-rule budget does not have — a design question for the final review, not a task fix.
Task 13: fix round 1/5 dispatched to the original implementer, FIX_BASE e3ce9dd.
Task 14: pre-dispatch brief scan (controller). Verified against the real code: `lint()`'s signature already accepts `objects`/`previous`/`now` as keyword-only params (wiki_lint.py:34-37), `_norm` exists (wiki_lint.py:131), and the two new rule groups append cleanly after `_relation_rules` (wiki_lint.py:45).
Task 14: Ruling: the linter has TWENTY rules, not nineteen. Spec §9's table has exactly 20 rows and they match the implementation exactly (T12's 8 + T13's 7 + T14's 5). The "19 rules" in the plan's prose — and in this ledger's own pre-flight scan row for T12 — is an arithmetic error in the plan. The spec is the binding authority; no code changes, and a reviewer must not treat the 20th rule as an Extra. Confirmed no test or comment hardcodes 19.
  Cost if wrong: none to the code — only the plan's prose is wrong, and it is now corrected in this ledger.
Task 14: Ruling: `previous_bodies` must derive its git path prefix from `bundle_dir(substrate, program_id).relative_to(substrate.repo_root).as_posix()` rather than hardcoding `f"programs/{program_id}/wiki/"` as the brief's Step-3 code does. The bundle layout already has exactly one owner; a second literal copy of it inside a git invocation is how the two drift silently.
  Cost if wrong: none — same string today, by construction.
Task 14: Ruling (Important, plan-mandated): the brief's `test_previous_bodies_empty_without_git` asserts nothing. `isinstance(x, dict)` is true of every possible return, and the p9 bundle it builds has no pages at all, so `previous_bodies`' loop never executes and the no-git path is never reached. It must write a page first and then assert `previous_bodies(...) == {}` — which genuinely exercises the git-returns-nonzero branch on a repo-less substrate.
  Cost if wrong: a strictly stronger test; if `previous_bodies` ever legitimately returns non-empty without git, that is itself the bug the test should catch.
Task 14: Ruling: move `dirty_before = _dirty_paths(substrate)` (wiki.py:105) to AFTER the `_lint_report(...)` call. Step 5 makes `_lint_report` run `run_lint(fix=True)`, which writes autofixed pages to the substrate — so with the current ordering those writes land after the "before" snapshot and are attributed to the agent at collect time. They are bundle-internal, so the `wrote outside the bundle` check does not misfire today; the ordering is still wrong and gets more wrong the moment autofix's reach widens.
  Cost if wrong: the containment snapshot becomes marginally tighter; nothing else reads dirty_before.
Task 14: minor (deferred): `previous_bodies` spawns one `git show` subprocess per page, so a lint run costs N subprocesses. Bundles are small in phase 1 and a single `git cat-file --batch` would be premature; noted for the final review.
Task 14: NOTE (raise with the reviewer, do not pre-fix): `_lint_report`'s autofix writes are not committed in the launch half — they ride along in the collect half's `substrate.commit(...)` at wiki.py:224. A run that never collects leaves them uncommitted in the substrate working tree. Ask the reviewer whether that is acceptable given the beat's crash-recovery story.
Task 13: fix round 1/5 re-review ALL ADDRESSED (haiku). Guard removed and split into `if not r.source:` / `elif r.source not in source_ids:` under the same rule code and severity; regression test covers a page with no sources block citing `source: "c1"`; dedupe via `dict.fromkeys(wikilinks(body))` with a test asserting both occurrences rewritten but exactly one finding; `# Related` idempotence test added; the plan edit touches only Task 13 Step 3's code block and its two message strings. Stricter-rule risk checked explicitly: six existing tests build relations with `source: "c1"` on source-less pages and now fire rel/no-source, but NONE asserted its absence, none were modified, and all new tests are pure additions — so nothing was loosened to keep green.
Task 13: complete (commits 0e36078..9c39991, review Approved after 1 fix round)
Task 14: BASE 9c39991, implementer dispatched (sonnet — three source files plus wiki.py integration), with rulings on the 20-rule count, the bundle_dir-derived git prefix, the assert-nothing test, and the dirty_before ordering carried in the dispatch.
Task 15: pre-dispatch brief scan (controller). Verified against the real code, all clean: `counts()` seeds every severity to 0 (wiki_lint.py:167) so `counts(findings)["error"]` cannot KeyError on a clean bundle; `render_report([])` returns exactly "# Wiki lint\n\nNo findings.\n" (wiki_lint.py:174-176), matching the test's "No findings" assertion; `beat(substrate, program, now, agent, *, usage_gate=None)` (wiki.py:74-75) matches the tests' `lambda sub, prog, now, agent, **kw` monkeypatch and the brief's positional call; `pending_objects(substrate, pid, ingested, quarantined)` and `iter_programs(status=None)` match their call sites.
Task 15: Ruling: SPLIT the task. Steps 1-6 and 9 (subparser, command body, tests, suite, commit) go to the implementer. Steps 7 and 8 do NOT — Step 7 launches a real `claude -p` wiki agent against a copy of real substrate data, which is a side effect outside this worktree that spends the human's Claude quota (weekly at 89%), and Step 8's charter note is defined as "what the end-to-end run revealed", so it cannot honestly precede Step 7.
  Why: the SDD skill's four stop conditions include a side effect outside the worktree that norms say you ask about first. A live agent run on real data is that, and it pairs naturally with the deployment go-ahead the human owes this branch anyway.
  Cost if wrong: phase 1's definition-of-done stays formally unmet until the human says go — every line of code and every unit test is still finished and reviewed, so the cost is one round-trip, not rework.
Task 15: the controller will itself run the NON-LLM half of Step 7 (scratch substrate copy, `--status`, `--lint`, git cleanliness checks) since none of it calls Claude, and will surface only the `--once` live run for approval.
Task 15: Ruling: `--once` is a no-op flag — the beat path is the fall-through default when neither `--status` nor `--lint` is given, so nothing reads `args.once`. Keep it exactly as the brief writes it: it documents intent, it mirrors `worker`/`dispatch`/`pm`, and it keeps the mutually-exclusive group honest about the three modes.
Task 15: the brief's Done-when line says "~90 new tests"; the branch is already past 1010 total. Stale prose, ignored.
Task 14: implementer DONE (commit df5b496, 1023 passing = 1010 baseline + 13 new, 1 warning claimed pre-existing). Commit scope verified: wiki.py, wiki_lint.py, wiki_store.py, test_wiki_lint_sources.py only. Implementer CONFIRMS the uncommitted-autofix finding: _lint_report writes via run_lint(fix=True) in the launch half with no commit until a later _collect succeeds, so a launched-but-never-collected lint run leaves autofixed pages uncommitted in the substrate working tree indefinitely. Reviewer dispatched (sonnet) with five focused risks named, including an independent severity verdict on that finding and on the claimed-pre-existing warning.
Task 14: review ❌ Needs fixes — 1 Important, 1 Minor. Everything else verified correct: all five rulings honoured (20 rules exactly, bundle_dir-derived prefix at wiki_store.py:215, the assert-nothing test genuinely rewritten to write a page then assert `== {}`, dirty_before moved with a comment and no other change to beat()); `_source_rules`' if/elif correctly falls through to neither branch for a Source page when objects is None; run_lint's fix-then-lint ordering genuinely reflects post-fix state because autofix mutates Pages in place, and fixed_count counts pages not findings; `previous_bodies`' check=False + returncode==0 shape is right (a missing path at HEAD is a normal miss, not an error); the throwaway Page in _trust_rules is safe because has_section reads only self.body.
Task 14: reviewer independently corroborated the "1 warning is pre-existing" claim — ran the new test file under `-W error::DeprecationWarning` (zero warnings from it) and located the StarletteDeprecationWarning's real source in the fastapi TestClient tests, none of which this diff touches.
Task 14: Ruling (Important): fix the placement. `previous_bodies` was inserted between `save_state` and `state_guard` rather than at the file's true end, so `state_guard` is still the last definition. Two reasons it is worth a round rather than a shrug: it splits `save_state` from `state_guard`, which are one unit, and the implementer's own report self-certified "appended at the end" while its own parenthetical said "before state_guard" — a claim that was verifiably false is worth correcting even when the code is harmless.
  Cost if wrong: a pure function move with no logic change; the only risk is the move itself, which the existing state tests cover.
Task 14: folding in the Minor while that function is open: `previous_bodies`' docstring and the plan's Interfaces block both promise `{}` when git is unavailable, but the `except` returns a partially-populated `out` if git dies partway through the page loop. True in the common case (missing binary raises on the first iteration, out is still empty) but overstated in general — the docstring should say best-effort/partial rather than `{}`.
Task 14: Ruling (deferred, NOT this task): the uncommitted-autofix-writes behaviour is real and newly introduced — `_lint_report` now writes via run_lint(fix=True) in beat()'s launch half with no commit before agent.launch(). Reviewer's independent verdict agrees with mine: bounded risk (writes stay inside the bundle, are swept up by the next successful collect, and ruling 4's reordering already stops them being misclassified as escaped writes), mandated verbatim by the brief's Step 5, and resolvable two ways — give those writes their own commit, or accept "eventually committed, never lost, occasionally comingled" as intentional. Carried to the final whole-branch review as an explicit design question.
Task 14: fix round 1/5 dispatched to the original implementer, FIX_BASE df5b496.

Task 14: fix round 1 re-review CLEAN. Verified: `previous_bodies` is now the last
definition in wiki_store.py (after `state_guard`); the move is byte-for-byte
behaviour-preserving (prefix derivation, page_paths loop, subprocess.run kwargs,
except handler all identical); `state_guard` body unmodified, only relocated;
docstring now describes partial-result best-effort behaviour; report corrected;
only wiki_store.py touched. 29 covering tests pass, full suite 1023.
Task 14: complete (d83aca8).

Task 15 dispatched. Ruling: SPLIT the task. Steps 1-6 and 9 (subparser, command
body, tests, suite, commit) go to the implementer. Steps 7 and 8 do NOT — Step 7
launches a real `claude -p` wiki agent against a copy of real substrate data,
which is a side effect outside this worktree that spends the human's Claude quota
(weekly at 90%), and Step 8's charter note is defined as "what the end-to-end run
revealed", so it cannot honestly precede Step 7. Cost if wrong: the phase-1
definition-of-done stays unverified until the human authorises the live run;
nothing is lost, only deferred.
Ruling: `--once` is a no-op flag — the beat path is the fall-through default and
argparse's mutually-exclusive group makes it meaningful only as the explicit
opposite of --lint/--status. Keep it as the brief writes it. Cost if wrong: a
redundant flag in the CLI surface.
Ruling: the Done-when line "~90 new tests" is stale prose from an earlier draft
(the task adds 6). Ignore it; the binding target is the 6 tests in Step 1 plus a
green suite. Cost if wrong: none.
Task 15: implementer dispatched (sonnet), BASE d83aca8. Scope handed over: Steps
1-6 and 9 only; Step 9 stages src/coscience/cli.py + tests/test_cli_wiki.py ONLY
(no docs/knowledge-charter.md, since Step 8 is held back with Step 7).
Task 15: implementer returned DONE at bd9622f "feat(wiki): coscience wiki CLI —
once, lint, status". Scope verified by `git show --stat`: src/coscience/cli.py
(+52/-1) and tests/test_cli_wiki.py (+68) only — no frontend paths, no
docs/knowledge-charter.md, no carried-over work. 6 new tests, suite 1029.
Task 15: task review dispatched (sonnet), package review-d83aca8..bd9622f.diff,
with 8 named risks: WikiAgent() constructed unconditionally on the --once path;
pending_objects positional-arg match; exit-code accumulation via counts()["error"];
--status listing non-ACTIVE programs vs the brief's Done-when wording;
substrate.commit on --fix (once for all programs, fires on zero fixes, and --fix
is outside the mutually-exclusive group so it can be passed without --lint);
insertion points; import extension vs duplication; whether the --fix test's
assertion is trivially satisfied.
Deferred-item digest for the final whole-branch review written to
.superpowers/sdd/2026-08-20-program-wiki-phase-1/deferred-for-final-review.md
(10 design questions, 7 coverage gaps, cosmetics, and the explicit note that
phase 1's definition of done is unverified pending the held-back Step 7).
Task 15: task review APPROVED. All 9 in-scope steps ✅. All 8 named risks came back
clean and checked-by-reading, not inferred: WikiAgent.__init__ only assigns
claude_bin (nothing launched) and both --once tests monkeypatch cli_mod.wiki.beat;
pending_objects' `quarantined` is positional-or-keyword so the positional 4th arg
is valid; counts() seeds every SEVERITY to 0 and `worst = max(...)` is monotonic
across the program loop; `if args.fix: substrate.commit(...)` is nested inside
`if args.lint:` so --fix alone never commits, and Substrate.commit uses check=False
so a zero-fix run is a harmless no-op; insertion points displace nothing; the
import line was extended, not duplicated, and all four new names are used; the
--fix test is not trivially satisfied (one wikilink, one changed page, "fixed 1").
Task 15: complete (bd9622f).

Task 15: minor (deferred): `--status` lists every program, not only ACTIVE ones —
the brief's Done-when says "every active program's pending count" but its own
Step-4 code (which the implementer transcribed character-for-character) filters
only on --program. Reviewer's verdict, which I adopt: a brief-authoring gap, not an
implementer deviation, and harmless — pending_objects computes cleanly for any
status and the --once path is unaffected because wiki.beat gates internally on
ACTIVE + wiki_enabled. Ruling: leave as written. A status command that hides
closed programs is less useful than one that shows them, and narrowing it now
would diverge the code from the plan over a wording mismatch. Cost if wrong: an
extra informational line per non-active program. Added to the final-review digest.

=== All 15 tasks complete. Branch feat/program-wiki: 71c2aba..bd9622f, 1029 tests. ===
Final whole-branch review dispatched on the most capable model, BASE 71c2aba,
pointed at deferred-for-final-review.md.
Final whole-branch review: first dispatch (opus) died mid-read on a session limit.
Re-dispatched on sonnet at the user's direction — weekly budget at 94% and the
389KB diff read is the expensive part. Same brief, same full scope, same
deferred-item digest; only the tier changed. BASE 71c2aba, HEAD bd9622f.

=== FINAL WHOLE-BRANCH REVIEW (sonnet, full scope): CHANGES REQUESTED ===
0 Critical, 3 Important, 3 Minor. All 10 deferred design questions RATIFIED
(6 correctly deferred, 4 not actually a problem) — none overturned, so the
per-task rulings stand. Report: final-review.md.

Important 1: escaped runs bypass state["failures"] and quarantine entirely
(wiki.py:189-207 returns before the counter) — a deterministically-escaping batch
retries forever, the only unbounded failure mode in the machine.
Important 2: report.json's `objects` field has NO consumer — _collect marks the
whole dispatched batch ingested on any exit-0 run, so the prompt's own sanctioned
"do fewer objects well and say so" path causes silent permanent under-ingestion.
Spec §8.6's claim that "lint will find anything wrong" is false for this: no rule
cross-checks objects against resulting pages. This is the plan-was-wrong case.
Important 3: the escape guard diffs `git status --porcelain` before/after, so an
agent that runs `git commit` itself makes out-of-bundle writes invisible. Nothing
in _PROHIBITIONS forbids it and the agent has unrestricted bash.

Rulings for the fix wave, written into final-fix-brief.md:
Ruling (F1): increment the SHARED failures counter on the escaped branch and let
escapes quarantine at the same threshold — no escaped-only counter. Consistent
with the Task 10 ruling that rejected per-kind counters. Keep not-recording the
batch; that part is deliberate. Cost if wrong: an escaping batch quarantines
after 3 tries instead of retrying, which is the point.
Ruling (F2): RECONCILE — ingest only set(batch) & set(report["objects"]),
remainder stays pending. Rejected the reviewer's alternative of deleting the
field and amending §8.6. Asymmetric costs: re-ingesting costs one agent pass,
missing an object costs a permanent undetectable hole. Backward compat mandatory:
absent/malformed/non-list `objects` falls back to ingesting the whole batch, so
§8.6's missing-report path and every existing test survive. An honest empty
coverage report is NOT routed to the failure counter. Cost if wrong: an agent
that under-reports its own coverage causes a re-ingest.
Ruling (F3): prompt-level fix ONLY this phase — forbid git commit/add/stash/
checkout/reset in _PROHIBITIONS. Explicitly NOT doing a sandbox, tool allow-list
or commit-SHA comparison: that is a real change to the agent seam and needs its
own task and review. Residual risk accepted and to be recorded: a prompt is
guidance, not an enforcement boundary. Cost if wrong: the guard stays defeatable
by a sufficiently confused agent, exactly as today.

Fix wave NOT yet dispatched — weekly usage at 96% (resets Sun 23:00). Surfaced to
the user for a spend decision. Brief is written and ready for a single cheap
dispatch. Nothing is half-done: bd9622f is green at 1029 tests.
