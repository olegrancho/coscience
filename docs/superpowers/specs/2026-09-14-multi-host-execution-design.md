# Multi-Host Execution — Design (block O)

**Status:** approved 2026-09-14
**Date:** 2026-09-14
**Todo:** O1 (this document); inherited by O2–O9
**Scope:** how a sprint's work runs on servers other than the machine that runs the
dispatcher — where the agent and its jobs run, how files move, how hosts are
onboarded and described, and how a worker agent asks for help when a host goes
wrong.

---

## 1. Summary

The worker agent stays where it runs today, beside the dispatcher. Only its detached
jobs run on a remote host, and the worker agent launches them there itself, over SSH,
into a working directory it has been told about. It is told which host it is on and
what that host is, because an agent that does not know its environment hardcodes
paths and assumes local software. The platform keeps only the parts that fail
silently: it verifies a declared job is really running, and it copies the job's
declared outputs back before waking the agent.

Hosts are onboarded by a human with an SSH key, probed for what they really have, and
confirmed into the pool. Each host can be reserved for named programs. Each program
keeps PM-curated notes per host — environments, what the host is good for, quirks.
When something goes wrong that the worker agent cannot handle, it escalates: the
sprint is held, the PM either resolves it at the resource or instruction level or
passes it to a human.

---

## 2. What assumes one machine today

| Assumption | Where |
|---|---|
| A job is a local `Popen` the agent started with `nohup … & echo $!` | `claude_executor.py:150` (DETACHED-JOB PROTOCOL) |
| A job's identity is `<pid>:<starttime>` read from this machine's `/proc` | `executor.py:40-91`, `models.py:160` (`job_token`) |
| Liveness and kill are local calls | `worker.py:175` (`job_alive`, `terminate` injectable) |
| Outputs are wherever the agent wrote them, readable in place | `worker.py:512` reads `job.json`; agents write absolute paths such as `<substrate>/sprints/<sprint-id>/work/run.out` |
| Capacity is one flat map with no machine in it | `resources.py` (`ResourcePool.capacity`), `ledger.py` |

The first servers to onboard differ in the ways this design has to handle: a shared
GPU box whose old driver and C library rule out current wheels, whose disk is nearly
full, and which already holds a file-synced copy of some program folders; and a
dedicated CPU-only box with nothing synced. Both have `rsync`, `setsid`, `nohup`,
Python 3, git and outbound internet; neither has the agent CLI.

---

## 3. Decisions

Recorded so a later reader does not relitigate them.

| Decision | Choice | Rejected, and why |
|---|---|---|
| Where the worker agent runs | Beside the dispatcher, as today | **Agent on the host:** every host needs the CLI and a login, the agent's sprint-folder writes would reach the substrate from a second machine, and usage and call-log tracking move off-box. **Per-host daemon:** a service to build and run before any job is placed; premature for two machines. |
| Where jobs run | On the sprint's host; `local` when the host is the dispatcher's own machine | — |
| Who launches a remote job | The worker agent, over SSH, into a working directory it is told | **A platform `coscience job start` that mirrors files and hides the host:** agents that do not know their environment hardcode paths, rely on local software and forget files, and the failures surface as confusion on a machine they cannot see. |
| How files reach the host | The worker agent moves them (rsync, git, whatever fits); no rule on where it edits | **A file-sync tool:** asynchronous, so a job can start on yesterday's code and a wake can read an output that has not arrived; covers only hosts in the sync. |
| How outputs come back | The dispatcher copies the job's declared `collect` paths into the sprint folder before waking the agent, and says so in the wake message | Leaving it to the agent: a forgotten copy reads as a failed job. |
| Substrate writers | One: the dispatcher's machine | Remote hosts never write the substrate repo directly. |
| Access | SSH key only, from the account that runs the dispatcher | Passwords, stored secrets, per-host daemons. |
| Placement once work has started | Sticky: a sprint stays on its host | It moves only when a human moves it, or the PM reallocates it in answer to an escalation (§8). |
| Escalation lease | The sprint keeps its lease while held | Releasing it would let other work onto a host that may be in a broken state. |
| Human alert | Dashboard flag only | Email or Slack notification. |
| What the PM may do about an escalation | Resource level (reallocate to another host) and instruction level (resume with instructions) | Hands-on repair of any kind. When the PM judges the issue cannot be fixed easily, it escalates to a human. |

