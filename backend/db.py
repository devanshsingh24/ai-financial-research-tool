"""PostgreSQL helpers: URL handling, fail-fast startup check, health ping, advisory locks.

Only used when DATABASE_URL is a postgresql:// URL (SQLite dev mode never imports psycopg).
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

log = logging.getLogger("finsight.persistence")


class DatabaseUnavailable(RuntimeError):
    """The database could not be reached within DB_CONNECT_TIMEOUT."""


def is_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def masked(url: str) -> str:
    """The URL with its password replaced, safe to log or show in errors."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return parts._replace(netloc=netloc).geturl()


def describe(url: str) -> str:
    parts = urlsplit(url)
    return (f"host={parts.hostname or 'localhost'} port={parts.port or 5432} "
            f"db={(parts.path or '/').lstrip('/') or '?'} user={parts.username or '?'}")


def _psycopg_url(url: str) -> str:
    # psycopg accepts postgresql:// and postgres://; SQLAlchemy-style prefixes are not valid.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


async def wait_for_db(url: str, timeout: float) -> None:
    """Retry a connection until it works or `timeout` seconds pass, then fail fast with a
    clear error (password masked). Docker's depends_on usually makes the first try succeed."""
    import psycopg

    deadline = time.monotonic() + timeout
    attempt, last_error = 0, ""
    while True:
        attempt += 1
        try:
            conn = await psycopg.AsyncConnection.connect(_psycopg_url(url), connect_timeout=5)
            await conn.close()
            if attempt > 1:
                log.info("Database reachable after %d attempts (%s)", attempt, describe(url))
            return
        except Exception as exc:  # psycopg.OperationalError and DNS errors alike
            last_error = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        if time.monotonic() >= deadline:
            raise DatabaseUnavailable(
                f"Cannot reach PostgreSQL ({describe(url)}) after {timeout:.0f}s: {last_error}. "
                f"Check DATABASE_URL ({masked(url)}), that the database is running, and the "
                f"user's password."
            )
        await asyncio.sleep(min(2.0, max(0.1, deadline - time.monotonic())))


def wait_for_db_sync(url: str, timeout: float) -> None:
    asyncio.run(wait_for_db(url, timeout))


async def ping(pool, timeout: float = 2.0) -> None:
    """Raise if the pool can't run SELECT 1 within `timeout` seconds."""

    async def _query() -> None:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")

    await asyncio.wait_for(_query(), timeout)


@asynccontextmanager
async def advisory_lock(pool, key: int) -> AsyncIterator[bool]:
    """Session-level pg_try_advisory_lock on one pooled connection. Yields True if this
    process got the lock (and releases it afterwards), False if someone else holds it."""
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (key,))).fetchone()
        locked = bool(row["locked"] if isinstance(row, dict) else row[0])
        try:
            yield locked
        finally:
            if locked:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (key,))


def error_text(exc: Optional[BaseException]) -> str:
    if exc is None:
        return ""
    text = str(exc).strip()
    return (text.splitlines()[0] if text else type(exc).__name__)[:300]
