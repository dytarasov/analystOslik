"""Integration tests for pass-2 (grouped LLM profiling): seeding, the
awaiting_input question cycle, and persistence of analyst semantics."""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from t2r.agents.admin_profiling.pass2 import (
    Pass2Deps,
    _describe_group_task,
    _describe_table_task,
    seed_pass2_tasks,
)
from t2r.infra.db.repos.profiling_task_repo_pg import ProfilingTaskRepo
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.llm.prompt_loader import PromptLoader

pytestmark = pytest.mark.integration


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, messages, temperature: float = 0.0) -> str:  # noqa: ANN001
        return self.responses.pop(0)


@pytest_asyncio.fixture
async def ctx(pg_dsn: str):
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
                    "INSERT INTO profiling_runs (source_id, status, started_at)"
                    " VALUES (:sid,'running',now()) RETURNING id"
                ),
                {"sid": sid},
            )
        ).scalar_one()
        # Harvested table + columns (no descriptions yet).
        repo = SemanticRepoPg(s)
        tid = await repo.upsert_table(
            source_id=sid, database="db", table="t", title=None, description=None,
            domain=None, tags=[], last_run_id=rid,
        )
        await repo.upsert_column(
            table_id=tid, name="status", position=1, data_type="String",
            description=None, semantic_role="dimension", null_ratio=0.0,
            distinct_count=3, total_count=100, examples=["active", "churned", "trial"],
            value_catalog=[{"value": "active", "count": 80}, {"value": "churned", "count": 15}],
        )
        await repo.upsert_column(
            table_id=tid, name="amount", position=2, data_type="Float64",
            description=None, semantic_role="measure", null_ratio=0.0,
            distinct_count=90, total_count=100, examples=[1.0, 2.5],
        )
        await s.commit()
    try:
        yield sm, rid, sid, tid
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_pass2_creates_table_and_group_tasks(ctx) -> None:
    sm, rid, sid, tid = ctx
    deps = Pass2Deps(sessionmaker=sm, llm=FakeLLM([]), prompts=PromptLoader())
    await seed_pass2_tasks(deps, run_id=rid, source_id=sid)

    async with sm() as s:
        tasks = await ProfilingTaskRepo(s).list_by_run(rid, kind="describe_group")
    targets = {t["target"] for t in tasks}
    assert "db.t#__table__" in targets
    # column groups depend on the table task
    table_task = next(t for t in tasks if t["target"] == "db.t#__table__")
    groups = [t for t in tasks if t["target"] != "db.t#__table__"]
    assert groups, "expected column-group tasks"
    assert all(table_task["id"] in g["depends_on"] for g in groups)


@pytest.mark.asyncio
async def test_describe_table_writes_summary(ctx) -> None:
    sm, rid, sid, tid = ctx
    llm = FakeLLM([
        json.dumps({
            "title": "Активность",
            "description": "Факты активности.",
            "grain": "одна строка = один статус",
            "domain": "operations",
            "tags": ["активность"],
        })
    ])
    deps = Pass2Deps(sessionmaker=sm, llm=llm, prompts=PromptLoader())
    res = await _describe_table_task(
        deps, sid, {"database": "db", "table_name": "t", "payload": {"mode": "table"}}
    )
    assert res.status == "done"
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT title, grain, domain FROM sem_tables WHERE id = :id"),
                {"id": tid},
            )
        ).mappings().first()
    assert row["title"] == "Активность"
    assert row["grain"] == "одна строка = один статус"


@pytest.mark.asyncio
async def test_describe_group_question_then_finalize(ctx) -> None:
    sm, rid, sid, tid = ctx
    # Round 0: model is unsure → asks a question.
    llm = FakeLLM([
        json.dumps({
            "columns": [],
            "questions": [{"column": "status", "text": "Что значит trial?", "choices": ["пробный", "тест"]}],
        }),
        # Round 1 (after answer): produces the enriched description.
        json.dumps({
            "columns": [{
                "name": "status",
                "description": "Статус учителя",
                "semantic_role": "dimension",
                "unit": None,
                "pii": False,
                "value_meanings": {"active": "активен", "churned": "отток", "trial": "пробный"},
                "safe_to_group_by": True,
                "safe_to_filter_by": True,
                "caveats": "",
                "suggested_aggregation": "count_distinct",
                "confidence": 0.92,
            }],
        }),
    ])
    deps = Pass2Deps(sessionmaker=sm, llm=llm, prompts=PromptLoader())

    task = {"database": "db", "table_name": "t", "columns": ["status"], "payload": {"mode": "columns"}}
    res1 = await _describe_group_task(deps, sid, task)
    assert res1.status == "awaiting_input"
    assert res1.payload["questions"][0]["column"] == "status"
    assert res1.payload["round"] == 1

    # Admin answered → re-run with answers in payload.
    answered = {
        "database": "db", "table_name": "t", "columns": ["status"],
        "payload": {
            "mode": "columns", "round": 1,
            "answers": [{"column": "status", "text": "Что значит trial?", "answer": "пробный период"}],
        },
    }
    res2 = await _describe_group_task(deps, sid, answered)
    assert res2.status == "done"

    async with sm() as s:
        cols = await SemanticRepoPg(s).get_columns(tid)
    status_col = next(c for c in cols if c["name"] == "status")
    assert status_col["description"] == "Статус учителя"
    assert status_col["semantics"]["value_meanings"]["trial"] == "пробный"
    assert status_col["semantics"]["confidence"] == 0.92


@pytest.mark.asyncio
async def test_describe_group_empty_output_fails_not_done(ctx) -> None:
    """Coverage guard: an empty/unparseable describer reply must NOT finalize the
    task 'done' (which would let coverage count the NULL-description column as
    covered — silent column loss). It fails so the scheduler retries / the run is
    honestly reported incomplete."""
    sm, rid, sid, tid = ctx
    # No questions, no columns — the prior silent-loss path.
    llm = FakeLLM([json.dumps({"columns": [], "questions": []})])
    deps = Pass2Deps(sessionmaker=sm, llm=llm, prompts=PromptLoader())
    task = {
        "database": "db", "table_name": "t",
        "columns": ["status"], "payload": {"mode": "columns"},
    }
    res = await _describe_group_task(deps, sid, task)
    assert res.status == "failed"

    async with sm() as s:
        cols = await SemanticRepoPg(s).get_columns(tid)
    status_col = next(c for c in cols if c["name"] == "status")
    assert status_col["description"] is None  # nothing was written
