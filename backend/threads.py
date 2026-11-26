"""Thread registry: who owns which LangGraph thread, plus a title and status.

The checkpointer stores a thread's state; this table stores the metadata the API needs
to list a user's threads and to refuse access to anyone else's. SQLite (dev) or the
same Postgres database as the checkpointer (prod), via SQLAlchemy Core async.

Schema: SQLite dev creates the tables directly. On Postgres they come from the Alembic
migrations in backend/migrations (run by the Docker "migrate" service); the API only checks
that the database is at the latest revision and refuses to start otherwise.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import Column, DateTime, Index, MetaData, String, Table, and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from . import config

STATUSES = ("idle", "running", "interrupted", "completed", "failed")

metadata = MetaData()
threads = Table(
    "threads",
    metadata,
    Column("thread_id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("title", String(200), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("status", String(16), nullable=False, default="idle"),
    Column("error", String(500), nullable=True),
    Index("ix_threads_user_updated", "user_id", "updated_at"),  # a user's threads, newest first
    Index("ix_threads_updated_at", "updated_at"),               # TTL cleanup
)

_engine: Optional[AsyncEngine] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _url() -> str:
    url = config.DATABASE_URL
    if url.startswith(("postgres://", "postgresql://")):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    if url:
        return url
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{(config.DATA_DIR / 'app.sqlite').as_posix()}"


def _db() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Thread registry is not open (the app lifespan opens it).")
    return _engine


class SchemaNotMigrated(RuntimeError):
    pass


def alembic_include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Alembic filter: only FinSight's own tables (and their indexes) are migrated.

    The same database holds LangGraph's tables (checkpoints, checkpoint_blobs,
    checkpoint_writes, store, store_vectors, *_migrations), which LangGraph's setup() owns.
    Without this filter, `alembic revision --autogenerate` would emit DROP TABLE for them."""
    own = metadata.tables
    if type_ == "table":
        return name in own
    table = getattr(getattr(obj, "table", None), "name", None)
    return table is None or table in own


def _alembic_heads() -> set:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(config.REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.BACKEND_DIR / "migrations"))
    return set(ScriptDirectory.from_config(cfg).get_heads())


async def _check_migrated(conn) -> None:
    from alembic.runtime.migration import MigrationContext

    current = set(await conn.run_sync(lambda sync: MigrationContext.configure(sync).get_current_heads()))
    heads = _alembic_heads()
    if current != heads:
        raise SchemaNotMigrated(
            f"Database schema is at revision {sorted(current) or 'none'}, the code needs {sorted(heads)}. "
            "Run the migrations first: `docker compose run --rm migrate` (or `alembic upgrade head`)."
        )


async def open_registry() -> None:
    global _engine
    url = _url()
    postgres = url.startswith("postgresql")
    _engine = create_async_engine(url, pool_pre_ping=True,
                                  **({"pool_size": 5, "max_overflow": 5} if postgres else {}))
    async with _engine.begin() as conn:
        if postgres:
            await _check_migrated(conn)
        else:
            from . import idempotency  # noqa: F401  (registers its table on `metadata`)

            await conn.run_sync(metadata.create_all)


def engine() -> AsyncEngine:
    return _db()


async def close_registry() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def _row(row: Any) -> Dict[str, Any]:
    out = dict(row._mapping)
    for key in ("created_at", "updated_at"):
        value = out[key]
        if value is not None and value.tzinfo is None:  # SQLite drops the timezone
            out[key] = value.replace(tzinfo=timezone.utc)
    return out


async def create(thread_id: str, user_id: str, title: str = "") -> Dict[str, Any]:
    now = _now()
    values = {"thread_id": thread_id, "user_id": user_id, "title": title[:200],
              "created_at": now, "updated_at": now, "status": "idle", "error": None}
    async with _db().begin() as conn:
        await conn.execute(threads.insert().values(**values))
    return values


async def get_owned(thread_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """The thread's row if it exists AND belongs to user_id; otherwise None."""
    async with _db().connect() as conn:
        row = (await conn.execute(
            select(threads).where(threads.c.thread_id == thread_id, threads.c.user_id == user_id)
        )).first()
    return _row(row) if row else None


async def list_for(user_id: str) -> List[Dict[str, Any]]:
    async with _db().connect() as conn:
        rows = (await conn.execute(
            select(threads).where(threads.c.user_id == user_id).order_by(threads.c.updated_at.desc())
        )).all()
    return [_row(r) for r in rows]


async def set_status(thread_id: str, status: str, error: Optional[str] = None,
                     title: Optional[str] = None) -> None:
    assert status in STATUSES, status
    values: Dict[str, Any] = {"status": status, "updated_at": _now(),
                              "error": error[:500] if error else None}
    if title is not None:
        values["title"] = title[:200]
    async with _db().begin() as conn:
        await conn.execute(update(threads).where(threads.c.thread_id == thread_id).values(**values))


async def delete_row(thread_id: str) -> None:
    async with _db().begin() as conn:
        await conn.execute(delete(threads).where(threads.c.thread_id == thread_id))


async def older_than(days: int) -> List[str]:
    cutoff = _now() - timedelta(days=days)
    async with _db().connect() as conn:
        rows = (await conn.execute(
            select(threads.c.thread_id).where(threads.c.updated_at < cutoff)
        )).all()
    return [r[0] for r in rows]


async def all_ids() -> set:
    async with _db().connect() as conn:
        return {r[0] for r in (await conn.execute(select(threads.c.thread_id))).all()}


# A 'running' row this old is treated as abandoned (e.g. a stream whose client vanished
# before the run started), so a thread can't stay locked forever.
STALE_RUN = timedelta(minutes=30)


async def claim(thread_id: str, allowed: Iterable[str], title: Optional[str] = None) -> bool:
    """Atomically move a thread to 'running' if its status is one of `allowed`.

    Returns False if another request (in any worker) got there first, so two concurrent
    resumes can't both run the graph (and both pay for a web search)."""
    now = _now()
    values: Dict[str, Any] = {"status": "running", "updated_at": now, "error": None}
    if title is not None:
        values["title"] = title[:200]
    startable = or_(threads.c.status.in_(list(allowed)),
                    and_(threads.c.status == "running", threads.c.updated_at < now - STALE_RUN))
    async with _db().begin() as conn:
        result = await conn.execute(
            update(threads).where(threads.c.thread_id == thread_id, startable).values(**values)
        )
    return (result.rowcount or 0) == 1


async def get_status(thread_id: str) -> Optional[str]:
    async with _db().connect() as conn:
        row = (await conn.execute(select(threads.c.status).where(threads.c.thread_id == thread_id))).first()
    return row[0] if row else None


async def fail_running() -> int:
    """At startup: a thread still 'running' was killed mid-run. Mark it failed so
    /threads/{id}/retry can resume it from its last checkpoint."""
    async with _db().begin() as conn:
        result = await conn.execute(
            update(threads).where(threads.c.status == "running")
            .values(status="failed", error="Server stopped during the run.", updated_at=_now())
        )
    return result.rowcount or 0
