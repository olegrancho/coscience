"""Who moved a sprint (P3). The program page highlights an experiment whose status
changed since the viewer last looked — which fired on the viewer's own approvals and
parks, announcing their own click back at them. Only a transition nobody asked for
deserves the highlight, so every entry has to say which kind it was."""
from coscience.models import Sprint, SprintStatus, set_status, status_actor


def _last(**kw) -> dict:
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g")
    set_status(s, kw.pop("to", SprintStatus.QUEUED), **kw)
    return s.status_history[-1]


def test_the_pm_is_the_pm_even_on_a_verb_a_human_also_uses():
    assert status_actor(_last(by="pm", action="run")) == "pm"


def test_the_pm_reopening_a_sprint_is_the_pm():
    assert status_actor(_last(to=SprintStatus.PROPOSED, by="pm", action="reopen")) == "pm"


def test_a_human_running_an_approved_sprint_is_a_human():
    assert status_actor(_last(by="olegs", action="run")) == "human"


def test_a_human_is_recognised_with_no_username_at_all():
    """The dashboard has no login, so every human action records by="". The verb is
    the only thing that separates them from the platform."""
    assert status_actor(_last(by="", action="approve")) == "human"


def test_every_human_verb_reads_as_human():
    for action in ("approve", "run", "send_back", "reject", "park", "unpark",
                   "cancel", "resume", "demote", "restore", "reallocate", "stop",
                   "to_human"):
        assert status_actor({"by": "", "action": action}) == "human", action


def test_a_system_transition_is_the_platform():
    assert status_actor(_last(to=SprintStatus.EXECUTING)) == "platform"


def test_the_worker_finishing_is_the_platform():
    assert status_actor(_last(to=SprintStatus.DONE)) == "platform"


def test_the_worker_failing_is_the_platform():
    assert status_actor(_last(to=SprintStatus.FAILED)) == "platform"


def test_the_dispatcher_hibernating_is_the_platform():
    assert status_actor(_last(to=SprintStatus.HIBERNATED, by="dispatcher",
                              action="hibernate")) == "platform"


def test_an_agent_raising_the_red_button_is_the_platform():
    """An escalation is exactly what a human has to be told about."""
    assert status_actor(_last(to=SprintStatus.ESCALATED, by="agent",
                              action="escalate")) == "platform"


def test_the_dispatcher_raising_one_is_the_platform_too():
    assert status_actor(_last(to=SprintStatus.ESCALATED, by="dispatcher",
                              action="escalate")) == "platform"


def test_a_pm_answering_an_escalation_is_the_pm():
    assert status_actor(_last(to=SprintStatus.EXECUTING, by="pm",
                              action="reallocate")) == "pm"


def test_a_human_answering_an_escalation_is_a_human():
    assert status_actor(_last(to=SprintStatus.EXECUTING, by="olegs",
                              action="reallocate")) == "human"


def test_an_entry_missing_its_fields_reads_as_the_platform():
    """Legacy history, written before `by`/`action` were recorded."""
    assert status_actor({}) == "platform"
    assert status_actor({"by": None, "action": None}) == "platform"
