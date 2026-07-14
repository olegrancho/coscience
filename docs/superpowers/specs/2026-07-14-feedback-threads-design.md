# Threaded Feedback (human ↔ agent) + PM compute edits — Design

**Status:** Design approved (pending spec review)
**Date:** 2026-07-14
**Related:** builds on the attribution work (`2026-07-13-user-accounts-design.md`); motivated by a live case where the PM was told twice to change a sprint's compute and did nothing.

## 1. Summary

Two changes:

1. **The PM can edit a sprint's compute** (`resources_required`). Today the PM has no
   action to change it, so "run this on CPU" feedback is silently unactionable.
2. **Feedback becomes a two-way conversation.** Every human→agent feedback surface
   (sprint→planner comments, sprint→worker comments, idea comments, program
   guidance) becomes a **thread**: the human writes, the responsible agent (PM or
   worker) **acts and replies**, the human can continue the thread or mark it
   complete. The dashboard shows threads collapsed as today, with a badge when the
   agent has an unseen reply; clicking unwraps the full history.

Motivation: one-directional feedback gives no signal that an instruction was seen,
honored, or refused. Threads close that loop and (change 1) give the PM the lever
it was missing.

## 2. Feature 1 — PM edits `resources_required`

- **Schema** (`pm_claude.py`): add `resources_required` (a `{name: number}` map, or
  `null`) to the `sprint_edits` item. Prompt gains one line: the PM may adjust an
  editable sprint's compute in response to feedback (e.g. drop a GPU it can't get).
- **Apply** (`pm_agent.py`): when an edit carries `resources_required`, set it on the
  sprint if the sprint is editable. Editability for compute mirrors
  `service.edit_sprint`: allowed unless the sprint is `done`/`canceled` (so `queued`
  sprints — like the live `embeddings-c29` case — can be re-pointed to CPU).
- No other behavior change; this is additive to the existing edit path.

## 3. Feature 2 — threaded feedback

### 3.1 The thread model (shared)

One structure, stored inline where the feedback lives today:

```
FeedbackThread = {
  id: str,
  target: "pm" | "worker",          # which agent responds
  status: "open" | "complete",
  agent_unseen: bool,               # an agent reply the human hasn't opened yet
  created_at: float,
  messages: [ { role: "human" | "pm" | "worker", text: str, by: str, at: float } ],
}
```

- **Surfaces & responder:**
  - Sprint → **planner** threads (`target: pm`) — answered by the PM cycle.
  - Sprint → **worker** threads (`target: worker`) — answered by the worker agent while the sprint executes.
  - **Idea** comment threads (`target: pm`).
  - **Program guidance** threads (`target: pm`).
- **Storage:** threads live inline in the same files as today — `sprint.md`
  (replaces the flat `comments` list), the idea record (replaces idea `comments`),
  and the program guidance store (replaces flat notes).
- **`by`** on human messages is the attributed user (from `current_user`); agent
  messages carry the responder label in `role`.
- **Protection:** an idea with any thread still counts as "protected" (same rule the
  old `comments` gave).

### 3.2 Back-compat (live data must survive)

On load, any legacy record is adapted to a thread with a single `human` message:
- a sprint comment `{id,text,added_at,target,by}` → a thread of that `target` with one message;
- an idea comment / guidance note → a `pm` thread with one message.

No migration script is required — adaptation happens in the substrate load path, so
the existing embeddings/demo substrate keeps working and old feedback appears as
open, unanswered threads.

### 3.3 Reply cadence (no spam)

An agent replies to a thread only when it is **open** AND its **last message is from
a human** (the ball is in the agent's court). After the agent replies, the last
message is the agent's, so the thread drops out of the agent's to-answer set until
the human adds another message. Completed threads are excluded entirely.

### 3.4 PM reply mechanism (structured)

- `gather_context` collects the open, human-last threads across the PM's surfaces
  (sprint-planner, idea, guidance) with their message history.
- PM output gains `thread_replies: [ { thread_id, text } ]`. The PM **acts** on the
  feedback through its existing levers (now including `resources_required`, revise,
  propose, curate) and its short reply states what it did — or why it can't.
- Apply appends each reply as a `pm` message and sets `agent_unseen = true`.

### 3.5 Worker reply mechanism (file-harvested)

The worker is a free-running `claude -p` session that exists only while the sprint
runs, so it can't emit structured JSON like the PM. Instead:

- The worker's instructions already inject human comments as direction; they now
  also tell it: to reply to a feedback thread, append one JSON line
  `{ "thread_id": "...", "text": "..." }` to `<sprint_dir>/feedback.out`.
- On each dispatch beat, the service **harvests** new lines from `feedback.out`
  (tracking a byte offset, same idea as reading agent output), appends each as a
  `worker` message to the matching open thread, and sets `agent_unseen = true`.
- **Consequence:** worker threads are answered only while the sprint is
  `executing`. A worker thread on a `queued`/`done` sprint waits; the UI hints
  "the agent will respond when this runs." PM threads are answered regardless.

### 3.6 Service / API

Human actions (all derive the actor from `current_user`):
- **Start / append:** the existing `add_sprint_comment` / `add_idea_comment` /
  `add_guidance` create a new thread (first human message) or, given a `thread_id`,
  append a human message to an existing thread. Appending to a `complete` thread
  reopens it (`status → open`).
- **Complete:** `POST …/threads/{tid}/complete` → `status = complete`; the
  responder drops it from context.
- **Seen:** `POST …/threads/{tid}/seen` → `agent_unseen = false` (called when the
  human unwraps a thread with an unseen reply).

Endpoints are scoped under their surface (sprint / program-idea / program-guidance)
following the existing route shapes.

### 3.7 Frontend

- One reusable `<FeedbackThread>` component, used in `SprintDetail` (both planner
  and worker threads), `IdeasView` (idea threads), and the guidance section of
  `ProgramDetail`.
- **Collapsed:** reads like today (first/last line + author chip). A **badge** shows
  when `agent_unseen` is true (agent replied, unread).
- **Click → unwrap:** shows the full message history; on open it calls `…/seen` to
  clear the badge. Unwrapped view has an add-message box and a **Mark complete**
  action. Worker threads on a non-running sprint show the "answers when it runs" hint.
- Author display reuses the existing `<UserChip>`; agent messages are labeled
  "PM" / "Agent".

### 3.8 Badge / seen semantics

`agent_unseen` is a single per-thread boolean shared across users (not per-user
unread). Set when any agent reply is appended; cleared when any user opens the
thread. Simplest useful signal; per-user unread is a non-goal.

## 4. Build order (one spec, sequenced plan)

1. Feature 1 (PM edits `resources_required`) — small, fixes the live bug.
2. Thread model + back-compat load adaptation (shared).
3. Sprint **planner** threads end-to-end: PM `thread_replies` (context + reply +
   act), service endpoints, `<FeedbackThread>` in SprintDetail.
4. Sprint **worker** threads: `feedback.out` harvest on the dispatch beat + worker
   instruction; wire into the same component.
5. Extend to **idea** threads and **guidance** threads (reuse model + component).

Each step is independently testable and leaves the app working.

## 5. Non-goals

- Reusing the live interactive PM chat (`ChatThread`) — that's a separate,
  expensive Claude session; feedback replies are cheap and part of the normal
  agent cadence.
- Per-user unread tracking (the badge is a shared per-thread flag).
- Worker replies outside sprint execution (a worker only exists while running).
- Real-time streaming of agent replies — they appear on the next PM cycle / dispatch beat.
