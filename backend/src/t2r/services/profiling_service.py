from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from neo4j import AsyncDriver
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.agents.admin_profiling.pipeline import ProfilingDeps, build_profiling_pipeline
from t2r.agents.orchestrator.registry import RunRegistry
from t2r.agents.orchestrator.run import AgentRun
from t2r.infra.clickhouse.factory import CHClientFactory
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.profiling_repo_pg import ProfilingRepoPg
from t2r.infra.db.repos.profiling_task_repo_pg import ProfilingTaskRepo
from t2r.infra.db.repos.selection_repo_pg import SelectionRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.infra.security.cipher import FernetCipher
from t2r.logging import get_logger

logger = get_logger("profiling_service")

# Cap on the human glossary injected into profiling prompts (per describe call),
# matching the client agent's budget — guards against a huge paste blowing the
# context window across many per-table/per-group calls.
GLOSSARY_CHAR_CAP = 40_000


class ProfilingService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        cipher: FernetCipher,
        neo4j_driver: AsyncDriver,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        prompts: PromptLoader,
        registry: RunRegistry,
    ) -> None:
        self.sm = sessionmaker
        self.cipher = cipher
        self.neo4j = neo4j_driver
        self.llm = llm
        self.embeddings = embeddings
        self.prompts = prompts
        self.registry = registry
        # Strong refs to fire-and-forget resume tasks. asyncio.create_task only
        # holds a weak reference, so without this the GC may collect a resume
        # mid-flight. Discarded on completion to avoid unbounded growth.
        self._bg_tasks: set[asyncio.Task[None]] = set()
        # One lock per run serialises resume paths (gate continue / answer
        # continue), so a double-submit can't spawn two concurrent pass-2 drains
        # on the same run. Safe to hold on the service since it's an APP singleton.
        self._resume_locks: dict[str, asyncio.Lock] = {}

    def _spawn(self, coro) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _resume_lock(self, run_id_db: UUID) -> asyncio.Lock:
        key = str(run_id_db)
        lock = self._resume_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._resume_locks[key] = lock
        return lock

    async def get_active(self, source_id: UUID) -> dict | None:
        """Return the active DB run for a source (or None) + whether the
        in-memory AgentRun still exists.
        """
        async with self.sm() as s:
            active = await ProfilingRepoPg(s).get_active(source_id)
        if not active:
            return None
        agent_run_id = (active.get("params") or {}).get("agent_run_id")
        attached = False
        if agent_run_id:
            attached = await self.registry.get(agent_run_id) is not None
        return {
            "run_id": active["id"],
            "status": active["status"],
            "started_at": active.get("started_at"),
            "agent_run_id": agent_run_id,
            "attached": attached,
        }

    async def start(
        self,
        source_id: UUID,
        *,
        requested_by: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[UUID, str, bool]:
        """Idempotent start.

        Returns (db_run_id, agent_run_id, reused).
        - If an active run exists in DB and its AgentRun is still in the
          in-memory registry → reuse it, reused=True.
        - If an active run exists in DB but the AgentRun is gone (e.g. after a
          backend restart) → mark it abandoned and start a fresh one.
        - On unique-violation race (two concurrent starts), re-read and reuse.
        """
        params = params or {}
        logger.info(
            "profiling.start: requested",
            source_id=str(source_id),
            requested_by=requested_by,
            params=params,
        )
        async with self.sm() as s:
            selection = await SelectionRepoPg(s).get(source_id)
            if not selection:
                from t2r.errors import ValidationError

                raise ValidationError(
                    "Сначала выберите таблицы для индексации в источнике"
                )

            existing = await ProfilingRepoPg(s).get_active(source_id)
            if existing:
                existing_agent_id = (existing.get("params") or {}).get("agent_run_id")
                if existing_agent_id and await self.registry.get(existing_agent_id):
                    logger.info(
                        "profiling.start.reused",
                        source_id=str(source_id),
                        run_id=str(existing["id"]),
                        agent_run_id=existing_agent_id,
                    )
                    return existing["id"], existing_agent_id, True
                # DB says active, but the worker is gone — release the slot.
                logger.warning(
                    "profiling.start.releasing_abandoned",
                    source_id=str(source_id),
                    run_id=str(existing["id"]),
                )
                await ProfilingRepoPg(s).mark_abandoned(
                    existing["id"], reason="abandoned_before_restart"
                )
                await SourceRepoPg(s, self.cipher).sync_profiling_status_from_runs(
                    source_id
                )
                await s.commit()

        whitelist = [(r["database"], r["table_name"]) for r in selection]

        agent_run = AgentRun(kind="profiling")
        # The agent_run_id is recorded inside params so we can correlate the
        # in-memory worker with the DB row across restarts and reused starts.
        params_with_agent = {**params, "agent_run_id": agent_run.id}

        async with self.sm() as s:
            try:
                run_id_db = await ProfilingRepoPg(s).create_run(
                    source_id, requested_by=requested_by, params=params_with_agent
                )
                await SourceRepoPg(s, self.cipher).set_profiling_status(
                    source_id, status="in_progress", run_id=run_id_db
                )
                await s.commit()
            except IntegrityError:
                await s.rollback()
                # Lost the race: another start() created an active run for us.
                active = await ProfilingRepoPg(s).get_active(source_id)
                if active:
                    existing_agent_id = (active.get("params") or {}).get("agent_run_id")
                    if existing_agent_id and await self.registry.get(existing_agent_id):
                        return active["id"], existing_agent_id, True
                # Race + no live worker — surface the conflict instead of
                # silently starting a second pipeline.
                from t2r.errors import ValidationError

                raise ValidationError(
                    "Профилирование для этого источника уже запущено. "
                    "Обновите страницу, чтобы увидеть текущий запуск."
                )

        agent_run.context.update(
            params=params_with_agent,
            source_id=str(source_id),
            pg_run_id=str(run_id_db),
            whitelist=whitelist,
        )
        await self.registry.add(agent_run)
        task = asyncio.create_task(self._run_pipeline_v2(source_id, run_id_db, agent_run))
        agent_run.attach_task(task)
        return run_id_db, agent_run.id, False

    async def _run_pipeline(
        self, source_id: UUID, run_id_db: UUID, agent_run: AgentRun
    ) -> None:
        error_message: str | None = None
        cancelled = False
        logger.info(
            "profiling._run_pipeline: started",
            run_id=str(run_id_db),
            agent_run_id=agent_run.id,
            source_id=str(source_id),
        )
        try:
            async with self.sm() as session:
                source_repo = SourceRepoPg(session, self.cipher)
                ch_factory = CHClientFactory(source_repo)
                deps = ProfilingDeps(
                    ch_factory=ch_factory,
                    profiling_repo=ProfilingRepoPg(session),
                    semantic_repo=SemanticRepoPg(session),
                    notes_repo=NotesRepoPg(session),
                    graph_repo=GraphRepoNeo4j(self.neo4j),
                    session=session,
                    llm=self.llm,
                    embeddings=self.embeddings,
                    prompts=self.prompts,
                )
                pipeline = build_profiling_pipeline(deps, source_id, run_id_db)

                # The pipeline commits its own progress per-table on this same
                # coroutine. We deliberately do NOT run a background committer:
                # AsyncSession / its asyncpg connection cannot service two
                # concurrent operations, and a periodic committer firing while a
                # step's execute() is in flight raised intermittent
                # "another operation is in progress" errors and could roll back
                # uncommitted table writes.
                await pipeline.run(agent_run)
                await session.commit()
                logger.info(
                    "profiling._run_pipeline: pipeline finished cleanly",
                    run_id=str(run_id_db),
                )
        except asyncio.CancelledError:
            cancelled = True
            logger.warning(
                "profiling._run_pipeline: CancelledError caught — flagging run cancelled",
                run_id=str(run_id_db),
                agent_run_id=agent_run.id,
            )
            try:
                async with self.sm() as s:
                    await ProfilingRepoPg(s).set_status(
                        run_id_db, "cancelled", error="cancelled_by_user"
                    )
                    await s.commit()
            except Exception:
                logger.exception("profiling._run_pipeline: failed to write 'cancelled' status")
            # Don't re-raise — the task was cancelled deliberately and we still
            # want the finally block below to run the denormalized status sync.
        except Exception as exc:  # noqa: BLE001
            logger.exception("profiling._run_pipeline: pipeline failed", run_id=str(run_id_db))
            error_message = str(exc)
            try:
                async with self.sm() as s:
                    await ProfilingRepoPg(s).set_status(
                        run_id_db, "failed", error=error_message
                    )
                    await s.commit()
            except Exception:
                logger.exception("profiling._run_pipeline: failed to write 'failed' status")
        finally:
            # Sync denormalized status from whichever final state the run row
            # ended up in (done/failed/cancelled — set by the pipeline or the
            # except branch above).
            try:
                async with self.sm() as s:
                    await SourceRepoPg(s, self.cipher).sync_profiling_status_from_runs(
                        source_id
                    )
                    await s.commit()
            except Exception:
                logger.exception("failed to sync source profiling status")
            if error_message and not agent_run.is_finished:
                await agent_run.finalize(error=error_message)
            logger.info(
                "profiling._run_pipeline: exited",
                run_id=str(run_id_db),
                agent_run_id=agent_run.id,
                cancelled=cancelled,
                error=error_message,
                run_state=agent_run.state,
            )

    # ── v2: two-pass, task-based, with a non-blocking question inbox ────────

    async def _run_pipeline_v2(
        self, source_id: UUID, run_id_db: UUID, agent_run: AgentRun
    ) -> None:
        import time

        from t2r.agents.admin_profiling.pass1 import Pass1Deps, run_pass1
        from t2r.agents.admin_profiling.pass2 import Pass2Deps, run_pass2
        from t2r.domain.events.types import step_completed, step_progress, step_started

        try:
            async with self.sm() as s:
                await ProfilingRepoPg(s).set_status(run_id_db, "running")
                await s.commit()

            whitelist = [tuple(w) for w in (agent_run.context.get("whitelist") or [])]

            # Pass 1 — dry structural harvest + relations (all columns).
            t0 = time.time()
            await agent_run.emit(step_started("harvest", "Сбор структуры и связей"))
            await run_pass1(
                Pass1Deps(self.sm, self.cipher, self.neo4j),
                run_id=run_id_db, source_id=source_id, whitelist=whitelist,
            )
            await agent_run.emit(step_completed("harvest", int((time.time() - t0) * 1000)))

            # ── Column-selection gate ──────────────────────────────────────
            # Pause after the dry harvest so the admin can drop columns from the
            # deep investigation (LLM describe / notes / synthesis only run over
            # what survives). Resumed via apply_column_selection → _continue_after_gate.
            # `paused` is an active status (so the slot stays held); restart marks
            # it abandoned like any other active run.
            async with self.sm() as s:
                await ProfilingRepoPg(s).set_status(run_id_db, "paused")
                await s.commit()
            await self._sync_status(source_id)
            await agent_run.emit(
                step_started("gate", "Сбор завершён — выберите колонки для исследования")
            )
            logger.info("v2: paused at column-selection gate", run_id=str(run_id_db))
            return
        except asyncio.CancelledError:
            try:
                async with self.sm() as s:
                    await ProfilingRepoPg(s).set_status(run_id_db, "cancelled", error="cancelled_by_user")
                    await s.commit()
            except Exception:
                logger.exception("v2: cancel persist failed")
        except Exception as exc:  # noqa: BLE001
            logger.exception("v2: pipeline failed", run_id=str(run_id_db))
            try:
                async with self.sm() as s:
                    await ProfilingRepoPg(s).set_status(run_id_db, "failed", error=str(exc))
                    await s.commit()
            except Exception:
                logger.exception("v2: fail persist failed")
            if not agent_run.is_finished:
                await agent_run.finalize(error=str(exc))
        finally:
            await self._sync_status(source_id)

    async def _maybe_finish(
        self, source_id: UUID, run_id_db: UUID, agent_run: AgentRun | None
    ) -> None:
        """Either park the run (questions pending) or finalize it (synthesize + done)."""
        async with self.sm() as s:
            run = await ProfilingRepoPg(s).get_run(run_id_db)
            counts = await ProfilingTaskRepo(s).counts(run_id_db)
            coverage = await ProfilingTaskRepo(s).coverage(run_id_db)
        # Idempotency: a second drain (double resume) or a late answer on an
        # already-terminal run must not re-run the expensive finalize/synthesize.
        if run and run["status"] in ("done", "failed", "cancelled"):
            logger.info("v2: maybe_finish on terminal run, skip", run_id=str(run_id_db), status=run["status"])
            return
        if counts.get("awaiting_input", 0) > 0:
            logger.info("v2: parked on questions", run_id=str(run_id_db), n=counts["awaiting_input"])
            return

        # Honest final status: if some task failed permanently (its dependents
        # then never run and stay pending) or coverage is incomplete, don't claim
        # a clean success — finalize as 'failed' with a clear reason, while still
        # writing notes/graph/synthesis for whatever DID complete.
        failed = counts.get("failed", 0)
        pending_left = counts.get("pending", 0) + counts.get("running", 0)
        incomplete = failed > 0 or pending_left > 0 or not coverage.get("complete", False)
        final_error: str | None = None
        if incomplete:
            final_error = (
                f"Профилирование завершено частично: failed={failed}, "
                f"незавершённых задач={pending_left}, колонок без описания="
                f"{len(coverage.get('missing') or [])}."
            )

        try:
            await self._write_notes(source_id)
        except Exception:
            logger.exception("v2: notes write failed (non-fatal)", run_id=str(run_id_db))

        # Mirror the final (enabled-only) layer to the graph. Pass 1 upserts a
        # node for EVERY harvested column, so columns dropped at the gate must be
        # pruned here — resync does both (upsert enabled + prune the rest).
        try:
            await self._resync_graph(source_id)
        except Exception:
            logger.exception("v2: graph resync failed (non-fatal)", run_id=str(run_id_db))

        try:
            await self._synthesize_source(source_id, run_id_db)
        except Exception:
            logger.exception("v2: synthesize failed (non-fatal)", run_id=str(run_id_db))
        async with self.sm() as s:
            await ProfilingRepoPg(s).set_status(
                run_id_db, "failed" if incomplete else "done", error=final_error
            )
            await s.commit()
        await self._sync_status(source_id)
        if agent_run is not None and not agent_run.is_finished:
            await agent_run.finalize(error=final_error)
        logger.info(
            "v2: run finished",
            run_id=str(run_id_db),
            status="failed" if incomplete else "done",
            error=final_error,
        )

    async def answer_question(self, task_id: UUID, answers: list[dict]) -> dict:
        """Store the admin's answers on a parked describe task, re-queue it, and
        resume the run in the background."""
        async with self.sm() as s:
            repo = ProfilingTaskRepo(s)
            task = await repo.get(task_id)
            if not task:
                from t2r.errors import NotFoundError

                raise NotFoundError("Задача не найдена")
            run_id_db = task["run_id"]
            source_id = task["source_id"]
            # Don't reopen a finished run: answering a stale question on a
            # done/failed/cancelled run would re-run the describe + finalize.
            run = await ProfilingRepoPg(s).get_run(run_id_db)
            if run and run["status"] in ("done", "failed", "cancelled"):
                from t2r.errors import ValidationError

                raise ValidationError("Этот запуск уже завершён — ответ не требуется")
            payload = dict(task.get("payload") or {})
            payload["answers"] = answers
            await repo.set_status(task_id, "pending", payload=payload)
            await s.commit()

        self._spawn(self._continue(source_id, run_id_db))
        return {"ok": True, "run_id": str(run_id_db)}

    async def _continue(self, source_id: UUID, run_id_db: UUID) -> None:
        from t2r.agents.admin_profiling.pass2 import Pass2Deps, continue_pass2

        # Serialise resumes of the same run so a double-answer can't drive two
        # concurrent pass-2 drains + double finalize.
        async with self._resume_lock(run_id_db):
            agent_run = await self._agent_run_for(run_id_db)
            # Wire cancellation: attach the running task so run.cancel() can abort
            # an in-flight resume, on par with start()/_run_pipeline_v2.
            if agent_run is not None:
                agent_run.attach_task(asyncio.current_task())  # type: ignore[arg-type]
            glossary = await self._load_glossary(source_id)
            try:
                await continue_pass2(
                    Pass2Deps(self.sm, self.llm, self.prompts, glossary),
                    run_id=run_id_db, source_id=source_id,
                )
                await self._maybe_finish(source_id, run_id_db, agent_run)
            except Exception as exc:  # noqa: BLE001
                # Don't leave the run wedged in 'running' forever — mark it failed
                # and resync, mirroring _continue_after_gate.
                logger.exception("v2: continue failed", run_id=str(run_id_db))
                try:
                    async with self.sm() as s:
                        await ProfilingRepoPg(s).set_status(
                            run_id_db, "failed", error=str(exc)
                        )
                        await s.commit()
                    await self._sync_status(source_id)
                except Exception:
                    logger.exception("v2: continue fail-persist failed", run_id=str(run_id_db))
                if agent_run is not None and not agent_run.is_finished:
                    await agent_run.finalize(error=str(exc))

    async def unpark_after_disable(
        self, table_id: UUID, disabled_names: list[str]
    ) -> None:
        """Clean parked describe tasks after columns are disabled mid-run.

        Disabling a column via the table UI while pass-2 is parked on a question
        about it would otherwise (a) leave a dead question in the inbox and (b)
        wedge the run forever. For each parked describe task of this table we drop
        questions about now-disabled columns; a task left with none is re-queued
        (pending) and the run resumed, so it finalizes with a best-effort
        description instead of waiting on an unanswerable question. No-op unless
        an active (running/paused) run exists for the column's source.
        """
        disabled = {str(n) for n in (disabled_names or [])}
        if not disabled:
            return
        run_id_db: UUID | None = None
        source_id: UUID | None = None
        requeued = 0
        async with self.sm() as s:
            semantic = SemanticRepoPg(s)
            table = await semantic.get_table(table_id)
            if not table:
                return
            source_id = table["source_id"]
            active = await ProfilingRepoPg(s).get_active(source_id)
            if not active or active["status"] not in ("running", "paused"):
                return
            run_id_db = active["id"]
            repo = ProfilingTaskRepo(s)
            parked = await repo.list_by_run(
                run_id_db, kind="describe_group", status="awaiting_input"
            )
            for t in parked:
                if (t["database"], t["table_name"]) != (
                    table["database"],
                    table["table_name"],
                ):
                    continue
                payload = dict(t.get("payload") or {})
                qs = payload.get("questions") or []
                kept = [
                    q
                    for q in qs
                    if isinstance(q, dict) and q.get("column") not in disabled
                ]
                if len(kept) == len(qs):
                    continue  # this task asked about nothing that got disabled
                payload["questions"] = kept
                if kept:
                    await repo.set_status(t["id"], "awaiting_input", payload=payload)
                else:
                    await repo.set_status(t["id"], "pending", payload=payload)
                    requeued += 1
            await s.commit()
        if requeued and source_id is not None and run_id_db is not None:
            logger.info(
                "v2: unparked tasks after column disable",
                run_id=str(run_id_db),
                requeued=requeued,
            )
            self._spawn(self._continue(source_id, run_id_db))

    async def get_column_selection(self, run_id_db: UUID) -> dict:
        """The dry-harvest snapshot the admin reviews at the gate: every table
        with its columns + the facts that inform a keep/drop decision."""
        async with self.sm() as s:
            run = await ProfilingRepoPg(s).get_run(run_id_db)
            if not run:
                from t2r.errors import NotFoundError

                raise NotFoundError("Запуск не найден")
            source_id = run["source_id"]
            repo = SemanticRepoPg(s)
            tables = await repo.list_tables(source_id)
            out = []
            for t in tables:
                cols = await repo.get_columns(t["id"])
                out.append(
                    {
                        "table_id": str(t["id"]),
                        "qname": f"{t['database']}.{t['table_name']}",
                        "title": t.get("title"),
                        "total_rows": t.get("total_rows"),
                        "columns": [
                            {
                                "name": c["name"],
                                "data_type": c["data_type"],
                                "semantic_role": c.get("semantic_role"),
                                "distinct_count": c.get("distinct_count"),
                                "null_ratio": (
                                    float(c["null_ratio"])
                                    if c.get("null_ratio") is not None
                                    else None
                                ),
                                "examples": c.get("examples"),
                                "enabled": c.get("enabled", True),
                            }
                            for c in cols
                        ],
                    }
                )
        return {"status": run["status"], "tables": out}

    async def apply_column_selection(
        self, run_id_db: UUID, disabled: list[dict]
    ) -> dict:
        """Record the columns to exclude (per table) and resume the run into
        pass 2 over what remains. Submitting an empty list proceeds with every
        column kept."""
        async with self.sm() as s:
            prepo = ProfilingRepoPg(s)
            run = await prepo.get_run(run_id_db)
            if not run:
                from t2r.errors import NotFoundError

                raise NotFoundError("Запуск не найден")
            # Atomically claim the gate transition (paused -> running). Only one
            # caller wins; a double-submit (double click / retried request) gets
            # None here and is rejected, so we never spawn two pass-2 drains.
            source_id = await prepo.try_begin_from_paused(run_id_db)
            if source_id is None:
                from t2r.errors import ValidationError

                raise ValidationError(
                    "Этот запуск не ожидает выбора колонок (уже продолжен или завершён)"
                )
            repo = SemanticRepoPg(s)
            total = 0
            for item in disabled:
                names = item.get("names") or []
                if names:
                    total += await repo.set_columns_enabled(
                        UUID(str(item["table_id"])), names, False
                    )
            await s.commit()

        logger.info(
            "v2: column selection applied", run_id=str(run_id_db), disabled=total
        )
        self._spawn(self._continue_after_gate(source_id, run_id_db))
        return {"ok": True, "disabled": total, "run_id": str(run_id_db)}

    async def _continue_after_gate(self, source_id: UUID, run_id_db: UUID) -> None:
        import time

        from t2r.agents.admin_profiling.pass2 import Pass2Deps, run_pass2
        from t2r.domain.events.types import step_completed, step_started

        async with self._resume_lock(run_id_db):
            agent_run = await self._agent_run_for(run_id_db)
            if agent_run is not None:
                agent_run.attach_task(asyncio.current_task())  # type: ignore[arg-type]
            glossary = await self._load_glossary(source_id)
            try:
                # try_begin_from_paused already flipped to 'running'; keep this for
                # the legacy non-gate path and as a harmless idempotent re-set.
                async with self.sm() as s:
                    await ProfilingRepoPg(s).set_status(run_id_db, "running")
                    await s.commit()
                await self._sync_status(source_id)

                t1 = time.time()
                if agent_run is not None:
                    await agent_run.emit(step_started("describe", "Профилирую колонки"))
                await run_pass2(
                    Pass2Deps(self.sm, self.llm, self.prompts, glossary),
                    run_id=run_id_db, source_id=source_id,
                )
                if agent_run is not None:
                    await agent_run.emit(
                        step_completed("describe", int((time.time() - t1) * 1000))
                    )
                await self._maybe_finish(source_id, run_id_db, agent_run)
            except Exception as exc:  # noqa: BLE001
                logger.exception("v2: continue_after_gate failed", run_id=str(run_id_db))
                try:
                    async with self.sm() as s:
                        await ProfilingRepoPg(s).set_status(
                            run_id_db, "failed", error=str(exc)
                        )
                        await s.commit()
                except Exception:
                    logger.exception("v2: gate-resume fail persist failed")
                await self._sync_status(source_id)
                if agent_run is not None and not agent_run.is_finished:
                    await agent_run.finalize(error=str(exc))

    async def _load_glossary(self, source_id: UUID) -> str:
        """The source's human glossary text (capped), fed to profiling prompts as
        authoritative domain context. Empty string if none set."""
        from sqlalchemy import text

        async with self.sm() as s:
            row = (
                await s.execute(
                    text("SELECT glossary_md FROM data_sources WHERE id = :id"),
                    {"id": source_id},
                )
            ).first()
        glossary = ((row[0] if row else None) or "").strip()
        if len(glossary) > GLOSSARY_CHAR_CAP:
            glossary = glossary[:GLOSSARY_CHAR_CAP] + "\n…[глоссарий обрезан по лимиту]"
        return glossary

    async def _resync_graph(self, source_id: UUID) -> None:
        from t2r.infra.graph.sync import try_resync_source_graph

        async with self.sm() as session:
            await try_resync_source_graph(
                SemanticRepoPg(session), GraphRepoNeo4j(self.neo4j), source_id
            )

    async def _agent_run_for(self, run_id_db: UUID) -> AgentRun | None:
        async with self.sm() as s:
            run = await ProfilingRepoPg(s).get_run(run_id_db)
        agent_run_id = (run or {}).get("params", {}).get("agent_run_id") if run else None
        if not agent_run_id:
            return None
        return await self.registry.get(agent_run_id)

    async def _sync_status(self, source_id: UUID) -> None:
        try:
            async with self.sm() as s:
                await SourceRepoPg(s, self.cipher).sync_profiling_status_from_runs(source_id)
                await s.commit()
        except Exception:
            logger.exception("v2: source status sync failed")

    async def reprofile_column(self, column_id: UUID, *, actor: str) -> dict:
        """Deep-(re)profile a single column: ensure it's enabled, run the LLM
        describer over its harvested facts, then rebuild the table's RAG notes
        and resync the graph. Used to fill in a column that was excluded before
        pass 2 (facts but no description) when it's re-included."""
        from t2r.agents.admin_profiling.describe import describe_columns
        from t2r.agents.admin_profiling.note_writer import rebuild_table_notes
        from t2r.infra.graph.sync import try_resync_source_graph

        async with self.sm() as session:
            semantic = SemanticRepoPg(session)
            col = await semantic.get_column(column_id)
            if not col:
                from t2r.errors import NotFoundError

                raise NotFoundError("Колонка не найдена")
            source_id = col["source_id"]
            table_id = col["table_id"]
            # A locked column is protected from re-profiling (apply_column_description
            # filters on locked=false), so calling the LLM would burn tokens and
            # update nothing. Bail out clearly instead of pretending it ran.
            if col.get("locked"):
                return {
                    "column_id": str(column_id),
                    "described": 0,
                    "skipped": "locked",
                }
            if not col.get("enabled", True):
                await semantic.set_column_enabled(column_id, True)
            described = await describe_columns(
                llm=self.llm,
                prompts=self.prompts,
                semantic=semantic,
                table_id=table_id,
                names=[col["name"]],
            )
            # Rebuild this table's notes from the (now-described) enabled columns.
            full = next(
                (t for t in await semantic.list_tables(source_id) if t["id"] == table_id),
                None,
            )
            if full:
                cols = await semantic.get_columns(table_id, only_enabled=True)
                await rebuild_table_notes(
                    notes_repo=NotesRepoPg(session),
                    embeddings=self.embeddings,
                    source_id=source_id,
                    table=full,
                    columns=cols,
                )
            await session.commit()
            try:
                await try_resync_source_graph(
                    semantic, GraphRepoNeo4j(self.neo4j), source_id
                )
            except Exception:
                logger.exception("reprofile_column: graph resync failed")
            return {"column_id": str(column_id), "described": described}

    async def _write_notes(self, source_id: UUID) -> None:
        """Build the per-table / per-column RAG substrate (md_notes + embeddings)
        from the persisted semantic layer, over enabled columns only."""
        from t2r.agents.admin_profiling.note_writer import write_source_notes

        async with self.sm() as session:
            await write_source_notes(
                semantic_repo=SemanticRepoPg(session),
                notes_repo=NotesRepoPg(session),
                embeddings=self.embeddings,
                source_id=source_id,
            )
            await session.commit()

    async def _synthesize_source(self, source_id: UUID, run_id_db: UUID) -> None:
        """Source-level glossary + metrics + overview note (analyst layer)."""
        from t2r.infra.llm.json_extractor import extract_json

        async with self.sm() as session:
            semantic = SemanticRepoPg(session)
            notes = NotesRepoPg(session)
            tables = await semantic.list_tables(source_id)
            if not tables:
                return
            id_to_qname = {str(t["id"]): f"{t['database']}.{t['table_name']}" for t in tables}
            table_blocks = []
            for t in tables:
                # Disabled columns must not leak into the source overview note.
                cols = await semantic.get_columns(t["id"], only_enabled=True)
                key_cols = [
                    {"name": c["name"], "role": c.get("semantic_role"),
                     "key": bool(c.get("is_in_primary_key") or c.get("is_in_sorting_key"))}
                    for c in cols
                    if c.get("is_in_primary_key") or c.get("is_in_sorting_key")
                    or c.get("semantic_role") in ("id", "fk", "measure", "timestamp")
                ][:20]
                table_blocks.append({
                    "qname": f"{t['database']}.{t['table_name']}", "title": t.get("title"),
                    "domain": t.get("domain"), "grain": t.get("grain"),
                    "total_rows": t.get("total_rows"), "columns": key_cols,
                })
            relations = await semantic.get_relations(source_id, only_enabled=True)
            edges = [
                {"from": id_to_qname.get(str(r["from_table_id"]), "?"),
                 "to": id_to_qname.get(str(r["to_table_id"]), "?"),
                 "cardinality": r.get("cardinality"),
                 "confidence": float(r["confidence"]) if r.get("confidence") is not None else None}
                for r in relations
            ]
            glossary = await self._load_glossary(source_id)
            rendered = self.prompts.render(
                "source_synthesizer", tables=table_blocks, edges=edges, glossary=glossary
            )
            out = await self.llm.complete([{"role": "user", "content": rendered}], temperature=0.3)
            try:
                obj = extract_json(out) or {}
            except Exception:
                obj = {}

            for g in obj.get("glossary") or []:
                term = str(g.get("term") or "").strip()
                definition = str(g.get("definition") or "").strip()
                if term and definition:
                    syn = [str(s).strip() for s in (g.get("synonyms") or []) if str(s).strip()]
                    await semantic.upsert_glossary_term(
                        source_id=source_id, term=term, definition=definition, synonyms=syn
                    )
            for m in obj.get("metrics") or []:
                name = str(m.get("name") or "").strip()
                expr = str(m.get("expression") or "").strip()
                if name and expr:
                    await semantic.upsert_metric(
                        source_id=source_id, name=name, expression=expr,
                        unit=(str(m.get("unit")).strip() or None) if m.get("unit") else None,
                        description=(str(m.get("description")).strip() or None) if m.get("description") else None,
                    )
            overview = obj.get("overview_md") or ""
            if overview:
                note_id = await notes.upsert_note(
                    source_id=source_id, scope="free", target_id=source_id,
                    title="Обзор источника данных", body_md=overview, tags=["overview"],
                )
                try:
                    emb = await self.embeddings.embed(overview)
                    await notes.set_embedding(note_id, emb)
                except Exception:
                    logger.exception("v2: source overview embedding failed")
            await session.commit()

    async def get_progress(self, run_id_db: UUID) -> dict:
        async with self.sm() as s:
            repo = ProfilingTaskRepo(s)
            counts = await repo.counts(run_id_db)
            coverage = await repo.coverage(run_id_db)
            tasks = await repo.board(run_id_db)
            awaiting = await repo.list_by_run(
                run_id_db, kind="describe_group", status="awaiting_input"
            )
            run = await ProfilingRepoPg(s).get_run(run_id_db)
            # Surface only questions about currently-enabled columns: a column
            # disabled after its question was parked must drop out of the inbox.
            enabled_by_table: dict[tuple[str, str], set[str]] = {}
            if awaiting and run:
                semantic = SemanticRepoPg(s)
                for t in await semantic.list_tables(run["source_id"]):
                    cols = await semantic.get_columns(t["id"], only_enabled=True)
                    enabled_by_table[(t["database"], t["table_name"])] = {
                        c["name"] for c in cols
                    }
        questions = []
        for t in awaiting:
            enabled = enabled_by_table.get((t["database"], t["table_name"]))
            qs = (t.get("payload") or {}).get("questions") or []
            if enabled is not None:
                qs = [
                    q for q in qs
                    if isinstance(q, dict) and q.get("column") in enabled
                ]
            if not qs:
                continue  # nothing answerable left — don't show an empty card
            questions.append(
                {
                    "task_id": str(t["id"]),
                    "database": t["database"],
                    "table": t["table_name"],
                    "questions": qs,
                }
            )
        return {
            "status": (run or {}).get("status"),
            "agent_run_id": (run or {}).get("params", {}).get("agent_run_id") if run else None,
            "counts": counts,
            "coverage": coverage,
            "questions": questions,
            "tasks": tasks,
        }

    async def get_run(self, run_id: UUID) -> dict | None:
        async with self.sm() as s:
            return await ProfilingRepoPg(s).get_run(run_id)

    async def list_runs(self, source_id: UUID) -> list[dict]:
        async with self.sm() as s:
            return await ProfilingRepoPg(s).list_runs(source_id)

    async def get_run_tables(self, run_id: UUID) -> list[dict]:
        async with self.sm() as s:
            return await ProfilingRepoPg(s).get_run_tables(run_id)
