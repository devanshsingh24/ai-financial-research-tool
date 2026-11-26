"""Threads API: user-owned, multi-turn research threads with HITL gates, retry and time travel.

The user is identified by the X-User-Id header (no auth yet: the value is trusted;
default "local"). Every route checks the thread registry, so a thread owned by another
user is reported as 404, the same as one that does not exist.
"""

import asyncio
import logging
import math
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from langchain_core.messages import BaseMessage, HumanMessage

from . import idempotency, persistence, rag, threads, web_research
from .idempotency import key_header
from .schemas import (
    AskResponse,
    CheckpointOut,
    ForkRequest,
    ForkResponse,
    MessageOut,
    QueryRequest,
    ReplayRequest,
    ResumeRequest,
    ThreadCreate,
    ThreadOut,
    ThreadState,
)
from .sse import research_events, sse_response

log = logging.getLogger("finsight.threads")

router = APIRouter(prefix="/threads", tags=["threads"])

# State keys a fork may edit. messages is excluded: its reducer appends, so an "edit"
# would silently add messages instead of replacing them.
FORKABLE = set(web_research.ResearchState.__annotations__) - {"messages"}
GRAPH_NODES = {"start_turn", "load_memories", "retrieve_scored", "draft", "plan_search",
               "ask_permission", "unverified", "web_search", "final_answer", "review_report",
               "finish", "save_memory"}


def current_user(
    x_user_id: str = Header("local", alias="X-User-Id", min_length=1, max_length=64,
                            pattern=r"^[A-Za-z0-9_.\-]+$"),
) -> str:
    return x_user_id


async def owned_thread(thread_id: str, user_id: str = Depends(current_user)) -> Dict[str, Any]:
    row = await threads.get_owned(thread_id, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown thread_id.")
    return row


# ---------------------------------------------------------------- running + status tracking


# Statuses a thread may be in for a new run to start. "running" is never allowed: two
# runs at once on one thread (e.g. a double-clicked resume) would both run the graph.
NOT_RUNNING = ("idle", "interrupted", "completed", "failed")
CAN_ASK = ("idle", "completed", "failed")  # not paused at a gate: that needs /resume


async def claim(thread_id: str, allowed=NOT_RUNNING, title: Optional[str] = None) -> None:
    """Atomically mark the thread running (in any worker), or 409 if it can't start now.

    Call it after the request is validated, right before the run starts: the run's
    tracked() wrapper moves the status on from 'running' when it ends."""
    if not await threads.claim(thread_id, allowed, title=title):
        status = await threads.get_status(thread_id)
        if status == "interrupted" and "interrupted" not in allowed:
            raise HTTPException(status_code=409, detail="This thread is paused at a confirmation; resume it first.")
        raise HTTPException(status_code=409, detail="This thread is already running.")


async def tracked(events: AsyncIterator[Tuple[str, Any]], thread_id: str) -> AsyncIterator[Tuple[str, Any]]:
    """Pass events through while keeping the registry status in step with the run.
    The thread was already claimed ('running') by claim()."""
    final = None
    try:
        async for event, data in events:
            if event in ("needs_confirmation", "answered"):
                final = event
            yield event, data
    except (Exception, asyncio.CancelledError, GeneratorExit) as exc:
        # Completed supersteps are already checkpointed; /retry continues from there.
        try:
            await threads.set_status(thread_id, "failed", error=str(exc) or type(exc).__name__)
        except Exception:
            log.exception("Could not mark thread %s failed", thread_id)
        raise
    await threads.set_status(thread_id, "interrupted" if final == "needs_confirmation" else "completed")


async def run_json(events: AsyncIterator[Tuple[str, Any]], thread_id: str) -> AskResponse:
    final: Dict[str, Any] = {}
    try:
        async for event, data in tracked(events, thread_id):
            if event in ("needs_confirmation", "answered"):
                final = data
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not answer: {exc}") from exc
    return AskResponse(**final)


def run_sse(events: AsyncIterator[Tuple[str, Any]], thread_id: str) -> StreamingResponse:
    return sse_response(research_events(tracked(events, thread_id)))


def require_index() -> None:
    if not rag.is_ready():
        raise HTTPException(status_code=409, detail="Add and process at least one source first.")


async def require_confirmable(thread_id: str, user_id: str) -> None:
    try:
        await web_research.check_confirmable(thread_id, user_id)
    except web_research.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Unknown thread_id.")
    except web_research.NotAwaitingConfirmation:
        raise HTTPException(status_code=409, detail="This question is not waiting for confirmation.")


# ---------------------------------------------------------------- JSON views of state


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseMessage):
        return {"role": "user" if isinstance(value, HumanMessage) else "assistant",
                "content": str(value.content)}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _messages(values: Dict[str, Any]) -> List[MessageOut]:
    return [MessageOut(**jsonable(m)) for m in values.get("messages") or []]


