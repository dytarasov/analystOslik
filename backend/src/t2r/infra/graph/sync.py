from __future__ import annotations

from uuid import UUID

from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.logging import get_logger

logger = get_logger("graph.sync")


async def resync_source_graph(
    repo: SemanticRepoPg, graph: GraphRepoNeo4j, source_id: UUID
) -> dict:
    """Re-materialise Neo4j from the Postgres semantic layer for one source.

    Idempotent: upserts every table/column node and FK/inferred/conceptual edge.
    Called after profiling AND after any edit (manual, admin_edit, table_chat,
    regenerate, glossary) so the agent's graph tools never read stale data.
    """
    tables = await repo.list_tables(source_id)
    table_count = column_count = rel_count = 0

    # Drop nodes for tables that no longer exist in PG (removed from selection /
    # renamed), so the graph stays an exact mirror of the source.
    await graph.prune_source_tables(str(source_id), [str(t["id"]) for t in tables])

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
        # Only enabled columns are mirrored; disabled/removed ones are pruned so
        # the graph (and `related_tables`) never surface a hidden column.
        enabled_cols = await repo.get_columns(t["id"], only_enabled=True)
        await graph.prune_table_columns(
            str(t["id"]), [str(c["id"]) for c in enabled_cols]
        )
        for c in enabled_cols:
            await graph.upsert_column(
                id=str(c["id"]),
                table_id=str(t["id"]),
                name=c["name"],
                data_type=c["data_type"],
                role=c.get("semantic_role"),
                status=c.get("confirmation_status") or "draft",
            )
            column_count += 1

    for r in await repo.get_relations(source_id, only_enabled=True):
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
            logger.exception("resync relation failed", rel_id=str(r.get("id")))

    return {"tables": table_count, "columns": column_count, "relations": rel_count}


async def try_resync_source_graph(
    repo: SemanticRepoPg, graph: GraphRepoNeo4j, source_id: UUID
) -> None:
    """Best-effort resync — never let a Neo4j hiccup fail the edit that triggered
    it. The PG layer stays the source of truth; the graph just lags until the
    next successful sync."""
    try:
        await resync_source_graph(repo, graph, source_id)
    except Exception:  # noqa: BLE001
        logger.exception("graph resync (best-effort) failed", source_id=str(source_id))
