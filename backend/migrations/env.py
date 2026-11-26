"""Alembic environment: FinSight's own tables on PostgreSQL (sync psycopg driver)."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from backend import idempotency, threads  # noqa: F401  (idempotency registers its table)

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = threads.metadata
# Only FinSight's tables: LangGraph's tables share the database (see alembic_include_object).
CONTEXT_OPTS = {"target_metadata": target_metadata, "compare_type": True,
                "include_object": threads.alembic_include_object}


def _url() -> str:
    url = (config.attributes.get("database_url")
           or os.getenv("MIGRATION_DATABASE_URL", "").strip()
           or os.getenv("DATABASE_URL", "").strip())
    if not url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("Set MIGRATION_DATABASE_URL or DATABASE_URL to a postgresql:// URL to run migrations.")
    return "postgresql+psycopg://" + url.split("://", 1)[1]


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"}, **CONTEXT_OPTS)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:  # tests pass an open connection
        context.configure(connection=connectable, **CONTEXT_OPTS)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, **CONTEXT_OPTS)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
