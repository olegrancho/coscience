# Sprint lifecycle — states and who drives them

The authoritative statement of how a sprint moves. If a memory, a comment or a habit says
otherwise, this file and the code it cites win.

Three actors move sprints, and they own different edges:

- **Human** — authorizes work and can override anything (dashboard / HTTP API → `service.py`)
- **PM agent** — plans, and **owns the approved pool as its queue** (`pm_agent.py`)
- **Dispatcher / worker** — execute what is already queued (`dispatcher.py`, `worker.py`)

## States

| State | Meaning |
|---|---|
| `proposed` | PM or human suggested it; awaiting review. Counts against the PM's cap (`Program.max_proposed`, falling back to `MAX_PROPOSED`, 4, when unset). |
| `approved` | A human authorized it — or the PM did, under a human's approval grant. **Authorized ≠ scheduled** — it is held here until released. |
| `queued` | Released to the scheduler. Runs when a resource slot frees. |
| `executing` | Lease granted; the worker agent is running. |
| `parked` | Human shelved a proposed sprint. Inert, and off the PM's cap. |
| `hibernated` | Dispatcher yielded it at a safe point to free capacity; resumes later. |
| `done` / `failed` / `canceled` | Terminal until a human acts. `canceled` is reversible: see `restore_sprint`. |

## Transitions

| From → To | Who | How |
|---|---|---|
| — → `proposed` | PM | `proposals` field in the cycle JSON |
| — → `proposed` | human | create sprint in the dashboard |
| `proposed` → `approved` | human | `approve_sprint` (`service.py`) |
| `proposed` → `approved` | PM, **only under a live approval grant** | `approve_ids` in the cycle JSON; recorded on the sprint as `approve (grant)` — see below |
| **`approved` → `queued`** | **PM** | **`release_ids` in the cycle JSON (`pm_agent.py:780-802`)** |
| `approved` → `queued` | human (override) | `run_sprint` (`service.py:98`), POST `/api/sprints/<id>/run` |
| `proposed` → `queued` | human | `run_sprint` — one-step authorize+run |
| `approved` stays `approved`, held | PM | `holds` in the cycle JSON — the planner's "not yet", with a reason shown on the sprint. It replaced a PM `reopen`: the planner can un-approve but cannot approve, so sending a sprint back to `proposed` spent a human decision it could not restore |
| `approved` → `proposed` | human | `send_back_sprint` (`service.py:108`) |
| `proposed` → `parked` → `proposed` | human | `park_sprint` / `unpark_sprint` |
| `queued` → `executing` | dispatcher | lease granted; eligible states are `queued`, `executing`, `hibernated` (`dispatcher.py:18`) |
| `executing` → `hibernated` → `queued` | dispatcher | cooperative yield at a safe point; never a hard kill |
| `executing` → `done` / `failed` | worker | `worker.py:417` / `worker.py:366`, `463` |
| `proposed` / `approved` / `queued` → `canceled` | human | `reject_sprint` |
| `done` / `failed` → `queued` | human | `resume_sprint` — drops results, resets counters, re-queues |
| `canceled` → where it was canceled from | **human only** | `restore_sprint` — undoes a cancel; a sprint canceled mid-run returns to `queued` as a fresh run, and one demoted to an idea is refused |

## Approval grants: the one way the PM approves

Approving is a human decision. A human can lend it to the PM for a bounded stretch with an
**approval grant** (Supercharge on the program page; `grant.py`): until the limit they chose
— a number of approvals, a deadline, or the current 5-hour or weekly usage window — the PM
may approve any proposed sprint in that program, a human's draft included, by listing it in
`approve_ids`. The PM's prompt shows the grant and what is left of it only while it is live.

The bound is enforced, not trusted: applying the cycle (`pm_agent.py`) re-checks the grant before every
approval and refuses those past the limit (reported as "Approve FAILED" in the cycle's
actions). A grant ends on its own when its limit is reached and says so — the end and its
reason stay on the program page until someone dismisses them — and a human can revoke it at
any time. An approval under a grant still does not schedule: the PM releases it with
`release_ids` like any other.

One kind of grant has no end of its own: **paced** (M3). It lets the PM approve only while
weekly usage is below the share of the weekly window that has passed — 30% used with half
the week gone may approve, 60% may not. While usage is ahead, the grant is paused rather
than ended: the PM's prompt carries no approval authority, approvals it lists anyway are
refused as "paused", and when the week catches up the authority comes back, which wakes the
PM. It runs until revoked, and bounds new approvals only; released work runs under the
usual usage gates.

## The part that surprises people

**Approve does not schedule.** The approved pool is the PM's managed queue: the human says
*"this is authorized"*, and the PM decides *when* it runs, sequencing releases as earlier
results land. That is why an approved sprint can sit for a while and still be healthy.

The dispatcher never looks at `approved` (`dispatcher.py:18`), so if the PM does not release
it and no human overrides, it stays put indefinitely.

The PM releases **only** by putting the sprint's exact id in `release_ids`. It has no tools
and writes nothing itself — every state change is applied by Python from the fields of the
one JSON object it returns (`pm_reasoner.py:1-3`). Prose in the `report` field performs
nothing; this has failed in production, so each cycle's report now carries a machine-written
**"Actions this cycle"** ledger, ids that don't resolve are reported instead of skipped
silently, and a report claiming an action nobody submitted is flagged in the beat log and in
`pm.md`.

## Where to look when a sprint is not moving

1. `report.md` → the **Actions this cycle** block: what the last cycle actually applied.
2. `programs/<id>/pm.md` → `log:` and `activations[]`: releases, failed releases, unbacked claims.
3. The PM loop log → `released …` / `SKIPPED <id> (why)` / `WARNING report claims …`.
4. `.coscience/queue.json` + `leases.json` → whether it reached the scheduler at all.
5. `.coscience/resources.yaml` → whether its `resources_required` can ever be satisfied here.
