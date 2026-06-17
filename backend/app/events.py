"""In-process pub/sub for SSE broadcasts.

Single-container deployment: one async queue per connected client. PUT/CRUD
mutations call broadcast() → all clients receive a JSON event. Frontend
re-hydrates the affected kind.

Caveats:
- Single-process only. If we scale to multiple workers / containers, replace
  with Redis pub/sub or NATS.
- No persistence. Reconnecting clients miss events that happened while away
  — they should re-hydrate fully on reconnect.
"""
import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("ft.events")

_subscribers: list[asyncio.Queue] = []


async def subscribe() -> asyncio.Queue:
    """New SSE client connects."""
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _subscribers.append(q)
    log.info("sse_subscribe", extra={"total": len(_subscribers)})
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    try:
        _subscribers.remove(q)
        log.info("sse_unsubscribe", extra={"total": len(_subscribers)})
    except ValueError:
        pass


def broadcast(event: str, data: dict[str, Any]) -> None:
    """Push an event to all connected clients. Drops on full queue."""
    payload = {"event": event, "data": data}
    msg = json.dumps(payload)
    dropped = 0
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dropped += 1
    if dropped:
        log.warning("sse_dropped", extra={"event": event, "dropped": dropped})


def subscriber_count() -> int:
    return len(_subscribers)
