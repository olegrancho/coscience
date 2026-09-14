---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 66
last_updated: 2026-09-13
---

# To QC

# To Do (sprint)

# To Do (backlog)

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

## O. Compute onboarding

Any server someone onboards becomes schedulable compute — its CPUs, memory and each
GPU with its VRAM join one pool — and every sprint lands on a machine its request
actually fits.

### O1. Decide the multi-host execution model

Write down where a sprint's agent runs, where its jobs run, and how both reach the
substrate and the program's files once more than one machine is in the pool.

Everything today assumes one host: `executor.launch_detached` is a local `Popen`,
liveness is a `pid:starttime` read from this machine's `/proc`, and worker, wiki
and chat agents all run beside the dispatcher. The choices that shape every other
item here are whether the agent stays central and only jobs go remote, or the agent
runs on the host; whether files are shared (sync, NFS) or shipped per run; and how
access is held (SSH keys, a per-host daemon). This blocks O2–O7; output is a design
doc, not code.

### O2. Model compute as hosts, not one flat pool

Turn `.coscience/resources.yaml` into a list of hosts — each with CPUs, memory and
GPUs — and derive the pool totals from them.

`ResourcePool.capacity` is a flat `{cpu, gpu, workers, housekeepers}` map and a lease
holds amounts with no idea which machine they are on, so 16 CPUs across two boxes
reads the same as 16 on one. A lease has to name its host for placement,
reconciling and release to mean anything. The current file becomes one implicit
`local` host, so a single-machine install keeps working unchanged.

### O3. Describe GPUs by their VRAM

Record each GPU's model and VRAM, and allocate GPU requests by memory so more than
one process can share a card that has room.

A GPU is a count today (`gpu: 1.0`), so p5-c24's job holds the only card while it
may use a fraction of its memory, and nothing can tell a 12 GB card from an 80 GB
one. Allocation by VRAM needs per-device bookkeeping in the ledger, an optional
"exclusive" flag for work that cannot share, and the chosen device handed to the
job (e.g. `CUDA_VISIBLE_DEVICES`).

### O4. Give a sprint's request the shape of real compute

Replace the flat `resources_required` map with total CPU and memory, GPU count with
VRAM per GPU, and whether the work may be split across hosts.

`distributed: false` means the whole request must fit on one machine; `true` lets
CPU work span hosts. Old flat requests migrate as `distributed: false`. Everything
that reads a request moves with it: `effective_requirement`, `over_capacity` (N2),
the PM's COMPUTE block and proposal schema (N1), the sprint edit dialog, and the
sprint page.

### O5. Onboard a server and discover what it offers

Let a human add a server with its access details, have an agent probe it, and add
the confirmed hardware to the pool.

The probe reads what the host really has — `nproc`, memory, `nvidia-smi` names and
VRAM, disk, and whether Python and the agent CLI are present — and proposes a host
entry for a human to confirm rather than writing it straight into the pool. Needs a
place to keep access details that is not the git-tracked substrate, and a Compute
page flow for adding, probing and confirming a host.

### O6. Place and run work on a remote host

Choose a host at grant time, then launch, watch and collect a sprint's work there.

Placement picks a host whose free CPU, memory and GPU VRAM fit the request (or
several hosts when distributed); launch, liveness, stop and collect then go through
that host instead of local `Popen` and `/proc`. The worker's detached-job flow
(sleep, wake, reconcile "no lease ⇒ no running job") has to work across the network
without killing a job because a probe timed out. Shape depends entirely on O1.

### O7. Keep hosts healthy, visible and removable

Heartbeat every host, stop granting on one that goes quiet, and let a human drain
and remove it; show hosts individually on Compute.

A dead host must not keep leases forever or have its jobs declared lost the moment
one heartbeat is missed. Compute today shows pool-wide gauges; with hosts it needs a
per-host view — CPU, memory, each GPU's VRAM in use — plus onboarding, drain and
remove controls.

## C. Codex as a second agent backend

Any agent kind in any program can run on Codex instead of Claude, with its tokens,
usage window and failures as visible as a Claude run's.

### C1. Cut a backend seam with Claude as its only implementation

Move command-building and stream parsing out of the worker, wiki, chat and PM into
one `AgentBackend` interface, with no change in behaviour.

