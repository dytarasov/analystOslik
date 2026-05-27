from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

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

ALLOWED_STMT_TYPES = (exp.Select, exp.Subquery, exp.With, exp.Union, exp.Show, exp.Describe, exp.Pragma)

# sqlglot falls back to `Command` for ClickHouse-specific syntax it doesn't model
# fully (e.g. `SHOW TABLES FROM db`, `DESCRIBE TABLE x`, `EXPLAIN ...`). We allow
# Command only when the leading keyword is in this safe set.
ALLOWED_COMMAND_KEYWORDS = {"SHOW", "DESCRIBE", "DESC", "EXPLAIN"}


class SqlGuardError(Exception):
    pass


def _qname_of(t: exp.Table) -> str:
    db = t.args.get("db")
    name = t.name
    if db:
        return f"{db.name if isinstance(db, exp.Identifier) else db}.{name}"
    return name


def _global_scope(
    tree: exp.Expression, cte_aliases: set[str]
) -> tuple[dict[str, str], list[str]]:
    """Map every table qualifier (alias, else bare name) to its qname across the
    whole statement, and list the distinct sem-table qnames in scope. Aliases are
    usually unique per statement, so global resolution of a qualifier is reliable
    for the flat / unique-alias queries the agent emits."""
    qual_to_qname: dict[str, str] = {}
    sem_scope: list[str] = []
    for t in tree.find_all(exp.Table):
        name = t.name
        if not t.args.get("db") and name in cte_aliases:
            continue
        qname = _qname_of(t)
        sem_scope.append(qname)
        if t.alias:
            qual_to_qname[t.alias] = qname
        qual_to_qname.setdefault(name, qname)
    return qual_to_qname, list(dict.fromkeys(sem_scope))


def _select_direct_scope(
    select: exp.Select, cte_aliases: set[str]
) -> tuple[list[str], bool]:
    """The tables directly in THIS select's FROM/JOINs (not nested subqueries).
    Returns (sem qnames, has_derived) — has_derived flags a subquery/CTE/table
    function operand, whose columns we can't enumerate, so `*` is left alone."""
    operands: list[exp.Expression] = []
    frm = select.find(exp.From)
    if frm is not None:
        operands.append(frm.this)
        operands.extend(frm.expressions)  # comma-joined tables
    for j in select.args.get("joins") or []:
        operands.append(j.this)
    qnames: list[str] = []
    has_derived = False
    for op in operands:
        if isinstance(op, exp.Table):
            if not op.args.get("db") and op.name in cte_aliases:
                has_derived = True  # CTE columns are not sem columns
                continue
            qnames.append(_qname_of(op))
        else:
            has_derived = True
    return list(dict.fromkeys(qnames)), has_derived


