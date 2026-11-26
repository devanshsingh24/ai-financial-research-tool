"""Settings for the FinSight backend."""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

# TAVILY_API_KEY lives in <repo root>/.env (git-ignored).
load_dotenv(REPO_ROOT / ".env")

# Model provider: "ollama" (local, default) or "groq" (Groq API for text + local Hugging Face
# embeddings; for a deployed server). Set LLM_PROVIDER in .env or the host's environment;
# backend/llm.py builds the models.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower() or "ollama"
if LLM_PROVIDER not in ("ollama", "groq"):
    raise ValueError(f"LLM_PROVIDER must be 'ollama' or 'groq', not {LLM_PROVIDER!r}")

# Each provider keeps its own data, because collections and memories are tied to the
# embedding model. Ollama: backend/faiss_index/ + backend/data/ (as before).
# Groq: backend/data/groq/ (collection, threads, memories, Tavily cache). Git-ignored.
if LLM_PROVIDER == "ollama":
    # Anchored to this file, so the server finds the index from any working directory.
    INDEX_PATH = BACKEND_DIR / "faiss_index"
    DATA_DIR = BACKEND_DIR / "data"   # Tavily cache, checkpoints, registry, memories
else:
    DATA_DIR = BACKEND_DIR / "data" / LLM_PROVIDER
    INDEX_PATH = DATA_DIR / "faiss_index"
# Containers set these explicitly (FINSIGHT_DATA_DIR=/data, INDEX_PATH=/data/faiss_index:
# named volumes).
if os.getenv("FINSIGHT_DATA_DIR", "").strip():
    DATA_DIR = Path(os.environ["FINSIGHT_DATA_DIR"].strip())
    INDEX_PATH = DATA_DIR / "faiss_index"
if os.getenv("INDEX_PATH", "").strip():
    INDEX_PATH = Path(os.environ["INDEX_PATH"].strip())

# Where the Ollama server runs (the "ollama" container in Docker).
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()

LLM_MODEL = "llama3"                  # Ollama
LLM_TEMPERATURE = 0.2
EMBED_MODEL = "nomic-embed-text"      # Ollama

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()   # never commit; never sent to the browser
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
# JSON mode (response_format=json_object) for the JSON steps: on/off. Verified with
# gpt-oss-120b, streamed too. Turn it off for a model that rejects it; the prompts ask for
# JSON and web_research._parse_json extracts it either way.
GROQ_JSON_MODE = os.getenv("GROQ_JSON_MODE", "on").strip().lower() or "on"
# Groq has no embedding models: embeddings run locally on the CPU (sentence-transformers).
HF_EMBED_MODEL = os.getenv("HF_EMBED_MODEL", "BAAI/bge-small-en-v1.5").strip()   # 384 dims

TOP_K = 4
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
SEPARATORS = ["\n\n", "\n", ".", ","]

# Comma-separated; a deployment adds its frontend URL.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# Web-search fallback.
# If the closest FAISS chunk is farther than this (L2 distance), the local draft is
# treated as unreliable and the user is offered a web search. Calibrated on nomic-embed-text
# with a Wikipedia article: on-topic questions scored 0.39-0.58, off-topic ones 0.80-1.20.
# Re-checked on the Nvidia Wikipedia article (222 chunks): nomic on-topic 0.37-0.68, off-topic
# 1.02-1.20; bge-small-en-v1.5 (groq provider) on-topic 0.24-0.60, off-topic 0.91-1.12. So 0.75
# suits both. Another embedding model needs its own check: set MAX_DISTANCE in the environment.
MAX_DISTANCE = float(os.getenv("MAX_DISTANCE", "").strip() or 0.75)
WEB_MAX_RESULTS = 5
WEB_SEARCH_DEPTH = "basic"


# Persistence, threads and memory. All overridable from <repo root>/.env.
def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


# Unset -> SQLite files under DATA_DIR (dev). postgresql://... -> Postgres for the
# checkpointer, the long-term memory store and the thread registry (prod).
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

CHECKPOINT_TTL_DAYS = _int("CHECKPOINT_TTL_DAYS", 30)       # delete threads idle this long
CLEANUP_INTERVAL_HOURS = _int("CLEANUP_INTERVAL_HOURS", 6)  # 0 disables the background job
MEMORY_SEARCH_LIMIT = _int("MEMORY_SEARCH_LIMIT", 5)        # memories read at thread start
# Vector size of the embedding model (sizes the memory-store index): nomic 768, bge-small 384.
EMBED_DIMS = _int("EMBED_DIMS", 384 if LLM_PROVIDER == "groq" else 768)
MAX_MESSAGES = _int("MAX_MESSAGES", 12)    # summarize older turns once a thread has more
KEEP_MESSAGES = _int("KEEP_MESSAGES", 6)   # most recent messages kept verbatim


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    return default if not value else value in ("1", "true", "yes", "on")


# Postgres (production). The psycopg pool is per worker process.
DB_POOL_MIN = _int("DB_POOL_MIN", 1)
DB_POOL_MAX = _int("DB_POOL_MAX", 10)
DB_CONNECT_TIMEOUT = _int("DB_CONNECT_TIMEOUT", 30)  # seconds to wait for the DB at startup
# Run LangGraph's saver/store setup() at startup. Docker runs it once in the "migrate"
# service (as the DB owner) and turns it off for the API, whose DB user can't run DDL.
DB_SETUP_ON_START = _bool("DB_SETUP_ON_START", True)
# Mark threads left "running" by a crash as failed at startup. With several workers this
# must run once per container start (backend/prestart.py), not in every worker.
FAIL_RUNNING_ON_START = _bool("FAIL_RUNNING_ON_START", True)
IDEMPOTENCY_TTL_HOURS = _int("IDEMPOTENCY_TTL_HOURS", 24)  # how long Idempotency-Key replies are kept
