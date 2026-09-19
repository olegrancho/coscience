---
scope: Co-Science platform — development work on wiki ingest reliability and LLM cost visibility.
version: 112
last_updated: 2026-09-18
---

# To QC

### O6. Place and run work on a remote host

With `COSCIENCE_ALLOW_REMOTE=1`, a remote server takes grants and a sprint stays on
the server it started on. The worker agent launches jobs there over SSH and declares
them with `host` and `collect`. The platform watches them (an SSH failure is
"unknown", a reboot is "lost"), stops them only by verified identity, and copies
outputs into `collected/` before waking the agent.

**Check:** set up so far:
- the page read as before with remote placement off;
- `COSCIENCE_ALLOW_REMOTE=1` is now set for the backend and both loops;
- a remote server is onboarded, answering, and reserved for the test program.

The test plan, waiting on Claude usage and a free worker slot (all three are busy):
1. Create a test-program sprint with a small CPU request and **Memory (GB) = 4**. This machine declares no memory, so the sprint can only be placed on the remote server; until O14, nothing else keeps the program off this machine.
2. Give it goals that launch a short job over SSH into its run folder, write an output, and declare the job with `host` and `collect`.
3. Release it and watch:
   - its lease names the remote server;
   - the sprint sleeps on the job;
   - it wakes with a note naming what was copied into `collected/`.
4. Run a second one and stop it mid-job: the job must end on the server.

Spreading one request across servers is not built. 1662 Python and 375 frontend tests passed at landing.

### O7. Keep hosts healthy, visible and removable

The dispatch loop checks each remote server once a minute. A server silent for 30
minutes, or drained, takes no new sprints but keeps its running work. The Compute
page's servers card shows health, what is in use, leftover run folders and stranded
leases, and can drain and remove a server. One sprint's failing beat no longer stalls
the others; after three in a row that sprint is failed.

**Check:** checked so far on the live platform:
- with remote placement off, the page read as before;
- with it on, the remote server reads "answering" and its use and leftovers show;
- an unreachable test server (reserved for the test program, its SSH target never resolves) reads "not answering since …" with the SSH reason.

Left to check on the unreachable server:
- it turns quiet 30 minutes after its first failure and says it takes no new work;
- Drain then Remove: Remove stays disabled until drained and for 2 minutes after, then removes it (which also cleans up the test server).

The failing-beat isolation and the three-strike cap are covered by tests only. The time reads `23.03` on a dot-separator locale (P2).

Still open, listed in spec §11: copy-back inside the beat, the footprint record for
collect paths outside the run directory, and ungated remove. 1712 Python and 388 frontend tests pass on the landed code.

### O8. Let a worker agent pull the red button

A worker agent writes `escalate.json`, or the platform raises one when a sprint sleeps on a
quiet server's job. The sprint is then held as `escalated`, keeping its lease and job. The PM
answers with resume, reallocate or to_human; a human can resume, move or stop from the sprint
page. Human-level escalations show in the header and on the program's sprint list.

**Check:** after it lands, write an `escalate.json` by hand in a test sprint's folder and let
its agent end its turn. The sprint should turn `escalated`, its escalation should show on the
sprint page, and the PM's next cycle should list it. Then answer Resume from the panel: the
next run's instructions carry the answer. A second escalation on the same sprint goes to a
human and shows "N need you" in the header. Landed and deployed.

### O10. Configure each server from its card

Every server card on Compute has a Config button. A remote server's dialog is prefilled,
can be re-probed, and saves with "Update configuration"; only a new SSH target or run folder
needs a passing probe. This machine's dialog has Detect, which fills the GPU cards with their
VRAM. It shows the detected CPU and memory beside the declared values, each with a
"use detected" link.

**Check:** after it lands, open Config on this machine and press Detect. The GPU should show its real VRAM,
CPU should stay at the declared count with the detected thread count beside it, and memory
stay empty unless you use detected. Update, and confirm `resources.yaml` keeps workers and housekeepers.
On a remote server, change notes and Update with no probe needed. Change its SSH target, and Update
stays blocked until a re-probe passes. "Use detected" memory takes the full RAM, not a 90%
share. Landed and deployed.

### O15. Remove a server with one button

The servers card has one Remove button: a server with nothing on it leaves the pool within a dispatch cycle, one with work on it takes no new work and leaves when that work ends, and Keep takes a removal back.

**Check:** on Compute, Remove an idle test server and see it gone within seconds, with a "removed" commit in the substrate; Remove a server holding a sprint and see "removing — waiting on <sprint>", then Keep. Once deployed this replaces O7's Drain → Remove steps. Landed and deployed.

### O11. Let an agent own server discovery

After a probe, the server dialog can start an agent survey: a full-access session that checks the server over SSH and proposes capacity, GPU cards and notes, which the dialog can apply; a failed check is accepted only through the agent's written reasons plus an explicit "with the agent's overrides" click.

**Check:** probe a test server, click Survey with an agent, wait for its reply and proposal, then Use proposal and Add: the pool entry carries the proposal's cards and notes. Re-probe it and confirm that its old overrides are refused as stale. Starts a real Claude session on this machine with this backend's SSH keys. Known and parked: a finished reply can be collected twice by the dashboard and the dispatcher at once, as in program chat today. Landed and deployed.


# To Do (sprint)

### O18. Stop a running sprint from the dashboard

Add a Stop action for a sprint that is executing or hibernated: it ends the agent, stops any job on its host, keeps what was produced and reports what it could not stop.

