from coscience import artifacts, threads
from coscience.models import Program
from coscience.pm_agent import context_fingerprint, gather_context


def _program(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))


def test_gather_lists_artifacts(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "figure")
    ctx = gather_context(substrate, "p")
    assert ctx.artifacts == [{"id": "doc", "title": "Doc", "kind": "figure"}]


def test_open_artifact_comment_is_feedback_and_retriggers(substrate):
    _program(substrate)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    before = context_fingerprint(gather_context(substrate, "p"))
    # add a human comment thread (target pm) on the artifact
    a = substrate.load_artifact("p", "doc")
    a.threads.append(threads.new_thread("pm", "please tighten", by="oleg", now=1.0))
    substrate.save_artifact(a)
    ctx = gather_context(substrate, "p")
    assert len(ctx.artifact_feedback) == 1
    assert ctx.artifact_feedback[0]["artifact_id"] == "doc"
    assert context_fingerprint(ctx) != before          # a new comment re-triggers the PM
