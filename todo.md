---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 58
last_updated: 2026-09-13
---

# To QC

### F10. Declare a dead call lost without waiting out the grace

The dispatcher collects finished chat turns every cycle, and a call whose process
is gone reads `lost` at once instead of after 15 minutes (`24eae7e`, deployed 09-13 02:59).

**Check:** after deploy, send a chat message and reply nothing else — its row on
Compute turns `ok` within a few seconds of the reply finishing, without opening
the thread again. A run that ends normally can read `lost` for up to one dispatcher
cycle before its collect writes the end; a row that stays `lost` is a real death.
On a dashboard-only host with no dispatcher, chat turns still wait for a read.

### N1. Tell the PM what compute exists

The PM prompt carries a COMPUTE block — declared capacity, what running sprints
hold, and the rule to request only what a sprint's heaviest step uses (`0f6ae89`).

**Check:** the next p2 PM transcript (`.coscience/pm-p2.out`) shows the COMPUTE
block with the real totals, and the next sprints it proposes ask for no more CPU
than their work needs — none above capacity. Capacity is deliberately not a
fingerprint input, so raising it does not wake a PM by itself.

### N2. Flag a request larger than total capacity

The dispatcher reports sprints asking for more than the pool's total as
unrunnable instead of waiting, and the sprint page says why (`73a683d`).

**Check:** with capacity below a queued sprint's request, the dispatch log reads
`… · waiting N · unrunnable 1 (<id>)` and that sprint's page shows "Can't ever
start: needs cpu 24 but capacity is 16" under its compute. The sprint is only
flagged — never parked or edited.

### K6. Count the experiments waiting to run

The rail's pulse has a "waiting" row counting approved and queued sprints in
active programs, with any that can never start shown apart as "N can't start".

**Check:** the rail's waiting number matches the approved plus queued sprints of
active programs on the board, and a sprint made unrunnable (N2) moves from
"waiting" to "can't start". "awaiting you" still counts proposed sprints only.

### D2. Stop reporting pages as created when they already existed

A wiki run's created/updated counts are measured from a content-hash snapshot of
the bundle's pages taken at launch, not taken from the agent's report.

**Check:** after the next ingest on p2 or p5, compare `last_run.pages_created` and
`pages_updated` on the wiki card with `git diff --stat` of that run's commit over
`programs/<id>/wiki/{concepts,entities,syntheses,sources}` — they should agree,
where the report's own lists may name more. The run dir holds `pages_before.json`.

### A4. Warn when the ledger and the bundle disagree

`coscience wiki --status` runs the dry-run reconcile and appends "ledger behind
bundle: N unrecorded, M drifted — see --reconcile" for any program that is behind.

**Check:** `coscience wiki --status` on the live substrate prints no such suffix
today (every program agrees, as of 09-13 18:37); a program whose Source page
proves an object the ledger does not record shows `1 unrecorded`. Only the cheap
version was built — the dispatcher does not run this check.

### F5. Backfill the log from run history

`python -m coscience.call_backfill --apply` rebuilt 31 wiki calls ($57.70, 08-26 to
09-04) into the call log from run envelopes; all 45 worker sidecars were already
logged, so none were added (`dc74da6`).

**Check:** on Compute, look at wiki calls before 09-04 — p3 r0001–r0010 and p5
r0001–r0013 now appear, most `rate-limited` (spot-checked against their envelopes:
429 "session limit"). Rows carry `backfilled: true`, and a second run adds nothing.
The pre-backfill log is kept beside the live one as `*.pre-backfill-20260913`.

### B2. Record ingest progress per object, not per run

The ingest prompt has the agent append each finished object to `progress.jsonl`;
a failed or rate-limited run keeps those objects and counts the failure only
against the rest (`6e5ee11`).

**Check:** the next ingest run dir on p2 or p5 holds a `progress.jsonl` with one
line per object it finished. When a run is cut off (a 429 or a restart), the wiki
beat line reads `wiki: ingest deferred … (kept N of M)` and those N objects are in
the ledger's `ingested` while the rest stay pending.

### H1. Work out what a Codex backend would take

`docs/codex-backend.md` maps every `claude` coupling to its `codex exec`
equivalent and recommends an `AgentBackend` seam; Codex CLI 0.154.0 is installed
on Avatar at `~/.local/bin/codex` (`1a9b7ec`).

**Check:** read the verdict and the two gaps (per-call cost, usage windows) in
`docs/codex-backend.md`. Its last section is still open: it needs `codex login`
on Avatar, then one throwaway `codex exec --json` and one `codex exec resume` to
learn the event schema, quota behaviour and session-id field.

# To Do

## A. Wiki ledger integrity

The ledger credits every object whose page is actually in the bundle, so no
result is silently missing from its program's wiki.

## B. Ingest failure handling

A transient outage costs at most the object in flight, and never excludes good
content permanently.

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

## F. LLM call metrics

Every Claude call the platform makes is visible on Compute with what it cost,
what it was for, and how it ended.

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

