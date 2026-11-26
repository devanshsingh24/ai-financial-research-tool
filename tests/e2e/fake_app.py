"""The real FinSight app with only the models faked, for end-to-end tests in Docker.

Everything else is the production code path: uvicorn workers, the psycopg pool,
AsyncPostgresSaver / AsyncPostgresStore, the Alembic-migrated registry, claims and
idempotency. Only the LLM, embeddings, retrieval and Tavily are the offline fakes from
tests/fakes.py, so runs are deterministic and cost nothing.
Run by docker-compose.e2e.yml with APP_MODULE=tests.e2e.fake_app:app.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for fakes)

import fakes  # noqa: E402

fakes.install(setattr)

from backend.main import app  # noqa: E402,F401
