from __future__ import annotations

from coscience import wiki_okf, wiki_store
from coscience.cli import main
from coscience.models import Program, Result, Sprint, SprintStatus


def _seed(substrate, pid="p1"):
    substrate.save_program(Program(id=pid, title="P", goals="g"))
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id="r1", sprint="s1", summary="s", completed_at=1.0))


def test_wiki_once_launches_a_beat(substrate, monkeypatch, capsys):
    _seed(substrate)
    import coscience.cli as cli_mod
    monkeypatch.setattr(cli_mod.wiki, "beat",
                        lambda sub, prog, now, agent, **kw: "wiki: launched ingest r0001")
    assert main(["wiki", "--repo", str(substrate.repo_root), "--once"]) == 0
    assert "launched ingest" in capsys.readouterr().out


def test_wiki_program_filter(substrate, monkeypatch):
    _seed(substrate, "p1")
    substrate.save_program(Program(id="p2", title="P2", goals="g"))
    seen = []
    import coscience.cli as cli_mod
    monkeypatch.setattr(cli_mod.wiki, "beat",
                        lambda sub, prog, now, agent, **kw: (seen.append(prog.id), "")[1])
    main(["wiki", "--repo", str(substrate.repo_root), "--once", "--program", "p2"])
    assert seen == ["p2"]


def test_wiki_lint_prints_the_report_and_exits_zero_when_clean(substrate, capsys):
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    assert main(["wiki", "--repo", str(substrate.repo_root), "--lint"]) == 0
    assert "No findings" in capsys.readouterr().out


def test_wiki_lint_exits_one_on_errors(substrate, capsys):
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="", title="A", body="x" * 400))
    assert main(["wiki", "--repo", str(substrate.repo_root), "--lint"]) == 1
    assert "okf/missing-type" in capsys.readouterr().out


def test_wiki_lint_fix_applies_mechanical_fixes(substrate, capsys):
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/b.md", type="Concept", title="B", body="x" * 400))
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="see [[b]]\n" + "x" * 400))
    main(["wiki", "--repo", str(substrate.repo_root), "--lint", "--fix"])
    text = (wiki_store.bundle_dir(substrate, "p1") / "concepts" / "a.md").read_text()
    assert "[b](/concepts/b.md)" in text
    assert "fixed 1" in capsys.readouterr().out


def test_wiki_status_lists_each_program(substrate, capsys):
    _seed(substrate)
    assert main(["wiki", "--repo", str(substrate.repo_root), "--status"]) == 0
    out = capsys.readouterr().out
    assert "p1" in out
    assert "pending 1" in out


def _ingested_source(substrate, pid="p1"):
    """A Source page carrying proof it was written from the current bytes."""
    obj = wiki_store.program_objects(substrate, pid)[0]
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path=obj.slug, type="Source", title="Sprint s1 result", graph_excluded=True,
        body="# Summary\n\n" + "x" * 400,
        extra={"origin": obj.oid, "origin_hash": wiki_store.object_hash(obj)}))
    return obj.oid


def test_wiki_reconcile_reports_without_writing(substrate, capsys):
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    oid = _ingested_source(substrate)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = [oid]

    assert main(["wiki", "--repo", str(substrate.repo_root), "--reconcile"]) == 0

    out = capsys.readouterr().out
    assert "credit 1" in out and "dry run" in out
    state = wiki_store.load_state(substrate, "p1")
    assert state["ingested"] == {} and state["quarantined"] == [oid]


def test_wiki_reconcile_apply_writes_the_state(substrate, capsys):
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    oid = _ingested_source(substrate)
    with wiki_store.state_guard(substrate, "p1") as state:
        state["quarantined"] = [oid]

    assert main(["wiki", "--repo", str(substrate.repo_root),
                 "--reconcile", "--apply"]) == 0

    state = wiki_store.load_state(substrate, "p1")
    assert oid in state["ingested"] and state["quarantined"] == []


def test_wiki_reconcile_exits_one_on_drift(substrate, capsys):
    """Drift needs a real re-ingest, so it must not look like a clean run."""
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    obj = wiki_store.program_objects(substrate, "p1")[0]
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path=obj.slug, type="Source", title="Sprint s1 result", graph_excluded=True,
        body="# Summary\n\n" + "x" * 400,
        extra={"origin": obj.oid, "origin_hash": "sha256:stale"}))

    assert main(["wiki", "--repo", str(substrate.repo_root), "--reconcile"]) == 1
    assert "drift 1" in capsys.readouterr().out
