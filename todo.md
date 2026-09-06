---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 20
last_updated: 2026-09-06
---

# To Do

## A. Wiki ledger integrity

The ledger credits every object whose page is actually in the bundle, so no
result is silently missing from its program's wiki.

### A4. Warn when the ledger and the bundle disagree

Have the dispatcher notice a bundle holding pages for objects the ledger still
calls pending, instead of waiting for someone to audit it.

`coscience wiki --reconcile` makes the divergence repairable but nothing detects
it: p3 sat with 49 pages behind an empty ledger for two days in silence. Cheapest
version is a count on the `--status` line; a stronger one runs the dry-run check
at dispatcher startup. Worth doing after B1 and B2, which should make the
divergence rare rather than routine.

## B. Ingest failure handling

A transient outage costs at most the object in flight, and never excludes good
content permanently.

### B2. Record ingest progress per object, not per run

Have the agent append each finished object to `progress.jsonl`, and read it in
`_collect` on failed runs.

`wiki.py` accounts per run but works per object, so a run killed at object 3 of 4
loses objects 1–2 despite their pages being committed. Needs a prompt clause in
`wiki_prompts.py` and a fallback branch in `_collect`, fed through the existing
`_reconciled` intersection so the agent still cannot widen its own mandate.

## D. Wiki content health

Every program wiki passes lint with no errors.

### D1. Fix the 29 dangling relations across p2, p3 and p5

Add body links for every relation lint reports as declared but never linked from
the prose.

`rel/no-link` is the only error rule firing anywhere: 21 in p2, 5 in p5, 3 in p3,
0 in wikitest. Everything else is info-level and expected. That p2 carries the
most while being the healthiest wiki suggests the agent declares relations in
frontmatter and forgets the prose link — a prompt fix rather than 29 hand edits.

## F. LLM call metrics

Every Claude call the platform makes is visible on Compute with what it cost,
what it was for, and how it ended.

### F5. Backfill the log from run history

Reconstruct past rows from the `agent.out` envelopes and cost sidecars already on
disk.

Every wiki run dir holds a result envelope with `total_cost_usd`, `modelUsage`,
`duration_ms` and a `rate_limit_event`; sprint dirs hold the worker sidecars. So
the history is recoverable rather than starting from zero, and it is the only way
the 08-30..09-01 wiki spend ever reaches the page.

# Done

### B1. Stop counting rate-limit deaths toward quarantine

A run killed by a 429 is recorded as `deferred`: its batch stays pending for a
later beat but never counts toward the quarantine threshold.

### B3. Stop blaming wiki runs for other actors' writes

`_escaped` ignores the areas other subsystems write, and an escape is now
reported without discarding the batch or counting toward quarantine.

### F3. Record the 5h window either side of each call

`wiki_agent` feeds `record_limits` like the other call sites, and the launch
stamp falls back through `read_budget` instead of going blank after 15 minutes.

### F4. Add the call log to the Compute page

`/api/usage/calls` and the `CallLog` table on `/ledger`, with all ten columns,
filters, pagination, and a live-agent readout in the rail's Pulse.

### G1. Give housekeeping agents a slot pool

PM and wiki runs now take a `housekeepers` lease before launching, so the six-way
pile-up that drove the 5h window from 26% to 116% cannot recur.

### G2. Expose the housekeeping slot count in Compute

`housekeepers` appears as a gauge with −/+ steppers on the capacity card, set to 1
on the live substrate.

### F1. Record the three call sites that log nothing

All five sites — `pm`, `worker`, `wiki-ingest`, `wiki-lint`, `chat` — now open a
call at launch and close it at collect.

### F6. Move the call log out of git, keyed per substrate

New rows go to `~/.cache/coscience/runs/<name>-<hash>.jsonl`; the old in-substrate
file is read as a frozen archive and never written again.

### F2. Make a row describe a call, not just its end

A call is two events folded into one row, with program and sprint split apart and
a real status vocabulary.

### A3. Reconcile p3 and p5, commit the corrected state

Both quarantines are empty and every object in p3 and p5 is credited.
