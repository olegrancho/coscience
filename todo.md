---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 170
last_updated: 2026-09-23
---

# To QC

### P9. Name the experiments behind the workers gauge

Hovering any gauge — on the Overview's compute card and on Compute — lists the experiments holding it, by title; and in the pulse, hovering "N agents calling Claude" lists every live agent with what it is working on, as does hovering each agent's row.

**Check:** while agents run, hover "agents calling Claude" in the rail's pulse — each line names a worker's experiment by title, or the program a planner or wiki agent works for, then the model — and hover one agent row for its own line. Then `workers` and `cpu` on Compute. The pulse was the first QC's finding; the rows' hover used to be a native tooltip naming only the program and model.

### P10. Give the lineage graph an auto-layout button

The lineage card has a labelled "Auto-layout" button, inline and in the expanded view, that forgets dragged positions and lays the graph out again.

**Check:** drag a few lineage nodes, press Auto-layout, and the graph returns to its computed layout. The control already existed as a bare "↺" icon since July; the fix was naming it. It does not ask first — what it discards is this browser's remembered drag positions.

### P5. Come back to the experiments list, not the top of the program

Going back from an experiment — by its back link or the browser's Back — lands on that experiment's row in the program's list, with a brief flash so the eye finds it.

**Check:** open a program, scroll to an experiment well down the list, open it, then go back both ways. Reaching the program any other way (the nav, a link) still starts at the top. It brings the row into view rather than restoring the exact scroll position, and the row stays visible even if the done/canceled cap would otherwise fold it.

### P6. Filter the experiments list to just the new ones

The experiments card has an "only new (N)" check beside the status filter that shows just the highlighted rows.

**Check:** on a program with highlights, tick it — only the highlighted rows remain, and it combines with the status filter (the count follows the filter). With nothing new it says "Nothing new since you last looked." instead of an empty list. The count is this browser's, so it can differ between machines.

### P7. Never hide an experiment that is new

The done/canceled cap still counts new rows toward its three, but never folds one away.

**Check:** with four sprints finished since your last look, all four stay in view without "Show all" and the older seen ones fold behind them. The rules are in `views/experimentsList.ts` with their tests.

### P12. Collapse the server notes on the program page

Each server's row in the notes card starts collapsed to one line — name, the note's first line, and an "N reports unread" badge — and opens on click.

**Check:** a program page with several servers: the card is a short list, a row with pending reports says so without opening, and Edit opens the row it edits.

### O23. Stop every program page polling the whole ledger

The notes payload now carries every server with its display name and whether the program may use it, and the program page no longer requests the ledger at all.

**Check:** with a program page open, the browser's network tab shows `/api/programs/<id>/host-notes` polling and no `/api/ledger`. Compute and the Overview still poll the ledger, as they should.

### O22. Tidy the edges of the server notes

Four fixes: the planner is no longer shown notes or reports for servers it may not use; a human can only start a note on a server the program may use; saves carry the note as opened and a conflict is refused, not overwritten; a planner cycle skips a note a human saved while it ran.

**Check:** (1) withdraw a program's access to a server that has pending reports — the next cycle's report has no "Host note FAILED" line, and the row offers "Mark read"; (2) `PUT …/host-notes/typo` answers 422; (3) edit one note in two tabs and save both — the second shows the first's text and asks before replacing it. The section-nav entry the item also named had already landed on 09-19.

### G1. Make the global capacity editor the platform's own limits, and nothing else

Compute's "Edit capacity" is now "Platform limits": two named fields — worker agents and housekeeping agents at once, empty meaning no limit — saved through their own endpoint (`PUT /api/platform-limits`), which refuses any other name.

**Check:** Compute → Platform limits shows those two fields and nothing else; change one and only that key moves in `resources.yaml`. The gauges' +/- steppers now exist only on `workers` and `housekeepers`.

### G2. Edit every machine's real capacity only on its own card, this one included

CPUs, memory and cards are edited only in each server's own dialog; this machine's save sends its own amounts and the server keeps the platform limits from the file.

**Check:** open this machine's row under servers, change its CPU count, save — `workers` and `housekeepers` in `resources.yaml` are unchanged, and no gauge on Compute offers a cpu or memory stepper. The live pool file had no invented top-level resource, so nothing needed migrating; one would still ride along untouched through this machine's save, but no screen edits it any more. `PUT /api/capacity` still accepts a platform key if sent, for any script that predates the split.

### P1. Redesign the sprint edit dialog

The edit dialog is a wide two-column form — the work (title, summary, goals, plan, rationale) on the left, how it runs (priority, worker model, preemptible, compute) on the right — and the title, summary and rationale are now editable through the API too.

