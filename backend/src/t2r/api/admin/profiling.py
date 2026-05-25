from datetime import datetime
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from t2r.agents.orchestrator.registry import RunRegistry
from t2r.api.common.sse import sse_response
from t2r.api.deps import AdminDep
from t2r.logging import get_logger
from t2r.services.profiling_service import ProfilingService

logger = get_logger("api.profiling")

router = APIRouter(prefix="/api/admin/profiling", tags=["admin-profiling"], dependencies=[AdminDep])


class StartRunRequest(BaseModel):
    source_id: UUID
    include: list[str] | None = None
    exclude: list[str] | None = None


class StartRunResponse(BaseModel):
    run_id: UUID
    agent_run_id: str
    reused: bool = False


class ActiveRunResponse(BaseModel):
    run_id: UUID
    agent_run_id: str | None
    status: str
    started_at: datetime | None
    attached: bool


@router.post("/runs", response_model=StartRunResponse)
@inject
async def start_run(
    payload: StartRunRequest,
    svc: FromDishka[ProfilingService],
    login: str = AdminDep,
) -> StartRunResponse:
    run_id, agent_run_id, reused = await svc.start(
        payload.source_id,
        requested_by=login,
        params={"include": payload.include, "exclude": payload.exclude},
    )
    return StartRunResponse(run_id=run_id, agent_run_id=agent_run_id, reused=reused)


@router.get("/runs/active", response_model=ActiveRunResponse | None)
@inject
async def get_active_run(
    source_id: UUID, svc: FromDishka[ProfilingService]
) -> ActiveRunResponse | None:
    active = await svc.get_active(source_id)
    if not active:
        return None
    return ActiveRunResponse(
        run_id=active["run_id"],
        agent_run_id=active.get("agent_run_id"),
        status=active["status"],
        started_at=active.get("started_at"),
        attached=bool(active.get("attached")),
    )


@router.get("/runs/{run_id}")
@inject
async def get_run(run_id: UUID, svc: FromDishka[ProfilingService]) -> dict:
    run = await svc.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    tables = await svc.get_run_tables(run_id)
    return {"run": run, "tables": tables}


@router.get("/runs/{run_id}/progress")
@inject
async def run_progress(run_id: UUID, svc: FromDishka[ProfilingService]) -> dict:
    """Polled by the run page: task counts, coverage, and pending questions."""
    return await svc.get_progress(run_id)


@router.get("/runs")
@inject
async def list_runs(source_id: UUID, svc: FromDishka[ProfilingService]) -> list[dict]:
    return await svc.list_runs(source_id)


@router.get("/agent-runs/{agent_run_id}/events")
@inject
async def stream(
    agent_run_id: str, request: Request, registry: FromDishka[RunRegistry]
):
    run = await registry.get(agent_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    return sse_response(run, request)


@router.post("/agent-runs/{agent_run_id}/cancel")
@inject
async def cancel(agent_run_id: str, registry: FromDishka[RunRegistry]) -> dict:
    logger.warning("api.profiling.cancel: received", agent_run_id=agent_run_id)
    run = await registry.get(agent_run_id)
    if not run:
        logger.warning(
            "api.profiling.cancel: agent run not in registry (already done / never lived)",
            agent_run_id=agent_run_id,
        )
        raise HTTPException(status_code=404, detail="agent run not found")
    logger.info(
        "api.profiling.cancel: found run",
        agent_run_id=agent_run_id,
        kind=run.kind,
        state=run.state,
        finished=run.is_finished,
    )
    await run.cancel()
    logger.info(
        "api.profiling.cancel: returning",
        agent_run_id=agent_run_id,
        new_state=run.state,
    )
    return {"cancelled": True, "state": run.state}


class RespondRequest(BaseModel):
    # Free-text legacy responses still work; structured answers come in as
    # ``{"answers": {"q1": "...", "q2": null}}`` from the clarification form.
    answer: str | dict | None = None
    answers: dict | None = None


@router.post("/agent-runs/{agent_run_id}/respond")
@inject
async def respond(
    agent_run_id: str,
    payload: RespondRequest,
    registry: FromDishka[RunRegistry],
) -> dict:
    run = await registry.get(agent_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    if payload.answers is not None:
        value: object = {"answers": payload.answers}
    else:
        value = payload.answer
    ok = await run.respond(value)
    return {"accepted": ok}


class AnswerItem(BaseModel):
    column: str | None = None
    text: str | None = None
    answer: str


class AnswerRequest(BaseModel):
    answers: list[AnswerItem]


@router.post("/tasks/{task_id}/answer")
@inject
async def answer_question(
    task_id: UUID, payload: AnswerRequest, svc: FromDishka[ProfilingService]
) -> dict:
    """Answer a parked describe task's questions; resumes the run in background."""
    return await svc.answer_question(
        task_id, [a.model_dump() for a in payload.answers]
    )
