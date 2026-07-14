from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus


def test_sprint_decisions_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"])
    s.decisions.append({"by": "stroganov", "action": "approve", "at": 1.0})
    sub.save_sprint(s)
    got = sub.load_sprint("s1")
    assert got.decisions == [{"by": "stroganov", "action": "approve", "at": 1.0}]


def test_sprint_defaults_empty_decisions(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_sprint(Sprint(id="s2", status=SprintStatus.PROPOSED, goals="g", plan=["a"]))
    assert sub.load_sprint("s2").decisions == []


def test_idea_by_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    from coscience.models import Idea
    sub.save_ideas("p1", "", [Idea(id="i1", text="t", source="human", by="apathak")])
    _summary, ideas = sub.load_ideas("p1")
    assert ideas[0].by == "apathak"
