from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AgentThreadRepo:
    """Persists the raw OpenAI tool-calling thread per chat session.

    This is what makes a session a session: a follow-up turn reloads the whole
    prior thread (assistant tool_calls + tool observations + answers) and keeps
    going, instead of starting a fresh loop on every message.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self, session_id: UUID) -> list[dict[str, Any]]:
        """Return the thread as OpenAI message dicts, in order."""
        rows = (
            await self.session.execute(
                text(
                    "SELECT role, content, tool_calls, tool_call_id"
                    " FROM agent_messages WHERE session_id = :sid ORDER BY seq"
                ),
                {"sid": session_id},
            )
        ).mappings().all()
        out: list[dict[str, Any]] = []
        for r in rows:
            msg: dict[str, Any] = {"role": r["role"], "content": r["content"] or ""}
            if r["role"] == "assistant" and r["tool_calls"]:
                msg["tool_calls"] = r["tool_calls"]
            if r["role"] == "tool":
                msg["tool_call_id"] = r["tool_call_id"]
            out.append(msg)
        return out

    async def append(self, session_id: UUID, messages: list[dict[str, Any]]) -> None:
        """Append messages after the current max seq for the session."""
        if not messages:
            return
        base = (
            await self.session.execute(
                text(
                    "SELECT COALESCE(MAX(seq), 0) FROM agent_messages"
                    " WHERE session_id = :sid"
                ),
                {"sid": session_id},
            )
        ).scalar() or 0
        for i, m in enumerate(messages, start=1):
            tool_calls = m.get("tool_calls")
            await self.session.execute(
                text(
                    "INSERT INTO agent_messages"
                    " (session_id, seq, role, content, tool_calls, tool_call_id)"
                    " VALUES (:sid, :seq, :role, :content,"
                    "  CAST(:tc AS jsonb), :tcid)"
                ),
                {
                    "sid": session_id,
                    "seq": base + i,
                    "role": m["role"],
                    "content": m.get("content"),
                    "tc": json.dumps(tool_calls, ensure_ascii=False)
                    if tool_calls
                    else None,
                    "tcid": m.get("tool_call_id"),
                },
            )
