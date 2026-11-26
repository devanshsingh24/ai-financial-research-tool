"""Local API for the FinSight Next.js dashboard.

Run from the repo root with: uvicorn backend.main:app --reload --reload-dir backend --port 8000
"""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text

from . import config, db, housekeeping, idempotency, llm as models, persistence, rag, threads, web_research
from .api_threads import (
    claim,
    current_user,
    require_confirmable,
    require_index,
    router as threads_router,
    run_json,
    run_sse,
)
from .config import CORS_ORIGINS
from .idempotency import key_header
from .schemas import (
    AskResponse,
    ConfirmRequest,
    ProcessRequest,
    ProcessResponse,
    QueryRequest,
    QueryResponse,
)
from .sse import sse, sse_response

# Show web-search cache hits / credit usage (and memory/persistence notes) in the uvicorn console.
for _name in ("finsight.web", "finsight.rag", "finsight.memory", "finsight.persistence", "finsight.housekeeping",
              "finsight.threads"):
    _log = logging.getLogger(_name)
    if not _log.handlers:
        _handler = logging.StreamHandler()
        _handler.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
        _log.addHandler(_handler)
        _log.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.getLogger("finsight.rag").info(
        "Models: %s · embeddings %s · data %s", models.describe(), models.embedding_id()["model"],
        config.DATA_DIR)
    rag.load_saved_index()
    async with AsyncExitStack() as stack:
        await persistence.open_runtime(stack)
        await threads.open_registry()
        stack.push_async_callback(threads.close_registry)
        if config.FAIL_RUNNING_ON_START:  # Docker does this once in backend/prestart.py instead
            failed = await threads.fail_running()
            if failed:
                logging.getLogger("finsight.persistence").info(
                    "%d thread(s) were interrupted by the last shutdown; /threads/{id}/retry resumes them",
                    failed)
        cleanup = None
        if config.CLEANUP_INTERVAL_HOURS > 0:
            cleanup = asyncio.create_task(housekeeping.run_forever())
        try:
            yield
        finally:
            if cleanup is not None:
                cleanup.cancel()
                try:
                    await cleanup
                except asyncio.CancelledError:
                    pass


app = FastAPI(title="FinSight Research API", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(threads_router)


async def _check_db() -> str:
    """'ok', or why the database can't be used (checks the registry and the saver pool)."""
    try:
        runtime = persistence.runtime()
    except RuntimeError as exc:
        return f"error: {exc}"
    try:
        async def registry_ping() -> None:
            async with threads.engine().connect() as conn:
                await conn.execute(text("SELECT 1"))

        await asyncio.wait_for(registry_ping(), 2.0)
        if runtime.pool is not None:
            await db.ping(runtime.pool, 2.0)
        return "ok"
    except Exception as exc:
        return f"error: {db.error_text(exc) or 'timeout'}"


@app.get("/health")
async def health() -> JSONResponse:
    """200 when the app is up and its database answers; 503 otherwise (Docker HEALTHCHECK).
    No collection yet is still healthy: collection_ready says whether one is loaded."""
    try:
        backend = persistence.runtime().backend
    except RuntimeError:
        backend = None
    db_status = await _check_db()
    healthy = db_status == "ok"
    return JSONResponse(status_code=200 if healthy else 503, content={
        "status": "ok" if healthy else "degraded",
        "db": db_status,
        "collection_ready": rag.is_ready(),
        "index_problem": rag.index_problem,
        "persistence": backend,
        "llm": models.describe(),
    })


@app.post("/api/process", response_model=ProcessResponse)
def process_urls(payload: ProcessRequest) -> ProcessResponse:
    urls = [str(url) for url in payload.urls]
    try:
        chunk_count = rag.build_index(urls)
        return ProcessResponse(
            message="Research collection is ready.", indexed_documents=chunk_count, sources=urls
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not process URLs: {exc}") from exc


@app.post("/api/query", response_model=QueryResponse)
def query(payload: QueryRequest) -> QueryResponse:
    if not rag.is_ready():
        raise HTTPException(status_code=409, detail="Add and process at least one source first.")
    try:
        answer, sources = rag.answer(payload.question)
        return QueryResponse(answer=answer, sources=sources)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not generate research: {exc}") from exc


@app.post("/api/query/stream")
def query_stream(payload: QueryRequest) -> StreamingResponse:
    """Stream the answer as Server-Sent Events: sources, token..., then done (or error)."""
    if not rag.is_ready():
        raise HTTPException(status_code=409, detail="Add and process at least one source first.")

    def events() -> Iterator[str]:
        try:
            for event, data in rag.stream_answer(payload.question):
                yield sse(event, data)
            yield sse("done", {})
        except Exception as exc:
            yield sse("error", {"detail": f"Could not generate research: {exc}"})

    return sse_response(events())


# ---------------------------------------------------------------- web-fallback (one question = one new thread)


async def _new_question_thread(question: str, user_id: str) -> str:
    thread_id = web_research.new_thread_id()
    await threads.create(thread_id, user_id, question)
    return thread_id


async def _owned_or_404(thread_id: str, user_id: str) -> None:
    if await threads.get_owned(thread_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Unknown thread_id.")


@app.post("/ask", response_model=AskResponse)
async def ask(payload: QueryRequest, request: Request, user_id: str = Depends(current_user),
              key: Optional[str] = Depends(key_header)):
    """Answer from FAISS; if the local answer isn't reliable, ask before a web search."""
    async def run() -> AskResponse:
        require_index()
        thread_id = await _new_question_thread(payload.question, user_id)
        await claim(thread_id, ("idle",))
        events = web_research.stream_run(web_research.turn_input(payload.question), thread_id, user_id)
        return await run_json(events, thread_id)
    return await idempotency.run_once(key, user_id, request, payload, run)


@app.post("/confirm", response_model=AskResponse)
async def confirm(payload: ConfirmRequest, request: Request, user_id: str = Depends(current_user),
                  key: Optional[str] = Depends(key_header)):
    """Resume a paused /ask: approve=true continues (search / keep report), false rejects."""
    async def run() -> AskResponse:
        await _owned_or_404(payload.thread_id, user_id)
        await require_confirmable(payload.thread_id, user_id)
        await claim(payload.thread_id)
        events = web_research.stream_run(web_research.resume_input(payload.approve),
                                         payload.thread_id, user_id)
        return await run_json(events, payload.thread_id)
    return await idempotency.run_once(key, user_id, request, payload, run)


@app.post("/ask/stream")
async def ask_stream(payload: QueryRequest, user_id: str = Depends(current_user)) -> StreamingResponse:
    """Streaming /ask: tokens of the local draft/answer arrive live."""
    require_index()
    thread_id = await _new_question_thread(payload.question, user_id)
    await claim(thread_id, ("idle",))
    events = web_research.stream_run(web_research.turn_input(payload.question), thread_id, user_id)
    return run_sse(events, thread_id)


@app.post("/confirm/stream")
async def confirm_stream(payload: ConfirmRequest,
                         user_id: str = Depends(current_user)) -> StreamingResponse:
    """Streaming /confirm: search status, then the final answer's tokens arrive live."""
    await _owned_or_404(payload.thread_id, user_id)
    await require_confirmable(payload.thread_id, user_id)
    await claim(payload.thread_id)
    events = web_research.stream_run(web_research.resume_input(payload.approve),
                                     payload.thread_id, user_id)
    return run_sse(events, payload.thread_id)
