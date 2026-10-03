"""The planner and chat see each sprint by the title the dashboard shows, and link to
sprints by it (todo K2)."""
from coscience import chat_agent
from coscience.pm_agent import context_fingerprint
from coscience.pm_claude import (SPRINT_LINKS, render_chat_prompt, render_draft_prompt,
                                 render_prompt, sprint_name)
from coscience.pm_reasoner import PMContext


def _ctx(title_open="Pocket-water rescoring", title_done="Kernel scale-up"):
    return PMContext(
        program_id="p1", goals="g", cycle=1,
        open_sprints=[{"id": "p1-c9-water", "status": "approved", "goals": "rescore waters",
                       "title": title_open}],
        completed=[{"id": "p1-c3-kernel", "goals": "scale the kernel", "result": "0.87",
                    "title": title_done}])


def test_every_prompt_names_sprints_by_title_and_carries_the_link_rule():
    ctx = _ctx()
    for text in (render_prompt(ctx), render_chat_prompt(ctx, [], "hi"),
                 render_draft_prompt(ctx, "an idea"), chat_agent.render_preamble(ctx, "read")):
        assert 'p1-c9-water "Pocket-water rescoring"' in text
        assert 'p1-c3-kernel "Kernel scale-up"' in text
        assert SPRINT_LINKS in text
    assert "](/sprints/<sprint-id>)" in SPRINT_LINKS


def test_an_untitled_sprint_is_named_by_its_goals_as_the_dashboard_does():
    assert sprint_name({"id": "x", "goals": "rescore  the\nwaters"}) == "rescore the waters"
    long = sprint_name({"id": "x", "goals": "w " * 60})
    assert len(long) <= 80 and long.endswith("…")
    assert sprint_name({"id": "x", "goals": ""}) == "x"


def test_titles_wake_no_program():
    # A title is not an input the planner reacts to: shipping titles into the context
    # must not change any program's fingerprint and wake every planner at once.
    assert context_fingerprint(_ctx()) == context_fingerprint(_ctx("other", "other too"))
