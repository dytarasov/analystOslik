"""Unit tests for the ReAct client agent loop and its tools.

The LLM is faked with a scripted sequence of tool-calling turns, all infra is
mocked, so we exercise the loop wiring (explore → run_sql → finish), the guard
embedded in run_sql, pausing on ask_user, and the finish/persist path — without
a live LLM, ClickHouse, or Postgres.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from t2r.agents.client_agent.deps import ClientAgentDeps
from t2r.agents.client_agent.loop import ReactAgentStep
from t2r.agents.client_agent.tools import ToolContext, build_registry
from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.run import AgentRun, RunState
from t2r.infra.llm.openai_client import AssistantTurn, ToolCall
from t2r.infra.llm.prompt_loader import PromptLoader

TABLES = [
    {
        "id": "t1",
        "database": "cdm",
        "table_name": "events",
        "title": "События",
        "grain": "1 строка = событие",
        "domain": "web",
        "total_rows": 1000,
    }
]
COLUMNS = [
    {
        "id": "c1",
        "name": "user_id",
        "position": 1,
        "data_type": "UInt64",
        "description": "идентификатор пользователя",
        "semantic_role": "id",
        "null_ratio": 0.0,
        "distinct_count": 500,
        "examples": [1, 2, 3],
        "is_in_primary_key": True,
        "is_in_sorting_key": True,
        "is_in_partition_key": False,
        "value_catalog": None,
        "value_range": None,
        "semantics": {"pii": False},
    }
]


def _turn(*calls: ToolCall, content: str | None = None) -> AssistantTurn:
    return AssistantTurn(content=content, tool_calls=list(calls))


def _call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=args)


class FakeLLM:
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.calls = 0
        self.seen: list[list[dict]] = []

    async def complete_with_tools(self, messages, tools, **kw):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        return self._turns.pop(0)


def _fake_ch_client(columns, rows):
    client = SimpleNamespace()
    client.query = AsyncMock(
        return_value=SimpleNamespace(column_names=columns, result_rows=rows)
    )
    client.close = AsyncMock()
    return client


def _make_deps(llm, *, ch_client=None) -> ClientAgentDeps:
    semantic_repo = SimpleNamespace(
        list_tables=AsyncMock(return_value=TABLES),
        get_columns=AsyncMock(return_value=COLUMNS),
        get_relations=AsyncMock(return_value=[]),
        list_glossary=AsyncMock(return_value=[]),
        list_metrics=AsyncMock(return_value=[]),
    )
    ch_factory = SimpleNamespace(
        for_source=AsyncMock(return_value=ch_client or _fake_ch_client(["x"], [[1]]))
    )
    # The loop loads the source glossary via a raw `session.execute(...).first()`.
    # SQLAlchemy's async `execute` is awaited but `Result.first()` is sync — model
    # that (a bare AsyncMock would make `.first()` return a coroutine).
    session = AsyncMock()
    _glossary_result = MagicMock()
    _glossary_result.first = MagicMock(return_value=None)  # no glossary configured
    session.execute = AsyncMock(return_value=_glossary_result)
    return ClientAgentDeps(
        ch_factory=ch_factory,
        semantic_repo=semantic_repo,
        notes_repo=SimpleNamespace(search=AsyncMock(return_value=[])),
        graph_repo=SimpleNamespace(
            neighbors=AsyncMock(
                return_value=[{"id": "t2", "name": "schools", "database": "dict"}]
            )
        ),
        session=session,
        llm=llm,
        embeddings=SimpleNamespace(embed=AsyncMock(return_value=[0.0] * 8)),
        prompts=PromptLoader(),
        export_dir="/tmp/t2r-test-exports",
        ch_max_execution_time=30,
        ch_default_limit=1000,
    )


async def _collect(run: AgentRun) -> list:
    return [ev async for ev in run.bus.subscribe(0)]


# ── full loop ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_explore_run_sql_finish():
    ch = _fake_ch_client(["count()"], [[1000]])
    llm = FakeLLM(
        [
            _turn(_call("list_tables")),
            _turn(_call("run_sql", sql="SELECT count() FROM cdm.events")),
            _turn(_call("finish", summary="Всего 1000 событий.", result_from="q1")),
        ]
    )
    deps = _make_deps(llm, ch_client=ch)
    task_id = uuid4()
    run = AgentRun("client_task")
    run.context.update(chat_history=[], prev_result=None)
    step = ReactAgentStep(deps, source_id=uuid4(), task_id=task_id, prompt="сколько событий?")

    await Pipeline([step]).run(run)

    assert run.state == RunState.done
    assert llm.calls == 3
    # task_runs UPDATE persisted with the summary
    # Two executes: the glossary lookup + the final task_runs UPDATE.
    assert deps.session.execute.await_count == 2
    assert deps.session.commit.await_count == 1
    events = await _collect(run)
    finals = [e for e in events if e.kind == "result.final"]
    assert len(finals) == 1
    assert finals[0].payload["summary"] == "Всего 1000 событий."
    assert finals[0].payload["sql"] == "SELECT count() FROM cdm.events"
    assert finals[0].payload["preview"]["rows"] == [[1000]]
    # tool calls surfaced as timeline steps
    assert any(e.kind == "step.started" for e in events)


@pytest.mark.asyncio
async def test_loop_plain_text_answer_is_finish():
    llm = FakeLLM([_turn(content="Это справочная таблица событий.")])
    deps = _make_deps(llm)
    run = AgentRun("client_task")
    run.context.update(chat_history=[], prev_result=None)
    step = ReactAgentStep(deps, source_id=uuid4(), task_id=uuid4(), prompt="что это?")

    await Pipeline([step]).run(run)

    assert run.state == RunState.done
    events = await _collect(run)
    finals = [e for e in events if e.kind == "result.final"]
    assert finals[0].payload["summary"] == "Это справочная таблица событий."
    assert finals[0].payload["sql"] is None


@pytest.mark.asyncio
async def test_loop_ask_user_pauses_and_resumes():
    llm = FakeLLM(
        [
            _turn(_call("ask_user", question="какой период?")),
            _turn(_call("finish", summary="За май 2026.")),
        ]
    )
    deps = _make_deps(llm)
    run = AgentRun("client_task")
    run.context.update(chat_history=[], prev_result=None)
    step = ReactAgentStep(deps, source_id=uuid4(), task_id=uuid4(), prompt="посчитай")

    task = asyncio.create_task(Pipeline([step]).run(run))
    # wait until the loop parks on the question
    for _ in range(200):
        if run.state == RunState.awaiting_input:
            break
        await asyncio.sleep(0.005)
    assert run.state == RunState.awaiting_input
    assert await run.respond("май 2026")
    await asyncio.wait_for(task, timeout=2)

    assert run.state == RunState.done
    events = await _collect(run)
    assert any(e.kind == "awaiting_input" for e in events)


@pytest.mark.asyncio
async def test_session_thread_is_replayed_and_persisted(monkeypatch):
    import t2r.agents.client_agent.loop as loop_mod

    prior = [
        {"role": "user", "content": "сколько учителей?"},
        {"role": "assistant", "content": "227489 учителей."},
    ]
    captured: dict = {}

    class FakeThreadRepo:
        def __init__(self, session):
            pass

        async def load(self, sid):
            return list(prior)

        async def append(self, sid, msgs):
            captured["msgs"] = msgs

    monkeypatch.setattr(loop_mod, "AgentThreadRepo", FakeThreadRepo)

    llm = FakeLLM([_turn(_call("finish", summary="А по школам — 42091."))])
    deps = _make_deps(llm)
    sid = uuid4()
    step = ReactAgentStep(
        deps, source_id=uuid4(), task_id=uuid4(), prompt="а школ?", session_id=sid
    )
    run = AgentRun("client_task")
    run.context.update(chat_history=[], prev_result=None)

    await Pipeline([step]).run(run)

    # The prior thread was replayed into the LLM context...
    sent = llm.seen[0]
    assert {"role": "user", "content": "сколько учителей?"} in sent
    assert {"role": "assistant", "content": "227489 учителей."} in sent
    assert sent[-1] == {"role": "user", "content": "а школ?"}
    # ...and this turn (new user msg + final answer) was persisted.
    roles = [m["role"] for m in captured["msgs"]]
    assert roles[0] == "user" and roles[-1] == "assistant"
    assert captured["msgs"][-1]["content"] == "А по школам — 42091."


# ── context trimming ────────────────────────────────────────────────────────


def _step():
    return ReactAgentStep(
        _make_deps(FakeLLM([])), source_id=uuid4(), task_id=uuid4(), prompt="x"
    )


def test_trim_thread_keeps_recent_turns_and_user_boundary():
    thread = []
    for i in range(12):
        thread.append({"role": "user", "content": f"q{i}"})
        thread.append({"role": "assistant", "content": f"a{i}"})
    trimmed = _step()._trim_thread(thread)
    assert len([m for m in trimmed if m["role"] == "user"]) <= 8
    assert trimmed[0]["role"] == "user"  # never orphan a tool message
    assert trimmed[-1]["content"] == "a11"  # most recent kept


def test_trim_thread_respects_char_budget():
    big = "x" * 30_000
    thread = []
    for i in range(6):
        thread.append({"role": "user", "content": f"q{i}"})
        thread.append({"role": "tool", "content": big, "tool_call_id": "c"})
    trimmed = _step()._trim_thread(thread)
    # 48k budget vs ~30k/turn ⇒ at most 2 turns survive
    assert len([m for m in trimmed if m["role"] == "user"]) <= 2
    assert trimmed[0]["role"] == "user"


# ── individual tools ────────────────────────────────────────────────────────


def _ctx(deps) -> ToolContext:
    return ToolContext(deps=deps, source_id=uuid4(), run=AgentRun("x"), tables=TABLES)


@pytest.mark.asyncio
async def test_run_sql_guard_rejects_unknown_table():
    deps = _make_deps(FakeLLM([]))
    reg = build_registry()
    res = await reg["run_sql"].handler(_ctx(deps), {"sql": "SELECT * FROM secret.t"})
    assert res["kind"] == "guard"
    assert "error" in res


@pytest.mark.asyncio
async def test_run_sql_stores_result_and_returns_query_id():
    ch = _fake_ch_client(["n"], [[5], [6]])
    deps = _make_deps(FakeLLM([]), ch_client=ch)
    ctx = _ctx(deps)
    res = await reg_run_sql(ctx, "SELECT n FROM cdm.events")
    assert res["query_id"] == "q1"
    assert res["rowcount"] == 2
    assert ctx.results["q1"].rows == [[5], [6]]


async def reg_run_sql(ctx, sql):
    return await build_registry()["run_sql"].handler(ctx, {"sql": sql})


@pytest.mark.asyncio
async def test_get_table_rejects_table_outside_whitelist():
    deps = _make_deps(FakeLLM([]))
    res = await build_registry()["get_table"].handler(_ctx(deps), {"qname": "x.y"})
    assert "error" in res


@pytest.mark.asyncio
async def test_get_table_renders_known_table():
    deps = _make_deps(FakeLLM([]))
    res = await build_registry()["get_table"].handler(_ctx(deps), {"qname": "cdm.events"})
    assert res["n_columns"] == 1
    assert "user_id" in res["schema"]


@pytest.mark.asyncio
async def test_related_tables_uses_neo4j_neighbors():
    deps = _make_deps(FakeLLM([]))
    ctx = ToolContext(
        deps=deps,
        source_id=uuid4(),
        run=AgentRun("x"),
        tables=TABLES + [{"id": "t2", "database": "dict", "table_name": "schools"}],
    )
    res = await build_registry()["related_tables"].handler(ctx, {"qname": "cdm.events"})
    deps.graph_repo.neighbors.assert_awaited_once()
    assert res["related"] == ["dict.schools"]
