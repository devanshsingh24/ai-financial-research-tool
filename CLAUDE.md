# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FinSight is a local RAG financial research tool: the user supplies article URLs, then asks questions answered only from those articles, with source links. Everything runs locally (Ollama models, FAISS index); no API keys are needed, except an optional Tavily key for the web-search fallback. A deployed server can use the Groq API instead of Ollama (`LLM_PROVIDER=groq` + `GROQ_API_KEY`), with local Hugging Face embeddings.

## Commands

Prerequisite: Ollama running locally with `llama3` and `nomic-embed-text` pulled (`ollama pull llama3`, `ollama pull nomic-embed-text`).

```powershell
pip install -r requirements-dev.txt -c backend/constraints.txt   # a .venv/ exists at the repo root; use it
cd frontend; npm install
Copy-Item .env.example .env                     # optional: TAVILY_API_KEY, DATABASE_URL, TTL/memory settings

uvicorn backend.main:app --reload --reload-dir backend --port 8000   # backend API (run from repo root; `backend` is a package)
npm run dev                                     # in frontend/: Next.js dashboard at http://localhost:3000
npm run build                                   # in frontend/: production build / type check

.venv/Scripts/python -m pytest -q                                    # offline test suite (repo root)
.venv/Scripts/python -m pytest -q tests/test_time_travel.py::test_fork_rejects_bad_input   # one test
python -m backend.housekeeping                  # one-off checkpoint TTL cleanup (cron)
.venv/Scripts/python -m backend.llm             # check the configured models (text, JSON, embedding); $env:LLM_PROVIDER="groq" for Groq

# Docker (API + PostgreSQL + cleanup); needs the DB passwords in .env (see .env.example)
docker compose up --build -d                    # dev: hot reload, Postgres on 127.0.0.1:5433
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build   # prod
docker compose --profile test run --rm tests    # full suite incl. Postgres variants + migrations
$env:FINSIGHT_E2E="1"; .venv/Scripts/python -m pytest tests/e2e -q   # container-restart e2e (own stack, port 8100)
docker compose run --rm migrate                 # Alembic + LangGraph setup() as the DB owner
bash scripts/backup.sh; bash scripts/restore.sh --yes backups/<file>.dump
```

The tests (`tests/`) need no Ollama, Tavily or network access. `conftest.py` patches in a prompt-routing `FakeLLM` (over `web_research.json_llm` and `rag.llm`), `HashEmbeddings`, `rag.search_with_scores` and `web_research._tavily`, and points `config.DATA_DIR` at `tmp_path`. A server "restart" means leaving and re-entering `with TestClient(app)`, which closes and reopens every DB connection. The fakes live in `tests/fakes.py` (also used by `tests/e2e/fake_app.py`). The Postgres variants are skipped unless `TEST_DATABASE_URL` is set; with it, `conftest.reset_postgres` migrates once and truncates the app tables before each test. There is no linter. `GET /health` reports whether an index is loaded (`collection_ready`).

## Architecture

Two processes talk over HTTP: a Next.js frontend (`frontend/`) and a FastAPI backend (`backend/`).

- **`backend/`** is a Python package (relative imports, so run it as `backend.main:app`).
  - `config.py`: model names, temperature (0.2), chunking (1000 chars, 200 overlap), top-k (4), CORS origins (`http://localhost:3000` only) and `INDEX_PATH`, which is anchored to the file (`backend/faiss_index/`), so the working directory doesn't matter.
  - `schemas.py`: Pydantic request/response models (1–10 URLs; questions 3–1500 chars).
  - `llm.py`: the only place models are built. `LLM_PROVIDER` (`ollama` default, or `groq`) picks `OllamaLLM`/`OllamaEmbeddings`, or `ChatGroq` (`GROQ_MODEL`, default `openai/gpt-oss-120b`, `reasoning_effort="low"`) with `HuggingFaceEmbeddings` (`HF_EMBED_MODEL`, default `BAAI/bge-small-en-v1.5`, normalized, 384 dims, runs locally on CPU; Groq has no embeddings). `make_llm(json_mode=True)` is Ollama `format="json"` or Groq `response_format={"type": "json_object"}` (`GROQ_JSON_MODE=on|off`; works streamed). JSON replies still always go through `_parse_json`. Don't construct models elsewhere. No LLM gateway (LiteLLM) on purpose.
  - `rag.py`: LLM, embeddings, prompt and the LangGraph `StateGraph` (`retrieve` → `generate`) over `RAGState`. The active retriever is the module-private `_retriever`, the only state shared between the endpoints. Access it only through `is_ready()`, `load_saved_index()`, `build_index(urls)` and `answer(question)`. Never `from .rag import _retriever`, because that binding goes stale when the index is rebuilt.
  - `main.py`: FastAPI app, includes the `/threads` router, and has a `lifespan` handler that calls `rag.load_saved_index()` (so the index survives restarts), opens the persistence runtime and thread registry, and starts the cleanup task. Routes:
    - `POST /api/process` calls `build_index`, which runs `WebBaseLoader` → splitter → `FAISS.from_documents` → `save_local`. It **replaces** the whole index; it does not append. Errors return 422.
    - `POST /api/query` calls `answer`, which returns the answer plus deduplicated source URLs. It returns 409 if nothing is indexed and 500 on generation errors.
    - `POST /api/query/stream` (used by the dashboard) calls `rag.stream_answer`, which reuses the `retrieve` node and the shared `chain`, but calls `chain.stream` instead of going through the graph. It returns `text/event-stream` with `event:` + JSON `data:` blocks: `sources` (once, deduped), `token` (repeated), then `done`, or `error` with `{detail}` if something fails mid-stream. The 409 and 422 errors happen before streaming starts, so they are normal JSON responses. If you change the prompt or retrieval, both paths pick it up; graph-only nodes would not apply to the stream.
