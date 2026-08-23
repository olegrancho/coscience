from coscience import wiki_okf, wiki_read


def _page(path, **kw):
    return wiki_okf.Page(path=path, type=kw.pop("type", "Concept"), **kw)


def test_trust_tier_is_derived_from_verified_not_from_status():
    # Spec 6.1: status is lifecycle, trust is derived. A stable page nobody
    # checked is still unverified — that is the whole point of separating them.
    assert wiki_read.trust_tier(_page("concepts/a.md", status="stable")) == "unverified"
    machine = _page("concepts/b.md", verified=[{"by": "coscience-wiki/claude-sonnet-5"}])
    assert wiki_read.trust_tier(machine) == "machine-confirmed"
    human = _page("concepts/c.md", verified=[{"by": "coscience-wiki/x"}, {"by": "human:oleg"}])
    assert wiki_read.trust_tier(human) == "human-reviewed"


def test_summary_counts_by_type_and_by_trust():
    pages = [_page("concepts/a.md"), _page("concepts/b.md"),
             _page("entities/c.md", type="Entity", verified=[{"by": "human:oleg"}])]
    out = wiki_read.summary(pages, state={}, pending=3, lint_counts={}, index_md="# I")
    assert out["counts"] == {"Concept": 2, "Entity": 1}
    assert out["trust"] == {"unverified": 2, "machine-confirmed": 0, "human-reviewed": 1}
    assert out["pending"] == 3
    assert out["index_md"] == "# I"


def test_summary_carries_run_state_through_verbatim():
    state = {"last_run": {"id": "r0001", "status": "ok"}, "run": {"id": "r0002"},
             "ingests_since_lint": 2, "quarantined": ["result:r9"]}
    out = wiki_read.summary([], state, pending=0, lint_counts={"error": 1, "warn": 4},
                            index_md="")
    assert out["last_run"] == {"id": "r0001", "status": "ok"}
    assert out["run"] == {"id": "r0002"}
    assert out["ingests_since_lint"] == 2
    assert out["quarantined"] == ["result:r9"]
    assert out["lint"] == {"error": 1, "warn": 4}


def test_summary_of_an_empty_bundle_is_all_zeros_not_an_error():
    out = wiki_read.summary([], {}, 0, {}, "")
    assert out["counts"] == {}
    assert out["trust"] == {"unverified": 0, "machine-confirmed": 0, "human-reviewed": 0}
    assert out["run"] is None and out["last_run"] is None


def test_summary_tolerates_an_unknown_page_type():
    # OKF requires consumers to tolerate types we did not specify.
    out = wiki_read.summary([_page("x/y.md", type="Protocol")], {}, 0, {}, "")
    assert out["counts"] == {"Protocol": 1}
