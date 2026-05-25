"""Integration tests for the profiling task state manager (Phase 1).

Exercises the durable status model, idempotent enqueue, dependency-aware
atomic claim, restart recovery, the coverage invariant, and the scheduler.
"""
from __future__ import annotations

from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from t2r.agents.admin_profiling.pass1 import seed_pass1_tasks
from t2r.agents.admin_profiling.scheduler import TaskResult, TaskScheduler
from t2r.infra.db.repos.profiling_task_repo_pg import ProfilingTaskRepo

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def ctx(pg_dsn: str):
    """A sessionmaker plus a fresh source + profiling run to hang tasks off."""
    engine = create_async_engine(pg_dsn)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        sid = (
            await s.execute(
                text(
                    "INSERT INTO data_sources (name, kind, host, port, database,"
                    " username, password_encrypted, secure)"
                    " VALUES ('t', 'clickhouse', 'h', 8123, 'd', 'u', :pw, false)"
                    " RETURNING id"
                ),
                {"pw": b"x"},
            )
        ).scalar_one()
        rid = (
            await s.execute(
                text(
                    "INSERT INTO profiling_runs (source_id, status, started_at)"
                    " VALUES (:sid, 'running', now()) RETURNING id"
                ),
                {"sid": sid},
            )
        ).scalar_one()
        await s.commit()
    try:
        yield sm, rid, sid
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_idempotent_enqueue(ctx) -> None:
    sm, rid, sid = ctx
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        a = await repo.create(run_id=rid, source_id=sid, kind="harvest_table", target="db.t")
        b = await repo.create(run_id=rid, source_id=sid, kind="harvest_table", target="db.t")
        await s.commit()
    assert a == b


@pytest.mark.asyncio
async def test_claim_respects_dependencies(ctx) -> None:
    sm, rid, sid = ctx
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        a = await repo.create(run_id=rid, source_id=sid, kind="harvest_table", target="A")
        await repo.create(
            run_id=rid, source_id=sid, kind="describe_group", target="B", depends_on=[a]
        )
        await s.commit()

    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        first = await repo.claim_next(rid)
        await s.commit()
    assert first is not None and first["target"] == "A"  # B blocked by A

    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        # B still not runnable while A is only 'running'
        assert await repo.claim_next(rid) is None
        await repo.set_status(a, "done")
        await s.commit()

    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        second = await repo.claim_next(rid)
        await s.commit()
    assert second is not None and second["target"] == "B"


@pytest.mark.asyncio
async def test_claim_is_exclusive(ctx) -> None:
    sm, rid, sid = ctx
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        for n in ("x", "y", "z"):
            await repo.create(run_id=rid, source_id=sid, kind="harvest_table", target=n)
        await s.commit()

    claimed = []
    for _ in range(4):
        async with sm() as s:
            t = await ProfilingTaskRepo(s).claim_next(rid)
            await s.commit()
        if t:
            claimed.append(t["target"])
    assert sorted(claimed) == ["x", "y", "z"]  # each claimed once, 4th empty


@pytest.mark.asyncio
async def test_reset_running_for_resume(ctx) -> None:
    sm, rid, sid = ctx
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        await repo.create(run_id=rid, source_id=sid, kind="harvest_table", target="A")
        await s.commit()
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        await repo.claim_next(rid)  # → running
        await s.commit()
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        n = await repo.reset_running(rid)
        await s.commit()
        assert n == 1
        again = await repo.claim_next(rid)  # runnable again
        await s.commit()
    assert again is not None


@pytest.mark.asyncio
async def test_coverage_invariant(ctx) -> None:
    sm, rid, sid = ctx
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        h = await repo.create(
            run_id=rid, source_id=sid, kind="harvest_table", target="db.t",
            database="db", table_name="t",
        )
        await repo.set_status(h, "done", result={"columns": ["a", "b", "c"]})
        g1 = await repo.create(
            run_id=rid, source_id=sid, kind="describe_group", target="db.t#g1",
            database="db", table_name="t", columns=["a", "b"],
        )
        await repo.set_status(g1, "done")
        await s.commit()

    async with sm() as s:
        cov = await ProfilingTaskRepo(s).coverage(rid)
    assert cov["expected"] == 3
    assert cov["complete"] is False
    assert cov["missing"] == [{"database": "db", "table": "t", "column": "c"}]

    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        g2 = await repo.create(
            run_id=rid, source_id=sid, kind="describe_group", target="db.t#g2",
            database="db", table_name="t", columns=["c"],
        )
        await repo.set_status(g2, "done")
        await s.commit()
    async with sm() as s:
        cov = await ProfilingTaskRepo(s).coverage(rid)
    assert cov["complete"] is True and cov["missing"] == []