- **`backend/web_research.py`** defines the research graph (`build_graph()`, uncompiled) behind `/ask`, `/confirm` and the `/threads` API. The dashboard uses it when its **Web fallback** toggle is on (the default). With the toggle off, the dashboard uses `/api/query/stream`.
  - Flow: `start_turn` → `load_memories` → `retrieve_scored` → `draft` (JSON-mode `llama3`: `{can_answer, answer}`).
    - If answerable: `finish`.
    - Otherwise: `plan_search` → `ask_permission` (**search gate**, `interrupt`).
      - reject → `unverified` → `finish`
      - approve, or edit the query → `web_search` → `final_answer` → `review_report` (**report gate**, `interrupt`) → `finish`
    - `finish` → `save_memory` → END.
  - One graph run is one turn of a thread. `start_turn` resets the per-turn fields (`_TURN_RESET`) and summarizes `messages` older than `MAX_MESSAGES`. `messages` (the `add_messages` reducer), `summary`, `memories` and `turn` persist across turns.
  - Gate resume values go through `_decision`: a legacy bool (`/confirm`) or `{action: approve|reject|edit, query?, draft?}`. Interrupt payloads carry `kind: "web_search" | "report"`.
  - **At most one Tavily search per question.** There is no edge back into `plan_search` or `web_search`, `web_search` refuses if `searches_used >= 1`, failures are not retried, and resuming returns 409 unless the thread is paused at a gate. Keep all of this if you edit the graph.
  - `can_answer` is forced to false in three cases: the best FAISS L2 distance is above `MAX_DISTANCE` (0.75, calibrated in `config.py`); the question contains recency words (`_RECENCY`); or the draft hedges (`_HEDGING`). The guards exist because llama3 often claims it can answer when it can't. They can only force false, never true.
  - **Running.** Every route runs the graph through the async generator `stream_run(input, thread_id, user_id, checkpoint_id?)` (`run` is its JSON form). It calls `app.astream(stream_mode=["custom", "updates"], durability="sync")` and then reads the thread head: a pending interrupt becomes `needs_confirmation`, otherwise `_response` builds `answered`.
    - Nodes emit `{"type": "token" | "sources" | "status", …}` through `_writer()`, which does nothing outside a streaming run.
    - The `draft` node extracts the `"answer"` string from streamed JSON with `_AnswerFieldStreamer`. The decision is still made on the full text afterwards.
  - The Tavily cache (`web_cache.py`, `DATA_DIR/tavily_cache.sqlite`) is keyed by sha256(query lower/stripped, topic, time_range). The `finsight.web` logger prints `cache HIT (0 credits)` or `credit USED (1 credit)`. `TAVILY_API_KEY` comes from `<repo root>/.env` and is re-read before each search. The Tavily client is created lazily, so the server runs without a key.
  - `rag.search_with_scores` uses the module-level `_store`, which `load_saved_index` and `build_index` set alongside `_retriever`.
