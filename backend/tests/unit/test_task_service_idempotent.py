"""TaskService.start_task idempotency — exercised with a fake session.

Covers the four branches added in the business-logic refactor:
1. active task + live AgentRun in registry  → reused (no second pipeline).
2. active task + worker gone (crash/restart) → row freed, fresh task created.
3. no active task                            → fresh task created normally.
4. unique-index race (IntegrityError)        → re-read and reuse the winner.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from t2r.agents.orchestrator.registry import RunRegistry
from t2r.agents.orchestrator.run import AgentRun
from t2r.services.task_service import TaskService


class _FakeResult:
    def __init__(self, row=None, rows=None) -> None:
        self._row = row
        self._rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self._row

    def all(self):
        return self._rows


class _FakeSession:
    """Routes execute() by SQL keyword. active_rows is a queue popped per SELECT."""

    def __init__(self, *, active_rows=None, insert_id=None, insert_raises=False) -> None:
        self.active_rows = list(active_rows or [])
        self.insert_id = insert_id
        self.insert_raises = insert_raises
        self.executed: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append(sql)
        if "INSERT INTO task_runs" in sql:
            if self.insert_raises:
                raise IntegrityError("insert", {}, Exception("dup active task"))
            return _FakeResult((self.insert_id,))
        if "SELECT id, agent_run_id FROM task_runs" in sql:
            row = self.active_rows.pop(0) if self.active_rows else None
            return _FakeResult(row)
        return _FakeResult(None)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _make_service(session: _FakeSession) -> tuple[TaskService, RunRegistry]:
    @asynccontextmanager
    async def _sm():
        yield session

    registry = RunRegistry()
    svc = TaskService(
        sessionmaker=_sm,  # type: ignore[arg-type]
        cipher=MagicMock(),
        neo4j_driver=MagicMock(),
        llm=MagicMock(),
        embeddings=MagicMock(),
        prompts=MagicMock(),
        registry=registry,
        settings=MagicMock(),
    )
    return svc, registry


@pytest.mark.asyncio
async def test_reuses_active_task_when_worker_alive() -> None:
    tid = uuid4()
    live = AgentRun(kind="client_task")
    session = _FakeSession(active_rows=[{"id": tid, "agent_run_id": live.id}])
    svc, registry = _make_service(session)
    await registry.add(live)

    with patch("t2r.services.task_service.asyncio.create_task") as create_task:
        out_tid, out_agent = await svc.start_task(
            session_id=uuid4(), source_id=uuid4(), prompt="hi"
        )

    assert out_tid == tid
    assert out_agent == live.id
    create_task.assert_not_called()
    # No INSERT was issued — we reused the existing run.
    assert not any("INSERT INTO task_runs" in s for s in session.executed)


@pytest.mark.asyncio
async def test_no_active_creates_fresh_task() -> None:
    new_id = uuid4()
    session = _FakeSession(active_rows=[], insert_id=new_id)
    svc, _ = _make_service(session)

    with patch(
        "t2r.services.task_service.asyncio.create_task"
    ) as create_task, patch.object(TaskService, "_run", new=MagicMock()):
        out_tid, out_agent = await svc.start_task(
            session_id=uuid4(), source_id=uuid4(), prompt="hi"
        )

    assert out_tid == new_id
    assert isinstance(out_agent, str) and out_agent
    create_task.assert_called_once()
    assert any("INSERT INTO task_runs" in s for s in session.executed)


@pytest.mark.asyncio
async def test_releases_abandoned_then_creates_fresh() -> None:
    new_id = uuid4()
    session = _FakeSession(
        # First SELECT finds an active row whose worker ("ghost") is not in
        # the registry, so the slot must be freed before the INSERT.
        active_rows=[{"id": uuid4(), "agent_run_id": "ghost"}],
        insert_id=new_id,
    )
    svc, _ = _make_service(session)

    with patch(
        "t2r.services.task_service.asyncio.create_task"
    ) as create_task, patch.object(TaskService, "_run", new=MagicMock()):
        out_tid, _ = await svc.start_task(
            session_id=uuid4(), source_id=uuid4(), prompt="hi"
        )

    assert out_tid == new_id
    create_task.assert_called_once()
    assert any("UPDATE task_runs SET status = 'failed'" in s for s in session.executed)


@pytest.mark.asyncio
async def test_integrity_race_reuses_winner() -> None:
    racer_tid = uuid4()
    racer_agent = uuid4().hex
    session = _FakeSession(
        # 1st SELECT (pre-check): nothing active. After INSERT raises, the
        # re-read SELECT finds the row a concurrent start() just created.
        active_rows=[None, {"id": racer_tid, "agent_run_id": racer_agent}],
        insert_raises=True,
    )
    svc, _ = _make_service(session)

    with patch("t2r.services.task_service.asyncio.create_task") as create_task:
        out_tid, out_agent = await svc.start_task(
            session_id=uuid4(), source_id=uuid4(), prompt="hi"
        )

    assert out_tid == racer_tid
    assert out_agent == racer_agent
    create_task.assert_not_called()


class _ContextSession:
    """Captures executed SQL so we can assert the prev-result query shape."""

    def __init__(self, *, session_id, prev_row=None) -> None:
        self.session_id = session_id
        self.prev_row = prev_row
        self.executed: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append(sql)
        if "SELECT session_id FROM task_runs" in sql:
            return _FakeResult(row=(self.session_id,))
        if "FROM chat_messages" in sql:
            return _FakeResult(rows=[])
        # prev_result query
        return _FakeResult(row=self.prev_row)


@pytest.mark.asyncio
async def test_prev_result_only_considers_turns_with_sql() -> None:
    """The shared-context fix: prev_result must skip followup turns (sql NULL).

    A followup is persisted as status='done' with result_sql NULL; without the
    `result_sql IS NOT NULL` filter the context chain breaks between two data
    queries. We assert the filter is present in the issued query.
    """
    session = _ContextSession(session_id=uuid4(), prev_row=None)

    @asynccontextmanager
    async def _sm():
        yield session

    svc = TaskService(
        sessionmaker=_sm,  # type: ignore[arg-type]
        cipher=MagicMock(),
        neo4j_driver=MagicMock(),
        llm=MagicMock(),
        embeddings=MagicMock(),
        prompts=MagicMock(),
        registry=RunRegistry(),
        settings=MagicMock(),
    )

    history, prev_result = await svc._load_session_context(uuid4())

    assert history == []
    assert prev_result is None
    prev_query = next(
        s for s in session.executed if "ORDER BY finished_at DESC" in s
    )
    assert "result_sql IS NOT NULL" in prev_query
