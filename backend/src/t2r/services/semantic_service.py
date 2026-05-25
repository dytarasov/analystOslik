from __future__ import annotations

from typing import Any
from uuid import UUID

from t2r.errors import NotFoundError
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg


class SemanticService:
    def __init__(self, repo: SemanticRepoPg) -> None:
        self.repo = repo

    async def list_tables(self, source_id: UUID) -> list[dict[str, Any]]:
        return await self.repo.list_tables(source_id)

    async def get_table(self, table_id: UUID) -> dict[str, Any]:
        t = await self.repo.get_table(table_id)
        if not t:
            raise NotFoundError("Таблица не найдена")
        cols = await self.repo.get_columns(table_id)
        return {**t, "columns": cols}

    async def update_table(
        self,
        table_id: UUID,
        *,
        actor: str,
        title: str | None = None,
        description: str | None = None,
        domain: str | None = None,
        tags: list[str] | None = None,
        user_notes: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        prev = await self.repo.get_table(table_id)
        if not prev:
            raise NotFoundError("Таблица не найдена")
        await self.repo.add_revision(
            entity_kind="sem_table",
            entity_id=table_id,
            payload={k: prev[k] for k in ("title", "description", "domain", "tags", "user_notes")},
            actor=actor,
            reason=reason,
        )
        await self.repo.update_table(
            table_id,
            title=title,
            description=description,
            domain=domain,
            tags=tags,
            user_notes=user_notes,
        )
        return await self.get_table(table_id)

    async def confirm_table(self, table_id: UUID, actor: str) -> dict[str, Any]:
        await self.repo.confirm_table(table_id, actor)
        return await self.get_table(table_id)

    async def get_column(self, column_id: UUID) -> dict[str, Any]:
        c = await self.repo.get_column(column_id)
        if not c:
            raise NotFoundError("Колонка не найдена")
        return c

    async def update_column(
        self,
        column_id: UUID,
        *,
        actor: str,
        description: str | None = None,
        semantic_role: str | None = None,
        user_notes: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        prev = await self.repo.get_column(column_id)
        if not prev:
            raise NotFoundError("Колонка не найдена")
        await self.repo.add_revision(
            entity_kind="sem_column",
            entity_id=column_id,
            payload={
                k: prev.get(k)
                for k in ("description", "semantic_role", "user_notes")
            },
            actor=actor,
            reason=reason,
        )
        await self.repo.update_column(
            column_id,
            description=description,
            semantic_role=semantic_role,
            user_notes=user_notes,
        )
        return await self.get_column(column_id)

    async def confirm_column(self, column_id: UUID, actor: str) -> dict[str, Any]:
        await self.repo.confirm_column(column_id, actor)
        return await self.get_column(column_id)

    async def list_table_revisions(self, table_id: UUID) -> list[dict[str, Any]]:
        return await self.repo.list_revisions(
            entity_kind="sem_table", entity_id=table_id
        )