The runner exists and is piloted, so this waits only on L1:
`python -m coscience.wiki_probe --program p2` reads `programs/p2/wiki-questions.md`
(a markdown list, one question per item) and writes `report.md`, `summary.json` and
both streams under `~/.cache/coscience/wiki-probe/p2/<stamp>/`. Each agent is
read-only in the bundle, starts from `index.md`, and may leave for raw results
only by saying so; the report flags every read outside the wiki. The 09-13 pilot
on p5 took 50s and $0.14 for one question on Sonnet 5, and the answering model
defaults to the program's planner model.

### L3. Debrief each agent after it answers

Ask the agent, in a second turn, what it could not find and what misled it.

The trace shows what an agent read; only the agent can say what it went looking
for and failed to find, which page it expected to exist, or where two pages
disagreed and it had to guess. Absence is the defect class a wiki hides best and
the one that matters most here. Built into the same runner as a `--resume` turn
with five fixed questions; on the pilot it cost $0.03 and surfaced a real defect
unprompted — the p5 canonical numbers ledger, billed as the single source of
truth, predates and omits the program's best result (0.8609, p5-c26).

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

## K. Dashboard legibility

The state of the work reads at a glance — without opening a page, counting cards
or decoding a slug.

## M. Delegated approval

Work does not stall waiting on human review: the PM can hold approval authority
for a stretch the human bounds, and the bound is enforced rather than trusted.

### M1. Let a human grant the PM bounded approval authority

Add a grant that lets the PM approve its own proposals until a stated limit is
reached, then lapses on its own.

Four limits, all measurable with what exists: the 5h window exhausted, the weekly
window exhausted (both from `usage_meter`), a wall-clock deadline, or N sprints
approved. The grant belongs on the program beside `activations`, which is already
the dashboard's record of what changed and when. Note this edits the state
machine: `docs/sprint-lifecycle.md` currently says `proposed → approved` is
**human only**, so that table and its rationale are part of this work, not a
footnote to it. It should also be revocable mid-flight, and it must lapse loudly
enough that nobody discovers weeks later that it expired.

### M2. Build the supercharge control

A button on the program that opens a modal for choosing the limit, and shows the
grant while it is live.

The modal is the whole UI: pick one of the four limits, confirm, and see what is
left of it afterwards — sprints remaining, time remaining, or which usage window
it is riding on. While a grant is live the program needs to say so unmistakably,
because a program approving its own work is the one state where a glance at the
dashboard must not be ambiguous. Depends on M1.

## H. Agent backends and models

Agent work is not locked to one CLI or one model, so the platform can follow
whatever is cheapest or most capable for a given job.

### H3. Pick the PM's model from the work the cycle is doing

Route each PM cycle to a model chosen by what woke it — the cheap one for
bookkeeping, the strong one for thinking.

The obstacle is not the picker, it is that a cycle is **one** Claude call
(`pm_agent.py:611`) returning all twelve kinds of output at once: proposals and
the report alongside idea pruning, re-ranking, `release_ids` and thread replies.
Brainstorming and housekeeping are fused, and `program.pm_model` is the only
knob. Splitting the cycle into a cheap pass and an expensive one would pay the
PM's large context twice, and its calls already run $0.31-$0.77 each.

The cheaper shape keeps one call and chooses the model from the trigger, which
the code already knows: `_context_payload` keys the PM's inputs by category and
the fingerprint diff that prints "idle — no input changed" already computes which
one moved. A landed result or a goals change is integration and wants the strong
model; a sprint status change, an idea comment or a feedback reply is mundane and
does not. Worth confirming against the call log that the mundane triggers really
are the cheap ones before wiring it.

## N. Requests that fit the compute

No sprint waits on a resource request the platform can never grant, and the PM
proposes work sized to the compute it actually has.

# Done

### F9. Label a wiki call by the model that did the work

A wiki call is labelled with the model that cost the most in its run, so a Haiku
side call no longer names an Opus ingest.

### K5. Let a human promote an idea into a sprint

An idea's → button opens the proposal form, which the planner can draft in full;
submitting creates the sprint, moves the idea's lineage onto it and drops the idea.

### F8. Decide a call is lost by its process, not its age

Every call records its process token, and a call stays `running` for as long as
that process is alive; a dead one still waits out the grace, which F10 tracks.

### G3. Charge the worker slot to the agent, not to the lease

A sprint asleep on a detached job holds its lease without a `workers` slot and
takes one back when it launches an agent, as p2-c54 did on waking at 01:11.

### H5. Give chat its own model

Each program has a chat model of its own, set in program settings, and an unset
one keeps chatting on the planner's model.

### H4. Give a program a default worker model

Each program has a worker model that new sprints take when proposed, shown with
the other three in a Models grid in program settings.

### K4. Mark how far into the window we are on the usage bars

Both usage bars carry a tick at the elapsed fraction of the 5h and weekly windows,
fed by a `resets_at` epoch on every usage reading.

### K2. Give the program wiki card something to say

The program's wiki card shows page counts by type as a coloured bar, the pending
count, and when the last run landed.

### K1. Group the programs overview by state

The programs page heads active, paused and closed programs with their own counts,
with closed folded until expanded.

### J1. Stop paying for tool schemas the wiki never calls

Wiki runs launch with only `Bash, Edit, Read, Write`, cutting every turn's prefix
from ~21.6k to ~14.1k tokens; p2 cost per turn fell from $0.053–0.056 to $0.042–0.046.
