from coscience.pm_claude import parse_response, render_prompt
from coscience.pm_reasoner import PMContext


def _ctx(**kw):
    return PMContext(program_id="p", goals="g", cycle=0, **kw)


def test_render_prompt_lists_artifacts_and_feedback():
    ctx = _ctx(
        artifacts=[{"id": "manuscript", "title": "Manuscript", "kind": "md"}],
        artifact_feedback=[{"artifact_id": "manuscript", "thread_id": "t1",
                            "messages": [{"role": "human", "text": "tighten the intro"}]}])
    p = render_prompt(ctx)
    assert "ARTIFACTS" in p
    assert "manuscript" in p
    assert "tighten the intro" in p
    assert "artifact_tasks" in p


def test_parse_reads_artifact_tasks():
    out = parse_response(
        '{"report":"r","artifact_tasks":[{"suffix":"fix-intro","artifact_ids":["manuscript"],'
        '"create":[],"instructions":"tighten the introduction per the comment"}]}')
    assert len(out.artifact_tasks) == 1
    assert out.artifact_tasks[0]["artifact_ids"] == ["manuscript"]


def test_parse_artifact_tasks_defaults_empty():
    out = parse_response('{"report":"r"}')
    assert out.artifact_tasks == []
