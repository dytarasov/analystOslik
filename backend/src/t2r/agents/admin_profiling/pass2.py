"""Pass 2 — grouped LLM profiling on the durable task queue.

Per table: one ``describe_group`` task in *table* mode (title/description/domain/
grain) plus several in *columns* mode (1-3 related columns each). Column tasks
depend on the table task so they have its context. A describe task that's
genuinely unsure parks itself in ``awaiting_input`` with its questions in the
payload; once answered (status → pending, payload.answers filled) it re-runs and
finalizes. Output is analyst-oriented (unit/pii/value_meanings/safe_to_*/
caveats/confidence) and lands in sem_columns.semantics.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.agents.admin_profiling.scheduler import TaskResult, TaskScheduler
from t2r.infra.db.repos.profiling_task_repo_pg import ProfilingTaskRepo
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.llm.json_extractor import extract_json
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.logging import get_logger

logger = get_logger("profiling_pass2")

MAX_QUESTION_ROUNDS = 2


@dataclass
class Pass2Deps:
    sessionmaker: async_sessionmaker[AsyncSession]
    llm: LLMClient
    prompts: PromptLoader
    # Source's human glossary (capped). Fed to the describers as authoritative
    # domain context so descriptions are right first-pass and the model can
    # self-answer instead of parking awaiting_input questions.
    glossary: str = ""


# ── pure grouping (unit-tested) ────────────────────────────────────────────


def group_columns(columns: list[dict[str, Any]], *, max_size: int = 3) -> list[list[dict[str, Any]]]:
    """Cluster columns into small groups of related ones.

    Related = sharing the leading name segment (``lesson_*`` together). Buckets
    larger than max_size are chunked. Order is preserved so groups stay stable
    across re-runs (idempotent task targets).
    """
    buckets: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for c in columns:
        name = c.get("name", "")
        prefix = name.split("_", 1)[0] if "_" in name else name
        buckets.setdefault(prefix, []).append(c)
    groups: list[list[dict[str, Any]]] = []
    for cols in buckets.values():
        for i in range(0, len(cols), max_size):
            groups.append(cols[i : i + max_size])
    return groups


# ── seeding ────────────────────────────────────────────────────────────────


async def seed_pass2_tasks(deps: Pass2Deps, *, run_id: UUID, source_id: UUID) -> None:
    async with deps.sessionmaker() as session:
        semantic = SemanticRepoPg(session)
        repo = ProfilingTaskRepo(session)
        tables = await semantic.list_tables(source_id)
        for t in tables:
            db, tbl, tid = t["database"], t["table_name"], t["id"]
            table_task = await repo.create(
                run_id=run_id,
                source_id=source_id,
                kind="describe_group",
                target=f"{db}.{tbl}#__table__",
                database=db,
                table_name=tbl,
                payload={"mode": "table"},
            )
            # Only enabled columns get described — excluded ones (dropped at the
            # column-selection gate or later) are skipped entirely.
            cols = await semantic.get_columns(tid, only_enabled=True)
            for gi, group in enumerate(group_columns(cols)):
                await repo.create(
                    run_id=run_id,
                    source_id=source_id,
                    kind="describe_group",
                    target=f"{db}.{tbl}#g{gi}",
                    database=db,
                    table_name=tbl,
                    columns=[c["name"] for c in group],
                    depends_on=[table_task],
                    payload={"mode": "columns"},
                )
        await session.commit()


# ── handlers ───────────────────────────────────────────────────────────────


def _catalog_sample(cat: Any, n: int = 12) -> str:
    if not isinstance(cat, list) or not cat:
        return ""
    vals = [str(v.get("value")) if isinstance(v, dict) else str(v) for v in cat[:n]]
    return ", ".join(vals)


async def _describe_table_task(
    deps: Pass2Deps, source_id: UUID, task: dict[str, Any]
) -> TaskResult:
    db, tbl = task["database"], task["table_name"]
    async with deps.sessionmaker() as session:
        semantic = SemanticRepoPg(session)
        table_id = await semantic.find_table(source_id, db, tbl)
        if not table_id:
            return TaskResult("skipped", error="table not harvested")
        cols = await semantic.get_columns(table_id, only_enabled=True)
        meta_rows = {
            f"{r['database']}.{r['table_name']}": r
            for r in await semantic.list_tables(source_id)
        }
        meta = meta_rows.get(f"{db}.{tbl}", {})
        col_ctx = [
            {
                "name": c["name"],
                "data_type": c["data_type"],
                "semantic_role": c.get("semantic_role"),
                "distinct_count": c.get("distinct_count"),
                "null_ratio": c.get("null_ratio"),
                "catalog_sample": _catalog_sample(c.get("value_catalog")),
            }
            for c in cols
        ]
        rendered = deps.prompts.render(
            "table_summary", database=db, table=tbl, meta=meta, columns=col_ctx,
            glossary=deps.glossary,
        )
        # Curated tables (confirmed / hand-edited / glossary) are locked — a
        # re-profile must not overwrite their human content.
        tinfo = await semantic.get_table(table_id)
        if tinfo and tinfo.get("locked"):
            return TaskResult("skipped", result={"locked": True})
        out = await deps.llm.complete([{"role": "user", "content": rendered}], temperature=0.2)
        try:
            obj = extract_json(out) or {}
        except Exception:
            obj = {}
        await semantic.update_table(
            table_id,
            title=obj.get("title"),
            description=obj.get("description"),
            domain=obj.get("domain"),
            tags=obj.get("tags"),
            grain=obj.get("grain"),
        )
        await session.commit()
    return TaskResult("done", result={"title": obj.get("title")})


async def _describe_group_task(
    deps: Pass2Deps, source_id: UUID, task: dict[str, Any]
) -> TaskResult:
    db, tbl = task["database"], task["table_name"]
    group_cols = list(task.get("columns") or [])
    payload = task.get("payload") or {}
    round_no = int(payload.get("round") or 0)
    prior_answers = payload.get("answers") or []

    async with deps.sessionmaker() as session:
        semantic = SemanticRepoPg(session)
        table_id = await semantic.find_table(source_id, db, tbl)
        if not table_id:
            return TaskResult("skipped", error="table not harvested")
        all_cols = await semantic.get_columns(table_id, only_enabled=True)
        by_name = {c["name"]: c for c in all_cols}
        trow = {
            f"{r['database']}.{r['table_name']}": r
            for r in await semantic.list_tables(source_id)
        }.get(f"{db}.{tbl}", {})

        target_cols = [by_name[n] for n in group_cols if n in by_name]
        if not target_cols:
            return TaskResult("skipped", error="no columns to describe")
        peers = ", ".join(
            f"{c['name']}({c.get('semantic_role') or '?'})"
            for c in all_cols
            if c["name"] not in group_cols
        )[:1200]

        col_ctx = [
            {
                "name": c["name"],
                "data_type": c["data_type"],
                "semantic_role": c.get("semantic_role"),
                "distinct_count": c.get("distinct_count"),
                "null_ratio": c.get("null_ratio"),
                "examples": c.get("examples"),
                "value_catalog": _catalog_sample(c.get("value_catalog"), 30),
                "value_range": c.get("value_range"),
            }
            for c in target_cols
        ]
        rendered = deps.prompts.render(
            "column_group_describer",
            database=db,
            table=tbl,
            table_title=trow.get("title"),
            table_description=trow.get("description"),
            grain=trow.get("grain"),
            columns=col_ctx,
            peers=peers,
            answers=prior_answers,
            glossary=deps.glossary,
        )
        out = await deps.llm.complete([{"role": "user", "content": rendered}], temperature=0.2)
        try:
            obj = extract_json(out) or {}
        except Exception:
            obj = {}

        questions = obj.get("questions") or []
        if questions and round_no < MAX_QUESTION_ROUNDS:
            # Park for the admin. Keep prior answers; the inbox fills payload.answers.
            return TaskResult(
                "awaiting_input",
                payload={
                    "mode": "columns",
                    "round": round_no + 1,
                    "questions": questions,
                    "answers": prior_answers,
                    "table": f"{db}.{tbl}",
                },
            )

        described = obj.get("columns") or []
        for d in described:
            name = d.get("name")
            col = by_name.get(name)
            if not col:
                continue
            semantics = {
                k: d.get(k)
                for k in (
                    "unit", "pii", "value_meanings", "safe_to_group_by",
                    "safe_to_filter_by", "caveats", "suggested_aggregation", "confidence",
                )
                if d.get(k) is not None
            }
            await semantic.apply_column_description(
                col["id"],
                description=d.get("description"),
                semantic_role=d.get("semantic_role"),
                semantics=semantics or None,
            )
        await session.commit()
    return TaskResult("done", result={"described": len(described)})


def register_pass2_handlers(
    scheduler: TaskScheduler, deps: Pass2Deps, *, source_id: UUID
) -> None:
    async def describe_handler(task: dict[str, Any]) -> TaskResult:
        mode = (task.get("payload") or {}).get("mode", "columns")
        if mode == "table":
            return await _describe_table_task(deps, source_id, task)
        return await _describe_group_task(deps, source_id, task)

    scheduler.register("describe_group", describe_handler)


async def continue_pass2(
    deps: Pass2Deps, *, run_id: UUID, source_id: UUID, concurrency: int = 6
) -> dict[str, Any]:
    """Drain pass-2 tasks without (re-)seeding — used to resume after an admin
    answers a parked question (the answered task is back to pending)."""
    scheduler = TaskScheduler(deps.sessionmaker, concurrency=concurrency)
    register_pass2_handlers(scheduler, deps, source_id=source_id)
    return await scheduler.drain(run_id)


async def run_pass2(
    deps: Pass2Deps, *, run_id: UUID, source_id: UUID, concurrency: int = 6
) -> dict[str, Any]:
    await seed_pass2_tasks(deps, run_id=run_id, source_id=source_id)
    return await continue_pass2(deps, run_id=run_id, source_id=source_id, concurrency=concurrency)
