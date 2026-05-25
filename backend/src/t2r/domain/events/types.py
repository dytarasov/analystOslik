from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

EventKind = Literal[
    "step.started",
    "step.progress",
    "step.completed",
    "step.failed",
    "llm.token",
    "tool.started",
    "tool.completed",
    "awaiting_input",
    "profiling.table.started",
    "profiling.table.completed",
    "result.partial",
    "result.final",
    "error",
    "done",
]


@dataclass(slots=True)
class AgentEvent:
    """A single event emitted from a pipeline run."""

    kind: EventKind
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: int = 0  # assigned by the bus
    run_id: str | None = None

    def to_sse(self) -> dict[str, Any]:
        return {
            "event": self.kind,
            "id": str(self.id),
            "data": {
                "run_id": self.run_id,
                "ts": self.ts,
                **self.payload,
            },
        }


def step_started(step_id: str, name: str) -> AgentEvent:
    return AgentEvent("step.started", {"step_id": step_id, "name": name})


def step_progress(step_id: str, progress: float, detail: str | None = None) -> AgentEvent:
    return AgentEvent(
        "step.progress",
        {"step_id": step_id, "progress": progress, "detail": detail},
    )


def step_completed(step_id: str, duration_ms: int) -> AgentEvent:
    return AgentEvent("step.completed", {"step_id": step_id, "duration_ms": duration_ms})


def step_failed(step_id: str, error: str, retry_possible: bool = False) -> AgentEvent:
    return AgentEvent(
        "step.failed",
        {"step_id": step_id, "error": error, "retry_possible": retry_possible},
    )


def llm_token(step_id: str, chunk: str) -> AgentEvent:
    return AgentEvent("llm.token", {"step_id": step_id, "chunk": chunk})


def tool_started(tool: str, args_summary: str | None = None) -> AgentEvent:
    return AgentEvent("tool.started", {"tool": tool, "args_summary": args_summary})


def tool_completed(tool: str, result_summary: str | None = None) -> AgentEvent:
    return AgentEvent("tool.completed", {"tool": tool, "result_summary": result_summary})


def awaiting_input(question: str, schema: dict | None = None, respond_url: str | None = None) -> AgentEvent:
    return AgentEvent(
        "awaiting_input",
        {"question": question, "schema": schema or {}, "respond_url": respond_url},
    )


def profiling_table_started(database: str, table: str, idx: int, total: int) -> AgentEvent:
    return AgentEvent(
        "profiling.table.started",
        {"database": database, "table": table, "idx": idx, "total": total},
    )


def profiling_table_completed(database: str, table: str, duration_ms: int) -> AgentEvent:
    return AgentEvent(
        "profiling.table.completed",
        {"database": database, "table": table, "duration_ms": duration_ms},
    )


def result_final(summary: str | None, sql: str | None, preview: dict | None, export_url: str | None) -> AgentEvent:
    return AgentEvent(
        "result.final",
        {"summary": summary, "sql": sql, "preview": preview, "export_url": export_url},
    )


def error_event(code: str, message: str) -> AgentEvent:
    return AgentEvent("error", {"code": code, "message": message})


def done_event() -> AgentEvent:
    return AgentEvent("done", {})