- **Persistence layer.**
  - **`persistence.py`.** `open_runtime(stack)` runs in `main.lifespan`. It picks `AsyncSqliteSaver` + `AsyncSqliteStore` (sqlite-vec index) in `backend/data/`, or `AsyncPostgresSaver` + `AsyncPostgresStore` sharing one psycopg `AsyncConnectionPool` (`autocommit`, `prepare_threshold=0`, `dict_row`) when `DATABASE_URL` is `postgresql://…`. It runs `setup()` only if `DB_SETUP_ON_START` (Docker turns it off: the `migrate` service does it as the owner). It then compiles the graph. It imports `rag`/`web_research` lazily, so `migrate` and `cleanup` (`open_saver`, `setup_postgres`) never load a model. Get the compiled graph with `persistence.runtime().app`; there is no module-level compiled graph. Everything is async because the Postgres saver requires it. Existing nodes stay sync and run in worker threads.
  - **`threads.py`.** The registry table `threads(thread_id, user_id, title, created_at, updated_at, status, error)`, using SQLAlchemy async (`app.sqlite`, or the same Postgres database). SQLite uses `create_all`; on Postgres the schema comes only from Alembic (`backend/migrations/`, `alembic.ini`) and `open_registry` refuses to start unless the DB is at head. `claim(thread_id, allowed)` is an atomic `UPDATE … WHERE status IN …` that every run goes through (no check-then-act: two workers can't both run a thread; a `running` row older than 30 min counts as abandoned). `fail_running()` turns crashed `running` threads into `failed`: in the lifespan locally, once per container start in `prestart.py` in Docker (`FAIL_RUNNING_ON_START=false`).
  - **`idempotency.py`.** Table `idempotency_keys` (same metadata). `run_once()` wraps the JSON POST routes when an `Idempotency-Key` header is sent: same key and body → the stored response (`Idempotent-Replayed: true`), different body → 422, in progress → 409, failure → key released. SSE routes don't take keys.
  - **`db.py`.** `wait_for_db` (fail fast with a masked URL), `ping`, `advisory_lock`. **`migrate.py`** (Alembic upgrade + LangGraph setup), **`prestart.py`** (DB reachable, schema at head, `fail_running`).
  - **`api_threads.py`.** The `/threads` router.
    - `current_user` reads `X-User-Id` (no auth; defaults to `local`). `owned_thread` returns 404 for another user's thread.
    - Routes `claim()` the thread right before running (409 if running, or paused when asking). `tracked()` then moves the status on (`interrupted` / `completed` / `failed`).
    - `/retry` resumes from the last checkpoint with `stream_run(None, …)`. `/replay` and `/fork` use `checkpoint_id` and `aupdate_state`, and `thread_config` adds `checkpoint_ns=""` for those.
    - The legacy `/ask*` routes create a registry thread per question; `/confirm*` checks ownership.
  - **`memory.py`.** `load_memories` runs on the first turn only and `save_memory` after each completed turn. Both are async nodes that receive the injected `store`, with namespace `(user_id, "memories")`. They are best-effort: errors are logged and never fail the answer.
  - **`housekeeping.py`.** `purge(CHECKPOINT_TTL_DAYS)` runs every `CLEANUP_INTERVAL_HOURS` (0 disables it): in the API locally, or as the Docker `cleanup` service (`--loop`). It deletes expired registered threads, old orphan checkpoint threads (one SQL query on Postgres) and expired idempotency keys, under a Postgres advisory lock.
  - **State hygiene.** Keep only plain data and messages in `ResearchState`. Clients and connections are injected via `compile(checkpointer=…, store=…)` or are module-level (LLMs).
- **`frontend/`** is the Next.js 15 App Router app (React 19, TypeScript). The UI is `page.tsx` (the `Dashboard` client component), `threads.tsx` (`ThreadsPanel`: thread list, conversation, recalled memories, retry and delete, checkpoint history with replay and fork) and `api.ts` (shared types, the `api()` JSON helper, `streamEvents`). Every request sends `X-User-Id` (from the USER field, kept in localStorage as `finsight-user`). With web fallback on, the terminal continues `activeThread` with `/threads/{id}/messages/stream`. It creates a thread first when there is no open thread, or when the open one is paused at a gate. Both gates are answered with `resumeGate` → `/threads/{id}/resume/stream`. `openThread` loads a thread's `pending` gate or `last` answer into the terminal. `threadsVersion` is bumped after each run so the panel reloads. Sources-only mode (`/api/query/stream`) isn't saved to threads. It reads the answer stream with `fetch` + `ReadableStream` (`EventSource` can't POST), parses the events with `parseEvent`, and shows a blinking `.cursor` while `isStreaming`. It calls the API at `NEXT_PUBLIC_API_URL`, defaulting to `http://localhost:8000`, and surfaces the backend's `detail` message on errors.
- **`backend/faiss_index/`** is git-ignored (a fresh clone has no collection). Each "process URLs" call writes a new version directory `v<ns>-<pid>/` (`index.faiss`, `index.pkl`, `meta.json`) and atomically swaps `CURRENT` (`rag.publish`); the newest 3 are kept. `_refresh()` (in `is_ready`, `search_with_scores`, `retrieve`) reloads when `CURRENT` changes, so all uvicorn workers follow. A flat legacy index still loads. It is loaded with `allow_dangerous_deserialization=True`, so only load indexes you created. In Docker it lives in the `faiss_index` volume (`INDEX_PATH=/data/faiss_index`).
- **Docker** (`Dockerfile`, `docker-compose*.yml`, `docker/`, `scripts/`, `docs/DATABASE.md`). Services: `db` (`pgvector/pgvector:pg16`; `docker/db/init/01-roles.sh` creates `finsight_app` (rows only) and `finsight_readonly` on first init), `migrate` (one-shot, DB owner), `api` (`docker/entrypoint.sh api` → prestart → uvicorn; DB user `finsight_app`; no `env_file`, only listed variables), `cleanup`, `ollama` (profile `local`), `tests` (profile `test`). Startup order: db healthy → migrate exits 0 → api/cleanup. The image is multi-stage, CPU-only torch, bge-small baked in (`HF_HUB_OFFLINE=1`), non-root uid 10001, `HEALTHCHECK` on `/health` (200 only if the DB answers; 503 otherwise).
- **`tests/`** is the offline pytest suite (see Commands).
- **`docs/`** holds `arch.md` (detailed architecture with Mermaid diagrams) and `postcleanup.md`.
- **`notebooks/rag.ipynb`** is the original notebook prototype of the same pipeline, kept for reference. It uses `UnstructuredURLLoader` (hence `unstructured` in `requirements.txt`), writes its own `faiss_index/` next to itself, and is not imported by the app.
- **`Presentation/`** holds a college presentation (`generate_deck.py` builds `FinSight_College_Presentation.pptx`). It is unrelated to the app runtime, and its slide text still refers to the old `app.py`.

## Gotchas

- An index is tied to its embedding model: `build_index` writes `faiss_index/meta.json`, and `load_saved_index` refuses a mismatch (sets `rag.index_problem`, shown in `/health`). A missing meta file means Ollama `nomic-embed-text`. Each provider has its own `DATA_DIR`/`INDEX_PATH` (`groq` → `backend/data/groq/`), so switching locally keeps both datasets. `EMBED_DIMS` defaults per provider (768 nomic, 384 bge). `MAX_DISTANCE=0.75` is calibrated for both nomic and bge-small; it's env-overridable for other embedding models.
- `tests/conftest.py` forces `LLM_PROVIDER=ollama` before importing the backend, so a `.env` set to Groq never reaches the tests (and no Hugging Face model is downloaded).
- `backend/requirements.txt` is runtime-only (the image installs it); tests and notebook extras are in `requirements-dev.txt`. Versions are pinned in `backend/constraints.txt` (regenerate from a working env with `pip freeze`, minus Windows-only packages). Bump `TORCH_VERSION` in the Dockerfile together with it.
- Changing a registry/idempotency `Table`: add an Alembic revision (`alembic revision --autogenerate`, with `MIGRATION_DATABASE_URL` set); `tests/test_migrations.py` fails on drift.
- One provider per Postgres database: the memory index's vector size is fixed by `EMBED_DIMS` when `migrate` first runs (`migrate` and `api` must use the same `LLM_PROVIDER`).
- Shell scripts must stay LF (`.gitattributes`), or they fail inside the containers.
- `docs/postcleanup.md` records files removed or moved and known issues not yet fixed; update it when cleaning up further.
- If you change backend behaviour or file layout, keep `README.md` and `docs/arch.md` in sync.
- Use `--reload-dir backend` with uvicorn. Without it, WatchFiles also watches `.venv/` and `frontend/node_modules/`, and reloads on Windows can hang (the old worker keeps serving old code).
- Approving the search gate (`/confirm approve=true` or `/threads/{id}/resume`) can spend a real Tavily credit. Test the search path offline (see `tests/conftest.py`).
- `config.DATA_DIR`, `config.DATABASE_URL` and `rag.embeddings` are read when the server starts (lifespan), not at import, so tests can patch them. Keep it that way.
- Postgres mode uses psycopg's async driver, which doesn't run on Windows' default Proactor event loop. Use Docker (or Linux/WSL) for it; the Postgres tests run in the `tests` container.
- The SQLite DBs in `backend/data/` (`checkpoints.sqlite`, `memory.sqlite`, `app.sqlite`) are dev data. Deleting them resets all threads and memories.
