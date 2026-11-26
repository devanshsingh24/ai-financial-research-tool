"""Checkpoint TTL cleanup: expired threads and orphan checkpoints are deleted."""

from datetime import datetime, timedelta, timezone

from conftest import user
from sqlalchemy import update

from backend import housekeeping, persistence, threads

ALICE = user("alice")


async def _backdate(thread_id: str, days: int) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=days)
    async with threads._db().begin() as conn:
        await conn.execute(update(threads.threads).where(threads.threads.c.thread_id == thread_id)
                           .values(updated_at=old))


async def _has_checkpoints(thread_id: str) -> bool:
    saver = persistence.runtime().saver
    return await saver.aget_tuple({"configurable": {"thread_id": thread_id}}) is not None


def test_purge_expired_and_orphan_threads(backend, server):
    with server() as c:
        ids = []
        for q in ["NVIDIA revenue?", "NVIDIA margins?", "NVIDIA guidance?"]:
            tid = c.post("/threads", json={}, headers=ALICE).json()["thread_id"]
            c.post(f"/threads/{tid}/messages", json={"question": q}, headers=ALICE)
            ids.append(tid)
        old, fresh, orphan = ids
        c.portal.call(_backdate, old, 40)
        c.portal.call(threads.delete_row, orphan)  # checkpoints left without a registry row

        assert c.portal.call(housekeeping.purge, 30) == {"expired": 1, "orphans": 0, "idempotency_keys": 0}
        assert not c.portal.call(_has_checkpoints, old)
        assert c.get(f"/threads/{old}", headers=ALICE).status_code == 404
        assert c.portal.call(_has_checkpoints, fresh)
        assert c.portal.call(_has_checkpoints, orphan)  # orphan is recent: kept

        # a negative TTL puts the cutoff in the future, so everything counts as expired
        assert c.portal.call(housekeeping.purge, -1) == {"expired": 1, "orphans": 1, "idempotency_keys": 0}
        assert not c.portal.call(_has_checkpoints, orphan)
        assert not c.portal.call(_has_checkpoints, fresh)
