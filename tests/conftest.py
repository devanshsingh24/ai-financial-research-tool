"""Offline fixtures: fake LLM, fake embeddings, fake retrieval and Tavily, temp data dir.

No Ollama, no Tavily credits. Restarting the server = leaving and re-entering a
TestClient context: the lifespan closes and reopens every database connection.

Postgres variants run when TEST_DATABASE_URL is set (the Docker "tests" service sets it):
the schema is migrated once per session and every test starts from empty tables.
"""

import os
from contextlib import contextmanager
from typing import Dict

# Before importing the backend: never pick up a real DATABASE_URL from .env, no cleanup loop.
os.environ["DATABASE_URL"] = ""
os.environ["CLEANUP_INTERVAL_HOURS"] = "0"
# Build the (patched-over) models for local Ollama even if .env selects Groq; no key is read.
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["MAX_DISTANCE"] = ""
os.environ["FINSIGHT_DATA_DIR"] = ""
os.environ["INDEX_PATH"] = ""
os.environ["DB_SETUP_ON_START"] = "true"
os.environ["FAIL_RUNNING_ON_START"] = "true"
os.environ.setdefault("USER_AGENT", "finsight-tests")

import pytest
from fastapi.testclient import TestClient

import fakes
from backend import config, web_cache
from backend.main import app

BACKENDS = ["sqlite", "postgres"]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

# Rows written by the app; emptied before each Postgres test (tables may not exist yet).
_APP_TABLES = ["threads", "idempotency_keys", "checkpoints", "checkpoint_blobs",
               "checkpoint_writes", "store_vectors", "store"]
_migrated = False


def requires_postgres() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set (Postgres variant)")
    return TEST_DATABASE_URL


def reset_postgres(url: str) -> None:
    """Migrate once per session, then empty every app table."""
    global _migrated
    import psycopg

    from backend import migrate

    if not _migrated:
        migrate.upgrade(url)
        _migrated = True
    with psycopg.connect(url, autocommit=True) as conn:
        existing = [t for t in _APP_TABLES
                    if conn.execute("SELECT to_regclass(%s)", (f"public.{t}",)).fetchone()[0]]
        if existing:
            conn.execute("TRUNCATE " + ", ".join(existing))


@pytest.fixture
def brain(tmp_path, monkeypatch) -> fakes.Brain:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(web_cache, "_conn", None)
    yield fakes.install(monkeypatch.setattr)
    if web_cache._conn is not None:
        web_cache._conn.close()


@pytest.fixture(params=BACKENDS)
def backend(request, brain, monkeypatch) -> str:
    """Parametrize a test over SQLite and (when TEST_DATABASE_URL is set) Postgres."""
    if request.param == "postgres":
        url = requires_postgres()
        reset_postgres(url)
        monkeypatch.setattr(config, "DATABASE_URL", url)
    return request.param


@pytest.fixture
def server():
    """server() -> context manager running the app; use it again to 'restart'."""

    @contextmanager
    def run():
        with TestClient(app) as client:
            yield client

    return run


def user(name: str) -> Dict[str, str]:
    return {"X-User-Id": name}
