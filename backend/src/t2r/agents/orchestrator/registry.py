from __future__ import annotations

import asyncio
import time

from t2r.agents.orchestrator.run import AgentRun


class RunRegistry:
    """In-memory registry of AgentRun objects with GC of finished runs."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._runs: dict[str, AgentRun] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def add(self, run: AgentRun) -> None:
        async with self._lock:
            self._runs[run.id] = run
            self._gc_locked()

    async def get(self, run_id: str) -> AgentRun | None:
        async with self._lock:
            self._gc_locked()
            return self._runs.get(run_id)

    def _gc_locked(self) -> None:
        now = time.time()
        stale = [
            rid
            for rid, r in self._runs.items()
            if r.is_finished and r.finished_at and (now - r.finished_at) > self._ttl
        ]
        for rid in stale:
            self._runs.pop(rid, None)
