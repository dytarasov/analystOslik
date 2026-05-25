from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from t2r.api.deps import AdminDep

router = APIRouter(prefix="/api/admin", tags=["admin-audit"], dependencies=[AdminDep])


@router.get("/audit")
@inject
async def list_audit(
    session: FromDishka[AsyncSession],
    entity_kind: str | None = None,
    entity_id: UUID | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = (
        "SELECT id, actor, action, entity_kind, entity_id, before, after, reason, created_at"
        " FROM audit_log WHERE 1=1"
    )
    params: dict = {"lim": limit}
    if entity_kind:
        sql += " AND entity_kind = :ek"
        params["ek"] = entity_kind
    if entity_id:
        sql += " AND entity_id = :eid"
        params["eid"] = entity_id
    sql += " ORDER BY created_at DESC LIMIT :lim"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]
