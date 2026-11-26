"""Alembic migrations apply cleanly to an empty PostgreSQL database, match the SQLAlchemy
models exactly (no drift), downgrade to nothing and upgrade again. Postgres only."""

import asyncio
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from conftest import requires_postgres
from sqlalchemy import create_engine, inspect

from backend import idempotency, migrate, persistence, threads  # noqa: F401  (registers idempotency_keys)

SCRATCH_DB = "finsight_migrations_test"



def _with_db(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture
def empty_db():
    url = requires_postgres()
    admin = _with_db(url, "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    yield _with_db(url, SCRATCH_DB)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')


def _engine(url: str):
    return create_engine("postgresql+psycopg://" + url.split("://", 1)[1])


def _indexes(insp, table: str) -> dict:
    return {ix["name"]: ix["column_names"] for ix in insp.get_indexes(table)}


def test_migrations_apply_cleanly(empty_db):
    cfg = migrate.alembic_config(empty_db)
    heads = set(ScriptDirectory.from_config(cfg).get_heads())

    migrate.upgrade(empty_db)
    # The real database also holds LangGraph's tables; autogenerate must leave them alone.
    asyncio.run(persistence.setup_postgres(empty_db))
    command.check(cfg)  # raises if autogenerate would emit any operation (e.g. DROP TABLE checkpoints)
    engine = _engine(empty_db)
    try:
        with engine.connect() as conn:
            insp = inspect(conn)
            assert {"threads", "idempotency_keys", "alembic_version"} <= set(insp.get_table_names())
            assert _indexes(insp, "threads") == {
                "ix_threads_user_updated": ["user_id", "updated_at"],
                "ix_threads_updated_at": ["updated_at"],
            }
            assert _indexes(insp, "idempotency_keys") == {
                "ix_idempotency_keys_user_id": ["user_id"],
                "ix_idempotency_keys_updated_at": ["updated_at"],
            }
            assert insp.get_pk_constraint("idempotency_keys")["constrained_columns"] == ["user_id", "key"]
            assert {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "store", "store_vectors"} <= set(
                insp.get_table_names())
            context = MigrationContext.configure(
                conn, opts={"compare_type": True, "include_object": threads.alembic_include_object})
            assert set(context.get_current_heads()) == heads
            # The migration creates exactly what the models describe.
            assert compare_metadata(context, threads.metadata) == []

        command.downgrade(cfg, "base")
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            assert not {"threads", "idempotency_keys"} & tables
            assert "checkpoints" in tables  # downgrading ours never touches LangGraph's

        migrate.upgrade(empty_db)  # and back up again, cleanly
        migrate.upgrade(empty_db)  # a second upgrade is a no-op
        with engine.connect() as conn:
            assert set(MigrationContext.configure(conn).get_current_heads()) == heads
    finally:
        engine.dispose()
