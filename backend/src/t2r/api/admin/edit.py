from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from t2r.agents.orchestrator.registry import RunRegistry
from t2r.api.common.sse import sse_response
from t2r.api.deps import AdminDep
from t2r.services.edit_service import EditService

router = APIRouter(prefix="/api/admin", tags=["admin-edit"], dependencies=[AdminDep])


class RegenerateRequest(BaseModel):
    guidance: str | None = None


@router.post("/tables/{table_id}/regenerate")
@inject
async def regenerate(
    table_id: UUID,
    payload: RegenerateRequest,
    svc: FromDishka[EditService],
    login: str = AdminDep,
) -> dict:
    run_id = await svc.regenerate_table(table_id, actor=login, guidance=payload.guidance)
    return {"agent_run_id": run_id}


class EditRequest(BaseModel):
    source_id: UUID
    prompt: str


@router.post("/edit")
@inject
async def admin_edit(
    payload: EditRequest, svc: FromDishka[EditService], login: str = AdminDep
) -> dict:
    run_id = await svc.admin_edit(payload.source_id, payload.prompt, actor=login)
    return {"agent_run_id": run_id}


@router.get("/edit/agent-runs/{agent_run_id}/events")
@inject
async def stream(agent_run_id: str, request: Request, registry: FromDishka[RunRegistry]):
    run = await registry.get(agent_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    return sse_response(run, request)
