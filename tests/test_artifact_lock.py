from coscience import artifacts


def _mk(substrate, aid):
    artifacts.create_artifact(substrate, "p", aid, aid.title(), "md")


def test_acquire_sets_lock_and_seeds_work(substrate):
    _mk(substrate, "doc")
    ok = artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    assert ok is True
    a = substrate.load_artifact("p", "doc")
    assert a.lock["holder_id"] == "chat:x"
    assert a.lock["last_activity"] == 1.0
    assert (substrate.artifact_dir("p", "doc") / "work").is_dir()


def test_acquire_rejected_when_held_by_other(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    ok = artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", now=2.0)
    assert ok is False
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == "chat:x"


def test_acquire_same_holder_is_idempotent(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", now=1.0)
    ok = artifacts.acquire_lock(substrate, "p", ["doc"], "sprint", "s1", now=2.0)
    assert ok is True


def test_multi_acquire_is_atomic_all_or_none(substrate):
    _mk(substrate, "a")
    _mk(substrate, "b")
    artifacts.acquire_lock(substrate, "p", ["b"], "chat", "chat:x", now=1.0)  # b busy
    ok = artifacts.acquire_lock(substrate, "p", ["a", "b"], "sprint", "s1", now=2.0)
    assert ok is False
    # a must NOT have been locked (all-or-none)
    assert substrate.load_artifact("p", "a").lock == {}
    assert substrate.load_artifact("p", "b").lock["holder_id"] == "chat:x"


def test_release_cuts_version_and_clears_lock(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("edited")
    vids = artifacts.release_lock(substrate, "p", ["doc"], now=2.0, created_by="chat:x")
    assert vids == ["v1"]
    a = substrate.load_artifact("p", "doc")
    assert a.lock == {}
    assert a.current == "v1"
    assert not (substrate.artifact_dir("p", "doc") / "work").exists()


def test_release_dedup_cuts_no_version(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)  # empty work
    vids = artifacts.release_lock(substrate, "p", ["doc"], now=2.0, created_by="chat:x")
    assert vids == [None]
    assert substrate.load_artifact("p", "doc").versions == []


def test_bump_activity(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    artifacts.bump_activity(substrate, "p", "doc", now=99.0)
    assert substrate.load_artifact("p", "doc").lock["last_activity"] == 99.0


def test_reap_releases_idle_chat_lock(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=0.0)
    released = artifacts.reap_stale_chat_locks(substrate, "p", now=1800.0, timeout=1800.0)
    assert released == ["doc"]
    assert substrate.load_artifact("p", "doc").lock == {}


def test_reap_ignores_fresh_and_sprint_locks(substrate):
    _mk(substrate, "chatdoc")
    _mk(substrate, "sprintdoc")
    artifacts.acquire_lock(substrate, "p", ["chatdoc"], "chat", "chat:x", now=1000.0)
    artifacts.acquire_lock(substrate, "p", ["sprintdoc"], "sprint", "s1", now=0.0)
    released = artifacts.reap_stale_chat_locks(substrate, "p", now=1500.0, timeout=1800.0)
    assert released == []                       # chat is fresh; sprint never reaped by this


def test_reap_releases_dead_holder(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:dead", now=1000.0)
    released = artifacts.reap_stale_chat_locks(
        substrate, "p", now=1001.0, timeout=1800.0, holder_alive=lambda h: False)
    assert released == ["doc"]


def test_reacquire_same_holder_preserves_work(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("draft")
    _mk(substrate, "doc2")
    ok = artifacts.acquire_lock(substrate, "p", ["doc", "doc2"], "chat", "chat:x", now=2.0)
    assert ok is True
    # the already-held artifact's in-progress work/ is untouched, its age not reset
    assert (substrate.artifact_dir("p", "doc") / "work" / "c.md").read_text() == "draft"
    assert substrate.load_artifact("p", "doc").lock["acquired_at"] == 1.0
    # the newly added artifact is locked + seeded
    assert (substrate.artifact_dir("p", "doc2") / "work").is_dir()
    assert substrate.load_artifact("p", "doc2").lock["holder_id"] == "chat:x"


def test_release_only_by_holder(substrate):
    _mk(substrate, "doc")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    (substrate.artifact_dir("p", "doc") / "work" / "c.md").write_text("edit")
    # a DIFFERENT holder must not release it
    out = artifacts.release_lock(substrate, "p", ["doc"], now=2.0, created_by="s1")
    assert out == [None]
    assert substrate.load_artifact("p", "doc").lock["holder_id"] == "chat:x"   # still held
    # the true holder can
    out = artifacts.release_lock(substrate, "p", ["doc"], now=3.0, created_by="chat:x")
    assert out == ["v1"]
    assert substrate.load_artifact("p", "doc").lock == {}
