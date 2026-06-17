"""Server-Sent Events stream — clients get realtime cross-user updates.

Auth-gated: JWT must be valid. Pass it via `?token=` query param since the
EventSource API doesn't allow custom headers.
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

import jwt
from app.config import settings
from app.events import subscribe, unsubscribe, subscriber_count

log = logging.getLogger("ft.sse")

router = APIRouter(prefix="/api/events", tags=["events"])


def _verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/stream")
async def stream(request: Request, token: str = Query(...)):
    claims = _verify_token(token)
    queue = await subscribe()
    user_label = claims.get("name") or claims.get("email") or "anon"

    async def event_gen():
        # Initial hello so client knows connection is live
        yield {"event": "hello", "data": f'{{"sub_count": {subscriber_count()}, "user": "{user_label}"}}'}
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    # Parse "event":"x","data":{...} server format and reformat for SSE
                    import json
                    parsed = json.loads(msg)
                    yield {"event": parsed["event"], "data": json.dumps(parsed["data"])}
                except asyncio.TimeoutError:
                    # Heartbeat keeps connection alive through proxies
                    yield {"event": "ping", "data": "{}"}
        finally:
            unsubscribe(queue)

    return EventSourceResponse(event_gen())


@router.get("/status")
async def status():
    return {"subscribers": subscriber_count()}
