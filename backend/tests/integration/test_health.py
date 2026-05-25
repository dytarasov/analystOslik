from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_healthz(app_client: httpx.AsyncClient) -> None:
    r = await app_client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_reports_postgres_ok(app_client: httpx.AsyncClient) -> None:
    # Neo4j is not started in tests — readyz reports per-component statuses, so
    # we only assert that postgres part is healthy.
    r = await app_client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["postgres"] == "ok"
