# Final whole-branch review — `feat/program-wiki` (Phase 1)

Reviewed range: `71c2aba..bd9622f` (21 commits, 32 files, +8640/-39).
Method: full read of the 9268-line diff in passes, cross-referenced against the
landed source tree, `docs/superpowers/specs/2026-08-20-program-wiki-design.md`,
`docs/superpowers/plans/2026-08-20-program-wiki-phase-1.md`, and
`.superpowers/sdd/2026-08-20-program-wiki-phase-1/progress.md`. Read-only —
nothing in the working tree, index, or HEAD was touched. No subagents were
dispatched; this review was performed end-to-end by one reader. No pytest runs
were needed: every doubt raised while reading was resolved by tracing the code
itself (module boundaries, exact `_collect`/`beat` control flow, prompt text,
git plumbing) rather than by executing anything.

## Verdict

**CHANGES REQUESTED** — not on architecture or on Phase-1 scope, both of which
are sound, but on three state-machine/contract gaps that the per-task reviews
could not see because each sits at a seam between two tasks' code. None of the
three requires a large fix; all three are the kind of thing worth closing
before this becomes the substrate every program depends on.

Blocking findings, one line each:

1. **Escaped-write runs are never quarantined or counted as a failure** — a
   batch that repeatedly triggers the containment check retries forever with
   no bound, unlike every other failure mode. (`wiki.py:187-207`)
2. **`report.json`'s `objects` field is never read** — `_collect()` marks the
   platform's *entire dispatched batch* ingested on any exit-0 run, regardless
   of what the agent's own report says it actually covered; no lint rule can
   detect the gap. (`wiki.py:209-216`, `wiki_agent.py:69-76`)
3. **The escape-detection mechanism can be silently defeated by the agent
   running `git commit` itself** — nothing in the prohibitions list forbids
   it, and the agent has unrestricted bash via `--dangerously-skip-permissions`.
   (`wiki_prompts.py:53-66`, `wiki.py:140-168`)

No deferred item is overturned. Item 1 (uncommitted autofix writes) is
ratified as correctly deferred, with one added nuance below.

## Findings

### Critical

None. Nothing in this branch corrupts state, crashes a process outside its
own beat, or loses already-ingested content.

### Important

**1. Escaped writes bypass the entire failure/quarantine mechanism —
`wiki.py:171-234` (`_collect`).**

Trace the branch at `wiki.py:189-207`:

```python
if status == "ok":
    escaped = _escaped(substrate, program.id, ...)
...
if status == "ok" and escaped:
    state["last_run"]["status"] = "escaped"
    substrate.commit(...)
    return f"wiki: {kind} ESCAPED — batch not recorded"
```

This `return` happens *before* line 223's `state["failures"] += 1` and before
the `state["ingested"][...]` loop at line 212. So an escaped run:

- Does not increment `state["failures"]` (only the `else` branch at
  `wiki.py:223-231`, reached for `status == "failed"`, does that).
- Does not mark its objects ingested (the comment at `wiki.py:203-204`
  explains this is deliberate — an agent that wrote outside the bundle may
  have written the wrong thing inside it too).
- Does not touch `state["quarantined"]`.

The objects stay in `pending_objects()`'s result set — neither ingested nor
quarantined — so the *same* batch is picked up again on the next eligible
beat. If whatever causes the escape is deterministic for that batch (a
specific object's content triggering a consistent agent misstep, or a bug in
a future prompt revision), the run repeats indefinitely: launch → escape →
commit a "wrote outside the bundle" commit → repeat. Every other failure mode
in this state machine is bounded by `COSCIENCE_WIKI_MAX_FAILURES`; this one
isn't bounded at all. It is visible per-beat in the status line and in
`state["last_run"]["escaped"]`, so an operator watching the log will notice,
but nothing in the system self-heals it — the only recovery path is a manual
edit to `state.json` to add the oids to `quarantined`.

Confirmed no test exercises this: `test_writes_outside_the_bundle_block_recording`
(`tests/test_wiki_beat_collect.py:203-215`) asserts `state["ingested"] == {}`
and `state["last_run"]["escaped"]`, but never asserts anything about
`state["failures"]`, and no test drives a second consecutive escape.

