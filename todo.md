---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 25
last_updated: 2026-09-11
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

Every program wiki is accurate about itself: it passes lint, and a run's report
matches what that run actually changed.

### D1. Fix the 29 dangling relations across p2, p3 and p5

Add body links for every relation lint reports as declared but never linked from
the prose.

`rel/no-link` is the only error rule firing anywhere: 21 in p2, 5 in p5, 3 in p3,
0 in wikitest. Everything else is info-level and expected. That p2 carries the
most while being the healthiest wiki suggests the agent declares relations in
frontmatter and forgets the prose link — a prompt fix rather than 29 hand edits.

### D2. Stop reporting pages as created when they already existed

Have the ingest report name what the run changed, not what it believes it wrote.

r0017 reported 4 pages created and 9 updated; diffing the bundle against the
pre-run commit shows 6 files changed, and all four "created" pages already
existed with the right `origin_hash`. The counts reach `last_run.pages_created`
and the dashboard, so a run that mostly confirmed existing work reads as a
productive one. The run already computes `dirty_before` for the containment
check, so the honest numbers are a diff away rather than the agent's own account.

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

## I. Wiki responsiveness

A finished sprint reaches its program's wiki while the result still matters,
without anyone waiting on a heartbeat or asking for it.

### I1. Ingest a result when its sprint finishes

Have sprint completion wake the wiki for that program instead of leaving the
object to the next scheduled beat.

Ingest is currently pull-only: `wiki.beat` finds pending objects whenever the
dispatcher gets to it, so a result can sit uningested for hours and the wiki
reads as stale exactly when someone has just looked at the sprint. The hook
belongs where the sprint lands its result, and the housekeeping lease plus the
`wiki:<program>` slot already serialise what it would trigger. Note the tension
with J3: firing per sprint makes batches smaller, so the fixed prefix is
amortised over less material.

## J. Wiki run cost

A wiki run's token bill is proportional to the material it ingests, not to the
harness wrapped around it.

### J2. Replace the wiki agent's default system prompt

Give the run a lean `--system-prompt` in place of Claude Code's built-in one.

Measured on the p5 bundle, the launch prefix is 21,615 tokens with the stock
setup, 14,096 once `--tools` drops the unused schemas (J1), and 7,870 with a
one-paragraph system prompt as well — another ~6.2k tokens, re-read on all 35
turns of a run, worth roughly 12% of an ingest's cost. The catch is that the
default prompt carries Claude Code's own behavioural scaffolding for editing
files carefully, and this agent edits a knowledge base unattended, so the saving
has to be weighed against a run that is measurably worse. Needs a real ingest on
each setting compared page by page, not a token count alone.

### J3. Choose the batch hold's max-wait, or leave it off

Decide what `COSCIENCE_WIKI_MAX_WAIT` should be on the live substrate; the
mechanism is built and defaults to off.

The hold ships: a short batch waits for company until `COSCIENCE_WIKI_BATCH`
objects are pending or the wait expires, and 0 (the default) means no hold, so
nothing changed until someone sets it. What is left is the number, and the
measurement argues for leaving it small or unset — results land a median of
5.5-24.6h apart per program, so a wait long enough to actually fill a batch of
four costs days of staleness. The $1.38-vs-$0.73 gap is backlog against steady
state, not a lever, and the existing code already batches whatever is pending.
Where a hold does pay is a burst of sprints finishing together, which is what I1
will produce — so this is worth revisiting once I1 lands, sized to a burst.

## L. Wiki quality improvement

The wiki answers the questions actually brought to it, and we know that from
evidence rather than impression.

### L1. Write the question set, a few per program

Collect the questions and requests Oleg wants a program's wiki to answer, and
keep them next to the program.

