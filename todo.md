---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 219
last_updated: 2026-10-03
---

# To QC

### O21. Show the agent a launch command that lets go of the ssh channel

The worker's remote instructions launch in two ssh calls — write `run.sh`, then a call holding only `setsid nohup bash run.sh … & echo $!` — and say why an `&&` chain in front of the `&` hangs.

**Check:** the next remote sprint's launch returns at once instead of after the 60-second
timeout, and `tests/test_remote_jobs.py` pins the new form. Reproduced first: against a
held pipe the old form blocked for the job's whole length, the new one returned in 3 ms.

### E1. Stop the dispatch loop committing every few seconds

A wiki run in flight no longer makes every dispatch cycle commit (`wiki.STATUS_ONLY`), and the substrate ignores sprint job logs; 4,204 tracked `.out`/`.log` files were untracked and stay on disk.

**Check:** over a day with wiki runs, `git log --format=%s | grep -c '^dispatch cycle$'` in
the substrate drops from hundreds to tens — 94% of them fell inside wiki runs — and a
wiki run's pages land in one commit at its end, not half-written across many. The
platform's own `agent.out` and `feedback.out` are still tracked.

### K2. Show the planner sprint titles, and have it link with them

Every sprint line the PM and chat agents see now reads `id "title"` (the goals' start when untitled), and their prompts tell them to write a sprint as `[title](/sprints/<id>)`.

**Check:** the next PM report and the next chat reply name sprints by title, as links that
open the sprint. Titles are not fingerprint inputs, so the deploy woke no program
(`tests/test_sprint_titles_in_prompts.py`).

### K1. Make every sprint and idea reference a link

Markdown everywhere links every known sprint id, and its short `<program>-c<n>` form when only one sprint has it; on a program's pages its idea ids link to the idea, which the ideas list scrolls to and outlines. Agents are told to link ideas, and workers sprints, by name.

**Check:** an older chat names sprints as `p2-c28` — each is now a link; the ideas page's
pool summary names ideas by id — clicking one jumps to that idea. A short form two sprints
share (the planner reused some numbers) stays plain text on purpose.

### I2. Highlight new sprint events on the lineage graph

Lineage nodes for the sprints the experiments list highlights carry a pulsing ring in the same colour, and clear with them when the sprint is opened; an idea node now opens that idea in the list.

**Check:** after a sprint finishes or the PM proposes one, its node on the program's
lineage graph has the ring until you open the sprint. The ring is per browser, like the
list. At full-graph zoom the ring shrinks with the node; it reads best expanded.

### I1. Write catch-up reports, and give them a page

A "Catch up" button opens each program's catch-up page: reports newest first, each a read-only planner chat opened by the catch-up request, with "Continue in chat", "Write one now" (from the last report, or the last 7/14/30 days) and the schedule (every 7 days, at least 10 finished sprints, editable there).

**Check:** the first scheduled reports — a program with none yet looks back one period, so
the busiest programs should get one within ten minutes of the deploy, past the planner's
usage gate — read like the hand-written "Update <date>" chats: bottom line, numbers table,
numbered next steps, sprints linked by title. The button shows "· N new" until opened.

### M1. Let a human grant the PM bounded approval authority

A program can hold one approval grant (`grant.py`, in `program.md`): until its limit — N approvals, a deadline, or the current 5-hour or weekly usage window — the PM approves any proposed sprint by `approve_ids`, re-checked against the grant at every approval; it ends on its own and says why. `docs/sprint-lifecycle.md` has the new edge.

**Check:** grant 2 approvals on a program with proposals waiting; the next cycle (a grant
wakes the planner) approves at most two, each recorded on the sprint as `approve (grant)`
and in the cycle's actions, a third is "Approve FAILED", and the grant shows as ended.

### M2. Build the supercharge control

"⚡ Supercharge" on the program page opens the grant dialog (approvals, hours, or a usage window); a live grant puts a banner at the top with what is left, what it approved and Revoke; an ended one stays with its reason until dismissed.

**Check:** the banner is impossible to miss while live and gone only after Dismiss; Revoke
ends it at once, and the planner's next prompt no longer offers `approve_ids`.

# To Do (sprint)

# To Do (backlog)

## I. Catching up on a program

Someone returning to a program after days away sees what changed, in one read,
without hunting through sprints and results.

## K. Cross-references

Every mention of a sprint or an idea, wherever it is written, is one click from it.

## E. Substrate history

The substrate's git history records work and decisions, not the loops' heartbeat.

## G. Usage budget

Whoever runs the platform decides how much of the Claude usage windows each kind of
agent may spend, from the dashboard, without touching code.

### G1. Edit the usage thresholds from Compute → Claude usage

Add a config control to the Claude usage card that sets, per agent kind (PM, worker, wiki), the 5-hour and weekly percentage at which it stops launching.

Today they are constants in code: the PM stops at 80% of the 5-hour window, workers at
90%, wiki runs at 70%, and all three at 99% of the week (`worker.py`, `wiki.py`). The
PM and dispatch loops are separate processes, so the values belong in a substrate file
they read each beat (as `resources.yaml` is), not in the backend's memory, and the
card should show each gate's line on its gauge. Changing one should need no restart.

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

### L13. Give every shared alias one owning page

The topic schema, ingest rules and migration map give a shared alias to the topic page, and lint's near-duplicate warning names which page keeps it.

### L12. Make program documentation a source the wiki can cite and track

Workdir docs ticked in Wiki settings are ingested as `doc:` sources, re-ingested when edited, cited from background pages, and checked by sweeps.

### L9. Add a heavy lint: an agent sweep for inconsistencies

A sweep runs every 10 wiki runs and after each migration; the first scheduled ones fixed scope slips and stale openings the mechanical lint could not see.

### D3. Retire the source page of a superseded artifact version

Lint reads a replaced artifact version as history, and the ingest marks its page deprecated with a link to the current version instead of calling it removed.

### J3. Choose the batch hold's max-wait, or leave it off

The live dispatch loop holds a short wiki batch 15 minutes: over its first four days 6 of 26 ingests carried more than one result, saving 7 runs, and the hold never added more than its 15 minutes.

### B1. Stop a long chat lagging behind the typing

The chat's message box keeps its own draft and each message is memoised, so typing into a long thread no longer re-renders it.

### J2. Replace the wiki agent's default system prompt

The wiki agent keeps Claude Code's built-in system prompt; a lean replacement saved ~5% of tokens and wrote a worse wiki, as the comment above `_TOOLS` in `wiki_agent.py` records.

### L4. Read the traces against Oleg's own account

Closed; what the probe traces surfaced fed the topic layout, the sweep and L12-L13.

### L10. Re-probe after the changes and compare

Both wikis were probed on the same question set in the old layout, after a sweep, and in topics, in both modes; topics scored best and read fewer pages per answer.

### L11. Version wiki layouts and migrate between them from the platform

Each wiki names its layout, and Wiki settings or `coscience wiki --migrate` move it to the current one as beat-driven runs; tested on a live wiki.
