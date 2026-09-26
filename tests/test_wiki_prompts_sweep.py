"""L9: the sweep's instructions name what the mechanical lint cannot see."""
from pathlib import Path

from coscience import wiki_prompts
from coscience.models import Program


def test_the_sweep_asks_for_the_cross_page_defects_and_a_report(tmp_path):
    text = wiki_prompts.render_sweep(Program(id="p9", title="T", goals="g"), tmp_path / "wiki",
                                     tmp_path / "run", results_dir=Path("/r"))
    for phrase in ("Stale current claims", "One quantity, two values",
                   "do not match their source", "sweep-report.md", "`/r`"):
        assert phrase in text
