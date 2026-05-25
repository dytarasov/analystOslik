from __future__ import annotations

import hashlib
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _cookie_hash(cookie: str) -> str:
    return hashlib.sha256(cookie.encode()).hexdigest()


class SessionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def new_cookie() -> str:
        return secrets.token_hex(16)

    async def upsert_meta(self, cookie: str, *, user_agent: str | None = None, ip_hash: str | None = None) -> str:
        ch = _cookie_hash(cookie)
        await self.session.execute(
            text(
                "INSERT INTO client_sessions_meta (cookie_id, user_agent, ip_hash)"
                " VALUES (:c, :ua, :ip)"
                " ON CONFLICT (cookie_id) DO UPDATE"
                " SET last_seen_at = now(),"
                "     requests_count = client_sessions_meta.requests_count + 1,"
                "     user_agent = COALESCE(:ua, client_sessions_meta.user_agent),"
                "     ip_hash = COALESCE(:ip, client_sessions_meta.ip_hash)"
            ),
            {"c": ch, "ua": user_agent, "ip": ip_hash},
        )
        return ch

    async def list_sessions(self, cookie: str) -> list[dict[str, Any]]:
        ch = _cookie_hash(cookie)
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, title, last_activity_at, source_id, created_at FROM chat_sessions"
                    " WHERE cookie_id = :c AND kind = 'client' AND deleted_at IS NULL"
                    " ORDER BY last_activity_at DESC LIMIT 100"
                ),
                {"c": ch},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def soft_delete_session(self, cookie: str, session_id: UUID) -> bool:
        """Set deleted_at on a session iff it belongs to this cookie.

        Returns True if a row was actually updated.
        """
        ch = _cookie_hash(cookie)
        res = await self.session.execute(
            text(
                "UPDATE chat_sessions SET deleted_at = now()"
                " WHERE id = :id AND cookie_id = :c AND deleted_at IS NULL"
            ),
            {"id": session_id, "c": ch},
        )
        return (res.rowcount or 0) > 0

    async def create_session(self, cookie: str, *, source_id: UUID | None = None, title: str | None = None) -> dict[str, Any]:
        ch = _cookie_hash(cookie)
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO chat_sessions (kind, cookie_id, source_id, title)"
                    " VALUES ('client', :c, :sid, :ti)"
                    " RETURNING id, title, last_activity_at, source_id, created_at"
                ),
                {"c": ch, "sid": source_id, "ti": title},
            )
        ).mappings().first()
        assert row is not None
        return dict(row)

    async def get_session(self, cookie: str, session_id: UUID) -> dict[str, Any] | None:
        ch = _cookie_hash(cookie)
        row = (
            await self.session.execute(
                text(
                    "SELECT id, title, last_activity_at, source_id, created_at FROM chat_sessions"
                    " WHERE id = :id AND cookie_id = :c AND deleted_at IS NULL"
                ),
                {"id": session_id, "c": ch},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def add_message(self, session_id: UUID, role: str, content: str, *, metadata: dict | None = None) -> UUID:
        import json as _json
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO chat_messages (session_id, role, content, metadata)"
                    " VALUES (:sid, :r, :c, CAST(:m AS jsonb)) RETURNING id"
                ),
                {"sid": session_id, "r": role, "c": content, "m": _json.dumps(metadata or {})},
            )
        ).first()
        assert row is not None
        await self.session.execute(
            text("UPDATE chat_sessions SET last_activity_at = now() WHERE id = :id"),
            {"id": session_id},
        )
        return row[0]

    async def list_messages(self, session_id: UUID) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, role, content, metadata, created_at FROM chat_messages"
                    " WHERE session_id = :sid ORDER BY created_at"
                ),
                {"sid": session_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]
