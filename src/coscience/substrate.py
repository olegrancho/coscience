"""Read/write the OKF substrate (a directory of markdown files)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from coscience.frontmatter_io import parse, serialize
from coscience.models import Sprint, SprintStatus, Step, ProgressState, Result


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
            detached={str(k): int(v) for k, v in (fm.get("detached") or {}).items()},
        )

    def save_progress(self, progress: ProgressState) -> None:
        fm = {
            "completed_steps": progress.completed_steps,
            "detached": progress.detached,
        }
        d = self.sprint_dir(progress.sprint_id)
        d.mkdir(parents=True, exist_ok=True)
        self._progress_path(progress.sprint_id).write_text(
            serialize(fm, f"# Progress {progress.sprint_id}\n")
        )

    # --- results ---
    def save_result(self, result: Result) -> None:
        fm = {"type": "result", "sprint": result.sprint}
        d = self.repo_root / "results"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{result.id}.md").write_text(serialize(fm, result.summary))

    # --- git ---
    def commit(self, message: str) -> None:
        if not (self.repo_root / ".git").is_dir():
            return
        subprocess.run(["git", "-C", str(self.repo_root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_root), "commit", "-q", "-m", message],
            check=False,  # tolerate "nothing to commit"
        )
