# Co-Science Platform — Design

**Status:** Design approved, pending spec review
**Date:** 2026-06-23
**Supersedes discussion in:** `docs/initial_specs.md`, `docs/revisions-01.md`

## 1. Summary

The Co-Science Platform coordinates LLM-based researchers to execute open-ended
research programs under human oversight. It scales up a mode the author has run
manually for months (an LLM agent executing a well-scoped, ~1-week research
sprint) into a multi-agent system that other people can collaborate on.

**Core design principles**

- **The agent is durable; the session is disposable.** All durable state lives
  outside agent sessions (a git repo + a small service), so a crashed or
  context-full session is replaced and resumes from checkpoints.
- **Portability is a hard requirement.** The whole platform is `git clone` +
  `docker compose up`. The repo carries all content; the service is derived,
  rebuildable state.
- **Capability minimization over injection detection.** Security is enforced
  architecturally (what an agent's container/network/credentials allow), not by
  asking the model nicely or by detecting attacks.
- **Humans steer, agents self-manage day-to-day.** Agents never ask for trivial
  in-sandbox actions; they escalate cross-boundary decisions to an async queue
  the oversight committee drains on its own schedule.

**Scope boundary:** sprints (short, self-contained) are feasible today and run
autonomously. The *program* (long, open-ended) is the genuinely hard part and is
deliberately kept under human judgment.

**Out of scope for this design:** `majordomo` (the author's private fleet-management
system) is referenced only as proof-of-concept inspiration and is never read into
or modified by this platform.

## 2. Architecture (Approach C — hybrid)

Two storage modes, split by what each is good at:

- **Content → a git repo** (OKF-formatted markdown). Source of truth for
  programs, sprints, results, ideas, decisions, and discussions. Human- and
  agent-readable, diffable, portable, shareable.
- **Coordination → a small containerized service** for the things filesystems
  are bad at: task queue, resource/GPU lock manager + ledger, search index
  (keyword + embeddings), API/MCP gateway, and the internal dashboard.

```
┌── Humans: Oversight Committee ──┐      ┌─ Internal results website ─┐
│  approve/reject/refine/fund     │      │  search, browse, provenance │
└──────────────┬──────────────────┘      └─────────────▲──────────────┘
        Dashboard (read/write via service)             │ read
        ┌──────▼───────────────────────────────────────┴─────┐
        │  COORDINATION SERVICE (containerized, portable)    │
        │  task queue · resource ledger+GPU locks · search   │
        │  index · API/MCP gateway · dashboard               │
        └───┬────────────────┬───────────────────┬───────────┘
   spawn/   │      lease     │       read/write  │ commits
   monitor  │                │                   ▼
   ┌────────▼───────┐  ┌─────▼────────┐   ┌───────────────────────┐
   │ Agent runtime  │  │ Resource mgr │   │ GIT REPO (OKF content)│
   │ heartbeat svcs │  │ (smart agent │   │ programs/ sprints/    │
   │ Workers/PM/RM  │  │  + ledger)   │   │ results/ ideas/       │
   └────────┬───────┘  └──────────────┘   │ artifacts/ .coscience/│
   enforced │                             └───────────────────────┘
   ┌────────▼───────┐
   │ Capability     │  Worker (data+scripts+whitelisted web)
   │ sandbox        │  Scout  (open web only) → screening gate
   └────────────────┘
```

## 3. Substrate & data model (git repo)

OKF format: one concept per markdown file, YAML frontmatter, linked by markdown
links. Layout:

```
co-science/
├── programs/<program-id>/
│   ├── program.md            # goal, status, budget, decision_log, links to sprints
│   └── discussion.md         # program-level OC ↔ PM threads
├── sprints/<sprint-id>/
│   ├── sprint.md             # the contract (see below)
│   ├── progress.md           # live checkpoint state (idempotent resume)
│   └── discussion.md         # sprint-level threads
├── ideas/<idea-id>.md        # proposed directions (PM/committee)
├── results/<result-id>.md    # OKF concept: lightweight summary + provenance
├── artifacts/<sprint-id>/    # heavy outputs: code, figures, datasets
├── discussion/<topic>.md     # cross-cutting conversation
└── .coscience/               # resources.yaml, capability profiles, service config
```

**Design choices**

- **Lightweight result / heavy artifact split.** `results/<id>.md` is a small OKF
  summary (claim, method, outcome, links) sized so 10–20 fit in an agent's
  context. Bulky outputs live under `artifacts/` (or an object store) and are
  *referenced* via OKF's `resource` field, never inlined.
- **Provenance as first-class frontmatter.** Extend OKF with relation lists:
  `confirms`, `refutes`, `inspired_by`, `supersedes` — each a list of
  `result-id`s. Renders as a navigable graph on the dashboard.
- **The sprint file is a contract.** `sprint.md` frontmatter:
  `goals`, `plan`, `status` (proposed/approved/executing/done/canceled),
  `resources_required` (gpu/cpu/time/token caps), `capability_profile`
  (Worker vs Scout pipeline), `priority`, links to program + produced results.
  One file: what the OC approves, what the agent executes against, what the
  dashboard renders.
- **`program.md` is a living document.** Its own `status` + `decision_log`;
  git keeps revision history. Programs evolve as discoveries land (goal
  refinement, scope change, pivot). The PM proposes program revisions; the OC
  approves them (higher-stakes than sprint approval).
- **Governance lives in the repo.** OC **decisions** are append-only
  `decision_log` entries (`{who, when, decision, rationale}`) in the relevant
  `program.md`/`sprint.md` — git gives the audit trail, the log makes rationale
  machine-readable so the PM can act on "refine, because X". OC **communications**
  are threaded markdown (`discussion.md`), read by the PM as context. Humans post
  via the dashboard; the service commits to the repo.
- **Search is derived state.** Auto-keywords go into frontmatter `tags`
  (git-visible). Embedding vectors are built by the service into a rebuildable
  index, so the repo stays portable and the index is disposable.

## 4. Agent runtime (heartbeat sessions)

All roles share one mechanism: a heartbeat loop (OpenClaw-style). A session
wakes, reads durable state (repo + service), does a bounded chunk of work,
writes results back, checkpoints, and sleeps until the next beat or an event.
Losing a session costs at most the last beat.

**Research agent (Worker) — sprint execution flow**

1. Service assigns an approved sprint → Worker session spawned **in its sandbox
   container** with the sprint's `capability_profile`.
2. Reads the `sprint.md` contract.
3. Requests GPU/host from the resource manager → gets a **lease** or is queued.
4. Executes experiments within its profile, checkpointing to `progress.md` each
   beat (idempotent restart).
5. Produces `results/<id>.md` (OKF + provenance) and heavy outputs to
   `artifacts/<id>/`; releases the lease.
6. Marks the sprint `done`; may emit follow-up `ideas/`.

**Detached long-running jobs (the "run this pipeline for a week" case).** Much
research is launch-a-job-then-wait: the agent kicks off a pipeline that occupies
a resource for days while the agent itself does nothing. The job is therefore
**decoupled from the agent session**:

- The agent launches the job as a **detached process on the host** (it survives
  the agent session) and records its handle/PID + status in `progress.md`.
- The agent then sleeps and only wakes periodically to poll the job; it does real
  work again when the job finishes. A dead agent session is replaced and
  **re-attaches** to the still-running job by reading `progress.md`.
- The **lease is held by the sprint, not the session** — so a week-long pipeline
  keeps its GPU even while the agent is idle and even across agent restarts.
- The **watchdog distinguishes job liveness from agent liveness**: "agent silent
  but job healthy" is fine; "job died" is the alert. Lease renewal is driven by
  job liveness, not agent activity.

**Program manager** — one per program. Heartbeat: read new results + OC
`discussion`/`decision_log`, update the program summary, propose new sprints
**and/or program revisions**, respond to "refine because X" requests, surface to
the dashboard. Replaces the author's manual "review results, propose, brainstorm"
session. Flexible on long-lived vs. compact-and-continue; the interface (reads
repo, writes proposals) is fixed.

**Resource manager** — one per environment. Lightweight agent over an
authoritative ledger (see §6). The ledger is deterministic and transactional;
the agent supplies policy.

**Fragility mitigations**

- External durable state + bounded beats + idempotent checkpoint/resume.
- **Watchdog:** the service tracks each agent's last-beat timestamp; silent/stuck
  agents are flagged on the dashboard and restarted (and their leases reclaimed).
- **Cost ceiling:** the sprint's token/resource cap is enforced; the agent halts
  and escalates near the limit.

**Self-improvement, safely:** each agent has an instruction file plus an
append-only, diffable `lessons` file, versioned in the repo and periodically
curated — *not* free-form unsupervised self-editing. Web-exposed output never
writes directly to durable agent memory (see §5).

## 5. Sandbox & capability model

The three trifecta channels:

| Axis | = trifecta channel |
|---|---|
| Open / non-whitelisted web | untrusted content (poison entry point) |
| Private data & tools | sensitive data |
| Run scripts / compute | action + exfiltration |

**Rule of Two** (Meta, 2025; refined by the 2025 "Design Patterns for Securing
LLM Agents against Prompt Injections" paper and defense-in-depth practice): an
unsupervised agent gets at most two of three; all three requires a human in the
loop. **Key insight: whitelisted web is *trusted* content, so it is not the
untrusted-content axis.** This collapses to **two agent types**:

- **Worker** = private data + scripts + *whitelisted* web. Does the actual
  research. Rule-of-Two-safe because the untrusted-content channel is closed.
- **Scout** = *open/untrusted* web only — no data, no scripts. Untrusted content
  can't hurt anything: nothing to steal, no way to act.

**Mandatory pipeline (load-bearing):**

```
Scout (open web) → screening gate (reviewer agent; human for sensitive) → Worker
```

The Scout's report is untrusted content. If it flowed directly into a Worker
(data + scripts), the full trifecta would reassemble. The screening gate is what
keeps two safe agents from composing into one unsafe one, and prevents injected
instructions from persisting into `lessons` files or propagating between agents.

**Enforcement is architectural, not prompt-based** (injection *detection* is
bypassed ~100% by determined attackers, so it is defense-in-depth only):

- **Web** = network egress policy (open egress only for Scouts; everything else
  allow-listed to whitelisted domains).
- **Private data/tools** = whether credentials/mounts/MCP tools exist at all.
- **Scripts** = whether an exec sandbox exists and what egress it has.
- The profile is set at **container spawn and is immutable for the session** — an
  agent cannot escalate its own capabilities.

**All-three sprints are forbidden by default** — they must be decomposed into
staged sub-sprints with different profiles (quarantined Scout → screen → Worker).
Human-in-the-loop-per-action stays available as the escape hatch.

### Autonomy & escalation boundary (rule: blast radius, not difficulty)

**Autonomous — never ask** (inside the sandbox = just do it):
edit/create files in own `sprints/<id>/` + `artifacts/<id>/`; run scripts,
install packages, iterate within the resource lease; read granted data; write
results/progress; request resources; emit follow-up ideas.

**Escalate — surface, don't block** (written to the async needs-decision queue;
agent continues other work or sleeps until resolved — never a synchronous prompt):
hitting a token/resource/cost cap; needing a capability outside its profile or
ungranted data; any irreversible or cross-boundary action (deleting/modifying
shared data, touching another sprint, changing platform config, secrets beyond
granted tools); the sprint goal looking unachievable/contradictory.

Out-of-profile actions are *physically impossible* in the sandbox, so they
become escalations automatically rather than silent overreach.

## 6. Resource / GPU scheduler

Inventory is **declared** in `.coscience/resources.yaml` (re-declared per
environment → portable): each host's GPU model + VRAM, CPU threads, RAM,
hostable profiles, egress policy.

PoC inventory:

| Resource | Capacity |
|---|---|
| GPU (big) | RTX 4090 24GB — avatar |
| GPU (small) | RTX 2080 Ti 11GB — superskomputer (remote, CentOS 7 EOL, disks full) |
| CPU pool | avatar 32T + ~36-thread box + superskomputer 12T + spare desktops |
| Disk | per-host capacity + free space (superskomputer is nearly full → low) |
| Runtime slots | max concurrent agent sessions (flat-subscription cap) |
| Tokens / $ | per-sprint + per-program caps (may be 0 in v1) |

The scarce, contended resource is **GPUs (2) vs up to 6 sprints**.

**Leases, not ownership.** Request = `{type, amount (VRAM/cores/GB), duration,
preemptible?, priority}`; grant = a **lease** in the ledger, held **by the sprint,
not the session** (so detached week-long jobs keep their resource across agent
restarts — see §4). Renewal is driven by **job liveness**, not agent activity; a
lease whose job has died (or which has no live job and a silent agent) expires and
the resource is reclaimed.

**Long-duration leases are a first-class case.** A request may legitimately be
"GPU for 7 days, non-preemptible." Two consequences:

- **Duration is declared up front and is part of what the OC approves.** Granting
  a 7-day GPU lease against a 2-GPU pool is a real capacity commitment, so it's a
  visible scheduling decision at sprint-approval time, not a surprise.
- **Preemptibility is an explicit lease attribute.** A long training run that
  can't be cleanly checkpointed is `preemptible: false` — the scheduler honors it
  as a hard hold for its full duration (it reduces available capacity but is never
  yanked mid-run). Only `preemptible: true` leases participate in the
  preempt-and-requeue policy below.

**Anti-gridlock policies**

1. **All-or-nothing grants** — a sprint declares its full need up front; never a
   partial set, so no agent holds GPU-A while blocking on GPU-B.
2. **Priority + aging** — OC sets priority; long-waiting requests age up so
   nothing starves.
3. **Max-hold + graceful preemption (preemptible leases only)** — a starved
   high-priority sprint preempts a low-priority `preemptible: true` holder:
   signal → checkpoint to `progress.md` → release → requeue. Safe because of
   idempotent resume; costs one beat. `preemptible: false` long jobs are never
   preempted — the scheduler plans around them.
4. **Bin-packing by fit** — small-VRAM jobs → 2080 Ti, large → 4090; pure-CPU
   sprints → CPU boxes, keeping GPUs free for GPU work; disk-heavy sprints away
   from nearly-full hosts.

**Runtime slots are a first-class resource:** even with 6 sprints approved, only
N run concurrently (whichever binds first — GPU, runtime slots, or token budget);
the rest queue.

**Disk is a resource with a lifecycle beyond the sprint.** Unlike GPU/CPU (freed
the moment the lease ends), artifacts on disk persist after a sprint finishes.
So disk is handled in two phases:

- **During the sprint:** the sprint declares a disk budget; outputs count against
  it. The scheduler won't place a disk-heavy sprint on a host without room (this
  matters for superskomputer, whose disks are nearly full), and a sprint
  approaching its disk budget escalates rather than filling the host.
