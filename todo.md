---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 39
last_updated: 2026-09-13
---

# To QC

### G3. Charge the worker slot to the agent, not to the lease

A sprint asleep on a detached job keeps its lease but hands back `workers`, and
takes it back at the point it launches an agent (`584d13b`).

**Check:** once a sleeping sprint wakes — p5-c35 at 09-13 02:01, p2-c54 at 02:22 —
it either shows `workers: 1` in `.coscience/leases.json` with a new worker row on
Compute, or, with all three slots busy, waits with its job finished. A woken
sprint has no priority over fresh work, so waiting on its own results is expected.

Already passed on 09-12 23:10: four sleeping sprints held leases with no `workers`
key and live jobs, and peak concurrent worker calls since the commit was 3.

### K2. Give the program wiki card something to say

The wiki card shows a page-type bar in the graph's hues, a count per type, the
pending count, and the kind and age of the last run (uncommitted, not deployed).

**Check:** after deploy and a hard reload, the Wiki card on `/programs/p2` and
`/programs/p5` — counts match the wiki's own page listing, colours match the
graph view, and "last ingest … ago" matches the newest run on Compute. An empty
wiki still shows the old one-line description.

### K4. Mark how far into the window we are on the usage bars

Both usage bars — the rail's and Compute's — carry a thin tick at the elapsed
fraction of the 5h and weekly windows; readings now keep `resets_at` as an epoch
(uncommitted, not deployed).

**Check:** after deploy, the 5h tick sits at `1 − (time to reset) / 5h` — e.g. 2h
before a reset it is at 60% — and the weekly tick moves about 0.6% an hour. An
idle host reading through the usage script still shows ticks; that path takes the
epoch from `~/.claude/statusline-usage-cache.json`, and shows no tick if that file
is unreadable.

### F9. Label a wiki call by the model that did the work

`read_outcome` takes the model with the largest `costUSD` in `modelUsage`
(uncommitted, not deployed).

**Check:** the first wiki-ingest row on Compute after deploy names Opus 4.6, not
Haiku. Rows already logged keep their old label; nothing rewrites history.

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

### F8. Decide a call is lost by its process, not its age

Record the agent's process token on the call's start event and infer `running` or
`lost` from whether that process is alive.

`calls()` marks any start older than `CALL_GRACE` (15 min) with no end as `lost`,
so healthy workers vanish from the rail's live-agent count and read as failures on
Compute — on 09-12 two p2 workers at 35 and 40 min showed `lost` while running, and
8 of 27 finished worker calls ran past 15 min (longest 67). It fails the other way
too: an agent that dies at minute 2 shows `running` until minute 15. The token is
`pid:starttime` and the dispatcher's reconcile already checks it against `/proc`
with a pid-reuse guard; keep the age rule only for calls started without a token.

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

## K. Dashboard legibility

The state of the work reads at a glance — without opening a page, counting cards
or decoding a slug.

### K5. Let a human promote an idea into a sprint

Add a promote button on an idea that opens the sprint proposal form, pre-filled
from the idea.

The loop is currently one-way: `POST /ideas/{id}/demote` turns a sprint back into
an idea and sets `demoted` so the PM cannot re-promote it, but there is no
promote — `Idea`'s own docstring says the PM "promotes promising ones into
sprints", and only the PM can. A human reading the pool has to retype the idea
into `ProposeSprintModal` by hand.

Most of the parts exist: the modal already collects id, goals, steps, priority and
artifacts, so promote is a pre-filled open plus an endpoint. Two things need
deciding — what becomes of the idea afterwards (`demoted` is the shape the reverse
direction already uses), and that the sprint records where it came from, since
`Idea.edges` carries outbound lineage and losing the link would make the pool
look like it grew a duplicate.

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

### H4. Give a program a default worker model

Add `worker_model` beside `pm_model` and `wiki_model`, so sprints inherit a
program's choice instead of a global constant.

Two of the three agent kinds are already configurable per program; the worker is
not. `Sprint.model` falls back to `DEFAULT_MODEL` (`claude-sonnet-5`,
`models.py:37`), so every sprint in every program starts from the same hardcoded
value and has to be overridden one sprint at a time — awkward for a program like
p5, whose work is heavier than the default assumes.

The shape already exists twice: `Program.__post_init__` resolves an empty
`pm_model`/`wiki_model` to `DEFAULT_MODEL`, `substrate.py` round-trips both, and
`ModelSelect` renders the picker. Two things to decide — the precedence (a
sprint's own model over the program default over `DEFAULT_MODEL`), and whether
changing the program default touches sprints already proposed. It probably should
not: those were reviewed and approved with a model attached, and moving them
underneath a human who has already looked at them is a surprise.

### H5. Give chat its own model

Add `chat_model` so a conversation is not silently bound to whatever the PM
reasoner is set to.

Chat borrows `program.pm_model` (`service.py:930`), which means changing the
PM's model to tune autonomous planning also changes what you are talking to, and
neither choice can be made without moving the other. Chat is also the one agent
with a human waiting on it — it fails open on usage where the autonomous three
fail closed — so its tradeoff is genuinely different: latency matters, and so does
wanting the strongest model precisely because someone is thinking with it.

With this and H4 all four agent kinds — pm, wiki, worker, chat — have their own
model, which is the point at which four scattered fields want collecting into one
"models" group in `ProgramSettingsModal` rather than being added one at a time.

# Done

### K1. Group the programs overview by state

The programs page heads active, paused and closed programs with their own counts,
with closed folded until expanded.

### J1. Stop paying for tool schemas the wiki never calls

Wiki runs launch with only `Bash, Edit, Read, Write`, cutting every turn's prefix
from ~21.6k to ~14.1k tokens; p2 cost per turn fell from $0.053–0.056 to $0.042–0.046.

### F7. Stamp the 5h window from the run's own stream

Every call's "5h before" is taken from the run's first `rate_limit_event`, so the
Compute column fills even after an idle stretch with no OAuth token.

### H2. Add Fable 5.1 to the model picker

`MODEL_OPTIONS` offers Fable 5.1, and a run on `claude-fable-5-1` is accepted end
to end.

### K3. Show the program's name in the wiki, not its slug

The wiki pages and graph views name the program in their back link instead of
saying "Program".

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
