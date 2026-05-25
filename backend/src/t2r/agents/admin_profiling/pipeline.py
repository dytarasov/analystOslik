from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from t2r.agents.orchestrator.pipeline import Pipeline
from t2r.agents.orchestrator.run import AgentRun
from t2r.agents.orchestrator.step import Step
from t2r.domain.events.types import (
    profiling_table_completed,
    profiling_table_started,
    step_progress,
)
from t2r.infra.clickhouse.client import CHClient
from t2r.infra.clickhouse.factory import CHClientFactory
from t2r.infra.clickhouse.profiler import CHProfiler
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.profiling_repo_pg import ProfilingRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.json_extractor import extract_json
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.logging import get_logger

logger = get_logger("admin_profiling")

# A column with at most this many distinct values gets a full value catalog
# (every value + frequency) — the key signal for correct categorical filters.
CATALOG_MAX_DISTINCT = 50
# Cap value-catalog scans per table (each is a GROUP BY scan).
CATALOG_BUDGET_PER_TABLE = 30
# Minimum sampled FK→PK overlap to accept a deterministic relation.
RELATION_MIN_OVERLAP = 0.5


class ProfilingDeps:
    """Bundle of services required by profiling steps."""

    def __init__(
        self,
        *,
        ch_factory: CHClientFactory,
        profiling_repo: ProfilingRepoPg,
        semantic_repo: SemanticRepoPg,
        notes_repo: NotesRepoPg,
        graph_repo: GraphRepoNeo4j,
        session: AsyncSession,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        prompts: PromptLoader,
    ) -> None:
        self.ch_factory = ch_factory
        self.profiling_repo = profiling_repo
        self.semantic_repo = semantic_repo
        self.notes_repo = notes_repo
        self.graph_repo = graph_repo
        self.session = session
        self.llm = llm
        self.embeddings = embeddings
        self.prompts = prompts


