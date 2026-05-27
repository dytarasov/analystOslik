from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter

from t2r.api.deps import AdminDep
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.graph.sync import resync_source_graph
from t2r.logging import get_logger

logger = get_logger("api.graph")

router = APIRouter(prefix="/api/admin", tags=["admin-graph"], dependencies=[AdminDep])


@router.post("/sources/{source_id}/graph/resync")
@inject
async def resync_graph(
    source_id: UUID,
    repo: FromDishka[SemanticRepoPg],
    graph: FromDishka[GraphRepoNeo4j],
) -> dict:
    """Re-materialise Neo4j from the Postgres semantic layer (manual trigger)."""
    res = await resync_source_graph(repo, graph, source_id)
    logger.info("graph resync done", source_id=str(source_id), **res)
    return res
