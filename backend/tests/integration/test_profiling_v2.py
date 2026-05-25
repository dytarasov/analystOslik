"""Integration tests for the v2 orchestration surface: progress reporting and
the answer→re-queue transition of the question inbox."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from t2r.agents.orchestrator.registry import RunRegistry
from t2r.infra.db.repos.profiling_task_repo_pg import ProfilingTaskRepo
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.services.profiling_service import ProfilingService

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def env(pg_dsn: str):
    engine = create_async_engine(pg_dsn)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        sid = (
            await s.execute(
                text(
                    "INSERT INTO data_sources (name, kind, host, port, database,"
                    " username, password_encrypted, secure)"
                    " VALUES ('t','clickhouse','h',8123,'d','u',:pw,false) RETURNING id"
                ),
                {"pw": b"x"},
            )
        ).scalar_one()
        rid = (
            await s.execute(
                text(
                    "INSERT INTO profiling_runs (source_id, status, params, started_at)"
                    " VALUES (:sid,'running', CAST('{}' AS jsonb), now()) RETURNING id"
                ),
                {"sid": sid},
            )
        ).scalar_one()
        await s.commit()
    svc = ProfilingService(
        sessionmaker=sm,
        cipher=MagicMock(),
        neo4j_driver=MagicMock(),
        llm=MagicMock(),
        embeddings=MagicMock(),
        prompts=PromptLoader(),
        registry=RunRegistry(),
    )
    try:
        yield svc, sm, rid, sid
    finally:
        await engine.dispose()


async def _seed_parked_question(sm, rid, sid) -> str:
    async with sm() as s:
        repo = ProfilingTaskRepo(s)
        tid = await repo.create(
            run_id=rid, source_id=sid, kind="describe_group",
            target="db.t#g0", database="db", table_name="t", columns=["status"],
        )
        await repo.set_status(
            tid, "awaiting_input",
            payload={
                "mode": "columns", "round": 1,
                "questions": [{"column": "status", "text": "Что значит trial?", "choices": ["пробный"]}],
            },
        )
        await s.commit()
    return str(tid)


@pytest.mark.asyncio
async def test_get_progress_reports_questions(env) -> None:
    svc, sm, rid, sid = env
    await _seed_parked_question(sm, rid, sid)

    prog = await svc.get_progress(rid)
    assert prog["counts"].get("awaiting_input") == 1
    assert len(prog["questions"]) == 1
    q = prog["questions"][0]
    assert q["table"] == "t"
    assert q["questions"][0]["column"] == "status"


@pytest.mark.asyncio
async def test_answer_question_requeues_task(env, monkeypatch) -> None:
    svc, sm, rid, sid = env
    tid = await _seed_parked_question(sm, rid, sid)

    # Don't actually resume the pipeline in the background.
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(svc, "_continue", _noop)

    res = await svc.answer_question(
        tid, [{"column": "status", "text": "Что значит trial?", "answer": "пробный период"}]
    )
    assert res["ok"] is True

    async with sm() as s:
        task = await ProfilingTaskRepo(s).get(tid)
    assert task["status"] == "pending"  # re-queued for re-run
    assert task["payload"]["answers"][0]["answer"] == "пробный период"
