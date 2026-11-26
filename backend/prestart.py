"""Run once per API container start, before the uvicorn workers (docker/entrypoint.sh).

1. Fail fast if PostgreSQL is unreachable (clear error, password masked).
2. Refuse to start if the schema isn't at the latest Alembic revision.
3. Mark threads left 'running' by the previous container as failed, so /retry can resume
   them. Doing this here, not in each worker, stops one worker's restart from failing
   runs that other workers are still executing.
"""

import asyncio
import logging
import sys

from . import config, db, threads

log = logging.getLogger("finsight.prestart")


async def _main() -> int:
    if db.is_postgres(config.DATABASE_URL):
        try:
            await db.wait_for_db(config.DATABASE_URL, config.DB_CONNECT_TIMEOUT)
        except db.DatabaseUnavailable as exc:
            log.error("%s", exc)
            return 2
    try:
        await threads.open_registry()
    except threads.SchemaNotMigrated as exc:
        log.error("%s", exc)
        return 3
    try:
        failed = await threads.fail_running()
        if failed:
            log.info("%d thread(s) were interrupted by the last shutdown; /threads/{id}/retry resumes them",
                     failed)
    finally:
        await threads.close_registry()
    log.info("Database ready")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: [%(name)s] %(message)s")
    sys.exit(asyncio.run(_main()))
