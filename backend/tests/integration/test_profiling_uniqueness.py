"""Active-profiling uniqueness + denormalized status + restart recovery.

We don't need a real ClickHouse for these tests — uniqueness lives entirely in
PG. We touch profiling_runs directly via a session and exercise the repo +
denormalization helpers.
"""
from __future__ import annotations

import asyncpg
import httpx
import pytest

pytestmark = pytest.mark.integration


async def _login(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/admin/auth/login", json={"login": "admin", "password": "admin"}
    )
    assert r.status_code == 200


async def _insert_source(conn: asyncpg.Connection, name: str = "src-unique") -> str:
    row = await conn.fetchrow(
        "INSERT INTO data_sources (name, kind, host, port, database, username,"
        " password_encrypted) VALUES ($1, 'clickhouse', 'h', 8123, 'db', 'u',"
        " '\\x00'::bytea) RETURNING id",
        name,
    )
    return str(row["id"])


@pytest.mark.asyncio
async def test_unique_active_index_blocks_duplicate_active_runs(pg_dsn: str) -> None:
    raw = pg_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw)
    try:
        sid = await _insert_source(conn, "src-uniq-1")
        await conn.execute(
            "INSERT INTO profiling_runs (source_id, status) VALUES ($1, 'running')",
            sid,
        )
        # Second active run for the same source must violate the partial unique index.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO profiling_runs (source_id, status) VALUES ($1, 'pending')",
                sid,
            )
        # Once the first run becomes terminal, a new active row is permitted.
        await conn.execute(
            "UPDATE profiling_runs SET status='done', finished_at=now() WHERE source_id=$1",
            sid,
        )
        await conn.execute(
            "INSERT INTO profiling_runs (source_id, status) VALUES ($1, 'running')",
            sid,
        )
        n = await conn.fetchval(
            "SELECT count(*) FROM profiling_runs WHERE source_id=$1", sid
        )
        assert n == 2
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_recovery_marks_active_runs_abandoned_and_syncs_source(pg_dsn: str) -> None:
    """Simulate a backend restart: lifespan recovery must release the active slot."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from t2r.infra.db.repos.profiling_repo_pg import ProfilingRepoPg
    from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
    from t2r.infra.security.cipher import FernetCipher
    from t2r.settings import get_settings

    raw = pg_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw)
    try:
        sid = await _insert_source(conn, "src-recover-1")
        await conn.execute(
            "INSERT INTO profiling_runs (source_id, status, started_at)"
            " VALUES ($1, 'running', now())",
            sid,
        )
    finally:
        await conn.close()

    settings = get_settings()
    cipher = FernetCipher(settings.encryption_key.encode())
    engine = create_async_engine(pg_dsn)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as s:
            ids = await ProfilingRepoPg(s).mark_all_active_abandoned(
                reason="abandoned_on_restart"
            )
            assert any(str(x) == sid for x in ids)
            for x in ids:
                await SourceRepoPg(s, cipher).sync_profiling_status_from_runs(x)
            await s.commit()
    finally:
        await engine.dispose()

    conn = await asyncpg.connect(raw)
    try:
        row = await conn.fetchrow(
            "SELECT status, error FROM profiling_runs WHERE source_id=$1", sid
        )
        assert row["status"] == "failed"
        assert row["error"] == "abandoned_on_restart"
        ds = await conn.fetchrow(
            "SELECT profiling_status FROM data_sources WHERE id=$1", sid
        )
        assert ds["profiling_status"] == "failed"
        # And the slot is free — we can insert a new active run.
        await conn.execute(
            "INSERT INTO profiling_runs (source_id, status) VALUES ($1, 'pending')",
            sid,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_active_endpoint_reflects_state(
    app_client: httpx.AsyncClient, pg_dsn: str
) -> None:
    """GET /api/admin/profiling/runs/active?source_id=... returns the active row or None."""
    await _login(app_client)
    r = await app_client.post(
        "/api/admin/sources",
        json={
            "name": "active-ep",
            "host": "h",
            "port": 8123,
            "database": "db",
            "username": "u",
            "password": "p",
        },
    )
    sid = r.json()["id"]

    # No runs yet → None
    r = await app_client.get(f"/api/admin/profiling/runs/active?source_id={sid}")
    assert r.status_code == 200
    assert r.json() is None

    # Inject an active run directly (bypassing CH-dependent start()).
    raw = pg_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw)
    try:
        await conn.execute(
            "INSERT INTO profiling_runs (source_id, status, started_at, params)"
            " VALUES ($1, 'running', now(), '{\"agent_run_id\": \"ghost\"}'::jsonb)",
            sid,
        )
    finally:
        await conn.close()

    r = await app_client.get(f"/api/admin/profiling/runs/active?source_id={sid}")
    assert r.status_code == 200
    body = r.json()
    assert body is not None
    assert body["status"] == "running"
    assert body["agent_run_id"] == "ghost"
    # Worker isn't in registry — should report not attached.
    assert body["attached"] is False


@pytest.mark.asyncio
async def test_sync_profiling_status_promotes_done_run(pg_dsn: str) -> None:
    """A finished 'done' run should flip data_sources.profiling_status to profiled."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
    from t2r.infra.security.cipher import FernetCipher
    from t2r.settings import get_settings

    raw = pg_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw)
    try:
        sid = await _insert_source(conn, "src-done-1")
        await conn.execute(
            "INSERT INTO profiling_runs (source_id, status, started_at, finished_at)"
            " VALUES ($1, 'done', now() - interval '5 minutes', now())",
            sid,
        )
    finally:
        await conn.close()

    settings = get_settings()
    cipher = FernetCipher(settings.encryption_key.encode())
    engine = create_async_engine(pg_dsn)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as s:
            await SourceRepoPg(s, cipher).sync_profiling_status_from_runs(sid)
            await s.commit()
    finally:
        await engine.dispose()

    conn = await asyncpg.connect(raw)
    try:
        row = await conn.fetchrow(
            "SELECT profiling_status, last_profiled_at, last_profiling_run_id"
            " FROM data_sources WHERE id=$1",
            sid,
        )
        assert row["profiling_status"] == "profiled"
        assert row["last_profiled_at"] is not None
        assert row["last_profiling_run_id"] is not None
    finally:
        await conn.close()
