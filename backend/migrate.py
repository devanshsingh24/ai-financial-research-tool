"""The Docker "migrate" service: bring a PostgreSQL database fully up to date, then exit.

1. Wait for the database (fail fast with a clear error).
2. `alembic upgrade head` for FinSight's own tables (thread registry, idempotency keys).
3. LangGraph's own migrations: AsyncPostgresSaver.setup() + AsyncPostgresStore.setup()
   (checkpoints, store, pgvector index).

Runs as the database owner (MIGRATION_DATABASE_URL, falling back to DATABASE_URL), because
the API's user may only read and write rows. Safe to run repeatedly.
    python -m backend.migrate
"""

import asyncio
import logging
import os
import sys

from . import config, db

log = logging.getLogger("finsight.migrate")


def alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(config.REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.BACKEND_DIR / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False  # keep our logging; alembic.ini's would silence it
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    from alembic import command

    command.upgrade(alembic_config(url), revision)


def main() -> int:
    url = os.getenv("MIGRATION_DATABASE_URL", "").strip() or config.DATABASE_URL
    if not db.is_postgres(url):
        log.error("MIGRATION_DATABASE_URL / DATABASE_URL must be a postgresql:// URL (got %r)", db.masked(url))
        return 2
    try:
        db.wait_for_db_sync(url, config.DB_CONNECT_TIMEOUT)
    except db.DatabaseUnavailable as exc:
        log.error("%s", exc)
        return 2
    log.info("Applying FinSight migrations (%s)", db.describe(url))
    upgrade(url)
    log.info("Applying LangGraph checkpoint + store migrations")
    from . import persistence

    asyncio.run(persistence.setup_postgres(url))
    log.info("Database is up to date")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: [%(name)s] %(message)s")
    sys.exit(main())
