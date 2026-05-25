"""Integration test fixtures.

Spins up a real Postgres via testcontainers, runs the project's SQL migrations,
patches `t2r.settings.get_settings`, and builds a FastAPI test app whose Dishka
container points at the spawned database.

Skips integration tests when Docker / testcontainers is unavailable, so the
unit-only suite still passes on a bare machine.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

# Disable Ryuk reaper — on macOS / Colima its port mapping detection is flaky.
# Containers are still cleaned up by the regular `with`-context.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    PostgresContainer = None  # type: ignore[assignment]

import asyncpg
import httpx

MIGRATIONS_DIR = Path(__file__).resolve().parents[2].parent / "migrations"
PGVECTOR_IMAGE = "pgvector/pgvector:pg16"


def _docker_available() -> bool:
    if PostgresContainer is None:
        return False
    try:
        import docker  # type: ignore[import-not-found]

        docker.from_env().ping()
        return True
    except Exception:
        return False


def _apply_migrations_sync(dsn: str) -> None:
    async def _run():
        conn = await asyncpg.connect(dsn)
        try:
            for path in sorted(MIGRATIONS_DIR.iterdir()):
                if not path.name.endswith(".sql"):
                    continue
                m = re.match(r"^(\d+)_(.+)\.sql$", path.name)
                if not m:
                    continue
                sql = path.read_text(encoding="utf-8")
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                        int(m.group(1)),
                        m.group(2),
                    )
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    if not _docker_available():
        pytest.skip("docker/testcontainers not available")
    with PostgresContainer(PGVECTOR_IMAGE, username="t2r", password="t2r", dbname="t2r") as pg:
        # asyncpg-compatible DSN (no driver suffix)
        raw_dsn = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        async_dsn = raw_dsn.replace("postgresql://", "postgresql+asyncpg://")
        _apply_migrations_sync(raw_dsn)
        os.environ["T2R_PG_DSN"] = async_dsn
        yield async_dsn


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(pg_dsn: str) -> AsyncIterator[None]:
    """Truncate domain tables before each integration test."""
    raw = pg_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw)
    try:
        await conn.execute(
            "TRUNCATE audit_log, llm_calls, task_runs, chat_messages, chat_sessions,"
            " client_sessions_meta, md_notes, sem_revisions, sem_relations,"
            " sem_columns, sem_tables, sem_metrics, sem_glossary,"
            " profiling_run_tables, profiling_runs, source_table_selections,"
            " data_sources RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()
    yield


@pytest_asyncio.fixture
async def app_client(pg_dsn: str) -> AsyncIterator[httpx.AsyncClient]:
    """Build a fresh FastAPI app pointing at the test database."""
    # Force settings rebuild and re-init container per test for isolation.
    from t2r.settings import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]

    from t2r.main import create_app

    app = create_app()
    # FastAPI lifespan handles Dishka init/teardown.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        async with app.router.lifespan_context(app):
            yield client
