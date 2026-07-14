from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus
from coscience import threads


def test_threads_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"])
    s.threads.append(threads.new_thread("pm", "hi", "u", now=1.0))
    sub.save_sprint(s)
    got = sub.load_sprint("s1")
    assert len(got.threads) == 1 and got.threads[0]["messages"][0]["text"] == "hi"


def test_legacy_comments_adapt_to_threads(tmp_path):
    sub = Substrate(tmp_path)
    # write a sprint.md with the OLD comments shape, no threads key
    d = sub.sprint_dir("s2"); d.mkdir(parents=True, exist_ok=True)
    (d / "sprint.md").write_text(
        "---\nstatus: proposed\ngoals: g\nplan: [a]\n"
        "comments:\n  - id: c1\n    text: legacy note\n    added_at: 5.0\n    target: pm\n"
        "---\n# s2\n")
    got = sub.load_sprint("s2")
    assert len(got.threads) == 1
    assert got.threads[0]["target"] == "pm"
    assert got.threads[0]["messages"][0]["text"] == "legacy note"
