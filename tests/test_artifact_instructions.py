from pathlib import Path

from coscience import artifacts
from coscience.claude_executor import build_instructions
from coscience.executor import ExecutionContext
from coscience.models import Sprint, SprintStatus
from coscience.worker import Worker
from tests.conftest import FakeAgent


def test_build_instructions_lists_artifact_work_paths(tmp_path):
    ctx = ExecutionContext(
        artifacts=[{"aid": "manuscript", "kind": "md",
                    "work_path": "/repo/programs/p/artifacts/manuscript/work"}])
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g")
    text = build_instructions(s, ctx, tmp_path / "scratchpad.md")
    assert "Artifacts to produce" in text
    assert "/repo/programs/p/artifacts/manuscript/work" in text
    assert "manuscript" in text


def test_build_instructions_no_section_without_artifacts(tmp_path):
    ctx = ExecutionContext()
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g")
    text = build_instructions(s, ctx, tmp_path / "scratchpad.md")
    assert "Artifacts to produce" not in text


def test_build_context_populates_artifact_work_paths(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "figure")
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["x"],
               program="p", artifacts_bound=["doc"])
    substrate.save_sprint(s)
    ctx = Worker(substrate, FakeAgent())._build_context(s)
    assert len(ctx.artifacts) == 1
    entry = ctx.artifacts[0]
    assert entry["aid"] == "doc"
    assert entry["kind"] == "figure"
    assert entry["work_path"].endswith("artifacts/doc/work")
