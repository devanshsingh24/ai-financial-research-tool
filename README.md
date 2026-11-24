# FinSight — AI Financial Research Terminal

Local RAG-based financial research with a Next.js dashboard, FastAPI, LangGraph, Ollama and FAISS. Add your own article URLs and get answers grounded in those sources, with links back to them. Everything runs on your machine; no API keys are needed.

![App Screenshot](https://cdn.corenexis.com/f/OEE6KQbCV3b.png)

## Features

- Paste up to 10 news or market-analysis URLs; the article text is fetched with LangChain's `WebBaseLoader`.
- Text is split into overlapping chunks, embedded with Ollama `nomic-embed-text` and indexed in FAISS.
- The index is saved to `backend/faiss_index/` and reloaded automatically when the API restarts.
- Questions are answered by a local Ollama `llama3` model via a LangGraph `retrieve → generate` workflow, with the source URLs listed under the answer.
- Answers stream in live, word by word with a typing cursor, like a chat app. The source links appear as soon as retrieval finishes, before the answer starts.
- **Web-search fallback** (dashboard toggle + API). `/ask` answers from your sources for free. If the answer needs fresh data or the sources don't cover it, the request pauses and asks *"Search the web? (1 credit)"*. `/confirm` then runs **one** Tavily search and returns an answer citing the web URLs, or returns the local draft marked as unverified. Results are cached, so repeating a search costs 0 credits.

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

3. Optional, for web search: copy `.env.example` to `.env` and add your [Tavily](https://tavily.com) key (`TAVILY_API_KEY=...`). `.env` is git-ignored.

4. Start the research API from the repo root:

```powershell
uvicorn backend.main:app --reload --port 8000
```

5. Start the Next.js dashboard in a second terminal:

```powershell
cd frontend
npm run dev
```

Open `http://localhost:3000`.

## Usage

1. Under **Research collection**, paste one or more article URLs and click **Create research collection**.
2. Wait for the "passages indexed" message. Note: each new collection **replaces** the previous index.
3. Ask a question in the **Research terminal**. The answer appears with its source links.

### Web search (Web fallback toggle)

The **Web fallback** switch sits at the top right of the Research terminal and is on by default.

- **On:** FinSight first answers from your sources (0 credits, tagged **YOUR SOURCES**). If they can't answer, for example on today's price or recent news, a card shows the **draft** with two buttons:
  - **Search the web**: runs one Tavily search (1 credit, or 0 if cached). The answer is tagged **WEB · 1 CREDIT**, shows the search query, and lists numbered sources `[1]…[5]` that match the citations in the text.
  - **No, use draft**: no search. The draft is shown and tagged **UNVERIFIED**.
- **Off:** answers stream live from your sources only. No web search is offered.

Web search needs `TAVILY_API_KEY` in `.env` (see Setup).

## Architecture

```
Next.js dashboard (frontend/app/page.tsx, :3000)
        │  POST /api/process  {urls}
        │  POST /api/query/stream {question}  → streamed events
        ▼
FastAPI (backend/main.py, :8000)
  process: WebBaseLoader → text splitter → nomic-embed-text → FAISS (saved to backend/faiss_index/)
  query:   retrieve (top 4 chunks) → sources event → llama3 tokens streamed → done
```

See [docs/arch.md](docs/arch.md) for the detailed architecture.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status and whether an index is loaded |
| `POST /api/process` | Fetch, chunk, embed and index URLs |
| `POST /api/query` | Answer a question from the indexed sources (whole answer as JSON) |
| `POST /api/query/stream` | Same, streamed as Server-Sent Events: `sources`, many `token`s, then `done` (or `error`). Used by the dashboard. |
| `POST /ask` | `{question}` → `{status: "answered", answer, sources}` or `{status: "needs_confirmation", thread_id, prompt, draft}` |
| `POST /confirm` | `{thread_id, approve}` → resumes a paused `/ask`: approve runs one web search, decline returns the draft marked "not verified" |

Example (PowerShell):

```powershell
$r = Invoke-RestMethod -Method Post http://localhost:8000/ask -ContentType application/json -Body '{"question":"What is Nvidia''s stock price today?"}'
$r.status   # needs_confirmation
Invoke-RestMethod -Method Post http://localhost:8000/confirm -ContentType application/json -Body (@{thread_id=$r.thread_id; approve=$true} | ConvertTo-Json)
```

## Project layout

| Path | Contents |
|---|---|
| `backend/main.py` | FastAPI app, CORS, startup and routes |
| `backend/rag.py` | Indexing and the LangGraph `retrieve → generate` pipeline |
| `backend/schemas.py` | Pydantic request/response models |
| `backend/config.py` | Models, chunking, top-k, index path, CORS and web-search settings; loads `.env` |
| `backend/web_research.py` | Web-search fallback graph (draft → confirm → one Tavily search), SQLite checkpointer |
| `backend/web_cache.py` | SQLite cache of Tavily results |
| `backend/data/` | Tavily cache + LangGraph checkpoints (git-ignored) |
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
