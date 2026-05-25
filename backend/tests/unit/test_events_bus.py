from __future__ import annotations

import asyncio

import pytest

from t2r.agents.orchestrator.events_bus import EventsBus
from t2r.domain.events.types import AgentEvent


@pytest.mark.asyncio
async def test_publish_assigns_monotonic_ids():
    bus = EventsBus()
    await bus.publish(AgentEvent("step.started", {"a": 1}))
    await bus.publish(AgentEvent("step.completed", {"a": 2}))
    assert bus.last_id == 2


@pytest.mark.asyncio
async def test_replay_for_new_subscribers():
    bus = EventsBus()
    await bus.publish(AgentEvent("step.started", {}))
    await bus.publish(AgentEvent("step.completed", {}))

    received: list[int] = []

    async def consume():
        async for ev in bus.subscribe(last_event_id=0):
            received.append(ev.id)
            if len(received) >= 2:
                return

    task = asyncio.create_task(consume())
    await asyncio.wait_for(task, timeout=2.0)
    assert received == [1, 2]


@pytest.mark.asyncio
async def test_subscribe_resumes_after_last_event_id():
    bus = EventsBus()
    await bus.publish(AgentEvent("step.started", {}))
    await bus.publish(AgentEvent("step.completed", {}))

    received: list[int] = []

    async def consume():
        async for ev in bus.subscribe(last_event_id=1):
            received.append(ev.id)
            return

    await asyncio.wait_for(asyncio.create_task(consume()), timeout=2.0)
    assert received == [2]


@pytest.mark.asyncio
async def test_close_terminates_subscribers():
    bus = EventsBus()

    async def consume():
        count = 0
        async for _ev in bus.subscribe():
            count += 1
        return count

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await bus.publish(AgentEvent("step.started", {}))
    await bus.close()
    n = await asyncio.wait_for(task, timeout=2.0)
    assert n == 1
