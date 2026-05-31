# 🫏 Аналитический Ослик

Спросите данные обычными словами — Ослик принесёт отчёт. Веб-приложение с двумя пайплайнами поверх ClickHouse:

1. **Админский (профилирование)** — глубоко изучает источник данных в два прохода (сухой сбор структуры → групповое LLM-профилирование с инбоксом вопросов), строит семантический слой (PostgreSQL), граф связей (Neo4j) и md-заметки с эмбеддингами (pgvector). Состояние задач durable — прогон возобновляем и ни одна колонка не теряется.
2. **Клиентский (text → report)** — **агент в ReAct-цикле с нативным OpenAI tool-calling**. Атомарные и «массивные» инструменты дают ему весь ретрив (RAG по заметкам, граф Neo4j, семантический слой, живой просмотр данных) и `run_sql` под guard'ом. Сам решает: ответить сразу, глубоко проанализировать или задать уточняющий вопрос. Диалог живёт в **сессии** — полный thread tool_calls переносится между ходами. Все шаги стримятся через SSE — никакого black-box.

Один общий стек: FastAPI + Dishka + asyncio (бэкенд), Next.js 14 + Tailwind (фронт), Docker Compose для развёртывания.

---

## Содержание

1. [Архитектура](#архитектура)
2. [Стек технологий](#стек-технологий)
3. [Структура проекта](#структура-проекта)
4. [Быстрый старт](#быстрый-старт)
5. [Полная настройка](#полная-настройка)
6. [Переменные окружения](#переменные-окружения)
7. [Тестовые данные (seeder)](#тестовые-данные-seeder)
8. [Админский пайплайн](#админский-пайплайн)
9. [Клиентский пайплайн](#клиентский-пайплайн)
10. [SSE-протокол](#sse-протокол)
11. [SQL guard](#sql-guard)
12. [Knowledge graph](#knowledge-graph)
13. [REST API](#rest-api)
14. [Маршруты фронта](#маршруты-фронта)
15. [Тесты](#тесты)
16. [Деплой и обслуживание](#деплой-и-обслуживание)
17. [Troubleshooting](#troubleshooting)
18. [Ограничения MVP](#ограничения-mvp)

---

## Архитектура

```
                      ┌────────────────────────────────────────────┐
                      │       Браузер (Next.js на :3000)           │
                      │  ── /admin     — админка                   │
                      │  ── /          — клиентский чат            │
                      └─────────────────────┬──────────────────────┘
                                            │ HTTP + SSE
                                            ▼
                      ┌────────────────────────────────────────────┐
                      │      FastAPI backend (:8000)               │
                      │                                            │
                      │  ┌──────────────┐    ┌──────────────────┐  │
                      │  │   REST API   │    │  SSE endpoints   │  │
                      │  │ (admin/client│    │  Last-Event-ID   │  │
                      │  └──────┬───────┘    └─────────┬────────┘  │
                      │         │                      │           │
                      │         ▼                      ▼           │
                      │  ┌─────────────────────────────────────┐   │
                      │  │     Dishka DI (App + Request)       │   │
                      │  └────────────┬────────────────────────┘   │
                      │               │                            │
                      │    ┌──────────┴──────────┐                 │
                      │    ▼                     ▼                 │
                      │ Services            AgentRun Registry      │
                      │ (auth, source,      (asyncio Tasks +       │
                      │  selection,         Events Bus +           │
                      │  profiling,         Replay Buffer)         │
                      │  edit, semantic,    │                      │
                      │  task, session)     ▼                      │
                      │                  Pipelines:                │
                      │                   - admin_profiling        │
                      │                   - admin_edit             │
                      │                   - client_task            │
                      │                                            │
                      │  ┌──────────────┐    ┌──────────────────┐  │
                      │  │ LLM Client   │    │ Embeddings       │  │
                      │  │(OpenAI-comp.)│    │(OpenAI-comp.)    │  │
                      │  └──────┬───────┘    └─────────┬────────┘  │
                      └─────────┼──────────────────────┼───────────┘
                                │                      │
              ┌─────────────────┼──────────────────────┼──────────────────┐
              ▼                 ▼                      ▼                  ▼
       ┌────────────┐   ┌────────────┐         ┌────────────┐    ┌────────────┐
       │ PostgreSQL │   │   Neo4j    │         │  External  │    │ ClickHouse │
       │ + pgvector │   │  (graph)   │         │   LLM      │    │ (источник  │
       │            │   │            │         │  DeepSeek/ │    │  данных)   │
       │ - sem_*    │   │ - Tables   │         │  GLM/...   │    │            │
       │ - md_notes │   │ - Columns  │         │            │    │ ← демо     │
       │ - audit    │   │ - REL_FK   │         │   bge-m3   │    │   seeder   │
       │ - tasks    │   │ - REL_INF  │         │   эмбеддер │    │   (23 табл,│
       │ - chats    │   │ - Concept  │         │            │    │   ~500k    │
       │ - selections│  │            │         │            │    │   строк)   │
       └────────────┘   └────────────┘         └────────────┘    └────────────┘
```

### Жизненный цикл данных

1. **Discover** — Backend читает `system.tables` источника, возвращает фронту список с метаданными.
2. **Selection** — Админ ставит чекбоксы → PUT в `source_table_selections`. Этот whitelist становится единственным набором, который видят оба пайплайна и SQL-guard.
3. **Profiling** — Pipeline идёт по whitelist'у: для каждой таблицы DDL + sample(100) + count/distinct/null% + query_log → LLM описание → задаёт админу 0–3 уточняющих вопроса (через SSE `awaiting_input`) → перегенерация описания с учётом ответа → upsert в `sem_tables`/`sem_columns`/`sem_relations` + merge в Neo4j + md-заметка + embed.
4. **Edit** — Админ редактирует поля через UI или текстовой командой («уточни описание X с учётом Y», «добавь связь A→B»). Pipeline парсит intent, применяет изменения с записью в `sem_revisions` (история) и `audit_log`.
5. **Client task** — Клиент задаёт вопрос → intent parse → retrieve context (kNN по embeddings + BFS Neo4j + рендер схемы) → clarification (опционально) → generate SQL → validate (sqlglot + whitelist + auto-LIMIT + max_execution_time) → execute → summarize → preview + XLSX.

---

## Стек технологий

| Слой | Технологии |
|---|---|
| Backend язык | Python 3.12 (asyncio) |
| Web | FastAPI + Uvicorn, sse-starlette (SSE), slowapi (rate limit) |
| DI | Dishka — App-scope (клиенты LLM/эмбеддингов/Neo4j, sessionmaker) + Request-scope (репозитории/сервисы) |
| БД-доступ | SQLAlchemy 2.x (async) + asyncpg, pgvector |
| Graph | Neo4j 5 async-драйвер (bolt) |
| ClickHouse | clickhouse-connect (HTTP, async) — **только чтение** источника |
| LLM | openai SDK (`AsyncOpenAI`) — любой OpenAI-совместимый провайдер |
| Эмбеддинги | bge-m3 → `vector(1024)`, через OpenAI-совместимый эндпоинт |
| SQL safety | sqlglot (guard: парсинг + переписывание) |
| Export | openpyxl (XLSX) |
| Security | cryptography (Fernet — пароли источников), bcrypt (пароль админа), pyjwt (cookie-сессия) |
| Logging | structlog (JSON в prod) + `request_id` middleware |
| Frontend язык | TypeScript |
| Framework | Next.js 14 (App Router), React 18 |
| UI | Tailwind CSS (тёплая палитра), Radix UI primitives (shadcn-style), lucide-react, sonner (toasts) |
| Формы/состояние | Zustand, react-hook-form + zod |
| Markdown/SQL | react-markdown + remark-gfm; SQL-редактор: react-simple-code-editor + prismjs + sql-formatter |
| Тесты бэка | pytest + pytest-asyncio + testcontainers (Postgres) |
| Тесты фронта | Vitest + @testing-library/react + jsdom |
| Хранилища | PostgreSQL 16 + pgvector, Neo4j 5.20 (+ APOC), ClickHouse 24.8 |
| Контейнеризация | Docker + Docker Compose v2 (dev + prod), nginx reverse-proxy, Let's Encrypt TLS |

### Модель и LLM-провайдер

**Провайдер-агностично.** Бэкенд работает с любым OpenAI-совместимым API (DeepSeek, GLM, OpenRouter, локальные сервы) — модель задаётся переменными `T2R_LLM_*`, генеративная и эмбеддинг-модель раздельные. Дефолт стека — DeepSeek (генерация) + bge-m3 (эмбеддинги, 1024-мерные).

- **Параметры генерации** (`settings.py`): `temperature=0.2` (фактологичность), `max_tokens=4096`; для структурного ингеста глоссария — `8192` (длинный JSON-ответ иначе обрезался).
- **Устойчивость к зависшему апстриму**: `llm_request_timeout=60s` × `llm_max_retries=1` (вместо дефолтных 600s×2 у SDK) — один вызов ограничен ~2 минутами, UI не висит в спиннере.
- **OpenRouter pinning**: `T2R_LLM_OPENROUTER_PROVIDER` фиксирует один апстрим (например, чтобы не прыгать между провайдерами по латентности).
- **Эмбеддинги**: вход усекается до 16000 символов (контекст bge-m3 — 8192 токена; широкая заметка о таблице иначе крашила эмбеддер). Хранится полный текст заметки, режется только вход вектора. Поиск — косинус по pgvector (`<=>`).
- **Парсинг ответов LLM** терпим к «грязному» выводу слабых моделей (`extract_json` + фоллбэки) — кривой JSON в tool-call не валит ход агента.

### Инфраструктура — как всё крутится

**Контейнеры (Docker Compose).** Пять сервисов: `postgres` (pgvector), `neo4j` (+APOC), `clickhouse`, `backend` (uvicorn), `frontend` (Next.js). В проде добавляется `nginx`. Dev (`docker-compose.yml`) пробрасывает порты наружу; prod (`docker-compose.prod.yml`) прячет всё за nginx и имеет собственные volume'ы (не пересекаются с dev). Демо-данные грузятся отдельным профилем `--profile seed` (контейнер `ch-seeder`).

**Single-domain reverse-proxy.** nginx отдаёт фронт и проксирует `/api/*` в бэкенд на **одном домене** (`analyticaloslik.com`) → cookie остаются first-party, CORS не нужен. Для SSE отключены буферизация и кэш, `read_timeout=3600s` (долгие стримы профилирования/агента не рвутся). `client_max_body_size 25m` под выгрузки.

**TLS и сеть.** Сертификат Let's Encrypt выпускается на хосте (`certbot --standalone`) и монтируется в nginx read-only; HTTP→HTTPS редирект + HSTS в проде. Cloudflare в режиме **DNS-only** (серое облако) — клиенты ходят прямо на origin:443. Прод-окружение — EC2 `t3.large`. Бэкенд доверяет `X-Forwarded-*` только от nginx (`--proxy-headers --forwarded-allow-ips=*`).

**Запуск и обновление.** Миграции применяются **автоматически на старте** (`apply_pending` в lifespan). При старте же отрабатывают **recovery-хуки**: осиротевшие после рестарта `profiling_runs` и `task_runs` корректно закрываются (а не висят вечно в `running`, блокируя новые старты). Деплой — `git pull main` + `docker compose -f docker/docker-compose.prod.yml up -d --build` (детали в [DEPLOY.md](DEPLOY.md) и [DEPLOY_RUNBOOK.md](DEPLOY_RUNBOOK.md)).

### Ключевые инженерные решения

- **Три слоя знаний, один источник правды.** PostgreSQL (`sem_*`) — истина; pgvector `md_notes` (RAG по смыслу) и Neo4j (граф связей) — производные, идемпотентно пересобираемые из PG (коммит в PG идёт **до** синка графа, чтобы граф никогда не опережал откатываемую транзакцию).
- **Durable-очередь профилирования.** Задачи живут в Postgres (`profiling_tasks`), забираются атомарно (`FOR UPDATE SKIP LOCKED`) с учётом зависимостей. Прогон **возобновляем**, переживает рестарт backend, а coverage-инвариант гарантирует, что **ни одна колонка не потеряется** (нет описания → ретрай, а не «тихо пропустить»).
- **Идемпотентные старты.** Один активный прогон на источник / одна активная задача на сессию — через partial unique index; двойной сабмит или SSE-реконнект **переиспользуют** живой `AgentRun`, а не плодят дубли.
- **SSE с реплеем.** Монотонный `id` события + заголовок `Last-Event-ID` → при F5/обрыве сервер досылает пропущенное из replay-буфера; профилирование при уходе со страницы **не прерывается**.
- **SQL guard между агентом и базой.** sqlglot-валидация: только чтение, whitelist таблиц **и колонок**, запрет внешних IO-функций (`url/s3/file/remote/...`) и DDL/DML, авто-`max_execution_time`. Агент физически не может это обойти — guard зашит внутрь `run_sql`.
- **Персистентный thread агента.** Полный OpenAI-диалог (`tool_calls` + наблюдения) хранится в `agent_messages` и реиграется на каждом ходу; обрезка по границам ходов + бюджет символов держат контекст под лимитом → follow-up продолжает контекст, а не исследует заново.
- **Защита ручной работы.** Флаги `locked`/`enabled`: отредактированные/подтверждённые описания и выбор колонок **переживают ре-профайл** (структурные факты обновляются, контент — нет).
- **Безопасность по умолчанию.** Пароли источников шифруются Fernet, пароль админа — bcrypt, клиентская часть за UUID-`access_key`-гейтом, rate-limit на логин/доступ/задачи, security-заголовки (`nosniff`/`DENY`/HSTS), structlog с `request_id` для трассировки.

---

## Структура проекта

```
texttoreport/
├── backend/
│   ├── .env                       — секреты + URL'ы LLM
│   ├── .env.example
│   ├── pyproject.toml
│   ├── scripts/
│   │   ├── migrate.py             — ручной runner миграций (CLI)
│   │   └── create_admin_hash.py   — генератор bcrypt-хеша пароля админа
│   ├── src/t2r/
│   │   ├── main.py                — create_app(), lifespan, middleware
│   │   ├── settings.py            — pydantic-settings (T2R_* env vars)
│   │   ├── logging.py             — structlog + request_id
│   │   ├── errors.py              — DomainError, NotFoundError, ...
│   │   ├── di/                    — Dishka providers
│   │   ├── api/
│   │   │   ├── admin/             — admin endpoints (auth/sources/profiling/tables/edit/audit/graph/selection)
│   │   │   ├── client/            — public endpoints (session/tasks/sources)
│   │   │   └── common/            — health, SSE helper
│   │   ├── domain/
│   │   │   ├── models/            — pydantic-модели
│   │   │   └── events/types.py    — типизированные SSE-события
│   │   ├── infra/
│   │   │   ├── db/                — engine, миграции, репозитории Postgres
│   │   │   ├── graph/             — Neo4j driver, Cypher шаблоны
│   │   │   ├── clickhouse/        — async-клиент, profiler, factory, permission probe
│   │   │   ├── llm/               — OpenAI-совместимые клиенты, PromptLoader
│   │   │   ├── security/          — Fernet, JWT, bcrypt, sql_guard
│   │   │   ├── rate_limit/        — slowapi limiter
│   │   │   └── export/xlsx.py     — openpyxl writer
│   │   ├── agents/
│   │   │   ├── orchestrator/      — AgentRun, Step, Pipeline, EventsBus, Registry
│   │   │   ├── admin_profiling/   — two-pass профилирование (pass1/pass2/scheduler)
│   │   │   ├── admin_edit/        — free-form-команды
│   │   │   ├── client_agent/      — ReAct-агент: loop.py, tools.py, deps.py
│   │   │   ├── tools/             — schema_renderer и т.д.
│   │   │   └── prompts/*.md       — все промпты (jinja2), вкл. client_agent.md
│   │   └── services/              — task_/profiling_/semantic_/edit_/selection_/source_/session_/auth_
│   └── tests/
│       ├── conftest.py
│       ├── unit/
│       └── integration/           — 8 тестов (testcontainers Postgres)
│
├── frontend/
│   ├── .env.local
│   ├── package.json
│   ├── tailwind.config.ts         — тёплая палитра (cream / орех / оранж)
│   ├── tsconfig.json
│   ├── next.config.js
│   ├── middleware.ts              — гейт /admin/*
│   ├── app/
│   │   ├── layout.tsx, globals.css
│   │   ├── (client)/              — публичный чат: /, /chat/[sessionId]
│   │   └── admin/
│   │       ├── (auth)/login       — форма логина
│   │       └── (protected)/
│   │           ├── page.tsx       — дашборд источников
│   │           └── sources/
│   │               ├── new        — создание + test-connection
│   │               └── [id]/
│   │                   ├── page.tsx        — выбор таблиц + запуски + сем-слой
│   │                   ├── chat            — свободная команда админу
│   │                   ├── graph           — knowledge-graph
│   │                   ├── runs/[runId]    — live SSE + clarification
│   │                   └── tables/[id]     — редактор + regenerate
│   ├── components/
│   │   ├── ui/                    — Button/Card/Input/Label
│   │   ├── chat/                  — AgentStatusTimeline/MessageBubble/ChatRunner/
│   │   │                            ChatComposer/ClarificationForm/TablePreview/SqlBlock
│   │   ├── admin/                 — GraphView, LogoutButton
│   │   └── providers/
│   ├── hooks/                     — useSSE, useTask (FSM)
│   ├── lib/                       — api client, sse parser, events types, utils
│   ├── styles/fonts.ts
│   └── tests/                     — 13 vitest-тестов
│
├── migrations/
│   ├── 0001_init.sql              — extensions (pgcrypto, vector), schema_migrations
│   ├── 0002_data_sources.sql      — источники + зашифрованные креды
│   ├── 0003_profiling.sql         — profiling_runs, profiling_run_tables
│   ├── 0004_chat.sql              — chat_sessions, chat_messages, client_sessions_meta
│   ├── 0005_semantic_layer.sql    — sem_tables, sem_columns, sem_relations, sem_metrics, sem_glossary, sem_revisions
│   ├── 0006_md_notes_embeddings.sql
│   ├── 0007_audit_log.sql
│   ├── 0008_llm_calls.sql
│   ├── 0009_task_runs.sql
│   ├── 0010_resize_md_notes_embedding.sql  — vector(1536) → vector(1024) под bge-m3
│   └── 0011_source_table_selections.sql    — whitelist админа
│
└── docker/
    ├── docker-compose.yml         — 5 сервисов + ch-seeder (profile=seed)
    ├── Dockerfile.backend
    ├── Dockerfile.frontend
    └── seed/                      — отдельный модуль наполнения CH
        ├── schema.sql             — 23 таблицы e-commerce/SaaS DWH
        ├── seed.py                — генератор ~500k строк, фиксированный seed=42
        ├── Dockerfile
        ├── requirements.txt
        └── README.md
```

---

## Быстрый старт

```bash
# 1. Заполнить ключ LLM (по умолчанию настроен на DeepSeek + bge-m3)
nano backend/.env  # T2R_LLM_API_KEY и T2R_EMB_API_KEY

# 2. Поднять стек
docker compose -f docker/docker-compose.yml up -d

# 3. (один раз) Залить демо-данные в ClickHouse
docker compose -f docker/docker-compose.yml --profile seed run --rm ch-seeder
```

UI: <http://localhost:3000> · API: <http://localhost:8000>  
Логин админа: **admin / admin** (поменять через `scripts/create_admin_hash.py`).

---

## Полная настройка

### 1. Сгенерировать секреты (если нужны новые)

```bash
# Fernet-ключ для шифрования паролей источников
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT-secret
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# bcrypt-хеш пароля админа
docker compose -f docker/docker-compose.yml exec backend python -m scripts.create_admin_hash <password>
```

Вписать в `backend/.env`:
- `T2R_ENCRYPTION_KEY=` (Fernet)
- `T2R_JWT_SECRET=`
- `T2R_ADMIN_PASSWORD_HASH=` (bcrypt)

> ⚠️ **Гача с docker-compose**: bcrypt-хеш содержит `$` в формате `$2b$12$h4pv…`. Compose интерпретирует `$h4pv…` в env_file как переменную и подставляет пусто. В `docker/docker-compose.yml` хеш дублируется в `environment:` блок с экранированием `$$`. Если меняешь пароль — обнови **оба** места.

### 2. LLM-провайдер

В `backend/.env`:

```bash
T2R_LLM_API_URL=https://api.deepseek.com/v1   # любой OpenAI-совместимый
T2R_LLM_API_KEY=sk-...
T2R_LLM_MODEL=deepseek-chat                   # gpt-4o, qwen-max, glm-4.5 — всё что принимает chat.completions

T2R_EMB_API_URL=https://api.deepinfra.com/v1/openai
T2R_EMB_API_KEY=...
T2R_EMB_MODEL=BAAI/bge-m3
T2R_EMB_DIM=1024
```

> Если меняешь модель эмбеддингов с другим dim — нужна новая миграция `ALTER COLUMN md_notes.embedding TYPE vector(N)`.

### 3. Запуск

```bash
docker compose -f docker/docker-compose.yml up -d
```

Что произойдёт:
1. Поднимутся postgres+pgvector, neo4j 5, clickhouse 24.8, backend, frontend.
2. Backend в `lifespan` автоматически применит все недостающие миграции из `/migrations/*.sql`.
3. `/readyz` начнёт возвращать `{"postgres":"ok","neo4j":"ok"}` через 10-20 секунд.

### 4. Опционально — демо-данные

```bash
docker compose -f docker/docker-compose.yml --profile seed run --rm ch-seeder
```

Создаст в ClickHouse базу `demo` с 23 таблицами и ~500k строк.

### 5. Открыть UI

- <http://localhost:3000/admin/login> → `admin/admin` → редирект на дашборд
- Добавить источник: host `clickhouse`, port `8123`, database `demo`, user `demo`, password `demo`
- Тест connection → readonly probe (предупредит если у юзера есть write-права)
- Выбрать таблицы → запустить профилирование
- После завершения — <http://localhost:3000/> для клиентского чата

---

## Переменные окружения

### Backend (`backend/.env`)

| Переменная | Дефолт | Описание |
|---|---|---|
| `T2R_ENV` | `dev` | `dev` / `prod` (влияет на `Secure` cookies) |
| `T2R_LOG_LEVEL` | `INFO` | structlog уровень |
| `T2R_CORS_ORIGINS` | `http://localhost:3000` | через запятую |
| `T2R_ADMIN_LOGIN` | `admin` | логин админа |
| `T2R_ADMIN_PASSWORD_HASH` | — | bcrypt(`admin`) по умолчанию |
| `T2R_JWT_SECRET` | — | секрет для подписи JWT |
| `T2R_JWT_TTL_SECONDS` | `86400` | TTL admin-cookie |
| `T2R_ENCRYPTION_KEY` | — | Fernet-key для шифрования credentials источников |
| `T2R_PG_DSN` | `postgresql+asyncpg://t2r:t2r@postgres:5432/t2r` | основная БД |
| `T2R_NEO4J_URI` | `bolt://neo4j:7687` | |
| `T2R_NEO4J_USER` | `neo4j` | |
| `T2R_NEO4J_PASSWORD` | `t2r_neo4j_pass` | |
| `T2R_LLM_API_URL` | `https://api.deepseek.com/v1` | OpenAI-совместимый base URL |
| `T2R_LLM_API_KEY` | — | ключ |
| `T2R_LLM_MODEL` | `deepseek-chat` | имя модели |
| `T2R_LLM_TEMPERATURE` | `0.2` | |
| `T2R_LLM_MAX_TOKENS` | `4096` | |
| `T2R_EMB_API_URL` | `https://api.openai.com/v1` | |
| `T2R_EMB_API_KEY` | — | |
| `T2R_EMB_MODEL` | `text-embedding-3-small` | (или `BAAI/bge-m3`) |
| `T2R_EMB_DIM` | `1536` | (должен совпадать с миграцией) |
| `T2R_CH_DEFAULT_MAX_EXECUTION_TIME` | `30` | sec — авто-SETTINGS каждого SQL |
| `T2R_CH_DEFAULT_LIMIT` | `10000` | авто-LIMIT N |
| `T2R_SSE_PING_INTERVAL` | `15` | sec — heartbeat |
| `T2R_CLIENT_RATE_LIMIT` | `10/minute` | slowapi |
| `T2R_EXPORT_DIR` | `/var/t2r/exports` | путь в контейнере для XLSX |

Алиасы: `T2R_LLM_BASE_URL` ⇄ `T2R_LLM_API_URL`, `T2R_EMB_BASE_URL` ⇄ `T2R_EMB_API_URL`.

### Frontend (`frontend/.env.local`)

| Переменная | Дефолт |
|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` |

---

## Тестовые данные (seeder)

Модуль `docker/seed/` поднимает в ClickHouse полноценный DWH (e-commerce + SaaS) для тестирования.

### Что внутри

- **23 таблицы**: `customers`, `employees`, `brands`, `categories` (само-связь `parent_id`), `products`, `suppliers`, `product_suppliers` (m2m), `warehouses`, `inventory`, `subscription_plans`, `currency_rates`, `orders`, `order_items`, `payments`, `shipments`, `refunds`, `reviews`, `web_events` (~300k строк), `marketing_campaigns`, `campaign_clicks` (~40k), `subscriptions`, `support_tickets`, `ticket_messages`.
- **~500k строк** при `SEED_PROFILE=full`, ~12k при `small`.
- **~22 inferred FK** (явных FK у CH нет — это даёт реальный материал агенту).
- Прогревает `system.query_log` 22 аналитическими запросами.
- Random seed `42` — повторные запуски детерминированы.

### Запуск

```bash
# Полный профиль
docker compose -f docker/docker-compose.yml --profile seed run --rm ch-seeder

# Маленький профиль:
SEED_PROFILE=small docker compose -f docker/docker-compose.yml --profile seed run --rm ch-seeder
```

---

## Админский пайплайн

### Шаг 1 — Discover

`GET /api/admin/sources/{id}/discover` → читает `system.tables` источника, возвращает список: `database, table, engine, total_rows, total_bytes, selected`.

### Шаг 2 — Selection (whitelist)

Админ ставит чекбоксы → `PUT /api/admin/sources/{id}/selection` → таблица `source_table_selections`. Без непустого whitelist'а профилирование откажется стартовать (`422 VALIDATION`).

**Whitelist — single source of truth**: те же таблицы видит SQL-guard клиентского пайплайна.

### Шаг 3 — Profiling

`POST /api/admin/profiling/runs` создаёт `AgentRun`, кладёт в `RunRegistry` (in-memory), запускает фоновую asyncio-таску.

Для каждой таблицы из whitelist'а:

1. `FetchDDL` — `SHOW CREATE TABLE`
2. `FetchSample` — `SELECT * LIMIT 100`
3. `FetchColumnStats` — `count() / uniqHLL12 / countIf(isNull)` за один запрос
4. `FetchColumnExamples` — 5 distinct-значений на колонку
5. `FetchUsageStats` — top-20 запросов из `system.query_log` за 14 дней
6. **`DescribeTable` (LLM)** — промпт `table_describer.md` возвращает JSON `{title, description, domain, tags, questions[]}`
7. **`AskAdmin`** — если есть `questions[]`: `await_user_input` → SSE `awaiting_input` → админ отвечает → `RefineDescription` (промпт `table_describer_refine.md`) переписывает с учётом ответа. Ответ сохраняется в `sem_tables.user_notes`.
8. `DescribeColumns` — батчем LLM возвращает массив `[{name, description, semantic_role}]`
9. `InferRelations` — LLM ищет связи к уже описанным таблицам этого run-а
10. `WriteSemanticLayer` — upsert в `sem_tables`/`sem_columns`/`sem_relations`
11. `UpdateGraph` — Cypher MERGE в Neo4j
12. `WriteMdNote` + `EmbedNote` — md-резюме + векторизация → `md_notes.embedding`

### Шаг 4 — Confirm / Edit / Regenerate (на каждую таблицу)

UI `/admin/sources/[id]/tables/[tableId]`:

- Поля `title/description/domain/tags/user_notes` редактируются вручную (PATCH → `sem_revisions` пишет историю + `audit_log`).
- Кнопка **Confirm** ставит `confirmation_status='confirmed'`.
- Кнопка **Regenerate** запускает короткий pipeline с промптом `regenerator.md`, учитывающий текущий `user_notes`.

### Шаг 5 — Free-form admin edit

`/admin/sources/[id]/chat` — текстовый чат с админ-агентом.

Промпт `admin_edit_planner.md` парсит ввод и возвращает массив операций:
- `update_table` — обновить поля
- `set_user_notes` — записать заметку
- `add_relation` — связь между колонками
- `add_glossary` — термин в глоссарий
- `add_note` — свободная md-заметка

Каждая операция в транзакции с записью в `sem_revisions` и `audit_log`.

---

## Клиентский пайплайн (ReAct-агент)

`POST /api/tasks` принимает `{session_id, source_id, prompt}`, возвращает `{task_id, agent_run_id}`. Запись в `task_runs.status='running'`, фоновая asyncio-таска. Вместо жёсткого конвейера — **единственный шаг `ReactAgentStep`** (`agents/client_agent/`), который крутит цикл «рассуждение → вызов инструмента → наблюдение» через нативный OpenAI function-calling (`LLMClient.complete_with_tools`). Процесс зашит в системный промпт `prompts/client_agent.md`.

### Инструменты (`agents/client_agent/tools.py`)

| Тул | Что делает |
|---|---|
| `list_tables` | дешёвый обзор таблиц (qname/title/grain/rows) |
| `get_table` | таблица + **все** колонки с семантикой (роли, value_catalog, диапазоны, ключи) |
| `get_columns` | точечный drill-down по колонкам (value_meanings, caveats, PII) |
| `search_knowledge` | **RAG**: kNN по `md_notes` через эмбеддинги |
| `find_relations` | прямые связи из `sem_relations` (+ cardinality / match_ratio) |
| `related_tables` | **граф Neo4j**: многоходовые связи (1–2 перехода) для JOIN через промежуточные таблицы |
| `glossary_lookup` / `list_metrics` | канонические термины и предопределённые метрики |
| `sample_rows` / `distinct_values` | **живой** просмотр данных ClickHouse (через `CHProfiler`) |
| `run_sql` | guard **зашит внутрь** (whitelist/LIMIT/timeout) → exec → колонки + строки |
| `ask_user` | пауза на уточняющий вопрос (через `await_user_input`) |
| `finish` | терминал: итоговый ответ + выбор результата для preview/XLSX |

### Непрерывность сессии

Полный OpenAI-thread (assistant `tool_calls` + tool-наблюдения + ответы) сохраняется в таблице `agent_messages` (миграция `0020`) и **реиграется на каждом ходу** — follow-up продолжает контекст, а не исследует заново. Обрезка по границам ходов (`MAX_THREAD_TURNS`) + бюджет символов (`THREAD_CHAR_BUDGET`) держат контекст под лимитом. `chat_messages` остаётся для отрисовки UI.

Рамки безопасности: лимит итераций / `run_sql`, guard внутри `run_sql` (модель не может обойти), терминальный `finish` контролирует конец и формат. На `finish` — превью первых 50 строк → `task_runs.result_preview`, полный результат → XLSX (openpyxl), эмит `result.final`.

---

## SSE-протокол

Транспорт — `sse-starlette` `EventSourceResponse`. Каждое сообщение:
- `event:` — тип
- `id:` — монотонный счётчик (для `Last-Event-ID` reconnect)
- `data:` — JSON

Heartbeat `: ping` каждые `T2R_SSE_PING_INTERVAL` секунд.

### Типы событий

| Тип | Payload |
|---|---|
| `step.started` | `{run_id, step_id, name, ts}` |
| `step.progress` | `{run_id, step_id, progress 0..1, detail?}` |
| `step.completed` | `{run_id, step_id, duration_ms}` |
| `step.failed` | `{run_id, step_id, error, retry_possible}` |
| `llm.token` | `{run_id, step_id, chunk}` |
| `tool.started` / `tool.completed` | `{tool, args_summary?/result_summary?}` |
| `awaiting_input` | `{question, schema?, respond_url?}` — пайплайн заблокирован |
| `profiling.table.started` / `.completed` | `{database, table, idx?, total?, duration_ms?}` |
| `result.partial` / `result.final` | финальный результат + URL XLSX |
| `error` | `{code, message}` |
| `done` | `{}` — финал, сервер закрывает stream |

### Reconnect

Клиент при F5 пересоздаёт `EventSource` → SSE-сервер видит `Last-Event-ID` header → реплеит из `replay_buffer` (500 событий) все с `id > Last-Event-ID`. **Профайлинг не прерывается** при уходе со страницы.

---

## SQL guard

`infra/security/sql_guard.py`. Принимает sql-строку, валидирует и переписывает.

**Правила:**
1. `sqlglot.parse(sql, read='clickhouse')` — единственный statement.
2. Разрешённые типы: `Select`, `With`, `Subquery`, `Show`, `Describe`, `Pragma`, `Command(SHOW/DESCRIBE/EXPLAIN)`.
3. **Запрещено**: `Insert`, `Update`, `Delete`, `Create`, `Drop`, `Alter`, `TruncateTable`, `Grant`.
4. **Запрещённые функции**: `url`, `urlCluster`, `file`, `s3`, `s3Cluster`, `hdfs`, `mysql`, `postgresql`, `remote`, `remoteSecure`, `executable`.
5. **Whitelist таблиц** = `source_table_selections`. Чужие → `SqlGuardError`. CTE-алиасы из `WITH` исключаются.
6. **Авто-LIMIT** добавляется если top-level `Select` без `Limit` и без агрегатов.
7. **Авто-SETTINGS** дописывает `SETTINGS max_execution_time=N, max_result_rows=M`.

---

## Knowledge graph

Neo4j 5 хранит:

| Узел | Поля |
|---|---|
| `Table` | id, source_id, database, name, title, domain, status |
| `Column` | id, table_id, name, data_type, role, status |
| `Concept` | id, term, definition |
| `Domain` | name |

| Ребро | Свойства |
|---|---|
| `(:Table)-[:HAS_COLUMN]->(:Column)` | — |
| `(:Column)-[:REFERENCES {kind:'fk', confidence}]->(:Column)` | явные FK из DDL |
| `(:Column)-[:REFERENCES_INFERRED {kind, confidence, reasoning, status}]->(:Column)` | от LLM |
| `(:Concept)-[:DESCRIBES]->(:Table or :Column)` | — |
| `(:Table)-[:BELONGS_TO_DOMAIN]->(:Domain)` | — |

### Назначение

Граф существует **только в Neo4j и только для инструментов агента** — фронтовая визуализация графа удалена как бесполезная на данном этапе. Клиентский агент ходит в граф тулом `related_tables` (многоходовые связи для JOIN). Материализация Neo4j из семантического слоя PG — через `POST /api/admin/sources/{id}/graph/resync` (бэкфилл/восстановление).

---

## REST API

Все admin endpoints требуют cookie `t2r_admin`.

### Auth

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/admin/auth/login` | body `{login, password}` → cookie |
| POST | `/api/admin/auth/logout` | clear cookie |
| GET | `/api/admin/auth/me` | `{login}` или 401 |

### Sources

| Метод | Путь | Описание |
|---|---|---|
| GET / POST | `/api/admin/sources` | список / создать |
| GET / DELETE | `/api/admin/sources/{id}` | детали / удалить |
| POST | `/api/admin/sources/{id}/test-connection` | проба + readonly probe |
| POST | `/api/admin/sources/test-credentials` | проверить без создания |

### Selection (whitelist)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/admin/sources/{id}/discover` | список таблиц из CH + флаг selected |
| GET | `/api/admin/sources/{id}/selection` | текущий whitelist |
| PUT | `/api/admin/sources/{id}/selection` | заменить whitelist |

### Profiling

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/admin/profiling/runs` | старт |
| GET | `/api/admin/profiling/runs` | список по source_id |
| GET | `/api/admin/profiling/runs/{run_id}` | детали + статус таблиц |
| GET | `/api/admin/profiling/agent-runs/{id}/events` | SSE |
| POST | `/api/admin/profiling/agent-runs/{id}/respond` | ответ на awaiting_input |
| POST | `/api/admin/profiling/agent-runs/{id}/cancel` | прервать |

### Tables

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/admin/sources/{id}/tables` | список sem_tables |
| GET | `/api/admin/tables/{id}` | таблица + колонки |
| PATCH | `/api/admin/tables/{id}` | обновить (sem_revisions) |
| POST | `/api/admin/tables/{id}/confirm` | confirmation_status='confirmed' |
| POST | `/api/admin/tables/{id}/regenerate` | мини-pipeline |

### Edit / Audit / Graph

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/admin/edit` | free-form команда |
| GET | `/api/admin/edit/agent-runs/{id}/events` | SSE |
| GET | `/api/admin/audit` | аудит-лог с фильтрами |
| GET | `/api/admin/sources/{id}/graph` | `{nodes, edges}` для cytoscape |

### Client (без admin auth)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/sources/public` | минимальный список источников |
| GET / POST | `/api/sessions` | список / создать сессию |
| GET | `/api/sessions/{id}/messages` | история чата |
| POST | `/api/tasks` | запуск задачи |
| GET | `/api/tasks/{id}` | состояние из БД |
| GET | `/api/tasks/agent-runs/{id}/events` | SSE |
| POST | `/api/tasks/agent-runs/{id}/respond` | ответ на awaiting_input |
| POST | `/api/tasks/agent-runs/{id}/cancel` | прервать |
| GET | `/api/tasks/{id}/export.xlsx` | скачать XLSX |

### Health

| Метод | Путь | Описание |
|---|---|---|
| GET | `/healthz` | liveness |
| GET | `/readyz` | проверяет PG + Neo4j |

---

## Маршруты фронта

| Путь | Описание |
|---|---|
| `/` | клиентский чат (главная) |
| `/chat/[sessionId]` | продолжение сессии клиента |
| `/admin/login` | форма логина |
| `/admin` | дашборд источников |
| `/admin/sources/new` | создание источника + test connection |
| `/admin/sources/[id]` | детали: выбор таблиц + запуски + сем-слой |
| `/admin/sources/[id]/runs/[runId]` | live SSE-таймлайн с clarification |
| `/admin/sources/[id]/tables/[tableId]` | редактор + Regenerate |
| `/admin/sources/[id]/chat` | свободная команда админ-агенту |

---

## Тесты

### Backend

```bash
cd backend
uv pip install -p .venv/bin/python -e ".[test]"
TESTCONTAINERS_RYUK_DISABLED=true .venv/bin/python -m pytest -q
```

**Unit** — `tests/unit/`, в т.ч.:
- `test_client_agent.py` — ReAct-цикл (explore → run_sql → finish), plain-text→finish, пауза/resume на `ask_user`, реплей+сохранение сессионного thread, обрезка контекста по ходам/бюджету, guard внутри `run_sql`, граф-тул `related_tables`
- `test_sql_guard.py` — whitelist / auto-LIMIT / forbidden funcs / DDL reject
- `test_json_extractor.py`, `test_events_bus.py` (replay/Last-Event-ID), `test_pipeline.py` (success/failure/await_user_input/cancel)
- `test_pass1_heuristics.py`, `test_pass2_grouping.py`, `test_profiling_enrichment.py` — профилирование
- `test_cipher.py`, `test_passwords.py`, `test_jwt.py`, `test_prompt_loader.py`, `test_xlsx.py`, `test_schema_renderer.py`, `test_auth_service.py`, `test_session_service.py`

**Integration** — `tests/integration/`, Postgres через testcontainers:
- `test_migrations.py`, `test_admin_auth.py`, `test_sources_crud.py`, `test_health.py`, `test_audit_and_tables.py`, `test_demo_sse.py`
- `test_profiling_v2.py`, `test_profiling_tasks.py`, `test_pass2.py`, `test_profiling_uniqueness.py`

### Frontend

```bash
cd frontend
npm test          # vitest
npx tsc --noEmit  # typecheck
```

---

## Деплой и обслуживание

### Логи

```bash
docker compose -f docker/docker-compose.yml logs -f backend
docker compose -f docker/docker-compose.yml logs -f frontend
```

Backend пишет structured JSON с `request_id` (есть и в response header `x-request-id`).

### Перезапуск backend без потери данных

```bash
docker compose -f docker/docker-compose.yml restart backend
```

Все sem_*-данные в Postgres, граф в Neo4j, источники с зашифрованными credentials — сохранятся.

### Полная переинициализация

```bash
docker compose -f docker/docker-compose.yml down -v   # ⚠️ удалит ВСЕ volumes
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml --profile seed run --rm ch-seeder
```

### Применение новой миграции

Создать файл `migrations/00NN_xxx.sql` → `docker compose restart backend`. В lifespan-старте применится автоматически. Можно вручную:

```bash
docker compose -f docker/docker-compose.yml exec backend python -m scripts.migrate up
docker compose -f docker/docker-compose.yml exec backend python -m scripts.migrate status
```

### Бэкап БД

```bash
docker compose -f docker/docker-compose.yml exec postgres pg_dump -U t2r t2r | gzip > backup-$(date +%F).sql.gz
```

### Восстановление

```bash
gunzip -c backup.sql.gz | docker compose -f docker/docker-compose.yml exec -T postgres psql -U t2r t2r
```

### Где лежат XLSX-экспорты

Named volume `docker_exports` смонтирован в `/var/t2r/exports` контейнера backend. Endpoint `GET /api/tasks/{id}/export.xlsx` отдаёт их через `FileResponse`.

---

## Troubleshooting

### `warning msg="The "h4pvfNzIQqqkjl6MSp357" variable is not set"`

Compose интерпретирует `$` внутри bcrypt-хеша в env_file. Хеш дублирован в `docker-compose.yml → backend.environment` с экранированием `$$`. Это работает корректно, warning безобиден. Если изменил пароль в `backend/.env` — продублируй и в compose.

### Backend падает с `SIGILL` (exit 132)

`cryptography 48.0+` имеет проблемы под Linux ARM64 в Docker Desktop. Зафиксированы версии `cryptography>=43,<45` и `bcrypt>=4.2,<5` в `pyproject.toml`. Если меняешь зависимости — проверь `pip show cryptography` внутри контейнера.

### Frontend `Module not found: Can't resolve '…'`

У фронта named volume для `node_modules`. После добавления нового пакета в `package.json`:

```bash
docker compose -f docker/docker-compose.yml exec frontend npm install
docker compose -f docker/docker-compose.yml restart frontend
```

### `dishka.exceptions.NoFactoryError`

В `di/providers/request.py` (или `app.py`) не зарегистрирован провайдер. Добавь `@provide` метод и импорт.

### Профилирование "застряло" после рестарта backend

In-memory `RunRegistry` теряется. Запись в `profiling_runs.status='running'` остаётся. Workaround:

```sql
UPDATE profiling_runs SET status='failed', error='backend restart' WHERE status='running';
```

### Cookie `t2r_admin` слетает / 401 в браузере

Если backend поменял `T2R_JWT_SECRET` — старые JWT невалидны. Сделай logout/login.

### Тесты — testcontainers не может стартовать Postgres

На macOS Docker Desktop ставится `TESTCONTAINERS_RYUK_DISABLED=true` в `tests/integration/conftest.py` чтобы обойти баг с port mapping. Если используешь Colima/podman — может потребоваться `DOCKER_HOST` env.

---

## Ограничения MVP

| Ограничение | Workaround / план |
|---|---|
| Только ClickHouse как источник | Адаптер абстрактный (`CHClient` → `Profiler`), легко добавить новые типы |
| Single admin (логин в env) | Можно поменять на multi-user — таблица users + role-based |
| In-memory RunRegistry — после restart backend run-ы теряются | Добавить recovery на старте + persistent agent_run_id ↔ pg_run_id |
| Replay-буфер 500 событий на run | Достаточно для 23 таблиц × 8 событий; на 1000+ таблицах могут потеряться ранние |
| LLM-провайдер без fallback | Один base_url+key. Можно сделать array провайдеров с retry |
| Без визуализации результата (диаграммы) | Только summary + table; диаграммы — TODO |
| `system.query_log` обязателен для usage-stats | На prod CH иногда отключён — пайплайн пропустит этот шаг |
| URL `/runs/[agent_run_id]` живёт пока бэк жив | Сделать router по `pg_run_id` с fallback на snapshot |
| Embeddings dim захардкожен в миграции | Миграция `0010` под 1024; для другой модели — новый `ALTER COLUMN` |
