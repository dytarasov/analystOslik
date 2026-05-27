from __future__ import annotations

from typing import Any
from uuid import UUID

from t2r.agents.admin_profiling.note_writer import rebuild_table_notes
from t2r.errors import NotFoundError
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.graph.sync import try_resync_source_graph
from t2r.infra.llm.embeddings import EmbeddingsClient


class SemanticService:
    def __init__(
        self,
        repo: SemanticRepoPg,
        graph: GraphRepoNeo4j,
        notes_repo: NotesRepoPg,
        embeddings: EmbeddingsClient,
    ) -> None:
        self.repo = repo
        self.graph = graph
        self.notes_repo = notes_repo
        self.embeddings = embeddings

    async def _sync(self, source_id: UUID | None) -> None:
        if source_id:
            # Commit PG first so the graph is only ever synced from durably
            # persisted state. Otherwise a later rollback of this request's
            # transaction would leave Neo4j ahead of Postgres (PG is the source of
            # truth, the graph is best-effort and rebuilt on the next re-profile).
            await self.repo.session.commit()
            await try_resync_source_graph(self.repo, self.graph, source_id)

    async def _rebuild_table_notes(self, table_id: UUID, source_id: UUID) -> None:
        """Regenerate the table note + enabled column notes from the current
        semantic layer (best-effort — a note/embedding hiccup must not fail the
        edit)."""
        try:
            table = await self.repo.get_table(table_id)
            if not table:
                return
            # get_table returns a slim row; list_tables has the physical meta the
            # note renders (engine/rows/keys).
            full = next(
                (t for t in await self.repo.list_tables(source_id)
                 if t["id"] == table_id),
                table,
            )
            cols = await self.repo.get_columns(table_id, only_enabled=True)
            await rebuild_table_notes(
                notes_repo=self.notes_repo,
                embeddings=self.embeddings,
                source_id=source_id,
                table=full,
                columns=cols,
            )
        except Exception:  # noqa: BLE001
            pass

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
            lock=True,
        )
        await self._sync(prev["source_id"])
        return await self.get_table(table_id)

    async def confirm_table(self, table_id: UUID, actor: str) -> dict[str, Any]:
        await self.repo.confirm_table(table_id, actor)
        t = await self.repo.get_table(table_id)
        await self._sync(t["source_id"] if t else None)
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
            lock=True,
        )
        await self._sync(prev.get("source_id"))
        return await self.get_column(column_id)

    async def confirm_column(self, column_id: UUID, actor: str) -> dict[str, Any]:
        await self.repo.confirm_column(column_id, actor)
        c = await self.repo.get_column(column_id)
        await self._sync(c.get("source_id") if c else None)
        return await self.get_column(column_id)

    async def set_column_enabled(
        self,
        column_id: UUID,
        *,
        enabled: bool,
        actor: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Exclude (or re-include) a column from downstream investigation, with
        the full cascade: flip the flag, drop/rebuild RAG notes, resync the graph
        (prunes the node + its edges, or restores them), and snapshot a revision.

        The hard facts stay in the row either way, so re-enabling is cheap. A
        re-enabled column that was never described keeps appearing draft until a
        single-column re-profile fills it in (handled separately).
        """
        col = await self.repo.get_column(column_id)
        if not col:
            raise NotFoundError("Колонка не найдена")
        source_id = col["source_id"]
        table_id = col["table_id"]

        await self.repo.add_revision(
            entity_kind="sem_column",
            entity_id=column_id,
            payload={
                k: col.get(k) for k in ("description", "semantic_role", "user_notes")
            }
            | {"enabled": col.get("enabled")},
            actor=actor,
            reason=reason or ("отключение колонки" if not enabled else "включение колонки"),
        )

        await self.repo.set_column_enabled(column_id, enabled)

        if not enabled:
            # The column's own RAG note must stop being retrievable; rebuild drops
            # it from the table note too.
            await self.notes_repo.delete_by_target(
                source_id=source_id, scope="column", target_id=column_id
            )
        # Persist the structural change before the slow, best-effort note rebuild
        # (which calls the embeddings API) so we don't hold the write transaction
        # open across a network round-trip.
        await self.repo.session.commit()
        await self._rebuild_table_notes(table_id, source_id)
        await self._sync(source_id)
        return await self.get_column(column_id)

    async def set_columns_enabled(
        self,
        table_id: UUID,
        *,
        names: list[str],
        enabled: bool,
        actor: str,
    ) -> dict[str, Any]:
        """Bulk enable/disable columns of one table by name (the 'disable
        selected' action and the dry-run column-selection gate). Flips all rows,
        then rebuilds notes + resyncs the graph once."""
        table = await self.repo.get_table(table_id)
        if not table:
            raise NotFoundError("Таблица не найдена")
        source_id = table["source_id"]
        wanted = set(names)
        cols = await self.repo.get_columns(table_id)
        affected = [c for c in cols if c["name"] in wanted]

        n = await self.repo.set_columns_enabled(table_id, list(wanted), enabled)
        if not enabled:
            for c in affected:
                await self.notes_repo.delete_by_target(
                    source_id=source_id, scope="column", target_id=c["id"]
                )
        # Commit the structural change before the slow note rebuild (embeddings).
        await self.repo.session.commit()
        await self._rebuild_table_notes(table_id, source_id)
        await self._sync(source_id)
        return {"updated": n}

    async def list_table_revisions(self, table_id: UUID) -> list[dict[str, Any]]:
        return await self.repo.list_revisions(
            entity_kind="sem_table", entity_id=table_id
        )

    async def list_column_revisions(self, column_id: UUID) -> list[dict[str, Any]]:
        return await self.repo.list_revisions(
            entity_kind="sem_column", entity_id=column_id
        )

    async def restore_table_revision(
        self, table_id: UUID, revision: int, actor: str
    ) -> dict[str, Any]:
        rev = await self.repo.get_revision(
            entity_kind="sem_table", entity_id=table_id, revision=revision
        )
        if not rev:
            raise NotFoundError("Ревизия не найдена")
        p = rev.get("payload") or {}
        # Reuses update_table → snapshots the current state as a new revision,
        # applies the old values, and resyncs the graph.
        return await self.update_table(
            table_id,
            actor=actor,
            title=p.get("title"),
            description=p.get("description"),
            domain=p.get("domain"),
            tags=p.get("tags"),
            user_notes=p.get("user_notes"),
            reason=f"восстановление ревизии {revision}",
        )

    async def restore_column_revision(
        self, column_id: UUID, revision: int, actor: str
    ) -> dict[str, Any]:
        rev = await self.repo.get_revision(
            entity_kind="sem_column", entity_id=column_id, revision=revision
        )
        if not rev:
            raise NotFoundError("Ревизия не найдена")
        p = rev.get("payload") or {}
        return await self.update_column(
            column_id,
            actor=actor,
            description=p.get("description"),
            semantic_role=p.get("semantic_role"),
            user_notes=p.get("user_notes"),
            reason=f"восстановление ревизии {revision}",
        )