def _checkpoint_id(config: Optional[Dict[str, Any]]) -> Optional[str]:
    return ((config or {}).get("configurable") or {}).get("checkpoint_id")


# ---------------------------------------------------------------- thread CRUD


@router.post("", response_model=ThreadOut, status_code=201)
async def create_thread(payload: ThreadCreate, request: Request, user_id: str = Depends(current_user),
                        key: Optional[str] = Depends(key_header)) -> Any:
    async def run() -> ThreadOut:
        return ThreadOut(**await threads.create(web_research.new_thread_id(), user_id, payload.title))
    return await idempotency.run_once(key, user_id, request, payload, run, status_code=201)


@router.get("", response_model=List[ThreadOut])
async def list_threads(user_id: str = Depends(current_user)) -> List[Dict[str, Any]]:
    return await threads.list_for(user_id)


@router.get("/{thread_id}", response_model=ThreadState)
async def get_thread(row: Dict[str, Any] = Depends(owned_thread)) -> ThreadState:
    app = persistence.runtime().app
    snapshot = await app.aget_state(web_research.thread_config(row["thread_id"], row["user_id"]))
    values = snapshot.values or {}
    interrupts = web_research.pending_interrupts(snapshot)
    pending = last = None
    if interrupts:
        pending = AskResponse(**web_research._response(row["thread_id"], {"__interrupt__": interrupts}))
    elif values.get("answer"):
        last = AskResponse(**web_research._response(row["thread_id"], values))
    return ThreadState(thread=ThreadOut(**row), next=list(snapshot.next or ()), pending=pending, last=last,
                       messages=_messages(values), summary=values.get("summary", ""),
                       memories=values.get("memories") or [], answer=values.get("answer") or None,
                       checkpoint_id=_checkpoint_id(snapshot.config))


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(row: Dict[str, Any] = Depends(owned_thread)) -> Response:
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail="This thread is running.")
    await persistence.runtime().saver.adelete_thread(row["thread_id"])
    await threads.delete_row(row["thread_id"])
    return Response(status_code=204)


# ---------------------------------------------------------------- turns


@router.post("/{thread_id}/messages", response_model=AskResponse)
async def post_message(payload: QueryRequest, request: Request,
                       row: Dict[str, Any] = Depends(owned_thread),
                       key: Optional[str] = Depends(key_header)) -> Any:
    """Ask a question on the thread (the first one or a follow-up)."""
    async def run() -> AskResponse:
        require_index()
        await claim(row["thread_id"], CAN_ASK, title=row["title"] or payload.question)
        events = web_research.stream_run(web_research.turn_input(payload.question),
                                         row["thread_id"], row["user_id"])
        return await run_json(events, row["thread_id"])
    return await idempotency.run_once(key, row["user_id"], request, payload, run)


@router.post("/{thread_id}/messages/stream")
async def post_message_stream(payload: QueryRequest,
                              row: Dict[str, Any] = Depends(owned_thread)) -> StreamingResponse:
    require_index()
    await claim(row["thread_id"], CAN_ASK, title=row["title"] or payload.question)
    events = web_research.stream_run(web_research.turn_input(payload.question),
                                     row["thread_id"], row["user_id"])
    return run_sse(events, row["thread_id"])


# ---------------------------------------------------------------- HITL


@router.post("/{thread_id}/resume", response_model=AskResponse)
async def resume(payload: ResumeRequest, request: Request, row: Dict[str, Any] = Depends(owned_thread),
                 key: Optional[str] = Depends(key_header)) -> Any:
    """Answer the gate the thread is paused at: approve, reject, or edit (query / draft)."""
    async def run() -> AskResponse:
        await require_confirmable(row["thread_id"], row["user_id"])
        await claim(row["thread_id"])
        events = web_research.stream_run(web_research.resume_input(payload.model_dump(exclude_none=True)),
                                         row["thread_id"], row["user_id"])
        return await run_json(events, row["thread_id"])
    return await idempotency.run_once(key, row["user_id"], request, payload, run)


@router.post("/{thread_id}/resume/stream")
async def resume_stream(payload: ResumeRequest,
                        row: Dict[str, Any] = Depends(owned_thread)) -> StreamingResponse:
    await require_confirmable(row["thread_id"], row["user_id"])
    await claim(row["thread_id"])
    events = web_research.stream_run(web_research.resume_input(payload.model_dump(exclude_none=True)),
                                     row["thread_id"], row["user_id"])
    return run_sse(events, row["thread_id"])


# ---------------------------------------------------------------- fault tolerance


