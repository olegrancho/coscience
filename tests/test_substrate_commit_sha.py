from __future__ import annotations

import subprocess

from coscience.substrate import Substrate


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)
    return Substrate(tmp_path)


def test_commit_returns_the_sha_it_created(tmp_path):
    s = _git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    sha = s.commit("first")
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert sha and sha == head


def test_commit_returns_empty_when_there_was_nothing_to_commit(tmp_path):
    """Returning the previous HEAD here would name an unrelated commit as the one
    that performed this operation — worse than admitting nothing happened."""
    s = _git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    first = s.commit("first")
    assert s.commit("nothing changed") == ""
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == first          # HEAD did not move


def test_commit_returns_empty_outside_a_git_repo(tmp_path):
    assert Substrate(tmp_path).commit("no repo here") == ""
