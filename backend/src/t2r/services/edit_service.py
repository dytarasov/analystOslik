from __future__ import annotations

import asyncio
from uuid import UUID

from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.agents.admin_edit.pipeline import build_admin_edit_pipeline
from t2r.agents.admin_profiling.describe import describe_columns
from t2r.agents.admin_profiling.note_writer import rebuild_table_notes
from t2r.agents.admin_profiling.pass2 import group_columns
from t2r.agents.admin_profiling.pipeline import _format_sample
from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.registry import RunRegistry
from t2r.agents.orchestrator.run import AgentRun
from t2r.agents.orchestrator.step import Step
from t2r.domain.events.types import result_final, step_progress
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.profiling_repo_pg import ProfilingRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.graph.sync import try_resync_source_graph
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.json_extractor import extract_json
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.logging import get_logger

logger = get_logger("edit_service")


class _RegenerateStep(Step):
    """Phase 1 of a table re-profile: re-describe the table summary itself
    (title/description/domain/grain/tags) from the latest harvested DDL+sample,
    honouring the admin's guidance/user_notes. Columns are handled by
    ``_ReprofileColumnsStep`` next. Opens its own short-lived session."""

    def __init__(
        self,
        *,
        table_id: UUID,
        guidance: str | None,
        actor: str,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        prompts: PromptLoader,
    ) -> None:
        super().__init__(step_id="regenerate", name="Перепрофилирую таблицу")
        self.table_id = table_id
        self.guidance = guidance
        self.actor = actor
        self.sm = sessionmaker
        self.llm = llm
        self.prompts = prompts

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        from sqlalchemy import text

        await run.emit(step_progress(self.step_id, 0.2, "Заново описываю таблицу"))
        async with self.sm() as session:
            semantic = SemanticRepoPg(session)
            table = await semantic.get_table(self.table_id)
            if not table:
                raise RuntimeError("Таблица не найдена")
            # Latest harvested DDL + sample for this table.
            row = (
                await session.execute(
                    text(
                        "SELECT ddl, sample FROM profiling_run_tables"
                        " WHERE database = :db AND table_name = :tbl"
                        " AND run_id IN (SELECT id FROM profiling_runs WHERE source_id = :sid)"
                        " ORDER BY started_at DESC LIMIT 1"
                    ),
                    {
                        "db": table["database"],
                        "tbl": table["table_name"],
                        "sid": table["source_id"],
                    },
                )
            ).first()
            ddl = row[0] if row else ""
            sample = row[1] if row else None
            sample_preview = _format_sample(sample, n_rows=3) if sample else "(нет данных)"
            user_notes = self.guidance or table.get("user_notes") or ""

            prompt = self.prompts.render(
                "regenerator",
                database=table["database"],
                table=table["table_name"],
                title=table.get("title"),
                description=table.get("description"),
                domain=table.get("domain"),
                tags=table.get("tags") or [],
                ddl=ddl,
                sample_preview=sample_preview,
                user_notes=user_notes,
            )
            await run.emit(step_progress(self.step_id, 0.6, "Генерирую описание таблицы"))
            out = await self.llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            try:
                new = extract_json(out)
            except Exception:
                new = {"description": out[:500]}

            await semantic.add_revision(
                entity_kind="sem_table",
                entity_id=self.table_id,
                payload={k: table.get(k) for k in ("title", "description", "domain", "tags")},
                actor=self.actor,
                reason="regenerate",
            )
            await semantic.update_table(
                self.table_id,
                title=new.get("title"),
                description=new.get("description"),
                domain=new.get("domain"),
                tags=new.get("tags"),
            )
            await session.commit()