@router.post("/{thread_id}/retry", response_model=AskResponse)
async def retry(request: Request, row: Dict[str, Any] = Depends(owned_thread),
                key: Optional[str] = Depends(key_header)) -> Any:
    """Continue a failed run from its last checkpoint. Nodes that already finished are
    not re-run (so a completed web search is never paid for twice)."""
    async def run() -> AskResponse:
        if row["status"] != "failed":
            raise HTTPException(status_code=409, detail="Only a failed thread can be retried.")
        snapshot = await persistence.runtime().app.aget_state(
            web_research.thread_config(row["thread_id"], row["user_id"]))
        if not snapshot.next:
            raise HTTPException(status_code=409, detail="Nothing left to run on this thread.")
        await claim(row["thread_id"], ("failed",))
        events = web_research.stream_run(None, row["thread_id"], row["user_id"])
        return await run_json(events, row["thread_id"])
    return await idempotency.run_once(key, row["user_id"], request, {}, run)


# ---------------------------------------------------------------- time travel


@router.get("/{thread_id}/history", response_model=List[CheckpointOut])
async def history(row: Dict[str, Any] = Depends(owned_thread),
                  limit: int = Query(50, ge=1, le=500),
                  full: bool = Query(False, description="Include retrieved chunks and web results")
                  ) -> List[CheckpointOut]:
    """Checkpoints of the thread, newest first."""
    out = []
    config = web_research.thread_config(row["thread_id"], row["user_id"])
    async for snap in persistence.runtime().app.aget_state_history(config, limit=limit):
        values = dict(snap.values or {})
        if not full:
            values.pop("documents", None)
            values.pop("web_results", None)
        out.append(CheckpointOut(
            checkpoint_id=_checkpoint_id(snap.config), parent_id=_checkpoint_id(snap.parent_config),
            step=(snap.metadata or {}).get("step"), source=(snap.metadata or {}).get("source"),
            next=list(snap.next or ()), created_at=snap.created_at, values=jsonable(values),
        ))
    return out


async def _require_checkpoint(row: Dict[str, Any], checkpoint_id: str) -> Any:
    config = web_research.thread_config(row["thread_id"], row["user_id"], checkpoint_id)
    snapshot = await persistence.runtime().app.aget_state(config)
    if _checkpoint_id(snapshot.config) != checkpoint_id or not snapshot.values:
        raise HTTPException(status_code=404, detail="Unknown checkpoint_id for this thread.")
    return snapshot


@router.post("/{thread_id}/replay", response_model=AskResponse)
async def replay(payload: ReplayRequest, request: Request, row: Dict[str, Any] = Depends(owned_thread),
                 key: Optional[str] = Depends(key_header)) -> Any:
    """Re-run the graph from a past checkpoint. The new run becomes a branch from that
    checkpoint and the thread's new head; the old checkpoints are kept."""
    async def run() -> AskResponse:
        if row["status"] == "running":
            raise HTTPException(status_code=409, detail="This thread is already running.")
        await _require_checkpoint(row, payload.checkpoint_id)
        await claim(row["thread_id"])
        events = web_research.stream_run(None, row["thread_id"], row["user_id"], payload.checkpoint_id)
        return await run_json(events, row["thread_id"])
    return await idempotency.run_once(key, row["user_id"], request, payload, run)


@router.post("/{thread_id}/fork", response_model=ForkResponse)
async def fork(payload: ForkRequest, request: Request, row: Dict[str, Any] = Depends(owned_thread),
               key: Optional[str] = Depends(key_header)) -> Any:
    """Edit state at a past checkpoint (update_state), creating a new branch; optionally run it.
    With run=false, run the branch later with /replay and the returned checkpoint_id."""
    return await idempotency.run_once(key, row["user_id"], request, payload, lambda: _fork(payload, row))


async def _fork(payload: ForkRequest, row: Dict[str, Any]) -> ForkResponse:
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail="This thread is already running.")
    bad = set(payload.values) - FORKABLE
    if bad:
        raise HTTPException(status_code=422, detail=f"These keys cannot be edited: {sorted(bad)}")
    if payload.as_node is not None and payload.as_node not in GRAPH_NODES:
        raise HTTPException(status_code=422, detail=f"Unknown node: {payload.as_node}")
    await _require_checkpoint(row, payload.checkpoint_id)
    await claim(row["thread_id"])  # also covers update_state, so no run interleaves with it
    app = persistence.runtime().app
    config = web_research.thread_config(row["thread_id"], row["user_id"], payload.checkpoint_id)
    try:
        new_config = await app.aupdate_state(config, payload.values, as_node=payload.as_node)
    except BaseException:
        await threads.set_status(row["thread_id"], row["status"])
        raise
    new_id = _checkpoint_id(new_config)
    result = None
    if payload.run:
        events = web_research.stream_run(None, row["thread_id"], row["user_id"], new_id)
        result = await run_json(events, row["thread_id"])
    else:
        await threads.set_status(row["thread_id"], "idle")
    return ForkResponse(checkpoint_id=new_id, result=result)
