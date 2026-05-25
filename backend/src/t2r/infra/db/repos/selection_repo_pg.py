from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SelectionRepoPg:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, source_id: UUID) -> list[dict]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT database, table_name, note, created_at"
                    " FROM source_table_selections WHERE source_id = :sid"
                    " ORDER BY database, table_name"
                ),
                {"sid": source_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def replace(self, source_id: UUID, items: list[dict]) -> int:
        """Replace the whole whitelist for a source. Returns count saved."""
        await self.session.execute(
            text("DELETE FROM source_table_selections WHERE source_id = :sid"),
            {"sid": source_id},
        )
        if not items:
            return 0
        for it in items:
            await self.session.execute(
                text(
                    "INSERT INTO source_table_selections (source_id, database, table_name, note)"
                    " VALUES (:sid, :db, :tbl, :note)"
                    " ON CONFLICT (source_id, database, table_name) DO NOTHING"
                ),
                {
                    "sid": source_id,
                    "db": it["database"],
                    "tbl": it["table"],
                    "note": it.get("note"),
                },
            )
        return len(items)

    async def has_any(self, source_id: UUID) -> bool:
        row = (
            await self.session.execute(
                text(
                    "SELECT 1 FROM source_table_selections WHERE source_id = :sid LIMIT 1"
                ),
                {"sid": source_id},
            )
        ).first()
        return row is not None
