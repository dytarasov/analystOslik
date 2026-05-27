"""Per-table chat with the LLM admin assistant.

A lightweight one-step pipeline:
- load table + columns + last chat history
- render the table_chat.md prompt
- stream the assistant reply via SSE
- parse a trailing JSON ``actions`` block and apply requested edits

Conversation persistence: one chat_sessions row per (admin, table_id), keyed by
``kind='admin_table'`` + ``target_id=table_id``. Both user prompt and assistant
reply (with parsed actions in metadata) are written to chat_messages.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import UUID

from neo4j import AsyncDriver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.registry import RunRegistry
from t2r.agents.orchestrator.run import AgentRun
from t2r.agents.orchestrator.step import Step
from t2r.domain.events.types import llm_token, result_final, step_progress
from t2r.errors import NotFoundError
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.graph.sync import try_resync_source_graph
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.logging import get_logger

logger = get_logger("table_chat")

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class _AskTableStep(Step):
    def __init__(
        self,
        *,
        session: AsyncSession,
        chat_session_id: UUID,
        table_id: UUID,
        prompt_text: str,
        actor: str,
        semantic_repo: SemanticRepoPg,
        llm: LLMClient,
        prompts: PromptLoader,
    ) -> None:
        super().__init__(step_id="table_chat", name="Думаю над таблицей")
        self.session = session
        self.chat_session_id = chat_session_id
        self.table_id = table_id
        self.prompt_text = prompt_text
        self.actor = actor
        self.semantic_repo = semantic_repo
        self.llm = llm
        self.prompts = prompts

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        await run.emit(step_progress(self.step_id, 0.1, "Собираю контекст"))
        table = await self.semantic_repo.get_table(self.table_id)
        if not table:
            raise NotFoundError("Таблица не найдена")
        columns = await self.semantic_repo.get_columns(self.table_id)
        history = await _load_history(self.session, self.chat_session_id, limit=12)

        prompt = self.prompts.render(
            "table_chat",
            table=table,
            columns=columns,
            history=history,
            prompt=self.prompt_text,
        )

        # Persist the user message before we call the LLM so the row exists
        # even if the call fails.
        await _insert_message(
            self.session, self.chat_session_id, role="user", content=self.prompt_text
        )

        await run.emit(step_progress(self.step_id, 0.4, "Запрашиваю LLM"))
        reply_chunks: list[str] = []
        async for token in self.llm.stream(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
        ):
            reply_chunks.append(token)
            await run.emit(llm_token(self.step_id, token))

        reply = "".join(reply_chunks).strip()
        actions = _extract_actions(reply)
        applied_summary = await _apply_actions(
            self.semantic_repo, table, actions, actor=self.actor
        )

        await _insert_message(
            self.session,
            self.chat_session_id,
            role="assistant",
            content=reply,
            metadata={"actions": actions, "applied": applied_summary},
        )
        # Bump the session's last activity.
        await self.session.execute(
            text(
                "UPDATE chat_sessions SET last_activity_at = now() WHERE id = :id"
            ),
            {"id": self.chat_session_id},
        )
        await self.session.commit()

        await run.emit(
            result_final(
                summary=reply.split("```")[0].strip() or "Готово",
                sql=None,
                preview={
                    "applied": applied_summary,
                    "actions": actions,
                    "chat_session_id": str(self.chat_session_id),
                },
                export_url=None,
            )
        )


def _extract_actions(text: str) -> list[dict[str, Any]]:
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return []
    try:
        parsed = json.loads(m.group(1))
    except Exception:
        return []
    actions = parsed.get("actions") if isinstance(parsed, dict) else None
    if not isinstance(actions, list):
        return []
    return [a for a in actions if isinstance(a, dict)]


async def _apply_actions(
    repo: SemanticRepoPg,
    table: dict[str, Any],
    actions: list[dict[str, Any]],
    *,
    actor: str,
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for action in actions:
        op = action.get("op")
        try:
            if op == "set_table":
                fields = action.get("fields") or {}
                await repo.add_revision(
                    entity_kind="sem_table",
                    entity_id=table["id"],
                    payload={
                        k: table.get(k)
                        for k in ("title", "description", "domain", "tags")
                    },
                    actor=actor,
                    reason="table_chat",
                )
                await repo.update_table(
                    table["id"],
                    title=fields.get("title"),
                    description=fields.get("description"),
                    domain=fields.get("domain"),
                    tags=fields.get("tags"),
                )
                applied.append({"op": "set_table", "fields": fields})
            elif op == "set_column":
                name = action.get("name")
                if not name:
                    continue
                col_id = await repo.find_column(table["id"], str(name))
                if not col_id:
                    continue
                fields = action.get("fields") or {}
                # Snapshot previous values before mutating.
                prev = await repo.get_column(col_id)
                if prev:
                    await repo.add_revision(
                        entity_kind="sem_column",
                        entity_id=col_id,
                        payload={
                            k: prev.get(k)
                            for k in ("description", "semantic_role", "user_notes")
                        },
                        actor=actor,
                        reason="table_chat",
                    )
                await repo.update_column(
                    col_id,
                    description=fields.get("description"),
                    semantic_role=fields.get("semantic_role"),
                    user_notes=fields.get("user_notes"),
                )
                applied.append({"op": "set_column", "name": name, "fields": fields})
            else:
                logger.warning("table_chat: unknown action", op=op)
        except Exception:
            logger.exception("table_chat: action failed", action=action)
    return applied


async def _load_history(
    session: AsyncSession, chat_session_id: UUID, *, limit: int
) -> list[dict[str, str]]:
    rows = (
        await session.execute(
            text(
                "SELECT role, content FROM chat_messages WHERE session_id = :sid"
                " ORDER BY created_at DESC LIMIT :lim"
            ),
            {"sid": chat_session_id, "lim": limit},
        )
    ).mappings().all()
    # Reverse to chronological.
    return [dict(r) for r in reversed(rows)]


async def _insert_message(
    session: AsyncSession,
    chat_session_id: UUID,
    *,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO chat_messages (session_id, role, content, metadata)"
            " VALUES (:sid, :role, :content, CAST(:md AS jsonb))"
        ),
        {
            "sid": chat_session_id,
            "role": role,
            "content": content,
            "md": json.dumps(metadata or {}, default=str),
        },
    )


async def _ensure_chat_session(
    session: AsyncSession, *, table_id: UUID, table_title: str | None
) -> UUID:
    row = (
        await session.execute(
            text(
                "SELECT id FROM chat_sessions WHERE kind = 'admin_table' AND target_id = :tid"
            ),
            {"tid": table_id},
        )
    ).first()
    if row:
        return row[0]
    row = (
        await session.execute(
            text(
                "INSERT INTO chat_sessions (kind, target_id, title)"
                " VALUES ('admin_table', :tid, :title) RETURNING id"
            ),
            {"tid": table_id, "title": table_title or "Диалог по таблице"},
        )
    ).first()
    assert row is not None
    await session.commit()
    return row[0]


class TableChatService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        neo4j_driver: AsyncDriver,
        llm: LLMClient,
        prompts: PromptLoader,
        registry: RunRegistry,
    ) -> None:
        self.sm = sessionmaker
        self.driver = neo4j_driver
        self.llm = llm
        self.prompts = prompts
        self.registry = registry

    async def ask(self, table_id: UUID, prompt_text: str, *, actor: str) -> str:
        async with self.sm() as s:
            table = await SemanticRepoPg(s).get_table(table_id)
            if not table:
                raise NotFoundError("Таблица не найдена")
            chat_session_id = await _ensure_chat_session(
                s, table_id=table_id, table_title=table.get("title")
            )

        agent_run = AgentRun(kind="table_chat")
        await self.registry.add(agent_run)
        task = asyncio.create_task(
            self._run(table_id, prompt_text, agent_run, actor, chat_session_id)
        )
        agent_run.attach_task(task)
        return agent_run.id

    async def history(self, table_id: UUID) -> dict[str, Any]:
        async with self.sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT id FROM chat_sessions WHERE kind = 'admin_table' AND target_id = :tid"
                    ),
                    {"tid": table_id},
                )
            ).first()
            if not row:
                return {"session_id": None, "messages": []}
            messages = (
                await s.execute(
                    text(
                        "SELECT id, role, content, metadata, created_at"
                        " FROM chat_messages WHERE session_id = :sid"
                        " ORDER BY created_at ASC"
                    ),
                    {"sid": row[0]},
                )
            ).mappings().all()
            return {
                "session_id": str(row[0]),
                "messages": [dict(m) for m in messages],
            }

    async def _run(
        self,
        table_id: UUID,
        prompt_text: str,
        agent_run: AgentRun,
        actor: str,
        chat_session_id: UUID,
    ) -> None:
        try:
            async with self.sm() as session:
                step = _AskTableStep(
                    session=session,
                    chat_session_id=chat_session_id,
                    table_id=table_id,
                    prompt_text=prompt_text,
                    actor=actor,
                    semantic_repo=SemanticRepoPg(session),
                    llm=self.llm,
                    prompts=self.prompts,
                )
                pipeline = Pipeline([step])
                await pipeline.run(agent_run)
                # Applied edits (table/column roles, tags) must reach Neo4j.
                repo = SemanticRepoPg(session)
                tbl = await repo.get_table(table_id)
                if tbl:
                    await try_resync_source_graph(
                        repo, GraphRepoNeo4j(self.driver), tbl["source_id"]
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception("table_chat failed")
            await agent_run.finalize(error=str(exc))
