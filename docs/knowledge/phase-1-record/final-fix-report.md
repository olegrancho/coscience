# Final review fix wave — report

Branch `feat/program-wiki`, base `3d68d3e`. Executed 2026-08-21 against the
three Important findings in `open-fix-brief.md`. All three landed. No ruling was
overturned.

**Suite:** 1029 passed at base → **1036 passed, rc=0** after the wave (7 new
tests, no test removed or weakened). Run on the sandbox dev checkout with
`~/venvs/coscience-dev/bin/python -m pytest`.

---

## Fix 1 — escaped runs are bounded by the failure counter

**`src/coscience/wiki.py:171`** — new `_count_failure(state, batch) -> bool`.
Increments the shared `state["failures"]`, and at `max_failures()` resets it to 0
and appends the batch to `state["quarantined"]`. Returns True only when that
quarantine actually happened.

**`src/coscience/wiki.py:246`** — the escaped branch now calls it before
returning. **`wiki.py:264`** — the failed branch's inlined counter/quarantine
block was replaced by `elif _count_failure(state, batch):`, so both kinds share
one code path and cannot drift apart.

Unchanged on purpose: an escaped run still does not mark its batch ingested, and
still returns `wiki: {kind} ESCAPED — batch not recorded` with
`state["last_run"]["status"] == "escaped"`.

**Tests:** `tests/test_wiki_beat_collect.py:131` extends the existing
`test_writes_outside_the_bundle_block_recording` with `failures == 1` rather than
duplicating it; `:235` `test_consecutive_escapes_keep_counting` (1 → 2); `:246`
`test_escapes_quarantine_the_batch_at_the_threshold` (at
`COSCIENCE_WIKI_MAX_FAILURES=2` the batch is quarantined and `failures` resets to
0, `ingested` still empty).

The new `_escape_cycle` helper (`:221`) drives the guard honestly: `_dirty_paths`
is patched through a mutable holder so the launch beat sees a clean tree and the
collect beat sees the escape. A single fixed patch would have made
`dirty_before == dirty_after` and produced no escape at all.

## Fix 2 — `report.json`'s `objects` is reconciled against the dispatched batch

**`src/coscience/wiki.py:190`** — new `_reconciled(batch, report)`. Returns
`batch` filtered to the oids the agent's report claims, preserving `batch` order.
**`wiki.py:253`** — the ingestion loop iterates that instead of `batch`.

Behaviour, exactly as ruled:

| `report["objects"]` | Result |
|---|---|
| absent, or not a list (incl. a bare string) | whole `batch` ingested — spec §8.6 preserved |
| list | `batch ∩ objects`; non-string entries ignored, undispatched oids ignored |
| `[]` or empty intersection | nothing ingested, run still `ok`, `failures` **not** incremented |

**Tests:** `tests/test_wiki_beat_collect.py:272` (2 of 4 covered → exactly those 2
ingested, the other 2 come back through `pending_objects()`); `:285` (no
`objects` key → all 4, the §8.6 regression guard); `:295` (report naming
`result:nope`, `sprint:s9` and the integer `17` ingests only the dispatched
`result:r1`); `:305` (`"objects": "result:r0"` falls back to all 4 rather than
iterating the string's characters); `:312` (`[]` ingests nothing, line is still
`wiki: ingest ok`, `failures == 0`, all 4 still pending).

## Fix 3 — the agent is forbidden from touching git state

**`src/coscience/wiki_prompts.py:57`** — one bullet added to `_PROHIBITIONS`,
second in the list, matching the surrounding voice: no `git commit`, `git add`,
`git stash`, `git checkout`, `git reset`, or anything else that changes the
repository's git state, because the platform commits on the agent's behalf at
collect time. The launch line and `--verbose` were not touched.

**Test:** `tests/test_wiki_prompts.py:45` extends the existing
`test_ingest_prompt_states_the_prohibitions` to assert all five command strings
reach the rendered prompt. No new test file.

### Residual risk, stated plainly

**This does not close the hole.** `_escaped()` and `_dirty_paths()` diff
`git status --porcelain` before and after the run, so they only ever see
uncommitted working-tree changes. The agent runs with
`--dangerously-skip-permissions` and unrestricted bash. A prompt is guidance, not
an enforcement boundary: an agent that commits anyway — through confusion, a
helper script, or a tool that commits as a side effect — still makes its
out-of-bundle writes invisible to the guard, and Fix 1's counter never fires
because the run looks clean.

Closing it properly means comparing the substrate's commit SHA across the run, or
constraining the agent's tools. Both are real changes to the agent seam and were
ruled out of this fix wave deliberately; they belong in their own task with their
own review.

## Deviations

One, minor and deliberate: on the escaped path the returned line stays exactly
`wiki: {kind} ESCAPED — batch not recorded` **even when that escape triggered
quarantine**, because the brief said to preserve the existing return string and
the dispatcher and CLI both match on it. The failed path still switches to
`wiki: {kind} quarantined N`. So an escape-driven quarantine is visible in
`.wiki/state.json` and through `coscience wiki status`, but not in the dispatch
beat line. If that asymmetry is unwanted, the fix is one line at `wiki.py:246`.

## Unrelated finding, not fixed here

`pyproject.toml:10` pins `mcp = ["mcp>=1.2"]`. `mcp` 2.0.0 removed
`mcp.server.fastmcp`, which `src/coscience/mcp_server.py:12` imports, so a fresh
install of the `mcp` extra fails collection on `tests/test_mcp_entry.py`,
`tests/test_mcp_server.py` and `tests/test_transport_programs.py`. Hit while
provisioning the dev venv; worked around there with `pip install "mcp<2"`. The
production venv has no `mcp` installed at all, so it never saw this. Out of scope
for this branch — worth a one-line upper bound on `main`.
