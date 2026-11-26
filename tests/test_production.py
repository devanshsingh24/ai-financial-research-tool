"""Production behaviour: Idempotency-Key replay, atomic run claims (no double runs across
workers), /health checking the database, and FAISS index versions shared by workers.
SQLite always; Postgres too when TEST_DATABASE_URL is set."""

import asyncio

import fakes
from conftest import user
from langchain_community.vectorstores import FAISS

from backend import rag, threads

ALICE = user("alice")
QUESTION = {"question": "What is NVIDIA's latest revenue?"}


def _key(value: str) -> dict:
    return {**ALICE, "Idempotency-Key": value}


def _paused_thread(c):
    tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
    r = c.post(f"/threads/{tid}/messages", json=QUESTION, headers=ALICE).json()
    assert r["status"] == "needs_confirmation" and r["kind"] == "web_search"
    return tid


def test_idempotency_key_replays_instead_of_rerunning(backend, brain, server):
    brain.can_answer = False
    with server() as c:
        first = c.post("/threads", json={"title": "Q3"}, headers=_key("create-1"))
        again = c.post("/threads", json={"title": "Q3"}, headers=_key("create-1"))
        assert first.status_code == again.status_code == 201
        assert again.json() == first.json()
        assert again.headers.get("Idempotent-Replayed") == "true"
        assert len(c.get("/threads", headers=ALICE).json()) == 1  # created once

        # Same key, different body: refused rather than silently replayed.
        assert c.post("/threads", json={"title": "other"}, headers=_key("create-1")).status_code == 422

        tid = first.json()["thread_id"]
        r1 = c.post(f"/threads/{tid}/messages", json=QUESTION, headers=_key("ask-1"))
        r2 = c.post(f"/threads/{tid}/messages", json=QUESTION, headers=_key("ask-1"))
        assert r1.status_code == r2.status_code == 200
        assert r2.json() == r1.json() and r1.json()["status"] == "needs_confirmation"
        assert brain.count("draft") == 1  # the graph ran once

        approve = {"action": "approve"}
        r1 = c.post(f"/threads/{tid}/resume", json=approve, headers=_key("resume-1"))
        r2 = c.post(f"/threads/{tid}/resume", json=approve, headers=_key("resume-1"))
        assert r2.json() == r1.json() and r1.json()["kind"] == "report"
        assert len(brain.tavily_queries) == 1  # one web search, even though the client retried

        # Keys are per user: bob's identical key is unrelated.
        bob = {**user("bob"), "Idempotency-Key": "create-1"}
        assert c.post("/threads", json={"title": "Q3"}, headers=bob).json()["thread_id"] != tid


def test_failed_request_releases_its_key(backend, brain, server):
    brain.can_answer = False
    with server() as c:
        tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        # Nothing to resume yet: the request fails (404), and the key is not burned.
        r = c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=_key("k-retry"))
        assert r.status_code == 404
        c.post(f"/threads/{tid}/messages", json=QUESTION, headers=ALICE)
        r = c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=_key("k-retry"))
        assert r.status_code == 200 and r.json()["kind"] == "report"


def test_only_one_concurrent_claim_wins(backend, brain, server):
    with server() as c:
        tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]

        async def race():
            return await asyncio.gather(*[threads.claim(tid, ("idle",)) for _ in range(8)])

        assert sorted(c.portal.call(race)) == [False] * 7 + [True]
        assert c.get(f"/threads/{tid}", headers=ALICE).json()["thread"]["status"] == "running"


def test_resume_is_refused_while_another_run_holds_the_thread(backend, brain, server):
    brain.can_answer = False
    with server() as c:
        tid = _paused_thread(c)
        assert c.portal.call(threads.claim, tid, ("interrupted",))  # e.g. another worker resuming
        for path in (f"/threads/{tid}/resume", f"/threads/{tid}/resume/stream"):
            r = c.post(path, json={"action": "approve"}, headers=ALICE)
            assert r.status_code == 409 and "already running" in r.json()["detail"]
        r = c.post(f"/threads/{tid}/messages", json=QUESTION, headers=ALICE)
        assert r.status_code == 409
        assert brain.tavily_queries == []  # nothing ran twice

        c.portal.call(threads.set_status, tid, "interrupted")  # that run finished
        r = c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE)
        assert r.status_code == 200 and len(brain.tavily_queries) == 1


def test_health_checks_the_database(backend, server, monkeypatch):
    with server() as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok" and r.json()["db"] == "ok"
        assert r.json()["persistence"] == backend

        def broken():
            raise RuntimeError("connection refused")

        monkeypatch.setattr(threads, "engine", broken)
        r = c.get("/health")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded" and "connection refused" in r.json()["db"]


def test_index_versions_are_picked_up_by_other_workers(tmp_path, monkeypatch):
    emb = fakes.HashEmbeddings()
    monkeypatch.setattr(rag, "INDEX_PATH", tmp_path / "faiss_index")
    monkeypatch.setattr(rag, "embeddings", emb)
    for name in ("_store", "_retriever", "_loaded_version", "index_problem"):
        monkeypatch.setattr(rag, name, None)

    v1 = rag.publish(FAISS.from_texts(["apple pie recipe with cinnamon"], emb,
                                      metadatas=[{"source": "https://a.example"}]))
    this_worker = (rag._store, rag._retriever, rag._loaded_version)
    assert rag.search_with_scores("apple pie")[1] == ["https://a.example"]

    # Another worker publishes a new collection ...
    v2 = rag.publish(FAISS.from_texts(["nvidia data center revenue"], emb,
                                      metadatas=[{"source": "https://n.example"}]))
    assert v2 != v1 and (tmp_path / "faiss_index" / "CURRENT").read_text() == v2
    # ... while this worker still has v1 in memory: its next request reloads.
    rag._store, rag._retriever, rag._loaded_version = this_worker
    assert rag.search_with_scores("nvidia revenue")[1] == ["https://n.example"]
    assert rag._loaded_version == v2

    for i in range(4):  # old versions are pruned
        rag.publish(FAISS.from_texts([f"text {i}"], emb))
    assert len([p for p in (tmp_path / "faiss_index").glob("v*") if p.is_dir()]) == 3
