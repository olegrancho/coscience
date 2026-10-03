"""Catch-up reports (todo I1): a brief, written by the planner, of what happened in a
program since the last one — for someone who has not looked in a week.

A report is a read-only chat with the planner whose first message is the catch-up
request. That is the shape the reports had when they were written by hand ("Update
<date>" chats), and it keeps what made those useful: the planner answers with the
program's full context, and the follow-ups that come after reading — "explain #3",
"promote it to a sprint" — happen in the same session. The thread carries a
`catchup` record (since, sprints, trigger, by) so the catch-up page can list it.

When one is due: once a period (`catchup_every_days`, weekly by default) has passed
since the last report, and only if at least `catchup_min_sprints` sprints (10) have
finished since — a quiet week produces nothing. The dispatch loop checks; the page's
"Write one now" starts one on demand."""
from __future__ import annotations

import time
from datetime import datetime

from coscience.models import SprintStatus

DAY = 86400.0
FINISHED = (SprintStatus.DONE, SprintStatus.FAILED)


def reports(substrate, program_id: str) -> list:
    """The program's catch-up chats, newest first."""
    out = [t for t in substrate.list_chat_threads(program_id) if t.catchup]
    return sorted(out, key=lambda t: t.created_at, reverse=True)


def last_report_at(substrate, program_id: str) -> float | None:
    rs = reports(substrate, program_id)
    return rs[0].created_at if rs else None


def _finished_at(sprint) -> float:
    from coscience.pm_agent import _finished_at as finished_at
    return finished_at(sprint)


def finished_since(substrate, program_id: str, since: float) -> list:
    """Sprints of this program that finished (done or failed) after `since`, oldest first."""
    out = []
    for s in substrate.iter_sprints():
        if s.program == program_id and s.status in FINISHED and _finished_at(s) > since:
            out.append(s)
    return sorted(out, key=_finished_at)


def window(substrate, program, now: float) -> float:
    """Where the next report starts: the last report, or one period back for the first."""
    last = last_report_at(substrate, program.id)
    if last is not None:
        return last
    return now - max(program.catchup_every_days, 1.0) * DAY


def due(substrate, program, now: float) -> tuple[bool, float, list]:
    """(due, since, finished sprints). Never due when the schedule is off, while a
    report is still being written, or before a full period has passed."""
    if program.catchup_every_days <= 0:
        return False, 0.0, []
    rs = reports(substrate, program.id)
    if rs and rs[0].pending:
        return False, 0.0, []
    last = rs[0].created_at if rs else None
    if last is not None and now - last < program.catchup_every_days * DAY:
        return False, last, []
    since = window(substrate, program, now)
    done = finished_since(substrate, program.id, since)
    return len(done) >= max(program.catchup_min_sprints, 0), since, done


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%-d %B %Y")


def request_text(program, since: float, sprints: list, results_dir) -> str:
    """The catch-up request, posted as the chat's first message. Its shape is the one
    the hand-written "Update <date>" chats converged on: bottom line first, one table
    of numbers, numbered next steps a follow-up can point at."""
    if sprints:
        listed = "\n".join(
            f"- [{s.title or s.id}](/sprints/{s.id}) — {s.status.value}, "
            f"finished {datetime.fromtimestamp(_finished_at(s)).strftime('%-d %b')}"
            for s in sprints)
    else:
        listed = "- (none finished in this period)"
    return f"""Catch-up report: what happened in this program since {_day(since)}.

Write it for someone who has not looked at the program since then and has forgotten
the details. Plain words, no jargon, self-contained. Use these sections, in order:

1. **Bottom line** — one or two bold sentences: what changed, and anything the program
   said earlier that no longer holds.
2. **Key numbers** — one table of the main measurements against the baseline the
   program measures itself by, saying whether each difference is significant.
3. **What this period delivered** — results that moved things, clean negatives that
   narrowed the search, and blockers, each linked to its sprints.
4. **Where the program stands** — against its goals.
5. **What to do next** — numbered and in order, each with why and roughly what it
   costs, so the reader can answer "promote #3".
6. **Housekeeping** — stuck or stale sprints, and artifacts or docs the new results
   have made out of date.

Keep it short if little happened. Read a full result under {results_dir} when its
summary is not enough. Do not propose, approve or change anything here: the reader
follows up in this chat.

Sprints that finished in this period ({len(sprints)}):
{listed}"""


def start(substrate, program_id: str, *, by: str, trigger: str, now: float | None = None,
          since: float | None = None, service=None, launch=None) -> dict:
    """Open a catch-up chat and post the request. Returns the chat's public record.
    `since` overrides where the report starts (for a reader away longer than a period)."""
    from coscience.service import Service
    now = time.time() if now is None else now
    service = service or Service(substrate.repo_root)
    program = substrate.load_program(program_id)
    rs = reports(substrate, program_id)
    if rs and rs[0].pending:
        raise ValueError("a catch-up report is already being written")
    start_at = since if since is not None else window(substrate, program, now)
    sprints = finished_since(substrate, program_id, start_at)
    title = f"Catch-up {datetime.fromtimestamp(now).strftime('%-d %b')}"
    tid = service.create_chat(program_id, title=title)["id"]
    thread = substrate.load_chat_thread(program_id, tid)
    thread.created_at = now
    thread.catchup = {"since": start_at, "sprints": [s.id for s in sprints],
                      "trigger": trigger, "by": by}
    substrate.save_chat_thread(program_id, thread)
    text = request_text(program, start_at, sprints, substrate.repo_root / "results")
    return service.post_chat_message(program_id, tid, text, by=by, launch=launch)
