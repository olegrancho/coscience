from coscience import threads


def test_new_and_needs_reply():
    t = threads.new_thread("pm", "run on cpu", "stroganov", now=1.0)
    assert t["target"] == "pm" and t["status"] == "open" and t["agent_unseen"] is False
    assert t["messages"] == [{"role": "human", "text": "run on cpu", "by": "stroganov", "at": 1.0}]
    assert threads.needs_reply(t) is True


def test_agent_reply_sets_unseen_and_stops_needing():
    t = threads.new_thread("pm", "x", "u", now=1.0)
    threads.append(t, "pm", "done — set cpu", "", now=2.0)
    assert t["agent_unseen"] is True
    assert threads.needs_reply(t) is False           # last msg is agent


def test_human_append_reopens_completed():
    t = threads.new_thread("pm", "x", "u", now=1.0)
    t["status"] = "complete"
    threads.append(t, "human", "one more thing", "u", now=3.0)
    assert t["status"] == "open" and threads.needs_reply(t) is True


def test_adapt_legacy_comment():
    t = threads.adapt_legacy({"text": "old", "added_at": 5.0, "target": "worker", "by": "u"},
                             default_target="pm", now=9.0)
    assert t["target"] == "worker" and t["status"] == "open"
    assert t["messages"][0] == {"role": "human", "text": "old", "by": "u", "at": 5.0}
