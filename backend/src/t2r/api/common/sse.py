from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from t2r.agents.orchestrator.run import AgentRun
from t2r.settings import get_settings


async def _producer(run: AgentRun, last_event_id: int, request: Request) -> AsyncIterator[dict]:
    async for event in run.bus.subscribe(last_event_id):
        if await request.is_disconnected():
            break
        sse = event.to_sse()
        yield {
            "event": sse["event"],
            "id": sse["id"],
            # ensure_ascii=False: keep Cyrillic as-is instead of \uXXXX —
            # smaller payload and readable on the wire.
            "data": json.dumps(sse["data"], default=str, ensure_ascii=False),
        }


def sse_response(run: AgentRun, request: Request) -> EventSourceResponse:
    last = int(request.headers.get("last-event-id") or 0)
    # ping= makes sse-starlette emit a `: ping` comment every N seconds during
    # idle gaps, keeping proxies/clients from dropping the stream. (The old
    # hand-rolled _heartbeat generator was never consumed, so pings never fired.)
    return EventSourceResponse(
        _producer(run, last, request),
        ping=get_settings().sse_ping_interval,
    )
