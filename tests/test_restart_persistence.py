"""Thread state and the thread registry survive a server restart."""

from conftest import user

from backend import threads

ALICE = user("alice")


def test_restart_persistence(backend, brain, server):
    brain.can_answer = False  # pause at the search gate
    with server() as c:
        tid = c.post("/threads", json={"title": "NVIDIA research"}, headers=ALICE).json()["thread_id"]
        r = c.post(f"/threads/{tid}/messages", json={"question": "What is NVIDIA's latest revenue?"},
                   headers=ALICE).json()
        assert r["status"] == "needs_confirmation" and r["kind"] == "web_search"

    # --- restart: every connection was closed; reopen from disk / Postgres
    with server() as c:
        listed = {t["thread_id"]: t for t in c.get("/threads", headers=ALICE).json()}
        assert listed[tid]["status"] == "interrupted"
        assert listed[tid]["title"] == "NVIDIA research"

        state = c.get(f"/threads/{tid}", headers=ALICE).json()
        assert state["next"] == ["ask_permission"]
        assert state["pending"]["kind"] == "web_search"
        assert state["pending"]["search"]["query"] == brain.planned_query
        assert [m["role"] for m in state["messages"]] == ["user"]

        r = c.post(f"/threads/{tid}/resume", json={"action": "reject"}, headers=ALICE).json()
        assert r["status"] == "answered"
        assert "Not verified" in r["answer"]
        assert brain.tavily_queries == []  # rejected: no credit spent

    # --- second restart: a follow-up turn sees the earlier conversation
    brain.can_answer = True
    with server() as c:
        r = c.post(f"/threads/{tid}/messages", json={"question": "And what about its margins?"},
                   headers=ALICE).json()
        assert r["status"] == "answered"
        state = c.get(f"/threads/{tid}", headers=ALICE).json()
        assert [m["role"] for m in state["messages"]] == ["user", "assistant", "user", "assistant"]
        assert state["thread"]["status"] == "completed"
        follow_up_prompt = brain.prompts["draft"][-1]
        assert "What is NVIDIA's latest revenue?" in follow_up_prompt  # history reached the prompt


def test_running_thread_is_marked_failed_after_restart(backend, brain, server):
    """A run killed by a crash ('running' in the registry) becomes 'failed' (retryable) on startup."""
    with server() as c:
        tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        c.portal.call(threads.set_status, tid, "running")
    with server() as c:
        row = next(t for t in c.get("/threads", headers=ALICE).json() if t["thread_id"] == tid)
        assert row["status"] == "failed"


def test_long_threads_are_summarized(brain, server, monkeypatch):
    from backend import config

    monkeypatch.setattr(config, "MAX_MESSAGES", 4)
    monkeypatch.setattr(config, "KEEP_MESSAGES", 2)
    with server() as c:
        tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        for q in ["NVIDIA revenue?", "NVIDIA margins?", "NVIDIA guidance?"]:
            assert c.post(f"/threads/{tid}/messages", json={"question": q},
                          headers=ALICE).json()["status"] == "answered"
        state = c.get(f"/threads/{tid}", headers=ALICE).json()
    assert brain.count("summary") >= 1
    assert state["summary"].startswith("- earlier turns")
    assert len(state["messages"]) <= 4
    assert state["messages"][-1]["role"] == "assistant"
