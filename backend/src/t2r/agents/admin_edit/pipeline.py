from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.run import AgentRun
from t2r.agents.orchestrator.step import Step
from t2r.domain.events.types import (
    result_final,
    step_progress,
    tool_completed,
    tool_started,
)
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.llm.json_extractor import extract_json
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader


class _AdminEditStep(Step):
    def __init__(
        self,
        *,
        prompt: str,
        source_id: UUID,
        actor: str,
        session: AsyncSession,
        semantic_repo: SemanticRepoPg,
        notes_repo: NotesRepoPg,
        llm: LLMClient,
        prompts: PromptLoader,
    ) -> None:
        super().__init__(step_id="admin_edit", name="Применяю команду администратора")
        self.prompt = prompt
        self.source_id = source_id
        self.actor = actor
        self.session = session
        self.semantic_repo = semantic_repo
        self.notes_repo = notes_repo
        self.llm = llm
        self.prompts = prompts

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        await run.emit(step_progress(self.step_id, 0.1, "Понимаю задачу"))
        tables = await self.semantic_repo.list_tables(self.source_id)
        rendered = self.prompts.render(
            "admin_edit_planner", prompt=self.prompt, tables=tables
        )
        await run.emit(tool_started("llm.planner"))
        plan_text = await self.llm.complete(
            [{"role": "user", "content": rendered}],
            temperature=0.1,
        )
        await run.emit(tool_completed("llm.planner"))
        try:
            actions = extract_json(plan_text)
            if not isinstance(actions, list):
                actions = []
        except Exception:
            actions = []

        await run.emit(step_progress(self.step_id, 0.4, f"План: {len(actions)} операций"))
        applied: list[dict[str, Any]] = []

        for i, action_obj in enumerate(actions, start=1):
            if not isinstance(action_obj, dict):
                continue
            action = action_obj.get("action")
            reason = action_obj.get("reason") or self.prompt[:200]
            try:
                if action == "update_table":
                    res = await self._apply_update_table(action_obj, reason)
                    if res:
                        applied.append(res)
                elif action == "set_user_notes":
                    res = await self._apply_user_notes(action_obj, reason)
                    if res:
                        applied.append(res)
                elif action == "add_relation":
                    res = await self._apply_add_relation(action_obj, reason)
                    if res:
                        applied.append(res)
                elif action == "add_glossary":
                    res = await self._apply_add_glossary(action_obj)
                    if res:
                        applied.append(res)
                elif action == "add_note":
                    res = await self._apply_add_note(action_obj)
                    if res:
                        applied.append(res)
            except Exception as exc:  # noqa: BLE001
                applied.append({"action": action, "error": str(exc)})
            await run.emit(step_progress(self.step_id, 0.4 + 0.5 * (i / max(len(actions), 1))))
        await self.session.commit()
        await run.emit(
            result_final(
                summary=f"Применено: {len(applied)} операций",
                sql=None,
                preview={"applied": applied},
                export_url=None,
            )
        )

    async def _resolve_table(self, qname: str | None) -> UUID | None:
        if not qname or "." not in qname:
            return None
        db, tbl = qname.split(".", 1)
        return await self.semantic_repo.find_table(self.source_id, db, tbl)

    async def _apply_update_table(self, action: dict, reason: str) -> dict | None:
        table_id = await self._resolve_table(action.get("target_table"))
        if not table_id:
            return None
        updates = action.get("updates") or {}
        prev = await self.semantic_repo.get_table(table_id)
        if prev:
            await self.semantic_repo.add_revision(
                entity_kind="sem_table",
                entity_id=table_id,
                payload={k: prev.get(k) for k in ("title", "description", "domain", "tags")},
                actor=self.actor,
                reason=reason,
            )
        await self.semantic_repo.update_table(
            table_id,
            title=updates.get("title"),
            description=updates.get("description"),
            domain=updates.get("domain"),
            tags=updates.get("tags"),
            lock=True,
        )
        await self._audit("sem_table", table_id, reason, prev, action)
        return {"action": "update_table", "table_id": str(table_id)}

    async def _apply_user_notes(self, action: dict, reason: str) -> dict | None:
        table_id = await self._resolve_table(action.get("target_table"))
        if not table_id:
            return None
        await self.semantic_repo.update_table(
            table_id, user_notes=action.get("user_notes"), lock=True
        )
        await self._audit("sem_table", table_id, reason, None, action)
        return {"action": "set_user_notes", "table_id": str(table_id)}

    async def _apply_add_relation(self, action: dict, reason: str) -> dict | None:
        from_table_id = await self._resolve_table(action.get("from_table"))
        to_table_id = await self._resolve_table(action.get("to_table"))
        if not from_table_id or not to_table_id:
            return None
        from_col_id = await self.semantic_repo.find_column(
            from_table_id, action.get("from_column", "")
        )
        to_col_id = await self.semantic_repo.find_column(
            to_table_id, action.get("to_column", "")
        )
        rel_id = await self.semantic_repo.insert_relation(
            source_id=self.source_id,
            from_table_id=from_table_id,
            from_column_id=from_col_id,
            to_table_id=to_table_id,
            to_column_id=to_col_id,
            kind=action.get("kind", "conceptual"),
            origin="manual",
            confidence=float(action.get("confidence", 0.7)),
            reasoning=action.get("reasoning"),
        )
        await self._audit("sem_relation", rel_id, reason, None, action)
        return {"action": "add_relation", "id": str(rel_id)}

    async def _apply_add_glossary(self, action: dict) -> dict | None:
        term = action.get("term")
        definition = action.get("definition")
        if not term or not definition:
            return None
        await self.session.execute(
            text(
                "INSERT INTO sem_glossary (source_id, term, definition, synonyms)"
                " VALUES (:s, :t, :d, :syn)"
                " ON CONFLICT (source_id, term) DO UPDATE"
                " SET definition = EXCLUDED.definition, synonyms = EXCLUDED.synonyms"
            ),
            {
                "s": self.source_id,
                "t": term,
                "d": definition,
                "syn": action.get("synonyms") or [],
            },
        )
        return {"action": "add_glossary", "term": term}

    async def _apply_add_note(self, action: dict) -> dict | None:
        title = action.get("title")
        body = action.get("body_md")
        if not body:
            return None
        await self.session.execute(
            text(
                "INSERT INTO md_notes (source_id, scope, title, body_md, tags)"
                " VALUES (:s, 'free', :ti, :b, :tg)"
            ),
            {
                "s": self.source_id,
                "ti": title,
                "b": body,
                "tg": action.get("tags") or [],
            },
        )
        return {"action": "add_note", "title": title}

    async def _audit(
        self,
        entity_kind: str,
        entity_id: UUID,
        reason: str,
        before: dict | None,
        after: dict | None,
    ) -> None:
        import json as _json

        await self.session.execute(
            text(
                "INSERT INTO audit_log (actor, action, entity_kind, entity_id, before, after, reason)"
                " VALUES (:a, :ac, :ek, :eid, CAST(:b AS jsonb), CAST(:af AS jsonb), :r)"
            ),
            {
                "a": self.actor,
                "ac": "admin_edit",
                "ek": entity_kind,
                "eid": entity_id,
                "b": _json.dumps(before, default=str) if before else None,
                "af": _json.dumps(after, default=str) if after else None,
                "r": reason,
            },
        )


def build_admin_edit_pipeline(
    *,
    prompt: str,
    source_id: UUID,
    actor: str,
    session: AsyncSession,
    semantic_repo: SemanticRepoPg,
    notes_repo: NotesRepoPg,
    llm: LLMClient,
    prompts: PromptLoader,
) -> Pipeline:
    return Pipeline(
        [
            _AdminEditStep(
                prompt=prompt,
                source_id=source_id,
                actor=actor,
                session=session,
                semantic_repo=semantic_repo,
                notes_repo=notes_repo,
                llm=llm,
                prompts=prompts,
            )
        ]
    )