- **After the sprint:** artifacts are **retained while their result is live and
  referenced**; cold/unreferenced artifacts become candidates for archival to
  cheaper storage or pruning. The resource manager tracks per-host disk pressure
  and surfaces it on the dashboard; low free space blocks new placements on that
  host. (Exact retention/archival policy is an open question — see §10.)

**Split:** the ledger is authoritative and transactional (atomic leases, no
double-grant); the RM agent supplies policy (preemption, aging weights). Cap
breaches escalate rather than silently overspending.

## 7. Dashboard & internal website

One internal web app (in the compose bundle, LAN-only + auth, not public). A thin
read/write layer over (git repo + service state): renders markdown/OKF content
and the service's ledger/index; human writes go through the service, which commits
to the repo. View-clusters:

**Governance & operations**

1. **Sprint board (kanban)** — cards by status.
2. **Sprint detail** — the contract, live `progress.md`, results, leases,
   `decision_log`, discussion. OC acts here: approve/reject/refine, comment,
   set priority/budget.
3. **Needs-decision queue** — the async escalation inbox; OC resolves → service
   writes back → agent's next heartbeat picks it up.
4. **Ideas feed** — PM-proposed + committee-added directions; promote to sprint
   or send back to PM with questions. Also surfaces **program-revision** proposals.
