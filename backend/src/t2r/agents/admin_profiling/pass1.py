"""Pass 1 — the dry structural harvest, built on the durable task queue.

For each selected table a ``harvest_table`` task collects DDL, keys, stats,
value catalogs, ranges and deeper probes, then writes the *hard facts* into the
semantic layer (no LLM yet — descriptions come in pass 2). A single
``relations`` task, depending on all harvests, infers FKs deterministically and
verifies them by value overlap.

Everything runs through the bounded-concurrency scheduler and is checkpointed,
so the harvest is resumable and every column is accounted for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.agents.admin_profiling.pipeline import (
    CATALOG_BUDGET_PER_TABLE,
    CATALOG_MAX_DISTINCT,
    RELATION_MIN_OVERLAP,
    _cardinality,
    _deterministic_relation_candidates,
)
from t2r.agents.admin_profiling.scheduler import TaskResult, TaskScheduler
from t2r.infra.clickhouse.factory import CHClientFactory
from t2r.infra.clickhouse.profiler import (
    CHProfiler,
    is_numeric_type,
    is_string_type,
    is_temporal_type,
)
from t2r.infra.db.repos.profiling_task_repo_pg import ProfilingTaskRepo
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.security.cipher import FernetCipher
from t2r.logging import get_logger

logger = get_logger("profiling_pass1")


@dataclass
class Pass1Deps:
    sessionmaker: async_sessionmaker[AsyncSession]
    cipher: FernetCipher
    neo4j_driver: AsyncDriver


# ── pure heuristics (unit-tested) ──────────────────────────────────────────

_RX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RX_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RX_URL = re.compile(r"^https?://", re.IGNORECASE)


def detect_pattern(examples: list[Any] | None) -> str | None:
    """Best-effort value-shape detection from a few sample values."""
    vals = [str(v) for v in (examples or []) if v is not None][:8]
    if not vals:
        return None
    if all(_RX_EMAIL.match(v) for v in vals):
        return "email"
    if all(_RX_UUID.match(v) for v in vals):
        return "uuid"
    if all(_RX_URL.match(v) for v in vals):
        return "url"
    if all(v[:1] in "{[" for v in vals):
        return "json"
    if all(v.isdigit() for v in vals):
        return "numeric_string"
    return None


def heuristic_role(
    name: str,
    data_type: str,
    stats: dict[str, Any],
    keys: dict[str, bool],
    has_catalog: bool,
) -> str:
    """Cheap first-pass semantic role. Pass 2 (LLM) refines it.

    id | fk | measure | dimension | timestamp | flag | free_text
    """
    name_l = (name or "").lower()
    distinct = stats.get("distinct")
    total = stats.get("total")
    is_key = bool(keys.get("primary") or keys.get("sorting"))

    if is_temporal_type(data_type):
        return "timestamp"
    if name_l == "id" or name_l.endswith("_id"):
        if is_key and distinct and total and distinct >= total * 0.95:
            return "id"
        return "fk"
    if "bool" in data_type.lower() or (distinct is not None and distinct <= 2):
        return "flag"
    if has_catalog or (distinct is not None and distinct <= CATALOG_MAX_DISTINCT):
        return "dimension"
    if is_numeric_type(data_type):
        return "measure"
    if is_string_type(data_type):
        return "free_text"
    return "dimension"


# ── harvest one table ──────────────────────────────────────────────────────


async def harvest_table(
    deps: Pass1Deps, source_id: UUID, run_id: UUID, database: str, table: str
) -> list[str]:
    """Collect the full structural+statistical snapshot of one table and write
    the hard facts to the semantic layer + graph. Returns the column names (for
    the coverage invariant). No LLM here.
    """
    async with deps.sessionmaker() as session:
        source_repo = SourceRepoPg(session, deps.cipher)
        semantic = SemanticRepoPg(session)
        graph = GraphRepoNeo4j(deps.neo4j_driver)
        client = await CHClientFactory(source_repo).for_source(source_id)
        try:
            profiler = CHProfiler(client)
            columns = await profiler.fetch_columns(database, table)
            ddl = await profiler.fetch_ddl(database, table)
            stats = await profiler.fetch_column_stats(database, table, columns)
            meta = await profiler.fetch_table_meta(database, table)
            col_keys = await profiler.fetch_column_keys(database, table)
            ranges = await profiler.fetch_ranges(database, table, columns)
            ext = await profiler.fetch_extended_stats(database, table, columns)

            examples: dict[str, list[Any]] = {}
            for c in columns[:30]:
                examples[c["name"]] = await profiler.fetch_column_examples(
                    database, table, c["name"]
                )

            catalogs: dict[str, list[dict[str, Any]]] = {}
            budget = CATALOG_BUDGET_PER_TABLE
            for c in columns:
                if budget <= 0:
                    break
                dist = stats.get(c["name"], {}).get("distinct")
                if dist is not None and 1 <= dist <= CATALOG_MAX_DISTINCT:
                    cat = await profiler.fetch_value_catalog(database, table, c["name"])
                    if cat:
                        catalogs[c["name"]] = cat
                        budget -= 1
        finally:
            await client.close()

        table_id = await semantic.upsert_table(
            source_id=source_id,
            database=database,
            table=table,
            title=None,
            description=None,
            domain=None,
            tags=[],
            last_run_id=run_id,
            engine=meta.get("engine"),
            total_rows=meta.get("total_rows"),
            total_bytes=meta.get("total_bytes"),
            sorting_key=meta.get("sorting_key"),
            partition_key=meta.get("partition_key"),
            primary_key=meta.get("primary_key"),
        )
        await graph.upsert_table(
            id=str(table_id),
            source_id=str(source_id),
            database=database,
            name=table,
            title=None,
            domain=None,
            status="draft",
        )

        for c in columns:
            name = c["name"]
            s = stats.get(name, {})
            keys = col_keys.get(name, {})
            has_catalog = name in catalogs
            # Merge ranges + extended stats + detected pattern into one profile blob.
            profile = dict(ranges.get(name) or {})
            if ext.get(name):
                profile.update(ext[name])
            pat = detect_pattern(examples.get(name))
            if pat:
                profile["pattern"] = pat
            role = heuristic_role(name, c["type"], s, keys, has_catalog)
            cid = await semantic.upsert_column(
                table_id=table_id,
                name=name,
                position=c["position"],
                data_type=c["type"],
                description=None,
                semantic_role=role,
                null_ratio=s.get("null_ratio"),
                distinct_count=s.get("distinct"),
                total_count=s.get("total"),
                examples=examples.get(name),
                is_in_sorting_key=bool(keys.get("sorting")),
                is_in_partition_key=bool(keys.get("partition")),
                is_in_primary_key=bool(keys.get("primary")),
                value_catalog=catalogs.get(name),
                value_range=profile or None,
            )
            await graph.upsert_column(
                id=str(cid),
                table_id=str(table_id),
                name=name,
                data_type=c["type"],
                role=role,
                status="draft",
            )
        await session.commit()
        logger.info(
            "pass1.harvest: done", run_id=str(run_id), table=f"{database}.{table}",
            columns=len(columns),
        )
        return [c["name"] for c in columns]


# ── deterministic relations across the whole source ────────────────────────


async def build_relations_for_source(
    deps: Pass1Deps, source_id: UUID, run_id: UUID
) -> int:
    """Cross-table FK inference: name/type candidates verified by value overlap."""
    async with deps.sessionmaker() as session:
        source_repo = SourceRepoPg(session, deps.cipher)
        semantic = SemanticRepoPg(session)
        graph = GraphRepoNeo4j(deps.neo4j_driver)

        tables = await semantic.list_tables(source_id)
        described: list[dict[str, Any]] = []
        cols_by_table: dict[str, list[dict[str, Any]]] = {}
        for t in tables:
            cols = await semantic.get_columns(t["id"])
            cols_by_table[str(t["id"])] = cols
            described.append(
                {
                    "id": str(t["id"]),
                    "database": t["database"],
                    "name": t["table_name"],
                    "columns": [
                        {
                            "name": c["name"],
                            "type": c["data_type"],
                            "primary": bool(c.get("is_in_primary_key")),
                            "sorting": bool(c.get("is_in_sorting_key")),
                        }
                        for c in cols
                    ],
                }
            )

        client = await CHClientFactory(source_repo).for_source(source_id)
        created = 0
        try:
            profiler = CHProfiler(client)
            for t in described:
                others = [o for o in described if o["id"] != t["id"]]
                cur_cols = [
                    {"name": c["name"], "type": c["type"]} for c in t["columns"]
                ]
                col_keys = {
                    c["name"]: {"primary": c["primary"], "sorting": c["sorting"]}
                    for c in t["columns"]
                }
                candidates = _deterministic_relation_candidates(
                    t["id"], t["database"], t["name"], cur_cols, col_keys, others
                )
                budget = 25
                for cand in candidates:
                    if budget <= 0:
                        break
                    budget -= 1
                    try:
                        ov = await profiler.fetch_value_overlap(
                            cand["from_db"], cand["from_tbl"], cand["from_col"],
                            cand["to_db"], cand["to_tbl"], cand["to_col"],
                        )
                    except Exception:  # noqa: BLE001
                        continue
                    ratio = ov.get("ratio")
                    if ratio is None or ratio < RELATION_MIN_OVERLAP:
                        continue
                    from_col_id = await semantic.find_column(
                        UUID(cand["from_table_id"]), cand["from_col"]
                    )
                    to_col_id = await semantic.find_column(
                        UUID(cand["to_table_id"]), cand["to_col"]
                    )
                    if not from_col_id or not to_col_id:
                        continue
                    if await semantic.relation_exists(
                        from_column_id=from_col_id, to_column_id=to_col_id
                    ):
                        continue
                    cardinality = None
                    if cand["from_table_id"] == t["id"]:
                        fc = next(
                            (c for c in cols_by_table.get(cand["from_table_id"], [])
                             if c["name"] == cand["from_col"]),
                            None,
                        )
                        if fc:
                            cardinality = _cardinality(
                                {
                                    "distinct": fc.get("distinct_count"),
                                    "total": fc.get("total_count"),
                                }
                            )
                    await semantic.insert_relation(
                        source_id=source_id,
                        from_table_id=UUID(cand["from_table_id"]),
                        from_column_id=from_col_id,
                        to_table_id=UUID(cand["to_table_id"]),
                        to_column_id=to_col_id,
                        kind="inferred",
                        confidence=round(float(ratio), 3),
                        reasoning=(
                            f"Совпадение ключей `{cand['from_col']}`↔`{cand['to_col']}`: "
                            f"{ov['matched']}/{ov['total']} найдено (overlap≈{ratio:.0%})."
                        ),
                        cardinality=cardinality,
                        match_ratio=round(float(ratio), 3),
                    )
                    await graph.upsert_relation(
                        from_col=str(from_col_id),
                        to_col=str(to_col_id),
                        kind="inferred",
                        confidence=round(float(ratio), 3),
                        reasoning=None,
                    )
                    created += 1
        finally:
            await client.close()
        await session.commit()
        logger.info("pass1.relations: done", run_id=str(run_id), created=created)
        return created


# ── seeding + handler factories ────────────────────────────────────────────


async def seed_pass1_tasks(
    repo: ProfilingTaskRepo,
    *,
    run_id: UUID,
    source_id: UUID,
    whitelist: list[tuple[str, str]],
) -> None:
    """Create one harvest task per table + a relations task depending on them."""
    harvest_ids: list[UUID] = []
    for db, tbl in whitelist:
        tid = await repo.create(
            run_id=run_id,
            source_id=source_id,
            kind="harvest_table",
            target=f"{db}.{tbl}",
            database=db,
            table_name=tbl,
        )
        harvest_ids.append(tid)
    await repo.create(
        run_id=run_id,
        source_id=source_id,
        kind="relations",
        target="__relations__",
        depends_on=harvest_ids,
    )


def register_pass1_handlers(
    scheduler: TaskScheduler, deps: Pass1Deps, *, source_id: UUID, run_id: UUID
) -> None:
    async def harvest_handler(task: dict[str, Any]) -> TaskResult:
        cols = await harvest_table(
            deps, source_id, run_id, task["database"], task["table_name"]
        )
        return TaskResult("done", result={"columns": cols})

    async def relations_handler(task: dict[str, Any]) -> TaskResult:
        n = await build_relations_for_source(deps, source_id, run_id)
        return TaskResult("done", result={"relations": n})

    scheduler.register("harvest_table", harvest_handler)
    scheduler.register("relations", relations_handler)


async def run_pass1(
    deps: Pass1Deps,
    *,
    run_id: UUID,
    source_id: UUID,
    whitelist: list[tuple[str, str]],
    concurrency: int = 6,
) -> dict[str, Any]:
    """Seed pass-1 tasks and drain them through the scheduler.

    Idempotent: re-running for the same run reuses existing tasks and skips
    already-done work (fingerprint/unique-key), so it doubles as resume.
    """
    async with deps.sessionmaker() as session:
        await seed_pass1_tasks(
            ProfilingTaskRepo(session),
            run_id=run_id,
            source_id=source_id,
            whitelist=whitelist,
        )
        await session.commit()

    scheduler = TaskScheduler(deps.sessionmaker, concurrency=concurrency)
    register_pass1_handlers(scheduler, deps, source_id=source_id, run_id=run_id)
    return await scheduler.drain(run_id)
