from __future__ import annotations

from typing import Any


def _fmt_catalog(catalog: Any, limit: int = 20) -> str:
    if not isinstance(catalog, list) or not catalog:
        return ""
    vals: list[str] = []
    for item in catalog[:limit]:
        if isinstance(item, dict):
            vals.append(str(item.get("value")))
        else:
            vals.append(str(item))
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
        out += f", avg={rng.get('avg')}"
    return out


def render_schema(
    tables: list[dict[str, Any]],
    columns_by_table: dict[str, list[dict[str, Any]]],
    *,
    max_tables: int = 30,
) -> str:
    """Render the semantic layer for the SQL generator.

    Surfaces not just names/types/descriptions but the physical keys, the
    actual categorical values, and numeric/date ranges — the context the agent
    needs to write correct WHERE / JOIN / time filters.
    """
    parts: list[str] = []
    for t in tables[:max_tables]:
        qname = f"{t['database']}.{t['table_name']}"
        title = t.get("title") or ""
        desc = (t.get("description") or "").strip()
        parts.append(f"### `{qname}` — {title}")

        meta_bits: list[str] = []
        if t.get("total_rows") is not None:
            meta_bits.append(f"rows≈{t['total_rows']:,}")
        if t.get("partition_key"):
            meta_bits.append(f"PARTITION BY ({t['partition_key']})")
        if t.get("sorting_key"):
            meta_bits.append(f"ORDER BY ({t['sorting_key']})")
        if meta_bits:
            parts.append(f"_{' · '.join(meta_bits)}_")
        if t.get("grain"):
            parts.append(f"Грануляр­ность: {t['grain']}")
        if desc:
            parts.append(desc)

        cols = columns_by_table.get(str(t["id"]), [])
        for c in cols:
            role = c.get("semantic_role") or ""
            cd = (c.get("description") or "").strip()
            marks = []
            if c.get("is_in_primary_key"):
                marks.append("PK")
            if c.get("is_in_sorting_key"):
                marks.append("ORDER")
            if c.get("is_in_partition_key"):
                marks.append("PART")
            mark_str = (" [" + ",".join(marks) + "]") if marks else ""
            line = f"  - `{c['name']}` : {c['data_type']} ({role}){mark_str} — {cd}"
            cat = _fmt_catalog(c.get("value_catalog"))
            if cat:
                line += f"\n      значения: {cat}"
            rng = _fmt_range(c.get("value_range"))
            if rng:
                line += f"\n      диапазон: {rng}"
            parts.append(line)
        parts.append("")
    return "\n".join(parts)