---

## 4. Hosts

A host is one machine in the pool. The dispatcher's own machine is the implicit host
`local`, so a single-machine install is unchanged: today's `.coscience/resources.yaml`
is read as that one host.

A host entry holds only what is not secret, so the list lives in the substrate — a
`hosts:` section of `.coscience/resources.yaml`, whose top-level amounts remain the
`local` host (schema in `docs/superpowers/plans/2026-09-14-o2-hosts-model.md`):

- **how to reach it** — an SSH alias or `user@host:port`; the key lives in the
  dispatcher account's `~/.ssh`;
- **capacity offered to the pool** — CPUs, memory, and each GPU with its VRAM (O3);
  may be less than the machine has, e.g. holding back a few threads;
- **allowed programs** — all, or a named list; placement never grants a reserved
  host to any other program, and a program's PM sees only the hosts its program may
  use;
- **run root** — where sprint working directories go, e.g. `~/coscience-runs`;
  never a path inside a synced folder, so sync and rsync do not fight over files;
- **probed facts** (§5), shown on the host's page and summarized to worker agents.

A lease names its host (O2). A request fits a host when that host alone covers it,
unless the request is `distributed` — recorded from O4, placed across hosts from O6.

GPUs are cards, not a summed count (O3). A host lists them as `gpus: [{vram_gb, model}]`;
a bare `gpu: N` still means N cards whose VRAM nobody declared, which are only ever lent
whole. A request's `gpu: N` alone asks for N whole cards; adding `gpu_vram_gb: X` makes
them X-GB shares other work may use too (on its own it means one card). A lease records
the card indices it holds, and the worker agent is told them as `CUDA_VISIBLE_DEVICES`.
A sprint re-granted while its agent or job still runs is given its previous cards first.

A request (O4) uses four keys — `cpu` cores, `memory_gb`, `gpu` whole cards and
`gpu_vram_gb` VRAM per shared card — plus a sprint-level `distributed` flag saying the
work may span hosts. The flag is recorded, editable and shown now, and used for
placement from O6.

**Rolling out cards is one-way.** Code older than O3 cannot read a `gpus:` list, and it
does not see VRAM shares as holding a card. Declare `gpus:` only after every host that
runs the platform has O3 deployed; to roll back, remove `gpus:` and let running share
leases finish first.

---

## 5. Onboarding

A human adds a host; a probe proposes an entry; the human confirms it. Nothing enters
the pool unconfirmed (O5).

The probe is deterministic code, not an agent: one read-only script over key-only SSH
(`BatchMode=yes`, default host-key policy — an unknown host key fails with an instruction
to connect once by hand) and the checks below. Each probe is recorded in the substrate as
`.coscience/host-probes/<name>.json`; confirming writes the host into `resources.yaml`
`hosts:`. A run root is `~`, a path under `~/` or an absolute path in letters, digits, `.`,
`_`, `-` and `/`, so nothing a human types reaches a remote shell unquoted.
Onboarding is off unless the deployment sets `COSCIENCE_ALLOW_ONBOARDING=1`, because a
probe makes the backend ssh with its own keys to a target a dashboard user types; a
server whose checks failed cannot be confirmed. Not yet collected (O7): disk quota,
compilers, transfer speed to the dispatcher machine, Python versions beyond `python3`,
and other users' processes (only logged-in users are counted).

**Declared by the human**

- SSH alias or `user@host:port` (key already installed)
- shared or dedicated — a shared box should not offer its full CPU count
- capacity offered to the pool
- allowed programs
- run root
- owner or contact, and any usage rules (hours, longest job)

**Found by the probe**

- OS and version, glibc, architecture — an old glibc already rules out many modern
  wheels and binaries
- CPU model, physical cores against threads, current load and other users' processes
- memory and swap
- per GPU: model, VRAM, driver version, highest supported CUDA, processes already on
  the card
- free space and quota under the run root — warn at onboarding below a threshold
- tools: `rsync`, `setsid`, `nohup`, Python versions, conda or uv, compilers, docker or
  apptainer
- outbound internet (pip, model downloads) and rough transfer speed to the dispatcher
  machine
- clock offset and boot id — a changed boot id means every job on the host is gone

