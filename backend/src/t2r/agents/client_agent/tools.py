from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID

from t2r.agents.client_agent.deps import ClientAgentDeps
from t2r.agents.orchestrator.run import AgentRun
from t2r.agents.tools.schema_renderer import render_schema
from t2r.infra.clickhouse.profiler import CHProfiler
from t2r.infra.security.sql_guard import SqlGuardError, validate_and_rewrite
from t2r.logging import get_logger

logger = get_logger("client_agent.tools")

# How much raw data we feed back into the model's context. Full results are
# kept server-side (for preview/export); the model only needs enough to reason.
ROW_VIEW_CAP = 30
CATALOG_CAP = 50
NOTE_BODY_CAP = 800
MAX_TABLES_OVERVIEW = 80


@dataclass
class StoredResult:
    """A run_sql result kept in full for preview/export; one per query."""

    query_id: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]

    @property
    def rowcount(self) -> int:
        return len(self.rows)


class ToolContext:
    """Per-run state shared across tool calls.

    Holds the data-source whitelist (built once from the semantic layer) and the
    accumulating ``run_sql`` results that ``finish`` ultimately exports.
    """

    def __init__(
        self,
        *,
        deps: ClientAgentDeps,
        source_id: UUID,
        run: AgentRun,
        tables: list[dict[str, Any]],
    ) -> None:
        self.deps = deps
        self.source_id = source_id
        self.run = run
        self.tables = tables
        self.by_qname: dict[str, dict[str, Any]] = {
            f"{t['database']}.{t['table_name']}": t for t in tables
        }
        self.id_to_qname: dict[Any, str] = {
            t["id"]: f"{t['database']}.{t['table_name']}" for t in tables
        }
        self.whitelist: set[str] = set(self.by_qname.keys())
        self.results: dict[str, StoredResult] = {}
        self._query_seq = 0
        self._columns_cache: dict[Any, list[dict[str, Any]]] = {}
        self._guard_maps: tuple[dict[str, list[str]], dict[str, set[str]]] | None = None

    def next_query_id(self) -> str:
        self._query_seq += 1
        return f"q{self._query_seq}"

    async def columns_of(self, table_id: Any) -> list[dict[str, Any]]:
        # Agent-facing: disabled columns are invisible everywhere the agent looks
        # (get_table / get_columns / schema render / relation name resolution).
        if table_id not in self._columns_cache:
            self._columns_cache[table_id] = await self.deps.semantic_repo.get_columns(
                table_id, only_enabled=True
            )
        return self._columns_cache[table_id]

    def resolve_qname(self, qname: str) -> dict[str, Any] | None:
        return self.by_qname.get(qname)

    async def column_guard_maps(
        self,
    ) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
        """(enabled_columns, disabled_columns) keyed by qname, for the SQL guard.
        Only tables that actually have a disabled column appear in either map, so
        the guard does no work for sources with nothing excluded."""
        if self._guard_maps is None:
            enabled: dict[str, list[str]] = {}
            disabled: dict[str, set[str]] = {}
            for qn, t in self.by_qname.items():
                cols = await self.deps.semantic_repo.get_columns(t["id"])  # all
                dis = {c["name"] for c in cols if not c.get("enabled", True)}
                if dis:
                    disabled[qn] = dis
                    enabled[qn] = [c["name"] for c in cols if c.get("enabled", True)]
            self._guard_maps = (enabled, disabled)
        return self._guard_maps


@dataclass
class Tool:
    name: str
    schema: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]
    label: Callable[[dict[str, Any]], str]
    terminal: bool = False  # finish — ends the loop
    interactive: bool = False  # ask_user — pauses the run


# ──────────────────────────────────────────────────────────────────────────
# Handlers
# ──────────────────────────────────────────────────────────────────────────


async def _list_tables(ctx: ToolContext, args: dict[str, Any]) -> Any:
    return {
        "tables": [
            {
                "qname": qn,
                "title": t.get("title"),
                "domain": t.get("domain"),
                "grain": t.get("grain"),
                "rows": t.get("total_rows"),
            }
            for qn, t in list(ctx.by_qname.items())[:MAX_TABLES_OVERVIEW]
        ]
    }


