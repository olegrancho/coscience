---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 199
last_updated: 2026-09-26
---

# To QC

### J3. Choose the batch hold's max-wait, or leave it off

The live dispatch loop holds a short wiki batch 15 minutes (`COSCIENCE_WIKI_MAX_WAIT=900`); the code default stays off.

**Check:** the env file's comment and `batch_max_wait`'s docstring carry the
measurement (77 runs, 59 single-object at ~$2.6, back-to-back runs 6-8 min apart on
one program), and over the next days the wiki runs show fewer single-object ingests
without any program's wiki lagging more than ~15 minutes behind its results.

### D3. Retire the source page of a superseded artifact version

Lint now reads a replaced artifact version as history: no error, and a `src/superseded` warning only while its page is not marked `status: deprecated`.

**Check:** `coscience wiki lint` on the affected program no longer reports the scaling figure's v1/v2
pages as `src/missing`, and the ingest prompt's new paragraph tells the agent to
deprecate the old page, not call it removed. The live v1 page still says "removed
from the platform", which the agent wrote because the old lint told it so; nothing
rewrites it automatically.

### L9. Add a heavy lint: an agent sweep for inconsistencies

The sweep (`wiki_prompts.render_sweep`) is a wiki run kind that runs every 10 wiki runs by default (`COSCIENCE_WIKI_SWEEP_EVERY`, 0 = off), on `coscience wiki --sweep --program <id>`, and after every migration — only once no ingest is pending.

**Check:** `tests/test_wiki_sweep_run.py`, and the two sweep reports in the lab — one
fixed a benchmark name wrong on 69 pages, the other a dozen copy errors — against a
sample of the pages they changed.

### B1. Stop a long chat lagging behind the typing

The chat's message box keeps its own draft and each message is memoised, so a keystroke re-renders the box, not the whole thread's markdown.

**Check:** type fast into a long chat; on a 40-message thread keystroke-to-paint went
from ~65 ms to ~11 ms in headless Chromium (same as a 2-message chat), and a slower
machine felt the old cost several times over as keys queued up. Sending clears the
box; a failed send keeps the text.

# To Do (sprint)

### L12. Make program documentation a source the wiki can cite and track

Add a documentation kind to `sources/`, so a page can cite a program's own docs and an edited doc is re-ingested like a new artifact version.

Background pages are built mostly from the program's workdir docs (README, REPRODUCE,
RESULTS, scripts), but a footnote can cite only results and artifacts, so migration
writers named the files in prose. Nothing then notices when a doc changes, and the
sweep, which checks pages against their sources, cannot check these claims — it missed
a wrong layer count that the program's REPRODUCE.md contradicts.

### L13. Give every shared alias one owning page

State in the topic schema which page owns a term that fits both a topic and a background page, and have lint name the owner when two pages claim it.

A migrated wiki had 16 near-duplicate warnings from aliases listed on both kinds
of page (one term on both a model page and a kernel topic); an alias should route a reader to
one page. Likely rule: the topic page owns it and the background page links to it.
The sweep can clear the clashes already in migrated wikis.

# To Do (backlog)

## B. UI responsiveness

Every page stays quick to use however much history sits behind it.

## A. Memory management

A sprint's memory is reserved before it runs, so two jobs on one server cannot
promise themselves the same RAM.

### A1. Reserve memory for every sprint

Declare `memory_gb` on every server, this machine included, and give each server a
default reservation for sprints that do not ask for memory.

The ledger gates memory only where a server declares it: this machine declares none, so
memory is never counted here, and a sprint that asks for none reserves none on any server
even if it uses tens of GB. With memory declared everywhere and a default per server
(e.g. 4 GB) charged when a request omits `memory_gb`, the ledger reflects every sprint.
The PM's "never request: memory_gb" line then goes away, and the capacity editor and the
server dialog show and edit the default. Written up as O16 before this block existed.

Details: [docs/superpowers/plans/2026-09-15-o16-o17-memory.md](docs/superpowers/plans/2026-09-15-o16-o17-memory.md)

### A2. Tell the worker agent its memory budget

Add a memory line to the worker agent's instructions: the amount its sprint reserved and
that its processes must stay under it.

Blocked on A1, which makes every sprint's reservation real. The instructions already
carry a GPU section naming the cards and VRAM share. Memory gets the same treatment on
trust, with no enforcement: nothing stops a job from using more. Enforcing it (a cgroup
or `MemoryMax`) and checking free memory at grant time stay unplanned until a job
actually runs a server out of memory. Written up as O17 before this block existed.

## D. Wiki content health

Every program wiki is accurate about itself: it passes lint, and a run's report
matches what that run actually changed.

## F. Agents' messageboard

An agent can leave something for whoever comes next, and reach one named
counterpart directly, without a human carrying the message.

### F1. Build the messageboard and its read/write API

A shared space where any agent can post a note and read what others have posted,
with API calls for both.

Agents today are sealed from each other: a worker learns nothing from the worker that
ran before it except through a result a human or the planner relayed, and nothing at
all from a worker in another program. The board is the low-ceremony channel — post,
and read what is there. Decide first what a message carries (author, program, subject,
body, when) and what scoping a reader gets, because that shapes every item below.

### F2. Give the worker agent the board in its instructions

Tell the worker agent the board exists, how to read it and when posting is worth it.

A channel no agent is told about is a channel nobody uses. This is the same treatment
the per-program server notes got: the instructions name it, say what belongs there and
what does not, and the agent decides. Blocked on F1.

### F3. Let the planner post to the board

The planner can leave a message as itself, alongside the agents.