**Tested before confirming**

- non-interactive key SSH works
- the run root is writable
- a `setsid nohup` job survives the SSH session closing, and the platform can find
  and kill it by its token
- rsync works in both directions

---

## 6. Running work on a remote host

### 6.1 What the worker agent is told

When a sprint is placed on a remote host, the worker agent's instructions gain a host
section:

- the host name, how to reach it (`ssh <host>`), and its sprint working
  directory (`~/coscience-runs/<sprint-id>/`);
- the probed facts in a few lines (CPU, memory, GPU and driver, OS, tools);
- this program's notes for the host (§9);
- that paths, environments and installed software differ from the machine it is
  running on, and nothing is shared unless it puts it there;
- that its own shell runs on the dispatcher's machine: quick commands and editing
  happen there, and anything heavy runs on the host;
- that it may work however suits the sprint — edit on the host over SSH, or edit
  locally and push — but a job only ever sees what is on the host when it starts;
- to clean up what it created on the host before finishing, and only after the
  results it needs are in the sprint folder.

### 6.2 Declaring a job

The detached-job protocol stays, launched over SSH. `job.json` gains two fields:

```json
{"host": "gpu1",
 "pid": 4121,
 "cmd": "python train.py --fold 0",
 "out_file": "~/coscience-runs/<sprint-id>/work/train.out",
 "collect": ["~/coscience-runs/<sprint-id>/work"],
 "expected_seconds": 7200, "wake_after_seconds": 7500, "max_seconds": 14400,
 "note": "fold0 retrain"}
```

`host` omitted means `local`, so today's declarations keep working. `collect` lists
paths on the host to bring back.

The instructions give the launch form verbatim:

```bash
ssh gpu1 'cd ~/coscience-runs/<sprint-id> && setsid nohup python train.py --fold 0 > work/train.out 2>&1 < /dev/null & echo $!'
```

### 6.3 What the dispatcher does

1. **Verify on declare.** Before accepting `job.json`, read
   `ssh <host> cat /proc/<pid>/stat`. A missing pid is reported straight back to the
   worker agent (relaunch in the same session with the error) instead of surfacing
   hours later. The recorded token is `<host>:<pid>:<starttime>`, plus the host's
   boot id.
2. **Liveness.** Each beat checks the token on its host. An SSH failure is *unknown*,
   never *dead* (§7).
3. **Stop and hibernate.** `ssh <host> kill -- -<pgid>`; `setsid` made the job its own
   process-group leader.
4. **Collect before waking.** When the job exits or the wake time comes, rsync every
   `collect` path into `sprints/<id>/collected/`, then relaunch the worker agent. The wake
   message states what happened:

   > Your job on gpu1 ended (exit 0). Before waking you, the platform
   > copied `~/coscience-runs/<sprint-id>/work` → `sprints/<sprint-id>/collected/work` at 14:02. Read the
   > results there; you do not need to copy them yourself. Nothing else on the host
   > was copied.

   A failed copy is stated just as plainly — which path, and why — so a stale folder
   is never read as fresh. (O6: copies land in `collected/<last folder name>`, so two
   `collect` paths may not share a last folder name; a job the platform could not stop
   at its time limit is named in the note, and the agent is asked to stop it.)
5. **Record remote footprint.** Every working directory and `collect` path a sprint
   used is recorded on the sprint. The dispatcher never deletes them itself: a
   hibernated sprint needs its directory back. Directories of finished, failed or
   stopped sprints are listed on the host's page for a human to remove (O7).

### 6.4 What stays local

The PM, wiki, chat and housekeeping agents, and the worker agent process itself, all
keep running on the dispatcher's machine. Only worker-agent jobs are placed.

---

## 7. Unreachable hosts and reboots

- An SSH failure makes a job's state unknown. The lease is kept and the check retried
  each beat.
- A host unreachable past a timeout (suggested 30 minutes; the number belongs to O7)
  stops receiving new grants, and every sprint sleeping on a job there is escalated by
  the dispatcher (§8.2) — no agent is running to notice.
- A changed boot id means the host rebooted and its jobs are gone: those jobs are
  declared lost and the worker agent is woken with that fact.
- No job is killed and no lease released because one probe timed out.

---

## 8. Escalation

### 8.1 Raised by the worker agent

