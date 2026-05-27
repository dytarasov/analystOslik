from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter
from pydantic import BaseModel

from t2r.api.deps import AdminDep
from t2r.services.edit_service import EditService
from t2r.services.profiling_service import ProfilingService
from t2r.services.semantic_service import SemanticService

router = APIRouter(prefix="/api/admin", tags=["admin-columns"], dependencies=[AdminDep])


class ColumnUpdate(BaseModel):
    description: str | None = None
    semantic_role: str | None = None
    user_notes: str | None = None
    enabled: bool | None = None
    reason: str | None = None


class ColumnRegenerate(BaseModel):
    guidance: str | None = None


@router.get("/columns/{column_id}")
@inject
async def get_column(column_id: UUID, svc: FromDishka[SemanticService]) -> dict:
    return await svc.get_column(column_id)


@router.patch("/columns/{column_id}")
@inject
async def update_column(
    column_id: UUID,
    payload: ColumnUpdate,
    svc: FromDishka[SemanticService],
    login: str = AdminDep,
) -> dict:
    # enabled is a distinct lifecycle action (cascades notes/graph/relations), so
    # it's applied via its own service path; content fields go the normal route.
    if payload.enabled is not None:
        await svc.set_column_enabled(
            column_id, enabled=payload.enabled, actor=login, reason=payload.reason
        )
    if (
        payload.description is not None
        or payload.semantic_role is not None
        or payload.user_notes is not None
    ):
        return await svc.update_column(
            column_id,
            actor=login,
            description=payload.description,
            semantic_role=payload.semantic_role,
            user_notes=payload.user_notes,
            reason=payload.reason,
        )
    return await svc.get_column(column_id)


@router.post("/columns/{column_id}/reprofile")
@inject
async def reprofile_column(
    column_id: UUID,
    svc: FromDishka[ProfilingService],
    login: str = AdminDep,
) -> dict:
    return await svc.reprofile_column(column_id, actor=login)


@router.post("/columns/{column_id}/confirm")
@inject
async def confirm_column(
    column_id: UUID, svc: FromDishka[SemanticService], login: str = AdminDep
) -> dict:
    return await svc.confirm_column(column_id, login)


@router.post("/columns/{column_id}/regenerate")
@inject
async def regenerate_column(
    column_id: UUID,
    payload: ColumnRegenerate,
    svc: FromDishka[EditService],
    login: str = AdminDep,
) -> dict:
    run_id = await svc.regenerate_column(
        column_id, actor=login, guidance=payload.guidance
    )
    return {"agent_run_id": run_id}


@router.get("/columns/{column_id}/revisions")
@inject
async def list_column_revisions(
    column_id: UUID, svc: FromDishka[SemanticService]
) -> list[dict]:
    return await svc.list_column_revisions(column_id)


@router.post("/columns/{column_id}/revisions/{revision}/restore")
@inject
async def restore_column_revision(
    column_id: UUID,
    revision: int,
    svc: FromDishka[SemanticService],
    login: str = AdminDep,
) -> dict:
    return await svc.restore_column_revision(column_id, revision, login)