It is the one participant with a view across a program's whole arc, so it is the one
most able to leave something worth finding. One more field in the cycle JSON, applied
the way `holds` and `host_notes` are. Blocked on F1.

### F4. Have the planner prune the board

Every few messages, the planner reviews the board and deletes what is no longer worth
keeping.

A shared space with no gardener fills with stale notes until reading it costs more than
it returns. The planner already maintains the per-program server notes on exactly this
pattern — it reads what accumulated and rewrites what survives — and the trigger is a
message count, not a clock, so a quiet board is never disturbed. Whether a prune is a
delete or an archive is open. Blocked on F1 and F3.

### F5. Direct messages between an agent and a planner

A planner inbox and an agent inbox, so one named counterpart can be addressed
directly rather than broadcast.

The board is for whoever finds it; some things are for one recipient. A worker that
needs its planner mid-sprint has only the escalation, which stops the sprint — far too
heavy for a question. The inbox is the light path: leave it, keep working, read the
reply next beat. Needs a delivery rule (does an unread message wake a cycle?) and a
decision on whether worker-to-worker is in scope. Blocked on F1.

## J. Wiki run cost

A wiki run's token bill is proportional to the material it ingests, not to the
harness wrapped around it.

## L. Wiki quality improvement

The wiki answers the questions actually brought to it, and we know that from
evidence rather than impression.

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
(`pm_agent.py:637`) returning all twelve kinds of output at once: proposals and
the report alongside idea pruning, re-ranking, `release_ids` and thread replies.
Brainstorming and housekeeping are fused, and `program.pm_model` is the only
knob. Splitting the cycle into a cheap pass and an expensive one would pay the
PM's large context twice, and its calls already run $0.31-$0.77 each.

The cheaper shape keeps one call and chooses the model from the trigger, which
the code already knows: `_triggers` labels which inputs moved before the call
(`pm_agent.py:603`), and each cycle already records those labels as `triggers`. A landed result or a goals change is integration and wants the strong
model; a sprint status change, an idea comment or a feedback reply is mundane and
does not. Worth confirming against the call log that the mundane triggers really
are the cheap ones before wiring it.

## O. Compute onboarding

Any server someone onboards becomes schedulable compute — its CPUs, memory and each
GPU with its VRAM join one pool — and every sprint lands on a machine its request
actually fits.

### O21. Show the agent a launch command that lets go of the ssh channel

Put a launch line in the worker's instructions that returns at once, instead of leaving
each agent to discover why its ssh call hangs.

Three remote sprints in a row have hit the same thing: backgrounding a whole
`cd … && … && setsid nohup … &` list runs the list in one subshell, which holds the ssh
channel until the 60-second timeout even when every stream is redirected. The job itself
starts fine, so this costs a minute and a confusing report rather than the run — but the
last two sprints only avoided it because their goals carried a paragraph of warning,
which is a sign the instructions are wrong, not the agents. The remote section of
`claude_executor.build_instructions` already shows a launch command; it should show one
that works, with the write-the-script call and the launch call kept separate.

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
(09-13): the stream gives `thread_id`, the agent's messages and token
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

## Q. Code rot

The platform does what someone decided it should, and never acts on a signal because it
happens to be there.

### Q1. Audit the codebase for rot and remove it

Find and fix every place where code reads a signal and acts on it without anyone deciding
it should, starting with an audit. Rot here means warnings, statuses, flags, fallbacks or
thresholds that gate, retry or change behaviour because a value exists, not because a
person asked for that behaviour.

Example found on 09-15: the launch gate blocked chat and the PM loop because Claude's
rate-limit reading said `allowed_warning` (the week running fast, calls still going through).
Nobody reads that warning, and nothing should act on it. The audit lists every such reader
across the backend, loops and dashboard: what it reads, what it changes, and whether anyone
asked for it. It includes heuristics that quietly change outcomes, flags nobody sets, and
fallbacks that hide a failure. Each finding is removed or made an explicit choice, and a
short note in `CLAUDE.md` says what not to reintroduce.

# Done

### J2. Replace the wiki agent's default system prompt

The wiki agent keeps Claude Code's built-in system prompt; a lean replacement saved ~5% of tokens and wrote a worse wiki, as the comment above `_TOOLS` in `wiki_agent.py` records.

### L4. Read the traces against Oleg's own account

Closed; what the probe traces surfaced fed the topic layout, the sweep and L12-L13.

### L10. Re-probe after the changes and compare

Both wikis were probed on the same question set in the old layout, after a sweep, and in topics, in both modes; topics scored best and read fewer pages per answer.

### L11. Version wiki layouts and migrate between them from the platform

Each wiki names its layout, and Wiki settings or `coscience wiki --migrate` move it to the current one as beat-driven runs; tested on a live wiki.

### L8. Organise every wiki around topics, not sprint results

Topics is the platform's default wiki layout: pages open with the current understanding, and ingests rewrite what a result overturns.

### L7. Grade answers on path and clarity, with the key as a reference

`--grade <model>` scores each answer's path and clarity and classes it against the key, which is a flag rather than a verdict.

### L6. Probe in two modes: free search and click-through

The probe answers in two modes: free search over the bundle, and click-through from `index.md` via `coscience.wiki_nav`.

### L5. Generate a question set with agents, and review it

Agents wrote question sets with answer keys from the raw results; after review 37 remain, kept with the human set outside both repos.

### L3. Debrief each agent after it answers

Every probe answer carries the agent's debrief: what it could not find, what misled it, where pages disagreed.

### L2. Answer each question with a traced agent

Both question sets were probed with traced agents: each answer comes with the pages read in order and the searches made.
