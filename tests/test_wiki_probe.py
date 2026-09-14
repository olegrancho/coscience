"""L2/L3: the wiki probe answers, debriefs, and keeps a readable trace."""
import json

from coscience import wiki_probe


def test_questions_are_the_top_level_list_items_with_continuations():
    text = """# p2 questions

Some note that is not a question.

- Why did waters not help?
- Which terms are ruled out?
  Include partial ones.
1. What is the best result so far?
"""
    assert wiki_probe.parse_questions(text) == [
        "Why did waters not help?",
        "Which terms are ruled out? Include partial ones.",
        "What is the best result so far?",
    ]


def _stream(bundle, session="s-1", answer="It is X [concepts/a.md].", cost=0.4):
    ev = [
        {"type": "system", "subtype": "init", "session_id": session, "model": "claude-sonnet-5"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Looking for the index."},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": str(bundle / "index.md")}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 2048}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "Grep", "input": {"pattern": "waters", "path": "concepts"}},
            {"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "/elsewhere/results/r1.md"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t3", "content": [{"type": "text", "text": "raw"}]}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}},
        {"type": "result", "result": answer, "session_id": session, "total_cost_usd": cost, "num_turns": 3},
    ]
    return "\n".join(json.dumps(e) for e in ev)


def test_the_trace_keeps_reads_in_order_with_size_and_flags_leaving_the_wiki(tmp_path):
    t = wiki_probe.trace(_stream(tmp_path), tmp_path)
    assert [(r["path"], r["inside_wiki"]) for r in t["reads"]] == [
        ("index.md", True), ("/elsewhere/results/r1.md", False)]
    assert t["reads"][0]["bytes"] == 2048
    assert t["searches"] == [{"tool": "Grep", "pattern": "waters", "path": "concepts"}]
    assert t["thinking"] == ["Looking for the index."]          # the answer is not repeated
    assert t["answer"] == "It is X [concepts/a.md]." and t["cost"] == 0.4
    assert t["session_id"] == "s-1"


def test_a_probe_answers_then_debriefs_in_the_same_session_and_writes_a_report(tmp_path):
    bundle, out = tmp_path / "wiki", tmp_path / "out"
    bundle.mkdir()
    calls = []

    def fake(args, prompt, cwd):
        calls.append((args, prompt, cwd))
        if "--resume" in args:
            return _stream(bundle, answer="I expected a page on waters.", cost=0.05)
        return _stream(bundle)

    summary = wiki_probe.probe("p2", ["Why did waters not help?"], bundle, out, model="claude-sonnet-5",
                               invoke=fake)

    (answer_call, debrief_call) = calls
    assert "Why did waters not help?" in answer_call[1] and answer_call[2] == str(bundle)
    assert "--tools" in answer_call[0] and "Read,Glob,Grep" in answer_call[0]
    assert debrief_call[0][-2:] == ["--resume", "s-1"] and debrief_call[1] == wiki_probe.DEBRIEF_PROMPT
    assert summary["questions"][0]["debrief"]["text"] == "I expected a page on waters."
    report = (out / "report.md").read_text()
    assert "## Q1. Why did waters not help?" in report
    assert "1. `index.md` (2 KB)" in report
    assert "**outside the wiki**" in report
    assert "I expected a page on waters." in report
    assert "total $0.45" in report
    assert (out / "q01.answer.jsonl").is_file() and (out / "q01.debrief.jsonl").is_file()