Today the dashboard offers Cancel only while a sprint is queued; once it runs, the only action is Edit. The platform can stop work — `Worker.stop_sprint` exists — but the stop request is honoured only for a sprint that escalated first, so a human who starts remote work has no way to stop it. Found while QC-ing O6's stop path. Details: [.superpowers/sdd/2026-09-18-o18-stop-running-sprint/brief.md](.superpowers/sdd/2026-09-18-o18-stop-running-sprint/brief.md)

### O9. Keep per-program host notes the PM maintains

Give each program a notes page per host — its Python environments and what the host
is good for in this program's work — that the PM curates and every worker agent
placed there reads.

Probed facts are the same for every program; what a host is for is not.
A shared server can be fungible CPU for one program's batch runs while its old GPU
driver and C library rule out current PyTorch builds for another. Worker agents find such quirks and report them; the PM folds the reports
into the notes, so the next sprint on that host starts from what the last one
learned.

### O12. Review the server cards against real servers

Once O8–O11 work and a few real servers run sprints, review the Compute page's server
cards and redesign them if they don't hold up.

The current card was designed against test fixtures. Several real servers with GPUs,
reservations, health states, leftovers and notes will show whether it reads at a glance or
needs another layout, such as one card per server or a denser table. The outcome may be
"keep it". Blocked on having servers onboarded and in use, not on code.

### O16. Reserve memory for every sprint

Declare `memory_gb` on every server, this machine included, and give each server a
default reservation for sprints that do not ask for memory.

The ledger gates memory only where a server declares it: this machine declares none, so
memory is never counted here, and a sprint that asks for none reserves none on any server
even if it uses tens of GB. With memory declared everywhere and a default per server
(e.g. 4 GB) charged when a request omits `memory_gb`, the ledger reflects every sprint.
The PM's "never request: memory_gb" line then goes away, and the capacity editor and the
server dialog show and edit the default.

### O17. Tell the worker agent its memory budget

Add a memory line to the worker agent's instructions: the amount its sprint reserved and
that its processes must stay under it.

Blocked on O16, which makes every sprint's reservation real. The instructions already
carry a GPU section naming the cards and VRAM share. Memory gets the same treatment on
trust, with no enforcement: nothing stops a job from using more. Enforcing it (a cgroup
or `MemoryMax`) and checking free memory at grant time stay unplanned until a job
actually runs a server out of memory.

# To Do (backlog)

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

### P1. Redesign the sprint edit dialog

Rebuild the sprint edit dialog so every editable field is shown and the screen space is
used well.

The dialog is a narrow single column (Mantine's default modal size). It shows goals,
priority, preemptible and the five compute fields. It leaves out the plan and the worker
model, which the edit API already accepts. Title, summary and rationale cannot be edited
at all. A wider layout could group the fields into what the work is (goals, plan) and how
it runs (priority, preemptible, model, compute), and keep the existing rule for which
fields each status may change.

### P2. Show times the same way whatever the browser's locale

Format every time on the dashboard as 24-hour `HH:MM`, and every date in one fixed style,
instead of taking the browser's locale.

A server's "not answering since" read `23.03` on a browser whose locale writes times with a
dot. The same locale formatting is used for the exact times in tooltips and the short dates
(`components/ui.tsx`), the call log's timestamps (`CallLog.tsx`) and the servers card
(`HostsCard.tsx`). One shared formatter used everywhere fixes all of them, and does not
depend on which locale a viewer's browser reports.

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

### O13. Document the remote-server switches for deployments

CLAUDE.md names the two remote-server switches, what each turns on, which processes must have them, and how to turn one off; this host's setup file says where it sets them.

### O14. Choose where each program may run from either side

Each server holds one list of the programs it runs, edited from the server's dialog or from a program's settings, and creating a program asks which servers it may use.

### O5. Onboard a server and discover what it offers

The Compute page's Add-server dialog probes a server over key-only SSH, runs four checks and adds it to the pool; the first real server was onboarded with it on the live platform.

### O4. Give a sprint's request the shape of real compute

A request names `cpu`, `memory_gb`, `gpu` and `gpu_vram_gb`; the PM's COMPUTE block lists what each host can give, and the edit dialog and sprint page round-trip the same shape.

### O3. Describe GPUs by their VRAM

Hosts list GPUs as cards with VRAM and a lease holds whole cards or VRAM shares; this machine's GPU is declared with its VRAM and read back by the live Compute page.

### O2. Model compute as hosts, not one flat pool

The pool is a `local` host plus an optional `hosts:` section, every lease names its host, and a malformed host entry is reported in `host_errors` instead of stopping the platform.

### O1. Decide the multi-host execution model

The multi-host design is recorded in `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md`, and every O item after it builds from that document.

### D1. Fix the 29 dangling relations across three program wikis

Lint reports no `rel/no-link` on any wiki, every ingest links its pages' declared
relations at collect, and `# Related` now sits above `# Human notes` on all pages.

### I1. Ingest a result when its sprint finishes

Closed as not a problem: an ingest already launches within a 5s beat, and the 3h+
waits were the wiki's 70% usage cutoff, recorded at `WIKI_THRESHOLD` in `wiki.py`.

### H1. Work out what a Codex backend would take

`docs/codex-backend.md` maps every `claude` coupling to `codex exec`, with a real run's
event schema and quota windows; the build is planned as block C.