async def _get_table(ctx: ToolContext, args: dict[str, Any]) -> Any:
    qname = (args.get("qname") or "").strip()
    t = ctx.resolve_qname(qname)
    if not t:
        return {"error": f"Таблица {qname!r} не входит в источник. Сначала list_tables."}
    cols = await ctx.columns_of(t["id"])
    schema_md = render_schema([t], {str(t["id"]): cols}, max_tables=1)
    return {"qname": qname, "n_columns": len(cols), "schema": schema_md}


async def _get_columns(ctx: ToolContext, args: dict[str, Any]) -> Any:
    qname = (args.get("qname") or "").strip()
    names = args.get("names") or []
    t = ctx.resolve_qname(qname)
    if not t:
        return {"error": f"Таблица {qname!r} не входит в источник."}
    cols = await ctx.columns_of(t["id"])
    wanted = {str(n) for n in names} if names else None
    out = []
    for c in cols:
        if wanted is not None and c["name"] not in wanted:
            continue
        out.append(
            {
                "name": c["name"],
                "data_type": c["data_type"],
                "role": c.get("semantic_role"),
                "description": c.get("description"),
                "null_ratio": _num(c.get("null_ratio")),
                "distinct_count": c.get("distinct_count"),
                "examples": c.get("examples"),
                "value_catalog": _cap_catalog(c.get("value_catalog")),
                "value_range": c.get("value_range"),
                "in_primary_key": c.get("is_in_primary_key"),
                "in_sorting_key": c.get("is_in_sorting_key"),
                "in_partition_key": c.get("is_in_partition_key"),
                "semantics": c.get("semantics"),
            }
        )
    return {"qname": qname, "columns": out}


async def _search_knowledge(ctx: ToolContext, args: dict[str, Any]) -> Any:
    query = (args.get("query") or "").strip()
    k = int(args.get("k") or 6)
    k = max(1, min(k, 15))
    if not query:
        return {"error": "query пустой"}
    emb = await ctx.deps.embeddings.embed(query)
    notes = await ctx.deps.notes_repo.search(ctx.source_id, emb, limit=k)
    return {
        "notes": [
            {
                "title": n.get("title"),
                "scope": n.get("scope"),
                "score": _num(n.get("score")),
                "body": (n.get("body_md") or "")[:NOTE_BODY_CAP],
            }
            for n in notes
        ]
    }


async def _find_sql_recipes(ctx: ToolContext, args: dict[str, Any]) -> Any:
    intent = (args.get("intent") or "").strip()
    repo = ctx.deps.sql_recipe_repo
    recipes: list[dict[str, Any]] = []
    if intent:
        try:
            emb = await ctx.deps.embeddings.embed(intent)
            recipes = await repo.search_recipes(ctx.source_id, emb, limit=5)
        except Exception:  # noqa: BLE001 — degrade to listing all on embed failure
            recipes = await repo.list_recipes(ctx.source_id)
    else:
        recipes = await repo.list_recipes(ctx.source_id)
    return {
        "recipes": [
            {
                "title": r.get("title"),
                "intent": r.get("intent"),
                "sql": r.get("sql"),
                "tables": list(r.get("tables") or []),
            }
            for r in recipes
        ]
    }


async def _find_relations(ctx: ToolContext, args: dict[str, Any]) -> Any:
    qname = (args.get("qname") or "").strip()
    t = ctx.resolve_qname(qname)
    if not t:
        return {"error": f"Таблица {qname!r} не входит в источник."}
    tid = t["id"]
    rels = await ctx.deps.semantic_repo.get_relations(ctx.source_id, only_enabled=True)
    out = []
    for r in rels:
        if r["from_table_id"] != tid and r["to_table_id"] != tid:
            continue
        from_col = await _col_name(ctx, r["from_table_id"], r.get("from_column_id"))
        to_col = await _col_name(ctx, r["to_table_id"], r.get("to_column_id"))
        out.append(
            {
                "from": f"{ctx.id_to_qname.get(r['from_table_id'], '?')}.{from_col}",
                "to": f"{ctx.id_to_qname.get(r['to_table_id'], '?')}.{to_col}",
                "kind": r.get("kind"),
                "cardinality": r.get("cardinality"),
                "match_ratio": _num(r.get("match_ratio")),
                "confidence": _num(r.get("confidence")),
                "reasoning": r.get("reasoning"),
            }
        )
    return {"qname": qname, "relations": out}


