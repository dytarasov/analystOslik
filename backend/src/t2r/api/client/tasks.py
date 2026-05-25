import os
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from t2r.agents.orchestrator.registry import RunRegistry
from t2r.api.client.session import COOKIE, _ensure_cookie
from t2r.api.common.sse import sse_response
from t2r.errors import NotFoundError
from t2r.services.session_service import SessionService
from t2r.services.task_service import TaskService

router = APIRouter(prefix="/api", tags=["client-tasks"])


class StartTaskRequest(BaseModel):
    session_id: UUID
    source_id: UUID
    prompt: str


class RespondRequest(BaseModel):
    answer: str


@router.post("/tasks")
@inject
async def start_task(
    payload: StartTaskRequest,
    response: Response,
    svc: FromDishka[TaskService],
    sessions: FromDishka[SessionService],
    t2r_session: str | None = Cookie(default=None),
) -> dict:
    cookie = _ensure_cookie(response, t2r_session)
    await sessions.upsert_meta(cookie)
    await sessions.add_message(payload.session_id, "user", payload.prompt)
    task_id, agent_run_id = await svc.start_task(
        session_id=payload.session_id,
        source_id=payload.source_id,
        prompt=payload.prompt,
    )
    return {"task_id": str(task_id), "agent_run_id": agent_run_id}


@router.get("/tasks/{task_id}")
@inject
async def get_task(task_id: UUID, svc: FromDishka[TaskService]) -> dict:
    t = await svc.get_task(task_id)
    if not t:
        raise NotFoundError("Task not found")
    return t


@router.get("/tasks/agent-runs/{agent_run_id}/events")
@inject
async def stream(
    agent_run_id: str, request: Request, registry: FromDishka[RunRegistry]
):
    run = await registry.get(agent_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    return sse_response(run, request)


@router.post("/tasks/agent-runs/{agent_run_id}/respond")
@inject
async def respond(
    agent_run_id: str,
    payload: RespondRequest,
    registry: FromDishka[RunRegistry],
) -> dict:
    run = await registry.get(agent_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    ok = await run.respond(payload.answer)
    return {"accepted": ok}


@router.post("/tasks/agent-runs/{agent_run_id}/cancel")
@inject
async def cancel(agent_run_id: str, registry: FromDishka[RunRegistry]) -> dict:
    run = await registry.get(agent_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    await run.cancel()
    return {"cancelled": True}


class RerunSqlRequest(BaseModel):
    sql: str


@router.post("/tasks/{task_id}/rerun-sql")
@inject
async def rerun_sql(
    task_id: UUID,
    payload: RerunSqlRequest,
    svc: FromDishka[TaskService],
) -> dict:
    return await svc.rerun_sql(task_id, payload.sql)


@router.get("/tasks/{task_id}/export.xlsx")
@inject
async def export_xlsx(task_id: UUID, svc: FromDishka[TaskService]) -> FileResponse:
    path = await svc.get_export_path(task_id)
    if not path or not os.path.exists(path):
        raise NotFoundError("Export не найден или ещё не сформирован")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"task_{task_id}.xlsx",
    )