def _enforce_columns(
    tree: exp.Expression,
    cte_aliases: set[str],
    enabled_columns: dict[str, list[str]],
    disabled_columns: dict[str, set[str]],
) -> None:
    """Reject references to disabled columns and expand `*` / `t.*` into the
    enabled column list (so a SELECT * never leaks a hidden column)."""
    qual_to_qname, sem_scope = _global_scope(tree, cte_aliases)
    single_global = sem_scope[0] if len(sem_scope) == 1 else None
    # Every disabled column name across all tables in scope. Used for the
    # unqualified-reference case in multi-table queries, where we can't pin the
    # column to one table — fail closed and require qualification.
    disabled_scope_names: set[str] = set()
    for q in sem_scope:
        disabled_scope_names |= disabled_columns.get(q, set())

    def disabled_of(qname: str | None) -> set[str]:
        return disabled_columns.get(qname or "", set())

    # 1) Explicit column references (skip the star pseudo-columns).
    for c in tree.find_all(exp.Column):
        if isinstance(c.this, exp.Star):
            continue
        if c.table:
            qn = qual_to_qname.get(c.table)
            if qn and c.name in disabled_of(qn):
                raise SqlGuardError(
                    f"Колонка `{c.name}` отключена в `{qn}` и недоступна для запроса"
                )
        elif single_global is not None:
            if c.name in disabled_of(single_global):
                raise SqlGuardError(
                    f"Колонка `{c.name}` отключена в `{single_global}` и недоступна для запроса"
                )
        elif c.name in disabled_scope_names:
            # Unqualified column in a multi-table query whose name matches a
            # disabled column somewhere in scope — ClickHouse could resolve it to
            # the disabled column and leak it. Reject and ask to qualify.
            raise SqlGuardError(
                f"Колонка `{c.name}` отключена в одной из таблиц запроса — "
                "укажите её явно через `таблица.колонка` (на разрешённой таблице) "
                "или не используйте отключённую колонку"
            )

    # 2) Star expansion, per select (so a subquery's * isn't expanded with the
    #    outer table's columns).
    for select in tree.find_all(exp.Select):
        direct, has_derived = _select_direct_scope(select, cte_aliases)
        new_exprs: list[exp.Expression] = []
        changed = False
        for e in select.expressions:
            # `t.*`
            if isinstance(e, exp.Column) and isinstance(e.this, exp.Star) and e.table:
                qn = qual_to_qname.get(e.table)
                if qn and disabled_of(qn):
                    enabled = enabled_columns.get(qn) or []
                    if not enabled:
                        raise SqlGuardError(
                            f"Все колонки `{qn}` отключены — нечего выбрать через `{e.table}.*`"
                        )
                    new_exprs.extend(exp.column(col, table=e.table) for col in enabled)
                    changed = True
                    continue
                new_exprs.append(e)
                continue
            # bare `*`
            if isinstance(e, exp.Star):
                disabled_in_scope = [q for q in direct if disabled_of(q)]
                if not disabled_in_scope:
                    new_exprs.append(e)
                    continue
                if len(direct) == 1 and not has_derived:
                    qn = direct[0]
                    enabled = enabled_columns.get(qn) or []
                    if not enabled:
                        raise SqlGuardError(
                            f"Все колонки `{qn}` отключены — нечего выбрать через `*`"
                        )
                    new_exprs.extend(exp.column(col) for col in enabled)
                    changed = True
                    continue
                raise SqlGuardError(
                    "В запросе с несколькими таблицами есть отключённые колонки — "
                    "перечислите нужные колонки явно вместо `*`"
                )
            new_exprs.append(e)
        if changed:
            select.set("expressions", new_exprs)


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
    enabled_columns: dict[str, list[str]] | None = None,
    disabled_columns: dict[str, set[str]] | None = None,
    default_limit: int = 10000,
    max_execution_time: int = 30,
) -> GuardResult:
    try:
        parsed_list = sqlglot.parse(sql, read="clickhouse")
    except ParseError as exc:
        # Surface as a guard error (kind='guard') so the agent gets a clean
        # "rewrite your SQL" signal instead of a raw parser stacktrace.
        raise SqlGuardError(f"Не удалось разобрать SQL: {exc}") from exc
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
    # sqlglot parses table functions in FROM (`file(...)`, `s3(...)`, `remote(...)`)
    # as exp.Table with the function name — they slip past the Func checks above,
    # so reject them by table name too.
    for t in tree.find_all(exp.Table):
        if (t.name or "").lower() in FORBIDDEN_FUNCS:
            raise SqlGuardError(f"Запрещённая функция: {(t.name or '').lower()}")

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

    # whitelist=None means "not enforced"; an empty set means "no tables allowed"
    # (fail closed) — a source with nothing profiled can't be queried, rather than
    # the table check silently turning off.
    if whitelist_qnames is not None:
        for q in referenced:
            # allow read-only system views
            if q.startswith("system.") and q in {"system.numbers", "system.one"}:
                continue
            if q not in whitelist_qnames:
                raise SqlGuardError(
                    f"Таблица `{q}` не входит в семантический слой источника"
                )

    # Column-level guard: reject disabled columns and expand `*`/`t.*` to the
    # enabled set. Only runs when the caller supplied a disabled map.
    if disabled_columns:
        _enforce_columns(tree, cte_aliases, enabled_columns or {}, disabled_columns)

    # Detect aggregate
    has_aggregate = bool(list(tree.find_all(exp.AggFunc)))

    # NOTE: row capping is intentionally OFF. We used to auto-inject `LIMIT N`
    # and set `max_result_rows`/`result_overflow_mode='break'`, but a silent cap
    # truncates results without telling the agent — it would read a capped set as
    # the full picture (wrong counts/sums). The query now returns the full result;
    # only `max_execution_time` bounds a runaway query (by time, which doesn't
    # distort the data). `default_limit` is accepted for compatibility but unused.
    rewritten_sql = tree.sql(dialect="clickhouse")

    # Settings are returned separately and applied via clickhouse-connect's
    # `settings=` argument — they end up as URL params, not query text. We
    # used to splice them into the SQL itself, which broke because the driver
    # also appends `FORMAT Native` to the body and CH parser then chokes on
    # `... SETTINGS ... FORMAT Native`. Driver-level settings sidestep it.
    settings: dict[str, Any] | None = None
    if isinstance(tree, (exp.Select, exp.With, exp.Subquery, exp.Union)):
        settings = {"max_execution_time": max_execution_time}

    return GuardResult(
        rewritten=rewritten_sql,
        has_aggregate=has_aggregate,
        referenced_tables=referenced,
        settings=settings,
    )
