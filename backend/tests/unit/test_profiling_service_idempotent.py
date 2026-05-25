"""ProfilingService.start() idempotency — exercised purely with mocks.

We swap the repos and session maker so the test exercises the three branches:
1. active run + live AgentRun in registry → reused.
2. active run + worker gone → row marked abandoned, fresh run created.
3. no active run → fresh run created normally.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from t2r.agents.orchestrator.registry import RunRegistry
from t2r.services.profiling_service import ProfilingService


class _FakeSession:
    async def commit(self) -> None:  # pragma: no cover - trivial
        return None

    async def rollback(self) -> None:  # pragma: no cover - trivial
        return None

    async def execute(self, *args, **kwargs):  # pragma: no cover - not exercised
        raise AssertionError("repo methods should be mocked")


def _sm_factory():
    @asynccontextmanager
    async def _ctx():
        yield _FakeSession()

    return _ctx


def _make_service(*, selection_rows, active_sequence):
    """Build ProfilingService with monkey-patched repos.

    selection_rows: what SelectionRepoPg.get returns
    active_sequence: list of return values for sequential ProfilingRepoPg.get_active calls
    """
    sm = _sm_factory()
    registry = RunRegistry()
    svc = ProfilingService(
        sessionmaker=sm,  # type: ignore[arg-type]
        cipher=MagicMock(),
        neo4j_driver=MagicMock(),
        llm=MagicMock(),
        embeddings=MagicMock(),
        prompts=MagicMock(),
        registry=registry,
    )
    return svc, registry, active_sequence, selection_rows


@pytest.mark.asyncio
async def test_reuses_active_run_when_worker_is_alive() -> None:
    """Existing active row + AgentRun still in registry → caller gets same IDs back."""
    from t2r.agents.orchestrator.run import AgentRun

    sid = uuid4()
    db_run_id = uuid4()
    pre_existing = AgentRun(kind="profiling")

    svc, registry, _, _ = _make_service(
        selection_rows=[{"database": "d", "table_name": "t"}],
        active_sequence=[
            {"id": db_run_id, "status": "running", "params": {"agent_run_id": pre_existing.id}},
        ],
    )
    await registry.add(pre_existing)

    sel = AsyncMock(return_value=[{"database": "d", "table_name": "t"}])
    get_active = AsyncMock(return_value={
        "id": db_run_id,
        "status": "running",
        "params": {"agent_run_id": pre_existing.id},
    })

    with patch(
        "t2r.services.profiling_service.SelectionRepoPg"
    ) as SelRepo, patch(
        "t2r.services.profiling_service.ProfilingRepoPg"
    ) as ProfRepo, patch(
        "t2r.services.profiling_service.SourceRepoPg"
    ) as SourceRepo:
        SelRepo.return_value.get = sel
        ProfRepo.return_value.get_active = get_active
        SourceRepo.return_value.sync_profiling_status_from_runs = AsyncMock()

        run_id, agent_id, reused = await svc.start(sid, requested_by="admin")

    assert reused is True
    assert run_id == db_run_id
    assert agent_id == pre_existing.id


@pytest.mark.asyncio
async def test_releases_abandoned_active_then_starts_fresh() -> None:
    """Active row but worker is gone (e.g. crashed backend) → free slot, new run."""
    sid = uuid4()
    abandoned_id = uuid4()
    fresh_id = uuid4()

    svc, _, _, _ = _make_service(
        selection_rows=[{"database": "d", "table_name": "t"}],
        # First call: there's an active row.
        # We don't make a second get_active call in this branch — the code
        # proceeds to create_run after marking abandoned.
        active_sequence=[
            {"id": abandoned_id, "status": "running", "params": {"agent_run_id": "ghost"}},
        ],
    )

    mark_abandoned = AsyncMock()
    create_run = AsyncMock(return_value=fresh_id)
    sync = AsyncMock()
    set_status = AsyncMock()

    with patch(
        "t2r.services.profiling_service.SelectionRepoPg"
    ) as SelRepo, patch(
        "t2r.services.profiling_service.ProfilingRepoPg"
    ) as ProfRepo, patch(
        "t2r.services.profiling_service.SourceRepoPg"
    ) as SourceRepo, patch(
        "t2r.services.profiling_service.asyncio.create_task"
    ) as create_task, patch.object(
        ProfilingService, "_run_pipeline", new=AsyncMock()
    ):
        SelRepo.return_value.get = AsyncMock(
            return_value=[{"database": "d", "table_name": "t"}]
        )
        ProfRepo.return_value.get_active = AsyncMock(
            return_value={"id": abandoned_id, "status": "running", "params": {"agent_run_id": "ghost"}}
        )
        ProfRepo.return_value.mark_abandoned = mark_abandoned
        ProfRepo.return_value.create_run = create_run
        SourceRepo.return_value.sync_profiling_status_from_runs = sync
        SourceRepo.return_value.set_profiling_status = set_status
        # Don't actually spawn the pipeline coroutine.
        create_task.return_value = MagicMock()

        run_id, agent_id, reused = await svc.start(sid, requested_by="admin")

    assert reused is False
    assert run_id == fresh_id
    mark_abandoned.assert_awaited_once()
    create_run.assert_awaited_once()
    set_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_active_creates_new_run() -> None:
    sid = uuid4()
    fresh_id = uuid4()

    svc, _, _, _ = _make_service(
        selection_rows=[{"database": "d", "table_name": "t"}],
        active_sequence=[None],
    )

    create_run = AsyncMock(return_value=fresh_id)
    set_status = AsyncMock()

    with patch(
        "t2r.services.profiling_service.SelectionRepoPg"
    ) as SelRepo, patch(
        "t2r.services.profiling_service.ProfilingRepoPg"
    ) as ProfRepo, patch(
        "t2r.services.profiling_service.SourceRepoPg"
    ) as SourceRepo, patch(
        "t2r.services.profiling_service.asyncio.create_task"
    ) as create_task, patch.object(
        ProfilingService, "_run_pipeline", new=AsyncMock()
    ):
        SelRepo.return_value.get = AsyncMock(
            return_value=[{"database": "d", "table_name": "t"}]
        )
        ProfRepo.return_value.get_active = AsyncMock(return_value=None)
        ProfRepo.return_value.create_run = create_run
        SourceRepo.return_value.set_profiling_status = set_status
        create_task.return_value = MagicMock()

        run_id, agent_id, reused = await svc.start(sid, requested_by="admin")

    assert reused is False
    assert run_id == fresh_id
    create_run.assert_awaited_once()
    set_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_refuses_without_selection() -> None:
    from t2r.errors import ValidationError

    sid = uuid4()
    svc, _, _, _ = _make_service(selection_rows=[], active_sequence=[])

    with patch("t2r.services.profiling_service.SelectionRepoPg") as SelRepo:
        SelRepo.return_value.get = AsyncMock(return_value=[])
        with pytest.raises(ValidationError):
            await svc.start(sid, requested_by="admin")
