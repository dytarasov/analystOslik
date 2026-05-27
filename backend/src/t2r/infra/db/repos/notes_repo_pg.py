from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


class NotesRepoPg:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _upsert_note_row(
        self,
        *,
        source_id: UUID,
        scope: str,
        target_id: UUID | None,
        title: str,
        body_md: str,
        tags: list[str],
        null_embedding_on_update: bool,
    ) -> UUID:
        """One-per-(scope, target) note upsert, race-safe against the
        md_notes_scope_target_uq index: SELECT→UPDATE the existing row, else
        INSERT under a savepoint and, if a concurrent writer won the insert
        race, fall back to UPDATE the row they created (instead of duplicating)."""
        set_emb = ", embedding = NULL" if null_embedding_on_update else ""

        async def _update(note_id: UUID) -> UUID:
            await self.session.execute(
                text(
                    "UPDATE md_notes SET title = :ti, body_md = :body, tags = :tags"
                    + set_emb
                    + ", updated_at = now() WHERE id = :id"
                ),
                {"id": note_id, "ti": title, "body": body_md, "tags": tags},
            )
            return note_id

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
            return await _update(existing[0])

        try:
            async with self.session.begin_nested():
                row = (
                    await self.session.execute(
                        text(
                            "INSERT INTO md_notes (source_id, scope, target_id, title, body_md, tags)"
                            " VALUES (:sid, :sc, :t, :ti, :body, :tags) RETURNING id"
                        ),
                        {
                            "sid": source_id, "sc": scope, "t": target_id,
                            "ti": title, "body": body_md, "tags": tags,
                        },
                    )
                ).first()
            assert row is not None
            return row[0]
        except IntegrityError:
            # Lost the insert race — the unique index rejected the duplicate.
            # Match the primary SELECT exactly: scope by source_id and use
            # IS NOT DISTINCT FROM so a NULL target_id still finds the row.
            dup = (
                await self.session.execute(
                    text(
                        "SELECT id FROM md_notes WHERE source_id = :sid AND scope = :sc"
                        " AND target_id IS NOT DISTINCT FROM :t"
                    ),
                    {"sid": source_id, "sc": scope, "t": target_id},
                )
            ).first()
            if dup:
                return await _update(dup[0])
            raise

    async def upsert_table_note(
        self,
        *,
        source_id: UUID,
        target_id: UUID,
        title: str,
        body_md: str,
        tags: list[str],
    ) -> UUID:
        return await self._upsert_note_row(
            source_id=source_id, scope="table", target_id=target_id,
            title=title, body_md=body_md, tags=tags,
            null_embedding_on_update=False,
        )

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
        return await self._upsert_note_row(
            source_id=source_id, scope=scope, target_id=target_id,
            title=title, body_md=body_md, tags=tags,
            null_embedding_on_update=True,
        )

    async def insert_note(
        self,
        *,
        source_id: UUID,
        scope: str,
        target_id: UUID | None,
        title: str,
        body_md: str,
        tags: list[str],
    ) -> UUID:
        """Plain insert (no upsert) — used for glossary-derived notes where many
        rows share scope='free'/target_id=NULL and must coexist. Re-ingest
        clears the old ones via ``delete_glossary_notes`` first."""
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

    async def delete_glossary_notes(self, source_id: UUID) -> int:
        """Remove all notes previously produced by glossary ingest (tagged
        'glossary'), so re-ingesting is idempotent rather than duplicating."""
        res = await self.session.execute(
            text(
                "DELETE FROM md_notes WHERE source_id = :sid"
                " AND 'glossary' = ANY(tags)"
            ),
            {"sid": source_id},
        )
        return res.rowcount or 0

    async def delete_by_target(
        self, *, source_id: UUID, scope: str, target_id: UUID
    ) -> int:
        """Remove the note(s) for a (scope, target) — used when a column is
        disabled so its RAG note (+embedding) stops being retrievable."""
        res = await self.session.execute(
            text(
                "DELETE FROM md_notes WHERE source_id = :sid AND scope = :sc"
                " AND target_id = :t"
            ),
            {"sid": source_id, "sc": scope, "t": target_id},
        )
        return res.rowcount or 0

    async def get_note_meta(
        self, *, source_id: UUID, scope: str, target_id: UUID
    ) -> dict | None:
        """Existing note's id + body + whether it already has an embedding — lets
        callers skip a re-embed when the body is unchanged (avoids re-vectorising
        a whole table's column notes when only one column toggled)."""
        row = (
            await self.session.execute(
                text(
                    "SELECT id, body_md, (embedding IS NOT NULL) AS has_embedding"
                    " FROM md_notes WHERE source_id = :sid AND scope = :sc"
                    " AND target_id = :t"
                ),
                {"sid": source_id, "sc": scope, "t": target_id},
            )
        ).mappings().first()
        return dict(row) if row else None

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
