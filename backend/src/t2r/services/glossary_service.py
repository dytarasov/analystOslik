from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from t2r.domain.models.source import GlossaryIngestResult
from t2r.errors import NotFoundError, UpstreamError
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.graph.sync import try_resync_source_graph
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.json_extractor import extract_json
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.logging import get_logger

logger = get_logger("glossary.ingest")


def _parse(raw: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        try:
            value = extract_json(raw)
        except ValueError as exc:
            raise UpstreamError("LLM вернул неразборчивый JSON для глоссария") from exc
    if not isinstance(value, dict):
        raise UpstreamError("LLM вернул не объект для глоссария")
    return value


class GlossaryService:
    """Structurally ingests a source's human-authored glossary into the
    semantic layer: glossary terms, metrics (with verbatim reference SQL),
    retrievable notes (rules + gold SQL examples, embedded) and best-effort
    join relations. Phase-1 prompt injection happens separately in the agent
    loop; this is the Phase-2 retrieval enrichment.
    """

    def __init__(
        self,
        *,
        source_repo: SourceRepoPg,
        semantic_repo: SemanticRepoPg,
        notes_repo: NotesRepoPg,
        graph_repo: GraphRepoNeo4j,
        embeddings: EmbeddingsClient,
        llm: LLMClient,
        prompts: PromptLoader,
    ) -> None:
        self.source_repo = source_repo
        self.semantic_repo = semantic_repo
        self.notes_repo = notes_repo
        self.graph_repo = graph_repo
        self.embeddings = embeddings
        self.llm = llm
        self.prompts = prompts

    async def ingest(self, source_id: UUID) -> GlossaryIngestResult:
        src = await self.source_repo.get(source_id)
        if not src:
            raise NotFoundError("Источник не найден")
        glossary = (src.glossary_md or "").strip()
        if not glossary:
            return GlossaryIngestResult(ok=False, warnings=["Глоссарий пуст — нечего разбирать"])

        prompt = self.prompts.render("glossary_ingest", glossary=glossary)
        raw = await self.llm.complete(
            [{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        data = _parse(raw)
        warnings: list[str] = []

        # Full idempotency: wipe everything the previous glossary ingest wrote
        # (notes are cleared inside _ingest_notes) so this run fully replaces it,
        # leaving profiling-origin rows untouched. Counts then reflect only the
        # current glossary, and removed items don't linger as orphans.
        await self.semantic_repo.delete_glossary_semantic(source_id)
        await self.semantic_repo.reset_glossary_columns(source_id)

        terms = await self._ingest_terms(source_id, data.get("glossary_terms") or [])
        metrics = await self._ingest_metrics(source_id, data.get("metrics") or [])
        notes = await self._ingest_notes(source_id, data.get("notes") or [], warnings)
        columns = await self._ingest_columns(
            source_id, data.get("columns") or [], warnings
        )
        relations = await self._ingest_relations(
            source_id, data.get("relations") or [], warnings
        )

        await self.source_repo.set_glossary_ingested(source_id)
        # Commit PG before touching Neo4j: the graph is best-effort and PG is the
        # source of truth, so it must only ever be synced from durably persisted
        # state (never ahead of a transaction that could still roll back).
        await self.semantic_repo.session.commit()
        # New relations / column statuses must reach Neo4j for the agent's graph
        # tools. Best-effort: PG is the source of truth.
        await try_resync_source_graph(self.semantic_repo, self.graph_repo, source_id)
        logger.info(
            "glossary ingested",
            source_id=str(source_id),
            terms=terms,
            metrics=metrics,
            notes=notes,
            columns=columns,
            relations=relations,
        )
        return GlossaryIngestResult(
            ok=True,
            notes=notes,
            metrics=metrics,
            terms=terms,
            columns=columns,
            relations=relations,
            warnings=warnings,
        )

    # ──────────────────────────────────────────────────────────────────

    async def _ingest_terms(self, source_id: UUID, items: list[Any]) -> int:
        n = 0
        for t in items:
            if not isinstance(t, dict) or not (t.get("term") or "").strip():
                continue
            await self.semantic_repo.upsert_glossary_term(
                source_id=source_id,
                term=str(t["term"]).strip(),
                definition=str(t.get("definition") or "").strip(),
                synonyms=[str(s) for s in (t.get("synonyms") or []) if s],
                origin="glossary",
            )
            n += 1
        return n

    async def _ingest_metrics(self, source_id: UUID, items: list[Any]) -> int:
        n = 0
        for m in items:
            if not isinstance(m, dict):
                continue
            name = (m.get("name") or "").strip()
            expr = (m.get("expression") or "").strip()
            if not name or not expr:
                continue
            await self.semantic_repo.upsert_metric(
                source_id=source_id,
                name=name,
                expression=expr,
                unit=(m.get("unit") or None),
                description=(m.get("description") or None),
                origin="glossary",
            )
            n += 1
        return n

    async def _ingest_notes(
        self, source_id: UUID, items: list[Any], warnings: list[str]
    ) -> int:
        # Idempotent re-ingest: drop the previous glossary notes first.
        await self.notes_repo.delete_glossary_notes(source_id)

        ids: list[UUID] = []
        texts: list[str] = []
        for n in items:
            if not isinstance(n, dict):
                continue
            body = (n.get("body_md") or "").strip()
            if not body:
                continue
            kind = (n.get("kind") or "rule").strip() or "rule"
            tbl = (n.get("table") or "").strip() if n.get("table") else ""
            base_title = (n.get("title") or kind).strip()
            title = f"[{tbl}] {base_title}" if tbl else base_title
            tags = ["glossary", kind]
            if tbl:
                tags.append(tbl)
            for extra in n.get("tags") or []:
                if extra and str(extra) not in tags:
                    tags.append(str(extra))
            note_id = await self.notes_repo.insert_note(
                source_id=source_id,
                scope="free",
                target_id=None,
                title=title,
                body_md=body,
                tags=tags,
            )
            ids.append(note_id)
            texts.append(f"{title}\n{body}")

        if texts:
            try:
                vectors = await self.embeddings.embed_many(texts)
                for note_id, vec in zip(ids, vectors):
                    await self.notes_repo.set_embedding(note_id, vec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("glossary note embedding failed", error=str(exc))
                warnings.append(
                    "Заметки сохранены, но не проиндексированы (ошибка эмбеддингов) —"
                    " векторный поиск их пока не найдёт"
                )
        return len(ids)

    async def _ingest_columns(
        self, source_id: UUID, items: list[Any], warnings: list[str]
    ) -> int:
        """Push glossary field semantics into sem_columns so the agent sees them
        on get_table/get_columns — not only via RAG. Best-effort: only profiled
        columns can be enriched."""
        n = 0
        for c in items:
            if not isinstance(c, dict):
                continue
            tbl = str(c.get("table") or "")
            name = (c.get("name") or "").strip()
            if "." not in tbl or not name:
                continue
            db, tname = tbl.split(".", 1)
            table_id = await self.semantic_repo.find_table(source_id, db, tname)
            if not table_id:
                warnings.append(f"колонка пропущена (таблица не профилирована): {tbl}.{name}")
                continue
            column_id = await self.semantic_repo.find_column(table_id, name)
            if not column_id:
                warnings.append(f"колонка не найдена в схеме: {tbl}.{name}")
                continue

            patch: dict[str, Any] = {"source": "glossary"}
            vm = c.get("value_meanings")
            if isinstance(vm, dict) and vm:
                patch["value_meanings"] = {str(k): str(v) for k, v in vm.items()}
            caveats = (c.get("caveats") or "").strip() if c.get("caveats") else ""
            if caveats:
                patch["caveats"] = caveats
            examples = c.get("examples")
            examples = [str(e) for e in examples] if isinstance(examples, list) else None
            description = (c.get("description") or "").strip() or None

            # Snapshot the prior column state before clobbering its description,
            # so a hand-edited (or earlier) value stays recoverable via revisions.
            prev = await self.semantic_repo.get_column(column_id)
            if prev and description and (prev.get("description") or "") != description:
                await self.semantic_repo.add_revision(
                    entity_kind="sem_column",
                    entity_id=column_id,
                    payload={
                        k: prev.get(k)
                        for k in ("description", "semantic_role", "user_notes")
                    },
                    actor="glossary",
                    reason="glossary ingest",
                )

            await self.semantic_repo.apply_glossary_column(
                column_id,
                description=description,
                semantics_patch=patch,
                examples=examples,
            )
            n += 1
        return n

    async def _ingest_relations(
        self, source_id: UUID, items: list[Any], warnings: list[str]
    ) -> int:
        n = 0
        for r in items:
            if not isinstance(r, dict):
                continue
            ft, tt = str(r.get("from_table") or ""), str(r.get("to_table") or "")
            if "." not in ft or "." not in tt:
                continue
            fdb, fname = ft.split(".", 1)
            tdb, tname = tt.split(".", 1)
            from_tid = await self.semantic_repo.find_table(source_id, fdb, fname)
            to_tid = await self.semantic_repo.find_table(source_id, tdb, tname)
            if not from_tid or not to_tid:
                warnings.append(f"связь пропущена (таблица не профилирована): {ft} → {tt}")
                continue
            fc = (r.get("from_column") or "").strip() or None
            tc = (r.get("to_column") or "").strip() or None
            from_cid = await self.semantic_repo.find_column(from_tid, fc) if fc else None
            to_cid = await self.semantic_repo.find_column(to_tid, tc) if tc else None
            if (
                from_cid
                and to_cid
                and await self.semantic_repo.relation_exists(
                    from_column_id=from_cid, to_column_id=to_cid
                )
            ):
                continue
            await self.semantic_repo.insert_relation(
                source_id=source_id,
                from_table_id=from_tid,
                from_column_id=from_cid,
                to_table_id=to_tid,
                to_column_id=to_cid,
                kind="conceptual",
                confidence=0.9,
                reasoning=(r.get("reasoning") or "связь из глоссария источника"),
                origin="glossary",
            )
            n += 1
        return n
