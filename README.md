# FinSight — AI Financial Research Terminal

Local RAG-based financial research with a Next.js dashboard, FastAPI, LangGraph, Ollama and FAISS. Add your own article URLs and get answers grounded in those sources, with links back to them. Everything runs on your machine; no API keys are needed.

![App Screenshot](https://cdn.corenexis.com/f/OEE6KQbCV3b.png)

## Features

- Paste up to 10 news or market-analysis URLs; the article text is fetched with LangChain's `WebBaseLoader`.
- Text is split into overlapping chunks, embedded with Ollama `nomic-embed-text` and indexed in FAISS.
- The index is saved to `backend/faiss_index/` and reloaded automatically when the API restarts.
- Questions are answered by a local Ollama `llama3` model via a LangGraph `retrieve → generate` workflow, with the source URLs listed under the answer.

## Setup

1. Install [Ollama](https://ollama.com) and pull the models:

```powershell
ollama pull llama3
ollama pull nomic-embed-text
```

2. Install the API and dashboard dependencies:

```powershell
pip install -r backend/requirements.txt
cd frontend; npm install; cd ..
```

3. Start the research API from the repo root:

```powershell
uvicorn backend.main:app --reload --port 8000
```

4. Start the Next.js dashboard in a second terminal:

```powershell
cd frontend
npm run dev
```

Open `http://localhost:3000`.

## Usage

1. Under **Research collection**, paste one or more article URLs and click **Create research collection**.
2. Wait for the "passages indexed" message. Note: each new collection **replaces** the previous index.
3. Ask a question in the **Research terminal**. The answer appears with its source links.

## Architecture

```
Next.js dashboard (frontend/app/page.tsx, :3000)
        │  POST /api/process  {urls}
        │  POST /api/query    {question}
        ▼
FastAPI (backend/main.py, :8000)
  process: WebBaseLoader → text splitter → nomic-embed-text → FAISS (saved to backend/faiss_index/)
  query:   LangGraph retrieve (top 4 chunks) → generate (llama3) → answer + source URLs
```

See [docs/arch.md](docs/arch.md) for the detailed architecture.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status and whether an index is loaded |
| `POST /api/process` | Fetch, chunk, embed and index URLs |
| `POST /api/query` | Answer a question from the indexed sources |

## Project layout

| Path | Contents |
|---|---|
| `backend/main.py` | FastAPI app, CORS, startup and routes |
| `backend/rag.py` | Indexing and the LangGraph `retrieve → generate` pipeline |
| `backend/schemas.py` | Pydantic request/response models |
| `backend/config.py` | Models, chunking, top-k, index path and CORS settings |
| `backend/requirements.txt` | Python dependencies |
| `backend/faiss_index/` | Saved FAISS index (git-ignored) |
| `frontend/` | Next.js dashboard (single page in `frontend/app/`) |
| `docs/arch.md` | Detailed architecture with diagrams |
| `docs/postcleanup.md` | Record of cleaned-up files and known issues |
| `notebooks/rag.ipynb` | Original notebook prototype of the pipeline (uses `UnstructuredURLLoader`) |
| `Presentation/` | College presentation deck and the script that generates it |

## Tech stack

| Package | Role |
|---|---|
| LangChain + `langchain-ollama` | Loaders, splitters, Ollama LLM and embeddings |
| LangGraph | Retrieve → generate workflow |
| FAISS (`faiss-cpu`) | Vector similarity search |
| FastAPI + Uvicorn | Local research API |
| Next.js 15 / React 19 / TypeScript | Dashboard UI |
| Ollama (`llama3`, `nomic-embed-text`) | Local LLM and embedding models |
