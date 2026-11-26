"""State history, replay from a past checkpoint, and fork via update_state."""

from conftest import user

ALICE = user("alice")


def _checkpoint_before(history, node):
    return next(cp for cp in history if cp["next"] == [node])


def test_replay_and_fork_from_past_checkpoint(brain, server):
    with server() as c:
        tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        r = c.post(f"/threads/{tid}/messages", json={"question": "How did NVIDIA revenue grow?"},
                   headers=ALICE).json()
        assert r["status"] == "answered" and r["answer"] == brain.draft_answer

        history = c.get(f"/threads/{tid}/history", headers=ALICE).json()
        assert history[0]["next"] == []  # newest first; head is finished
        original_ids = {cp["checkpoint_id"] for cp in history}
        before_draft = _checkpoint_before(history, "draft")
        assert before_draft["values"]["question"] == "How did NVIDIA revenue grow?"
        assert "documents" not in before_draft["values"]  # trimmed unless ?full=true

        # ---- replay: nodes after the checkpoint run again; earlier ones do not
        retrievals, drafts = brain.retrievals, brain.count("draft")
        brain.draft_answer = "Replayed answer."
        r = c.post(f"/threads/{tid}/replay", json={"checkpoint_id": before_draft["checkpoint_id"]},
                   headers=ALICE).json()
        assert r["answer"] == "Replayed answer."
        assert brain.count("draft") == drafts + 1
        assert brain.retrievals == retrievals
        state = c.get(f"/threads/{tid}", headers=ALICE).json()
        # branched from before the first answer: one question, one (replayed) answer
        assert [m["content"] for m in state["messages"]] == ["How did NVIDIA revenue grow?", "Replayed answer."]
        history2 = c.get(f"/threads/{tid}/history", headers=ALICE).json()
        assert original_ids <= {cp["checkpoint_id"] for cp in history2}  # old branch kept

        # ---- fork: edit the question at the past checkpoint and run the new branch
        r = c.post(f"/threads/{tid}/fork", headers=ALICE, json={
            "checkpoint_id": before_draft["checkpoint_id"],
            "values": {"question": "What is NVIDIA's gross margin?"},
        }).json()
        fork_id = r["checkpoint_id"]
        assert r["result"]["status"] == "answered"
        assert "Question: What is NVIDIA's gross margin?" in brain.prompts["draft"][-1]
        history3 = c.get(f"/threads/{tid}/history", headers=ALICE).json()
        fork_cp = next(cp for cp in history3 if cp["checkpoint_id"] == fork_id)
        assert fork_cp["parent_id"] == before_draft["checkpoint_id"]
        assert fork_cp["values"]["question"] == "What is NVIDIA's gross margin?"
        assert original_ids <= {cp["checkpoint_id"] for cp in history3}
        # the original checkpoint is untouched
        orig = next(cp for cp in history3 if cp["checkpoint_id"] == before_draft["checkpoint_id"])
        assert orig["values"]["question"] == "How did NVIDIA revenue grow?"


def test_fork_edit_draft_without_running_then_replay(brain, server):
    brain.can_answer = False
    with server() as c:
        tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        r = c.post(f"/threads/{tid}/messages", json={"question": "Latest NVIDIA price?"}, headers=ALICE).json()
        assert r["kind"] == "web_search"
        history = c.get(f"/threads/{tid}/history", headers=ALICE).json()
        at_gate = _checkpoint_before(history, "ask_permission")

        # fork the paused checkpoint with an edited draft, but don't run it yet
        r = c.post(f"/threads/{tid}/fork", headers=ALICE, json={
            "checkpoint_id": at_gate["checkpoint_id"], "values": {"draft": "Edited draft."},
            "as_node": "plan_search", "run": False,
        }).json()
        assert r["result"] is None
        # run the forked branch: it pauses at the gate again, now showing the edited draft
        r = c.post(f"/threads/{tid}/replay", json={"checkpoint_id": r["checkpoint_id"]}, headers=ALICE).json()
        assert r["status"] == "needs_confirmation" and r["draft"] == "Edited draft."
        r = c.post(f"/threads/{tid}/resume", json={"action": "reject"}, headers=ALICE).json()
        assert r["answer"].startswith("Edited draft.")


def test_fork_rejects_bad_input(brain, server):
    with server() as c:
        tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
        c.post(f"/threads/{tid}/messages", json={"question": "NVIDIA revenue?"}, headers=ALICE)
        cp = c.get(f"/threads/{tid}/history", headers=ALICE).json()[0]["checkpoint_id"]
        assert c.post(f"/threads/{tid}/fork", headers=ALICE,
                      json={"checkpoint_id": cp, "values": {"messages": []}}).status_code == 422
        assert c.post(f"/threads/{tid}/fork", headers=ALICE,
                      json={"checkpoint_id": cp, "values": {"draft": "x"}, "as_node": "nope"}).status_code == 422
        assert c.post(f"/threads/{tid}/replay", headers=ALICE,
                      json={"checkpoint_id": "does-not-exist"}).status_code == 404
