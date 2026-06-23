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