5. **Resource view** — lease table + wait queue + host utilization.
6. **Agent monitor** — live agent health from the watchdog.

**Knowledge (results website)**

7. **Results browser** — keyword + semantic search, browse by program/tag, follow
   provenance links (`confirms`/`refutes`/`inspired_by`) as a navigable graph,
   click through to artifacts.

**Identity:** lightweight auth, but real identities (for `decision_log`
attribution and multi-user collaboration), captured from day one.

## 8. End-to-end workflow

1. **Program created** (human): `program.md` with goal + budget. PM pointed at it.
2. **PM generates sprint ideas** → `ideas/`, surfaced on the Ideas feed.
3. **OC reviews**: reject / refine (back to PM via discussion) / add own. On
   approval → `sprint.md` with `status: approved`, `capability_profile`,
   `resources_required`, priority, caps. Budget allocated; `decision_log` records
   who/why.
4. **Resource manager** sees approved sprints + priorities in the ledger.
5. **Execution:** service spawns a Worker per approved sprint in its sandbox. It
   requests resources → RM grants a lease or queues it. If open web is needed, a
   Scout runs first → screening gate → Worker consumes the sanitized report.
6. **Worker runs on its heartbeat:** experiments, checkpoint each beat, escalate
   blockers to the needs-decision queue, stay within caps. Produces
   `results/<id>.md` + `artifacts/`, releases the lease, marks `done`, may emit
   follow-up `ideas/`.
