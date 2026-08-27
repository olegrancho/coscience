from __future__ import annotations

from coscience import wiki_store
from coscience.models import Program
from coscience.service import Service


def _seed(substrate, days=()):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    wiki_store.ensure_bundle(substrate, "p1")
    d = wiki_store.state_dir(substrate, "p1") / "lint"
    d.mkdir(parents=True, exist_ok=True)
    for day in days:
        (d / f"{day}.md").write_text(f"# lint {day}\n")
    return Service(substrate.repo_root)


def test_filed_reports_come_back_newest_first(substrate):
    out = _seed(substrate, ["2026-08-20", "2026-08-27", "2026-08-24"]).wiki_lint_report("p1")
    assert [r["date"] for r in out["reports"]] == ["2026-08-27", "2026-08-24", "2026-08-20"]
    assert "# lint 2026-08-27" in out["reports"][0]["text"]


def test_a_bundle_with_no_reports_returns_an_empty_list(substrate):
    assert _seed(substrate).wiki_lint_report("p1")["reports"] == []


def test_live_findings_are_still_served(substrate):
    """The header badge reads counts. Adding reports must not move it."""
    out = _seed(substrate, ["2026-08-27"]).wiki_lint_report("p1")
    assert "counts" in out and "findings" in out


def test_a_non_markdown_file_in_the_lint_directory_is_ignored(substrate):
    svc = _seed(substrate, ["2026-08-27"])
    (wiki_store.state_dir(substrate, "p1") / "lint" / "notes.txt").write_text("x")
    assert [r["date"] for r in svc.wiki_lint_report("p1")["reports"]] == ["2026-08-27"]
