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

    async def neighbors(self, table_id: str) -> list[dict[str, Any]]:
        async with self.driver.session() as s:
            res = await s.run(q.NEIGHBORS, table_id=table_id)
            return [dict(r) async for r in res]

    async def subgraph(self, source_id: str) -> list[dict[str, Any]]:
        async with self.driver.session() as s:
            res = await s.run(q.SUBGRAPH_FOR_SOURCE, source_id=source_id)
            return [dict(r) async for r in res]
