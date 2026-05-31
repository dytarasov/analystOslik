from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from t2r.domain.models.source import SqlNotesIngestResult
from t2r.errors import NotFoundError, UpstreamError
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.db.repos.sql_recipe_repo_pg import SqlRecipeRepoPg
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.json_extractor import extract_json
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.logging import get_logger
from t2r.services.ingest_chunking import chunk_markdown

logger = get_logger("sql_notes.ingest")


def _parse(raw: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        try:
            value = extract_json(raw)
        except ValueError as exc:
            raise UpstreamError("LLM вернул неразборчивый JSON для SQL-заметок") from exc
    if not isinstance(value, dict):
        raise UpstreamError("LLM вернул не объект для SQL-заметок")
    return value


class SqlNotesService:
    """Parses a source's free-form SQL notes into discrete recipes
    {title, intent, sql, tables} and stores them in ``sql_recipes``.

    Only the natural-language ``intent`` is embedded (the retrieval key); the SQL
    is stored verbatim and never vectorized. Unlike the glossary, this needs no
    profiling — recipes are self-contained, ``tables`` is advisory.
    """

    def __init__(
        self,
        *,
        source_repo: SourceRepoPg,
        sql_recipe_repo: SqlRecipeRepoPg,
        embeddings: EmbeddingsClient,
        llm: LLMClient,
        prompts: PromptLoader,
        ingest_max_tokens: int = 8192,
    ) -> None:
        self.source_repo = source_repo
        self.sql_recipe_repo = sql_recipe_repo
        self.embeddings = embeddings
        self.llm = llm
        self.prompts = prompts
        self.ingest_max_tokens = ingest_max_tokens

    async def ingest(self, source_id: UUID) -> SqlNotesIngestResult:
        src = await self.source_repo.get(source_id)
        if not src:
            raise NotFoundError("Источник не найден")
        notes = (src.sql_notes_md or "").strip()
        if not notes:
            return SqlNotesIngestResult(
                ok=False, warnings=["SQL-заметки пусты — нечего разбирать"]
            )

        warnings: list[str] = []
        recipes: list[dict[str, Any]] = []
        # Same chunk-then-merge approach as the glossary: a big notes file would
        # overflow the model's output budget in one call.
        for chunk in chunk_markdown(notes):
            prompt = self.prompts.render("sql_notes_ingest", sql_notes=chunk)
            raw = await self.llm.complete(
                [{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=self.ingest_max_tokens,
            )
            piece = _parse(raw)
            for r in piece.get("recipes") or []:
                sql = (r.get("sql") or "").strip()
                if not sql:
                    continue
                recipes.append(
                    {
                        "title": (r.get("title") or "").strip() or "(без названия)",
                        "intent": (r.get("intent") or "").strip(),
                        "sql": sql,
                        "tables": [str(t) for t in (r.get("tables") or []) if t],
                    }
                )

        # Embed the NL intent (fallback to title) — never the SQL.
        if recipes:
            texts = [r["intent"] or r["title"] for r in recipes]
            try:
                vecs = await self.embeddings.embed_many(texts)
                for r, v in zip(recipes, vecs, strict=False):
                    r["embedding"] = v
            except Exception as exc:  # noqa: BLE001
                logger.exception("sql recipe embedding failed")
                warnings.append(
                    f"Не удалось построить эмбеддинги рецептов (поиск будет хуже): {exc}"
                )

        await self.sql_recipe_repo.replace_recipes(source_id, recipes)
        await self.source_repo.set_sql_notes_ingested(source_id)
        logger.info(
            "sql notes ingested", source_id=str(source_id), recipes=len(recipes)
        )
        return SqlNotesIngestResult(ok=True, recipes=len(recipes), warnings=warnings)
