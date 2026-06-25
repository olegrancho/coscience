import re

from coscience.executor import ShellStepExecutor, is_running
from coscience.models import Sprint, SprintStatus, Step
from coscience.worker import Worker


def test_launch_stores_identity_token(substrate):
    substrate.save_sprint(Sprint(
        id="J", status=SprintStatus.EXECUTING, goals="g",
        plan=[Step("job", "detached: sleep 30")]))
    worker = Worker(substrate, ShellStepExecutor())
    worker.run_sprint_beat(substrate.load_sprint("J"))  # launches the detached job

    token = substrate.load_progress("J").detached["job"]
    assert re.fullmatch(r"\d+:\d+", token)   # "<pid>:<starttime>", not a bare int
    assert is_running(token) is True

    worker.stop_sprint(substrate.load_sprint("J"))       # cleanup
    assert substrate.load_progress("J").detached == {}


def test_legacy_int_detached_value_still_readable(substrate, tmp_path):
    # Simulate an old on-disk progress file whose detached value is a bare int.
    substrate.save_sprint(Sprint(id="L", status=SprintStatus.EXECUTING, goals="g",
                                 plan=[Step("job", "detached: sleep 30")]))
    prog = substrate.load_progress("L")
    prog.detached["job"] = "999999999"   # implausible bare PID (dead) — legacy shape
    substrate.save_progress(prog)
    # Reloads as a string and is_running treats it as plain liveness (dead -> False).
    reloaded = substrate.load_progress("L")
    assert reloaded.detached["job"] == "999999999"
    assert is_running(reloaded.detached["job"]) is False
