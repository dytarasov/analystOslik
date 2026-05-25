from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from t2r.agents.orchestrator.run import AgentRun
from t2r.settings import get_settings


async def _producer(run: AgentRun, last_event_id: int, request: Request) -> AsyncIterator[dict]:
    settings = get_settings()
    ping_interval = settings.sse_ping_interval

    async def _heartbeat(stop: asyncio.Event):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=ping_interval)
            except asyncio.TimeoutError:
                yield {"comment": "ping"}

    stop_event = asyncio.Event()
    try:
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
    finally:
        stop_event.set()


def sse_response(run: AgentRun, request: Request) -> EventSourceResponse:
    last = int(request.headers.get("last-event-id") or 0)
    return EventSourceResponse(_producer(run, last, request))
