from coscience.models import DEFAULT_MODEL, Program


def test_wiki_model_defaults_to_default_model():
    p = Program(id="p1", title="T", goals="G")
    assert p.wiki_model == DEFAULT_MODEL
    assert p.wiki_enabled is True


def test_wiki_fields_round_trip(substrate):
    substrate.save_program(Program(id="p1", title="T", goals="G",
                                   wiki_model="claude-haiku-4-5-20251001",
                                   wiki_enabled=False))
    got = substrate.load_program("p1")
    assert got.wiki_model == "claude-haiku-4-5-20251001"
    assert got.wiki_enabled is False


def test_enabled_program_writes_no_wiki_enabled_key(substrate):
    substrate.save_program(Program(id="p1", title="T", goals="G"))
    text = (substrate.program_dir("p1") / "program.md").read_text()
    assert "wiki_enabled" not in text


def test_legacy_program_without_wiki_keys_defaults_enabled(substrate):
    d = substrate.program_dir("p1")
    d.mkdir(parents=True)
    (d / "program.md").write_text("---\ntype: program\ntitle: T\nstatus: active\n---\n\nG\n")
    got = substrate.load_program("p1")
    assert got.wiki_enabled is True
    assert got.wiki_model == DEFAULT_MODEL
