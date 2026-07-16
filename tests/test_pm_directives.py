"""Directed PM cycles: Compress (merge/prune/re-rank — only pinned spared) and
Brainstorm (add ideas). Covers the apply-time protection relaxation, re-ranking,
staging round-trip, and the HTTP route guards."""
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Idea, Program, ProgramStatus
from coscience.pm_agent import pm_beat, read_staging, write_staging
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def _seed_pool(svc):
    ideas = [
        Idea(id="H", text="human idea", source="human"),
        Idea(id="P", text="plain pm idea", source="pm"),
        Idea(id="PIN", text="pinned pm idea", source="pm", pinned=True),
        Idea(id="DEM", text="demoted pm idea", source="pm", demoted=True),
    ]
    svc.substrate.save_ideas("p1", "seed", ideas)


def test_compress_prunes_all_but_pinned_and_reranks(tmp_path):
    svc = _svc(tmp_path); _seed_pool(svc)
    out = PMCycleOutput(delete_idea_ids=["H", "P", "DEM", "PIN"],  # PIN must be ignored
                        new_ideas=["merged direction"], idea_order=["PIN"])
    reasoner = FakeReasoner([out])
    result = pm_beat(svc.substrate, "p1", reasoner, force=True, directive="compress")
    assert result["ideas_removed"] == 3 and result["ideas_added"] == 1   # H,P,DEM pruned; merged added

    _summary, ideas = svc.substrate.load_ideas("p1")
    ids = [i.id for i in ideas]
    assert "PIN" in ids                       # pinned survived
    assert "H" not in ids and "P" not in ids and "DEM" not in ids   # everything else prunable
    assert any(i.text == "merged direction" for i in ideas)          # merged idea added
    assert ids[0] == "PIN"                     # re-ranked: idea_order first
    assert reasoner.calls[0].directive == "compress"   # directive reached the reasoner


def test_brainstorm_adds_and_keeps_protection(tmp_path):
    svc = _svc(tmp_path); _seed_pool(svc)
    # Even if the reasoner returns deletes, a non-compress cycle keeps the normal
    # gate: the human idea must NOT be pruned.
    out = PMCycleOutput(delete_idea_ids=["PIN"], new_ideas=["fresh a", "fresh b"])
    result = pm_beat(svc.substrate, "p1", FakeReasoner([out]), force=True, directive="brainstorm")
    assert result["ideas_added"] == 2          # truthful count for the UI

    _summary, ideas = svc.substrate.load_ideas("p1")
    ids = {i.id for i in ideas}
    texts = {i.text for i in ideas}
    assert "PIN" in ids                        # pinned protection held
    assert "fresh a" in texts and "fresh b" in texts


def test_plain_cycle_prunes_unpinned_keeps_pinned(tmp_path):
    svc = _svc(tmp_path); _seed_pool(svc)
    out = PMCycleOutput(delete_idea_ids=["PIN", "P", "H"])
    pm_beat(svc.substrate, "p1", FakeReasoner([out]), force=True)   # no directive

    ids = {i.id for i in svc.substrate.load_ideas("p1")[1]}
    assert "PIN" in ids                        # pinned protected (same rule as compress now)
    assert "P" not in ids and "H" not in ids   # any non-pinned idea is prunable


def test_staging_roundtrips_directive_and_order(tmp_path):
    svc = _svc(tmp_path)
    out = PMCycleOutput(new_ideas=["x"], idea_order=["a", "b"])
    write_staging(svc.substrate, "p1", 3, out, "fp", "compress")
    staged = read_staging(svc.substrate, "p1")
    assert staged.directive == "compress"
    assert staged.output.idea_order == ["a", "b"]


def test_route_rejects_unknown_mode(tmp_path):
    svc = _svc(tmp_path)
    c = TestClient(build_app(svc))
    assert c.post("/api/programs/p1/ideas/bogus").status_code == 404


def test_directive_on_missing_program_is_404(tmp_path):
    svc = _svc(tmp_path)
    c = TestClient(build_app(svc))
    assert c.post("/api/programs/nope/ideas/compress").status_code == 404
