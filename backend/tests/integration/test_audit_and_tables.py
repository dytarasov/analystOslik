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


@pytest.mark.asyncio
async def test_update_table_writes_revision_and_returns_columns(
    app_client: httpx.AsyncClient, pg_dsn: str
) -> None:
    await _login(app_client)

    # seed a source + sem_tables/sem_columns directly via asyncpg
    raw = pg_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw)
    try:
        sid = await conn.fetchval(
            "INSERT INTO data_sources (name, kind, host, port, database, username, password_encrypted)"
            " VALUES ('s', 'clickhouse', 'h', 8123, 'd', 'u', E'\\\\x00')"
            " RETURNING id"
        )
        tid = await conn.fetchval(
            "INSERT INTO sem_tables (source_id, database, table_name, title, description, domain, tags)"
            " VALUES ($1, 'analytics', 'orders', 'Заказы', 'старое описание', 'sales', ARRAY['fact'])"
            " RETURNING id",
            sid,
        )
        await conn.execute(
            "INSERT INTO sem_columns (table_id, name, position, data_type, description)"
            " VALUES ($1, 'id', 1, 'Int64', 'PK')",
            tid,
        )
    finally:
        await conn.close()

    # get
    r = await app_client.get(f"/api/admin/tables/{tid}")
    assert r.status_code == 200
    assert r.json()["description"] == "старое описание"
    assert len(r.json()["columns"]) == 1

    # update
    r = await app_client.patch(
        f"/api/admin/tables/{tid}",
        json={"description": "новое описание", "reason": "manual"},
    )
    assert r.status_code == 200
    assert r.json()["description"] == "новое описание"

    # revision row was written
    conn = await asyncpg.connect(raw)
    try:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM sem_revisions WHERE entity_kind='sem_table' AND entity_id=$1",
            tid,
        )
        assert cnt == 1
    finally:
        await conn.close()

    # confirm
    r = await app_client.post(f"/api/admin/tables/{tid}/confirm")
    assert r.status_code == 200
    assert r.json()["confirmation_status"] == "confirmed"
