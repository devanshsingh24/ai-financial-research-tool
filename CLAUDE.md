# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FinSight is a local RAG financial research tool: the user supplies article URLs, then asks questions answered only from those articles, with source links. Everything runs locally (Ollama models, FAISS index); no API keys are needed.

## Commands

Prerequisite: Ollama running locally with `llama3` and `nomic-embed-text` pulled (`ollama pull llama3`, `ollama pull nomic-embed-text`).

```powershell
pip install -r backend/requirements.txt
cd frontend; npm install

uvicorn backend.main:app --reload --port 8000   # backend API (run from repo root; `backend` is a package)
npm run dev                                     # in frontend/: Next.js dashboard at http://localhost:3000
npm run build                                   # in frontend/: production build / type check
```

There are no tests or linters configured. `GET http://localhost:8000/health` reports whether an index is loaded (`collection_ready`).

## Architecture

Two processes talk over HTTP: a Next.js frontend (`frontend/`) and a FastAPI backend (`backend/`).

- **`backend/`** is a Python package (relative imports, so run it as `backend.main:app`).
  - `config.py`: model names, temperature (0.2), chunking (1000 chars, 200 overlap), top-k (4), CORS origins (`http://localhost:3000` only) and `INDEX_PATH`, which is anchored to the file (`backend/faiss_index/`), so the working directory doesn't matter.
  - `schemas.py`: Pydantic request/response models (1–10 URLs; questions 3–1500 chars).
  - `rag.py`: LLM, embeddings, prompt and the LangGraph `StateGraph` (`retrieve` → `generate`) over `RAGState`. The active retriever is the module-private `_retriever`, the only state shared between the endpoints. Access it only through `is_ready()`, `load_saved_index()`, `build_index(urls)` and `answer(question)`. Never `from .rag import _retriever`, because that binding goes stale when the index is rebuilt.
  - `main.py`: FastAPI app, a `lifespan` handler that calls `rag.load_saved_index()` (so the index survives restarts), and routes:
    - `POST /api/process` calls `build_index`, which runs `WebBaseLoader` → splitter → `FAISS.from_documents` → `save_local`. It **replaces** the whole index; it does not append. Errors return 422.
    - `POST /api/query` calls `answer`, which returns the answer plus deduplicated source URLs. It returns 409 if nothing is indexed and 500 on generation errors.
    - `POST /api/query/stream` (used by the dashboard) calls `rag.stream_answer`, which reuses the `retrieve` node and the shared `chain`, but calls `chain.stream` instead of going through the graph. It returns `text/event-stream` with `event:` + JSON `data:` blocks: `sources` (once, deduped), `token` (repeated), then `done`, or `error` with `{detail}` if something fails mid-stream. The 409 and 422 errors happen before streaming starts, so they are normal JSON responses. If you change the prompt or retrieval, both paths pick it up; graph-only nodes would not apply to the stream.
- **`frontend/`** is the Next.js 15 App Router app (React 19, TypeScript). The entire UI is one client component in `frontend/app/page.tsx`. It reads the answer stream with `fetch` + `ReadableStream` (`EventSource` can't POST), parses the events with `parseEvent`, and shows a blinking `.cursor` while `isStreaming`. It calls the API at `NEXT_PUBLIC_API_URL`, defaulting to `http://localhost:8000`, and surfaces the backend's `detail` message on errors.
- **`backend/faiss_index/`** (`index.faiss` + `index.pkl`) is git-ignored (a fresh clone has no collection) and is rewritten on every "process URLs" call. It is loaded with `allow_dangerous_deserialization=True`, so only load indexes you created.
- **`docs/`** holds `arch.md` (detailed architecture with Mermaid diagrams) and `postcleanup.md`.
- **`notebooks/rag.ipynb`** is the original notebook prototype of the same pipeline, kept for reference. It uses `UnstructuredURLLoader` (hence `unstructured` in `requirements.txt`), writes its own `faiss_index/` next to itself, and is not imported by the app.
- **`Presentation/`** holds a college presentation (`generate_deck.py` builds `FinSight_College_Presentation.pptx`). It is unrelated to the app runtime, and its slide text still refers to the old `app.py`.

## Gotchas

- `backend/requirements.txt` includes packages neither the backend nor the notebook imports (`sentence-transformers`, `pandas`, `numpy`, `tiktoken`).
- `docs/postcleanup.md` records files removed or moved and known issues not yet fixed; update it when cleaning up further.
- If you change backend behaviour or file layout, keep `README.md` and `docs/arch.md` in sync.