@pytest.mark.asyncio
async def test_scheduler_runs_chain_and_parallel(ctx) -> None:
    sm, rid, sid = ctx
    executed: list[str] = []

    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        a = await repo.create(run_id=rid, source_id=sid, kind="harvest_table", target="A")
        await repo.create(
            run_id=rid, source_id=sid, kind="describe_group", target="B", depends_on=[a]
        )
        await repo.create(run_id=rid, source_id=sid, kind="harvest_table", target="C")
        await s.commit()

    async def handler(task: dict) -> TaskResult:
        executed.append(task["target"])
        return TaskResult("done", result={"ok": True})

    sched = TaskScheduler(sm, concurrency=4)
    sched.register("harvest_table", handler)
    sched.register("describe_group", handler)
    summary = await sched.drain(rid)

    assert set(executed) == {"A", "B", "C"}
    assert executed.index("A") < executed.index("B")  # dep order honoured
    assert summary["counts"].get("done") == 3


@pytest.mark.asyncio
async def test_scheduler_parks_on_awaiting_input(ctx) -> None:
    sm, rid, sid = ctx
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        q = await repo.create(run_id=rid, source_id=sid, kind="question", target="Q")
        await repo.create(
            run_id=rid, source_id=sid, kind="describe_group", target="D", depends_on=[q]
        )
        await s.commit()

    async def ask(task: dict) -> TaskResult:
        return TaskResult("awaiting_input", payload={"q": "?"})

    async def never(task: dict) -> TaskResult:  # pragma: no cover - must not run
        raise AssertionError("dependent ran before its question was answered")

    sched = TaskScheduler(sm, concurrency=2)
    sched.register("question", ask)
    sched.register("describe_group", never)
    summary = await sched.drain(rid)

    assert summary["counts"].get("awaiting_input") == 1
    assert summary["counts"].get("pending") == 1  # the blocked dependent


@pytest.mark.asyncio
async def test_scheduler_retries_then_succeeds(ctx) -> None:
    sm, rid, sid = ctx
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        await repo.create(run_id=rid, source_id=sid, kind="harvest_table", target="flaky")
        await s.commit()

    async def flaky(task: dict) -> TaskResult:
        # `attempts` was incremented by claim_next, so it's 1 on the first run.
        if task["attempts"] < 2:
            raise RuntimeError("transient")
        return TaskResult("done")

    sched = TaskScheduler(sm, concurrency=1, max_attempts=3)
    sched.register("harvest_table", flaky)
    summary = await sched.drain(rid)
    assert summary["counts"].get("done") == 1


@pytest.mark.asyncio
async def test_seed_pass1_creates_harvest_and_relations(ctx) -> None:
    sm, rid, sid = ctx
    whitelist = [("cdm", "events"), ("dict", "teachers"), ("dict", "schools")]
    async with sm() as s:
        await seed_pass1_tasks(
            ProfilingTaskRepo(s), run_id=rid, source_id=sid, whitelist=whitelist
        )
        await s.commit()

    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        harvests = await repo.list_by_run(rid, kind="harvest_table")
        rels = await repo.list_by_run(rid, kind="relations")
    assert len(harvests) == 3
    assert len(rels) == 1
    # relations depends on every harvest task
    assert sorted(rels[0]["depends_on"]) == sorted(h["id"] for h in harvests)


@pytest.mark.asyncio
async def test_pass1_drain_runs_harvest_before_relations(ctx) -> None:
    sm, rid, sid = ctx
    whitelist = [("cdm", "events"), ("dict", "teachers")]
    async with sm() as s:
        await seed_pass1_tasks(
            ProfilingTaskRepo(s), run_id=rid, source_id=sid, whitelist=whitelist
        )
        await s.commit()

    order: list[str] = []

    async def fake_harvest(task: dict) -> TaskResult:
        order.append(f"harvest:{task['target']}")
        return TaskResult("done", result={"columns": ["a", "b"]})

    async def fake_relations(task: dict) -> TaskResult:
        # All harvests must be done by the time relations runs.
        async with sm() as s:
            counts = await ProfilingTaskRepo(s).counts(rid)
        assert counts.get("done") == 2, "relations ran before harvests finished"
        order.append("relations")
        return TaskResult("done", result={"relations": 0})

    sched = TaskScheduler(sm, concurrency=4)
    sched.register("harvest_table", fake_harvest)
    sched.register("relations", fake_relations)
    summary = await sched.drain(rid)

    assert summary["counts"].get("done") == 3
    assert order[-1] == "relations"  # relations last, after both harvests
