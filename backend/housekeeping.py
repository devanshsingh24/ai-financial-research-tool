"""Delete checkpoints of threads idle longer than CHECKPOINT_TTL_DAYS, and expired
Idempotency-Key records.

Runs:
- in the API process every CLEANUP_INTERVAL_HOURS (local dev; 0 disables), or
- as its own process: `python -m backend.housekeeping` (once, for cron) or
  `python -m backend.housekeeping --loop` (the Docker "cleanup" service).

On Postgres, a run takes an advisory lock, so several runners (workers, replicas, cron)
never purge at the same time. Long-term memories (the store) are not expired. The Tavily
cache is untouched.
"""

import argparse
import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from . import config, db, idempotency, persistence, threads

log = logging.getLogger("finsight.housekeeping")

LOCK_KEY = 0x46494E53  # "FINS": arbitrary but fixed advisory-lock id for cleanup


async def _orphans_postgres(pool, registered: set, cutoff: datetime) -> List[str]:
    """Threads with checkpoints but no registry row, whose newest checkpoint is older than
    `cutoff`. One query over the checkpoints table, without loading any checkpoint blobs."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT thread_id, max((checkpoint->>'ts')::timestamptz) AS latest "
            "FROM checkpoints GROUP BY thread_id HAVING max((checkpoint->>'ts')::timestamptz) < %s",
            (cutoff,),
        )).fetchall()
    return [r["thread_id"] for r in rows if r["thread_id"] not in registered]


async def _orphans_scan(saver, registered: set, cutoff: datetime) -> List[str]:
    latest: Dict[str, datetime] = {}
    async for item in saver.alist(None):
        thread_id = item.config["configurable"]["thread_id"]
        if thread_id in registered:
            continue
        ts = datetime.fromisoformat(item.checkpoint["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        latest[thread_id] = max(ts, latest.get(thread_id, ts))
    return [t for t, ts in latest.items() if ts < cutoff]


async def _purge(ttl_days: int, saver, pool) -> Dict[str, int]:
    expired = await threads.older_than(ttl_days)
    for thread_id in expired:
        await saver.adelete_thread(thread_id)
        await threads.delete_row(thread_id)

    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    registered = await threads.all_ids()
    if pool is not None:
        orphans = await _orphans_postgres(pool, registered, cutoff)
    else:
        orphans = await _orphans_scan(saver, registered, cutoff)
    for thread_id in orphans:
        await saver.adelete_thread(thread_id)
    keys = await idempotency.purge()
    if expired or orphans or keys:
        log.info("Purged %d expired threads, %d orphan threads, %d idempotency keys",
                 len(expired), len(orphans), keys)
    return {"expired": len(expired), "orphans": len(orphans), "idempotency_keys": keys}


_standalone = None  # (saver, pool) when running as its own process


async def purge(ttl_days: int) -> Dict[str, int]:
    """Delete expired registered threads, orphan checkpoint threads (e.g. ones made before
    the registry existed) whose latest checkpoint is older than the TTL, and expired
    idempotency keys. On Postgres only one runner at a time does the work."""
    if _standalone is not None:
        saver, pool = _standalone
    else:
        runtime = persistence.runtime()
        saver, pool = runtime.saver, runtime.pool
    if pool is None:
        return await _purge(ttl_days, saver, None)
    async with db.advisory_lock(pool, LOCK_KEY) as locked:
        if not locked:
            log.info("Cleanup already running elsewhere; skipping this round")
            return {"expired": 0, "orphans": 0, "idempotency_keys": 0, "skipped": 1}
        return await _purge(ttl_days, saver, pool)


async def run_forever(interval_hours: float = 0) -> None:
    """Purge now, then every interval (CLEANUP_INTERVAL_HOURS by default)."""
    interval = (interval_hours or config.CLEANUP_INTERVAL_HOURS or 6) * 3600
    while True:
        try:
            await purge(config.CHECKPOINT_TTL_DAYS)
        except Exception as exc:  # keep the loop alive; try again next interval
            log.warning("Checkpoint cleanup failed: %s", db.error_text(exc))
        await asyncio.sleep(interval)


async def _main(loop: bool) -> None:
    global _standalone
    async with AsyncExitStack() as stack:
        _standalone = await persistence.open_saver(stack)  # no graph, store or models needed
        await threads.open_registry()
        stack.push_async_callback(threads.close_registry)
        if loop:
            log.info("Cleanup every %sh, TTL %s days", config.CLEANUP_INTERVAL_HOURS or 6,
                     config.CHECKPOINT_TTL_DAYS)
            await run_forever()
        else:
            print(await purge(config.CHECKPOINT_TTL_DAYS))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: [%(name)s] %(message)s")
    parser = argparse.ArgumentParser(description="Delete old checkpoints and idempotency keys.")
    parser.add_argument("--loop", action="store_true", help="keep running every CLEANUP_INTERVAL_HOURS")
    asyncio.run(_main(parser.parse_args().loop))
