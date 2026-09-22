"""A substrate that cannot commit must not look like one with nothing to commit (B4).

On 2026-09-20 the real repo refused every commit for twelve hours in silence, because
`Substrate.commit` returned "" for both outcomes and every caller read "" as "nothing to
commit".
"""
import subprocess

import pytest

from coscience import commit_health
from coscience.substrate import Substrate


@pytest.fixture
def repo(tmp_path):
    """A substrate that is a real git repo, so commit() takes its real path."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)
    return Substrate(tmp_path)


def test_a_commit_that_lands_says_nothing_is_wrong(repo):
    (repo.repo_root / "a.md").write_text("one")
    assert repo.commit("first")
    assert commit_health.read(repo.repo_root) is None


def test_nothing_to_commit_is_not_a_failure(repo):
    """The distinction the outage turned on: an empty commit is the normal case."""
    (repo.repo_root / "a.md").write_text("one")
    repo.commit("first")
    assert repo.commit("again") == ""
    assert commit_health.read(repo.repo_root) is None


def test_a_refused_commit_is_recorded_though_the_caller_still_sees_nothing(repo):
    # Refuse the commit the way the outage did — from git's own side, with content
    # staged and waiting. The caller's "" is unchanged; the platform now knows.
    (repo.repo_root / "a.md").write_text("one")
    hook = repo.repo_root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'object file is empty' >&2\nexit 1\n")
    hook.chmod(0o755)

    assert repo.commit("first") == ""
    state = commit_health.read(repo.repo_root)
    assert state["count"] == 1
    assert "object file is empty" in state["reason"]


def test_a_recovered_repo_clears_the_record(repo):
    (repo.repo_root / "a.md").write_text("one")
    hook = repo.repo_root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    repo.commit("blocked")
    assert commit_health.read(repo.repo_root)["count"] == 1

    hook.unlink()
    assert repo.commit("unblocked")
    assert commit_health.read(repo.repo_root) is None


def test_failures_accumulate_and_keep_the_start_of_the_run(repo):
    first = commit_health.record_failure(repo.repo_root, "one", now=1000.0)
    second = commit_health.record_failure(repo.repo_root, "two", now=1600.0)
    assert (second["count"], second["since"]) == (2, 1000.0)
    assert second["reason"] == "two"          # the latest reason, not the first
    assert first["since"] == 1000.0


def test_one_failure_stays_off_the_dashboard(repo):
    """A lost race or a momentary lock is not an outage; a repo that has stopped is."""
    commit_health.record_failure(repo.repo_root, "transient", now=1000.0)
    assert commit_health.describe(commit_health.read(repo.repo_root)) == ""
    for _ in range(commit_health.REPORT_AFTER - 1):
        commit_health.record_failure(repo.repo_root, "still broken")
    line = commit_health.describe(commit_health.read(repo.repo_root))
    assert "has not committed" in line and "still broken" in line


def test_an_unreadable_record_reads_as_healthy(repo):
    """A broken warning channel must not become an error of its own."""
    path = commit_health._path(repo.repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert commit_health.read(repo.repo_root) is None
    assert commit_health.describe(None) == ""


def test_a_failing_add_raises_and_is_recorded(repo, monkeypatch):
    """The shape the outage actually took: `add` fails, so nothing is ever staged.
    It raised before this change too — but silently, as far as any watcher knew."""
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if isinstance(cmd, list) and "add" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "error: invalid object")
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="git add failed"):
        repo.commit("doomed")
    assert "invalid object" in commit_health.read(repo.repo_root)["reason"]
