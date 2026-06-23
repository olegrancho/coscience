import os
import stat

from coscience.claude_executor import ClaudeCodeExecutor
from coscience.models import Step


def test_build_command_uses_prompt_flag():
    cmd = ClaudeCodeExecutor(claude_bin="claude").build_command(Step("s1", "say hi"))
    assert cmd == ["claude", "-p", "say hi", "--output-format", "text"]


def test_run_invokes_fake_claude(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/usr/bin/env bash\necho \"AGENT:$2\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    r = ClaudeCodeExecutor(claude_bin=str(fake)).run(Step("s1", "do research"))
    assert r.completed is True
    assert "AGENT:do research" in r.output
