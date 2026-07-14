from pathlib import Path

from coscience.claude_executor import build_instructions
from coscience.executor import ExecutionContext
from coscience.models import Sprint, SprintStatus


def _sprint():
    return Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"])


def test_protocol_documented_and_max_seconds():
    txt = build_instructions(_sprint(), ExecutionContext(), Path("/tmp/s1/scratchpad.md"))
    assert "job.json" in txt and "wake_after_seconds" in txt and "expected_seconds" in txt


def test_assess_section_when_reason_set():
    ctx = ExecutionContext(assess_reason="finished", job_out="j.out", job_note="train")
    txt = build_instructions(_sprint(), ctx, Path("/tmp/s1/scratchpad.md"))
    assert "j.out" in txt and ("finished" in txt or "assess" in txt.lower())
