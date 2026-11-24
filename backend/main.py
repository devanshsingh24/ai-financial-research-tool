"""Local API for the FinSight Next.js dashboard.

Run from the repo root with: uvicorn backend.main:app --reload --port 8000
"""

import json
from contextlib import asynccontextmanager
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import logging

from . import rag, web_research
from .config import CORS_ORIGINS
from .schemas import (
    AskResponse,
    ConfirmRequest,
    ProcessRequest,
    ProcessResponse,
    QueryRequest,
    QueryResponse,
)

# Show web-search cache hits / credit usage in the uvicorn console.
_web_log = logging.getLogger("finsight.web")
if not _web_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [web] %(message)s"))
    _web_log.addHandler(_handler)
    _web_log.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    rag.load_saved_index()
    yield


app = FastAPI(title="FinSight Research API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "collection_ready": rag.is_ready()}


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


@app.post("/ask", response_model=AskResponse)
def ask(payload: QueryRequest) -> AskResponse:
    """Answer from FAISS; if the local answer isn't reliable, ask before a web search."""
    if not rag.is_ready():
        raise HTTPException(status_code=409, detail="Add and process at least one source first.")
    try:
        return AskResponse(**web_research.ask(payload.question))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not answer: {exc}") from exc


@app.post("/confirm", response_model=AskResponse)
def confirm(payload: ConfirmRequest) -> AskResponse:
    """Resume a paused /ask: approve=true runs ONE web search, false returns the draft."""
    try:
        return AskResponse(**web_research.confirm(payload.thread_id, payload.approve))
    except web_research.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Unknown thread_id.")
    except web_research.NotAwaitingConfirmation:
        raise HTTPException(status_code=409, detail="This question is not waiting for confirmation.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not answer: {exc}") from exc


def _sse(event: str, data: object) -> str:
    # JSON-encoding the data keeps newlines inside tokens from breaking the event framing.
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/query/stream")
def query_stream(payload: QueryRequest) -> StreamingResponse:
    """Stream the answer as Server-Sent Events: sources, token..., then done (or error)."""
    if not rag.is_ready():
        raise HTTPException(status_code=409, detail="Add and process at least one source first.")

    def events() -> Iterator[str]:
        try:
            for event, data in rag.stream_answer(payload.question):
                yield _sse(event, data)
            yield _sse("done", {})
        except Exception as exc:
            yield _sse("error", {"detail": f"Could not generate research: {exc}"})

    return _sse_response(events())


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse_response(events: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers=_SSE_HEADERS)


def _research_events(stream: Iterator) -> Iterator[str]:
    """SSE wrapper for web_research streams: sources/token/status, then
    needs_confirmation or answered, then done (or error)."""
    try:
        for event, data in stream:
            yield _sse(event, data)
        yield _sse("done", {})
    except Exception as exc:
        yield _sse("error", {"detail": f"Could not answer: {exc}"})


@app.post("/ask/stream")
def ask_stream(payload: QueryRequest) -> StreamingResponse:
    """Streaming /ask: tokens of the local draft/answer arrive live."""
    if not rag.is_ready():
        raise HTTPException(status_code=409, detail="Add and process at least one source first.")
    return _sse_response(_research_events(web_research.stream_ask(payload.question)))


@app.post("/confirm/stream")
def confirm_stream(payload: ConfirmRequest) -> StreamingResponse:
    """Streaming /confirm: search status, then the final answer's tokens arrive live."""
    try:
        web_research.check_confirmable(payload.thread_id)
    except web_research.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Unknown thread_id.")
    except web_research.NotAwaitingConfirmation:
        raise HTTPException(status_code=409, detail="This question is not waiting for confirmation.")
    return _sse_response(
        _research_events(web_research.stream_confirm(payload.thread_id, payload.approve))
    )