class _ReprofileColumnsStep(Step):
    """Phase 2 of a table re-profile: (re)describe every *enabled* column from
    its harvested facts, then rebuild the table's RAG notes and resync the graph.

    Respects the admin's column choices: disabled columns are left excluded (not
    described, not re-enabled), re-enabled-but-undescribed columns finally get a
    description, and locked (hand-edited) columns are skipped by
    ``apply_column_description``. Each group runs in its own short-lived session
    so a long table doesn't hold one PG connection idle across many LLM calls."""

    def __init__(
        self,
        *,
        table_id: UUID,
        sessionmaker: async_sessionmaker[AsyncSession],
        embeddings: EmbeddingsClient,
        neo4j_driver: AsyncDriver,
        llm: LLMClient,
        prompts: PromptLoader,
    ) -> None:
        super().__init__(step_id="reprofile_columns", name="Профилирую колонки")
        self.table_id = table_id
        self.sm = sessionmaker
        self.embeddings = embeddings
        self.driver = neo4j_driver
        self.llm = llm
        self.prompts = prompts

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        async with self.sm() as s:
            enabled = await SemanticRepoPg(s).get_columns(self.table_id, only_enabled=True)
        groups = group_columns(enabled) if enabled else []
        total = max(len(groups), 1)
        failed_groups = 0
        for i, group in enumerate(groups):
            await run.emit(
                step_progress(self.step_id, i / total, f"Колонки {i + 1}/{len(groups)}")
            )
            async with self.sm() as s:
                try:
                    await describe_columns(
                        llm=self.llm,
                        prompts=self.prompts,
                        semantic=SemanticRepoPg(s),
                        table_id=self.table_id,
                        names=[c["name"] for c in group],
                    )
                    await s.commit()
                except Exception as exc:  # noqa: BLE001
                    # Best-effort per group: a failure here must NOT abort the step.
                    # Phase 1 (table summary) is already committed, so raising would
                    # report the run 'failed' while the summary stands (status lie)
                    # and skip the notes rebuild. Skip the group, keep its prior
                    # description, and finish — mirrors the per-group design.
                    failed_groups += 1
                    logger.warning(
                        "regenerate: column group describe failed — skipped",
                        table_id=str(self.table_id),
                        group=[c["name"] for c in group],
                        error=str(exc),
                    )
        if groups and failed_groups == len(groups):
            # Nothing described, but phase 1 stands and columns keep prior
            # descriptions — report (non-fatally) rather than wedge the run 'failed'.
            await run.emit(
                step_progress(
                    self.step_id, 0.95,
                    "Колонки не переописаны (LLM недоступен) — описание таблицы обновлено",
                )
            )
        # Rebuild RAG notes + resync graph from the now-described enabled columns
        # so the agent immediately sees the refreshed (and newly re-included) ones.
        async with self.sm() as s:
            semantic = SemanticRepoPg(s)
            table = await semantic.get_table(self.table_id)
            if table:
                cols = await semantic.get_columns(self.table_id, only_enabled=True)
                await rebuild_table_notes(
                    notes_repo=NotesRepoPg(s),
                    embeddings=self.embeddings,
                    source_id=table["source_id"],
                    table=table,
                    columns=cols,
                )
                await s.commit()
                await try_resync_source_graph(
                    semantic, GraphRepoNeo4j(self.driver), table["source_id"]
                )
        await run.emit(
            step_progress(self.step_id, 1.0, f"Описано колонок: {len(enabled)}")
        )


class _RegenerateColumnStep(Step):
    """Re-describe a single column. Commits inside its own short session BEFORE
    emitting result_final, so the run is never reported successful (Pipeline.run
    finalises right after) while the write is still uncommitted — a commit failure
    must surface as a real failure, not a phantom success with the old value."""

    def __init__(
        self,
        *,
        column_id: UUID,
        guidance: str | None,
        actor: str,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        prompts: PromptLoader,
    ) -> None:
        super().__init__(step_id="regenerate_column", name="Перегенерирую колонку")
        self.column_id = column_id
        self.guidance = guidance
        self.actor = actor
        self.sm = sessionmaker
        self.llm = llm
        self.prompts = prompts

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        await run.emit(step_progress(self.step_id, 0.2, "Готовлю контекст"))
        async with self.sm() as session:
            semantic = SemanticRepoPg(session)
            column = await semantic.get_column(self.column_id)
            if not column:
                raise RuntimeError("Колонка не найдена")

            guidance = self.guidance or column.get("user_notes") or ""

            prompt = self.prompts.render(
                "column_regenerate",
                database=column["database"],
                table=column["table_name"],
                table_title=column.get("table_title") or "",
                table_description=column.get("table_description") or "",
                column=column,
                guidance=guidance,
            )
            await run.emit(step_progress(self.step_id, 0.5, "Генерирую"))
            out = await self.llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            try:
                new = extract_json(out) or {}
            except Exception:
                new = {"description": out[:500]}

            await semantic.add_revision(
                entity_kind="sem_column",
                entity_id=self.column_id,
                payload={
                    k: column.get(k)
                    for k in ("description", "semantic_role", "user_notes")
                },
                actor=self.actor,
                reason="regenerate_column",
            )
            await semantic.update_column(
                self.column_id,
                description=new.get("description"),
                semantic_role=new.get("semantic_role"),
            )
            await session.commit()
        await run.emit(
            result_final(
                summary=new.get("description") or "Готово",
                sql=None,
                preview={"new": new, "column_id": str(self.column_id)},
                export_url=None,
            )
        )


