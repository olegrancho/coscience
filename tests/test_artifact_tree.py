from coscience import artifacts


def _one_edit(substrate, aid, text, by, now):
    work = artifacts.seed_work(substrate, "p", aid)
    (work / "c.md").write_text(text)
    return artifacts.cut_version(substrate, "p", aid, by, now=now)


def test_revert_moves_current_without_new_version(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _one_edit(substrate, "doc", "one", "human", 1.0)   # v1
    _one_edit(substrate, "doc", "two", "human", 2.0)   # v2
    artifacts.revert(substrate, "p", "doc", "v1")
    a = substrate.load_artifact("p", "doc")
    assert a.current == "v1"
    assert [v.id for v in a.versions] == ["v1", "v2"]   # nothing deleted or added


def test_edit_after_revert_branches_from_reverted_node(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _one_edit(substrate, "doc", "one", "human", 1.0)   # v1
    _one_edit(substrate, "doc", "two", "human", 2.0)   # v2 (parent v1)
    artifacts.revert(substrate, "p", "doc", "v1")
    vid = _one_edit(substrate, "doc", "three", "chat:x", 3.0)   # branch off v1
    assert vid == "v3"
    a = substrate.load_artifact("p", "doc")
    assert a.versions[2].parent == "v1"                # v3 is a sibling of v2
    assert a.current == "v3"


def test_revert_unknown_version_raises(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _one_edit(substrate, "doc", "one", "human", 1.0)
    try:
        artifacts.revert(substrate, "p", "doc", "v9")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_children_and_is_leaf(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _one_edit(substrate, "doc", "one", "human", 1.0)   # v1
    _one_edit(substrate, "doc", "two", "human", 2.0)   # v2 (parent v1)
    artifacts.revert(substrate, "p", "doc", "v1")
    _one_edit(substrate, "doc", "three", "human", 3.0)  # v3 (parent v1)
    a = substrate.load_artifact("p", "doc")
    assert sorted(artifacts.children(a, "v1")) == ["v2", "v3"]
    assert artifacts.is_leaf(a, "v2") is True
    assert artifacts.is_leaf(a, "v1") is False
