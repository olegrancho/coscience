from coscience.frontmatter_io import parse, serialize


def test_parse_extracts_frontmatter_and_body():
    text = "---\nstatus: approved\ngoals: cure\n---\n\nhello body\n"
    fm, body = parse(text)
    assert fm == {"status": "approved", "goals": "cure"}
    assert body == "hello body\n"


def test_parse_no_frontmatter_returns_empty_dict():
    fm, body = parse("just text\n")
    assert fm == {}
    assert body == "just text\n"


def test_parse_keeps_triple_dash_inside_body():
    text = "---\na: 1\n---\n\nline\n---\nmore\n"
    fm, body = parse(text)
    assert fm == {"a": 1}
    assert "---\nmore" in body


def test_serialize_roundtrips():
    fm = {"status": "approved", "goals": "cure"}
    body = "some notes"
    out = serialize(fm, body)
    fm2, body2 = parse(out)
    assert fm2 == fm
    assert body2.strip() == "some notes"


def test_roundtrips_frontmatter_value_containing_triple_dash():
    # Regression: a chat message stored in frontmatter that contains a markdown
    # table separator (|---|---|) or a `---` rule must not be mistaken for the
    # closing fence. Previously parse() split on the bare '---' substring and
    # truncated the YAML -> ScannerError.
    reply = "Summary:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n---\n\nDone."
    fm = {"type": "chat", "messages": [{"role": "pm", "text": reply, "at": 1.0}]}
    fm2, body = parse(serialize(fm, ""))
    assert fm2["messages"][0]["text"] == reply
    assert body == ""
