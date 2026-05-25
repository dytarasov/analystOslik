from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter

from t2r.infra.db.repos.source_repo_pg import SourceRepoPg

router = APIRouter(prefix="/api/sources", tags=["client-sources"])


@router.get("/public")
@inject
async def list_public_sources(repo: FromDishka[SourceRepoPg]) -> list[dict]:
    sources = await repo.list()
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "database": s.database,
            "readonly_verified": s.readonly_verified,
        }
        for s in sources
    ]
