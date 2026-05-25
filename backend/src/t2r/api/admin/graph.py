from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter

from t2r.api.deps import AdminDep
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
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
    """Re-materialise Neo4j from the Postgres semantic layer.

    Useful when the graph desynced — old bug dropped columns silently if the
    Table node didn't exist yet. Call this once to backfill.
    """
    tables = await repo.list_tables(source_id)
    table_count = 0
    column_count = 0
    rel_count = 0

    for t in tables:
        await graph.upsert_table(
            id=str(t["id"]),
            source_id=str(source_id),
            database=t["database"],
            name=t["table_name"],
            title=t.get("title"),
            domain=t.get("domain"),
            status=t.get("confirmation_status") or "draft",
        )
        table_count += 1

        cols = await repo.get_columns(t["id"])
        for c in cols:
            await graph.upsert_column(
                id=str(c["id"]),
                table_id=str(t["id"]),
                name=c["name"],
                data_type=c["data_type"],
                role=c.get("semantic_role"),
                status=c.get("confirmation_status") or "draft",
            )
            column_count += 1

    relations = await repo.get_relations(source_id)
    for r in relations:
        if not r.get("from_column_id") or not r.get("to_column_id"):
            continue
        try:
            await graph.upsert_relation(
                from_col=str(r["from_column_id"]),
                to_col=str(r["to_column_id"]),
                kind=r["kind"],
                confidence=float(r["confidence"]),
                reasoning=r.get("reasoning"),
            )
            rel_count += 1
        except Exception:  # noqa: BLE001
            logger.exception("resync relation failed", rel_id=str(r["id"]))

    logger.info(
        "graph resync done",
        source_id=str(source_id),
        tables=table_count,
        columns=column_count,
        relations=rel_count,
    )
    return {"tables": table_count, "columns": column_count, "relations": rel_count}
