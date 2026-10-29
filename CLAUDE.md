# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FinSight is a local RAG financial research tool: the user supplies article URLs, then asks questions answered only from those articles, with source links. Everything runs locally (Ollama models, FAISS index); no API keys are needed.

## Commands

Prerequisite: Ollama running locally with `llama3` and `nomic-embed-text` pulled (`ollama pull llama3`, `ollama pull nomic-embed-text`).

```powershell
pip install -r requirements.txt
npm install

uvicorn app:app --reload --port 8000   # backend API (run from repo root)
npm run dev                            # Next.js dashboard at http://localhost:3000
npm run build                          # production build / type check
```

There are no tests or linters configured. `GET http://localhost:8000/health` reports whether an index is loaded (`collection_ready`).

## Architecture

Two processes talk over HTTP: a Next.js frontend and a FastAPI backend.

- **`app.py`** is the whole backend. State lives in the module-level `retriever` global, which is the only link between the two endpoints.
  - `POST /api/process`: `WebBaseLoader` → `RecursiveCharacterTextSplitter` (1000 chars, 200 overlap) → `FAISS.from_documents` with `nomic-embed-text` → `save_local("faiss_index")`. It **replaces** the entire index and the global `retriever` (top-k = 4); it does not append.
  - `POST /api/query`: runs a LangGraph `StateGraph` (`retrieve` → `generate`) over `RAGState`, using `llama3` at temperature 0.2. It returns the answer plus deduplicated source URLs. It returns 409 if nothing is indexed.
  - On startup, `load_saved_index` reloads `faiss_index/` into `retriever`, so the index survives restarts. The relative `INDEX_PATH` means the server must be started from the repo root.
  - CORS only allows `http://localhost:3000`.
- **`app/`** is the Next.js 15 App Router frontend (React 19, TypeScript). The entire UI is one client component in `app/page.tsx`. It calls the API at `NEXT_PUBLIC_API_URL`, defaulting to `http://localhost:8000`, and surfaces the backend's `detail` message on errors.
- **`faiss_index/`** (`index.faiss` + `index.pkl`) is git-ignored (a fresh clone has no collection) and is rewritten on every "process URLs" call. It is loaded with `allow_dangerous_deserialization=True`, so only load indexes you created.
- **`rag.ipynb`** is the original notebook prototype of the same pipeline, kept for reference. It uses `UnstructuredURLLoader` (hence `unstructured` in `requirements.txt`) and is not imported by the app.
- **`Presentation/`** holds a college presentation (`generate_deck.py` builds `FinSight_College_Presentation.pptx`). It is unrelated to the app runtime.

## Gotchas

- `requirements.txt` includes packages neither `app.py` nor `rag.ipynb` imports (`sentence-transformers`, `pandas`, `numpy`, `tiktoken`).
- `postcleanup.md` records files removed from the repo and known issues not yet fixed; update it when cleaning up further.