class _ProfileEverythingStep(Step):
    """Single sequential step that profiles all selected tables.

    We keep one big step for SSE clarity (one progress timeline) and rely on
    `profiling.table.*` events for per-table granularity.
    """

    def __init__(self, deps: ProfilingDeps, source_id: UUID, run_id: UUID) -> None:
        super().__init__(step_id="profile", name="Профилирую таблицы")
        self.deps = deps
        self.source_id = source_id
        self.run_id = run_id

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        client: CHClient = await self.deps.ch_factory.for_source(self.source_id)
        try:
            profiler = CHProfiler(client)
            await self.deps.profiling_repo.set_status(self.run_id, "running")

            params = ctx.get("params", {}) or {}
            # Whitelist-driven: the only tables we touch are those the admin
            # explicitly picked via `source_table_selections`. `include`/`exclude`
            # in params is still honored as an extra filter for partial reruns.
            include = set(params.get("include") or [])
            exclude = set(params.get("exclude") or [])
            whitelist: list[tuple[str, str]] = ctx.get("whitelist") or []

            if not whitelist:
                raise RuntimeError(
                    "Список таблиц для профилирования пуст — сначала выберите таблицы в интерфейсе"
                )

            tasks: list[tuple[str, str]] = []
            for db, tbl in whitelist:
                qname = f"{db}.{tbl}"
                if include and qname not in include:
                    continue
                if qname in exclude:
                    continue
                tasks.append((db, tbl))

            total = len(tasks)
            logger.info(
                "profiling.pipeline: starting",
                run_id=str(self.run_id),
                source_id=str(self.source_id),
                total_tables=total,
                whitelist=[f"{d}.{t}" for d, t in tasks],
            )
            await run.emit(step_progress(self.step_id, 0.0, f"Найдено таблиц: {total}"))

            # Per-source context of already described tables for relation inference
            described: list[dict[str, Any]] = []

            import time as _time

            for idx, (db, tbl) in enumerate(tasks, start=1):
                if run.cancel_event.is_set():
                    logger.warning(
                        "profiling.pipeline: cancel_event set before table — aborting",
                        run_id=str(self.run_id),
                        next_table=f"{db}.{tbl}",
                        processed=idx - 1,
                        total=total,
                    )
                    return
                logger.info(
                    "profiling.pipeline: table starting",
                    run_id=str(self.run_id),
                    db=db,
                    table=tbl,
                    idx=idx,
                    total=total,
                )
                await run.emit(profiling_table_started(db, tbl, idx, total))
                t_started = _time.time()
                try:
                    description = await self._process_table(
                        run, profiler, db, tbl, described
                    )
                    described.append(description)
                    logger.info(
                        "profiling.pipeline: table done",
                        run_id=str(self.run_id),
                        db=db,
                        table=tbl,
                        idx=idx,
                        total=total,
                        elapsed_s=round(_time.time() - t_started, 2),
                    )
                except asyncio.CancelledError:
                    logger.warning(
                        "profiling.pipeline: CancelledError inside table",
                        run_id=str(self.run_id),
                        db=db,
                        table=tbl,
                    )
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "profiling.pipeline: table failed",
                        db=db,
                        table=tbl,
                        elapsed_s=round(_time.time() - t_started, 2),
                    )
                    await self.deps.profiling_repo.upsert_table(
                        self.run_id, db, tbl, status="failed", error=str(exc)
                    )
                await run.emit(profiling_table_completed(db, tbl, 0))
                await run.emit(step_progress(self.step_id, idx / max(total, 1)))
                # Commit after every table so progress is durable and visible to
                # the UI. We commit on THIS coroutine (never from a background
                # task) — AsyncSession is not safe for concurrent use, and the
                # previous periodic-committer task could collide with an
                # in-flight execute() on the same connection.
                await self._commit_progress()

            await self.deps.profiling_repo.set_stats(
                self.run_id, {"tables_total": total, "tables_done": total}
            )
            await self.deps.profiling_repo.set_status(self.run_id, "done")
            await self._commit_progress()
        finally:
            await client.close()

    async def _commit_progress(self) -> None:
        try:
            await self.deps.session.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "profiling.pipeline: progress commit failed", run_id=str(self.run_id)
            )
            await self.deps.session.rollback()

    async def _process_table(
        self,
        run: AgentRun,
        profiler: CHProfiler,
        db: str,
        tbl: str,
        described: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ddl = await profiler.fetch_ddl(db, tbl)
        columns = await profiler.fetch_columns(db, tbl)
        sample = await profiler.fetch_sample(db, tbl, limit=100)
        stats = await profiler.fetch_column_stats(db, tbl, columns)
        usage = await profiler.fetch_usage_stats(db, tbl)

        # ── Physical metadata + keys (P1) ────────────────────────────────
        meta = await profiler.fetch_table_meta(db, tbl)
        col_keys = await profiler.fetch_column_keys(db, tbl)

        # ── Numeric/temporal ranges in one scan (P1) ─────────────────────
        ranges = await profiler.fetch_ranges(db, tbl, columns)

        # examples per column (best-effort)
        examples: dict[str, list[Any]] = {}
        for c in columns[:30]:  # limit work
            examples[c["name"]] = await profiler.fetch_column_examples(db, tbl, c["name"])

        # ── Value catalogs for low-cardinality columns (P1) ──────────────
        # Full value list + frequency — what lets the client agent write
        # correct `WHERE col = '...'` filters.
        catalogs: dict[str, list[dict[str, Any]]] = {}
        budget = CATALOG_BUDGET_PER_TABLE
        for c in columns:
            if budget <= 0:
                break
            dist = stats.get(c["name"], {}).get("distinct")
            if dist is not None and 1 <= dist <= CATALOG_MAX_DISTINCT:
                cat = await profiler.fetch_value_catalog(db, tbl, c["name"])
                if cat:
                    catalogs[c["name"]] = cat
                    budget -= 1

        await self.deps.profiling_repo.upsert_table(
            self.run_id,
            db,
            tbl,
            status="describing",
            ddl=ddl,
            sample=sample,
            column_stats=stats,
            usage_stats=usage,
        )

        sample_preview = _format_sample(sample, n_rows=5)

        table_json = await self._describe_table(
            db, tbl, ddl, columns, stats, sample_preview, usage, meta, user_notes=None
        )

        # Human-in-the-loop review on EVERY table: show the draft description and
        # wait for the admin's free-form corrections/notes (or a skip). No
        # auto-generated questions — the admin decides what to add.
        answer = await run.await_user_input(
            question=(
                f"Проверьте описание таблицы `{db}.{tbl}` — "
                "добавьте уточнения или пропустите"
            ),
            schema={
                "type": "table_review",
                "database": db,
                "table": tbl,
                "title": table_json.get("title"),
                "description": table_json.get("description"),
                "domain": table_json.get("domain"),
                "grain": table_json.get("grain"),
                "columns": [{"name": c["name"], "type": c["type"]} for c in columns],
            },
        )
        user_text = _extract_review_text(answer)
        if user_text:
            qa_pairs = [
                {
                    "id": "note",
                    "text": "Комментарий администратора по таблице",
                    "kind": "free",
                    "column": None,
                    "answer": user_text,
                }
            ]
            refined = await self._refine_table(db, tbl, table_json, qa_pairs)
            if isinstance(refined, dict) and refined.get("description"):
                table_json.update(
                    {
                        k: refined.get(k)
                        for k in ("title", "description", "domain", "tags", "grain")
                        if refined.get(k)
                    }
                )
            table_json["_admin_notes"] = user_text

        # Compact time-coverage summary for the table profile blob.
        time_coverage = {
            name: {"min": str(r.get("min")), "max": str(r.get("max"))}
            for name, r in ranges.items()
            if r.get("min") is not None or r.get("max") is not None
        }
        table_id = await self.deps.semantic_repo.upsert_table(
            source_id=self.source_id,
            database=db,
            table=tbl,
            title=table_json.get("title"),
            description=table_json.get("description"),
            domain=table_json.get("domain"),
            tags=table_json.get("tags", []),
            last_run_id=self.run_id,
            engine=meta.get("engine"),
            total_rows=meta.get("total_rows"),
            total_bytes=meta.get("total_bytes"),
            sorting_key=meta.get("sorting_key"),
            partition_key=meta.get("partition_key"),
            primary_key=meta.get("primary_key"),
            grain=table_json.get("grain"),
            profile={"time_coverage": time_coverage} if time_coverage else None,
        )
        if table_json.get("_admin_notes"):
            await self.deps.semantic_repo.update_table(
                table_id, user_notes=table_json["_admin_notes"]
            )

        # Column descriptions
        col_inputs = []
        for c in columns:
            s = stats.get(c["name"], {})
            keys = col_keys.get(c["name"], {})
            col_inputs.append(
                {
                    "name": c["name"],
                    "type": c["type"],
                    "examples": examples.get(c["name"], []),
                    "distinct": s.get("distinct"),
                    "null_ratio": s.get("null_ratio"),
                    # Top values w/ freq (only for low-cardinality columns).
                    "catalog": [v.get("value") for v in catalogs.get(c["name"], [])][:30],
                    "range": ranges.get(c["name"]),
                    "is_key": bool(
                        keys.get("primary") or keys.get("sorting") or keys.get("partition")
                    ),
                }
            )
        col_descriptions = await self._describe_columns(
            db, tbl, table_json, col_inputs
        )
        # Map by name
        desc_by_name = {d.get("name"): d for d in col_descriptions if isinstance(d, dict)}

        # Create the Table node in the graph FIRST — UPSERT_COLUMN's old
        # MATCH-based form silently dropped columns when the table didn't
        # exist yet. Upserting the table here removes that ordering hazard.
        await self.deps.graph_repo.upsert_table(
            id=str(table_id),
            source_id=str(self.source_id),
            database=db,
            name=tbl,
            title=table_json.get("title"),
            domain=table_json.get("domain"),
            status="draft",
        )

        col_id_by_name: dict[str, UUID] = {}
        for c in columns:
            d = desc_by_name.get(c["name"], {})
            s = stats.get(c["name"], {})
            keys = col_keys.get(c["name"], {})
            cid = await self.deps.semantic_repo.upsert_column(
                table_id=table_id,
                name=c["name"],
                position=c["position"],
                data_type=c["type"],
                description=d.get("description"),
                semantic_role=d.get("semantic_role"),
                null_ratio=s.get("null_ratio"),
                distinct_count=s.get("distinct"),
                total_count=s.get("total"),
                examples=examples.get(c["name"]),
                is_in_sorting_key=bool(keys.get("sorting")),
                is_in_partition_key=bool(keys.get("partition")),
                is_in_primary_key=bool(keys.get("primary")),
                value_catalog=catalogs.get(c["name"]),
                value_range=ranges.get(c["name"]),
            )
            col_id_by_name[c["name"]] = cid
            await self.deps.graph_repo.upsert_column(
                id=str(cid),
                table_id=str(table_id),
                name=c["name"],
                data_type=c["type"],
                role=d.get("semantic_role"),
                status="draft",
            )

        # Relations: deterministic value-overlap-verified first, then LLM
        # backstop for semantic/conceptual links the heuristics miss.
        if described:
            await self._build_relations(
                profiler=profiler,
                db=db,
                tbl=tbl,
                table_id=table_id,
                columns=columns,
                stats=stats,
                col_keys=col_keys,
                col_descriptions=col_descriptions,
                table_json=table_json,
                described=described,
            )

        # md_note + embedding (P4 — this is the vector-search substrate, so we
        # pack value catalogs, keys, ranges and grain into it).
        md_body = _render_md_note(
            db, tbl, table_json, col_descriptions, stats, meta, ranges, catalogs, col_keys
        )
        note_id = await self.deps.notes_repo.upsert_table_note(
            source_id=self.source_id,
            target_id=table_id,
            title=table_json.get("title") or tbl,
            body_md=md_body,
            tags=list(table_json.get("tags", [])),
        )
        try:
            emb = await self.deps.embeddings.embed(md_body)
            await self.deps.notes_repo.set_embedding(note_id, emb)
        except Exception:  # noqa: BLE001
            logger.exception("embedding failed", db=db, table=tbl)

        # Column-scoped notes for the high-signal columns (keys, columns with a
        # value catalog, or numeric/date ranges) so they're independently
        # retrievable by the client agent.
        await self._write_column_notes(
            db, tbl, table_json, columns, desc_by_name, col_id_by_name,
            stats, catalogs, ranges, col_keys,
        )

        await self.deps.profiling_repo.upsert_table(
            self.run_id, db, tbl, status="done"
        )

        return {
            "id": str(table_id),
            "database": db,
            "name": tbl,
            "title": table_json.get("title"),
            "columns": [
                {
                    "name": c["name"],
                    "type": c["type"],
                    "role": desc_by_name.get(c["name"], {}).get("semantic_role"),
                    "primary": bool(col_keys.get(c["name"], {}).get("primary")),
                    "sorting": bool(col_keys.get(c["name"], {}).get("sorting")),
                }
                for c in columns
            ],
        }

    async def _write_column_notes(
        self,
        db: str,
        tbl: str,
        table_json: dict[str, Any],
        columns: list[dict[str, Any]],
        desc_by_name: dict[str, dict[str, Any]],
        col_id_by_name: dict[str, UUID],
        stats: dict[str, Any],
        catalogs: dict[str, list[dict[str, Any]]],
        ranges: dict[str, dict[str, Any]],
        col_keys: dict[str, dict[str, bool]],
        *,
        max_notes: int = 20,
    ) -> None:
        written = 0
        table_title = table_json.get("title") or tbl
        for c in columns:
            if written >= max_notes:
                break
            name = c["name"]
            keys = col_keys.get(name, {})
            has_catalog = name in catalogs
            has_range = name in ranges
            is_key = bool(keys.get("primary") or keys.get("sorting") or keys.get("partition"))
            if not (has_catalog or has_range or is_key):
                continue
            col_id = col_id_by_name.get(name)
            if not col_id:
                continue
            body = _render_column_note(
                db, tbl, table_title, c, desc_by_name.get(name, {}),
                stats.get(name, {}), catalogs.get(name), ranges.get(name), keys,
            )
            note_id = await self.deps.notes_repo.upsert_note(
                source_id=self.source_id,
                scope="column",
                target_id=col_id,
                title=f"{table_title} · {name}",
                body_md=body,
                tags=[],
            )
            try:
                emb = await self.deps.embeddings.embed(body)
                await self.deps.notes_repo.set_embedding(note_id, emb)
            except Exception:  # noqa: BLE001
                logger.exception("column note embedding failed", db=db, table=tbl, column=name)
            written += 1

    async def _build_relations(
        self,
        *,
        profiler: CHProfiler,
        db: str,
        tbl: str,
        table_id: UUID,
        columns: list[dict[str, Any]],
        stats: dict[str, Any],
        col_keys: dict[str, dict[str, bool]],
        col_descriptions: list[dict[str, Any]],
        table_json: dict[str, Any],
        described: list[dict[str, Any]],
    ) -> None:
        # ── 1. Deterministic candidates verified by sampled value overlap ──
        candidates = _deterministic_relation_candidates(
            str(table_id), db, tbl, columns, col_keys, described
        )
        overlap_budget = 25
        for cand in candidates:
            if overlap_budget <= 0:
                break
            overlap_budget -= 1
            try:
                ov = await profiler.fetch_value_overlap(
                    cand["from_db"], cand["from_tbl"], cand["from_col"],
                    cand["to_db"], cand["to_tbl"], cand["to_col"],
                )
            except Exception:  # noqa: BLE001
                logger.exception("value overlap probe failed", cand=cand)
                continue
            ratio = ov.get("ratio")
            if ratio is None or ratio < RELATION_MIN_OVERLAP:
                continue
            from_col_id = await self.deps.semantic_repo.find_column(
                UUID(cand["from_table_id"]), cand["from_col"]
            )
            to_col_id = await self.deps.semantic_repo.find_column(
                UUID(cand["to_table_id"]), cand["to_col"]
            )
            if not from_col_id or not to_col_id:
                continue
            # Cardinality is meaningful only when the FK (from) side is the
            # current table — that's the only side we have fresh stats for.
            if cand["from_table_id"] == str(table_id):
                cardinality = _cardinality(stats.get(cand["from_col"], {}))
            else:
                cardinality = None
            reasoning = (
                f"Совпадение ключей `{cand['from_col']}`↔`{cand['to_col']}`: "
                f"{ov['matched']}/{ov['total']} значений найдены в целевой колонке "
                f"(overlap≈{ratio:.0%})."
            )
            await self._insert_relation_safe(
                from_table_id=UUID(cand["from_table_id"]),
                from_col_id=from_col_id,
                to_table_id=UUID(cand["to_table_id"]),
                to_col_id=to_col_id,
                kind="inferred",
                confidence=round(float(ratio), 3),
                reasoning=reasoning,
                cardinality=cardinality,
                match_ratio=round(float(ratio), 3),
            )

        # ── 2. LLM backstop for semantic relations heuristics didn't catch ──
        try:
            relations = await self._infer_relations(
                db, tbl, table_json, col_descriptions, described
            )
        except Exception:  # noqa: BLE001
            logger.exception("llm relation inference failed")
            relations = []
        for rel in relations:
            try:
                from_col_id = await self.deps.semantic_repo.find_column(
                    table_id, rel.get("from_column", "")
                )
                if not from_col_id:
                    continue
                to_table_id = await self.deps.semantic_repo.find_table(
                    self.source_id,
                    rel.get("to_database") or db,
                    rel.get("to_table", ""),
                )
                if not to_table_id:
                    continue
                to_col_id = await self.deps.semantic_repo.find_column(
                    to_table_id, rel.get("to_column", "")
                )
                if not to_col_id:
                    continue
                confidence = float(rel.get("confidence", 0.5))
                if confidence < 0.5:
                    continue
                await self._insert_relation_safe(
                    from_table_id=table_id,
                    from_col_id=from_col_id,
                    to_table_id=to_table_id,
                    to_col_id=to_col_id,
                    kind="inferred",
                    confidence=confidence,
                    reasoning=rel.get("reasoning"),
                    cardinality=None,
                    match_ratio=None,
                )
            except Exception:  # noqa: BLE001
                logger.exception("relation insert failed", rel=rel)

    async def _insert_relation_safe(
        self,
        *,
        from_table_id: UUID,
        from_col_id: UUID,
        to_table_id: UUID,
        to_col_id: UUID,
        kind: str,
        confidence: float,
        reasoning: str | None,
        cardinality: str | None,
        match_ratio: float | None,
    ) -> None:
        # Dedup: deterministic + LLM passes can propose the same edge.
        if await self.deps.semantic_repo.relation_exists(
            from_column_id=from_col_id, to_column_id=to_col_id
        ):
            return
        await self.deps.semantic_repo.insert_relation(
            source_id=self.source_id,
            from_table_id=from_table_id,
            from_column_id=from_col_id,
            to_table_id=to_table_id,
            to_column_id=to_col_id,
            kind=kind,
            confidence=confidence,
            reasoning=reasoning,
            cardinality=cardinality,
            match_ratio=match_ratio,
        )
        await self.deps.graph_repo.upsert_relation(
            from_col=str(from_col_id),
            to_col=str(to_col_id),
            kind=kind,
            confidence=confidence,
            reasoning=reasoning,
        )

    async def _describe_table(
        self,
        db: str,
        tbl: str,
        ddl: str,
        columns: list[dict[str, Any]],
        stats: dict[str, Any],
        sample_preview: str,
        usage: dict[str, Any],
        meta: dict[str, Any],
        user_notes: str | None,
    ) -> dict[str, Any]:
        prompt = self.deps.prompts.render(
            "table_describer",
            database=db,
            table=tbl,
            ddl=ddl,
            columns=columns,
            stats=stats,
            sample_preview=sample_preview,
            usage=usage,
            meta=meta,
            user_notes=user_notes,
        )
        out = await self.deps.llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        try:
            return extract_json(out) or {}
        except Exception:
            return {"title": tbl, "description": out[:500], "domain": None, "tags": []}

    async def _refine_table(
        self,
        db: str,
        tbl: str,
        prev: dict[str, Any],
        qa_pairs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = self.deps.prompts.render(
            "table_describer_refine",
            database=db,
            table=tbl,
            prev=prev,
            qa_pairs=qa_pairs,
        )
        out = await self.deps.llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        try:
            return extract_json(out) or {}
        except Exception:
            return {}

    async def _describe_columns(
        self,
        db: str,
        tbl: str,
        table_json: dict[str, Any],
        columns: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not columns:
            return []
        prompt = self.deps.prompts.render(
            "column_describer",
            database=db,
            table=tbl,
            table_title=table_json.get("title") or "",
            table_description=table_json.get("description") or "",
            columns=columns,
            user_notes=None,
        )
        out = await self.deps.llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        try:
            parsed = extract_json(out)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    async def _infer_relations(
        self,
        db: str,
        tbl: str,
        table_json: dict[str, Any],
        col_descriptions: list[dict[str, Any]],
        other_tables: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Limit context size: take last 30 described tables
        ctx_tables = other_tables[-30:]
        prompt = self.deps.prompts.render(
            "relation_inferrer",
            database=db,
            table=tbl,
            table_title=table_json.get("title") or "",
            columns=[
                {
                    "name": c.get("name"),
                    "type": "",
                    "semantic_role": c.get("semantic_role") or "",
                    "description": c.get("description") or "",
                }
                for c in col_descriptions
            ],
            other_tables=ctx_tables,
        )
        out = await self.deps.llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        try:
            parsed = extract_json(out)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []


def _extract_review_text(answer: Any) -> str:
    """Pull the admin's free-form review text out of the respond payload.

    The review form posts ``{"answer": "..."}`` (a plain string). We also accept
    a bare string or a ``{"text": ...}`` shape, and treat skip/empty as no input.
    """
    if isinstance(answer, str):
        text = answer
    elif isinstance(answer, dict):
        raw = answer.get("text") or answer.get("answer")
        text = raw if isinstance(raw, str) else ""
    else:
        text = ""
    text = text.strip()
    if text.lower() in ("skip", "пропустить"):
        return ""
    return text


def _format_sample(sample: dict[str, Any], n_rows: int = 5) -> str:
    cols = sample.get("columns", [])
    rows = sample.get("rows", [])[:n_rows]
    out = "| " + " | ".join(cols) + " |\n"
    out += "|" + "|".join("---" for _ in cols) + "|\n"
    for r in rows:
        out += "| " + " | ".join(_short_cell(v) for v in r) + " |\n"
    return out


def _short_cell(v: Any) -> str:
    s = str(v)
    if len(s) > 60:
        s = s[:57] + "…"
    return s.replace("|", "/")


def _fmt_catalog(catalog: list[dict[str, Any]] | None, limit: int = 25) -> str:
    if not catalog:
        return ""
    vals = [str(v.get("value")) for v in catalog[:limit]]
    suffix = " …" if len(catalog) > limit else ""
    return ", ".join(vals) + suffix


def _fmt_range(rng: dict[str, Any] | None) -> str:
    if not rng:
        return ""
    lo, hi = rng.get("min"), rng.get("max")
    out = f"{lo} … {hi}"
    if rng.get("avg") is not None:
        out += f" (avg={rng.get('avg')}, median={rng.get('median')})"
    return out


def _render_md_note(
    db: str,
    tbl: str,
    table_json: dict[str, Any],
    col_descriptions: list[dict[str, Any]],
    stats: dict[str, Any],
    meta: dict[str, Any] | None = None,
    ranges: dict[str, dict[str, Any]] | None = None,
    catalogs: dict[str, list[dict[str, Any]]] | None = None,
    col_keys: dict[str, dict[str, bool]] | None = None,
) -> str:
    meta = meta or {}
    ranges = ranges or {}
    catalogs = catalogs or {}
    col_keys = col_keys or {}
    parts = [
        f"# {table_json.get('title') or tbl}",
        "",
        f"`{db}.{tbl}` · domain: {table_json.get('domain') or '—'}",
    ]
    if table_json.get("grain"):
        parts.append(f"Грануляр­ность: {table_json['grain']}")
    parts += ["", table_json.get("description") or "", ""]

    # Physical metadata block — keys are the join/filter columns.
    phys = []
    if meta.get("engine"):
        phys.append(f"engine={meta['engine']}")
    if meta.get("total_rows") is not None:
        phys.append(f"rows≈{meta['total_rows']:,}")
    if meta.get("sorting_key"):
        phys.append(f"ORDER BY ({meta['sorting_key']})")
    if meta.get("partition_key"):
        phys.append(f"PARTITION BY ({meta['partition_key']})")
    if meta.get("primary_key"):
        phys.append(f"PRIMARY KEY ({meta['primary_key']})")
    if phys:
        parts += ["## Физические свойства", " · ".join(phys), ""]

    parts.append("## Колонки")
    for c in col_descriptions:
        name = c.get("name", "?")
        role = c.get("semantic_role", "")
        desc = c.get("description", "")
        s = stats.get(name, {}) if isinstance(stats, dict) else {}
        keys = col_keys.get(name, {})
        key_marks = []
        if keys.get("primary"):
            key_marks.append("PK")
        if keys.get("sorting"):
            key_marks.append("ORDER")
        if keys.get("partition"):
            key_marks.append("PART")
        key_str = (" [" + ",".join(key_marks) + "]") if key_marks else ""
        line = (
            f"- `{name}` ({role}){key_str} — {desc}"
            f" · distinct={s.get('distinct')} null={s.get('null_ratio')}"
        )
        cat = _fmt_catalog(catalogs.get(name))
        if cat:
            line += f"\n    значения: {cat}"
        rng = _fmt_range(ranges.get(name))
        if rng:
            line += f"\n    диапазон: {rng}"
        parts.append(line)
    return "\n".join(parts)


def _render_column_note(
    db: str,
    tbl: str,
    table_title: str,
    col: dict[str, Any],
    desc: dict[str, Any],
    stat: dict[str, Any],
    catalog: list[dict[str, Any]] | None,
    rng: dict[str, Any] | None,
    keys: dict[str, bool],
) -> str:
    name = col["name"]
    parts = [
        f"# {table_title} · колонка `{name}`",
        "",
        f"`{db}.{tbl}.{name}` : {col['type']} · роль: {desc.get('semantic_role') or '—'}",
        "",
        desc.get("description") or "",
    ]
    key_marks = [k for k, on in (("primary key", keys.get("primary")),
                                 ("sorting key", keys.get("sorting")),
                                 ("partition key", keys.get("partition"))) if on]
    if key_marks:
        parts.append(f"Ключ: {', '.join(key_marks)}.")
    if stat:
        parts.append(
            f"distinct={stat.get('distinct')} · null_ratio={stat.get('null_ratio')}"
        )
    cat = _fmt_catalog(catalog, limit=50)
    if cat:
        parts += ["", "Возможные значения (по частоте):", cat]
    rng_s = _fmt_range(rng)
    if rng_s:
        parts += ["", f"Диапазон: {rng_s}"]
    return "\n".join(parts)


def _singular(name: str) -> str:
    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if name.endswith("s") and len(name) > 1:
        return name[:-1]
    return name


def _norm_type(t: str) -> str:
    s = (t or "").strip()
    changed = True
    while changed:
        changed = False
        for wrap in ("Nullable(", "LowCardinality("):
            if s.startswith(wrap) and s.endswith(")"):
                s = s[len(wrap):-1].strip()
                changed = True
    # Drop parametrization like Decimal(10,2) / DateTime('UTC') / FixedString(8)
    head = s.split("(", 1)[0]
    return head


def _compatible_types(t1: str, t2: str) -> bool:
    a, b = _norm_type(t1), _norm_type(t2)
    if a == b:
        return True
    # Treat the integer family as mutually joinable (UInt32 ↔ Int64 keys happen).
    int_fam = {"Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32", "UInt64"}
    return a in int_fam and b in int_fam


def _cardinality(from_stat: dict[str, Any]) -> str | None:
    """N:1 when the from-column repeats, 1:1 when it's ~unique.

    Assumes the target column is a key (unique) — the deterministic candidate
    generator only points at key columns on the target side.
    """
    distinct = from_stat.get("distinct")
    total = from_stat.get("total")
    if not distinct or not total:
        return None
    return "1:1" if distinct >= total * 0.99 else "N:1"


def _deterministic_relation_candidates(
    cur_table_id: str,
    db: str,
    tbl: str,
    columns: list[dict[str, Any]],
    col_keys: dict[str, dict[str, bool]],
    described: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate FK candidate pairs by name + type matching against already
    described tables. Direction points at the key (PK) side as the target.

    We are deliberately generous here — the caller verifies every candidate
    with a sampled value-overlap probe, so false positives are cheap.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for cc in columns:
        cname = cc["name"]
        cc_keys = col_keys.get(cname, {})
        cc_is_key = bool(cc_keys.get("primary") or cc_keys.get("sorting"))
        looks_fk = cname.endswith("_id") or cname == "id"
        for ot in described:
            ot_id = ot.get("id")
            ot_db = ot.get("database")
            ot_name = ot.get("name")
            if not ot_id or not ot_name:
                continue
            ent = _singular(ot_name)
            for oc in ot.get("columns") or []:
                ocname = oc.get("name")
                if not ocname:
                    continue
                if not _compatible_types(cc.get("type", ""), oc.get("type", "")):
                    continue
                name_match = (
                    cname == ocname
                    or cname == f"{ent}_id"
                    or cname == f"{ot_name}_id"
                    or (looks_fk and ocname == "id")
                )
                if not name_match:
                    continue
                oc_is_key = bool(oc.get("primary") or oc.get("sorting"))
                # Direction: referenced (target) side is the key column.
                if oc_is_key or not cc_is_key:
                    cand = {
                        "from_table_id": cur_table_id,
                        "from_db": db, "from_tbl": tbl, "from_col": cname,
                        "to_table_id": str(ot_id),
                        "to_db": ot_db, "to_tbl": ot_name, "to_col": ocname,
                    }
                else:
                    cand = {
                        "from_table_id": str(ot_id),
                        "from_db": ot_db, "from_tbl": ot_name, "from_col": ocname,
                        "to_table_id": cur_table_id,
                        "to_db": db, "to_tbl": tbl, "to_col": cname,
                    }
                key = (
                    cand["from_table_id"], cand["from_col"],
                    cand["to_table_id"], cand["to_col"],
                )
                if key in seen:
                    continue
                seen.add(key)
                out.append(cand)
    return out


class _SynthesizeSourceStep(Step):
    """Source-level synthesis after all tables are profiled (П3).

    Produces the business layer the client agent already consumes but profiling
    never filled: a glossary, candidate metrics, and a star-schema / join-path
    overview persisted as an embedded source note.
    """

    def __init__(self, deps: ProfilingDeps, source_id: UUID) -> None:
        super().__init__(step_id="synthesize", name="Синтезирую модель источника")
        self.deps = deps
        self.source_id = source_id

    async def execute(self, run: AgentRun, ctx) -> None:  # type: ignore[override]
        try:
            tables = await self.deps.semantic_repo.list_tables(self.source_id)
            if not tables:
                return
            await run.emit(step_progress(self.step_id, 0.2, "Собираю карту таблиц"))

            id_to_qname = {
                str(t["id"]): f"{t['database']}.{t['table_name']}" for t in tables
            }
            # Compact per-table catalog: title/domain/grain + key columns.
            table_blocks: list[dict[str, Any]] = []
            for t in tables:
                cols = await self.deps.semantic_repo.get_columns(t["id"])
                key_cols = [
                    {
                        "name": c["name"],
                        "role": c.get("semantic_role"),
                        "key": bool(
                            c.get("is_in_primary_key") or c.get("is_in_sorting_key")
                        ),
                    }
                    for c in cols
                    if c.get("is_in_primary_key")
                    or c.get("is_in_sorting_key")
                    or c.get("semantic_role") in ("id", "fk", "measure", "timestamp")
                ][:20]
                table_blocks.append(
                    {
                        "qname": f"{t['database']}.{t['table_name']}",
                        "title": t.get("title"),
                        "domain": t.get("domain"),
                        "grain": t.get("grain"),
                        "total_rows": t.get("total_rows"),
                        "columns": key_cols,
                    }
                )

            relations = await self.deps.semantic_repo.get_relations(self.source_id)
            edges = [
                {
                    "from": id_to_qname.get(str(r["from_table_id"]), "?"),
                    "to": id_to_qname.get(str(r["to_table_id"]), "?"),
                    "cardinality": r.get("cardinality"),
                    "confidence": float(r["confidence"]) if r.get("confidence") is not None else None,
                }
                for r in relations
            ]

            await run.emit(step_progress(self.step_id, 0.5, "Генерирую глоссарий и метрики"))
            rendered = self.deps.prompts.render(
                "source_synthesizer",
                tables=table_blocks,
                edges=edges,
            )
            out = await self.deps.llm.complete(
                [{"role": "user", "content": rendered}],
                temperature=0.3,
            )
            try:
                obj = extract_json(out) or {}
            except Exception:
                obj = {}

            glossary = obj.get("glossary") or []
            metrics = obj.get("metrics") or []
            overview = obj.get("overview_md") or ""

            n_terms = 0
            for g in glossary:
                term = str(g.get("term") or "").strip()
                definition = str(g.get("definition") or "").strip()
                if not term or not definition:
                    continue
                syn = [str(s).strip() for s in (g.get("synonyms") or []) if str(s).strip()]
                await self.deps.semantic_repo.upsert_glossary_term(
                    source_id=self.source_id,
                    term=term,
                    definition=definition,
                    synonyms=syn,
                )
                n_terms += 1

            n_metrics = 0
            for m in metrics:
                name = str(m.get("name") or "").strip()
                expr = str(m.get("expression") or "").strip()
                if not name or not expr:
                    continue
                await self.deps.semantic_repo.upsert_metric(
                    source_id=self.source_id,
                    name=name,
                    expression=expr,
                    unit=(str(m.get("unit")).strip() or None) if m.get("unit") else None,
                    description=(str(m.get("description")).strip() or None) if m.get("description") else None,
                )
                n_metrics += 1

            # Source-level overview note (embedded → retrievable by the client).
            note_body = overview or _fallback_overview(table_blocks, edges)
            note_id = await self.deps.notes_repo.upsert_note(
                source_id=self.source_id,
                scope="free",
                target_id=self.source_id,
                title="Обзор источника данных",
                body_md=note_body,
                tags=["overview"],
            )
            try:
                emb = await self.deps.embeddings.embed(note_body)
                await self.deps.notes_repo.set_embedding(note_id, emb)
            except Exception:  # noqa: BLE001
                logger.exception("source overview embedding failed")

            await run.emit(
                step_progress(
                    self.step_id,
                    1.0,
                    f"Готово: {n_terms} терминов, {n_metrics} метрик, обзор источника",
                )
            )
            logger.info(
                "profiling.synthesize: done",
                source_id=str(self.source_id),
                terms=n_terms,
                metrics=n_metrics,
                edges=len(edges),
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Synthesis is best-effort enrichment — never fail the whole run.
            logger.exception("profiling.synthesize: failed", source_id=str(self.source_id))


def _fallback_overview(
    tables: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> str:
    parts = ["# Обзор источника данных", "", "## Таблицы"]
    for t in tables:
        parts.append(
            f"- `{t['qname']}` — {t.get('title') or ''}"
            f" ({t.get('domain') or '—'}, rows≈{t.get('total_rows')})"
        )
    if edges:
        parts += ["", "## Связи"]
        for e in edges:
            parts.append(
                f"- `{e['from']}` → `{e['to']}`"
                + (f" ({e['cardinality']})" if e.get("cardinality") else "")
            )
    return "\n".join(parts)


def build_profiling_pipeline(deps: ProfilingDeps, source_id: UUID, run_id: UUID) -> Pipeline:
    return Pipeline(
        [
            _ProfileEverythingStep(deps, source_id, run_id),
            _SynthesizeSourceStep(deps, source_id),
        ]
    )
