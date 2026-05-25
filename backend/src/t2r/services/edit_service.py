from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from t2r.agents.admin_edit.pipeline import build_admin_edit_pipeline
from t2r.agents.admin_profiling.pipeline import _format_sample
from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.registry import RunRegistry
from t2r.agents.orchestrator.run import AgentRun
from t2r.agents.orchestrator.step import Step
from t2r.domain.events.types import result_final, step_progress
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.profiling_repo_pg import ProfilingRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.llm.json_extractor import extract_json
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.logging import get_logger

logger = get_logger("edit_service")


class _RegenerateStep(Step):
    def __init__(
        self,
        *,
        table_id: UUID,
        guidance: str | None,
        actor: str,
        session: AsyncSession,
        semantic_repo: SemanticRepoPg,
        llm: LLMClient,
        prompts: PromptLoader,
    ) -> None:
        super().__init__(step_id="regenerate", name="Перегенерирую описание")
        self.table_id = table_id
        self.guidance = guidance
        self.actor = actor
        self.session = session
        self.semantic_repo = semantic_repo
        self.llm = llm
        self.prompts = prompts

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        await run.emit(step_progress(self.step_id, 0.2, "Готовлю контекст"))
        table = await self.semantic_repo.get_table(self.table_id)
        if not table:
            raise RuntimeError("Таблица не найдена")
        # Get latest sample for this table from profiling_run_tables
        from sqlalchemy import text
        row = (
            await self.session.execute(
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
        await run.emit(step_progress(self.step_id, 0.5, "Генерирую"))
        out = await self.llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        try:
            new = extract_json(out)
        except Exception:
            new = {"description": out[:500]}

        await self.semantic_repo.add_revision(
            entity_kind="sem_table",
            entity_id=self.table_id,
            payload={k: table.get(k) for k in ("title", "description", "domain", "tags")},
            actor=self.actor,
            reason="regenerate",
        )
        await self.semantic_repo.update_table(
            self.table_id,
            title=new.get("title"),
            description=new.get("description"),
            domain=new.get("domain"),
            tags=new.get("tags"),
        )
        await self.session.commit()
        await run.emit(
            result_final(
                summary=new.get("title") or "Готово",
                sql=None,
                preview={"new": new},
                export_url=None,
            )
        )


class _RegenerateColumnStep(Step):
    def __init__(
        self,
        *,
        column_id: UUID,
        guidance: str | None,
        actor: str,
        semantic_repo: SemanticRepoPg,
        llm: LLMClient,
        prompts: PromptLoader,
    ) -> None:
        super().__init__(step_id="regenerate_column", name="Перегенерирую колонку")
        self.column_id = column_id
        self.guidance = guidance
        self.actor = actor
        self.semantic_repo = semantic_repo
        self.llm = llm
        self.prompts = prompts

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        await run.emit(step_progress(self.step_id, 0.2, "Готовлю контекст"))
        column = await self.semantic_repo.get_column(self.column_id)
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

        await self.semantic_repo.add_revision(
            entity_kind="sem_column",
            entity_id=self.column_id,
            payload={
                k: column.get(k)
                for k in ("description", "semantic_role", "user_notes")
            },
            actor=self.actor,
            reason="regenerate_column",
        )
        await self.semantic_repo.update_column(
            self.column_id,
            description=new.get("description"),
            semantic_role=new.get("semantic_role"),
        )
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
        llm: LLMClient,
        prompts: PromptLoader,
        registry: RunRegistry,
    ) -> None:
        self.sm = sessionmaker
        self.llm = llm
        self.prompts = prompts
        self.registry = registry

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
            async with self.sm() as session:
                step = _RegenerateColumnStep(
                    column_id=column_id,
                    guidance=guidance,
                    actor=actor,
                    semantic_repo=SemanticRepoPg(session),
                    llm=self.llm,
                    prompts=self.prompts,
                )
                pipeline = Pipeline([step])
                await pipeline.run(agent_run)
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("regenerate column failed")
            await agent_run.finalize(error=str(exc))

    async def _run_regenerate(
        self,
        table_id: UUID,
        agent_run: AgentRun,
        actor: str,
        guidance: str | None,
    ) -> None:
        try:
            async with self.sm() as session:
                step = _RegenerateStep(
                    table_id=table_id,
                    guidance=guidance,
                    actor=actor,
                    session=session,
                    semantic_repo=SemanticRepoPg(session),
                    llm=self.llm,
                    prompts=self.prompts,
                )
                pipeline = Pipeline([step])
                await pipeline.run(agent_run)
        except Exception as exc:  # noqa: BLE001
            logger.exception("regenerate failed")
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
        except Exception as exc:  # noqa: BLE001
            logger.exception("admin edit failed")
            await agent_run.finalize(error=str(exc))


# alias to silence "unused" linters
_ = ProfilingRepoPg
