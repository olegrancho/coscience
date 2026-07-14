"""Read/write the OKF substrate (a directory of markdown files)."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from coscience.frontmatter_io import parse, serialize
from coscience.models import (Sprint, SprintStatus, ProgressState, Result, Program,
                              ProgramStatus, PMState, Idea, ChatThread)


class Substrate:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    # --- sprints ---
    def sprint_dir(self, sprint_id: str) -> Path:
        return self.repo_root / "sprints" / sprint_id

    def load_sprint(self, sprint_id: str) -> Sprint:
        text = (self.sprint_dir(sprint_id) / "sprint.md").read_text()
        fm, _body = parse(text)
        # plan is natural-language suggested steps; tolerate legacy [{id,run}] entries
        plan = [s if isinstance(s, str) else str(s.get("run") or s.get("text") or s)
                for s in fm.get("plan", [])]
        import time as _t
        from coscience import threads as _th
        if "threads" in fm:
            sprint_threads = list(fm.get("threads") or [])
        else:  # back-compat: adapt legacy comments (target defaults to worker)
            sprint_threads = [_th.adapt_legacy(c, "worker", now=float(c.get("added_at", _t.time())))
                              for c in fm.get("comments", [])]
        return Sprint(
            id=sprint_id,
            status=SprintStatus(fm["status"]),
            goals=fm.get("goals", ""),
            plan=plan,
            program=fm.get("program"),
            results=list(fm.get("results", [])),
            resources_required={
                str(k): float(v) for k, v in (fm.get("resources_required") or {}).items()
            },
            priority=int(fm.get("priority", 0)),
            preemptible=bool(fm.get("preemptible", True)),
            rationale=str(fm.get("rationale", "")),
            title=str(fm.get("title", "")),
            summary=str(fm.get("summary", "")),
            created_at=None if fm.get("created_at") is None else float(fm["created_at"]),
            threads=sprint_threads,
            model=str(fm.get("model", "")),
            votes=[{"by": str(v["by"]), "value": int(v["value"]), "at": float(v["at"])}
                   for v in fm.get("votes", [])],
            decisions=[{"by": str(d.get("by", "")), "action": str(d.get("action", "")),
                        "at": float(d.get("at", 0.0))} for d in fm.get("decisions", [])],
        )

    def save_sprint(self, sprint: Sprint) -> None:
        fm = {
            "status": str(sprint.status),
            "goals": sprint.goals,
            "plan": list(sprint.plan),
        }
        if sprint.program is not None:
            fm["program"] = sprint.program
        if sprint.results:
            fm["results"] = sprint.results
        if sprint.resources_required:
            fm["resources_required"] = sprint.resources_required
        if sprint.priority != 0:
            fm["priority"] = sprint.priority
        if not sprint.preemptible:
            fm["preemptible"] = False
        if sprint.rationale:
            fm["rationale"] = sprint.rationale
        if sprint.title:
            fm["title"] = sprint.title
        if sprint.summary:
            fm["summary"] = sprint.summary
        d = self.sprint_dir(sprint.id)
        # Stamp creation time on first save only, so it survives later edits and
        # legacy sprints (saved before this field existed) stay unstamped.
        if sprint.created_at is None and not (d / "sprint.md").is_file():
            sprint.created_at = time.time()
        if sprint.created_at is not None:
            fm["created_at"] = sprint.created_at
        if sprint.threads:
            fm["threads"] = list(sprint.threads)
        if sprint.model:
            fm["model"] = sprint.model
        if sprint.votes:
            fm["votes"] = list(sprint.votes)
        if sprint.decisions:
            fm["decisions"] = list(sprint.decisions)
        d.mkdir(parents=True, exist_ok=True)
        (d / "sprint.md").write_text(serialize(fm, f"# Sprint {sprint.id}\n"))

    def iter_sprints(self, status: SprintStatus | None = None) -> list[Sprint]:
        sprints_dir = self.repo_root / "sprints"
        if not sprints_dir.is_dir():
            return []
        out = []
        for d in sorted(sprints_dir.iterdir()):
            if (d / "sprint.md").is_file():
                sprint = self.load_sprint(d.name)
                if status is None or sprint.status == status:
                    out.append(sprint)
        return out

    # --- progress ---
    def _progress_path(self, sprint_id: str) -> Path:
        return self.sprint_dir(sprint_id) / "progress.md"

    def load_progress(self, sprint_id: str) -> ProgressState:
        path = self._progress_path(sprint_id)
        if not path.is_file():
            return ProgressState(sprint_id=sprint_id)
        fm, _ = parse(path.read_text())
        started = fm.get("started_at")
        return ProgressState(
            sprint_id=sprint_id,
            agent_token=str(fm.get("agent_token", "")),
            started_at=None if started is None else float(started),
            failures=int(fm.get("failures", 0)),
            last_error=str(fm.get("last_error", "")),
        )

    def save_progress(self, progress: ProgressState) -> None:
        fm = {
            "agent_token": progress.agent_token,
            "started_at": progress.started_at,
            "failures": progress.failures,
            "last_error": progress.last_error,
        }
        d = self.sprint_dir(progress.sprint_id)
        d.mkdir(parents=True, exist_ok=True)
        self._progress_path(progress.sprint_id).write_text(
            serialize(fm, f"# Progress {progress.sprint_id}\n")
        )

    # --- results ---
    def save_result(self, result: Result) -> None:
        fm = {"type": "result", "sprint": result.sprint}
        if result.completed_at is not None:
            fm["completed_at"] = result.completed_at
        d = self.repo_root / "results"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{result.id}.md").write_text(serialize(fm, result.summary))

    def load_result(self, result_id: str) -> Result:
        path = self.repo_root / "results" / f"{result_id}.md"
        fm, body = parse(path.read_text())
        # explicit completed_at wins; otherwise fall back to when the file was written
        completed_at = fm.get("completed_at")
        if completed_at is None:
            try:
                completed_at = path.stat().st_mtime
            except OSError:
                completed_at = None
        return Result(id=result_id, sprint=str(fm.get("sprint", "")), summary=body.strip(),
                      completed_at=None if completed_at is None else float(completed_at))

    def iter_results(self) -> list[Result]:
        results_dir = self.repo_root / "results"
        if not results_dir.is_dir():
            return []
        out = []
        for path in sorted(results_dir.glob("*.md")):
            out.append(self.load_result(path.stem))
        return out

    # --- programs ---
    def program_dir(self, program_id: str) -> Path:
        return self.repo_root / "programs" / program_id

    def load_program(self, program_id: str) -> Program:
        text = (self.program_dir(program_id) / "program.md").read_text()
        fm, body = parse(text)
        return Program(
            id=program_id,
            title=str(fm.get("title", "")),
            goals=body.strip(),
            status=ProgramStatus(fm.get("status", "active")),
            pm_model=str(fm.get("pm_model", "")),
            workdir=str(fm.get("workdir", "")),
        )

    def save_program(self, program: Program) -> None:
        fm = {"type": "program", "title": program.title, "status": str(program.status)}
        if program.pm_model:
            fm["pm_model"] = program.pm_model
        if program.workdir:
            fm["workdir"] = program.workdir
        d = self.program_dir(program.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "program.md").write_text(serialize(fm, program.goals.strip() + "\n"))

    def iter_programs(self, status: ProgramStatus | None = None) -> list[Program]:
        programs_dir = self.repo_root / "programs"
        if not programs_dir.is_dir():
            return []
        out = []
        for d in sorted(programs_dir.iterdir()):
            if (d / "program.md").is_file():
                p = self.load_program(d.name)
                if status is None or p.status == status:
                    out.append(p)
        return out

    def save_report(self, program_id: str, report: str) -> None:
        d = self.program_dir(program_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.md").write_text(report.rstrip() + "\n")

    def load_report(self, program_id: str) -> str:
        path = self.program_dir(program_id) / "report.md"
        return path.read_text() if path.is_file() else ""

    def load_pm_state(self, program_id: str) -> PMState:
        path = self.program_dir(program_id) / "pm.md"
        if not path.is_file():
            return PMState(program_id=program_id)
        fm, _ = parse(path.read_text())
        return PMState(
            program_id=program_id,
            cycle=int(fm.get("cycle", 0)),
            last_run=fm.get("last_run"),
            proposed_ids=list(fm.get("proposed_ids", [])),
            log=list(fm.get("log", [])),
            last_fingerprint=str(fm.get("last_fingerprint", "")),
            last_signals=dict(fm.get("last_signals", {})),
            activations=list(fm.get("activations", [])),
        )

    def save_pm_state(self, state: PMState) -> None:
        fm = {
            "type": "pm_state",
            "cycle": state.cycle,
            "last_run": state.last_run,
            "proposed_ids": state.proposed_ids,
            "log": state.log,
        }
        if state.last_fingerprint:
            fm["last_fingerprint"] = state.last_fingerprint
        if state.last_signals:
            fm["last_signals"] = state.last_signals
        if state.activations:
            fm["activations"] = state.activations
        d = self.program_dir(state.program_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "pm.md").write_text(serialize(fm, f"# PM state {state.program_id}\n"))

    def load_guidance(self, program_id: str) -> list[dict]:
        path = self.program_dir(program_id) / "guidance.md"
        if not path.is_file():
            return []
        fm, _ = parse(path.read_text())
        out = []
        for n in fm.get("notes", []):
            out.append({"id": str(n["id"]), "text": str(n["text"]),
                        "added_at": float(n["added_at"])})
        return out

    def save_guidance(self, program_id: str, notes: list[dict]) -> None:
        d = self.program_dir(program_id)
        d.mkdir(parents=True, exist_ok=True)
        fm = {"type": "guidance", "notes": notes}
        (d / "guidance.md").write_text(serialize(fm, f"# Guidance {program_id}\n"))

    # --- PM chat (a Q&A thread with the planner) ---
    def load_chat(self, program_id: str) -> list[dict]:
        path = self.program_dir(program_id) / "chat.md"
        if not path.is_file():
            return []
        fm, _ = parse(path.read_text())
        return [{"role": str(m.get("role", "user")), "text": str(m.get("text", "")),
                 "at": float(m.get("at", 0.0))} for m in fm.get("messages", [])]

    def save_chat(self, program_id: str, messages: list[dict]) -> None:
        d = self.program_dir(program_id)
        d.mkdir(parents=True, exist_ok=True)
        fm = {"type": "chat", "messages": messages}
        (d / "chat.md").write_text(serialize(fm, f"# PM chat {program_id}\n"))

    # --- multi-thread PM chat (each thread is one resumable claude session) ---
    def chats_dir(self, program_id: str) -> Path:
        return self.program_dir(program_id) / "chats"

    def chat_thread_dir(self, program_id: str, thread_id: str) -> Path:
        return self.chats_dir(program_id) / thread_id

    def list_chat_threads(self, program_id: str) -> list[ChatThread]:
        d = self.chats_dir(program_id)
        threads = [self.load_chat_thread(program_id, sub.name)
                   for sub in (d.iterdir() if d.is_dir() else [])
                   if (sub / "thread.md").is_file()]
        threads = [t for t in threads if t is not None]
        threads.sort(key=lambda t: t.created_at)
        return threads

    def load_chat_thread(self, program_id: str, thread_id: str) -> "ChatThread | None":
        path = self.chat_thread_dir(program_id, thread_id) / "thread.md"
        if not path.is_file():
            return None
        fm, _ = parse(path.read_text())
        return ChatThread(
            id=thread_id,
            title=str(fm.get("title", "New chat")),
            scope=str(fm.get("scope", "read")),
            announced_scope=str(fm.get("announced_scope", "")),
            session_id=str(fm.get("session_id", "")),
            created_at=float(fm.get("created_at", 0.0)),
            turns_done=int(fm.get("turns_done", 0)),
            pending=bool(fm.get("pending", False)),
            agent_token=str(fm.get("agent_token", "")),
            messages=[{"role": str(m.get("role", "user")), "text": str(m.get("text", "")),
                       "at": float(m.get("at", 0.0)), "by": str(m.get("by", ""))}
                      for m in fm.get("messages", [])],
        )

    def save_chat_thread(self, program_id: str, thread: ChatThread) -> None:
        d = self.chat_thread_dir(program_id, thread.id)
        d.mkdir(parents=True, exist_ok=True)
        fm = {"type": "chat_thread", "title": thread.title, "scope": thread.scope,
              "announced_scope": thread.announced_scope,
              "session_id": thread.session_id, "created_at": thread.created_at,
              "turns_done": thread.turns_done, "pending": thread.pending,
              "agent_token": thread.agent_token, "messages": thread.messages}
        (d / "thread.md").write_text(serialize(fm, f"# Chat {thread.id}\n"))

    def delete_chat_thread(self, program_id: str, thread_id: str) -> None:
        import shutil
        d = self.chat_thread_dir(program_id, thread_id)
        if d.is_dir():
            shutil.rmtree(d)

    # --- ideas (a pool of candidate directions + the PM's summary of it) ---
    def load_ideas(self, program_id: str) -> tuple[str, list[Idea]]:
        path = self.program_dir(program_id) / "ideas.md"
        if not path.is_file():
            return "", []
        fm, _ = parse(path.read_text())
        ideas = []
        for n in fm.get("ideas", []):
            ideas.append(Idea(
                id=str(n["id"]), text=str(n["text"]),
                source=str(n.get("source", "human")),
                by=str(n.get("by", "")),
                pinned=bool(n.get("pinned", False)),
                comments=[{"id": str(c["id"]), "text": str(c["text"]),
                           "added_at": float(c["added_at"]),
                           "by": str(c.get("by", ""))} for c in n.get("comments", [])],
                created_at=float(n.get("created_at", 0.0)),
                demoted=bool(n.get("demoted", False)),
            ))
        return str(fm.get("summary", "")), ideas

    def save_ideas(self, program_id: str, summary: str, ideas: list[Idea]) -> None:
        d = self.program_dir(program_id)
        d.mkdir(parents=True, exist_ok=True)
        fm = {
            "type": "ideas",
            "summary": summary,
            "ideas": [
                {"id": i.id, "text": i.text, "source": i.source, "pinned": i.pinned,
                 "by": i.by,
                 "comments": list(i.comments), "created_at": i.created_at,
                 **({"demoted": True} if i.demoted else {})}
                for i in ideas
            ],
        }
        (d / "ideas.md").write_text(serialize(fm, f"# Ideas {program_id}\n"))

    # --- git ---
    def commit(self, message: str) -> None:
        if not (self.repo_root / ".git").is_dir():
            return
        subprocess.run(["git", "-C", str(self.repo_root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_root), "commit", "-q", "-m", message],
            check=False,  # tolerate "nothing to commit"
        )
