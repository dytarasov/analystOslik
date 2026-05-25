"""End-to-end SSE smoke test against the built-in demo pipeline."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest


pytestmark = pytest.mark.integration


async def _login(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/admin/auth/login", json={"login": "admin", "password": "admin"}
    )
    assert r.status_code == 200


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Naive parser for a finished SSE response body."""
    out: list[tuple[str, dict]] = []
    event = ""
    data: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line:
            if event or data:
                payload = "\n".join(data)
                try:
                    obj = json.loads(payload) if payload else {}
                except json.JSONDecodeError:
                    obj = {"raw": payload}
                out.append((event, obj))
            event = ""
            data = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return out


@pytest.mark.asyncio
async def test_demo_sse_emits_steps_and_done(app_client: httpx.AsyncClient) -> None:
    await _login(app_client)
    r = await app_client.post("/api/admin/_demo/runs")
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    # The demo pipeline finishes in ~4-5 seconds (3 steps × 3 ticks × 0.5s).
    # We poll the SSE endpoint as a normal request — the producer terminates
    # itself when the bus closes after `done`.
    async with asyncio.timeout(20):
        r = await app_client.get(f"/api/admin/_demo/runs/{run_id}/events")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    kinds = [ev for ev, _ in events]
    assert "step.started" in kinds
    assert "step.completed" in kinds
    assert "done" in kinds
    # ensure ordering: every step has a started before completed
    started_steps = [d["step_id"] for k, d in events if k == "step.started"]
    completed_steps = [d["step_id"] for k, d in events if k == "step.completed"]
    assert started_steps == completed_steps
