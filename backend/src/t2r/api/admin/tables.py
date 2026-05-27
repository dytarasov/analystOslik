from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from t2r.agents.orchestrator.registry import RunRegistry
from t2r.api.common.sse import sse_response
from t2r.api.deps import AdminDep
from t2r.services.semantic_service import SemanticService
from t2r.services.table_chat_service import TableChatService

router = APIRouter(prefix="/api/admin", tags=["admin-tables"], dependencies=[AdminDep])


class TableUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    domain: str | None = None
    tags: list[str] | None = None
    user_notes: str | None = None
    reason: str | None = None


@router.get("/sources/{source_id}/tables")
@inject
async def list_tables(source_id: UUID, svc: FromDishka[SemanticService]) -> list[dict]:
    return await svc.list_tables(source_id)


@router.get("/tables/{table_id}")
@inject
async def get_table(table_id: UUID, svc: FromDishka[SemanticService]) -> dict:
    return await svc.get_table(table_id)


@router.patch("/tables/{table_id}")
@inject
async def update_table(
    table_id: UUID,
    payload: TableUpdate,
    svc: FromDishka[SemanticService],
    login: str = AdminDep,
) -> dict:
    return await svc.update_table(
        table_id,
        actor=login,
        title=payload.title,
        description=payload.description,
        domain=payload.domain,
        tags=payload.tags,
        user_notes=payload.user_notes,
        reason=payload.reason,
    )


@router.post("/tables/{table_id}/confirm")
@inject
async def confirm_table(
    table_id: UUID, svc: FromDishka[SemanticService], login: str = AdminDep
) -> dict:
    return await svc.confirm_table(table_id, login)


@router.get("/tables/{table_id}/revisions")
@inject
async def list_table_revisions(
    table_id: UUID, svc: FromDishka[SemanticService]
) -> list[dict]:
    return await svc.list_table_revisions(table_id)


@router.post("/tables/{table_id}/revisions/{revision}/restore")
@inject
async def restore_table_revision(
    table_id: UUID,
    revision: int,
    svc: FromDishka[SemanticService],
    login: str = AdminDep,
) -> dict:
    return await svc.restore_table_revision(table_id, revision, login)


class ColumnsToggle(BaseModel):
    names: list[str]
    enabled: bool


@router.post("/tables/{table_id}/columns/toggle")
@inject
async def toggle_columns(
    table_id: UUID,
    payload: ColumnsToggle,
    svc: FromDishka[SemanticService],
    login: str = AdminDep,
) -> dict:
    return await svc.set_columns_enabled(
        table_id, names=payload.names, enabled=payload.enabled, actor=login
    )


class TableAskRequest(BaseModel):
    prompt: str


@router.post("/tables/{table_id}/ask")
@inject
async def ask_table(
    table_id: UUID,
    payload: TableAskRequest,
    svc: FromDishka[TableChatService],
    login: str = AdminDep,
) -> dict:
    run_id = await svc.ask(table_id, payload.prompt, actor=login)
    return {"agent_run_id": run_id}


@router.get("/tables/{table_id}/chat")
@inject
async def table_chat_history(
    table_id: UUID, svc: FromDishka[TableChatService]
) -> dict:
    return await svc.history(table_id)


@router.get("/tables/agent-runs/{agent_run_id}/events")
@inject
async def stream_table_chat(
    agent_run_id: str, request: Request, registry: FromDishka[RunRegistry]
):
    run = await registry.get(agent_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    return sse_response(run, request)
