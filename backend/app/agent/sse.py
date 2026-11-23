"""Wire form of the events: `event: <name>\\ndata: <json>\\n\\n` (docs/api/contract.md)."""
from __future__ import annotations

import json
from typing import AsyncIterator

from .loop import Event

MEDIA_TYPE = "text/event-stream"
# no proxy buffering, no caching: each event must reach the browser when it is written
HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def encode(event: Event) -> str:
    data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.name}\ndata: {data}\n\n"


async def encode_stream(events: AsyncIterator[Event]) -> AsyncIterator[str]:
    """For `StreamingResponse(encode_stream(agent.stream_turn(...)), media_type=MEDIA_TYPE)`."""
    async for event in events:
        yield encode(event)
