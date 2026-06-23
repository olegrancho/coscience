from coscience.executor import ShellStepExecutor
from coscience.models import Step


def test_successful_command_is_completed():
    r = ShellStepExecutor().run(Step("s1", "echo hello"))
    assert r.step_id == "s1"
    assert r.completed is True
    assert "hello" in r.output


def test_failing_command_is_not_completed():
    r = ShellStepExecutor().run(Step("s2", "exit 3"))
    assert r.completed is False