class EditService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        neo4j_driver: AsyncDriver,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        prompts: PromptLoader,
        registry: RunRegistry,
    ) -> None:
        self.sm = sessionmaker
        self.driver = neo4j_driver
        self.llm = llm
        self.embeddings = embeddings
        self.prompts = prompts
        self.registry = registry

    async def _sync_graph(self, session: AsyncSession, source_id: UUID | None) -> None:
        """Push the edited semantic layer to Neo4j so the agent's graph tools
        stay current. Best-effort — PG remains the source of truth."""
        if not source_id:
            return
        await try_resync_source_graph(
            SemanticRepoPg(session), GraphRepoNeo4j(self.driver), source_id
        )

    async def regenerate_table(
        self, table_id: UUID, *, actor: str, guidance: str | None
    ) -> str:
        agent_run = AgentRun(kind="regenerate")
        await self.registry.add(agent_run)
        task = asyncio.create_task(self._run_regenerate(table_id, agent_run, actor, guidance))
        agent_run.attach_task(task)
        return agent_run.id

    async def regenerate_column(
        self, column_id: UUID, *, actor: str, guidance: str | None
    ) -> str:
        agent_run = AgentRun(kind="regenerate_column")
        await self.registry.add(agent_run)
        task = asyncio.create_task(
            self._run_regenerate_column(column_id, agent_run, actor, guidance)
        )
        agent_run.attach_task(task)
        return agent_run.id

    async def _run_regenerate_column(
        self,
        column_id: UUID,
        agent_run: AgentRun,
        actor: str,
        guidance: str | None,
    ) -> None:
        try:
            step = _RegenerateColumnStep(
                column_id=column_id,
                guidance=guidance,
                actor=actor,
                sessionmaker=self.sm,
                llm=self.llm,
                prompts=self.prompts,
            )
            # The step commits before result_final; Pipeline.run finalises the run.
            await Pipeline([step]).run(agent_run)
            # Resync the graph from the now-committed state (own short session).
            async with self.sm() as session:
                col = await SemanticRepoPg(session).get_column(column_id)
                await self._sync_graph(session, col.get("source_id") if col else None)
        except Exception as exc:  # noqa: BLE001
            logger.exception("regenerate column failed")
            if not agent_run.is_finished:
                await agent_run.finalize(error=str(exc))

    async def _run_regenerate(
        self,
        table_id: UUID,
        agent_run: AgentRun,
        actor: str,
        guidance: str | None,
    ) -> None:
        # A full table re-profile: re-describe the table summary, then (re)describe
        # every enabled column. Each step manages its own sessions; Pipeline.run
        # finalises the run (emits `done`). Notes + graph are refreshed in step 2.
        try:
            pipeline = Pipeline(
                [
                    _RegenerateStep(
                        table_id=table_id,
                        guidance=guidance,
                        actor=actor,
                        sessionmaker=self.sm,
                        llm=self.llm,
                        prompts=self.prompts,
                    ),
                    _ReprofileColumnsStep(
                        table_id=table_id,
                        sessionmaker=self.sm,
                        embeddings=self.embeddings,
                        neo4j_driver=self.driver,
                        llm=self.llm,
                        prompts=self.prompts,
                    ),
                ]
            )
            await pipeline.run(agent_run)
        except Exception as exc:  # noqa: BLE001
            logger.exception("regenerate failed")
            if not agent_run.is_finished:
                await agent_run.finalize(error=str(exc))

    async def admin_edit(self, source_id: UUID, prompt: str, *, actor: str) -> str:
        agent_run = AgentRun(kind="admin_edit")
        await self.registry.add(agent_run)
        task = asyncio.create_task(self._run_edit(source_id, prompt, agent_run, actor))
        agent_run.attach_task(task)
        return agent_run.id

    async def _run_edit(
        self,
        source_id: UUID,
        prompt: str,
        agent_run: AgentRun,
        actor: str,
    ) -> None:
        try:
            async with self.sm() as session:
                pipeline = build_admin_edit_pipeline(
                    prompt=prompt,
                    source_id=source_id,
                    actor=actor,
                    session=session,
                    semantic_repo=SemanticRepoPg(session),
                    notes_repo=NotesRepoPg(session),
                    llm=self.llm,
                    prompts=self.prompts,
                )
                await pipeline.run(agent_run)
                await self._sync_graph(session, source_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("admin edit failed")
            await agent_run.finalize(error=str(exc))


# alias to silence "unused" linters
_ = ProfilingRepoPg
