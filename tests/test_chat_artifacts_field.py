from coscience.models import ChatThread


def test_chatthread_artifacts_default_empty():
    t = ChatThread(id="c1")
    assert t.artifacts == []


def test_chat_thread_artifacts_roundtrip(substrate):
    t = ChatThread(id="c1", title="edit fig", scope="full", artifacts=["umap"])
    substrate.save_chat_thread("p", t)
    b = substrate.load_chat_thread("p", "c1")
    assert b.artifacts == ["umap"]
    assert b.scope == "full"
