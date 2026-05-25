from __future__ import annotations

import asyncio
import time
import uuid
from enum import Enum
from typing import Any

from t2r.agents.orchestrator.context import RunContext
from t2r.agents.orchestrator.events_bus import EventsBus
from t2r.domain.events.types import AgentEvent, done_event, error_event
from t2r.logging import get_logger

logger = get_logger("agent_run")


class RunState(str, Enum):
    pending = "pending"
    running = "running"
    awaiting_input = "awaiting_input"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class AgentRun:
    """An ongoing pipeline execution with its own event bus and context."""

    def __init__(self, kind: str, *, context: RunContext | None = None) -> None:
        self.id: str = uuid.uuid4().hex
        self.kind = kind
        self.state: RunState = RunState.pending
        self.context = context or RunContext()
        self.bus = EventsBus()
        self.cancel_event = asyncio.Event()
        self._awaiting_future: asyncio.Future[Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self.created_at = time.time()
        self.finished_at: float | None = None
        self.error: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.state in (RunState.done, RunState.failed, RunState.cancelled)

    def attach_task(self, task: asyncio.Task[None]) -> None:
        self._task = task

    async def emit(self, event: AgentEvent) -> None:
        await self.bus.publish(event, run_id=self.id)

    async def finalize(self, *, error: str | None = None) -> None:
        if self.is_finished:
            logger.debug("run.finalize: already finished", run_id=self.id, state=self.state)
            return
        if error:
            self.state = RunState.failed
            self.error = error
            logger.warning("run.finalize: failed", run_id=self.id, error=error)
            await self.emit(error_event("INTERNAL", error))
        else:
            self.state = RunState.done
            logger.info("run.finalize: done", run_id=self.id)
        self.finished_at = time.time()
        await self.emit(done_event())
        await self.bus.close()

    async def cancel(self) -> None:
        if self.is_finished:
            logger.info("run.cancel: already finished, ignoring", run_id=self.id, state=self.state)
            return
        logger.warning(
            "run.cancel: requested",
            run_id=self.id,
            kind=self.kind,
            state=self.state,
            awaiting=self._awaiting_future is not None,
            task_running=self._task is not None and not self._task.done(),
        )
        self.cancel_event.set()
        if self._awaiting_future and not self._awaiting_future.done():
            logger.info("run.cancel: cancelling pending await_user_input", run_id=self.id)
            self._awaiting_future.cancel()
        self.state = RunState.cancelled
        self.finished_at = time.time()
        # Notify SSE subscribers so the UI flips out of "running" immediately,
        # otherwise the page stays in connecting/running until the next event.
        try:
            await self.emit(done_event())
        except Exception:
            logger.exception("run.cancel: failed to emit done", run_id=self.id)
        # The pipeline coroutine may be parked inside a long LLM/CH call. Cancel
        # the task so the next await raises CancelledError and the pipeline
        # unwinds. Without this the only effect of cancel_event is that the
        # *next* loop iteration sees the flag — current work keeps running.
        task = self._task
        if task is not None and not task.done():
            logger.info("run.cancel: cancelling underlying asyncio task", run_id=self.id)
            task.cancel()
        else:
            logger.info(
                "run.cancel: no live task to cancel",
                run_id=self.id,
                task_attached=task is not None,
                task_done=task.done() if task is not None else None,
            )
        await self.bus.close()
        logger.info("run.cancel: completed", run_id=self.id)

    async def await_user_input(self, question: str, schema: dict | None = None) -> Any:
        from t2r.domain.events.types import awaiting_input

        self.state = RunState.awaiting_input
        loop = asyncio.get_running_loop()
        self._awaiting_future = loop.create_future()
        await self.emit(awaiting_input(question, schema))
        try:
            answer = await self._awaiting_future
        finally:
            self.state = RunState.running
            self._awaiting_future = None
        return answer

    async def respond(self, answer: Any) -> bool:
        if self._awaiting_future and not self._awaiting_future.done():
            self._awaiting_future.set_result(answer)
            return True
        return False
