"""Read/write the OKF substrate (a directory of markdown files)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from coscience.frontmatter_io import parse, serialize
from coscience.models import Sprint, SprintStatus, Step, ProgressState, Result, Program, ProgramStatus, PMState


class Substrate:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    # --- sprints ---
    def sprint_dir(self, sprint_id: str) -> Path:
        return self.repo_root / "sprints" / sprint_id

    def load_sprint(self, sprint_id: str) -> Sprint:
        text = (self.sprint_dir(sprint_id) / "sprint.md").read_text()
        fm, _body = parse(text)
        plan = [Step.from_dict(d) for d in fm.get("plan", [])]
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
        )

    def save_sprint(self, sprint: Sprint) -> None:
        fm = {
            "status": str(sprint.status),
            "goals": sprint.goals,
            "plan": [{"id": s.id, "run": s.run} for s in sprint.plan],
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
        return ProgressState(
            sprint_id=sprint_id,
            completed_steps=list(fm.get("completed_steps", [])),
            detached={str(k): str(v) for k, v in (fm.get("detached") or {}).items()},
            outputs={str(k): str(v) for k, v in (fm.get("outputs") or {}).items()},
        )

    def save_progress(self, progress: ProgressState) -> None:
        fm = {
            "completed_steps": progress.completed_steps,
            "detached": progress.detached,
            "outputs": progress.outputs,
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
        )

    def save_program(self, program: Program) -> None:
        fm = {"type": "program", "title": program.title, "status": str(program.status)}
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
        )

    def save_pm_state(self, state: PMState) -> None:
        fm = {
            "type": "pm_state",
            "cycle": state.cycle,
            "last_run": state.last_run,
            "proposed_ids": state.proposed_ids,
            "log": state.log,
        }
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

    # --- git ---
    def commit(self, message: str) -> None:
        if not (self.repo_root / ".git").is_dir():
            return
        subprocess.run(["git", "-C", str(self.repo_root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_root), "commit", "-q", "-m", message],
            check=False,  # tolerate "nothing to commit"
        )
