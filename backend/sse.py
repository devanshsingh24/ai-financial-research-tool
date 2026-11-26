"""Server-Sent Events helpers shared by the legacy routes and the threads API."""

import json
from typing import AsyncIterator, Iterator, Tuple, Union

from fastapi.responses import StreamingResponse

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse(event: str, data: object) -> str:
    # JSON-encoding the data keeps newlines inside tokens from breaking the event framing.
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def sse_response(events: Union[Iterator[str], AsyncIterator[str]]) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers=_SSE_HEADERS)


async def research_events(stream: AsyncIterator[Tuple[str, object]]) -> AsyncIterator[str]:
    """SSE wrapper for research runs: sources/token/status, then needs_confirmation or
    answered, then done (or error)."""
    try:
        async for event, data in stream:
            yield sse(event, data)
        yield sse("done", {})
    except Exception as exc:
        yield sse("error", {"detail": f"Could not answer: {exc}"})
