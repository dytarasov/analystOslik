from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.infra.db.repos.profiling_task_repo_pg import ProfilingTaskRepo
from t2r.logging import get_logger

logger = get_logger("profiling_scheduler")


@dataclass
class TaskResult:
    """What a handler reports back for a task.

    status: 'done' | 'awaiting_input' | 'failed' | 'skipped'
    """

    status: str
    result: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    error: str | None = None


# A handler receives the claimed task row and does the work. It must not raise
# for expected failures — return TaskResult(status='failed', error=...). Raising
# is treated as a transient failure and retried up to max_attempts.
Handler = Callable[[dict[str, Any]], Awaitable[TaskResult]]


class TaskScheduler:
    """Bounded-concurrency executor over the profiling_tasks queue.

    Workers atomically claim runnable tasks (deps satisfied) and run the handler
    registered for the task's kind. The run drains until no task is runnable and
    nothing is in flight — tasks parked in ``awaiting_input`` (questions to the
    admin) simply stop the drain; the scheduler is re-invoked once answered.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        concurrency: int = 6,
        max_attempts: int = 3,
        idle_poll_s: float = 0.2,
    ) -> None:
        self.sm = sessionmaker
        self.concurrency = concurrency
        self.max_attempts = max_attempts
        self.idle_poll_s = idle_poll_s
        self.handlers: dict[str, Handler] = {}

    def register(self, kind: str, handler: Handler) -> None:
        self.handlers[kind] = handler

    async def drain(self, run_id: UUID) -> dict[str, Any]:
        """Run until no more tasks are runnable; return final counts + coverage."""
        # Resume safety: any task left 'running' from a dead worker goes back to
        # 'pending' before we start pulling.
        async with self.sm() as s:
            reset = await ProfilingTaskRepo(s).reset_running(run_id)
            await s.commit()
        if reset:
            logger.info("scheduler.drain: re-enqueued orphaned running", run_id=str(run_id), count=reset)

        workers = [
            asyncio.create_task(self._worker(run_id, i)) for i in range(self.concurrency)
        ]
        try:
            await asyncio.gather(*workers)
        finally:
            for w in workers:
                if not w.done():
                    w.cancel()

        async with self.sm() as s:
            repo = ProfilingTaskRepo(s)
            return {"counts": await repo.counts(run_id), "coverage": await repo.coverage(run_id)}

    async def _worker(self, run_id: UUID, worker_id: int) -> None:
        while True:
            async with self.sm() as session:
                repo = ProfilingTaskRepo(session)
                task = await repo.claim_next(run_id)
                await session.commit()  # publish the claim so peers skip it

            if task is None:
                # Nothing runnable right now. If something is still running it may
                # unblock a dependent — wait and retry. Otherwise we're done.
                async with self.sm() as session:
                    counts = await ProfilingTaskRepo(session).counts(run_id)
                if counts.get("running", 0) > 0:
                    await asyncio.sleep(self.idle_poll_s)
                    continue
                return

            await self._execute(run_id, task)

    async def _execute(self, run_id: UUID, task: dict[str, Any]) -> None:
        handler = self.handlers.get(task["kind"])
        task_id: UUID = task["id"]
        if handler is None:
            await self._finalize(task_id, TaskResult("failed", error=f"no handler for kind={task['kind']}"))
            return
        try:
            res = await handler(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("scheduler.task: handler raised", task_id=str(task_id), kind=task["kind"])
            res = TaskResult("failed", error=str(exc))

        if res.status == "failed" and task["attempts"] < self.max_attempts:
            # Transient — put it back for another attempt.
            async with self.sm() as session:
                await ProfilingTaskRepo(session).set_status(task_id, "pending", error=res.error)
                await session.commit()
            logger.warning(
                "scheduler.task: retry",
                task_id=str(task_id),
                attempt=task["attempts"],
                error=res.error,
            )
            return

        await self._finalize(task_id, res)

    async def _finalize(self, task_id: UUID, res: TaskResult) -> None:
        async with self.sm() as session:
            await ProfilingTaskRepo(session).set_status(
                task_id, res.status, result=res.result, payload=res.payload, error=res.error
            )
            await session.commit()
