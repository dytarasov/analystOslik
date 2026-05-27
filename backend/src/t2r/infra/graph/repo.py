from __future__ import annotations

from typing import Any

from neo4j import AsyncDriver

from t2r.infra.graph import cypher as q


class GraphRepoNeo4j:
    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver

    async def upsert_table(
        self,
        *,
        id: str,
        source_id: str,
        database: str,
        name: str,
        title: str | None,
        domain: str | None,
        status: str,
    ) -> None:
        async with self.driver.session() as s:
            await s.run(
                q.UPSERT_TABLE,
                id=id,
                source_id=source_id,
                database=database,
                name=name,
                title=title,
                domain=domain,
                status=status,
            )

    async def upsert_column(
        self,
        *,
        id: str,
        table_id: str,
        name: str,
        data_type: str,
        role: str | None,
        status: str,
    ) -> None:
        async with self.driver.session() as s:
            await s.run(
                q.UPSERT_COLUMN,
                id=id,
                table_id=table_id,
                name=name,
                data_type=data_type,
                role=role,
                status=status,
            )

    async def upsert_relation(
        self,
        *,
        from_col: str,
        to_col: str,
        kind: str,
        confidence: float,
        reasoning: str | None,
    ) -> None:
        async with self.driver.session() as s:
            await s.run(
                q.UPSERT_RELATION,
                from_col=from_col,
                to_col=to_col,
                kind=kind,
                confidence=confidence,
                reasoning=reasoning,
            )

    async def delete_source(self, source_id: str) -> None:
        """Remove all Table/Column nodes for a source — call on source delete so
        Neo4j doesn't keep orphans the agent could traverse."""
        async with self.driver.session() as s:
            await s.run(q.DELETE_SOURCE, source_id=source_id)

    async def prune_source_tables(self, source_id: str, keep_ids: list[str]) -> None:
        """Drop Table nodes (and their columns) for a source whose id is no
        longer present in PG — keeps a re-profiled/re-selected source authoritative."""
        async with self.driver.session() as s:
            await s.run(q.PRUNE_SOURCE_TABLES, source_id=source_id, keep=keep_ids)

    async def prune_table_columns(self, table_id: str, keep_ids: list[str]) -> None:
        """Drop Column nodes of a table not in the enabled set (disabled/removed
        columns) so the graph mirrors only investigated columns."""
        async with self.driver.session() as s:
            await s.run(q.PRUNE_TABLE_COLUMNS, table_id=table_id, keep=keep_ids)

    async def delete_column(self, column_id: str) -> None:
        """Remove a single Column node (and its relation edges)."""
        async with self.driver.session() as s:
            await s.run(q.DELETE_COLUMN, id=column_id)

    async def neighbors(self, table_id: str) -> list[dict[str, Any]]:
        async with self.driver.session() as s:
            res = await s.run(q.NEIGHBORS, table_id=table_id)
            return [dict(r) async for r in res]

    async def subgraph(self, source_id: str) -> list[dict[str, Any]]:
        async with self.driver.session() as s:
            res = await s.run(q.SUBGRAPH_FOR_SOURCE, source_id=source_id)
            return [dict(r) async for r in res]
