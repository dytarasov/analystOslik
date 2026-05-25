from __future__ import annotations

import asyncio

import pytest

from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.run import AgentRun, RunState
from t2r.agents.orchestrator.step import Step


class _OkStep(Step):
    async def execute(self, run: AgentRun, ctx) -> None:
        ctx.set("hits", ctx.get("hits", 0) + 1)


class _BoomStep(Step):
    async def execute(self, run: AgentRun, ctx) -> None:
        raise RuntimeError("boom")


class _WaitInputStep(Step):
    async def execute(self, run: AgentRun, ctx) -> None:
        ans = await run.await_user_input("what?")
        ctx.set("answer", ans)


@pytest.mark.asyncio
async def test_pipeline_runs_all_steps_in_order():
    run = AgentRun("test")
    pipeline = Pipeline([_OkStep(), _OkStep(), _OkStep()])
    await pipeline.run(run)
    assert run.state == RunState.done
    assert run.context.get("hits") == 3


@pytest.mark.asyncio
async def test_pipeline_stops_on_failure():
    run = AgentRun("test")
    pipeline = Pipeline([_OkStep(), _BoomStep(), _OkStep()])
    await pipeline.run(run)
    assert run.state == RunState.failed
    assert run.context.get("hits") == 1
    assert run.error == "boom"


@pytest.mark.asyncio
async def test_pipeline_continues_on_error_when_allowed():
    run = AgentRun("test")
    pipeline = Pipeline([_OkStep(), _BoomStep(), _OkStep()], continue_on_error=True)
    await pipeline.run(run)
    assert run.state == RunState.done
    assert run.context.get("hits") == 2


@pytest.mark.asyncio
async def test_pipeline_awaiting_input_and_respond():
    run = AgentRun("test")
    pipeline = Pipeline([_WaitInputStep()])
    runner = asyncio.create_task(pipeline.run(run))

    async def wait_for_state(target: RunState, deadline: float = 2.0) -> None:
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < deadline:
            if run.state == target:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"never reached {target}")

    await wait_for_state(RunState.awaiting_input)
    ok = await run.respond("forty two")
    assert ok is True
    await asyncio.wait_for(runner, timeout=2.0)
    assert run.state == RunState.done
    assert run.context.get("answer") == "forty two"


@pytest.mark.asyncio
async def test_cancel_marks_state():
    run = AgentRun("test")
    await run.cancel()
    assert run.state == RunState.cancelled
