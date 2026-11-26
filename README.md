# FinSight — AI Financial Research Terminal

Local RAG-based financial research with a Next.js dashboard, FastAPI, LangGraph, Ollama and FAISS. Add your own article URLs and get answers grounded in those sources, with links back to them. Everything runs on your machine with no API keys needed. A deployed server can switch to the Groq API with one setting.

![App Screenshot](https://cdn.corenexis.com/f/OEE6KQbCV3b.png)

## Features

- Paste up to 10 news or market-analysis URLs; the article text is fetched with LangChain's `WebBaseLoader`.
- Text is split into overlapping chunks, embedded with Ollama `nomic-embed-text` and indexed in FAISS.
- The index is saved to `backend/faiss_index/` and reloaded automatically when the API restarts.
- Questions are answered by a local Ollama `llama3` model via a LangGraph `retrieve → generate` workflow, with the source URLs listed under the answer.
- Answers stream in live, word by word with a typing cursor, like a chat app. The source links appear as soon as retrieval finishes, before the answer starts.
- **Web-search fallback** (dashboard toggle + API). `/ask` answers from your sources for free. If the answer needs fresh data or the sources don't cover it, the request pauses at a **search gate** that shows the planned query: approve it, edit it, or decline. An approved search runs **one** Tavily search, then pauses again at a **report gate**: approve the web report, edit it, or reject it for the local draft marked as unverified. Results are cached, so repeating a search costs 0 credits.
- **Persistent threads.** Every question runs in a LangGraph thread checkpointed after each step (SQLite in dev, Postgres in prod). Threads survive server restarts, including a thread paused at a gate, and a failed run resumes from its last checkpoint without redoing finished steps (or paying for a search twice). Threads are multi-turn, with older turns summarized automatically. Each thread belongs to a user, given in the `X-User-Id` header (no auth yet; defaults to `local`).
- **Time travel.** Browse a thread's checkpoints, replay from any one of them, or fork it with edited values.
- **Model provider switch.** Local Ollama by default; set `LLM_PROVIDER=groq` with a `GROQ_API_KEY` on a deployed server to use the Groq API for text (`openai/gpt-oss-120b`) and Hugging Face `bge-small-en-v1.5` for embeddings, with no code change (see below).
- **Long-term memory.** Per-user memories (preferences, tracked companies, report summaries) are stored with an embedding index and recalled at the start of new threads.

## Setup

1. Install [Ollama](https://ollama.com) and pull the models:

```powershell
ollama pull llama3
ollama pull nomic-embed-text
```

2. Install the API and dashboard dependencies:

```powershell
pip install -r requirements-dev.txt -c backend/constraints.txt   # API + tests + notebook extras
cd frontend; npm install; cd ..
```

`backend/requirements.txt` lists only what the API needs at runtime (and what the Docker image installs). `backend/constraints.txt` pins the versions known to work together.

Prefer containers? See [Run with Docker](#run-with-docker-postgresql): the API, PostgreSQL and the cleanup job in one command.

3. Optional: copy `.env.example` to `.env` (git-ignored) and set:
   - `TAVILY_API_KEY` for web search ([Tavily](https://tavily.com)).
   - `DATABASE_URL=postgresql://…` to store checkpoints, threads and memories in Postgres instead of the SQLite files in `backend/data/`. The memory index needs the `pgvector` extension. On Windows, psycopg's async mode doesn't support the default Proactor event loop, so use Postgres through [Docker](#run-with-docker-postgresql) (or Linux/WSL).
   - `LLM_PROVIDER=groq` + `GROQ_API_KEY` to use Groq instead of Ollama (see [Deploying with Groq](#deploying-with-groq)).
   - `CHECKPOINT_TTL_DAYS` and `CLEANUP_INTERVAL_HOURS` for checkpoint cleanup, plus the memory settings (see `.env.example`).

4. Start the research API from the repo root:

```powershell
uvicorn backend.main:app --reload --reload-dir backend --port 8000
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

Answers stream in live, word by word, in **both** modes.

- **On:** the answer from your sources streams in (0 credits, tagged **YOUR SOURCES**). If your sources can't answer, for example on today's price or recent news, the streamed text is tagged **DRAFT · NOT VERIFIED**. Under it are the planned search query (editable) and two buttons:
  - **Search the web**: runs one Tavily search for the query shown (1 credit, or 0 if cached). The status line shows the search (for example *Searching the web: "Nvidia stock price today" · finance · past day*), then the web report streams in, tagged **WEB · AWAITING YOUR REVIEW**. Then:
    - **Approve report** keeps it, tagged **WEB · 1 CREDIT**, with numbered sources `[1]…[5]` that match the citations.
    - **Edit** lets you rewrite it before saving.
    - **Reject** shows the local draft instead, tagged **UNVERIFIED**.
  - **No, use draft**: no search. The draft stays, tagged **UNVERIFIED**.
- **Off:** answers stream from your sources only. No web search is offered.

Web search needs `TAVILY_API_KEY` in `.env` (see Setup).

### Research threads (persistence panel)

With web fallback on, every question is saved in a thread. The terminal shows which thread you're in (**THREAD** chip): the next question is a follow-up in that thread, or **+ New** starts a fresh one. The **Research threads** panel under the terminal (also linked from the nav) shows:

- **Your threads**, with their status: NEW, RUNNING, AWAITING YOU (paused at a gate), DONE or FAILED. Click one to open it in the terminal, including any pending gate. Gates survive server restarts.
- **The open thread's details:**
  - the conversation, with older turns summarized
  - the long-term memories recalled when it started
  - **Retry from checkpoint** for a failed run, and **Delete**
- **Checkpoint history**, showing every saved step of the graph:
  - **Replay** re-runs from that step.
  - **Fork** (on "Before: Retrieve sources") re-runs the turn with an edited question. Both create a new branch; the old one stays in the history.
- **USER** field: threads and memories are scoped to this ID (sent as `X-User-Id`; there's no sign-in yet). Change it to see another user's empty workspace.

Sources-only answers (web fallback off) aren't saved to threads.

## Try Groq locally

The dashboard needs no change: it talks to whatever API runs at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`), and that API decides the model. With `GROQ_API_KEY` in `.env`:

```powershell
# 1. Check the key and models (tiny Groq calls + one local embedding; no server needed)
$env:LLM_PROVIDER="groq"; .venv/Scripts/python -m backend.llm

# 2. Stop the running API, then start it on Groq (the dashboard keeps running)
$env:LLM_PROVIDER="groq"; uvicorn backend.main:app --reload --reload-dir backend --port 8000
```

3. Refresh http://localhost:3000. The nav shows **GROQ · OPENAI/GPT-OSS-120B**, and the threads panel is empty: Groq mode keeps its own collection, threads and memories in `backend/data/groq/`, so your Ollama data stays as it was.
4. Create the collection again with your article URLs (embedded locally with `bge-small-en-v1.5`), then ask questions.
5. Back to Ollama: `Remove-Item Env:LLM_PROVIDER` (or use a new terminal) and restart the API. Your original collection and threads are back.

The first start downloads `BAAI/bge-small-en-v1.5` (~130 MB) into the Hugging Face cache; later starts load it from disk in a second or two. A variable set in the shell overrides `.env`, so `.env` can stay on Ollama. `python -m backend.llm` without `LLM_PROVIDER` checks your Ollama models the same way.

## Deploying with Groq

Locally, leave `LLM_PROVIDER` unset: llama3 and `nomic-embed-text` run in Ollama. On the deployed server, set these in the host's environment (not in a committed file):

```
LLM_PROVIDER=groq
GROQ_API_KEY=...                         # your key; stays on the server
GROQ_MODEL=openai/gpt-oss-120b           # optional (default); also openai/gpt-oss-20b, qwen/qwen3.8-27b
CORS_ORIGINS=https://your-dashboard.example
DATABASE_URL=postgresql://...            # the server's own database
```

and build the dashboard with `NEXT_PUBLIC_API_URL` set to the deployed API. `/health` then shows `"llm": "groq:openai/gpt-oss-120b"`, and the nav shows **GROQ · OPENAI/GPT-OSS-120B**.

- **Embeddings run on the server.** Groq has no embedding models, so `bge-small-en-v1.5` runs on the server's CPU through `sentence-transformers` (384-dim, normalized vectors). This pulls in PyTorch, which makes the install large (~1 GB). On Linux, install the CPU-only wheel first: `pip install torch --index-url https://download.pytorch.org/whl/cpu`. The model loads at startup, so a cold start takes a few seconds longer.
- **Create the collection on the server.** bge and nomic embeddings aren't compatible, so the server refuses an index built with the other model (`/health` → `index_problem`). Keep each provider's threads and memories in its own database for the same reason (locally this is automatic: `backend/data/groq/`).
- **Web-fallback threshold.** `MAX_DISTANCE=0.75` works for bge-small as well as nomic (on-topic questions scored 0.24–0.60, off-topic 0.91–1.12 on a test article). Re-check it only if you change `HF_EMBED_MODEL`; the uvicorn console logs `best FAISS distance …` for each question.
- **Cost and limits.** Every user's question uses your Groq key's quota and rate limits, and `X-User-Id` isn't authenticated. Add sign-in and a per-user rate limit before making it public.

## Run with Docker (PostgreSQL)

The production setup runs these services:
- **API**, as a non-root container with the embedding model baked in.
- **PostgreSQL 16 + pgvector**, for checkpoints, threads, idempotency keys and long-term memory.
- **A one-shot `migrate` service**, which applies schema changes as the database owner.
- **A `cleanup` service**, for checkpoint TTL cleanup.

The API connects as a least-privilege user that can only read and write rows. See [docs/DATABASE.md](docs/DATABASE.md) for the roles, tables, DBeaver access and inspection queries.

**1. Configure `.env`** (copy `.env.example`). Docker needs these:

```
POSTGRES_PASSWORD=...        # database owner (migrations, backups)
APP_DB_PASSWORD=...          # finsight_app: what the API uses
READONLY_DB_PASSWORD=...     # finsight_readonly: DBeaver
GROQ_API_KEY=...             # Docker defaults to LLM_PROVIDER=groq
TAVILY_API_KEY=...           # optional: web search
```

Use URL-safe random values, e.g. `python -c "import secrets; print(secrets.token_hex(24))"`. Roles and passwords are created when the database volume is first initialised. After changing them, run `bash scripts/db-roles.sh`.

**2. Run.**

| | Command |
|---|---|
| Dev: hot reload, Postgres on `127.0.0.1:5433` | `docker compose up --build -d` |
| Prod: no DB port, 2 uvicorn workers, `restart: always` | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` |
| Local models: an Ollama container (set `LLM_PROVIDER=ollama`) | `docker compose --profile local up -d`, then `docker compose exec ollama ollama pull llama3` and `… pull nomic-embed-text` |
| Health | `curl http://localhost:8000/health` returns 200 with `"db": "ok"`, or 503 if the DB is unreachable |
| Logs | `docker compose logs -f api` (also `migrate`, `cleanup`, `db`) |
| Stop (volumes kept) / remove everything | `docker compose down` / `docker compose down -v` (**deletes the data**) |

The dashboard still runs outside Docker (`cd frontend; npm run dev`) and talks to `http://localhost:8000`. In production, set `CORS_ORIGINS` to the dashboard's URL, and put the API behind a TLS reverse proxy.

**3. Tests.**

```powershell
docker compose --profile test run --rm tests       # full suite, incl. Postgres variants + migrations (database finsight_test)
$env:FINSIGHT_E2E="1"; .venv/Scripts/python -m pytest tests/e2e -q   # real container restarts (its own stack on port 8100)
```

**4. Backups** (bash: Git Bash, WSL, Linux, macOS):

```bash
bash scripts/backup.sh                                   # -> backups/finsight_YYYYmmdd_HHMMSS.dump (keeps 14)
bash scripts/restore.sh backups/finsight_20261126_120000.dump   # asks you to type the DB name; --yes skips
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml" bash scripts/backup.sh   # prod stack
```

**Notes:**
- **One provider per database.** The memory index's vector size is fixed by the provider when `migrate` first runs: 384 for Groq + bge-small, 768 for Ollama + nomic.
- **Retry-safe JSON calls.** JSON `POST` endpoints accept an `Idempotency-Key` header, so a client can retry them safely.
- **No double runs.** Concurrent requests can't run the same thread twice: the extra one gets 409.

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
| `GET /health` | 200 if the app and its database are up (503 otherwise): `db`, whether an index is loaded (or why not), persistence backend and model |
| `POST /api/process` | Fetch, chunk, embed and index URLs |
| `POST /api/query` | Answer a question from the indexed sources (whole answer as JSON) |
| `POST /api/query/stream` | Same, streamed as Server-Sent Events: `sources`, many `token`s, then `done` (or `error`). Used by the dashboard. |
| `POST /ask` | `{question}` → new thread → `{status: "answered", answer, sources}` or `{status: "needs_confirmation", thread_id, kind, prompt, draft, search}` (`kind` is `web_search` or `report`) |
| `POST /confirm` | `{thread_id, approve}` → answers the gate a paused thread is at: approve continues, `false` rejects (unverified draft) |
| `POST /ask/stream`, `POST /confirm/stream` | Streaming versions (SSE): `sources`, `status`, many `token`s, then `needs_confirmation` or `answered` (same payload as the JSON endpoints), then `done` |

**Threads API.** Every route is scoped to the `X-User-Id` header; another user's thread returns 404.

| Endpoint | Purpose |
|---|---|
| `POST /threads` `{title?}` · `GET /threads` | Create a thread · list my threads (`status`: idle, running, interrupted, completed, failed) |
| `GET /threads/{id}` · `DELETE /threads/{id}` | Thread state (messages, summary, memories, `next`, pending gate) · delete it with its checkpoints |
| `POST /threads/{id}/messages` (`/stream`) | `{question}`: ask on the thread, as its first question or a follow-up |
| `POST /threads/{id}/resume` (`/stream`) | `{action: approve\|reject\|edit, query?, draft?}`: answer the gate. `edit` + `query` at the search gate, `edit` + `draft` at the report gate. Used by the dashboard. |
| `POST /threads/{id}/retry` | Resume a failed run from its last checkpoint |
| `GET /threads/{id}/history` | Checkpoints, newest first (`?full=true` includes retrieved chunks) |
| `POST /threads/{id}/replay` `{checkpoint_id}` | Re-run from a past checkpoint (as a new branch) |
| `POST /threads/{id}/fork` `{checkpoint_id, values, as_node?, run?}` | Edit state at a past checkpoint and run the new branch |

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
| `backend/llm.py` | Builds the Ollama or Groq + Hugging Face models from `LLM_PROVIDER`; `python -m backend.llm` checks them |
| `backend/config.py` | Models, chunking, top-k, index path, CORS and web-search settings; loads `.env` |
| `backend/web_research.py` | Research graph (draft → search gate → one Tavily search → report gate) and helpers to run it |
| `backend/persistence.py` | Checkpointer + memory store (SQLite or Postgres via `DATABASE_URL`); compiles the graph |
| `backend/threads.py` · `backend/api_threads.py` | Thread registry table · `/threads` API |
| `backend/memory.py` | Short-term summarizing and long-term memory nodes |
| `backend/housekeeping.py` | Checkpoint TTL + idempotency-key cleanup (in the API locally; the `cleanup` service in Docker; `python -m backend.housekeeping` for cron) |
| `backend/idempotency.py` | `Idempotency-Key` support for the JSON POST endpoints |
| `backend/db.py` · `backend/migrate.py` · `backend/prestart.py` | Postgres helpers (fail-fast connect, health ping, advisory lock) · migrations runner · per-container startup checks |
| `backend/migrations/` · `alembic.ini` | Alembic migrations for the registry and idempotency tables |
| `backend/web_cache.py` | SQLite cache of Tavily results |
| `backend/data/` | `checkpoints.sqlite`, `memory.sqlite`, `app.sqlite` (registry), Tavily cache (git-ignored). With `LLM_PROVIDER=groq`, all of these plus the collection live in `backend/data/groq/`. |
| `tests/` | Offline pytest suite (fakes in `tests/fakes.py`). `pytest -q` from the repo root; Postgres variants in Docker; `tests/e2e/` drives a real compose stack. |
| `backend/requirements.txt` · `backend/constraints.txt` · `requirements-dev.txt` | Runtime dependencies (what the image installs) · pinned versions · tests and notebook extras |
| `Dockerfile` · `docker-compose.yml` · `docker-compose.override.yml` · `docker-compose.prod.yml` · `docker-compose.e2e.yml` | Image (runtime + test stages) · services · dev · prod · e2e test stack |
| `docker/` | Container entrypoint and the Postgres init script (roles, `finsight_test`, pgvector) |
| `scripts/` | `backup.sh`, `restore.sh`, `db-roles.sh` |
| `backend/faiss_index/` | Saved FAISS index (git-ignored) |
| `frontend/` | Next.js dashboard (single page in `frontend/app/`) |
| `docs/arch.md` | Detailed architecture with diagrams |
| `docs/DATABASE.md` | PostgreSQL roles, tables, DBeaver connection, inspection queries, backups |
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
| Ollama (`llama3`, `nomic-embed-text`) | Local LLM and embedding models (default) |
| `langchain-groq` (Groq API) | Hosted LLM when `LLM_PROVIDER=groq` |
| `langchain-huggingface` + `sentence-transformers` | Local `bge-small-en-v1.5` embeddings when `LLM_PROVIDER=groq` |
