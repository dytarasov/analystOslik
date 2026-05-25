from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import expressions as exp

# Functions with side effects / external IO we never allow in agent-issued SQL.
FORBIDDEN_FUNCS = {
    "url",
    "urlcluster",
    "file",
    "s3",
    "s3cluster",
    "hdfs",
    "mysql",
    "postgresql",
    "remote",
    "remotesecure",
    "executable",
}

ALLOWED_STMT_TYPES = (exp.Select, exp.Subquery, exp.With, exp.Show, exp.Describe, exp.Pragma)

# sqlglot falls back to `Command` for ClickHouse-specific syntax it doesn't model
# fully (e.g. `SHOW TABLES FROM db`, `DESCRIBE TABLE x`, `EXPLAIN ...`). We allow
# Command only when the leading keyword is in this safe set.
ALLOWED_COMMAND_KEYWORDS = {"SHOW", "DESCRIBE", "DESC", "EXPLAIN"}


class SqlGuardError(Exception):
    pass


@dataclass
class GuardResult:
    rewritten: str
    has_aggregate: bool
    referenced_tables: list[str]
    settings: dict[str, Any] | None = None


def validate_and_rewrite(
    sql: str,
    *,
    whitelist_qnames: set[str] | None = None,
    default_limit: int = 10000,
    max_execution_time: int = 30,
) -> GuardResult:
    parsed_list = sqlglot.parse(sql, read="clickhouse")
    parsed_list = [p for p in parsed_list if p is not None]
    if len(parsed_list) != 1:
        raise SqlGuardError("Допустим только один SQL-statement")
    tree = parsed_list[0]

    # Reject DML/DDL outright by type
    if isinstance(tree, exp.Command):
        kw = (tree.this or "").upper() if isinstance(tree.this, str) else ""
        # ClickHouse SHOW/DESCRIBE/EXPLAIN often parses as Command
        if kw not in ALLOWED_COMMAND_KEYWORDS:
            raise SqlGuardError(
                f"Запрещённый тип statement: Command({kw}). Только SELECT/WITH/SHOW/DESCRIBE разрешены."
            )
        # Trust the rest of the command body as opaque text; nothing else to rewrite.
        return GuardResult(rewritten=sql, has_aggregate=False, referenced_tables=[])
    if not isinstance(tree, ALLOWED_STMT_TYPES):
        raise SqlGuardError(
            f"Запрещённый тип statement: {tree.key}. Только SELECT/WITH/SHOW/DESCRIBE разрешены."
        )

    # No DML/DDL nested expressions
    forbidden_nodes = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.TruncateTable,
        exp.Grant,
    )
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, forbidden_nodes):
            raise SqlGuardError("Запрещены DML/DDL операторы")

    # Forbidden table functions
    for f in tree.find_all(exp.Anonymous):
        fname = (f.name or "").lower()
        if fname in FORBIDDEN_FUNCS:
            raise SqlGuardError(f"Запрещённая функция: {fname}")
    for f in tree.find_all(exp.Func):
        fname = (f.name or "").lower()
        if fname in FORBIDDEN_FUNCS:
            raise SqlGuardError(f"Запрещённая функция: {fname}")

    # Collect CTE aliases — they look like tables in `tree.find_all(exp.Table)`
    # but should not be subject to the whitelist check.
    cte_aliases = {cte.alias for cte in tree.find_all(exp.CTE) if cte.alias}

    # Collect tables
    referenced: list[str] = []
    for t in tree.find_all(exp.Table):
        db = t.args.get("db")
        name = t.name
        if not db and name in cte_aliases:
            continue
        if db:
            qname = f"{db.name if isinstance(db, exp.Identifier) else db}.{name}"
        else:
            qname = name
        referenced.append(qname)

    if whitelist_qnames is not None and whitelist_qnames:
        for q in referenced:
            # allow read-only system views
            if q.startswith("system.") and q in {"system.numbers", "system.one"}:
                continue
            if q not in whitelist_qnames:
                raise SqlGuardError(
                    f"Таблица `{q}` не входит в семантический слой источника"
                )

    # Detect aggregate
    has_aggregate = bool(list(tree.find_all(exp.AggFunc)))

    # Inject LIMIT if absent on top-level SELECT
    if isinstance(tree, exp.Select) and not tree.args.get("limit") and not has_aggregate:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(default_limit)))

    rewritten_sql = tree.sql(dialect="clickhouse")

    # Settings are returned separately and applied via clickhouse-connect's
    # `settings=` argument — they end up as URL params, not query text. We
    # used to splice them into the SQL itself, which broke because the driver
    # also appends `FORMAT Native` to the body and CH parser then chokes on
    # `... SETTINGS ... FORMAT Native`. Driver-level settings sidestep it.
    settings: dict[str, Any] | None = None
    if isinstance(tree, (exp.Select, exp.With, exp.Subquery)):
        # `max_result_rows` caps the result set for safety, but with the default
        # `result_overflow_mode='throw'` a legitimate aggregate (e.g. GROUP BY a
        # high-cardinality column returning >max_result_rows groups) would error
        # out instead of returning data. `break` makes ClickHouse stop and return
        # the truncated result, which is the right behaviour for a preview tool —
        # better a capped answer than a hard failure on a valid query.
        settings = {
            "max_execution_time": max_execution_time,
            "max_result_rows": default_limit,
            "result_overflow_mode": "break",
        }

    return GuardResult(
        rewritten=rewritten_sql,
        has_aggregate=has_aggregate,
        referenced_tables=referenced,
        settings=settings,
    )
