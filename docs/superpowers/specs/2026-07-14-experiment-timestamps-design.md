# Experiment Timestamps & Status Timeline — Design

**Date:** 2026-07-14
**Requested by:** Aish ("add dates or serial numbers for experiments tracker") + user
("agree with dates; show datetime in experiment, and datetime of status change in
experiment text"). Serial numbers rejected — sprints already have a `ref` id.

## Goal

Surface *when* an experiment (sprint) was created and *when each status change
happened*, as a single merged chronological timeline on the sprint page.

## Decisions

- **Full lifecycle history**: record every status transition, system-driven
  (executing/done/failed) and manual (approve/run/send_back/reject/reopen).
- **Absolute datetime display**, relative on hover (inverse of existing `RelTime`).
- **One merged timeline** combining governance actions and status transitions.

## Backend

### models.py
- Add `Sprint.status_history: list[dict]` — each entry
  `{"status": str, "at": float, "by": str, "action": str}`.
  `action` = the verb for a human/PM action (`approve`, `run`, `send_back`,
  `reject`, `reopen`); `""` for a system transition.
- New module-level helper:
  ```python
  def set_status(sprint, new_status, by="", action=""):
      """Set sprint.status and append a history entry only when the status
      actually changes (dedup: dispatcher-grant + worker-start both set
      EXECUTING within one cycle)."""
      last = sprint.status_history[-1]["status"] if sprint.status_history else None
      if last != new_status.value:
          sprint.status_history.append(
              {"status": new_status.value, "at": time.time(),
               "by": str(by or ""), "action": action})
      sprint.status = new_status
  ```

### Transition sites — replace `sprint.status = X` with `set_status(...)`
- `service.py`: `approve` (action="approve"), `run` (action="run"),
  `send_back` (action="send_back"), `reject` (action="reject") — pass `by`.
  Remove the paired `_decide(...)` calls (status_history now carries `by`+verb).
- `pm_agent.py`: release→QUEUED (`by="pm", action="run"`),
  reopen→PROPOSED (`by="pm", action="reopen"`).
- `dispatcher.py`: grant→EXECUTING (system).
- `worker.py`: start→EXECUTING (system), →FAILED (system), →DONE (system).

`_decide` / the `decisions` field are left in the model for reading legacy data
but no longer written.

### substrate.py
- Persist `status_history` to frontmatter (like `decisions`) and load it back.
- On first-ever save (the existing `created_at is None` branch), seed
  `status_history = [{"status": sprint.status.value, "at": created_at,
  "by": "", "action": ""}]` so a fresh sprint's timeline starts at birth.
  Legacy sprints (already saved) are never re-seeded → no dup vs their
  existing `decisions`.

### service.get_sprint
- Add `"created_at": self._created_at(sprint)` and
  `"status_history": list(sprint.status_history)` to the payload.

## Frontend

### ui.tsx
- New `AbsTime({at, prefix})`: renders absolute local datetime
  (`toLocaleString`), relative time on hover (`title=relTime(at)`).

### api.ts
- Extend `Sprint` type: `created_at?: number | null`,
  `status_history?: {status: string; at: number; by: string; action: string}[]`.

### SprintDetail.tsx
- Header: near `ref {id}` show `created <AbsTime at={created_at}/>`.
- Replace the standalone "decision log" block with a **merged timeline**:
  combine `status_history` + legacy `decisions` (mapped to a common shape),
  sort by `at`. Render each row:
  - has `action` → `<b>{action}</b> by <UserChip/> · <AbsTime/>`
  - else → `→ <StatusBadge/> · <AbsTime/>` (system transition, no chip).
  Non-mine `by` keeps the existing `OTHER_SHADE` background.

## Tests (pytest)
- `set_status`: appends on change, dedups same-status, sets status.
- Transitions record history: worker done/failed, dispatcher grant,
  service approve/run/send_back/reject.
- substrate round-trip persists `status_history`; first save seeds initial
  entry; legacy sprint (pre-existing) not re-seeded.
- `get_sprint` payload includes `created_at` and `status_history`.

## Out of scope
- Serial numbers (rejected).
- Backfilling full history for legacy sprints (only the initial seed + forward
  transitions; legacy `decisions` still shown via the merge).
