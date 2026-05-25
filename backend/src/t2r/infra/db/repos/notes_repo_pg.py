from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


class NotesRepoPg:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_table_note(
        self,
        *,
        source_id: UUID,
        target_id: UUID,
        title: str,
        body_md: str,
        tags: list[str],
    ) -> UUID:
        # one note per (scope=table, target_id)
        existing = (
            await self.session.execute(
                text(
                    "SELECT id FROM md_notes WHERE scope='table' AND target_id = :t"
                ),
                {"t": target_id},
            )
        ).first()
        if existing:
            await self.session.execute(
                text(
                    "UPDATE md_notes SET title = :ti, body_md = :body, tags = :tags,"
                    " updated_at = now() WHERE id = :id"
                ),
                {"id": existing[0], "ti": title, "body": body_md, "tags": tags},
            )
            return existing[0]
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO md_notes (source_id, scope, target_id, title, body_md, tags)"
                    " VALUES (:sid, 'table', :t, :ti, :body, :tags) RETURNING id"
                ),
                {"sid": source_id, "t": target_id, "ti": title, "body": body_md, "tags": tags},
            )
        ).first()
        assert row is not None
        return row[0]

    async def upsert_note(
        self,
        *,
        source_id: UUID,
        scope: str,
        target_id: UUID | None,
        title: str,
        body_md: str,
        tags: list[str],
    ) -> UUID:
        """Upsert a note for any scope (column/free/domain), keyed on
        (scope, target_id). Used for column-level notes and the source overview.
        """
        existing = (
            await self.session.execute(
                text(
                    "SELECT id FROM md_notes WHERE source_id = :sid AND scope = :sc"
                    " AND target_id IS NOT DISTINCT FROM :t"
                ),
                {"sid": source_id, "sc": scope, "t": target_id},
            )
        ).first()
        if existing:
            await self.session.execute(
                text(
                    "UPDATE md_notes SET title = :ti, body_md = :body, tags = :tags,"
                    " embedding = NULL, updated_at = now() WHERE id = :id"
                ),
                {"id": existing[0], "ti": title, "body": body_md, "tags": tags},
            )
            return existing[0]
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO md_notes (source_id, scope, target_id, title, body_md, tags)"
                    " VALUES (:sid, :sc, :t, :ti, :body, :tags) RETURNING id"
                ),
                {
                    "sid": source_id,
                    "sc": scope,
                    "t": target_id,
                    "ti": title,
                    "body": body_md,
                    "tags": tags,
                },
            )
        ).first()
        assert row is not None
        return row[0]

    async def set_embedding(self, note_id: UUID, embedding: list[float]) -> None:
        await self.session.execute(
            text(
                "UPDATE md_notes SET embedding = CAST(:e AS vector) WHERE id = :id"
            ),
            {"id": note_id, "e": _vec_literal(embedding)},
        )

    async def search(
        self, source_id: UUID, embedding: list[float], limit: int = 10
    ) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, scope, target_id, title, body_md, tags,"
                    " 1 - (embedding <=> CAST(:e AS vector)) AS score"
                    " FROM md_notes WHERE source_id = :sid AND embedding IS NOT NULL"
                    " ORDER BY embedding <=> CAST(:e AS vector) LIMIT :lim"
                ),
                {"sid": source_id, "e": _vec_literal(embedding), "lim": limit},
            )
        ).mappings().all()
        return [dict(r) for r in rows]
