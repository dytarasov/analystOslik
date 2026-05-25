from __future__ import annotations

import asyncio
import time
from typing import Sequence

from t2r.agents.orchestrator.run import AgentRun, RunState
from t2r.agents.orchestrator.step import Step
from t2r.domain.events.types import step_completed, step_failed, step_started
from t2r.logging import get_logger

logger = get_logger("pipeline")


class Pipeline:
    def __init__(self, steps: Sequence[Step], *, continue_on_error: bool = False) -> None:
        self.steps = list(steps)
        self.continue_on_error = continue_on_error

    async def run(self, run: AgentRun) -> None:
        run.state = RunState.running
        logger.info(
            "pipeline.run: starting",
            run_id=run.id,
            kind=run.kind,
            steps=[s.step_id for s in self.steps],
        )
        try:
            for step in self.steps:
                if run.cancel_event.is_set():
                    logger.warning(
                        "pipeline.run: cancel_event set, finalising",
                        run_id=run.id,
                        next_step=step.step_id,
                    )
                    await run.cancel()
                    return
                started = time.time()
                logger.info("pipeline.run: step started", run_id=run.id, step=step.step_id)
                await run.emit(step_started(step.step_id, step.name))
                try:
                    await step.execute(run, run.context)
                except asyncio.CancelledError:
                    duration = int((time.time() - started) * 1000)
                    logger.warning(
                        "pipeline.run: step cancelled",
                        run_id=run.id,
                        step=step.step_id,
                        duration_ms=duration,
                    )
                    raise
                except Exception as exc:  # noqa: BLE001
                    duration = int((time.time() - started) * 1000)
                    await run.emit(step_failed(step.step_id, str(exc)))
                    logger.exception("step failed", step=step.step_id, duration_ms=duration)
                    if self.continue_on_error:
                        continue
                    await run.finalize(error=str(exc))
                    return
                duration = int((time.time() - started) * 1000)
                logger.info(
                    "pipeline.run: step completed",
                    run_id=run.id,
                    step=step.step_id,
                    duration_ms=duration,
                )
                await run.emit(step_completed(step.step_id, duration))
            logger.info("pipeline.run: all steps done — finalising OK", run_id=run.id)
            await run.finalize()
        except asyncio.CancelledError:
            logger.warning("pipeline.run: CancelledError propagated", run_id=run.id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("pipeline.run: unhandled exception", run_id=run.id)
            await run.finalize(error=str(exc))