The worker agent writes `sprints/<id>/escalate.json` and ends its turn:

```json
{"what": "gpu1 refuses SSH since 13:10; job 4121 state unknown",
 "tried": "three retries over 20 min",
 "may_have_broken_something": false,
 "needs": "a working host, or confirmation the job is still running"}
```

Its instructions say when: a host it cannot reach, a failure it cannot explain after
a reasonable attempt, a belief that it damaged the host or the program's data, or an
environment blocker it cannot resolve within the sprint. Not for ordinary bugs in its
own code.

### 8.2 Raised by the dispatcher

When a host stays unreachable past the timeout while a worker agent sleeps on a job
there, the dispatcher writes the same record itself, with `by: dispatcher`.

### 8.3 The hold

The sprint moves to a new status, **`escalated`**, which is added to
`docs/sprint-lifecycle.md`:

- the worker agent is not relaunched;
- the lease is kept; the worker slot is released (`ledger.release_key`), as during a
  job sleep;
- a running job keeps running; if it ends during the hold, its outputs are still
  collected, but nobody is woken;
- the escalation is posted as a thread on the sprint targeting the PM, which wakes
  the PM's next cycle through the existing path (`pm_agent.py:190`).

### 8.4 The PM's answer

The PM cycle output gains `escalation_answers`, one per open escalation, with one of
three actions:

| Action | Effect |
|---|---|
| `resume` | The sprint returns to `executing`; the PM's instructions are added to the worker agent's relaunch message. |
| `reallocate` | The sprint is moved to a named host the program may use: outputs are collected from the old host if it is reachable, a job still running there is stopped, the lease is released and re-requested on the new host, and the worker agent is told it starts fresh there with only what is in the sprint folder. |
| `to_human` | The escalation is passed to a human. |

The PM has no tools and does no hands-on repair. Its prompt tells it to escalate to a
human whenever the issue needs someone on a machine, data may be damaged, or it is
not confident the problem can be fixed easily.

A sprint that escalates again after the PM resumed or reallocated it goes straight to
a human; the PM does not answer the same sprint twice in a row.

### 8.5 The human side

An escalated-to-human sprint is flagged on the dashboard: an attention badge on the
sprint and its program, and a count in the header. The human replies in the thread
and resumes, reallocates or stops the sprint.

---

## 9. Per-program host notes

Probed facts are the same for every program; what a host is for is not. Each program
keeps `programs/<id>/hosts/<host>.md`:

- environments to use (`~/envs/<name>`, how to activate);
- what the host is for in this program — e.g. a shared box used as fungible CPU for
  batch runs;
- quirks — e.g. an old GPU driver and C library that rule out current PyTorch builds.

**Writers.** The PM curates the notes through a `host_notes` field in its cycle
output. Worker agents report what they learned through a `host_notes` entry in
`finished.json` or in an escalation; the PM folds those in. A human can edit a note on
the dashboard.

**Readers.** Every worker agent placed on the host gets the note in its instructions,
and the PM sees its program's notes beside the COMPUTE block, so it proposes work that
fits the machine.

---

## 10. What each item inherits

| Item | Takes from this design |
|---|---|
| O2 | hosts file in the substrate; implicit `local`; allowed programs; lease names its host; run root |
| O3 | per-GPU VRAM and driver in the host entry; device choice handed to the job |
| O4 | canonical request keys (`cpu`, `memory_gb`, `gpu`, `gpu_vram_gb`) and the `distributed` flag, recorded; per-host COMPUTE for the PM |
| O5 | onboarding split: declared / probed / tested (§5); SSH key only |
| O6 | sticky placement respecting reservations; host section in worker-agent instructions; `job.json` `host` and `collect`; verify on declare; remote liveness, kill, collect-before-wake with the wake message; remote footprint record |
| O7 | unreachable timeout and boot-id rule (§7); per-host page with probed facts, leftover run directories, drain and remove |
| O8 | `escalated` status, `escalate.json`, dispatcher-raised escalation, PM `escalation_answers`, repeat goes to a human, dashboard flag |
| O9 | `programs/<id>/hosts/<host>.md`, PM `host_notes`, worker-agent reports, injection into instructions and the PM's COMPUTE block |

---

## 11. Left to the items