**Check:** open Edit on a proposed sprint and on an approved one. Proposed: everything is live. Approved: title, summary, model and the scheduler fields are live; goals, plan and rationale are greyed with a line saying why. The rationale rule is new — it argued for the approval, so it closes with goals and plan; the title and summary stay open until done or canceled. Changing the model on a sprint whose agent is running says it will restart it.

# To Do (sprint)

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

## B. Disk space

No machine is given work it has no room for, and a machine that is running out says
so on the dashboard before it stops working.

## E. The planner's record

What the planner decided about a sprint is readable on that sprint, truthful about what
it actually did, and never a human authorization it has no power to give back.

## D. Wiki content health

Every program wiki is accurate about itself: it passes lint, and a run's report
matches what that run actually changed.

### D3. Retire the source page of a superseded artifact version

Decide what happens to a source page when its artifact moves to a new version, so
lint stops reporting it as `src/missing`.

One program's only lint error is the source page of an artifact's v1:
the artifact moved to v2 on 09-12 and v2 was ingested, but only an artifact's current
version counts as an object, so v1's page reads as pointing at nothing. Every future
revision will do the same. The choices are to retire or merge the old page on
ingest, or to have lint accept an origin that is a superseded version.

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

### J2. Replace the wiki agent's default system prompt

Give the run a lean `--system-prompt` in place of Claude Code's built-in one.

Measured on one program's bundle, the launch prefix is 21,615 tokens with the stock
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
Where a hold does pay is a burst of sprints finishing together. I1 closed without
adding a sprint-completion trigger, so nothing produces bursts on purpose; set it
only if the call log shows several one-object ingests landing minutes apart.

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
`python -m coscience.wiki_probe --program <id>` reads `programs/<id>/wiki-questions.md`
(a markdown list, one question per item) and writes `report.md`, `summary.json` and
both streams under `~/.cache/coscience/wiki-probe/<id>/<stamp>/`. Each agent is
read-only in the bundle, starts from `index.md`, and may leave for raw results
only by saying so; the report flags every read outside the wiki. The 09-13 pilot
on one program took 50s and $0.14 for one question on Sonnet 5, and the answering model
defaults to the program's planner model.

### L3. Debrief each agent after it answers

Ask the agent, in a second turn, what it could not find and what misled it.

The trace shows what an agent read; only the agent can say what it went looking
for and failed to find, which page it expected to exist, or where two pages
disagreed and it had to guess. Absence is the defect class a wiki hides best and
the one that matters most here. Built into the same runner as a `--resume` turn
with five fixed questions; on the pilot it cost $0.03 and surfaced a real defect
unprompted — a canonical numbers page, billed as the single source of truth,
predates and omits the program's best result.

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

### P8. Show how long each running experiment has been going, on Compute

Compute's running-now table names each experiment by title and says how long it has held compute, with the grant time on hover.

### P2. Show times the same way whatever the browser's locale

Every time on the dashboard is 24-hour `HH:MM` and every date `22 Sep` / `22 Sep 2026`, from one formatter that never consults the locale.

### P11. Show only typed nodes on the wiki graph by default

The wiki graph opens with "typed only" on whenever it has a typed relation, and off when it has none.

### B4. Say when the substrate cannot commit

A refused commit is told apart from an empty one and recorded outside the substrate, and the pulse grows a red row after three in a row — checked on a scratch substrate with a refusing hook, and silent again on the next good commit.

### B3. Retire a Claude call whose end was never written

A planner cycle closes its own unfinished calls before opening the next one, so a loop that lost an end event no longer shows a call in flight for as long as it lives.

### E2. Keep a planner cycle's reasoning after the next cycle runs

Every cycle's report is kept under the program's `reports/` and readable from a cycle selector on the program page, and each sprint a cycle acted on carries that cycle's own sentences about it.

### B2. Stop giving work to a machine with under 500 MB free

A machine under the gate (1 GB) takes no new grants and launches no worker agent, while
the planners keep beating; both thresholds can be moved from the environment, so the gate
is rehearsed by shifting the line instead of filling a real disk.

### B1. Warn on the pulse zone when a machine is low on disk

Every machine's free space is on the servers table, dated to its last health check when it came from a remote one, and the pulse calls out anything running low — severely once it is under the gate.

### E1. Replace the planner's reopen with a hold that keeps the approval

The planner can no longer un-approve anything: it holds an approved sprint instead, with a one-sentence rationale shown on the sprint, which release or a human clears.

### E3. Stop flagging a declined action as an unbacked claim

A planner explaining that it had nothing to do is no longer stamped as claiming it acted: the check reads the whole sentence and requires a past-tense verb, so it fires on a real claim and not on an honest one.
