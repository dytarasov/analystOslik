"""Render + persist the RAG substrate (md_notes + embeddings) from the
*persisted* semantic layer.

The v2 pipeline harvests facts (pass 1) and describes columns (pass 2) straight
into ``sem_tables``/``sem_columns`` but historically never built the per-table /
per-column notes the client agent's ``search_knowledge`` retrieves — only the
source overview + glossary notes existed. This module closes that gap: it reads
the semantic layer and writes one note per table plus notes for the high-signal
columns, embedding each.

It works purely off the persisted layer (no ClickHouse), so the same functions
rebuild a single table's notes after an edit / column toggle (phases 4–5).
Disabled columns are never noted — callers pass the already-enabled set.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.logging import get_logger

logger = get_logger("profiling.note_writer")

# Cap how many columns get their own retrievable note (keys / categorical /
# ranged ones are the signal; the rest live in the table note).
MAX_COLUMN_NOTES = 20


def _fmt_catalog(catalog: Any, limit: int = 25) -> str:
    if not isinstance(catalog, list) or not catalog:
        return ""
    vals = [
        str(v.get("value")) if isinstance(v, dict) else str(v) for v in catalog[:limit]
    ]
    suffix = " …" if len(catalog) > limit else ""
    return ", ".join(vals) + suffix


def _fmt_range(rng: Any) -> str:
    if not isinstance(rng, dict):
        return ""
    lo, hi = rng.get("min"), rng.get("max")
    if lo is None and hi is None:
        return ""
    out = f"{lo} … {hi}"
    if rng.get("avg") is not None:
        out += f" (avg={rng.get('avg')}, median={rng.get('median')})"
    return out


def _key_marks(col: dict[str, Any]) -> list[str]:
    marks = []
    if col.get("is_in_primary_key"):
        marks.append("PK")
    if col.get("is_in_sorting_key"):
        marks.append("ORDER")
    if col.get("is_in_partition_key"):
        marks.append("PART")
    return marks


def is_high_signal(col: dict[str, Any]) -> bool:
    """A column worth its own retrievable note: a key, categorical (has a value
    catalog), or numeric/date with a range."""
    return bool(
        col.get("is_in_primary_key")
        or col.get("is_in_sorting_key")
        or col.get("is_in_partition_key")
        or col.get("value_catalog")
        or _fmt_range(col.get("value_range"))
    )


def render_table_note(table: dict[str, Any], columns: list[dict[str, Any]]) -> str:
    db, tbl = table["database"], table["table_name"]
    title = table.get("title") or tbl
    parts = [
        f"# {title}",
        "",
        f"`{db}.{tbl}` · domain: {table.get('domain') or '—'}",
    ]
    if table.get("grain"):
        parts.append(f"Гранулярность: {table['grain']}")
    parts += ["", (table.get("description") or "").strip(), ""]

    phys = []
    if table.get("engine"):
        phys.append(f"engine={table['engine']}")
    if table.get("total_rows") is not None:
        phys.append(f"rows≈{table['total_rows']:,}")
    if table.get("sorting_key"):
        phys.append(f"ORDER BY ({table['sorting_key']})")
    if table.get("partition_key"):
        phys.append(f"PARTITION BY ({table['partition_key']})")
    if table.get("primary_key"):
        phys.append(f"PRIMARY KEY ({table['primary_key']})")
    if phys:
        parts += ["## Физические свойства", " · ".join(phys), ""]

    parts.append("## Колонки")
    for c in columns:
        role = c.get("semantic_role") or ""
        marks = _key_marks(c)
        key_str = (" [" + ",".join(marks) + "]") if marks else ""
        line = (
            f"- `{c['name']}` ({role}){key_str} — {(c.get('description') or '').strip()}"
            f" · distinct={c.get('distinct_count')} null={c.get('null_ratio')}"
        )
        cat = _fmt_catalog(c.get("value_catalog"))
        if cat:
            line += f"\n    значения: {cat}"
        rng = _fmt_range(c.get("value_range"))
        if rng:
            line += f"\n    диапазон: {rng}"
        parts.append(line)
    return "\n".join(parts)


def render_column_note(table: dict[str, Any], col: dict[str, Any]) -> str:
    db, tbl = table["database"], table["table_name"]
    table_title = table.get("title") or tbl
    name = col["name"]
    parts = [
        f"# {table_title} · колонка `{name}`",
        "",
        f"`{db}.{tbl}.{name}` : {col.get('data_type')} · роль: {col.get('semantic_role') or '—'}",
        "",
        (col.get("description") or "").strip(),
    ]
    marks = _key_marks(col)
    if marks:
        parts.append(f"Ключ: {', '.join(marks)}.")
    parts.append(
        f"distinct={col.get('distinct_count')} · null_ratio={col.get('null_ratio')}"
    )
    cat = _fmt_catalog(col.get("value_catalog"), limit=50)
    if cat:
        parts += ["", "Возможные значения (по частоте):", cat]
    rng = _fmt_range(col.get("value_range"))
    if rng:
        parts += ["", f"Диапазон: {rng}"]
    return "\n".join(parts)


async def _embed_note(
    notes_repo: NotesRepoPg, embeddings: EmbeddingsClient, note_id: UUID, body: str
) -> None:
    try:
        emb = await embeddings.embed(body)
        await notes_repo.set_embedding(note_id, emb)
    except Exception:  # noqa: BLE001
        logger.exception("note embedding failed", note_id=str(note_id))


async def _unchanged(
    notes_repo: NotesRepoPg, *, source_id: UUID, scope: str, target_id: UUID, body: str
) -> bool:
    """True if a note with this exact body already exists AND is embedded — in
    which case re-upsert + re-embed would be pure waste."""
    meta = await notes_repo.get_note_meta(
        source_id=source_id, scope=scope, target_id=target_id
    )
    return bool(meta and meta["has_embedding"] and meta["body_md"] == body)


async def rebuild_table_notes(
    *,
    notes_repo: NotesRepoPg,
    embeddings: EmbeddingsClient,
    source_id: UUID,
    table: dict[str, Any],
    columns: list[dict[str, Any]],
) -> int:
    """(Re)write the table note + high-signal column notes for one table from
    the given (already enabled) columns. Idempotent and **embedding-frugal**:
    a note whose body is unchanged (and already embedded) is skipped entirely,
    so toggling one column doesn't re-vectorise the whole table. Returns the
    number of notes (re)embedded."""
    table_id = table["id"]
    table_title = table.get("title") or table["table_name"]
    embedded = 0

    table_body = render_table_note(table, columns)
    if not await _unchanged(
        notes_repo, source_id=source_id, scope="table", target_id=table_id,
        body=table_body,
    ):
        note_id = await notes_repo.upsert_table_note(
            source_id=source_id,
            target_id=table_id,
            title=table_title,
            body_md=table_body,
            tags=list(table.get("tags") or []),
        )
        await _embed_note(notes_repo, embeddings, note_id, table_body)
        embedded += 1

    col_notes = 0
    for c in columns:
        if col_notes >= MAX_COLUMN_NOTES:
            break
        if not is_high_signal(c):
            continue
        col_notes += 1
        body = render_column_note(table, c)
        if await _unchanged(
            notes_repo, source_id=source_id, scope="column", target_id=c["id"],
            body=body,
        ):
            continue
        cnote_id = await notes_repo.upsert_note(
            source_id=source_id,
            scope="column",
            target_id=c["id"],
            title=f"{table_title} · {c['name']}",
            body_md=body,
            tags=[],
        )
        await _embed_note(notes_repo, embeddings, cnote_id, body)
        embedded += 1
    return embedded


async def write_source_notes(
    *,
    semantic_repo: SemanticRepoPg,
    notes_repo: NotesRepoPg,
    embeddings: EmbeddingsClient,
    source_id: UUID,
) -> dict[str, int]:
    """Rebuild table + column notes for every table of a source, over the
    enabled columns only. Called at the end of profiling."""
    tables = await semantic_repo.list_tables(source_id)
    total_notes = 0
    for t in tables:
        cols = await semantic_repo.get_columns(t["id"], only_enabled=True)
        total_notes += await rebuild_table_notes(
            notes_repo=notes_repo,
            embeddings=embeddings,
            source_id=source_id,
            table=t,
            columns=cols,
        )
    logger.info(
        "profiling.notes: written", source_id=str(source_id),
        tables=len(tables), notes=total_notes,
    )
    return {"tables": len(tables), "notes": total_notes}
