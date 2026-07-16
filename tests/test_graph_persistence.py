from coscience import graph
from coscience.models import Idea, Sprint, SprintStatus, Program, ProgramStatus
from coscience.substrate import Substrate


def _sub(tmp_path):
    return Substrate(tmp_path)


def test_sprint_edges_roundtrip(tmp_path):
    sub = _sub(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1",
               edges=[graph.new_edge("builds_on", "s1", "s0", "pm", rationale="uses it")])
    sub.save_sprint(s)
    loaded = sub.load_sprint("s1")
    assert loaded.edges == s.edges


def test_sprint_without_edges_loads_empty(tmp_path):
    sub = _sub(tmp_path)
    sub.save_sprint(Sprint(id="s2", status=SprintStatus.PROPOSED, goals="g"))
    text = (sub.sprint_dir("s2") / "sprint.md").read_text()
    assert "edges" not in text                      # empty list not written
    assert sub.load_sprint("s2").edges == []


def test_idea_edges_roundtrip(tmp_path):
    sub = _sub(tmp_path)
    sub.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    ideas = [Idea(id="i1", text="a",
                  edges=[graph.new_edge("inspired_by", "i1", "i0", "human", by="u", rationale="r")]),
             Idea(id="i2", text="b")]
    sub.save_ideas("p1", "sum", ideas)
    _summary, loaded = sub.load_ideas("p1")
    assert loaded[0].edges == ideas[0].edges
    assert loaded[1].edges == []                    # no edges -> empty
