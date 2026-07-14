import json

from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus
from coscience import threads, feedback_harvest


def test_harvest_appends_worker_reply(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g", plan=["a"], program="p1")
    th = threads.new_thread("worker", "use fewer epochs", "u", now=1.0)
    s.threads.append(th); sub.save_sprint(s)
    d = sub.sprint_dir("s1")
    (d / "feedback.out").write_text(json.dumps({"thread_id": th["id"], "text": "done, cut to 3"}) + "\n")
    n = feedback_harvest.harvest_feedback(sub, "s1")
    assert n == 1
    got = sub.load_sprint("s1").threads[0]
    assert got["messages"][-1]["role"] == "worker" and got["agent_unseen"] is True
    # idempotent: no new lines -> 0
    assert feedback_harvest.harvest_feedback(sub, "s1") == 0
