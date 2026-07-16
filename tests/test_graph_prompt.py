from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import gather_context
from coscience.pm_claude import render_prompt
from coscience.substrate import Substrate


def _sub(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="P", goals="cure", status=ProgramStatus.ACTIVE))
    return sub


def test_gather_context_builds_windowed_graph_lines(tmp_path):
    sub = _sub(tmp_path)
    sub.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="base", program="p1"))
    sub.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="next", program="p1",
                           edges=[graph.new_edge("builds_on", "s2", "s1", "pm", rationale="r")]))
    ctx = gather_context(sub, "p1")
    assert "s2: builds_on s1" in ctx.graph_lines


def test_render_prompt_shows_graph_and_edge_ops_schema():
    from coscience.pm_reasoner import PMContext
    ctx = PMContext(program_id="p1", goals="g", cycle=0,
                    graph_lines=["s2: builds_on s1"])
    prompt = render_prompt(ctx)
    assert "s2: builds_on s1" in prompt
    assert "edge_ops" in prompt                       # PM is told it can emit edges
    assert "builds_on" in prompt                       # vocabulary surfaced