Each agent kind builds its own `claude` command line and parses Claude Code's
stream itself (`claude_executor`, `wiki_agent`, `chat_agent`, `pm_claude`, plus the
shared `agent_stream` and `usage_meter` readers), so there is nowhere a second
backend plugs in. The interface is `command(...)`, `parse(events) -> StreamResult`
and `label(event)`; lifecycle logic stays where it is. The existing tests passing
unchanged is the proof it is a refactor. Blocks C2–C5.

Details: [docs/codex-backend.md](docs/codex-backend.md)

### C2. Implement the Codex backend

Launch and resume Codex runs through the seam, and read both what its stream says
and what only its session file records.

`codex exec --json -o <file> -` and `codex exec resume <thread_id> …` worked headless
on Avatar (09-13): the stream gives `thread_id`, the agent's messages and token
usage; the session file under `~/.codex/sessions/` adds the model, duration and the
5h/weekly `rate_limits`. Permissions map to `-s read-only` for read-only scopes and
a writable or full-access sandbox for workers (a decision). Still unknown: what a run
does when the quota is exhausted — `rate_limit_reached_type` is the field to watch.

### C3. Gate each launch on the quota of the backend it uses

Read the usage window of whichever backend an agent is about to run on, and show
both quotas on the rail.

The worker, wiki and PM launch gates and the rail's bars read Claude's
`unifiedWindows` only. Codex's windows have the same shape (`used_percent`,
`resets_at`, 300 and 10080 minutes) but come from its session file, so the reading
has to be recorded per backend after each run, as Claude's is today.

### C4. Show Codex calls on Compute

Record Codex calls in the call log with tokens, model and how they ended, and decide
what the cost column shows for them.

A ChatGPT-plan run has no per-call price: its events carry tokens only. The choice is
tokens with an empty cost, or a cost estimated from a price table; either way the
Compute totals must not silently mix real Claude dollars with estimates.

### C5. Choose the backend per agent kind per program

Put a backend choice beside each model picker in program settings, with Codex's
models offered when Codex is chosen.

The Models grid already holds planner, chat, worker and wiki models (H4/H5), so this
is a second dial on the same four cards plus a `*_backend` field per kind on the
program. `MODEL_OPTIONS` is Claude-only today and needs a per-backend list.

### C6. Pilot Codex on a low-risk job before sprints

Run Codex first on something read-only and cheap — the wiki probe or Draft with AI —
and compare its output and token use with Claude's on the same inputs.

Workers edit files unattended for an hour at a time, so they are the wrong first
target. A read-only job exercises launch, parse, gate and call log end to end, and a
side-by-side comparison says whether Codex is worth routing real work to. Which job
to pilot is a decision.

# Done

### H1. Work out what a Codex backend would take

`docs/codex-backend.md` maps every `claude` coupling to `codex exec`, with a real run's
event schema and quota windows; the build is planned as block C.

### B2. Record ingest progress per object, not per run

Ingest agents log each finished object to `progress.jsonl` (wikitest r0004 did, before
its report), and a cut-off run keeps those objects; the cut-off path is unit-tested only.

### F5. Backfill the log from run history

The call log now holds 31 rebuilt wiki calls from 08-26 to 09-04 ($57.70), visible on
Compute; `python -m coscience.call_backfill` adds any others without duplicating.

### A4. Warn when the ledger and the bundle disagree

`coscience wiki --status` flags a ledger behind its bundle; with wikitest's wt-r4
removed from the ledger it read "1 unrecorded" until `--reconcile --apply`.

### D2. Stop reporting pages as created when they already existed

A wiki run's page counts are measured from a snapshot of the bundle; wikitest r0004
recorded 1 created and 4 updated where the agent claimed 7 updates.

### F10. Declare a dead call lost without waiting out the grace

The dispatcher collects finished chat turns each cycle, so a call whose process is
gone reads `lost` at once; a p3 reply closed 5s after it finished with no reader.

### N1. Tell the PM what compute exists

The PM prompt states the declared capacity and what running sprints hold, and tells
the PM to request only what a sprint's heaviest step uses.

### N2. Flag a request larger than total capacity

A sprint asking for more than the pool's total is reported as unrunnable in the
dispatch log and on its page, not counted as waiting.

### K6. Count the experiments waiting to run

The rail counts approved and queued experiments as "waiting", with any that can
never start shown beside it.

### F9. Label a wiki call by the model that did the work

A wiki call is labelled with the model that cost the most in its run, so a Haiku
side call no longer names an Opus ingest.
