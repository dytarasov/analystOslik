from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_login_logout_flow(app_client: httpx.AsyncClient) -> None:
    # me requires auth -> 401
    r = await app_client.get("/api/admin/auth/me")
    assert r.status_code == 401

    # wrong password
    r = await app_client.post(
        "/api/admin/auth/login", json={"login": "admin", "password": "wrong"}
    )
    assert r.status_code == 401

    # correct creds (fixture defaults: admin / admin)
    r = await app_client.post(
        "/api/admin/auth/login", json={"login": "admin", "password": "admin"}
    )
    assert r.status_code == 200
    assert r.json()["login"] == "admin"
    assert "t2r_admin" in app_client.cookies

    # me now works
    r = await app_client.get("/api/admin/auth/me")
    assert r.status_code == 200
    assert r.json() == {"login": "admin"}

    # logout clears cookie
    r = await app_client.post("/api/admin/auth/logout")
    assert r.status_code == 204
    app_client.cookies.delete("t2r_admin")
    r = await app_client.get("/api/admin/auth/me")
    assert r.status_code == 401
