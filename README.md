# FinSight — AI Financial Research Terminal

Local RAG-based financial research with a Next.js dashboard, FastAPI, LangGraph, Ollama and FAISS. Add your own article URLs and get answers grounded in those sources, with links back to them. Everything runs on your machine; no API keys are needed.

![App Screenshot](https://cdn.corenexis.com/f/OEE6KQbCV3b.png)

## Features

- Paste up to 10 news or market-analysis URLs; the article text is fetched with LangChain's `WebBaseLoader`.
- Text is split into overlapping chunks, embedded with Ollama `nomic-embed-text` and indexed in FAISS.
- The index is saved to `faiss_index/` and reloaded automatically when the API restarts.
- Questions are answered by a local Ollama `llama3` model via a LangGraph `retrieve → generate` workflow, with the source URLs listed under the answer.

## Setup

1. Install [Ollama](https://ollama.com) and pull the models:

```powershell
ollama pull llama3
ollama pull nomic-embed-text
```

2. Install the API and dashboard dependencies:

```powershell
pip install -r requirements.txt
npm install
```

3. Start the research API from the repo root:

```powershell
uvicorn app:app --reload --port 8000
```

4. Start the Next.js dashboard in a second terminal:

```powershell
npm run dev
```

Open `http://localhost:3000`.

## Usage

1. Under **Research collection**, paste one or more article URLs and click **Create research collection**.
2. Wait for the "passages indexed" message. Note: each new collection **replaces** the previous index.
3. Ask a question in the **Research terminal**. The answer appears with its source links.

## Architecture

```
Next.js dashboard (app/page.tsx, :3000)
        │  POST /api/process  {urls}
        │  POST /api/query    {question}
        ▼
FastAPI (app.py, :8000)
  process: WebBaseLoader → text splitter → nomic-embed-text → FAISS (saved to faiss_index/)
  qu
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status and whether an index is loaded |
| `POST /api/process` | Fetch, chunk, embed and index URLs |
| `POST /api/query` | Answer a question from the indexed sources |

## Project layout

| Path | Contents |
|---|---|
| `app.py` | FastAPI backend and LangGraph RAG pipeline |
| `app/` | Next.js dashboard (single page) |
| `faiss_index/` | Saved FAISS index |
| `rag.ipynb` | Original notebook prototype of the pipeline (uses `UnstructuredURLLoader`) |
| `Presentation/` | College presentation deck and the script that generates it |
| `postcleanup.md` | Record of cleaned-up files and known issues |

## Tech stack

| Package | Role |
|---|---|
| LangChain + `langchain-ollama` | Loaders, splitters, Ollama LLM and embeddings |
| LangGraph | Retrieve → generate workflow |
| FAISS (`faiss-cpu`) | Vector similarity search |
| FastAPI + Uvicorn | Local research API |
| Next.js 15 / React 19 / TypeScript | Dashboard UI |
| Ollama (`llama3`, `nomic-embed-text`) | Local LLM and embedding models |
