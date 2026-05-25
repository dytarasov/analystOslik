from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any
from uuid import UUID

from sqlalchemy import text

from t2r.agents.client_agent.deps import ClientAgentDeps
from t2r.agents.client_agent.tools import StoredResult, ToolContext, build_registry
from t2r.agents.orchestrator.run import AgentRun
from t2r.agents.orchestrator.step import Step
from t2r.domain.events.types import (
    result_final,
    step_completed,
    step_started,
)
from t2r.infra.db.repos.agent_thread_repo_pg import AgentThreadRepo
from t2r.infra.export.xlsx import write_xlsx
from t2r.infra.llm.openai_client import ToolCall
from t2r.logging import get_logger

logger = get_logger("client_agent.loop")

MAX_ITERATIONS = 16
MAX_EXEC = 10
PREVIEW_ROWS = 50
# How many past user turns of the raw tool-calling thread we replay. Whole turns
# are kept (so no tool message is ever orphaned from its assistant tool_call),
# which bounds context growth across a long session.
MAX_THREAD_TURNS = 8
# Hard ceiling on replayed history size (~4 chars/token ⇒ ~12k tokens). Oldest
# whole turns are dropped first so we never blow the model's context window even
# if turns contain big schema dumps.
THREAD_CHAR_BUDGET = 48_000
# Per tool-observation cap appended to the live context. Full results are still
# kept server-side for preview/export; this only bounds what the model re-reads.
OBS_CHAR_CAP = 12_000