7. **Result lands in the Results DB**, gets indexed, appears in the results browser.
8. **PM heartbeat** reviews new results + OC discussion → updates the program
   summary → proposes next sprints **and/or program revisions** → Ideas feed.
9. **OC** tracks progress, proposes/approves/refines the next round; approves
   program revisions as discoveries steer the goal.
10. **Loop** until the OC judges the program goal achieved.

Throughout: the watchdog flags stuck agents, cost caps halt runaways, and every
human/agent decision and message is versioned in the repo.

## 9. Embeddings & external services

- **Default embedding provider = a cloud API** (OpenAI `text-embedding-3`-class),
  pluggable via service config — so the platform is portable to any environment.
  Avatar's LAN-only embeddings API is demoted to an *optional* provider for
  fully-private/air-gapped deployments.
- **Privacy implication:** a cloud embedding API means result summaries leave the
  environment. Fine for collaborative/shared programs; a per-deployment trust
  decision for sensitive private research. The provider knob (cloud default,
  local fallback) lets each deployment choose; the platform stays portable either
  way.

## 10. Open questions / to research before implementation

- **OKF maturity:** the spec is v0.1, published 2026-06-12 — expect churn. Decide
  whether to extend OKF in place (provenance/resource relations) or wrap it.
- **Heartbeat runtime:** adopt OpenClaw / NemoClaw (security-hardened) vs. a
  minimal home-grown heartbeat over the task queue. Needs an empirical spike on
  long-running Claude Code session reliability (context management, resume, cost).
- **Coordination service stack:** concrete choice of queue + ledger + search
  index components that containerize cleanly and stay portable.
- **Sandbox enforcement mechanism:** containers + network egress policy details
  per profile; how the Scout→gate→Worker screening is implemented; verify each
  configuration is actually airtight.
- **Cost model:** realistic token/compute burn for a multi-day sprint, to confirm
  the economics.
- **Artifact retention/archival policy:** when unreferenced artifacts get archived
  vs. pruned, where cold storage lives, and how this interacts with the
  portability requirement (heavy artifacts probably don't travel in the git repo).

## 11. Non-goals (YAGNI for v1)

- Money/token economy beyond hard caps (v1 tokens may be 0).
- Automated result validation as a subsystem — revalidation is just another
  sprint the OC can spawn; quality is the OC's call.
- Auto-hardening of agents (second priority — results are already reviewable via
  the dashboard).
- Public (non-internal) website.
- Scaling beyond the single PoC fleet (the varied-VM / many-program future is a
  later phase; portability is designed in so the jump is a copy, not a rebuild).
