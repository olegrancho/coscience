from coscience import artifacts


def _edit(substrate, aid, text, now, by="human"):
    work = artifacts.seed_work(substrate, "p", aid)
    (work / "c.md").write_text(text)
    return artifacts.cut_version(substrate, "p", aid, by, now=now)


def test_archive_single_version(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _edit(substrate, "doc", "one", 1.0)
    artifacts.archive_version(substrate, "p", "doc", "v1")
    a = substrate.load_artifact("p", "doc")
    assert a.versions[0].archived is True
    # reversible
    artifacts.archive_version(substrate, "p", "doc", "v1", archived=False)
    assert substrate.load_artifact("p", "doc").versions[0].archived is False


def test_archive_subtree_flags_descendants(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    _edit(substrate, "doc", "one", 1.0)            # v1
    _edit(substrate, "doc", "two", 2.0)            # v2 (parent v1)
    artifacts.revert(substrate, "p", "doc", "v1")
    _edit(substrate, "doc", "three", 3.0)          # v3 (parent v1)
    _edit(substrate, "doc", "four", 4.0)           # v4 (parent v3)
    artifacts.archive_subtree(substrate, "p", "doc", "v3")
    a = substrate.load_artifact("p", "doc")
    flagged = {v.id: v.archived for v in a.versions}
    assert flagged == {"v1": False, "v2": False, "v3": True, "v4": True}


def test_archive_whole_artifact_hides_from_default_iter(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.archive_artifact(substrate, "p", "doc")
    assert [a.id for a in substrate.iter_artifacts("p")] == []
    assert [a.id for a in substrate.iter_artifacts("p", include_archived=True)] == ["doc"]
    # never hard-deleted
    assert (substrate.artifact_dir("p", "doc") / "meta.md").is_file()
