from coscience.models import Program, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_claude import ClaudeCodeReasoner

# A realistic model reply: prose around a fenced JSON block.
TRANSCRIPT = """Looking at the program, the next experiment should test dosage.

```json
{"report": "## Status\\nOne assay done; proposing a dose-response follow-up.",
 "proposals": [
   {"suffix": "dose-response", "goals": "Run a dose-response assay",
    "plan": [{"id": "run", "run": "echo dose-response"}],
    "priority": 2, "resources_required": {"gpu": 1}, "rationale": "highest value next"}]}
```
That is my recommendation.
"""


def test_canned_transcript_flows_through_pm_beat(substrate):
    substrate.save_program(Program(id="p1", title="C", goals="cure cancer"))
    reasoner = ClaudeCodeReasoner(invoke=lambda prompt: TRANSCRIPT)

    summary = pm_beat(substrate, "p1", reasoner)

    sid = "p1-c0-dose-response"
    assert summary["submitted"] == [sid]
    sprint = substrate.load_sprint(sid)
    assert sprint.status == SprintStatus.PROPOSED       # propose-only
    assert sprint.program == "p1"
    assert sprint.priority == 2
    assert sprint.resources_required == {"gpu": 1.0}
    assert "dose-response" in substrate.load_report("p1")
