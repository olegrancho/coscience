---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 238
last_updated: 2026-10-06
---

# To QC

### I3. Add a day-by-day narrative to the catch-up report

The catch-up request asks for a "Day by day" section, after the key numbers: a dated heading per day since the last report, at most two short paragraphs, quiet days skipped.

**Check:** "Write one now" on a program's Catch-up page; the report has the section, in
date order, and no day runs past two paragraphs.

### K3. Give every sprint a number no other sprint in its program has

New planner sprints are `<program>-s<n>-<slug>`, numbered per program from above every number already used; a sprint records the cycle that proposed it, so re-applying a staged cycle finds it again.

**Check:** the next planner proposals in a live program carry `-s<n>-` numbers that
differ from each other, and a short form like `<program>-s<n>` in a chat links to its
sprint. Proposals a person makes keep their unnumbered ids.

### K4. Link a promoted idea's id to the sprint it became

Promoted sprints record `from_idea`; an idea id mentioned after its promotion links to the sprint, and an old Ideas-page link to it forwards there. Past promotions were backfilled from history (118 sprints).

**Check:** in the summary at the top of a program's Ideas page, an id the summary
says was "promoted to a sprint" is a link and opens that sprint; an old planner report's
link to that idea does the same. The backfill matched by wording where the history held
no exact record, so a wrong pairing is possible but none showed against the 13 exact ones.

### D1. Lay the lineage graph out compactly instead of in one wide band

The auto-layout places each node by one parent edge (a tree) and still draws every edge; separate clusters and edgeless nodes are packed into rows instead of one band.

**Check:** "Auto-layout" on the largest program's lineage card, and on a small one
with several loose ideas: the large one needs far less sideways panning, the small one
fits the card. The card's zoom still stops at 50%, so a big graph still pans; nodes you
dragged by hand keep their places until Auto-layout.

### G1. Edit the usage thresholds from Compute → Claude usage

Each kind of agent's stop line in the 5-hour and weekly windows is set from Compute ("Change them" under the usage bars) and kept in one substrate file the loops read on every check; the bars mark each line.

**Check:** on Compute, lower the wiki 5-hour line below current usage and save; the
mark moves, and the next wiki batch waits (the dispatch log says usage) without any
restart. Put it back after. Chats are not gated by these.

### B3. Keep a chat's agent running between replies for a while

A chat's agent now stays up after replying and takes the next message on its input, closing after 10 idle minutes (`COSCIENCE_CHAT_KEEPALIVE`, 0 = off); Compute → Claude usage lists the ones kept open.

**Check:** in a chat, send a message, then a follow-up within a few minutes: the
follow-up's reply starts noticeably sooner, and Compute names the chat as waiting
in between. After 10 quiet minutes it drops off Compute, and the next message still
continues the same conversation. A backend restart ends waiting agents; the next
message resumes as before.

### M3. Add a paced approval mode that keeps weekly usage on schedule

Supercharge has a fifth limit, "Paced to the week, until revoked": the planner may approve only while weekly usage is below the share of the week gone; ahead of pace the grant pauses (banner says so, with a pace bar) and resumes as the week catches up.

**Check:** grant "paced" on a program while usage is behind the week: the banner shows
the pace bar and the planner approves. When usage is ahead, the banner reads "paused",
the next PM cycle's actions show approvals refused as "paused", and the grant has not
ended. Revoke works as for the other limits.

# To Do (sprint)

# To Do (backlog)

## I. Catching up on a program

Someone returning to a program after days away sees what changed, in one read,
without hunting through sprints and results.

## K. Cross-references

Every mention of a sprint or an idea, wherever it is written, is one click from it.

## N. Experiment context

Reading one experiment shows where it came from and what followed it, without opening the graph.

## D. Lineage graph

A program's lineage reads at a glance on one screen, without panning sideways to find its parts.

## E. Substrate history

The substrate's git history records work and decisions, not the loops' heartbeat.

## G. Usage budget

Whoever runs the platform decides how much of the Claude usage windows each kind of
agent may spend, from the dashboard, without touching code.

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

### N1. Show each experiment's lineage below its results

Each experiment's page has a Lineage card under Results, read live from the program's graph: where it came from and what followed.

### B2. Keep the experiments list's filters across a visit to an experiment

The experiments list's "only new", status and "show all" filters are kept per program for the browser tab.

### K1. Make every sprint and idea reference a link

Sprint ids, short forms and idea ids written in chats, reports and pages render as links; an idea link opens the Ideas page at that idea.

### O21. Show the agent a launch command that lets go of the ssh channel

Workers launch remote jobs in the two-call form, and a live launch returned in seconds with the job running on.

### M2. Build the supercharge control

The ⚡ button on the program page opens the grant dialog, and a live or ended grant shows as a banner with what it approved, Revoke and Dismiss.

### M1. Let a human grant the PM bounded approval authority

Under a live approval grant, limited by approvals, a deadline or a usage window, the planner approves proposed sprints itself; each approval is checked against the limit and recorded on the sprint.

### I2. Highlight new sprint events on the lineage graph

Lineage nodes whose sprints changed since this browser last looked are ringed until they are seen.

### I1. Write catch-up reports, and give them a page

The planner writes a catch-up report weekly once enough sprints have finished, or on demand, and each program has a Catch-up page listing them.

### K2. Show the planner sprint titles, and have it link with them

The planner, chat and worker prompts now carry each sprint's title next to its id, and tell the agent to link sprints by title.

### E1. Stop the dispatch loop committing every few seconds

The substrate's dispatch commits now follow real events (340 a day before, 26 in the 20 hours after), wiki runs land in one commit each, and sprint job logs are untracked.
