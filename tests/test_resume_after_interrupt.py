"""HITL gates (approve / reject / edit), resuming after a restart, and retry after a failure."""

from conftest import user

from backend.web_research import UNVERIFIED_NOTE

ALICE = user("alice")
QUESTION = {"question": "What is NVIDIA's latest revenue?"}


def _paused_thread(c):
    tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
    r = c.post(f"/threads/{tid}/messages", json=QUESTION, headers=ALICE).json()
    assert r["status"] == "needs_confirmation" and r["kind"] == "web_search"
    return tid, r


def test_edit_query_then_edit_report_across_restarts(backend, brain, server):
    brain.can_answer = False
    with server() as c:
        tid, gate = _paused_thread(c)
        assert gate["search"]["query"] == brain.planned_query
        assert gate["draft"] == brain.draft_answer

    with server() as c:  # restart while paused at the search gate
        r = c.post(f"/threads/{tid}/resume", json={"action": "edit", "query": "nvidia q3 fy2027 revenue"},
                   headers=ALICE).json()
        assert brain.tavily_queries == ["nvidia q3 fy2027 revenue"]  # edited query was searched
        assert r["status"] == "needs_confirmation" and r["kind"] == "report"
        assert brain.final_text in r["answer"]

    with server() as c:  # restart while paused at the report gate
        assert c.get(f"/threads/{tid}", headers=ALICE).json()["next"] == ["review_report"]
        r = c.post(f"/threads/{tid}/resume", json={"action": "edit", "draft": "My edited report."},
                   headers=ALICE).json()
        assert r["status"] == "answered"
        assert r["answer"] == "My edited report."
        assert r["web_search"]["used"] is True
        assert r["web_search"]["query"] == "nvidia q3 fy2027 revenue"
        state = c.get(f"/threads/{tid}", headers=ALICE).json()
        assert state["messages"][-1] == {"role": "assistant", "content": "My edited report."}
        assert state["thread"]["status"] == "completed"
    assert len(brain.tavily_queries) == 1


def test_approve_both_gates(backend, brain, server):
    brain.can_answer = False
    with server() as c:
        tid, _ = _paused_thread(c)
        r = c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE).json()
        assert r["kind"] == "report"
        r = c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE).json()
        assert r["status"] == "answered"
        assert r["answer"].startswith(brain.final_text)
        assert "Sources:\n[1] https://news.example.com/nvda" in r["answer"]
        # not paused any more
        assert c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE).status_code == 409


def test_reject_report_returns_unverified_draft(brain, server):
    brain.can_answer = False
    with server() as c:
        tid, _ = _paused_thread(c)
        c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE)
        r = c.post(f"/threads/{tid}/resume", json={"action": "reject"}, headers=ALICE).json()
        assert r["status"] == "answered"
        assert r["answer"] == f"{brain.draft_answer}\n\n{UNVERIFIED_NOTE}"


def test_legacy_ask_confirm_still_work(brain, server):
    brain.can_answer = False
    with server() as c:
        r = c.post("/ask", json=QUESTION).json()  # no header -> user "local"
        assert r["status"] == "needs_confirmation" and r["kind"] == "web_search"
        r = c.post("/confirm", json={"thread_id": r["thread_id"], "approve": True}).json()
        assert r["kind"] == "report"
        r = c.post("/confirm", json={"thread_id": r["thread_id"], "approve": True}).json()
        assert r["status"] == "answered" and r["web_search"]["used"] is True
        # the legacy streaming route emits the gate event too
        body = c.post("/ask/stream", json=QUESTION).text
        assert "event: needs_confirmation" in body and "event: done" in body


def test_retry_after_failure_skips_completed_nodes(backend, brain, server):
    brain.can_answer = False
    brain.fail_final = 1  # final_answer's LLM call fails once
    with server() as c:
        tid, _ = _paused_thread(c)
        r = c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE)
        assert r.status_code == 500
        state = c.get(f"/threads/{tid}", headers=ALICE).json()
        assert state["thread"]["status"] == "failed"
        assert state["next"] == ["final_answer"]

    before = (brain.retrievals, brain.count("draft"), brain.count("plan"), list(brain.tavily_queries))
    assert before[0] == 1 and before[3] == [brain.planned_query]

    with server() as c:  # restart, then resume from the last checkpoint
        r = c.post(f"/threads/{tid}/retry", headers=ALICE).json()
        assert r["status"] == "needs_confirmation" and r["kind"] == "report"
        after = (brain.retrievals, brain.count("draft"), brain.count("plan"), list(brain.tavily_queries))
        assert after == before  # retrieve, draft, plan and the paid search did NOT re-run
        assert brain.count("final") == 2  # only the failed node ran again
        r = c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=ALICE).json()
        assert r["status"] == "answered"
        assert c.post(f"/threads/{tid}/retry", headers=ALICE).status_code == 409  # not failed now
