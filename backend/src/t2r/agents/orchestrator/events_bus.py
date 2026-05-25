from __future__ import annotations

import asyncio
from collections import deque
from typing import AsyncIterator

from t2r.domain.events.types import AgentEvent


class EventsBus:
    """Per-run pub/sub with a replay buffer for SSE reconnects."""

    def __init__(self, max_replay: int = 500) -> None:
        self._counter = 0
        self._replay: deque[AgentEvent] = deque(maxlen=max_replay)
        self._subscribers: list[asyncio.Queue[AgentEvent | None]] = []
        self._closed = False
        self._lock = asyncio.Lock()

    async def publish(self, event: AgentEvent, *, run_id: str | None = None) -> None:
        async with self._lock:
            self._counter += 1
            event.id = self._counter
            if run_id and not event.run_id:
                event.run_id = run_id
            self._replay.append(event)
            subs = list(self._subscribers)
        for q in subs:
            await q.put(event)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            subs = list(self._subscribers)
        for q in subs:
            await q.put(None)

    @property
    def last_id(self) -> int:
        return self._counter

    async def subscribe(
        self, last_event_id: int = 0
    ) -> AsyncIterator[AgentEvent]:
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        async with self._lock:
            for ev in self._replay:
                if ev.id > last_event_id:
                    await queue.put(ev)
            if self._closed:
                await queue.put(None)
            else:
                self._subscribers.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            async with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)
