"""Checkpointer + long-term store, chosen by DATABASE_URL, and the compiled research graph.

dev  (DATABASE_URL unset): AsyncSqliteSaver  -> data/checkpoints.sqlite
                           AsyncSqliteStore  -> data/memory.sqlite (sqlite-vec index)
prod (postgresql://...):   AsyncPostgresSaver + AsyncPostgresStore (pgvector index), sharing
                           one psycopg AsyncConnectionPool per worker process.

Both are async because the graph runs with astream/ainvoke (which AsyncPostgresSaver
requires). Everything is opened inside the FastAPI lifespan and closed on shutdown, so
no connection lives in graph state or at import time.

setup() (LangGraph's own table migrations) runs at startup unless DB_SETUP_ON_START is
off. In Docker the one-shot "migrate" service runs it as the database owner, because the
API's least-privilege user can't create tables.
"""

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import config, db

log = logging.getLogger("finsight.persistence")


@dataclass
class Runtime:
    app: Any      # compiled research graph
    saver: Any    # checkpointer
    store: Any    # long-term memory store
    backend: str  # "sqlite" | "postgres"
    pool: Any = None  # psycopg AsyncConnectionPool (postgres only)


_runtime: Optional[Runtime] = None


def runtime() -> Runtime:
    if _runtime is None:
        raise RuntimeError("Persistence is not open (the app lifespan opens it).")
    return _runtime


def is_postgres(url: str) -> bool:
    return db.is_postgres(url)


def index_config() -> Dict[str, Any]:
    # rag.embeddings is looked up at open time, so tests can swap in fake embeddings.
    # Imported here, not at module top: the migrate/cleanup processes use this module
    # without loading any model.
    from . import rag

    return {"dims": config.EMBED_DIMS, "embed": rag.embeddings, "fields": ["text"]}


async def open_pool(stack: AsyncExitStack, url: str):
    """A psycopg pool with the settings LangGraph's Postgres saver/store require."""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    await db.wait_for_db(url, config.DB_CONNECT_TIMEOUT)
    pool = AsyncConnectionPool(
        conninfo=url,
        min_size=config.DB_POOL_MIN,
        max_size=max(config.DB_POOL_MIN, config.DB_POOL_MAX),
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,  # drop dead connections (e.g. after a DB restart)
        open=False,
        name="finsight",
    )
    await pool.open(wait=True, timeout=config.DB_CONNECT_TIMEOUT)
    stack.push_async_callback(pool.close)
    return pool


async def open_runtime(stack: AsyncExitStack, setup: Optional[bool] = None) -> Runtime:
    """Open the saver and store on `stack`, optionally run their migrations, compile the graph."""
    global _runtime
    setup = config.DB_SETUP_ON_START if setup is None else setup
    url = config.DATABASE_URL
    pool = None
    if is_postgres(url):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres.aio import AsyncPostgresStore

        pool = await open_pool(stack, url)
        saver = AsyncPostgresSaver(conn=pool)
        store = AsyncPostgresStore(conn=pool, index=index_config())
        backend = "postgres"
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.sqlite.aio import AsyncSqliteStore

        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        saver = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(config.DATA_DIR / "checkpoints.sqlite"))
        )
        store = await stack.enter_async_context(
            AsyncSqliteStore.from_conn_string(str(config.DATA_DIR / "memory.sqlite"),
                                              index=index_config())
        )
        backend = "sqlite"
        setup = True  # SQLite files belong to this process; always safe
    if setup:
        await saver.setup()
        await store.setup()
    from . import web_research

    app = web_research.build_graph().compile(checkpointer=saver, store=store)
    _runtime = Runtime(app=app, saver=saver, store=store, backend=backend, pool=pool)
    stack.callback(_clear)
    log.info("Persistence open (%s%s)", backend, f", {db.describe(url)}" if pool else "")
    return _runtime


async def open_saver(stack: AsyncExitStack):
    """Just the checkpointer (and the pool on Postgres): what cleanup needs, with no
    graph, store or models loaded. Returns (saver, pool)."""
    url = config.DATABASE_URL
    if is_postgres(url):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        pool = await open_pool(stack, url)
        return AsyncPostgresSaver(conn=pool), pool
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    saver = await stack.enter_async_context(
        AsyncSqliteSaver.from_conn_string(str(config.DATA_DIR / "checkpoints.sqlite")))
    await saver.setup()
    return saver, None


def _no_embed(texts):  # setup() only needs the vector size, never embeds anything
    raise RuntimeError("embedding is not available in this process")


async def setup_postgres(url: str) -> None:
    """Run LangGraph's saver + store migrations once (the Docker "migrate" service)."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore

    async with AsyncExitStack() as stack:
        pool = await open_pool(stack, url)
        await AsyncPostgresSaver(conn=pool).setup()
        await AsyncPostgresStore(
            conn=pool, index={"dims": config.EMBED_DIMS, "embed": _no_embed, "fields": ["text"]}
        ).setup()


def _clear() -> None:
    global _runtime
    _runtime = None
