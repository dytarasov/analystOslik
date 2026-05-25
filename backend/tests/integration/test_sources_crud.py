from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.integration


async def _login(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/admin/auth/login", json={"login": "admin", "password": "admin"}
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_sources_crud_without_real_clickhouse(app_client: httpx.AsyncClient) -> None:
    await _login(app_client)

    # initially empty
    r = await app_client.get("/api/admin/sources")
    assert r.status_code == 200
    assert r.json() == []

    # create
    payload = {
        "name": "test-ch",
        "host": "nonexistent.local",
        "port": 8123,
        "database": "demo",
        "username": "u",
        "password": "p",
    }
    r = await app_client.post("/api/admin/sources", json=payload)
    assert r.status_code == 200
    created = r.json()
    sid = created["id"]
    # password must not leak
    assert "password" not in created and "password_encrypted" not in created
    assert created["name"] == "test-ch"
    assert created["readonly_verified"] is False

    # list returns one
    r = await app_client.get("/api/admin/sources")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # get by id
    r = await app_client.get(f"/api/admin/sources/{sid}")
    assert r.status_code == 200

    # public endpoint reveals minimal info without auth
    fresh = httpx.AsyncClient(
        transport=app_client._transport, base_url="http://testserver"
    )
    try:
        r = await fresh.get("/api/sources/public")
        assert r.status_code == 200
        items = r.json()
        assert any(i["id"] == sid for i in items)
        assert all("password" not in i for i in items)
    finally:
        await fresh.aclose()

    # delete
    r = await app_client.delete(f"/api/admin/sources/{sid}")
    assert r.status_code == 204
    r = await app_client.get("/api/admin/sources")
    assert r.json() == []


@pytest.mark.asyncio
async def test_test_connection_fails_gracefully(app_client: httpx.AsyncClient) -> None:
    await _login(app_client)
    r = await app_client.post(
        "/api/admin/sources",
        json={
            "name": "bogus",
            "host": "10.255.255.1",  # non-routable
            "port": 1,
            "database": "demo",
            "username": "u",
            "password": "p",
        },
    )
    sid = r.json()["id"]
    r = await app_client.post(f"/api/admin/sources/{sid}/test-connection")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body.get("error")
