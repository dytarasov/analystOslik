from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from t2r.infra.clickhouse.factory import CHClientFactory
from t2r.infra.clickhouse.profiler import CHProfiler
from t2r.infra.db.repos.selection_repo_pg import SelectionRepoPg
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.security.cipher import FernetCipher


class SelectionService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        cipher: FernetCipher,
    ) -> None:
        self.sm = sessionmaker
        self.cipher = cipher

    async def discover(self, source_id: UUID) -> list[dict[str, Any]]:
        """Read the source database catalogue without persisting anything.

        Returns flat list: [{database, table, engine, total_rows, total_bytes, selected}].
        `selected` reflects the existing whitelist so the UI can pre-check checkboxes.
        """
        async with self.sm() as session:
            repo = SourceRepoPg(session, self.cipher)
            factory = CHClientFactory(repo)
            client = await factory.for_source(source_id)
            try:
                profiler = CHProfiler(client)
                databases = await profiler.fetch_databases()
                out: list[dict[str, Any]] = []
                for db in databases:
                    tables = await profiler.fetch_tables(db)
                    for t in tables:
                        out.append(
                            {
                                "database": db,
                                "table": t["name"],
                                "engine": t.get("engine"),
                                "total_rows": t.get("total_rows"),
                                "total_bytes": t.get("total_bytes"),
                            }
                        )
            finally:
                await client.close()

            selected = {
                (r["database"], r["table_name"])
                for r in await SelectionRepoPg(session).get(source_id)
            }
            for item in out:
                item["selected"] = (item["database"], item["table"]) in selected
            return out

    async def get(self, source_id: UUID) -> list[dict]:
        async with self.sm() as session:
            return await SelectionRepoPg(session).get(source_id)

    async def replace(self, source_id: UUID, items: list[dict]) -> int:
        async with self.sm() as session:
            n = await SelectionRepoPg(session).replace(source_id, items)
            await session.commit()
            return n
