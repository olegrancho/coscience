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


def _client(substrate):
    from fastapi.testclient import TestClient
    from coscience.http_api import build_app
    from coscience.service import Service
    substrate.save_program(Program(id="p1", title="T", goals="G"))
    return TestClient(build_app(Service(substrate.repo_root)))


def test_the_program_payload_exposes_both_wiki_controls(substrate):
    body = _client(substrate).get("/api/programs/p1").json()
    assert body["wiki_model"] == DEFAULT_MODEL
    assert body["wiki_enabled"] is True


def test_setting_the_wiki_model_leaves_the_planner_model_alone(substrate):
    # The whole point of the separate control: planning and writing are different
    # jobs and must be dialled independently.
    c = _client(substrate)
    c.post("/api/programs/p1/model", json={"model": "claude-opus-5"})
    r = c.post("/api/programs/p1/wiki-model", json={"model": "claude-haiku-4-5-20251001"})
    assert r.status_code == 200
    body = c.get("/api/programs/p1").json()
    assert body["wiki_model"] == "claude-haiku-4-5-20251001"
    assert body["pm_model"] == "claude-opus-5"


def test_an_empty_wiki_model_falls_back_to_the_default(substrate):
    c = _client(substrate)
    c.post("/api/programs/p1/wiki-model", json={"model": ""})
    assert c.get("/api/programs/p1").json()["wiki_model"] == DEFAULT_MODEL


def test_wiki_enabled_round_trips_through_the_api(substrate):
    c = _client(substrate)
    assert c.post("/api/programs/p1/wiki-enabled", json={"enabled": False}).status_code == 200
    assert c.get("/api/programs/p1").json()["wiki_enabled"] is False
    c.post("/api/programs/p1/wiki-enabled", json={"enabled": True})
    assert c.get("/api/programs/p1").json()["wiki_enabled"] is True


def test_the_wiki_controls_404_on_an_unknown_program(substrate):
    c = _client(substrate)
    assert c.post("/api/programs/nope/wiki-model", json={"model": "x"}).status_code == 404
    assert c.post("/api/programs/nope/wiki-enabled", json={"enabled": False}).status_code == 404
