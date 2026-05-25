"""Demo SSE endpoint used to verify the streaming pipeline end to end.

Starts a dummy pipeline that emits a few events, then completes.
"""
from __future__ import annotations

import asyncio

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Request

from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.registry import RunRegistry
from t2r.agents.orchestrator.run import AgentRun
from t2r.agents.orchestrator.step import Step
from t2r.api.common.sse import sse_response
from t2r.api.deps import AdminDep
from t2r.domain.events.types import step_progress

router = APIRouter(prefix="/api/admin/_demo", tags=["demo"], dependencies=[AdminDep])


class _ProgressStep(Step):
    def __init__(self, n: int) -> None:
        super().__init__(step_id=f"demo-{n}", name=f"Demo step {n}")
        self._n = n

    async def execute(self, run, ctx) -> None:  # type: ignore[override]
        for i in range(3):
            if run.cancel_event.is_set():
                return
            await run.emit(step_progress(self.step_id, (i + 1) / 3.0, f"tick {i + 1}/3"))
            await asyncio.sleep(0.5)


@router.post("/runs")
@inject
async def start_run(registry: FromDishka[RunRegistry]) -> dict:
    run = AgentRun("demo")
    await registry.add(run)
    pipeline = Pipeline([_ProgressStep(1), _ProgressStep(2), _ProgressStep(3)])
    task = asyncio.create_task(pipeline.run(run))
    run.attach_task(task)
    return {"run_id": run.id}


@router.get("/runs/{run_id}/events")
@inject
async def stream(run_id: str, request: Request, registry: FromDishka[RunRegistry]):
    run = await registry.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return sse_response(run, request)
