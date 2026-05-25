from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter
from pydantic import BaseModel

from t2r.api.deps import AdminDep
from t2r.services.selection_service import SelectionService

router = APIRouter(prefix="/api/admin", tags=["admin-selection"], dependencies=[AdminDep])


class SelectionItem(BaseModel):
    database: str
    table: str
    note: str | None = None


class SelectionUpdate(BaseModel):
    items: list[SelectionItem]


@router.get("/sources/{source_id}/discover")
@inject
async def discover(source_id: UUID, svc: FromDishka[SelectionService]) -> list[dict]:
    """List all tables in the source database with `selected` flag from whitelist."""
    return await svc.discover(source_id)


@router.get("/sources/{source_id}/selection")
@inject
async def get_selection(source_id: UUID, svc: FromDishka[SelectionService]) -> list[dict]:
    return await svc.get(source_id)


@router.put("/sources/{source_id}/selection")
@inject
async def replace_selection(
    source_id: UUID,
    payload: SelectionUpdate,
    svc: FromDishka[SelectionService],
) -> dict:
    n = await svc.replace(source_id, [i.model_dump() for i in payload.items])
    return {"saved": n}
