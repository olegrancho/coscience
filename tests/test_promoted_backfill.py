"""K4 backfill: recover which idea a past sprint was promoted from, from git history."""
import subprocess

from coscience import promoted_backfill as pb
from coscience.models import Idea, Program, Sprint, SprintStatus
from coscience.substrate import Substrate


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return Substrate(tmp_path)


def _commit(root, msg):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


def _sprint(sid, title, goals):
    return Sprint(id=sid, status=SprintStatus.PROPOSED, goals=goals, title=title,
                  plan=["x"], program="p1")


def test_a_planner_promotion_is_recovered_and_a_prune_is_not(tmp_path):
    s = _repo(tmp_path)
    s.save_program(Program(id="p1", title="P", goals="g"))
    s.save_ideas("p1", "", [
        Idea(id="i-net", text="**A network that reads every pose of one docking run together.**"),
        Idea(id="i-old", text="Measure solvent exposure of buried waters near the pocket."),
    ])
    _commit(tmp_path, "pool")
    # One cycle: promotes i-net, prunes i-old, and proposes something unrelated.
    s.save_ideas("p1", "", [])
    s.save_sprint(_sprint("p1-c4-run-context", "Network that reads every pose of a docking run",
                          "Train a network that reads all poses of one docking run together."))
    s.save_sprint(_sprint("p1-c4-figure", "Refresh the scaling figure", "Redraw the figure."))
    _commit(tmp_path, "pm cycle")
    # A person's promotion, recorded in its commit message.
    s.save_sprint(_sprint("p1-human", "Anything", "whatever"))
    _commit(tmp_path, "program p1: idea i-h promoted to sprint p1-human")

    rows = pb.plan(tmp_path, s)
    assert sorted((sid, idea) for sid, idea, _ in rows) == [
        ("p1-c4-run-context", "i-net"), ("p1-human", "i-h")]

    assert pb.main(["--repo", str(tmp_path), "--apply"]) == 0
    assert s.load_sprint("p1-c4-run-context").from_idea == "i-net"
    assert pb.plan(tmp_path, s) == []          # recorded once, never changed again
