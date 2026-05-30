from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TERMINAL_STATUSES: tuple[str, ...] = ("done", "skipped")
ACTIVE_STATUSES: tuple[str, ...] = ("running",)

_COLS = (
    "id, run_id, source_id, kind, target, database, table_name, columns, status,"
    " attempts, input_fingerprint, depends_on, payload, result, error,"
    " started_at, finished_at, created_at, updated_at"
)


def _row(r: Any) -> dict[str, Any]:
    return dict(r)


class ProfilingTaskRepo:
    """Durable task state for the two-pass profiling pipeline.

    All state transitions go through here so the run is fully resumable and no
    column is ever silently dropped (see ``coverage``).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        run_id: UUID,
        source_id: UUID,
        kind: str,
        target: str,
        database: str | None = None,
        table_name: str | None = None,
        columns: list[str] | None = None,
        depends_on: list[UUID] | None = None,
        payload: dict[str, Any] | None = None,
        input_fingerprint: str | None = None,
    ) -> UUID:
        """Idempotent enqueue: one task per (run_id, kind, target).

        Returns the existing task id on conflict instead of duplicating work.
        """
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO profiling_tasks"
                    " (run_id, source_id, kind, target, database, table_name,"
                    "  columns, depends_on, payload, input_fingerprint)"
                    " VALUES (:rid, :sid, :kind, :target, :db, :tbl,"
                    "  :cols, :deps, CAST(:payload AS jsonb), :fp)"
                    " ON CONFLICT (run_id, kind, target) DO NOTHING"
                    " RETURNING id"
                ),
                {
                    "rid": run_id,
                    "sid": source_id,
                    "kind": kind,
                    "target": target,
                    "db": database,
                    "tbl": table_name,
                    "cols": columns or [],
                    "deps": depends_on or [],
                    "payload": json.dumps(payload) if payload is not None else None,
                    "fp": input_fingerprint,
                },
            )
        ).first()
        if row:
            return row[0]
        existing = (
            await self.session.execute(
                text(
                    "SELECT id FROM profiling_tasks"
                    " WHERE run_id = :rid AND kind = :kind AND target = :target"
                ),
                {"rid": run_id, "kind": kind, "target": target},
            )
        ).first()
        assert existing is not None
        return existing[0]

    async def get(self, task_id: UUID) -> dict[str, Any] | None:
        row = (
            await self.session.execute(
                text(f"SELECT {_COLS} FROM profiling_tasks WHERE id = :id"),
                {"id": task_id},
            )
        ).mappings().first()
        return _row(row) if row else None

    async def list_by_run(
        self, run_id: UUID, *, kind: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        conds = ["run_id = :rid"]
        params: dict[str, Any] = {"rid": run_id}
        if kind is not None:
            conds.append("kind = :kind")
            params["kind"] = kind
        if status is not None:
            conds.append("status = :status")
            params["status"] = status
        rows = (
            await self.session.execute(
                text(
                    f"SELECT {_COLS} FROM profiling_tasks"
                    f" WHERE {' AND '.join(conds)} ORDER BY created_at"
                ),
                params,
            )
        ).mappings().all()
        return [_row(r) for r in rows]

    async def claim_next(self, run_id: UUID) -> dict[str, Any] | None:
        """Atomically grab one runnable task and mark it running.

        A task is runnable when it's ``pending`` and every dependency is in a
        terminal-success state. ``FOR UPDATE SKIP LOCKED`` lets multiple workers
        pull different tasks concurrently without stepping on each other.

        ``attempts`` is NOT bumped here — it counts genuine transient failures
        (bumped only on the failure-retry path), so it isn't eroded by benign
        re-claims (question rounds, restarts) that would otherwise exhaust the
        retry budget before the task ever truly failed.
        """
        row = (
            await self.session.execute(
                text(
                    "WITH ready AS ("
                    "  SELECT t.id FROM profiling_tasks t"
                    "  WHERE t.run_id = :rid AND t.status = 'pending'"
                    "    AND NOT EXISTS ("
                    "      SELECT 1 FROM unnest(t.depends_on) AS dep_id"
                    "      JOIN profiling_tasks d ON d.id = dep_id"
                    "      WHERE d.status NOT IN ('done', 'skipped')"
                    "    )"
                    "  ORDER BY t.created_at"
                    "  FOR UPDATE SKIP LOCKED"
                    "  LIMIT 1"
                    ")"
                    " UPDATE profiling_tasks p"
                    " SET status = 'running',"
                    "     started_at = now(), updated_at = now()"
                    " FROM ready WHERE p.id = ready.id"
                    f" RETURNING {', '.join('p.' + c.strip() for c in _COLS.split(','))}"
                ),
                {"rid": run_id},
            )
        ).mappings().first()
        return _row(row) if row else None

    async def set_status(
        self,
        task_id: UUID,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        payload: dict[str, Any] | None = None,
        bump_attempts: bool = False,
    ) -> None:
        await self.session.execute(
            text(
                "UPDATE profiling_tasks SET status = :st,"
                "  result = COALESCE(CAST(:result AS jsonb), result),"
                "  payload = COALESCE(CAST(:payload AS jsonb), payload),"
                "  error = :err,"
                "  attempts = attempts + CASE WHEN :bump THEN 1 ELSE 0 END,"
                "  finished_at = CASE WHEN :terminal THEN now() ELSE finished_at END,"
                "  updated_at = now()"
                " WHERE id = :id"
            ),
            {
                "id": task_id,
                "st": status,
                "result": json.dumps(result) if result is not None else None,
                "payload": json.dumps(payload) if payload is not None else None,
                "err": error,
                "bump": bump_attempts,
                "terminal": status in ("done", "failed", "skipped"),
            },
        )

    async def reset_running(self, run_id: UUID) -> int:
        """Resume: put orphaned ``running`` tasks back to ``pending``.

        On a restart the in-memory workers are gone, so anything left running
        must be retried. ``awaiting_input`` is preserved — those wait on a human.
        """
        rows = (
            await self.session.execute(
                text(
                    "UPDATE profiling_tasks SET status = 'pending', updated_at = now()"
                    " WHERE run_id = :rid AND status = 'running' RETURNING id"
                ),
                {"rid": run_id},
            )
        ).all()
        return len(rows)

    async def board(self, run_id: UUID) -> list[dict[str, Any]]:
        """Compact task list for the live run board (no heavy payload/result)."""
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, kind, target, database, table_name, columns, status,"
                    " attempts, error, started_at, finished_at, created_at"
                    " FROM profiling_tasks WHERE run_id = :rid"
                    " ORDER BY created_at, target"
                ),
                {"rid": run_id},
            )
        ).mappings().all()
        return [_row(r) for r in rows]

    async def counts(self, run_id: UUID) -> dict[str, int]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT status, count(*) AS n FROM profiling_tasks"
                    " WHERE run_id = :rid GROUP BY status"
                ),
                {"rid": run_id},
            )
        ).all()
        return {r[0]: int(r[1]) for r in rows}

    async def coverage(self, run_id: UUID) -> dict[str, Any]:
        """The "don't lose a column" invariant.

        Expected columns come from completed ``harvest_table`` tasks (which list
        every discovered column). Covered columns come from terminal
        ``describe_group`` tasks. Anything expected-but-not-covered is reported
        as ``missing`` — while it's non-empty the run is NOT fully indexed.
        """
        harvested = (
            await self.session.execute(
                text(
                    "SELECT database, table_name, result FROM profiling_tasks"
                    " WHERE run_id = :rid AND kind = 'harvest_table' AND status = 'done'"
                ),
                {"rid": run_id},
            )
        ).mappings().all()
        expected: set[tuple[str, str, str]] = set()
        for h in harvested:
            res = h["result"] or {}
            cols = res.get("columns") if isinstance(res, dict) else None
            for c in cols or []:
                expected.add((h["database"], h["table_name"], str(c)))

        # Whether ANY column was harvested at all (before the disable subtraction).
        # Distinguishes a legitimately all-disabled run (complete) from an empty /
        # inaccessible source that harvested nothing (must stay incomplete).
        harvested_any = len(expected) > 0

        # Columns the admin disabled are intentionally excluded from describe_group,
        # so they are never "covered". They stay harvested in pass-1 (the catalog
        # must remain complete for cheap re-enable), so without subtracting them
        # here they'd surface as "missing" and falsely fail an otherwise-complete
        # run. Disabling a column is a deliberate decision — don't require a description.
        disabled_rows = (
            await self.session.execute(
                text(
                    "SELECT t.database, t.table_name, c.name"
                    " FROM sem_columns c"
                    " JOIN sem_tables t ON t.id = c.table_id"
                    " JOIN profiling_runs r ON r.source_id = t.source_id"
                    " WHERE r.id = :rid AND c.enabled = false"
                ),
                {"rid": run_id},
            )
        ).all()
        expected -= {(r[0], r[1], str(r[2])) for r in disabled_rows}

        # Covered = columns a terminal describe_group task claims (task bookkeeping)
        # UNION columns that actually carry a description in the persisted layer
        # (or are curated-locked). The persisted-layer half credits columns whose
        # group task 'failed' on a single stubborn sibling — so `missing` lists
        # only the truly-undescribed ones instead of a lone bad column dragging its
        # already-described siblings down and failing the whole run. The task half
        # is now accurate too: the pass-2 guard only lets a describe_group reach
        # 'done' once all its enabled, non-locked columns have descriptions.
        covered: set[tuple[str, str, str]] = set()
        task_rows = (
            await self.session.execute(
                text(
                    "SELECT database, table_name, columns FROM profiling_tasks"
                    " WHERE run_id = :rid AND kind = 'describe_group'"
                    " AND status IN ('done', 'skipped')"
                ),
                {"rid": run_id},
            )
        ).mappings().all()
        for c in task_rows:
            for col in c["columns"] or []:
                covered.add((c["database"], c["table_name"], str(col)))
        desc_rows = (
            await self.session.execute(
                text(
                    "SELECT t.database, t.table_name, c.name"
                    " FROM sem_columns c"
                    " JOIN sem_tables t ON t.id = c.table_id"
                    " JOIN profiling_runs r ON r.source_id = t.source_id"
                    " WHERE r.id = :rid AND c.enabled = true"
                    "   AND (c.locked = true"
                    "        OR (c.description IS NOT NULL"
                    "            AND length(btrim(c.description)) > 0))"
                ),
                {"rid": run_id},
            )
        ).all()
        covered |= {(r[0], r[1], str(r[2])) for r in desc_rows}

        missing = sorted(expected - covered)
        return {
            "expected": len(expected),
            "covered": len(expected & covered),
            "missing": [
                {"database": d, "table": t, "column": col} for (d, t, col) in missing
            ],
            # Complete = every still-expected column is covered AND something was
            # actually harvested. All-disabled (expected emptied above) is complete;
            # a no-harvest run is not.
            "complete": len(missing) == 0 and harvested_any,
        }