This is the half only Oleg can supply and it blocks the rest of the block. The
motivating complaint is that the wiki reads "relatively okay, but in some ways
not quite what I need" — an impression formed while working, and one he expects
to be biased. Questions written down BEFORE any agent runs are what convert that
into something falsifiable: a wiki either answers them or it does not. One-off
for now, not a standing suite; making them re-runnable is a later decision.

### L2. Answer each question with a traced agent

Run one subagent per question against the program's bundle, and keep its stream.

No API and no wiki engine are needed: the bundle is markdown on disk, so an agent
with Read and Glob already does "get page, follow its links", and `ls concepts/`
is the concept listing. The retrieval trace is free — every `Read` with its path
and size is already in `agent.out`, which is how r0017's 18 reads and 127KB of
context were reconstructed. What each run yields is an answer, the pages it
reached, the order it reached them in, and what it did with them.

### L3. Debrief each agent after it answers

Ask the agent, in a second turn, what it could not find and what misled it.

The trace shows what an agent read; only the agent can say what it went looking
for and failed to find, which page it expected to exist, or where two pages
disagreed and it had to guess. Absence is the defect class a wiki hides best and
the one that matters most here. Cheap — the session is already open, so the
debrief costs one more turn on a context that is already paid for.

### L4. Read the traces against Oleg's own account

Compare what the agents struggled with to where Oleg finds the wiki lacking.

The point of the exercise is the delta: where the traces confirm the impression,
where they contradict it, and where they surface problems nobody had noticed.
Agreement between an independent trace and a held opinion is worth more than
either alone, and disagreement is where the bias was.

### L5. Fix what L4 justifies

Make the wiki changes the evidence supports, and nothing it does not.

Deliberately unspecified: the whole point is that the work is chosen by findings
rather than by intuition. The likely surfaces are the ingest and lint prompts in
`wiki_prompts.py`, the page schema and relation vocabulary in the bundle's
`CLAUDE.md`, and `index.md` as a retrieval entry point — but committing to any of
those now would be the same guessing this block exists to replace.

## H. Agent backends and models

Agent work is not locked to one CLI or one model, so the platform can follow
whatever is cheapest or most capable for a given job.

### H1. Work out what a Codex backend would take

Explore first: map every place the platform assumes the `claude` CLI, and report
what an alternative executor would have to satisfy.

The coupling is wider than the binary name. Three call sites default
`claude_bin="claude"` (`claude_executor.py`, `wiki_agent.py`, `chat_agent.py`)
and each builds a shell line around Claude Code's own flags —
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`, `-p`, `--model`,
`--dangerously-skip-permissions`, `--resume`. Deeper in, `agent_stream.py` parses
Claude Code's stream-json envelope, and the whole call log reads cost, turns and
`rate_limit_event` out of that same shape. A second backend needs an answer for
each of those, plus session resume, which is what the worker leans on after an
ambiguous exit. Output is a written finding, not code: whether this is an
adapter behind the existing three classes or a deeper seam.

### H2. Add Fable 5.1 to the model picker

Put `claude-fable-5-1` in `MODEL_OPTIONS` and confirm a real run on it.

`MODEL_OPTIONS` (`frontend/src/components/ui.tsx`) is the whole list the PM,
wiki and sprint pickers offer, and it stops at Opus 5 / Sonnet 5 / Haiku 4.5.
The backend passes `model` through as free text and `ModelSelect` already renders
an unlisted value as-is, so nothing needs to change server-side — this is one
entry, its label, and a run that proves the flag is accepted end to end.

# Done

### K1. Group the programs overview by state

Active, paused and closed each head their own section with a count, and closed is
folded until asked — closed previously had no visual treatment at all.

### F7. Stamp the 5h window from the run's own stream

`limits_before` now comes from the first `rate_limit_event` the run reports, so
the column fills without an OAuth token the box may not have.

### J1. Stop paying for tool schemas the wiki never calls

Wiki runs launch with `--tools Read,Edit,Write,Bash`, cutting the launch prefix
from 21,615 tokens to 14,096.

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
