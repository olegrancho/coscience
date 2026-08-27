import pytest

from coscience import wiki_store


def test_load_state_defaults_when_absent(wiki_bundle):
    substrate, pid = wiki_bundle
    state = wiki_store.load_state(substrate, pid)
    assert state == {"ingested": {}, "ingests_since_lint": 0, "run": None,
                     "last_run": None, "failures": 0, "quarantined": [],
                     "merge_proposals": [], "merges_refused": [], "runs": []}


def test_save_then_load_round_trips(wiki_bundle):
    substrate, pid = wiki_bundle
    state = wiki_store.load_state(substrate, pid)
    state["ingested"]["result:r1"] = {"hash": "sha256:aa", "at": 1.0, "run": "r0001"}
    state["ingests_since_lint"] = 2
    wiki_store.save_state(substrate, pid, state)
    assert (wiki_store.state_dir(substrate, pid) / "state.json").is_file()
    got = wiki_store.load_state(substrate, pid)
    assert got["ingested"]["result:r1"]["hash"] == "sha256:aa"
    assert got["ingests_since_lint"] == 2


def test_load_state_survives_corruption(wiki_bundle):
    substrate, pid = wiki_bundle
    (wiki_store.state_dir(substrate, pid) / "state.json").write_text("{not json")
    assert wiki_store.load_state(substrate, pid)["ingested"] == {}


def test_partial_state_file_is_merged_onto_defaults(wiki_bundle):
    substrate, pid = wiki_bundle
    (wiki_store.state_dir(substrate, pid) / "state.json").write_text('{"failures": 2}')
    state = wiki_store.load_state(substrate, pid)
    assert state["failures"] == 2
    assert state["quarantined"] == []
    assert state["run"] is None


def test_state_guard_saves_on_clean_exit(wiki_bundle):
    substrate, pid = wiki_bundle
    with wiki_store.state_guard(substrate, pid) as state:
        state["failures"] = 3
    assert wiki_store.load_state(substrate, pid)["failures"] == 3


def test_state_guard_does_not_save_on_exception(wiki_bundle):
    substrate, pid = wiki_bundle
    with pytest.raises(RuntimeError):
        with wiki_store.state_guard(substrate, pid) as state:
            state["failures"] = 9
            raise RuntimeError("boom")
    assert wiki_store.load_state(substrate, pid)["failures"] == 0


def test_lock_file_lives_in_dot_coscience(wiki_bundle):
    substrate, pid = wiki_bundle
    with wiki_store.state_guard(substrate, pid):
        pass
    assert (substrate.repo_root / ".coscience" / "wiki.lock").exists()
