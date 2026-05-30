"""Reusable column-describer: render the ``column_group_describer`` prompt for a
set of columns of one table and persist the analyst-facing description+semantics.

Pass 2 wraps this in the durable task queue (with the question-parking loop); the
single-column re-profile (re-enabling a column that was excluded before pass 2,
so it has facts but no description) calls it directly for an immediate result.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.llm.json_extractor import extract_json
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader

_SEMANTICS_KEYS = (
    "unit", "pii", "value_meanings", "safe_to_group_by",
    "safe_to_filter_by", "caveats", "suggested_aggregation", "confidence",
)


class ColumnDescribeError(Exception):
    """The describer returned nothing usable (unparseable / empty / no matching
    column) for columns that were asked to be described. Raised so callers don't
    silently report success on a column that stays description-less."""


def _catalog_sample(cat: Any, n: int = 30) -> str:
    if not isinstance(cat, list) or not cat:
        return ""
    vals = [str(v.get("value")) if isinstance(v, dict) else str(v) for v in cat[:n]]
    return ", ".join(vals)


async def describe_columns(
    *,
    llm: LLMClient,
    prompts: PromptLoader,
    semantic: SemanticRepoPg,
    table_id: UUID,
    names: list[str],
) -> int:
    """Describe the named columns of a table with one LLM call and apply the
    result via ``apply_column_description`` (which itself skips locked rows).
    Returns the number of columns actually described."""
    if not names:
        return 0
    table = await semantic.get_table(table_id)
    if not table:
        return 0
    all_cols = await semantic.get_columns(table_id, only_enabled=True)
    by_name = {c["name"]: c for c in all_cols}
    targets = [by_name[n] for n in names if n in by_name]
    if not targets:
        return 0

    peers = ", ".join(
        f"{c['name']}({c.get('semantic_role') or '?'})"
        for c in all_cols
        if c["name"] not in set(names)
    )[:1200]
    col_ctx = [
        {
            "name": c["name"],
            "data_type": c["data_type"],
            "semantic_role": c.get("semantic_role"),
            "distinct_count": c.get("distinct_count"),
            "null_ratio": c.get("null_ratio"),
            "examples": c.get("examples"),
            "value_catalog": _catalog_sample(c.get("value_catalog")),
            "value_range": c.get("value_range"),
            "format": (c.get("value_range") or {}).get("pattern"),
        }
        for c in targets
    ]
    rendered = prompts.render(
        "column_group_describer",
        database=table["database"],
        table=table["table_name"],
        table_title=table.get("title"),
        table_description=table.get("description"),
        grain=table.get("grain"),
        columns=col_ctx,
        peers=peers,
        answers=[],
    )
    out = await llm.complete([{"role": "user", "content": rendered}], temperature=0.2)
    try:
        obj = extract_json(out) or {}
    except Exception:
        obj = {}

    described = obj.get("columns") or []
    n = 0
    for d in described:
        col = by_name.get(d.get("name"))
        if not col:
            continue
        semantics = {k: d.get(k) for k in _SEMANTICS_KEYS if d.get(k) is not None}
        await semantic.apply_column_description(
            col["id"],
            description=d.get("description"),
            semantic_role=d.get("semantic_role"),
            semantics=semantics or None,
        )
        n += 1
    if n == 0:
        # targets is non-empty here (guarded above), so a zero count means the
        # reply was unparseable / empty / matched no requested column.
        raise ColumnDescribeError(
            f"describer returned no usable columns for "
            f"{table['database']}.{table['table_name']}"
        )
    return n
