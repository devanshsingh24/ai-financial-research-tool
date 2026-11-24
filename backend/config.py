"""Settings for the FinSight backend."""

from pathlib import Path

# Anchored to this file, so the server finds the index from any working directory.
INDEX_PATH = Path(__file__).resolve().parent / "faiss_index"

LLM_MODEL = "llama3"
LLM_TEMPERATURE = 0.2
EMBED_MODEL = "nomic-embed-text"

TOP_K = 4
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
SEPARATORS = ["\n\n", "\n", ".", ","]

CORS_ORIGINS = ["http://localhost:3000"]