*Fix*: increment `state["failures"]` on the escaped branch too (and let it
participate in quarantine once past threshold), or give escaped its own
counter with the same threshold. Either is a few lines in `_collect`.

**2. `report.json`'s `objects` field has no consumer — `_collect()` trusts its
own dispatch list, not the agent's account of what it did.**

`wiki_prompts.py`'s housekeeping section instructs the agent to write
`report.json` as `{pages_created, pages_updated, objects, notes}`
(`wiki_prompts.py:78`). But grep the whole tree for where `report["objects"]`
or `report.get("objects")` is read after `collect()` returns it:

```
$ grep -n '"objects"' src/coscience/wiki.py src/coscience/wiki_agent.py
src/coscience/wiki_prompts.py:78:  "objects": ["result:r1"], ...
```

Nowhere. `_collect()`'s ingestion loop (`wiki.py:209-216`) is:

```python
if status == "ok":
    objects = {o.oid: o for o in wiki_store.program_objects(substrate, program.id)}
    for oid in batch:
        obj = objects.get(oid)
        state["ingested"][oid] = {"hash": ..., "at": now, "run": run_id}
```

`batch` here is `run["batch"]` — the list the *platform* dispatched at launch
time (`wiki.py:111`), not anything the agent reports back. So on any exit-0
run, every object the platform sent is marked ingested, whether or not the
agent's own `report.json` says it actually got to all of them. Concretely:
a batch of 4 objects where the agent, following its own instructed four-pass
protocol, decides object 3 is a near-duplicate not worth a page, or simply
runs out of turn budget after 2 objects and (per `_PROHIBITIONS`, "do fewer
objects well and say so in the report") honestly reports covering only 2 —
all 4 still get marked `ingested`. `pending_objects()` will never surface the
other 2 again. This is not a crash-path bug; it happens on the *intended*,
well-behaved path the prompt explicitly asks for ("do fewer objects well and
say so").

This directly contradicts spec §8.6's stated safety net: *"if \[report.json\]
is missing, the run is still ok with unknown counts, and lint will find
anything wrong."* Lint cannot find this: none of the six rule groups in
`wiki_lint.py` cross-check `program_objects()`/ingested oids against which
pages actually resulted from them. `src/missing` (`wiki_lint.py:351-380`
region) only checks the reverse direction — a *source page's* `origin`
pointing at an object that no longer exists — never "an object that should
have produced a page but didn't." So §8.6's claim is the "plan mandated
something that is simply wrong" case this review is explicitly for: the
stated mitigation does not cover the failure mode it's invoked for.

No test drives this either — none of `tests/test_wiki_beat_collect.py`'s
cases construct a `report.json` with a narrower `objects`/`pages_created`
list than the dispatched `batch` and check what gets marked ingested.

*Fix*: either reconcile `report.get("objects")` (or infer coverage from
`pages_created`/`pages_updated` + `iter_pages()`) against `batch` before
marking ingestion, only marking the intersection ingested and leaving the
rest pending — or, if under-coverage is meant to be acceptable, drop the
`objects` field from the schema and correct §8.6's claim about lint. As
written, the schema promises reconciliation the code doesn't perform.

**3. The escape-detection mechanism can be silently defeated by the agent
committing its own changes.**

`_escaped()` (`wiki.py:160-168`) and `_dirty_paths()` (`wiki.py:140-157`) work
by diffing `git status --porcelain` before and after the run — i.e., they see
*uncommitted* working-tree changes only. The agent runs with
`--dangerously-skip-permissions` and unrestricted bash (only `Monitor` is in
`--disallowedTools`; see `wiki_agent.py:40-49`). Nothing in
`_PROHIBITIONS` (`wiki_prompts.py:53-66`) forbids running `git add`/`git
commit`. The five prohibited actions are: writing outside the bundle,
computing hashes, backgrounding work, deleting/emptying pages, and writing a
`verified:` entry — git operations are not mentioned.

If the agent runs `git commit` for any reason (a plausible "helpful" reflex
for a coding agent operating inside a git-tracked directory — nothing here is
exotic, it's the same instinct that makes coding agents `git init` unprompted
elsewhere), any write it made — including one outside
`programs/<pid>/wiki/` — is no longer "dirty" by the time `_collect()`
samples `_dirty_paths()` again. `git status --porcelain` reports nothing, so
`_escaped()` returns `[]`: the exact writes the containment check exists to
catch go undetected. This isn't a race condition or an edge case in the
`git status` parsing — it's a direct, deterministic bypass of the one safety
net spec §13.1 relies on ("detection, not prevention").

This narrows an already-accepted residual risk (§13.1 explicitly declines to
prevent escapes, only detect them) rather than introducing a new category —
so I'm not calling it Critical — but it means the one detection mechanism
that exists has a known, simple bypass that the prohibitions list should at
minimum name explicitly.

*Fix*: add "Do not run `git add`, `git commit`, or any other git command" to
`_PROHIBITIONS`. This doesn't make the check airtight (the agent could still
disobey), but right now the omission looks like it wasn't considered at all
rather than accepted as residual risk.

### Minor

**4. `Dispatcher.run_one_cycle`'s end-of-cycle commit can race with a wiki
collect's own commit in the same cycle — `dispatcher.py:188-190` vs.
`wiki.py:206,233`.** When `report.wiki` is non-empty because a wiki beat
already called `substrate.commit(...)` inside `_collect`, the dispatcher's
own `self.substrate.commit("dispatch cycle")` at the end of the loop fires a
second `git commit` with (correctly) nothing new to add — tolerated via
`check=False` in `Substrate.commit` (`substrate.py:546-553`, "tolerate
nothing to commit"). Harmless, but worth knowing it's not a bug if seen in
logs: two commit attempts per cycle when a wiki run collects, one a no-op.

**5. `cli.py`'s `wiki` command's default beat loop has no exception guard,
unlike `Dispatcher._wiki_beat`.** `cli.py:299-305`:

```python
agent = WikiAgent()
now = time.time()
for program in programs:
    line = wiki.beat(substrate, program, now, agent)
```

versus `dispatcher.py:193-203`'s explicit `try/except Exception`. All six
`tests/test_cli_wiki.py` tests monkeypatch `wiki.beat` directly, so none
exercise a real exception through this path. This is very likely intentional
— a CLI invocation surfacing a raw traceback rather than swallowing it is
normal CLI behavior, and is arguably what you *want* for a diagnostic
first run — but flagging it since it's a real asymmetry between the two
callers of `wiki.beat`. See "Readiness for the live run" below for why this
matters specifically now.

**6. `_is_linked`'s bare-slug match broadens beyond wikilink shape (already
in the deferred digest as item 3; verified, not re-scored as a new finding)** —
see deferred-item verdicts.

## Cross-seam traces

### (a) result saved → `program_objects` → `pending_objects` → batch →
### ingest prompt → `report.json` → collect → `state.json`'s `ingested`

Traced end to end through `wiki_store.program_objects` (`wiki_store.py:268`),
`pending_objects` (`wiki_store.py:308`), `beat()`'s batch selection
(`wiki.py:105-111`), `wiki_prompts.render_ingest` (carries oid, title,
absolute path, `origin_hash`, platform-assigned slug per object —
`wiki_prompts.py`, contract-tested in `tests/test_wiki_prompts.py`), through
to `wiki_agent.collect()` → `read_report()` (`wiki_agent.py:69-76`, missing or
corrupt `report.json` silently becomes `{}`), into `_collect()`'s ingestion
loop.

**What actually breaks in this seam**: the trip from "agent's account of what
it did" to "what gets recorded as ingested" is not a pipe — `report.json`'s
`objects` field is produced but never consumed (Important finding 2 above).
The seam that *is* honored end-to-end is object identity: `object_hash()`
(sha256 for a result's file bytes, or sha256-over-sorted-relpaths for an
artifact's version directory — `wiki_store.py:239-267`) is computed
identically at dispatch time (for the prompt's `origin_hash`) and at
`pending_objects()` drift-detection time, so a result edited after ingestion
is correctly detected as drifted and re-offered (`tests/test_wiki_objects.py:
test_pending_excludes_ingested_but_returns_drifted`). Quarantine correctly
removes an oid from `pending_objects()`'s candidate set
(`wiki_store.py:308-330`). The one gap is the report→ingestion link.

### (b) page written → `write_page` → `iter_pages` → `parse_page` → lint rule
### → `autofix` → `write_page` round-trip

Traced through `wiki_okf.parse_page`/`render_page` (delegates YAML parsing to
`coscience.frontmatter_io.parse`, per the Task 3 ruling) and confirmed by
`tests/test_wiki_okf.py:test_render_round_trips` and
`test_unknown_keys_are_preserved` — an unrecognized frontmatter key
(`weird_key: kept`) survives a full parse→render→parse→render cycle
byte-identically, satisfying OKF v0.2's "never drop unknown keys" conformance
requirement. `autofix()` (`wiki_lint.py:304-340`) round-trips too:
`tests/test_wiki_lint_links.py:test_autofix_is_idempotent` and
`test_autofix_related_section_append_is_idempotent` both confirm a second
`autofix()` pass over already-fixed pages produces zero further changes and
zero findings — my earlier suspicion (from partial reading) that the
`# Related` append might not be idempotent across cycles was wrong; there is
a direct test for exactly that case and it passes. `AUTOFIXABLE` is exactly
`{"link/wikilink", "rel/no-link"}` (`wiki_lint.py:199`), matched by
`test_autofixable_set`, and no fixed page reintroduces a finding it didn't
have before (`test_autofix_appends_a_relation_link_to_satisfy_containment`
explicitly checks `rel/no-link` is gone from a fresh `lint()` pass over the
fixed output). This seam is sound.

### Failure paths in `beat()`'s state machine

- **Crash between launch and collect**: covered by `collect_grace()`
  (`wiki.py:51-52,178-185`) — a dead process with no `agent.exit` is treated
  as "collecting" for one grace window, then "failed" past it. Tested
  (`test_dead_process_without_exit_file_waits_out_the_grace_then_fails`).
  Sound.
- **Process killed without `agent.exit`**: same path as above — indistinguishable
  from a slow filesystem until the grace window elapses, by design.
- **Missing/truncated/malformed `report.json`**: `read_report()`
  (`wiki_agent.py:69-76`) catches `OSError`/`ValueError` and returns `{}`;
  exit 0 is still "ok" per spec §8.6. This is where Important finding 2 lives
  — the "ok despite missing report" path is exactly the one with no
  reconciliation.
- **`COSCIENCE_WIKI_MAX_FAILURES` exhaustion**: correctly resets
  unconditionally on crossing threshold (the Task 10 fix), gates the
  quarantine *action* on non-empty batch so a lint run's perpetual `[]` batch
  can't itself be quarantined. Verified against the fixed code at
  `wiki.py:223-231`. Sound, tested
  (`test_lint_failures_reset_the_shared_counter_so_a_fresh_ingest_gets_its_own_chances`).
- **Concurrent beats for two programs**: cannot happen within one dispatcher
  process (`run_one_cycle`'s `for program in ...` loop is sequential). Across
  two hosts sharing one substrate (per `CLAUDE.md`, more than one host may run
  the full platform), `state_guard`'s flock is the same idiom
  `artifacts._lock_guard` already uses — no new risk introduced by this
  branch; whatever cross-host guarantees (or lack thereof) that idiom has
  today, wiki inherits unchanged.
- **The one path that *can* wedge a program (bounded per-cycle, unbounded
  over time)**: repeated escapes — Important finding 1.

## Agent blast radius (focus area 3)

**What stops it writing outside `programs/<pid>/wiki/`**: nothing at launch
time. `cwd` is the bundle (`wiki_agent.py:36-38`) as a *convention*, and the
run uses `--dangerously-skip-permissions` — the docstring at `wiki.py:163-164`
says this outright ("cwd is a convention rather than a sandbox"). The only
enforcement is after-the-fact detection via `_escaped()`.

**Can `dirty_before`/`dirty_after` be defeated?** Yes, deterministically, if
the agent commits its own changes — Important finding 3. There is also a
narrower, racier exposure: `_dirty_paths()` snapshots the *entire* substrate
repo's working tree, not anything scoped to the program, and the repo-wide
flock is held only while `beat()`'s `with wiki_store.state_guard(...)` block
is open — i.e., during the brief launch and brief collect calls, never during
the multi-minute detached run in between. A concurrent writer elsewhere in
the substrate (a sprint worker, a different program's wiki run) that happens
to have an uncommitted, dirty write at the exact instant `_collect()` samples
`dirty_after` would be misattributed to this run as an "escape." Most other
writers in this codebase commit promptly via `substrate.commit()`'s `git add
-A`, which narrows the window a lot, and a false escape's only consequence is
a spurious retry (not data loss, given finding 1's caveat that repeated
escapes aren't bounded) — so I'm not scoring this as its own finding, but it
compounds with finding 1: a falsely-flagged run also never gets quarantined
or retried-with-backoff, it just retries at the same cadence indefinitely.

**What happens to offending writes when the check fires?** Nothing is
reverted (`_escaped`'s docstring explains why: reverting risks destroying a
concurrent actor's legitimate work). The run's batch is deliberately not
recorded as ingested (`wiki.py:203-204`), `state["last_run"]["status"]` is set
to `"escaped"`, and a commit is made capturing whatever is dirty at that
point — meaning the escaped write itself gets committed into substrate
history as part of that same commit, permanently, alongside whatever the
agent legitimately wrote inside the bundle. Detection does not mean
containment of the historical record; it only means the platform won't treat
that batch as done.

## Linter coherence (focus area 4)

20 rule codes confirmed by direct grep against `wiki_lint.py` (19 matched by
`"[a-z]+/[a-z-]+"`, plus `human-notes/removed` at `wiki_lint.py:395` whose
hyphenated prefix doesn't match that pattern) — matches spec §9's frozen
table and the Task 14 ruling that "19 rules" in the plan's prose was an
arithmetic error, not a missing rule. Severities cross-checked against every
assertion in `tests/test_wiki_lint*.py`: no contradiction found (errors:
`okf/bad-yaml`, `okf/missing-type`, `okf/index-frontmatter`,
`page/duplicate-slug`, `rel/unknown-type`, `rel/no-link`, `rel/no-source`,
`src/hash-drift`, `src/missing`, `src/is-concept`, `human-notes/removed`;
warns: `page/stub`, `page/stale`, `page/near-duplicate`, `link/broken`,
`link/wikilink`, `rel/dangling`; infos: `page/orphan`, `rel/cycle`,
`trust/unverified-stable`). No rule contradicts another observed in testing —
`rel/cycle` and `contradicts` relations are explicitly kept separate
(`test_contradicts_is_not_treated_as_a_cycle`), and the acyclic-type tuple
only covers directional-progress relation types, which is the correct
scoping.

`autofix()` idempotency and non-regression are both directly tested (see
trace (b) above) — confirmed sound, no gap here despite my initial suspicion
while still mid-read.

The one genuine linter gap already covered above is structural, not a rule
bug: no rule in any of the six groups checks object→page coverage, which is
exactly the hole Important finding 2 exploits. This isn't a missing 21st rule
code needed to fix finding 2 — the fix for 2 belongs in `_collect`'s
reconciliation logic, not in the lint layer, since lint only ever sees pages
that exist, never the platform's own dispatch ledger.

## Dead ends / orphans (focus area 6)

- `report.json`'s `objects` field (Important finding 2) — specified,
  requested from the agent, never consumed.
- Everything else flagged in the deferred digest's "cosmetic" section
  (unused `import time` in `test_wiki_objects.py`, `StreamResult.usage` typed
  as bare `dict`, the forward-referencing docstring, `object_hash`'s stray
  empty-string-literal docstring opener, the `ERROR`/`error —` case
  inconsistency, the wrong line-count claim in `task-4-report.md`) — verified
  present, not worth separate findings, exactly as the digest says.
- No other orphaned consumer, unread constant, or duplicated logic found
  across the three lint tasks (12/13/14) — `_okf_rules`, `_page_rules`,
  `_link_rules`, `_relation_rules`, `_source_rules`, `_trust_rules` each own a
  disjoint slice of the frontmatter/body surface with no overlap.

## Deferred-item verdicts

In the order listed in `deferred-for-final-review.md`:

1. **Uncommitted autofix writes in `beat()`'s launch half** —
   **CORRECTLY DEFERRED.** Confirmed bounded: `Substrate.commit()` is `git
   add -A` at repo root (`substrate.py:549`), so any never-collected autofix
   write is swept into the *next* successful commit by *any* actor anywhere
   in the substrate repo, not necessarily this program's own next wiki
   commit — slightly broader commingling than the digest's framing implies,
   but still: never lost, never silently discarded, only mis-attributed in
   the git log. Not a merge blocker; giving these writes their own commit
   before `agent.launch()` (Resolution 1 in the digest) is a cheap
   follow-up, not required now.
2. **`rel/unknown-type` is overloaded** — **CORRECTLY DEFERRED.** Splitting
   needs a 21st code against a spec-frozen 20-row table; the overload only
   degrades `render_report`'s grouping readability, not correctness.
3. **`_is_linked`'s bare-slug match** — **NOT ACTUALLY A PROBLEM.** It only
   ever *suppresses* an info-severity `page/orphan` finding, never produces a
   false positive or a functional error; the blast radius is "the linter is
   occasionally quieter than it should be" on an already-lowest-severity
   rule.
4. **`previous_bodies` spawns one `git show` per page** — **CORRECTLY
   DEFERRED.** Phase-1 bundle sizes make this a non-issue; premature to batch
   now.
5. **Usage gate evaluated before the pending check** — **CORRECTLY
   DEFERRED.** An extra subprocess call per active enabled program per
   dispatch cycle when nothing is pending is a real but small inefficiency at
   phase-1 program counts.
6. **`agent.launch()` inside `state_guard`'s repo-wide flock** —
   **CORRECTLY DEFERRED.** Launch itself is fast (subprocess spawn + a few
   file writes); the serialization cost is small at phase-1 scale.
7. **`state_guard` is not reentrant** — **CORRECTLY DEFERRED.** Inherits
   `artifacts._lock_guard`'s existing idiom and its existing risk profile;
   harmless under the beat's single-writer design, which is the only thing
   calling it.
8. **Every clean `state_guard` exit writes `state.json`** —
   **NOT ACTUALLY A PROBLEM.** Confirmed via direct trace of
   `@contextmanager` semantics and `tests/test_wiki_state.py`'s
   `test_state_guard_saves_on_clean_exit` /
   `test_state_guard_does_not_save_on_exception` — this is exactly the
   specified behavior, not an oversight.
9. **Wiki runs don't increment `report.beaten`** — **CORRECTLY DEFERRED.**
   Confirmed at `dispatcher.py:165` (`report.beaten += 1` only in the sprint
   lease loop) vs. `dispatcher.py:183-185` (wiki beats append to
   `report.wiki`, never touch `beaten`). Cosmetic accounting gap in a status
   line; already flagged for a phase-2 look.
10. **`wiki_model` written to frontmatter on every save** — **NOT ACTUALLY
    A PROBLEM.** Matches `pm_model`'s pre-existing, already-accepted
    behavior; no new inconsistency introduced.

## Readiness for the live run (Task 15 Steps 7–8, held back)

Nothing found would *crash* a single `coscience wiki --once --program <pid>
--repo <scratch-copy>` invocation outright under normal conditions — the
per-module error handling (`read_report`, `_dirty_paths`, `previous_bodies`
all fail soft) is solid. Two things are worth knowing before that run:

- **Minor finding 5** applies directly: `cli.py`'s beat loop
  (`cli.py:299-305`) has no exception guard, so if `wiki.beat()` does raise
  during the live run (e.g., a real substrate has content shapes the test
  fixtures don't — a legacy `program.md` with unusual frontmatter, a `git`
  binary quirk on the run host), the CLI invocation will exit with a raw
  Python traceback rather than a graceful line. For a first supervised run
  this is arguably desirable (you want to see it, not have it swallowed),
  but don't be surprised by it, and don't read a traceback here as evidence
  the branch is broken — it's the intended (if asymmetric) behavior of this
  particular entry point.
- **Important finding 2** is the one to watch for empirically: after the live
  run, check that `state["ingested"]` actually covers everything
  `report.json` claims — or more simply, spot-check that every object in the
  dispatched batch produced a corresponding page or a `notes` explanation.
  Nothing will flag it if it doesn't; that's the finding.
- Important findings 1 and 3 require either a repeated deterministic escape
  or the agent choosing to run `git` itself — neither is expected on a
  single clean run, so they shouldn't affect Step 7, but are worth knowing
  going in so an ESCAPED result isn't mistaken for the run simply failing.

No finding here should be read as a reason to *withhold* authorization for
Step 7 — the live run is exactly how finding 2 would first become visible
empirically, and running it produces useful signal either way.
