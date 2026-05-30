from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ACTIVE_STATUSES: tuple[str, ...] = ("pending", "running", "awaiting_input", "paused")

# Statuses we abandon on backend restart. 'paused' (column-selection gate) AND
# 'awaiting_input' (waiting on a human answer) are deliberately excluded: both
# have all prior work persisted in profiling_tasks and resume off the DB
# (apply_column_selection / answer_question work without the in-memory run), so
# abandoning them would force a needless full re-profile. 'pending'/'running' had
# a live in-memory worker that's now gone, so they are abandoned.
ABANDON_ON_RESTART: tuple[str, ...] = ("pending", "running")


class ProfilingRepoPg:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active(self, source_id: UUID) -> dict | None:
        """Return the single active run for the source, or None.

        The DB enforces single-active via uniq_active_profiling_per_source.
        """
        row = (
            await self.session.execute(
                text(
                    "SELECT id, source_id, status, requested_by, params, started_at,"
                    " finished_at, error, stats, created_at"
                    " FROM profiling_runs"
                    " WHERE source_id = :sid AND status = ANY(:statuses)"
                    " ORDER BY started_at DESC LIMIT 1"
                ),
                {"sid": source_id, "statuses": list(ACTIVE_STATUSES)},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def mark_abandoned(self, run_id: UUID, *, reason: str = "abandoned") -> None:
        await self.session.execute(
            text(
                "UPDATE profiling_runs"
                " SET status = 'failed', error = :err, finished_at = now()"
                " WHERE id = :id AND status = ANY(:statuses)"
            ),
            {"id": run_id, "err": reason, "statuses": list(ACTIVE_STATUSES)},
        )

    async def mark_all_active_abandoned(self, *, reason: str = "abandoned_on_restart") -> list[UUID]:
        rows = (
            await self.session.execute(
                text(
                    "UPDATE profiling_runs"
                    " SET status = 'failed', error = :err, finished_at = now()"
                    " WHERE status = ANY(:statuses)"
                    " RETURNING id, source_id"
                ),
                {"err": reason, "statuses": list(ABANDON_ON_RESTART)},
            )
        ).mappings().all()
        return [r["source_id"] for r in rows]

    async def try_begin_from_paused(self, run_id: UUID) -> UUID | None:
        """Atomically claim a paused run for resume (paused → running).

        Returns the source_id on success, or None if the run was no longer
        paused — which is how we reject a double-submit of the column-selection
        gate (only one caller wins the transition; the rest get None)."""
        row = (
            await self.session.execute(
                text(
                    "UPDATE profiling_runs SET status = 'running'"
                    " WHERE id = :id AND status = 'paused' RETURNING source_id"
                ),
                {"id": run_id},
            )
        ).first()
        return row[0] if row else None

    async def mark_awaiting_input(self, run_id: UUID) -> None:
        """Park a run on a human question — guarded so a stray late drain can't
        resurrect an already-finalized run back to an (active, restart-surviving)
        'awaiting_input' state and wedge the source."""
        await self.session.execute(
            text(
                "UPDATE profiling_runs SET status = 'awaiting_input'"
                " WHERE id = :id AND status NOT IN ('done', 'failed', 'cancelled')"
            ),
            {"id": run_id},
        )

    async def create_run(
        self, source_id: UUID, *, requested_by: str | None, params: dict
    ) -> UUID:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO profiling_runs (source_id, status, requested_by, params, started_at)"
                    " VALUES (:sid, 'pending', :by, CAST(:params AS jsonb), now()) RETURNING id"
                ),
                {"sid": source_id, "by": requested_by, "params": json.dumps(params)},
            )
        ).first()
        assert row is not None
        return row[0]

    async def set_status(self, run_id: UUID, status: str, *, error: str | None = None) -> None:
        await self.session.execute(
            text(
                "UPDATE profiling_runs SET status = :st, error = :err,"
                " finished_at = CASE WHEN :done THEN now() ELSE finished_at END"
                " WHERE id = :id"
            ),
            {
                "id": run_id,
                "st": status,
                "err": error,
                "done": status in ("done", "failed", "cancelled"),
            },
        )

    async def set_stats(self, run_id: UUID, stats: dict) -> None:
        await self.session.execute(
            text("UPDATE profiling_runs SET stats = CAST(:s AS jsonb) WHERE id = :id"),
            {"id": run_id, "s": json.dumps(stats)},
        )

    async def upsert_table(
        self,
        run_id: UUID,
        database: str,
        table: str,
        *,
        status: str,
        ddl: str | None = None,
        sample: dict | None = None,
        column_stats: dict | None = None,
        usage_stats: dict | None = None,
        error: str | None = None,
    ) -> UUID:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO profiling_run_tables"
                    " (run_id, database, table_name, status, ddl, sample, column_stats,"
                    "  usage_stats, error, started_at)"
                    " VALUES (:rid, :db, :tbl, :st, :ddl,"
                    " CAST(:sample AS jsonb), CAST(:cs AS jsonb), CAST(:us AS jsonb), :err, now())"
                    " ON CONFLICT (run_id, database, table_name) DO UPDATE"
                    " SET status = EXCLUDED.status,"
                    "     ddl = COALESCE(EXCLUDED.ddl, profiling_run_tables.ddl),"
                    "     sample = COALESCE(EXCLUDED.sample, profiling_run_tables.sample),"
                    "     column_stats = COALESCE(EXCLUDED.column_stats, profiling_run_tables.column_stats),"
                    "     usage_stats = COALESCE(EXCLUDED.usage_stats, profiling_run_tables.usage_stats),"
                    "     error = COALESCE(EXCLUDED.error, profiling_run_tables.error),"
                    "     finished_at = CASE WHEN EXCLUDED.status IN ('done','failed','skipped') THEN now() ELSE profiling_run_tables.finished_at END"
                    " RETURNING id"
                ),
                {
                    "rid": run_id,
                    "db": database,
                    "tbl": table,
                    "st": status,
                    "ddl": ddl,
                    "sample": json.dumps(sample, default=str) if sample is not None else None,
                    "cs": json.dumps(column_stats, default=str) if column_stats is not None else None,
                    "us": json.dumps(usage_stats, default=str) if usage_stats is not None else None,
                    "err": error,
                },
            )
        ).first()
        assert row is not None
        return row[0]

    async def get_run(self, run_id: UUID) -> dict | None:
        row = (
            await self.session.execute(
                text(
                    "SELECT id, source_id, status, requested_by, params, started_at,"
                    " finished_at, error, stats, created_at FROM profiling_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list_runs(self, source_id: UUID) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, status, started_at, finished_at, error, stats FROM profiling_runs"
                    " WHERE source_id = :sid ORDER BY started_at DESC LIMIT 50"
                ),
                {"sid": source_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def get_run_tables(self, run_id: UUID) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT database, table_name, status, error, started_at, finished_at"
                    " FROM profiling_run_tables WHERE run_id = :rid"
                    " ORDER BY database, table_name"
                ),
                {"rid": run_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]
