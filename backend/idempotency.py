"""Idempotency-Key support for the JSON POST endpoints.

A client that may retry a request (timeouts, flaky networks) sends a unique
`Idempotency-Key` header. The first request with that key runs and its successful response
is stored. A retry with the same key and the same body gets the stored response back
without running anything again (no second graph run, no second web search). Keys are
scoped to the user and kept for IDEMPOTENCY_TTL_HOURS (housekeeping deletes older ones).

- Same key, different body or endpoint     -> 422
- Same key while the first is still running -> 409 (retry later)
- The first request fails (4xx/5xx)         -> the key is released, so a retry runs again

Streaming (SSE) endpoints don't take a key: a stream can't be replayed. They are protected
against double runs by threads.claim().
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from fastapi import Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import JSON, Column, DateTime, Index, Integer, String, Table, delete, select, update
from sqlalchemy.exc import IntegrityError

from . import config, threads

# An in-progress key older than this is treated as abandoned (its worker died mid-request).
STALE_AFTER = timedelta(minutes=15)

idempotency_keys = Table(
    "idempotency_keys",
    threads.metadata,
    Column("user_id", String(64), primary_key=True),
    Column("key", String(128), primary_key=True),
    Column("method", String(8), nullable=False),
    Column("path", String(300), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("state", String(16), nullable=False),          # in_progress | done
    Column("status_code", Integer, nullable=True),
    Column("response", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_idempotency_keys_user_id", "user_id"),
    Index("ix_idempotency_keys_updated_at", "updated_at"),  # TTL cleanup
)


def key_header(
    idempotency_key: Optional[str] = Header(
        None, alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$",
        description="Optional. Retries with the same key return the first response instead of running again.",
    ),
) -> Optional[str]:
    return idempotency_key


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def request_hash(method: str, path: str, body: Any) -> str:
    canonical = json.dumps(jsonable_encoder(body), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{method} {path}\n{canonical}".encode()).hexdigest()


async def _claim_key(user_id: str, key: str, method: str, path: str, digest: str) -> Optional[JSONResponse]:
    """Insert the key as in_progress. Returns a stored response to replay, or None to run."""
    now = _now()
    row = {"user_id": user_id, "key": key, "method": method, "path": path, "request_hash": digest,
           "state": "in_progress", "status_code": None, "response": None, "created_at": now, "updated_at": now}
    engine = threads.engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(idempotency_keys.insert().values(**row))
        return None
    except IntegrityError:
        pass
    async with engine.connect() as conn:
        existing = (await conn.execute(select(idempotency_keys).where(
            idempotency_keys.c.user_id == user_id, idempotency_keys.c.key == key))).first()
    if existing is None:  # deleted in between (failed first attempt): try once more
        return await _claim_key(user_id, key, method, path, digest)
    existing = existing._mapping
    if existing["request_hash"] != digest:
        raise HTTPException(status_code=422, detail="This Idempotency-Key was already used for a different request.")
    if existing["state"] == "done":
        return JSONResponse(content=existing["response"], status_code=existing["status_code"] or 200,
                            headers={"Idempotent-Replayed": "true"})
    if now - _aware(existing["updated_at"]) < STALE_AFTER:
        raise HTTPException(status_code=409, detail="A request with this Idempotency-Key is still in progress.")
    # Abandoned (its worker died): take it over.
    async with engine.begin() as conn:
        result = await conn.execute(update(idempotency_keys).where(
            idempotency_keys.c.user_id == user_id, idempotency_keys.c.key == key,
            idempotency_keys.c.state == "in_progress", idempotency_keys.c.updated_at == existing["updated_at"],
        ).values(updated_at=now))
    if (result.rowcount or 0) != 1:
        raise HTTPException(status_code=409, detail="A request with this Idempotency-Key is still in progress.")
    return None


async def _release(user_id: str, key: str) -> None:
    async with threads.engine().begin() as conn:
        await conn.execute(delete(idempotency_keys).where(
            idempotency_keys.c.user_id == user_id, idempotency_keys.c.key == key))


async def run_once(key: Optional[str], user_id: str, request: Request, body: Any,
                   handler: Callable[[], Awaitable[Any]], status_code: int = 200) -> Any:
    """Run `handler` once per (user, Idempotency-Key). Without a key, just run it."""
    if key is None:
        return await handler()
    method, path = request.method, request.url.path
    replay = await _claim_key(user_id, key, method, path, request_hash(method, path, body))
    if replay is not None:
        return replay
    try:
        result = await handler()
    except BaseException:
        await _release(user_id, key)
        raise
    content = jsonable_encoder(result)
    async with threads.engine().begin() as conn:
        await conn.execute(update(idempotency_keys).where(
            idempotency_keys.c.user_id == user_id, idempotency_keys.c.key == key,
        ).values(state="done", status_code=status_code, response=content, updated_at=_now()))
    return JSONResponse(content=content, status_code=status_code)


async def purge(ttl_hours: Optional[int] = None) -> int:
    """Delete keys older than the TTL. Returns how many were removed."""
    hours = config.IDEMPOTENCY_TTL_HOURS if ttl_hours is None else ttl_hours
    cutoff = _now() - timedelta(hours=hours)
    async with threads.engine().begin() as conn:
        result = await conn.execute(delete(idempotency_keys).where(idempotency_keys.c.updated_at < cutoff))
    return result.rowcount or 0
