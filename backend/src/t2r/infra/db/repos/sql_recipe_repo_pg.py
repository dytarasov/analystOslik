from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


class SqlRecipeRepoPg:
    """Per-source store of typical SQL recipes (title + intent + verbatim SQL).

    Only the natural-language ``intent`` is embedded for retrieval — the SQL is
    kept verbatim and never vectorized. The recipe set per source is small, so
    search is an exact cosine scan (no ANN index needed).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_recipes(
        self, source_id: UUID, recipes: list[dict[str, Any]]
    ) -> int:
        """Wipe the source's recipes and insert the given set (full replace, so a
        re-ingest never leaves orphans). Each recipe: title, intent, sql, tables,
        embedding (list[float] | None)."""
        await self.session.execute(
            text("DELETE FROM sql_recipes WHERE source_id = :sid"),
            {"sid": source_id},
        )
        for r in recipes:
            emb = r.get("embedding")
            await self.session.execute(
                text(
                    "INSERT INTO sql_recipes (source_id, title, intent, sql, tables, embedding)"
                    " VALUES (:sid, :title, :intent, :sql, :tables, "
                    + ("CAST(:emb AS vector)" if emb else "NULL")
                    + ")"
                ),
                {
                    "sid": source_id,
                    "title": r.get("title") or "(без названия)",
                    "intent": r.get("intent") or "",
                    "sql": r.get("sql") or "",
                    "tables": list(r.get("tables") or []),
                    **({"emb": _vec_literal(emb)} if emb else {}),
                },
            )
        return len(recipes)

    async def search_recipes(
        self, source_id: UUID, embedding: list[float], limit: int = 5
    ) -> list[dict[str, Any]]:
        """Top-k recipes by intent-embedding cosine similarity."""
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, title, intent, sql, tables,"
                    " 1 - (embedding <=> CAST(:e AS vector)) AS score"
                    " FROM sql_recipes WHERE source_id = :sid AND embedding IS NOT NULL"
                    " ORDER BY embedding <=> CAST(:e AS vector) LIMIT :lim"
                ),
                {"sid": source_id, "e": _vec_literal(embedding), "lim": limit},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def list_recipes(
        self, source_id: UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        """All recipes (fallback when there's no query to embed)."""
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, title, intent, sql, tables FROM sql_recipes"
                    " WHERE source_id = :sid ORDER BY created_at LIMIT :lim"
                ),
                {"sid": source_id, "lim": limit},
            )
        ).mappings().all()
        return [dict(r) for r in rows]
