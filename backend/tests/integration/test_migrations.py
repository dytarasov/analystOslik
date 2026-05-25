from __future__ import annotations

import pytest
import asyncpg


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_all_migrations_applied(pg_dsn: str) -> None:
    raw = pg_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw)
    try:
        rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
        versions = [r["version"] for r in rows]
        # The list keeps growing — assert it is consecutive starting from 1.
        assert versions == list(range(1, len(versions) + 1))
        assert versions[-1] >= 18, "migrations 0001..0018 must be applied"

        for tbl in (
            "data_sources",
            "profiling_runs",
            "profiling_run_tables",
            "sem_tables",
            "sem_columns",
            "sem_relations",
            "md_notes",
            "chat_sessions",
            "task_runs",
            "audit_log",
            "llm_calls",
            "source_table_selections",
        ):
            exists = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", f"public.{tbl}"
            )
            assert exists, f"missing table {tbl}"

        # New denormalized columns from migration 0012
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='data_sources'"
        )
        names = {r["column_name"] for r in cols}
        for c in ("profiling_status", "last_profiled_at", "last_profiling_run_id"):
            assert c in names, f"data_sources is missing column {c}"

        # Partial unique index protecting active profiling slots
        idx = await conn.fetchval(
            "SELECT indexname FROM pg_indexes WHERE indexname = 'uniq_active_profiling_per_source'"
        )
        assert idx == "uniq_active_profiling_per_source"

        # Partial unique index protecting single-active-task-per-session (0016)
        task_idx = await conn.fetchval(
            "SELECT indexname FROM pg_indexes WHERE indexname = 'uniq_active_task_per_session'"
        )
        assert task_idx == "uniq_active_task_per_session"

        # Semantic-layer enrichment columns from migration 0017
        sem_t_cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name='sem_tables'"
            )
        }
        for c in ("sorting_key", "partition_key", "primary_key", "total_rows", "grain"):
            assert c in sem_t_cols, f"sem_tables missing {c}"
        sem_c_cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name='sem_columns'"
            )
        }
        for c in ("value_catalog", "value_range", "is_in_primary_key"):
            assert c in sem_c_cols, f"sem_columns missing {c}"

        # Profiling task state manager from migration 0018
        assert await conn.fetchval(
            "SELECT to_regclass('public.profiling_tasks') IS NOT NULL"
        ), "missing table profiling_tasks"
        ptask_idx = await conn.fetchval(
            "SELECT indexname FROM pg_indexes WHERE indexname = 'uniq_profiling_task_unit'"
        )
        assert ptask_idx == "uniq_profiling_task_unit"

        ext = await conn.fetchval(
            "SELECT extname FROM pg_extension WHERE extname = 'vector'"
        )
        assert ext == "vector"
    finally:
        await conn.close()
