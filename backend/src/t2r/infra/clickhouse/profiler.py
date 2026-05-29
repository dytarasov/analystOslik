from __future__ import annotations

from typing import Any

from t2r.infra.clickhouse.client import CHClient

SYSTEM_DBS = {"system", "INFORMATION_SCHEMA", "information_schema"}

_NUMERIC_PREFIXES = ("Int", "UInt", "Float", "Decimal")


def _base_type(t: str) -> str:
    """Strip Nullable(...) / LowCardinality(...) wrappers to the inner type."""
    s = (t or "").strip()
    changed = True
    while changed:
        changed = False
        for wrap in ("Nullable(", "LowCardinality("):
            if s.startswith(wrap) and s.endswith(")"):
                s = s[len(wrap):-1].strip()
                changed = True
    return s


def is_numeric_type(t: str) -> bool:
    return _base_type(t).startswith(_NUMERIC_PREFIXES)


def is_temporal_type(t: str) -> bool:
    return _base_type(t).startswith("Date")


def is_string_type(t: str) -> bool:
    return _base_type(t).startswith(("String", "FixedString"))


class CHProfiler:
    def __init__(self, client: CHClient) -> None:
        self.client = client

    async def fetch_databases(self) -> list[str]:
        res = await self.client.query("SELECT name FROM system.databases ORDER BY name")
        return [str(r[0]) for r in res.result_rows if str(r[0]) not in SYSTEM_DBS]

    async def fetch_tables(self, database: str) -> list[dict[str, Any]]:
        res = await self.client.query(
            "SELECT name, engine, total_rows, total_bytes FROM system.tables "
            "WHERE database = {db:String} AND NOT is_temporary ORDER BY name",
            parameters={"db": database},
        )
        out: list[dict[str, Any]] = []
        for row in res.result_rows:
            out.append(
                {
                    "name": str(row[0]),
                    "engine": str(row[1]) if row[1] is not None else None,
                    "total_rows": int(row[2]) if row[2] is not None else None,
                    "total_bytes": int(row[3]) if row[3] is not None else None,
                }
            )
        return out

    async def fetch_columns(self, database: str, table: str) -> list[dict[str, Any]]:
        res = await self.client.query(
            "SELECT name, type, default_expression, comment, position"
            " FROM system.columns WHERE database = {db:String} AND table = {tbl:String}"
            " ORDER BY position",
            parameters={"db": database, "tbl": table},
        )
        return [
            {
                "name": str(r[0]),
                "type": str(r[1]),
                "default": str(r[2]) if r[2] else None,
                "comment": str(r[3]) if r[3] else None,
                "position": int(r[4]),
            }
            for r in res.result_rows
        ]

    async def fetch_ddl(self, database: str, table: str) -> str:
        res = await self.client.query(
            f"SHOW CREATE TABLE {database}.{table}"
        )
        rows = res.result_rows
        return str(rows[0][0]) if rows else ""

    async def fetch_sample(
        self, database: str, table: str, limit: int = 100, timeout: int = 10
    ) -> dict[str, Any]:
        res = await self.client.query(
            f"SELECT * FROM {database}.{table} LIMIT {limit}",
            settings={"max_execution_time": timeout},
        )
        columns = list(res.column_names)
        rows = [list(r) for r in res.result_rows]
        return {"columns": columns, "rows": rows}

    async def fetch_column_stats(
        self, database: str, table: str, columns: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        if not columns:
            return {}
        # Build a single SELECT with aggregates per column to minimize roundtrips.
        agg_parts = ["count() AS _total"]
        for c in columns:
            col = c["name"]
            safe = col.replace("`", "``")
            agg_parts.append(f"countIf(isNull(`{safe}`)) AS _null_{c['position']}")
            agg_parts.append(f"uniqHLL12(`{safe}`) AS _uniq_{c['position']}")
        sql = (
            f"SELECT {', '.join(agg_parts)} FROM {database}.{table} "
            "SETTINGS max_execution_time = 30"
        )
        try:
            res = await self.client.query(sql)
        except Exception:
            return {}
        if not res.result_rows:
            return {}
        row = res.result_rows[0]
        total = int(row[0]) if row[0] is not None else 0
        stats: dict[str, dict[str, Any]] = {}
        i = 1
        for c in columns:
            nulls = int(row[i]) if row[i] is not None else 0
            uniq = int(row[i + 1]) if row[i + 1] is not None else 0
            i += 2
            stats[c["name"]] = {
                "total": total,
                "null": nulls,
                "null_ratio": (nulls / total) if total else None,
                "distinct": uniq,
            }
        return stats

    async def fetch_column_examples(
        self, database: str, table: str, column: str, n: int = 5
    ) -> list[Any]:
        safe = column.replace("`", "``")
        try:
            res = await self.client.query(
                f"SELECT DISTINCT `{safe}` FROM {database}.{table} "
                f"WHERE isNotNull(`{safe}`) LIMIT {n} "
                "SETTINGS max_execution_time = 10"
            )
        except Exception:
            return []
        return [r[0] for r in res.result_rows]

    async def fetch_column_examples_batch(
        self,
        database: str,
        table: str,
        columns: list[dict[str, Any]],
        *,
        sample_len: int = 300,
        sample_rows: int = 20000,
        chunk: int = 80,
        timeout: int = 30,
    ) -> dict[str, list[str]]:
        """Representative example values for EVERY column in one query per chunk.

        Per column we take the *richest* non-null value (``argMaxIf`` over string
        length — for a JSON/CSV/array blob this is a populated one, not a random
        empty ``[]``) plus two random samples, each truncated to ``sample_len``.
        This replaces the per-column round-trip so wide tables don't lose examples
        for their tail columns — the gap that left the describer with no sample and
        forced it to ask "what format is this column?" about data it should infer.

        Aggregation runs over a bounded ``LIMIT sample_rows`` block, not the whole
        table, so cost stays flat regardless of row count (stringifying large blob
        columns over millions of rows would otherwise risk the time limit). Best-
        effort: a failed chunk yields no examples for its columns rather than
        aborting the harvest; columns are chunked to bound the aggregate count.
        """
        out: dict[str, list[str]] = {}
        for start in range(0, len(columns), chunk):
            block = columns[start : start + chunk]
            inner_cols = ", ".join(
                f"`{c['name'].replace('`', '``')}`" for c in block
            )
            parts: list[str] = []
            layout: list[tuple[str, int]] = []  # name, base index into the row
            idx = 0
            for c in block:
                safe = c["name"].replace("`", "``")
                expr = f"substring(toString(`{safe}`), 1, {sample_len})"
                parts.append(
                    f"argMaxIf({expr}, length(toString(`{safe}`)), isNotNull(`{safe}`))"
                )
                parts.append(f"groupArraySampleIf(2)({expr}, isNotNull(`{safe}`))")
                layout.append((c["name"], idx))
                idx += 2
            sql = (
                f"SELECT {', '.join(parts)} FROM"
                f" (SELECT {inner_cols} FROM {database}.{table} LIMIT {sample_rows})"
                f" SETTINGS max_execution_time = {timeout}"
            )
            try:
                res = await self.client.query(sql)
            except Exception:
                continue
            if not res.result_rows:
                continue
            row = res.result_rows[0]
            for name, base in layout:
                richest = row[base]
                samples = row[base + 1] or []
                seen: list[str] = []
                for v in [richest, *samples]:
                    if v is None:
                        continue
                    sv = str(v)
                    # Drop blanks; keep first occurrence (richest leads) up to 3.
                    if sv and sv not in seen:
                        seen.append(sv)
                if seen:
                    out[name] = seen[:3]
        return out

    async def fetch_table_meta(self, database: str, table: str) -> dict[str, Any]:
        """Physical table metadata: engine, size, and the ClickHouse keys.

        sorting_key / partition_key / primary_key are the natural join columns
        and the partition column for efficient time filtering — the agent needs
        them to write good SQL.
        """
        try:
            res = await self.client.query(
                "SELECT engine, total_rows, total_bytes, sorting_key,"
                " partition_key, primary_key FROM system.tables"
                " WHERE database = {db:String} AND name = {tbl:String}",
                parameters={"db": database, "tbl": table},
            )
        except Exception:
            return {}
        if not res.result_rows:
            return {}
        r = res.result_rows[0]
        return {
            "engine": str(r[0]) if r[0] is not None else None,
            "total_rows": int(r[1]) if r[1] is not None else None,
            "total_bytes": int(r[2]) if r[2] is not None else None,
            "sorting_key": str(r[3]) if r[3] else None,
            "partition_key": str(r[4]) if r[4] else None,
            "primary_key": str(r[5]) if r[5] else None,
        }

    async def fetch_column_keys(
        self, database: str, table: str
    ) -> dict[str, dict[str, bool]]:
        """Per-column membership in sorting/partition/primary keys."""
        try:
            res = await self.client.query(
                "SELECT name, is_in_sorting_key, is_in_partition_key,"
                " is_in_primary_key FROM system.columns"
                " WHERE database = {db:String} AND table = {tbl:String}",
                parameters={"db": database, "tbl": table},
            )
        except Exception:
            return {}
        out: dict[str, dict[str, bool]] = {}
        for r in res.result_rows:
            out[str(r[0])] = {
                "sorting": bool(r[1]),
                "partition": bool(r[2]),
                "primary": bool(r[3]),
            }
        return out

    async def fetch_value_catalog(
        self,
        database: str,
        table: str,
        column: str,
        *,
        limit: int = 50,
        timeout: int = 15,
    ) -> list[dict[str, Any]]:
        """Top values with frequencies, ordered by count desc.

        Only meaningful for low-cardinality columns — the caller gates this on
        the column's distinct estimate. This is what lets the agent write
        correct `WHERE status = '...'` filters.
        """
        safe = column.replace("`", "``")
        try:
            res = await self.client.query(
                f"SELECT `{safe}` AS v, count() AS c FROM {database}.{table}"
                f" WHERE isNotNull(`{safe}`) GROUP BY v ORDER BY c DESC LIMIT {limit}",
                settings={"max_execution_time": timeout},
            )
        except Exception:
            return []
        return [{"value": r[0], "count": int(r[1])} for r in res.result_rows]

    async def fetch_ranges(
        self,
        database: str,
        table: str,
        columns: list[dict[str, Any]],
        *,
        max_cols: int = 40,
        timeout: int = 30,
    ) -> dict[str, dict[str, Any]]:
        """min/max for temporal columns, min/max/avg/median for numeric ones.

        Computed in a single aggregate scan. Powers relative time windows ("за
        последние 30 дней" relative to max date) and scale awareness.
        """
        targets: list[tuple[dict[str, Any], bool]] = []
        for c in columns:
            t = c["type"]
            if is_temporal_type(t):
                targets.append((c, False))
            elif is_numeric_type(t):
                targets.append((c, True))
            if len(targets) >= max_cols:
                break
        if not targets:
            return {}

        select_parts: list[str] = []
        layout: list[tuple[str, bool, int]] = []  # name, is_numeric, base_index
        idx = 0
        for c, numeric in targets:
            safe = c["name"].replace("`", "``")
            select_parts.append(f"min(`{safe}`)")
            select_parts.append(f"max(`{safe}`)")
            if numeric:
                select_parts.append(f"avg(`{safe}`)")
                select_parts.append(f"quantile(0.5)(`{safe}`)")
                layout.append((c["name"], True, idx))
                idx += 4
            else:
                layout.append((c["name"], False, idx))
                idx += 2
        sql = f"SELECT {', '.join(select_parts)} FROM {database}.{table}"
        try:
            res = await self.client.query(sql, settings={"max_execution_time": timeout})
        except Exception:
            return {}
        if not res.result_rows:
            return {}
        row = res.result_rows[0]
        out: dict[str, dict[str, Any]] = {}
        for name, numeric, base in layout:
            entry: dict[str, Any] = {"min": row[base], "max": row[base + 1]}
            if numeric:
                entry["avg"] = row[base + 2]
                entry["median"] = row[base + 3]
            out[name] = entry
        return out

    async def fetch_extended_stats(
        self,
        database: str,
        table: str,
        columns: list[dict[str, Any]],
        *,
        max_cols: int = 60,
        timeout: int = 30,
    ) -> dict[str, dict[str, Any]]:
        """Deeper per-column probes in one scan: string length (avg/max) and
        numeric zero/negative counts. Best-effort — returns {} on any error so
        it never blocks the harvest.
        """
        parts: list[str] = []
        layout: list[tuple[str, str, int]] = []
        idx = 0
        for c in columns:
            t = c["type"]
            safe = c["name"].replace("`", "``")
            if is_string_type(t):
                parts.append(f"avg(length(`{safe}`))")
                parts.append(f"max(length(`{safe}`))")
                layout.append((c["name"], "str", idx))
                idx += 2
            elif is_numeric_type(t):
                parts.append(f"countIf(`{safe}` = 0)")
                parts.append(f"countIf(`{safe}` < 0)")
                layout.append((c["name"], "num", idx))
                idx += 2
            if len(layout) >= max_cols:
                break
        if not parts:
            return {}
        sql = f"SELECT {', '.join(parts)} FROM {database}.{table}"
        try:
            res = await self.client.query(sql, settings={"max_execution_time": timeout})
        except Exception:
            return {}
        if not res.result_rows:
            return {}
        row = res.result_rows[0]
        out: dict[str, dict[str, Any]] = {}
        for name, kind, base in layout:
            if kind == "str":
                out[name] = {"avg_len": row[base], "max_len": row[base + 1]}
            else:
                out[name] = {
                    "zeros": int(row[base] or 0),
                    "negatives": int(row[base + 1] or 0),
                }
        return out

    async def fetch_value_overlap(
        self,
        from_db: str,
        from_table: str,
        from_column: str,
        to_db: str,
        to_table: str,
        to_column: str,
        *,
        sample: int = 2000,
        timeout: int = 15,
    ) -> dict[str, Any]:
        """Sampled fraction of distinct from-values present in the target column.

        High overlap is strong evidence the columns form a real join key, far
        more reliable than name/type heuristics alone.
        """
        fcol = from_column.replace("`", "``")
        tcol = to_column.replace("`", "``")
        sql = (
            f"SELECT count() AS total,"
            f" countIf(v IN (SELECT `{tcol}` FROM {to_db}.{to_table})) AS matched"
            f" FROM (SELECT DISTINCT `{fcol}` AS v FROM {from_db}.{from_table}"
            f"        WHERE isNotNull(`{fcol}`) LIMIT {sample})"
        )
        try:
            res = await self.client.query(sql, settings={"max_execution_time": timeout})
        except Exception:
            return {"total": 0, "matched": 0, "ratio": None}
        if not res.result_rows:
            return {"total": 0, "matched": 0, "ratio": None}
        total = int(res.result_rows[0][0] or 0)
        matched = int(res.result_rows[0][1] or 0)
        return {
            "total": total,
            "matched": matched,
            "ratio": (matched / total) if total else None,
        }

    async def fetch_usage_stats(
        self, database: str, table: str, days: int = 14, limit: int = 20
    ) -> dict[str, Any]:
        try:
            res = await self.client.query(
                "SELECT query, count() AS cnt, avg(query_duration_ms) AS avg_ms"
                " FROM system.query_log"
                " WHERE event_time > now() - INTERVAL {days:UInt32} DAY"
                "   AND has(tables, {qt:String})"
                "   AND type = 'QueryFinish'"
                " GROUP BY query ORDER BY cnt DESC LIMIT {lim:UInt32}",
                parameters={
                    "days": days,
                    "qt": f"{database}.{table}",
                    "lim": limit,
                },
            )
        except Exception:
            return {"top_queries": [], "available": False}
        return {
            "available": True,
            "top_queries": [
                {
                    "query": str(r[0])[:1000],
                    "count": int(r[1]),
                    "avg_ms": float(r[2]) if r[2] is not None else None,
                }
                for r in res.result_rows
            ],
        }
