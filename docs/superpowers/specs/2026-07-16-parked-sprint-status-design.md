# Parked Sprint Status — Design

**Date:** 2026-07-16
**Status:** approved for planning

## 1. Goal

Add a human-only `parked` status for **proposed** sprints: a shelf that removes a
pending experiment from the PM's proposed-cap without deleting or demoting it, so
the human can free up sprint space for the PM. Parked sprints are inert (no agent,
no PM action) and recoverable.

## 2. State machine

```
proposed --park-->   parked
parked   --unpark--> proposed
parked   --demote--> canceled (+ new demoted idea)   [existing demote, extended]
parked   --delete--> canceled                          [soft delete]
```

- **Human-only.** The PM never parks/unparks/deletes; it never even sees parked
  sprints.
- **Park scope:** only `proposed` ("pending") sprints may be parked (that's what
  the proposed-cap counts, so that's what frees PM space).
- **Delete is soft:** it sets `CANCELED` (action `"delete"`). No file removal; the
  record + git history stay. (Distinct in intent from demote, which also mints an
  idea.)

## 3. Backend

### 3.1 Model
- `SprintStatus.PARKED = "parked"` (models.py enum).

### 3.2 Service transitions (service.py)
- `park_sprint(id, by)` — require `PROPOSED`; `set_status(PARKED, action="park")`.
- `unpark_sprint(id, by)` — require `PARKED`; `set_status(PROPOSED, action="unpark")`.
- `delete_sprint(id, by)` — require `PARKED`; `set_status(CANCELED, action="delete")`.
- `demote_sprint` — extend the allowed set to include `PARKED` (currently
  `PROPOSED`/`APPROVED`).

### 3.3 PM cap — no code change needed
Parking works for the cap **because** parked is a distinct status:
- Enforcement count `iter_sprints(status=PROPOSED)` (pm_agent) excludes parked.
- `gather_context.open_sprints` lists only proposed/approved/queued/executing, so
  parked never enters `proposed_count` or the PM prompt.
Leave `PARKED` out of those sets — parking a proposed sprint frees a slot with no
other change. The PM does not see parked sprints at all.

### 3.4 Graph
- Parked stays in `get_graph` (recoverable, real experiment) — NOT excluded like
  canceled. `node_stage` maps it to `experiment`.
- Add `status` to each graph node payload (`GraphNode.status`: the sprint status,
  `""` for ideas) so the frontend can render parked nodes **dimmed**.

### 3.5 HTTP (http_api.py)
- `POST /sprints/{id}/park`, `POST /sprints/{id}/unpark`, `POST /sprints/{id}/delete`
  — same shape as approve/reject (Depends(current_user), NotFound→404,
  ValueError→422, return `get_sprint`).

## 4. Frontend

- **status.ts:** add `parked` to `STATUS_VAR` (+ a `--st-parked` CSS var, a muted
  amber/gray) and to `SPRINT_STATE_ORDER`.
- **sprintActions.ts:** add `"parked"` to the `SprintStatus` type and `park`/
  `unpark`/`delete` to the `Action` type. `availableActions`:
  - `proposed` → add `park`.
  - `parked` → `["unpark", "demote", "delete"]`.
- **SprintDetail.tsx:** park (on proposed, in the ⋯ menu), unpark (button on
  parked), demote + delete (menu on parked, delete behind a confirm). Handlers
  mirror the existing `demote` closure (api call → notify → refresh).
- **api.ts:** `parkSprint`/`unparkSprint`/`deleteSprint` (POST → `Sprint`).
- **ProgramDetail.tsx:** add `"parked"` to the experiments `order` array (so the
  status filter offers it). Not capped.
- **Graph node dim:** `GraphNode.status` flows to the flow-node data; box + dot
  nodes render at reduced opacity when `status === "parked"`.

## 5. Testing

- Service: park (proposed→parked), unpark (parked→proposed), delete
  (parked→canceled), demote from parked, and that park/unpark/delete reject wrong
  source statuses (e.g. park an executing sprint → ValueError).
- Cap: a parked sprint does not count toward `proposed_count`/`free_slots` (extend
  the existing cap tests).
- HTTP: park/unpark/delete happy path + 422 on wrong status + 404 missing.
- get_graph: node payload carries `status`; parked node present (not excluded).
- Frontend: `sprintActions` expected arrays updated + `availableActions("parked")`.

## 6. Out of scope
- PM-initiated parking (human-only by design).
- Auto-unpark / expiry.
- Hard file deletion (delete is soft per decision).
