"""Settings for the FinSight backend."""

from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

# TAVILY_API_KEY lives in <repo root>/.env (git-ignored).
load_dotenv(REPO_ROOT / ".env")

# Anchored to this file, so the server finds the index from any working directory.
INDEX_PATH = BACKEND_DIR / "faiss_index"

# Tavily cache and LangGraph checkpoints (git-ignored).
DATA_DIR = BACKEND_DIR / "data"

LLM_MODEL = "llama3"
LLM_TEMPERATURE = 0.2
EMBED_MODEL = "nomic-embed-text"

TOP_K = 4
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
SEPARATORS = ["\n\n", "\n", ".", ","]

CORS_ORIGINS = ["http://localhost:3000"]

# Web-search fallback.
# If the closest FAISS chunk is farther than this (L2 distance), the local draft is
# treated as unreliable and the user is offered a web search. Calibrated on nomic-embed-text
# with a Wikipedia article: on-topic questions scored 0.39-0.58, off-topic ones 0.80-1.20.
MAX_DISTANCE = 0.75
WEB_MAX_RESULTS = 5
WEB_SEARCH_DEPTH = "basic"