class ReactAgentStep(Step):
    """A single orchestrator step that runs the whole ReAct tool loop.

    The full tool-calling thread (assistant tool_calls + tool observations) is
    persisted per session and replayed on the next turn, so a conversation
    actually accumulates context instead of each message being answered in
    isolation.
    """

    def __init__(
        self,
        deps: ClientAgentDeps,
        *,
        source_id: UUID,
        task_id: UUID,
        prompt: str,
        session_id: UUID | None = None,
    ) -> None:
        super().__init__(step_id="agent", name="Агент")
        self.deps = deps
        self.source_id = source_id
        self.task_id = task_id
        self.prompt = prompt
        self.session_id = session_id

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        tables = await self.deps.semantic_repo.list_tables(self.source_id)
        tctx = ToolContext(
            deps=self.deps, source_id=self.source_id, run=run, tables=tables
        )
        registry = build_registry()
        tool_schemas = [t.schema for t in registry.values()]
        thread_repo = AgentThreadRepo(self.deps.session)

        prior: list[dict[str, Any]] = []
        if self.session_id:
            prior = self._trim_thread(await thread_repo.load(self.session_id))
        # Fallback for sessions that predate the persisted thread.
        if not prior:
            prior = self._history_messages(ctx)

        messages: list[dict[str, Any]] = [self._system_message(ctx, tables, bool(prior))]
        messages.extend(prior)
        persist_from = len(messages)  # everything from here is new this turn
        messages.append({"role": "user", "content": self.prompt})

        exec_count = 0
        step_seq = 0

        for _ in range(MAX_ITERATIONS):
            if run.cancel_event.is_set():
                raise asyncio.CancelledError()

            turn = await self.deps.llm.complete_with_tools(messages, tool_schemas)

            if not turn.tool_calls:
                await self._finish(
                    run, tctx, summary=turn.content or "", result_from=None,
                    messages=messages, persist_from=persist_from,
                )
                return

            messages.append(self._assistant_message(turn.content, turn.tool_calls))

            finished = False
            for tc in turn.tool_calls:
                tool = registry.get(tc.name)
                step_seq += 1
                step_id = f"tool{step_seq}"
                label = tool.label(tc.arguments) if tool else f"Неизвестный тул {tc.name}"
                started = time.time()
                await run.emit(step_started(step_id, label))

                if tool is None:
                    result: Any = {"error": f"Неизвестный инструмент {tc.name!r}"}
                elif tool.terminal:
                    await run.emit(step_completed(step_id, _ms(started)))
                    await self._finish(
                        run,
                        tctx,
                        summary=str(tc.arguments.get("summary") or turn.content or ""),
                        result_from=tc.arguments.get("result_from"),
                        messages=messages,
                        persist_from=persist_from,
                    )
                    finished = True
                    break
                else:
                    if tc.name == "run_sql":
                        exec_count += 1
                        if exec_count > MAX_EXEC:
                            result = {
                                "error": "Превышен лимит запросов. Заверши ответ "
                                "через finish по уже полученным данным.",
                                "kind": "budget",
                            }
                        else:
                            result = await self._safe_call(tool, tctx, tc)
                    else:
                        result = await self._safe_call(tool, tctx, tc)

                await run.emit(step_completed(step_id, _ms(started)))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _truncate(
                            json.dumps(result, default=str, ensure_ascii=False),
                            OBS_CHAR_CAP,
                        ),
                    }
                )

            if finished:
                return

        await self._finish(
            run,
            tctx,
            summary="Не удалось полностью завершить анализ за отведённое число "
            "шагов. Ниже — последний полученный результат.",
            result_from=None,
            messages=messages,
            persist_from=persist_from,
        )

    # ──────────────────────────────────────────────────────────────────

    async def _safe_call(self, tool, tctx: ToolContext, tc: ToolCall) -> Any:
        try:
            return await tool.handler(tctx, tc.arguments)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool failed", tool=tc.name, error=str(exc))
            return {"error": str(exc)}

    def _assistant_message(
        self, content: str | None, calls: list[ToolCall]
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments, ensure_ascii=False),
                    },
                }
                for c in calls
            ],
        }

    def _trim_thread(self, thread: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep recent whole turns within both a turn-count and a char budget.

        Turns are cut only at user-message boundaries, so an assistant
        tool_call is never separated from its tool result. The newest turns are
        kept; oldest are dropped first once either budget is exceeded.
        """
        starts = [i for i, m in enumerate(thread) if m.get("role") == "user"]
        if not starts:
            return thread
        ranges = [
            (s, starts[k + 1] if k + 1 < len(starts) else len(thread))
            for k, s in enumerate(starts)
        ]
        kept: list[tuple[int, int]] = []
        total = 0
        for s, e in reversed(ranges):
            size = sum(_msg_chars(m) for m in thread[s:e])
            if kept and (len(kept) >= MAX_THREAD_TURNS or total + size > THREAD_CHAR_BUDGET):
                break
            kept.append((s, e))
            total += size
        return thread[kept[-1][0]:] if kept else thread[ranges[-1][0]:]

    def _history_messages(self, ctx) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in ctx.get("chat_history") or []:
            role = m.get("role")
            content = m.get("content") or ""
            if role in ("user", "assistant") and content:
                out.append({"role": role, "content": content})
        return out

    def _system_message(
        self, ctx, tables: list[dict[str, Any]], has_thread: bool
    ) -> dict[str, Any]:
        overview = "\n".join(
            f"- `{t['database']}.{t['table_name']}`"
            + (f" — {t['title']}" if t.get("title") else "")
            + (f" (грануляр.: {t['grain']})" if t.get("grain") else "")
            for t in tables
        ) or "(в источнике пока нет описанных таблиц)"

        prev_block = ""
        # Only inject the prev-result hint when we have no replayed thread —
        # otherwise the prior SQL is already in the thread verbatim.
        if not has_thread:
            prev_result = ctx.get("prev_result")
            if prev_result and prev_result.get("sql"):
                prev_block = (
                    "Предыдущий результат в этой сессии:\n"
                    f"- вопрос: {prev_result.get('prompt')}\n"
                    f"- SQL: {prev_result.get('sql')}\n"
                    f"- итог: {prev_result.get('summary')} "
                    f"({prev_result.get('rowcount')} строк)"
                )

        system = self.deps.prompts.render(
            "client_agent", tables_overview=overview, prev_block=prev_block
        )
        return {"role": "system", "content": system}

    async def _finish(
        self,
        run: AgentRun,
        tctx: ToolContext,
        *,
        summary: str,
        result_from: str | None,
        messages: list[dict[str, Any]],
        persist_from: int,
    ) -> None:
        chosen: StoredResult | None = None
        if result_from and result_from in tctx.results:
            chosen = tctx.results[result_from]
        elif tctx.results:
            chosen = list(tctx.results.values())[-1]

        sql = chosen.sql if chosen else None
        preview: dict[str, Any] | None = None
        rowcount = 0
        export_path = ""
        if chosen:
            rowcount = chosen.rowcount
            preview = {
                "columns": chosen.columns,
                "rows": [[_safe(v) for v in r] for r in chosen.rows[:PREVIEW_ROWS]],
            }
            export_path = os.path.join(
                self.deps.export_dir, f"task_{self.task_id}.xlsx"
            )
            try:
                write_xlsx(
                    export_path, columns=chosen.columns, rows=chosen.rows, title="report"
                )
            except Exception:  # noqa: BLE001
                logger.exception("xlsx write failed")
                export_path = ""

        await self.deps.session.execute(
            text(
                "UPDATE task_runs SET status = 'done',"
                " result_summary = :s, result_sql = :sql,"
                " result_preview = CAST(:p AS jsonb), result_rowcount = :rc,"
                " export_path = :path, finished_at = now()"
                " WHERE id = :id"
            ),
            {
                "id": self.task_id,
                "s": summary,
                "sql": sql,
                "p": json.dumps(preview, default=str, ensure_ascii=False)
                if preview is not None
                else None,
                "rc": rowcount,
                "path": export_path or None,
            },
        )

        # Record this turn's full thread (user + tool calls + observations +
        # the final answer) so the next turn continues with it.
        messages.append({"role": "assistant", "content": summary})
        if self.session_id:
            try:
                await AgentThreadRepo(self.deps.session).append(
                    self.session_id, messages[persist_from:]
                )
            except Exception:  # noqa: BLE001
                logger.exception("agent thread persist failed")

        await self.deps.session.commit()

        export_url = (
            f"/api/tasks/{self.task_id}/export.xlsx" if export_path else None
        )
        await run.emit(
            result_final(
                summary=summary, sql=sql, preview=preview, export_url=export_url
            )
        )


def _ms(started: float) -> int:
    return int((time.time() - started) * 1000)


def _msg_chars(m: dict[str, Any]) -> int:
    n = len(m.get("content") or "")
    tc = m.get("tool_calls")
    if tc:
        n += len(json.dumps(tc, ensure_ascii=False))
    return n


def _truncate(s: str, cap: int) -> str:
    if len(s) <= cap:
        return s
    return s[:cap] + f"…[обрезано, всего {len(s)} символов]"


def _safe(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return str(v)
    return v
