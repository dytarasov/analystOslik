from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from t2r.domain.models.source import DataSource
from t2r.infra.security.cipher import FernetCipher


_SOURCE_COLUMNS = (
    "id, name, kind, host, port, database, username, secure,"
    " extra_settings, readonly_verified, last_test_at, last_test_status,"
    " last_test_error, last_profiling_run_id, last_profiled_at, profiling_status,"
    " glossary_md, glossary_ingested_at,"
    " created_at, updated_at"
)


def _row_to_source(row: Any) -> DataSource:
    return DataSource(
        id=row.id,
        name=row.name,
        kind=row.kind,
        host=row.host,
        port=row.port,
        database=row.database,
        username=row.username,
        secure=row.secure,
        extra_settings=row.extra_settings or {},
        readonly_verified=row.readonly_verified,
        last_test_at=row.last_test_at,
        last_test_status=row.last_test_status,
        last_test_error=row.last_test_error,
        last_profiling_run_id=getattr(row, "last_profiling_run_id", None),
        last_profiled_at=getattr(row, "last_profiled_at", None),
        profiling_status=getattr(row, "profiling_status", "never_profiled") or "never_profiled",
        glossary_md=getattr(row, "glossary_md", None),
        glossary_ingested_at=getattr(row, "glossary_ingested_at", None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SourceRepoPg:
    def __init__(self, session: AsyncSession, cipher: FernetCipher) -> None:
        self.session = session
        self.cipher = cipher

    async def list(self) -> list[DataSource]:
        rows = (
            await self.session.execute(
                text(
                    f"SELECT {_SOURCE_COLUMNS} FROM data_sources ORDER BY created_at DESC"
                )
            )
        ).mappings().all()
        return [_row_to_source(type("R", (), dict(r))) for r in rows]

    async def get(self, source_id: UUID) -> DataSource | None:
        row = (
            await self.session.execute(
                text(f"SELECT {_SOURCE_COLUMNS} FROM data_sources WHERE id = :id"),
                {"id": source_id},
            )
        ).mappings().first()
        if not row:
            return None
        return _row_to_source(type("R", (), dict(row)))

    async def get_password(self, source_id: UUID) -> str | None:
        row = (
            await self.session.execute(
                text("SELECT password_encrypted FROM data_sources WHERE id = :id"),
                {"id": source_id},
            )
        ).first()
        if not row:
            return None
        return self.cipher.decrypt(bytes(row[0]))

    async def create(
        self,
        *,
        name: str,
        kind: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        secure: bool,
        extra_settings: dict[str, Any],
    ) -> DataSource:
        encrypted = self.cipher.encrypt(password)
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO data_sources (name, kind, host, port, database, username,"
                    " password_encrypted, secure, extra_settings)"
                    " VALUES (:name, :kind, :host, :port, :database, :username,"
                    " :password_encrypted, :secure, CAST(:extra_settings AS jsonb))"
                    f" RETURNING {_SOURCE_COLUMNS}"
                ),
                {
                    "name": name,
                    "kind": kind,
                    "host": host,
                    "port": port,
                    "database": database,
                    "username": username,
                    "password_encrypted": encrypted,
                    "secure": secure,
                    "extra_settings": __import__("json").dumps(extra_settings),
                },
            )
        ).mappings().first()
        assert row is not None
        return _row_to_source(type("R", (), dict(row)))

    async def update(
        self,
        source_id: UUID,
        *,
        name: str | None = None,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        username: str | None = None,
        password: str | None = None,
        secure: bool | None = None,
        extra_settings: dict[str, Any] | None = None,
        glossary_md: str | None = None,
    ) -> DataSource | None:
        """Partial update. Only provided fields are changed. An empty/None
        password is ignored (keeps the existing encrypted secret). If any
        connection-affecting field changes, the readonly/test verdict is reset
        — it no longer reflects the new endpoint until re-tested. Editing the
        glossary clears ``glossary_ingested_at`` so the admin sees it needs a
        re-ingest; it is NOT connection-affecting.
        """
        sets: list[str] = []
        params: dict[str, Any] = {"id": source_id}
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if host is not None:
            sets.append("host = :host")
            params["host"] = host
        if port is not None:
            sets.append("port = :port")
            params["port"] = port
        if database is not None:
            sets.append("database = :database")
            params["database"] = database
        if username is not None:
            sets.append("username = :username")
            params["username"] = username
        if secure is not None:
            sets.append("secure = :secure")
            params["secure"] = secure
        if extra_settings is not None:
            sets.append("extra_settings = CAST(:extra_settings AS jsonb)")
            params["extra_settings"] = __import__("json").dumps(extra_settings)
        if glossary_md is not None:
            sets.append("glossary_md = :glossary_md")
            sets.append("glossary_ingested_at = NULL")
            params["glossary_md"] = glossary_md
        password_changed = password is not None and password != ""
        if password_changed:
            sets.append("password_encrypted = :pwd")
            params["pwd"] = self.cipher.encrypt(password)

        conn_changed = password_changed or any(
            v is not None for v in (host, port, database, username, secure)
        )
        if conn_changed:
            sets.append("readonly_verified = false")
            sets.append("last_test_status = NULL")
            sets.append("last_test_error = NULL")

        if not sets:
            return await self.get(source_id)

        sets.append("updated_at = now()")
        row = (
            await self.session.execute(
                text(
                    f"UPDATE data_sources SET {', '.join(sets)}"
                    f" WHERE id = :id RETURNING {_SOURCE_COLUMNS}"
                ),
                params,
            )
        ).mappings().first()
        if not row:
            return None
        return _row_to_source(type("R", (), dict(row)))

    async def set_glossary_ingested(self, source_id: UUID) -> None:
        """Mark the current glossary as structurally ingested just now."""
        await self.session.execute(
            text(
                "UPDATE data_sources SET glossary_ingested_at = now(),"
                " updated_at = now() WHERE id = :id"
            ),
            {"id": source_id},
        )

    async def update_test_status(
        self,
        source_id: UUID,
        *,
        ok: bool,
        readonly: bool,
        error: str | None,
    ) -> None:
        await self.session.execute(
            text(
                "UPDATE data_sources SET last_test_at = now(),"
                " last_test_status = :status, last_test_error = :err,"
                " readonly_verified = :ro, updated_at = now()"
                " WHERE id = :id"
            ),
            {
                "id": source_id,
                "status": "ok" if ok else "fail",
                "err": error,
                "ro": readonly,
            },
        )

    async def delete(self, source_id: UUID) -> None:
        await self.session.execute(
            text("DELETE FROM data_sources WHERE id = :id"),
            {"id": source_id},
        )

    async def set_profiling_status(
        self,
        source_id: UUID,
        *,
        status: str,
        run_id: UUID | None = None,
        mark_profiled_at: bool = False,
    ) -> None:
        await self.session.execute(
            text(
                "UPDATE data_sources SET"
                "  profiling_status = :status,"
                "  last_profiling_run_id = COALESCE(:rid, last_profiling_run_id),"
                "  last_profiled_at = CASE WHEN :touch THEN now() ELSE last_profiled_at END,"
                "  updated_at = now()"
                " WHERE id = :id"
            ),
            {
                "id": source_id,
                "status": status,
                "rid": run_id,
                "touch": mark_profiled_at,
            },
        )

    async def sync_profiling_status_from_runs(self, source_id: UUID) -> None:
        """Recompute denormalized status from the most relevant run.

        Active wins; otherwise latest done → profiled; otherwise latest failed
        → failed; otherwise never_profiled.
        """
        await self.session.execute(
            text(
                "WITH ranked AS ("
                "  SELECT id, status, started_at, finished_at, created_at,"
                "    ROW_NUMBER() OVER (ORDER BY"
                "      CASE status"
                "        WHEN 'running' THEN 1"
                "        WHEN 'awaiting_input' THEN 1"
                "        WHEN 'paused' THEN 1"
                "        WHEN 'pending' THEN 1"
                "        WHEN 'done' THEN 2"
                "        WHEN 'failed' THEN 3"
                "        WHEN 'cancelled' THEN 4"
                "        ELSE 5 END,"
                "      COALESCE(finished_at, started_at, created_at) DESC"
                "    ) AS rn"
                "  FROM profiling_runs WHERE source_id = :sid"
                ")"
                " UPDATE data_sources ds SET"
                "   last_profiling_run_id = r.id,"
                "   last_profiled_at = CASE WHEN r.status = 'done' THEN r.finished_at ELSE ds.last_profiled_at END,"
                "   profiling_status = CASE"
                "     WHEN r.status IN ('running','awaiting_input','paused','pending') THEN 'in_progress'"
                "     WHEN r.status = 'done' THEN 'profiled'"
                "     WHEN r.status = 'failed' THEN 'failed'"
                "     WHEN r.status = 'cancelled' AND ds.profiling_status = 'in_progress' THEN 'never_profiled'"
                "     WHEN r.status = 'cancelled' THEN ds.profiling_status"
                "     ELSE ds.profiling_status END,"
                "   updated_at = now()"
                " FROM ranked r WHERE r.rn = 1 AND ds.id = :sid"
            ),
            {"sid": source_id},
        )
