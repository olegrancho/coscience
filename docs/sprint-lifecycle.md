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
| `proposed` | PM or human suggested it; awaiting review. Counts against the PM's cap (`MAX_PROPOSED`, 4). |
| `approved` | A human authorized it. **Authorized ≠ scheduled** — it is held here until released. |
| `queued` | Released to the scheduler. Runs when a resource slot frees. |
| `executing` | Lease granted; the worker agent is running. |
| `parked` | Human shelved a proposed sprint. Inert, and off the PM's cap. |
| `hibernated` | Dispatcher yielded it at a safe point to free capacity; resumes later. |
| `done` / `failed` / `canceled` | Terminal until a human acts. |

## Transitions

| From → To | Who | How |
|---|---|---|
| — → `proposed` | PM | `proposals` field in the cycle JSON |
| — → `proposed` | human | create sprint in the dashboard |
| `proposed` → `approved` | human only | `approve_sprint` (`service.py:89`) |
| **`approved` → `queued`** | **PM** | **`release_ids` in the cycle JSON (`pm_agent.py:780-802`)** |
| `approved` → `queued` | human (override) | `run_sprint` (`service.py:98`), POST `/api/sprints/<id>/run` |
| `proposed` → `queued` | human | `run_sprint` — one-step authorize+run |
| `approved` → `proposed` | PM | `reopen_ids` in the cycle JSON (`pm_agent.py:804-825`) |
| `approved` → `proposed` | human | `send_back_sprint` (`service.py:108`) |
| `proposed` → `parked` → `proposed` | human | `park_sprint` / `unpark_sprint` |
| `queued` → `executing` | dispatcher | lease granted; eligible states are `queued`, `executing`, `hibernated` (`dispatcher.py:18`) |
| `executing` → `hibernated` → `queued` | dispatcher | cooperative yield at a safe point; never a hard kill |
| `executing` → `done` / `failed` | worker | `worker.py:417` / `worker.py:366`, `463` |
| `proposed` / `approved` / `queued` → `canceled` | human | `reject_sprint` |
| `done` / `failed` → `queued` | human | `resume_sprint` — drops results, resets counters, re-queues |

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
