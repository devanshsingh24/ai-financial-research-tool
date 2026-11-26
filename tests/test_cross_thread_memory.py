"""Long-term memory: written at the end of a run, recalled at the start of another thread."""

from conftest import user

from backend import config

ALICE = user("alice")


def test_memories_recalled_in_new_thread(brain, server, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_SEARCH_LIMIT", 2)
    with server() as c:
        t1 = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        r = c.post(f"/threads/{t1}/messages", headers=ALICE, json={
            "question": "Track NVIDIA for me. I prefer short bullet answers. What drove NVIDIA revenue?"})
        assert r.json()["status"] == "answered"
        t2 = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        c.post(f"/threads/{t2}/messages", headers=ALICE,
               json={"question": "Is Tesla delivery growth slowing?"})
    assert brain.count("extract") == 2

    # restart: memories live in the store, not in any thread's state
    with server() as c:
        t3 = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        c.post(f"/threads/{t3}/messages", headers=ALICE,
               json={"question": "How are NVIDIA margins trending?"})
        memories = c.get(f"/threads/{t3}", headers=ALICE).json()["memories"]

    assert memories, "no memories recalled"
    assert any("User tracks NVIDIA" in m for m in memories)
    assert not any("Tesla" in m for m in memories)  # relevance: the Tesla thread isn't recalled
    assert "User tracks NVIDIA" in brain.prompts["draft"][-1]  # and they reached the prompt


def test_preferences_are_stored_once(brain, server):
    """Same preference twice -> one memory (keys are content hashes, so upserts dedupe)."""
    from backend import persistence

    q = {"question": "I prefer short answers. What is NVIDIA revenue?"}
    with server() as c:
        for _ in range(2):
            tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
            c.post(f"/threads/{tid}/messages", headers=ALICE, json=q)
        items = c.portal.call(lambda: persistence.runtime().store.asearch(("alice", "memories"), limit=50))
    kinds = sorted(i.value["kind"] for i in items)
    assert kinds.count("preference") == 1
    assert kinds.count("company") == 1
    assert kinds.count("report") == 2  # one per thread
