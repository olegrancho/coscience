---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 194
last_updated: 2026-09-25
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

### L1. Write the question set, a few per program

Two programs have question sets, kept outside both repos in the host's platform config directory and passed to the probe with `--questions`.

**Check:** the files hold the questions as written, and nothing under the substrate's
program directories mentions them — an agent that could read them could write the
wiki to the test.

### J2. Replace the wiki agent's default system prompt

Decided against: the wiki agent keeps Claude Code's built-in prompt, and the reason is written above `_TOOLS` in `wiki_agent.py`.

**Check:** the comment's numbers — one real ingest re-run from the same starting
bundle both ways: ~6k of a ~115k per-call context saved, $3.92 lean vs $3.61 stock,
and a blind page-by-page judgement that the stock run was modestly better. One pair
is a small sample; the call is that a ≤5% saving is not worth a second one.

### L2. Answer each question with a traced agent

Both question sets were probed on 09-24: 16 questions, $13.23, one report per program under the host's probe cache.

**Check:** each report has an answer, the pages read in order and the searches for
every question, and flags the three answers that left the wiki for raw results.

### L3. Debrief each agent after it answers

Every answer in both reports carries its debrief: what the agent could not find, what misled it, where pages disagreed.

**Check:** the debriefs name concrete pages and defects rather than generic advice —
the recurring one is an answer scattered over many concept pages with no page that
gathers it.

### L5. Generate a question set with agents, and review it

Sonnet wrote 20 questions with answer keys per program from the raw results, plans, goals and workdir docs; after review 37 remain, kept with the human set outside both repos.

**Check:** the review notes in the lab's decision log — three dropped because their
answers live in sprint plans, not results — and a sample of keys against the result
files they cite.

### L6. Probe in two modes: free search and click-through

`coscience.wiki_probe --mode nav` answers through `coscience.wiki_nav`, an MCP tool that serves `index.md` and then only pages linked from pages already opened; `--bundle` points the probe at any copy of a wiki.

**Check:** `tests/test_wiki_nav.py`, and a nav-mode report's per-question page list —
every page after the index is linked from one opened before it.

### L7. Grade answers on path and clarity, with the key as a reference

`--grade <model>` scores each answer's path and clarity (1-5) and classes it against the key; the report opens with a grade table.

**Check:** a baseline report's grade table, and whether the grades match a read of
two or three answers. The key classes run lenient — an answer naming the wrong
benchmark version came out "differs-defensibly" — so treat them as flags, not verdicts.

### L8. Organise every wiki around topics, not sprint results

Topics is the platform's current wiki layout (`wiki_topics.py`): topic and background pages that open with the current understanding, and an ingest that rewrites what a result overturns. New wikis start in it; existing ones move by L11's migration.

**Check:** the lab's decision log (outside both repos) — two real wikis migrated to
copies, probe scores against the old layout, and the ingest test where a result that
overturned a verdict had the page opening, description, index line and status page
rewritten. Still open before it is fully comfortable: docs as a source kind, alias
ownership between background and topic pages.

### L9. Add a heavy lint: an agent sweep for inconsistencies

The sweep (`wiki_prompts.render_sweep`) is a wiki run kind that runs every 10 wiki runs by default (`COSCIENCE_WIKI_SWEEP_EVERY`, 0 = off), on `coscience wiki --sweep --program <id>`, and after every migration — only once no ingest is pending.

**Check:** `tests/test_wiki_sweep_run.py`, and the two sweep reports in the lab — one
fixed a benchmark name wrong on 69 pages, the other a dozen copy errors — against a
sample of the pages they changed.

### L10. Re-probe after the changes and compare

Both wikis were probed on the same 53 questions as current, current + sweep and topics, in both modes; the table is at the top of the lab's decision log.

**Check:** the table against the per-run reports, and the two correctness cases the
grades cannot show (a wrong layer count, a wrong benchmark name) in the answers
themselves.

### L11. Version wiki layouts and migrate between them from the platform

Each bundle names its layout on the first line of its CLAUDE.md; `wiki_layouts` holds the current one and the upgrade path, and `wiki_migrate` moves a wiki over as beat-driven runs (map, write batches, finish, swap) while the old one keeps serving. Wiki settings show the layout and offer the migration; `coscience wiki --migrate` does every program. Instructions: `docs/wiki-layouts.md`.

**Check:** `tests/test_wiki_migrate.py`; Wiki settings on a wiki in the old layout
(the offer, then progress); and one real migration on a copy of the substrate before
any live wiki is migrated — the lab migrations were run by hand, this code has not yet
driven a real one.

# To Do (sprint)

### L4. Read the traces against Oleg's own account

Compare what the agents struggled with to where Oleg finds the wiki lacking.

The point of the exercise is the delta: where the traces confirm the impression,
where they contradict it, and where they surface problems nobody had noticed.
Agreement between an independent trace and a held opinion is worth more than
either alone, and disagreement is where the bias was.

# To Do (backlog)

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

## G. Where capacity is declared

Capacity is declared and edited in the place it actually belongs: how many agent
processes the platform may run, set once for the platform; real CPUs, memory and
cards, set on the machine that has them.

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

## P. UI updates

The dashboard shows a human everything they need to see and change, and uses the
screen space it takes.

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

### P13. Show the app's version in the top-right corner

The header reads "live · v0.1.1", from a `VERSION` file that the build bakes in, `/api/version` reports, and each deploy raises by one.

### P14. Stop the sprint page's Wake now and Clear hold buttons being cut off

The sprint cards' buttons keep their full width and the text beside them wraps, so no label is clipped.

### P1. Redesign the sprint edit dialog

The edit dialog is a wide two-column form — the work on the left, how it runs on the right — and the title, summary and rationale are editable too.

### O22. Tidy the edges of the server notes

The planner is no longer shown notes it could never write back, a note can only be started on a usable server, and a note saved over a changed one — by a person or the planner — is refused instead of silently overwriting it.

### O23. Stop every program page polling the whole ledger

Only Compute and the Overview poll the ledger; the notes card gets its servers with the notes, and the rail's pulse reads a small `/api/pulse`.

### G2. Edit every machine's real capacity only on its own card, this one included

Each server's dialog sets what Co-Science may use beside what the machine has, in one aligned table with a row per resource and an on/off switch per graphics card.

### G1. Make the global capacity editor the platform's own limits, and nothing else

The platform's two limits are set only on their own gauges on Compute — steppers, an ∞ that removes the cap, and a "Set a limit" when there is none — through an endpoint that refuses any other name.

### P12. Collapse the server notes on the program page

Each server's note starts collapsed to one line — name, the note's first line and an unread-reports badge — and opens on click.

### P7. Never hide an experiment that is new

The done/canceled cap never folds away a row the viewer has not seen yet.

### P6. Filter the experiments list to just the new ones

The experiments card has an "only new (N)" check that shows just the highlighted rows, combined with the status filter.