async def _related_tables(ctx: ToolContext, args: dict[str, Any]) -> Any:
    qname = (args.get("qname") or "").strip()
    t = ctx.resolve_qname(qname)
    if not t:
        return {"error": f"Таблица {qname!r} не входит в источник."}
    # Multi-hop (1-2  references) neighbourhood from the Neo4j graph — surfaces
    # join partners reachable via intermediate tables, which the direct
    # find_relations would miss.
    neighbors = await ctx.deps.graph_repo.neighbors(str(t["id"]))
    out = []
    for n in neighbors:
        qn = f"{n.get('database')}.{n.get('name')}"
        # only expose tables that are part of this source's whitelist
        if qn in ctx.whitelist:
            out.append(qn)
    return {"qname": qname, "related": out}


async def _glossary_lookup(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = (args.get("term") or "").strip().lower()
    terms = await ctx.deps.semantic_repo.list_glossary(ctx.source_id)
    if term:
        terms = [
            g
            for g in terms
            if term in (g["term"] or "").lower()
            or any(term in (s or "").lower() for s in (g.get("synonyms") or []))
        ]
    return {"glossary": terms[:50]}


async def _list_metrics(ctx: ToolContext, args: dict[str, Any]) -> Any:
    return {"metrics": await ctx.deps.semantic_repo.list_metrics(ctx.source_id)}


async def _sample_rows(ctx: ToolContext, args: dict[str, Any]) -> Any:
    qname = (args.get("qname") or "").strip()
    n = max(1, min(int(args.get("n") or 5), 20))
    t = ctx.resolve_qname(qname)
    if not t:
        return {"error": f"Таблица {qname!r} не входит в источник."}
    client = await ctx.deps.ch_factory.for_source(ctx.source_id)
    try:
        profiler = CHProfiler(client)
        sample = await profiler.fetch_sample(t["database"], t["table_name"], limit=n)
    finally:
        await client.close()
    return {"qname": qname, **sample}


async def _distinct_values(ctx: ToolContext, args: dict[str, Any]) -> Any:
    qname = (args.get("qname") or "").strip()
    column = (args.get("column") or "").strip()
    limit = max(1, min(int(args.get("limit") or 30), CATALOG_CAP))
    t = ctx.resolve_qname(qname)
    if not t:
        return {"error": f"Таблица {qname!r} не входит в источник."}
    if not column:
        return {"error": "column обязателен"}
    # Disabled columns are out of scope — only probe enabled ones.
    if column not in {c["name"] for c in await ctx.columns_of(t["id"])}:
        return {"error": f"Колонка {column!r} недоступна в {qname}."}
    client = await ctx.deps.ch_factory.for_source(ctx.source_id)
    try:
        profiler = CHProfiler(client)
        catalog = await profiler.fetch_value_catalog(
            t["database"], t["table_name"], column, limit=limit
        )
    finally:
        await client.close()
    return {"qname": qname, "column": column, "values": catalog}


async def _run_sql(ctx: ToolContext, args: dict[str, Any]) -> Any:
    sql = (args.get("sql") or "").strip()
    if not sql:
        return {"error": "sql пустой", "kind": "guard"}
    enabled_cols, disabled_cols = await ctx.column_guard_maps()
    try:
        guard = validate_and_rewrite(
            sql,
            whitelist_qnames=ctx.whitelist,
            enabled_columns=enabled_cols,
            disabled_columns=disabled_cols,
            default_limit=ctx.deps.ch_default_limit,
            max_execution_time=ctx.deps.ch_max_execution_time,
        )
    except SqlGuardError as exc:
        return {"error": str(exc), "kind": "guard"}

    client = await ctx.deps.ch_factory.for_source(ctx.source_id)
    try:
        res = await client.query(guard.rewritten, settings=guard.settings or None)
        cols = list(res.column_names)
        rows = [list(r) for r in res.result_rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_sql execute failed", error=str(exc))
        return {"error": str(exc), "kind": "execute", "rewritten_sql": guard.rewritten}
    finally:
        await client.close()

    qid = ctx.next_query_id()
    ctx.results[qid] = StoredResult(
        query_id=qid, sql=guard.rewritten, columns=cols, rows=rows
    )
    return {
        "query_id": qid,
        "rewritten_sql": guard.rewritten,
        "columns": cols,
        "rows": [[_safe(v) for v in r] for r in rows[:ROW_VIEW_CAP]],
        "rowcount": len(rows),
        "truncated_view": len(rows) > ROW_VIEW_CAP,
    }


async def _ask_user(ctx: ToolContext, args: dict[str, Any]) -> Any:
    question = (args.get("question") or "").strip()
    choices = args.get("choices") or None
    schema = {"choices": choices} if choices else None
    answer = await ctx.run.await_user_input(
        question, schema, timeout=ctx.deps.answer_timeout_seconds
    )
    return {"answer": answer}


async def _confirm_plan(ctx: ToolContext, args: dict[str, Any]) -> Any:
    """Restate the understood task in PLAIN language and wait for a yes/no before
    writing SQL. The text is meant for a non-technical product manager — no table
    or column names. Pauses the run with Да/Нет choices (rendered as buttons)."""
    understanding = (args.get("understanding") or "").strip()
    filters = (args.get("filters") or "").strip()
    grain = (args.get("grain") or "").strip()

    # Markdown — the frontend renders this with the same Markdown component as the
    # final answer. Lead sentence, then plain-worded bullets for period & breakdown.
    parts: list[str] = []
    if understanding:
        parts.append(understanding)
    bullets: list[str] = []
    if filters:
        bullets.append(f"**За какой период / по каким данным:** {filters}")
    if grain:
        bullets.append(f"**Как разобьём результат:** {grain}")
    if bullets:
        parts.append("\n".join(f"- {b}" for b in bullets))
    question = "\n\n".join(parts) if parts else "Правильно понял задачу?"

    schema = {
        "kind": "plan",
        "choices": ["Да, считаем", "Нет — поправлю"],
        "understanding": understanding,
        "filters": filters,
        "grain": grain,
    }
    answer = await ctx.run.await_user_input(
        question, schema, timeout=ctx.deps.answer_timeout_seconds
    )
    return {"answer": answer}


async def _finish(ctx: ToolContext, args: dict[str, Any]) -> Any:
    # Terminal — actually handled by the loop. Defined so it is advertised to
    # the model with a proper schema.
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _safe(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return str(v)
    return v


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cap_catalog(catalog: Any) -> Any:
    if isinstance(catalog, list) and len(catalog) > CATALOG_CAP:
        return catalog[:CATALOG_CAP] + [{"value": "…", "count": None}]
    return catalog


async def _col_name(ctx: ToolContext, table_id: Any, column_id: Any) -> str | None:
    if not column_id:
        return None
    for c in await ctx.columns_of(table_id):
        if c["id"] == column_id:
            return c["name"]
    return None


# ──────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────


def _fn(name: str, description: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }


_OBJ = "object"
_STR = {"type": "string"}


def build_registry() -> dict[str, Tool]:
    """All tools the client agent can call, with native OpenAI schemas."""
    tools: list[Tool] = [
        Tool(
            name="list_tables",
            schema=_fn(
                "list_tables",
                "Обзор всех таблиц источника: qname, заголовок, домен, "
                "гранулярность, число строк. Дёшево — начни отсюда.",
                {"type": _OBJ, "properties": {}, "required": []},
            ),
            handler=_list_tables,
            label=lambda a: "Смотрю список таблиц",
        ),
        Tool(
            name="get_table",
            schema=_fn(
                "get_table",
                "Полное описание таблицы и ВСЕХ её колонок (типы, роли, ключи, "
                "категориальные значения, диапазоны). Главный инструмент изучения.",
                {
                    "type": _OBJ,
                    "properties": {"qname": {**_STR, "description": "database.table"}},
                    "required": ["qname"],
                },
            ),
            handler=_get_table,
            label=lambda a: f"Изучаю таблицу {a.get('qname', '')}",
        ),
        Tool(
            name="get_columns",
            schema=_fn(
                "get_columns",
                "Глубокий разбор конкретных колонок: семантика, значения-смыслы, "
                "PII, caveats, рекомендуемая агрегация. Для точечного уточнения.",
                {
                    "type": _OBJ,
                    "properties": {
                        "qname": {**_STR, "description": "database.table"},
                        "names": {"type": "array", "items": _STR},
                    },
                    "required": ["qname", "names"],
                },
            ),
            handler=_get_columns,
            label=lambda a: f"Уточняю колонки {a.get('qname', '')}",
        ),
        Tool(
            name="search_knowledge",
            schema=_fn(
                "search_knowledge",
                "Семантический поиск (RAG) по заметкам о данных — бизнес-смыслы, "
                "определения, нюансы. Используй на естественном языке.",
                {
                    "type": _OBJ,
                    "properties": {
                        "query": _STR,
                        "k": {"type": "integer", "description": "сколько заметок, 1-15"},
                    },
                    "required": ["query"],
                },
            ),
            handler=_search_knowledge,
            label=lambda a: f"Ищу в заметках: {(a.get('query') or '')[:40]}",
        ),
        Tool(
            name="find_sql_recipes",
            schema=_fn(
                "find_sql_recipes",
                "ОБЯЗАТЕЛЬНО перед confirm_plan/run_sql: проверить типовые SQL-рецепты "
                "источника по смыслу задачи. Возвращает готовые эталонные запросы "
                "(название, когда применять, дословный SQL). Если подходящий рецепт "
                "есть — бери его SQL за основу, а не изобретай свой.",
                {
                    "type": _OBJ,
                    "properties": {
                        "intent": {
                            **_STR,
                            "description": "смысл задачи на естественном языке",
                        }
                    },
                    "required": ["intent"],
                },
            ),
            handler=_find_sql_recipes,
            label=lambda a: f"Ищу SQL-рецепты: {(a.get('intent') or '')[:40]}",
        ),
        Tool(
            name="find_relations",
            schema=_fn(
                "find_relations",
                "Связи таблицы с другими (FK/inferred): партнёры для JOIN, "
                "кардинальность, доля совпадений ключей.",
                {
                    "type": _OBJ,
                    "properties": {"qname": {**_STR, "description": "database.table"}},
                    "required": ["qname"],
                },
            ),
            handler=_find_relations,
            label=lambda a: f"Связи таблицы {a.get('qname', '')}",
        ),
        Tool(
            name="related_tables",
            schema=_fn(
                "related_tables",
                "Связанные таблицы из графа знаний (Neo4j), включая косвенные "
                "связи через промежуточные таблицы (1–2 перехода). Помогает найти "
                "путь для JOIN, когда таблицы связаны не напрямую.",
                {
                    "type": _OBJ,
                    "properties": {"qname": {**_STR, "description": "database.table"}},
                    "required": ["qname"],
                },
            ),
            handler=_related_tables,
            label=lambda a: f"Граф связей {a.get('qname', '')}",
        ),
        Tool(
            name="glossary_lookup",
            schema=_fn(
                "glossary_lookup",
                "Канонические термины и определения предметной области. Без term — "
                "весь глоссарий.",
                {
                    "type": _OBJ,
                    "properties": {"term": _STR},
                    "required": [],
                },
            ),
            handler=_glossary_lookup,
            label=lambda a: "Смотрю глоссарий",
        ),
        Tool(
            name="list_metrics",
            schema=_fn(
                "list_metrics",
                "Предопределённые метрики источника (имя, выражение, единицы).",
                {"type": _OBJ, "properties": {}, "required": []},
            ),
            handler=_list_metrics,
            label=lambda a: "Смотрю метрики",
        ),
        Tool(
            name="sample_rows",
            schema=_fn(
                "sample_rows",
                "Живые примеры строк таблицы из ClickHouse (когда описаний мало).",
                {
                    "type": _OBJ,
                    "properties": {
                        "qname": {**_STR, "description": "database.table"},
                        "n": {"type": "integer", "description": "1-20"},
                    },
                    "required": ["qname"],
                },
            ),
            handler=_sample_rows,
            label=lambda a: f"Живые строки {a.get('qname', '')}",
        ),
        Tool(
            name="distinct_values",
            schema=_fn(
                "distinct_values",
                "Реальные значения колонки с частотами (для точных фильтров).",
                {
                    "type": _OBJ,
                    "properties": {
                        "qname": {**_STR, "description": "database.table"},
                        "column": _STR,
                        "limit": {"type": "integer", "description": "1-50"},
                    },
                    "required": ["qname", "column"],
                },
            ),
            handler=_distinct_values,
            label=lambda a: f"Значения {a.get('qname', '')}.{a.get('column', '')}",
        ),
        Tool(
            name="run_sql",
            schema=_fn(
                "run_sql",
                "Выполнить ClickHouse SELECT/WITH. Запрос проходит guard (whitelist "
                "таблиц, авто-LIMIT, таймаут). Только так можно получить числа. "
                "Возвращает query_id, колонки и первые строки.",
                {
                    "type": _OBJ,
                    "properties": {"sql": {**_STR, "description": "один SELECT/WITH"}},
                    "required": ["sql"],
                },
            ),
            handler=_run_sql,
            label=lambda a: "Выполняю SQL",
        ),
        Tool(
            name="confirm_plan",
            schema=_fn(
                "confirm_plan",
                "ОБЯЗАТЕЛЬНО перед написанием SQL: простыми словами переформулируй, "
                "как ты понял задачу, и дождись подтверждения. Пиши так, чтобы понял "
                "продакт, который НИКОГДА не видел SQL: НИКАКИХ названий таблиц, "
                "колонок, функций и технических терминов — только бизнес-смысл. "
                "Вызывай ТОЛЬКО после изучения схемы/заметок. Пользователь ответит "
                "«Да, считаем» или поправит.",
                {
                    "type": _OBJ,
                    "properties": {
                        "understanding": {
                            **_STR,
                            "description": (
                                "Что именно посчитаем или покажем — простыми словами для "
                                "непрофессионала, без названий таблиц/колонок и SQL-терминов"
                            ),
                        },
                        "filters": {
                            **_STR,
                            "description": (
                                "За какой период и по каким данным считаем — простыми "
                                "словами (если уместно)"
                            ),
                        },
                        "grain": {
                            **_STR,
                            "description": (
                                "Как разобьём результат — простыми словами, напр. «по "
                                "месяцам», «по городам» (если уместно)"
                            ),
                        },
                    },
                    "required": ["understanding"],
                },
            ),
            handler=_confirm_plan,
            label=lambda a: "Уточняю постановку задачи",
            interactive=True,
        ),
        Tool(
            name="ask_user",
            schema=_fn(
                "ask_user",
                "Задать пользователю уточняющий вопрос и дождаться ответа. Только "
                "когда реально заблокирован неоднозначностью — иначе действуй сам.",
                {
                    "type": _OBJ,
                    "properties": {
                        "question": _STR,
                        "choices": {"type": "array", "items": _STR},
                    },
                    "required": ["question"],
                },
            ),
            handler=_ask_user,
            label=lambda a: "Уточняю у пользователя",
            interactive=True,
        ),
        Tool(
            name="finish",
            schema=_fn(
                "finish",
                "Завершить и дать пользователю итоговый ответ. summary — текст на "
                "русском. result_from — query_id запроса, чья таблица показывается "
                "и экспортируется (по умолчанию последний run_sql).",
                {
                    "type": _OBJ,
                    "properties": {
                        "summary": _STR,
                        "result_from": {**_STR, "description": "query_id, напр. q2"},
                    },
                    "required": ["summary"],
                },
            ),
            handler=_finish,
            label=lambda a: "Формирую ответ",
            terminal=True,
        ),
    ]
    return {t.name: t for t in tools}
