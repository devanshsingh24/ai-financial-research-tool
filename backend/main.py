"""Local API for the FinSight Next.js dashboard.

Run from the repo root with: uvicorn backend.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import rag
from .config import CORS_ORIGINS
from .schemas import ProcessRequest, ProcessResponse, QueryRequest, QueryResponse


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