- The hosts and lease schema — settled in O2
  (`docs/superpowers/plans/2026-09-14-o2-hosts-model.md`).
- The unreachable timeout — O7, starting from 30 minutes.
- How a `distributed` request is split across hosts — not built in O6 (every request is
  placed on one host; the flag stays recorded only). When it lands, `over_capacity`
  and the unrunnable flag must judge a `distributed` request against the hosts it may span.
- Whether the wiki and chat agents ever need remote placement — out of scope for O.

**Before O6 makes any remote host placeable** (found in O2's final review; none has
an effect while only `local` takes work):

- The PM's COMPUTE block sums capacity across a program's hosts, but a request must
  fit on one host: give the PM per-host capacity (or the largest host) and say no
  request may exceed one host. (Done in O4: COMPUTE lists each host and states the
  one-host rule.)
- The Compute page capacity editor edits the pool total; it must edit the `local`
  host's amounts instead. (Done in O6: it edits this machine's capacity once a remote
  host takes work.)
- The unrunnable message ("needs gpu 1 but capacity is 0") must name the host it
  judged. (Done in O6: it names a pinned host, or the closest host when several take work.)
- A re-granted sprint's preferred GPU cards are matched by index only; once several
  hosts are placeable, the preference must name its host. (Done in O6: a sprint that
  launched anything is re-granted only on its own host, so the indices are that host's.)
- Onboarding and remote placement need an access decision: the dashboard's auth gate is
  inactive where no users file exists, and confirmed hosts will receive sprint code and data.
  (Decided in O5/O6: onboarding is off unless `COSCIENCE_ALLOW_ONBOARDING=1`, and no remote
  host is placeable unless `COSCIENCE_ALLOW_REMOTE=1`.)
- Refuse a run root of `~` or `/` for any `rsync --delete`, and find a remote job by its
  token (pid and start time), not by pid alone. (Done in O6: copying back never uses
  `--delete` and refuses `~` and `/`; a remote job token is `<host>:<pid>:<starttime>:<boot_id>`,
  and a job whose start time was never read is not signalled.)
- Run a probe as a background job: a probe of a stalling host can hold a request thread
  for minutes.
- Choose a placement policy on purpose; O2 is first-fit in declaration order, which
  lets small sprints fragment a large host.
- Yield-victim selection only frees room on one host at a time, so a deficit in both
  host CPU and a worker slot can miss a victim on another host. (Addressed in O3:
  victims are chosen by simulation and pruned.)
- For a multi-card share request the unrunnable message names one card size ("needs
  gpu_vram_gb 32 but capacity is 48"); say how many cards have enough VRAM on which host.

**O4 (done):** the PM prompt names the request keys, including `gpu_vram_gb` shares, and
the COMPUTE block lists each card's VRAM.

**O7:** a lease whose host was removed or renamed in the pool makes pool-wide
`available` negative and strands a sleeping sprint's worker-slot reacquire; drain
and remove must release or migrate such leases first. (Done in O7: such a lease is
"stranded" — it keeps its sprint's work but leaves pool-wide use, and the Compute page
lists it; a host is removed only when drained and no lease names it.) Likewise, card-less GPU leases
from before O3 that outnumber a host's cards are given no card on load and drop out of
`used`; over-commitment should be shown, not hidden.

**O7, from O6's final review:**

- Copying a job's outputs back runs inside the dispatch beat (up to 600 s per path), so a
  large collect delays every other sprint's beat; move it off the beat.
- The remote footprint record (§6.3 step 5) is not built: `job_collect` is cleared when a
  job ends. A host page can list `<run_root>/<sprint-id>` from `progress.host`, but collect
  paths outside the run directory are not remembered.
- Stopping or reconciling a sprint clears a remote job token even when the stop failed,
  with no note (the time-limit path has one). (Done in O7: the stop path writes the same note.)
- The sprint list's unrunnable check reads one progress file per sprint.
- Switching `COSCIENCE_ALLOW_REMOTE` off while a remote lease is held drops that host from
  the pool total while the lease still counts, so pool-wide `available` goes negative
  (per-host fit is unaffected). (Done in O7: that lease is stranded and leaves pool-wide use.) Both the HTTP server and the dispatch loop must have the
  variable set, or the Compute page and actual placement disagree.

**O7 as built:** the dispatch loop asks each placeable remote host `bash -c true` at most
once a minute (checks run concurrently) and records `.coscience/host-health.json`, which the
HTTP server reads. A host failing for 30 minutes is quiet. A quiet host, or one marked
`drain: true` in `resources.yaml`, takes no new grants, but a sprint whose agent or job is
still running there is re-adopted on it after a lost lease, so no job's tracking is dropped
because its host went quiet. One sprint's failing beat no longer aborts the dispatch cycle; the
error is recorded on the sprint and counted in the loop's output. Escalating sprints that sleep
on a quiet host is O8's.

A server is removed only when it is drained, at least two minutes have passed since the drain
(so a cycle that loaded the pool before the drain cannot still grant there), no lease names it,
and no unfinished sprint has worked on it. A sprint whose beat raises repeatedly is failed after
the worker's failure cap instead of holding its lease forever. Left open: removing a server is
not gated like adding one (it makes no SSH call, like the capacity and pause routes); a corrupt
health file restarts the 30-minute count; the sprint list reads the health file once per pinned
sprint; more than eight unreachable servers still add about 20 s per eight to a cycle.

**O8 as built:** a sprint escalates when its worker agent writes `escalate.json` (read on a clean
or a failed exit, removed at every launch, and winning over `finished.json`; a job declared in the
same turn is still tracked), or when the dispatcher sees it sleeping on a job whose host is quiet.
The sprint moves to `escalated`: its lease is kept and renewed, its worker slot released, it is
never granted or yielded, and a job it has keeps being watched and collected, but the agent is not
relaunched. The escalation is a sprint thread marked `kind: "escalation"`, addressed to the PM, or
to a human when the PM already answered this sprint once. One function applies every answer and
returns a reason instead of failing: the PM may resume (its instructions reach the next run once),
reallocate (the dispatcher then stops and collects on the old host, noting a stop it could not make,
releases the lease and re-pins the sprint to the new host, which starts a fresh session) or pass it
to a human; a human may resume, reallocate or stop at either level, and a human answer resets the
repeat rule, as does re-running a finished sprint. The dashboard shows the escalation on the sprint
page with the human answers and a count of human-level escalations in the header.

**O8, left open:** escalating a sprint on a host a human drained is not automatic (only a quiet
host raises one); a program list has a "needs you" badge but no per-program count; a human's
replies in the escalation thread are for people only — the agent reads only the answer's
instructions; the escalation beat and a human answer still race in theory on anything other than
job fields (answers never write job fields, and the beat reloads before saving); and the report
check for claimed answers is a text heuristic.


**O14 as built (reworked 2026-09-17).** A server stores one list: `programs:`, the programs
it runs. An empty list means it takes none; a server whose key has never been set admits
every program, and the dialog shows it with all of them ticked so the first save makes the
list explicit. There is no "all programs" state and no exclusion list. A program's settings
tick the servers it may run on, and creating a program asks which servers it may use,
defaulting to all; both write the same per-server list. Nothing in either view is disabled:
unticking a server's last program leaves it taking nothing, and the card says so.

**O15 as built.** Drain and its two-minute remove wait are gone. Remove marks a server
(`remove: true`): it takes no new grants, and the dispatcher deletes it at the start of
a cycle, before granting, once no lease names it and no unfinished sprint's work is on
it. Only the dispatcher grants, so deleting there cannot race a grant, and no human
timing is involved. Every writer of `resources.yaml` holds one file lock. A marked
server lists what it is waiting on; Keep takes the mark back. A hand-written
`drain: true` still keeps new work off a server, and Keep clears it.

**O11 as built.** After a probe, a human can ask an agent to survey the server. The survey
is a full-scope chat session scoped to the server (`.coscience/host-surveys/<name>/`),
built on program chat's thread store, launch, collection, gates and call log (kind
`survey`). The agent reaches the server over SSH, may write only inside the run root and
its own working directory, and writes `proposal.json`: capacity, GPU cards, notes and
overrides. The backend validates the proposal before showing it; an invalid one is shown
as an error and never applied. A failed check stays failed: the server enters the pool
over it only when the human accepts overrides and every failed check has the agent's
written reason, and those reasons are kept in the server's notes. Behind
`COSCIENCE_ALLOW_ONBOARDING`. This machine's Detect is unchanged.
