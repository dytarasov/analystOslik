from __future__ import annotations

import asyncio
from uuid import UUID

from neo4j import AsyncDriver
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.agents.client_agent.deps import ClientAgentDeps
from t2r.agents.client_agent.loop import ReactAgentStep
from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.registry import RunRegistry
from t2r.agents.orchestrator.run import AgentRun
from t2r.infra.clickhouse.factory import CHClientFactory
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.infra.security.cipher import FernetCipher
from t2r.logging import get_logger
from t2r.settings import Settings

logger = get_logger("task_service")


class TaskService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        cipher: FernetCipher,
        neo4j_driver: AsyncDriver,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        prompts: PromptLoader,
        registry: RunRegistry,
        settings: Settings,
    ) -> None:
        self.sm = sessionmaker
        self.cipher = cipher
        self.neo4j = neo4j_driver
        self.llm = llm
        self.embeddings = embeddings
        self.prompts = prompts
        self.registry = registry
        self.settings = settings

    async def start_task(
        self, *, session_id: UUID, source_id: UUID, prompt: str
    ) -> tuple[UUID, str]:
        """Idempotent start, mirroring ProfilingService.start.

        A chat session may have at most one active task at a time (enforced by
        uniq_active_task_per_session). On a double-submit / SSE-reconnect re-POST
        we reuse the live run instead of spawning a second pipeline that would
        race on the DB session and double-write assistant messages.
        """
        # If an active task already exists and its worker is still alive, reuse
        # it. If the worker is gone (e.g. after a crash), release the slot.
        async with self.sm() as s:
            existing = (
                await s.execute(
                    text(
                        "SELECT id, agent_run_id FROM task_runs"
                        " WHERE session_id = :sid"
                        " AND status IN ('running', 'awaiting_input')"
                        " ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"sid": session_id},
                )
            ).mappings().first()
        if existing:
            existing_agent_id = existing.get("agent_run_id")
            if existing_agent_id and await self.registry.get(existing_agent_id):
                logger.info(
                    "start_task.reused",
                    session_id=str(session_id),
                    task_id=str(existing["id"]),
                    agent_run_id=existing_agent_id,
                )
                return existing["id"], existing_agent_id
            # DB says active, but the worker is gone — free the slot so the
            # INSERT below doesn't trip the unique index.
            logger.warning(
                "start_task.releasing_abandoned",
                session_id=str(session_id),
                task_id=str(existing["id"]),
            )
            async with self.sm() as s:
                await s.execute(
                    text(
                        "UPDATE task_runs SET status = 'failed',"
                        " error = COALESCE(error, 'abandoned_before_restart'),"
                        " finished_at = now()"
                        " WHERE id = :id AND status IN ('running', 'awaiting_input')"
                    ),
                    {"id": existing["id"]},
                )
                await s.commit()

        # Pre-create AgentRun so we have its id BEFORE inserting the DB row —
        # we want agent_run_id persisted from the very start (refresh-resume).
        agent_run = AgentRun(kind="client_task")
        async with self.sm() as s:
            try:
                row = (
                    await s.execute(
                        text(
                            "INSERT INTO task_runs (session_id, source_id, status, prompt,"
                            " agent_run_id, started_at)"
                            " VALUES (:sid, :src, 'running', :p, :arid, now()) RETURNING id"
                        ),
                        {
                            "sid": session_id,
                            "src": source_id,
                            "p": prompt,
                            "arid": agent_run.id,
                        },
                    )
                ).first()
                assert row is not None
                task_id = row[0]
                await s.commit()
            except IntegrityError:
                await s.rollback()
                # Lost the race against a concurrent start() — reuse whatever
                # active task now exists for the session.
                active = (
                    await s.execute(
                        text(
                            "SELECT id, agent_run_id FROM task_runs"
                            " WHERE session_id = :sid"
                            " AND status IN ('running', 'awaiting_input')"
                            " ORDER BY started_at DESC LIMIT 1"
                        ),
                        {"sid": session_id},
                    )
                ).mappings().first()
                if active and active.get("agent_run_id"):
                    return active["id"], active["agent_run_id"]
                from t2r.errors import ValidationError

                raise ValidationError(
                    "В этом чате уже выполняется запрос. Дождитесь его завершения."
                )

        agent_run.context.update(task_id=str(task_id), source_id=str(source_id))
        await self.registry.add(agent_run)
        runner = asyncio.create_task(self._run(task_id, source_id, prompt, agent_run))
        agent_run.attach_task(runner)
        return task_id, agent_run.id

    async def get_active_task_for_session(self, session_id: UUID) -> dict | None:
        """Найти незавершённый task для сессии — для переподписки SSE.

        Возвращает None если активного нет, иначе {task_id, agent_run_id, prompt,
        status}. Если worker уже умер (нет в registry) — тоже None.
        """
        async with self.sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT id, agent_run_id, prompt, status FROM task_runs"
                        " WHERE session_id = :sid"
                        " AND status IN ('running', 'awaiting_input')"
                        " ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"sid": session_id},
                )
            ).mappings().first()
        if not row:
            return None
        agent_run_id = row.get("agent_run_id")
        if not agent_run_id:
            return None
        live = await self.registry.get(agent_run_id) is not None
        return {
            "task_id": str(row["id"]),
            "agent_run_id": agent_run_id,
            "prompt": row["prompt"],
            "status": row["status"],
            "live": live,
        }

    async def _run(
        self,
        task_id: UUID,
        source_id: UUID,
        prompt: str,
        agent_run: AgentRun,
    ) -> None:
        error_msg: str | None = None
        cancelled = False
        try:
            # Pull session_id + prior conversation context BEFORE building the
            # pipeline so the classifier can branch on followup vs new_query.
            history, prev_result = await self._load_session_context(task_id)
            agent_run.context.update(
                chat_history=history,
                prev_result=prev_result,
            )

            async with self.sm() as session:
                sid_row = (
                    await session.execute(
                        text("SELECT session_id FROM task_runs WHERE id = :id"),
                        {"id": task_id},
                    )
                ).first()
                session_id = sid_row[0] if sid_row else None

                deps = ClientAgentDeps(
                    ch_factory=CHClientFactory(SourceRepoPg(session, self.cipher)),
                    semantic_repo=SemanticRepoPg(session),
                    notes_repo=NotesRepoPg(session),
                    graph_repo=GraphRepoNeo4j(self.neo4j),
                    session=session,
                    llm=self.llm,
                    embeddings=self.embeddings,
                    prompts=self.prompts,
                    export_dir=self.settings.export_dir,
                    ch_max_execution_time=self.settings.ch_default_max_execution_time,
                    ch_default_limit=self.settings.ch_default_limit,
                    run_budget_seconds=self.settings.agent_run_budget_seconds,
                    answer_timeout_seconds=self.settings.client_answer_timeout_seconds,
                )

                # One ReAct tool-loop handles everything — new query, follow-up,
                # and SQL modification. Continuity comes from the persisted
                # per-session tool-calling thread, replayed each turn.
                pipeline = Pipeline(
                    [
                        ReactAgentStep(
                            deps,
                            source_id=source_id,
                            task_id=task_id,
                            prompt=prompt,
                            session_id=session_id,
                        )
                    ]
                )
                await pipeline.run(agent_run)
        except asyncio.CancelledError:
            cancelled = True
            error_msg = "cancelled_by_user"
            try:
                async with self.sm() as s:
                    await s.execute(
                        text(
                            "UPDATE task_runs SET status = 'cancelled', error = :e,"
                            " finished_at = now() WHERE id = :id"
                        ),
                        {"id": task_id, "e": error_msg},
                    )
                    await s.commit()
            except Exception:
                logger.exception("task cancel persist failed")
        except Exception as exc:  # noqa: BLE001
            logger.exception("task failed")
            error_msg = str(exc)
            try:
                async with self.sm() as s:
                    await s.execute(
                        text(
                            "UPDATE task_runs SET status = 'failed', error = :e, finished_at = now()"
                            " WHERE id = :id"
                        ),
                        {"id": task_id, "e": error_msg},
                    )
                    await s.commit()
            except Exception:
                logger.exception("task fail persist failed")
        finally:
            # Persist the assistant turn so it survives a backend restart and
            # re-renders correctly on /chat/{session_id}.
            try:
                await self._save_assistant_message(task_id, error=error_msg, cancelled=cancelled)
            except Exception:
                logger.exception("assistant message persist failed")
            if error_msg and not agent_run.is_finished:
                await agent_run.finalize(error=error_msg)

    async def _load_session_context(
        self, task_id: UUID
    ) -> tuple[list[dict], dict | None]:
        """Pull last 12 messages from the chat session + the latest finished task.

        Returns (history, prev_result). prev_result carries enough for the
        classifier / modify_sql / followup branches: sql, summary, rowcount,
        preview-shape (just columns + first-rows for sample).
        """
        async with self.sm() as s:
            row = (
                await s.execute(
                    text("SELECT session_id FROM task_runs WHERE id = :id"),
                    {"id": task_id},
                )
            ).first()
            if not row:
                return [], None
            session_id = row[0]

            msg_rows = (
                await s.execute(
                    text(
                        "SELECT role, content, metadata, created_at"
                        " FROM chat_messages WHERE session_id = :sid"
                        " ORDER BY created_at DESC LIMIT 12"
                    ),
                    {"sid": session_id},
                )
            ).mappings().all()
            history = [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "metadata": m["metadata"] or {},
                }
                for m in reversed(msg_rows)
            ]

            # The previous result we hand to the classifier / modify_sql / retrieve
            # steps must be the last turn that actually produced SQL. followup
            # turns are persisted as done with result_sql = NULL, so without the
            # `result_sql IS NOT NULL` filter the context chain would break the
            # moment the user asks a non-SQL follow-up between two data queries
            # ("сколько учителей" → "почему так мало" → "выгрузи их email").
            prev_row = (
                await s.execute(
                    text(
                        "SELECT id, result_sql, result_summary, result_rowcount,"
                        " result_preview, prompt, status"
                        " FROM task_runs WHERE session_id = :sid AND id <> :tid"
                        " AND status = 'done' AND result_sql IS NOT NULL"
                        " ORDER BY finished_at DESC NULLS LAST, created_at DESC LIMIT 1"
                    ),
                    {"sid": session_id, "tid": task_id},
                )
            ).mappings().first()
            prev_result: dict | None = None
            if prev_row:
                preview = prev_row.get("result_preview") or {}
                cols = preview.get("columns") if isinstance(preview, dict) else None
                rows = preview.get("rows") if isinstance(preview, dict) else None
                prev_result = {
                    "task_id": str(prev_row["id"]),
                    "sql": prev_row.get("result_sql"),
                    "summary": prev_row.get("result_summary"),
                    "rowcount": prev_row.get("result_rowcount"),
                    "prompt": prev_row.get("prompt"),
                    "preview_columns": cols,
                    "preview_sample": (rows or [])[:5] if rows else [],
                }
        return history, prev_result

    async def _save_assistant_message(
        self,
        task_id: UUID,
        *,
        error: str | None = None,
        cancelled: bool = False,
    ) -> None:
        import json as _json

        async with self.sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT session_id, result_summary, result_sql, result_preview,"
                        " result_rowcount, export_path FROM task_runs WHERE id = :id"
                    ),
                    {"id": task_id},
                )
            ).first()
            if not row:
                return
            session_id, summary, sql, preview, rowcount, export_path = row
            if cancelled:
                content = "Запрос был прерван."
            elif error:
                content = f"Не удалось сформировать ответ: {error}"
            else:
                content = summary or "Готово."
            metadata = {
                "task_id": str(task_id),
                "sql": sql,
                "preview": preview,  # already jsonb-decoded → dict/None
                "rowcount": rowcount,
                "summary": summary,
                "export_url": (
                    f"/api/tasks/{task_id}/export.xlsx" if export_path else None
                ),
                "error": error,
                "cancelled": cancelled,
            }
            await s.execute(
                text(
                    "INSERT INTO chat_messages (session_id, role, content, metadata)"
                    " VALUES (:sid, 'assistant', :c, CAST(:m AS jsonb))"
                ),
                {
                    "sid": session_id,
                    "c": content,
                    "m": _json.dumps(metadata, default=str),
                },
            )
            await s.execute(
                text("UPDATE chat_sessions SET last_activity_at = now() WHERE id = :id"),
                {"id": session_id},
            )
            await s.commit()

    async def get_task(self, task_id: UUID) -> dict | None:
        async with self.sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT id, session_id, source_id, status, prompt, result_summary,"
                        " result_sql, result_preview, result_rowcount, export_path, error,"
                        " started_at, finished_at, created_at"
                        " FROM task_runs WHERE id = :id"
                    ),
                    {"id": task_id},
                )
            ).mappings().first()
            return dict(row) if row else None

    async def get_export_path(self, task_id: UUID) -> str | None:
        async with self.sm() as s:
            row = (
                await s.execute(
                    text("SELECT export_path FROM task_runs WHERE id = :id"),
                    {"id": task_id},
                )
            ).first()
            return row[0] if row and row[0] else None

    async def rerun_sql(self, task_id: UUID, sql: str) -> dict:
        """Re-execute an arbitrary SQL against the task's source (Jupyter-like).

        Reuses the SQL guard with the full semantic-layer whitelist for the
        source. Overwrites preview / sql / rowcount / export_path on the
        existing task_runs row so the XLSX download stays under the same URL.
        """
        import json
        import os

        from t2r.errors import NotFoundError
        from t2r.infra.clickhouse.factory import CHClientFactory
        from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
        from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
        from t2r.infra.export.xlsx import write_xlsx
        from t2r.infra.security.sql_guard import SqlGuardError, validate_and_rewrite

        async with self.sm() as s:
            row = (
                await s.execute(
                    text("SELECT source_id FROM task_runs WHERE id = :id"),
                    {"id": task_id},
                )
            ).first()
            if not row:
                raise NotFoundError("Задача не найдена")
            source_id: UUID = row[0]
            tables = await SemanticRepoPg(s).list_tables(source_id)

        whitelist = {f"{t['database']}.{t['table_name']}" for t in tables}
        try:
            guarded = validate_and_rewrite(
                sql,
                whitelist_qnames=whitelist,
                default_limit=self.settings.ch_default_limit,
                max_execution_time=self.settings.ch_default_max_execution_time,
            )
        except SqlGuardError as exc:
            return {"ok": False, "error": str(exc), "kind": "guard"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "kind": "parse"}

        # Execute
        async with self.sm() as s:
            source_repo = SourceRepoPg(s, self.cipher)
            ch_factory = CHClientFactory(source_repo)
            client = await ch_factory.for_source(source_id)
        try:
            res = await client.query(
                guarded.rewritten, settings=guarded.settings or None
            )
            cols = list(res.column_names)
            rows = [list(r) for r in res.result_rows]
        except Exception as exc:  # noqa: BLE001
            try:
                await client.close()
            except Exception:
                pass
            logger.exception("rerun_sql execute failed")
            return {"ok": False, "error": str(exc), "kind": "execute"}
        await client.close()

        preview_rows = rows[:50]
        preview = {
            "columns": cols,
            "rows": [[_safe(v) for v in r] for r in preview_rows],
        }

        export_path = os.path.join(self.settings.export_dir, f"task_{task_id}.xlsx")
        try:
            write_xlsx(export_path, columns=cols, rows=rows, title="report")
        except Exception:  # noqa: BLE001
            logger.exception("rerun_sql xlsx write failed")
            export_path = ""

        async with self.sm() as s:
            await s.execute(
                text(
                    "UPDATE task_runs SET status = 'done', result_sql = :sql,"
                    " result_preview = CAST(:p AS jsonb), result_rowcount = :rc,"
                    " export_path = :path, error = NULL, finished_at = now()"
                    " WHERE id = :id"
                ),
                {
                    "id": task_id,
                    "sql": guarded.rewritten,
                    "p": json.dumps(preview, default=str),
                    "rc": len(rows),
                    "path": export_path or None,
                },
            )
            await s.commit()

        export_url = f"/api/tasks/{task_id}/export.xlsx" if export_path else None
        return {
            "ok": True,
            "sql": guarded.rewritten,
            "preview": preview,
            "rowcount": len(rows),
            "export_url": export_url,
        }


def _safe(v):
    if isinstance(v, (dict, list)):
        return str(v)
    return v
