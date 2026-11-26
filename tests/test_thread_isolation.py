"""Users can only see and act on their own threads and memories."""

from conftest import user

ALICE, BOB = user("alice"), user("bob")


def test_other_users_get_404_everywhere(brain, server):
    brain.can_answer = False
    with server() as c:
        tid = c.post("/threads", json={"title": "alice only"}, headers=ALICE).json()["thread_id"]
        c.post(f"/threads/{tid}/messages", json={"question": "NVIDIA latest revenue?"}, headers=ALICE)
        cp = c.get(f"/threads/{tid}/history", headers=ALICE).json()[0]["checkpoint_id"]

        assert tid not in {t["thread_id"] for t in c.get("/threads", headers=BOB).json()}
        assert tid not in {t["thread_id"] for t in c.get("/threads").json()}  # default user "local"
        attempts = [
            c.get(f"/threads/{tid}", headers=BOB),
            c.get(f"/threads/{tid}/history", headers=BOB),
            c.post(f"/threads/{tid}/messages", json={"question": "hijack?"}, headers=BOB),
            c.post(f"/threads/{tid}/messages/stream", json={"question": "hijack?"}, headers=BOB),
            c.post(f"/threads/{tid}/resume", json={"action": "approve"}, headers=BOB),
            c.post(f"/threads/{tid}/resume/stream", json={"action": "approve"}, headers=BOB),
            c.post(f"/threads/{tid}/retry", headers=BOB),
            c.post(f"/threads/{tid}/replay", json={"checkpoint_id": cp}, headers=BOB),
            c.post(f"/threads/{tid}/fork", json={"checkpoint_id": cp, "values": {"draft": "x"}}, headers=BOB),
            c.post("/confirm", json={"thread_id": tid, "approve": True}, headers=BOB),
            c.post("/confirm/stream", json={"thread_id": tid, "approve": True}, headers=BOB),
            c.delete(f"/threads/{tid}", headers=BOB),
        ]
        assert [a.status_code for a in attempts] == [404] * len(attempts)
        assert brain.tavily_queries == []  # Bob's approve attempts spent nothing

        # Alice's thread is untouched and still hers to resume
        state = c.get(f"/threads/{tid}", headers=ALICE).json()
        assert state["thread"]["status"] == "interrupted" and state["next"] == ["ask_permission"]
        assert c.post(f"/threads/{tid}/resume", json={"action": "reject"}, headers=ALICE).status_code == 200
        assert c.delete(f"/threads/{tid}", headers=ALICE).status_code == 204
        assert c.get(f"/threads/{tid}", headers=ALICE).status_code == 404


def test_memories_do_not_leak_between_users(brain, server):
    with server() as c:
        t1 = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        c.post(f"/threads/{t1}/messages", headers=ALICE,
               json={"question": "Track NVIDIA. I prefer short answers. NVIDIA revenue?"})
        t2 = c.post("/threads", json={}, headers=BOB).json()["thread_id"]
        c.post(f"/threads/{t2}/messages", headers=BOB, json={"question": "NVIDIA revenue?"})
        assert c.get(f"/threads/{t2}", headers=BOB).json()["memories"] == []
        t3 = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        c.post(f"/threads/{t3}/messages", headers=ALICE, json={"question": "NVIDIA revenue?"})
        assert c.get(f"/threads/{t3}", headers=ALICE).json()["memories"]


def test_invalid_user_header_rejected(brain, server):
    with server() as c:
        assert c.get("/threads", headers={"X-User-Id": "../etc"}).status_code == 422
