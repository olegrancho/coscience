from coscience.models import Program
from coscience.pm_agent import read_staging, write_staging
from coscience.pm_reasoner import PMCycleOutput


def test_artifact_tasks_survive_staging(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    out = PMCycleOutput(report="r", artifact_tasks=[
        {"suffix": "fix", "artifact_ids": ["doc"], "create": [], "instructions": "tighten"}])
    write_staging(substrate, "p", 3, out)
    staged = read_staging(substrate, "p")
    assert staged.output.artifact_tasks == [
        {"suffix": "fix", "artifact_ids": ["doc"], "create": [], "instructions": "tighten"}]
