# Деплой «Аналитического Ослика»

Прод-стек: фронт (Next.js, `next start`) + бэкенд (FastAPI/uvicorn) + Postgres/pgvector
+ Neo4j + ClickHouse за **одним доменом** через nginx reverse-proxy. Cookie остаются
first-party (Secure + SameSite=Lax). Клиентская часть закрыта общим UUID-ключом,
админка — логином/паролем.

## 1. Подготовка секретов

В `backend/.env` (не коммитится):

```dotenv
T2R_ENV=prod
T2R_CORS_ORIGINS=https://oslik.example.com        # реальный origin фронта
T2R_ACCESS_KEY=<сгенерируйте: python -c "import uuid; print(uuid.uuid4())">
T2R_JWT_SECRET=<случайные 32+ байта>
T2R_ENCRYPTION_KEY=<Fernet: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
T2R_ADMIN_LOGIN=...
# хеш можно держать тут (env_file не экранируется внутри .env), но в compose он
# задаётся через environment с $$ — см. docker/docker-compose.prod.yml
T2R_LLM_API_KEY=...
T2R_EMB_API_KEY=...
```

Сгенерировать хеш пароля админа: `cd backend && python -m scripts.create_admin_hash <пароль>`.
Хеш **не хранится в compose** (публичный репозиторий) — экспортируйте его в shell
**перед** `compose up` (в shell-значении `$` НЕ удваивают):
`export T2R_ADMIN_PASSWORD_HASH='$2b$12$...'`. Без него `compose up` упадёт с понятной
ошибкой (fail-closed).

При желании переопределите пароли БД через переменные окружения compose:
`POSTGRES_PASSWORD`, `NEO4J_PASSWORD`, `CLICKHOUSE_PASSWORD`.

> **Символ `$` в значениях `backend/.env`.** Docker Compose интерполирует `$` в
> значениях `env_file` → секрет с `$` молча испортится. Bcrypt-хеш уже вынесен в
> `environment` с `$$`. Для остальных секретов (`T2R_JWT_SECRET`,
> `T2R_ENCRYPTION_KEY`) генерируйте значения без `$` (base64/hex — безопасны).

## 2. UUID-ключ доступа

- Бэкенд: `T2R_ACCESS_KEY=<uuid>` включает гейт. Пусто → гейт выключен (dev).
- Фронт: образ собирается с `NEXT_PUBLIC_REQUIRE_ACCESS_KEY=1` (уже в prod-compose),
  тогда показывается экран `/unlock`.
- Пользователю выдаёте этот UUID. Он вводит его на `/unlock`, получает cookie
  `t2r_access` (подписанный токен с отпечатком ключа). Смена `T2R_ACCESS_KEY`
  мгновенно инвалидирует все ранее выданные cookie.

## 3. Запуск

```bash
docker compose -f docker/docker-compose.prod.yml up -d --build
```

> **Сборка фронта требует доступа к Google Fonts.** `next build` тянет шрифты
> Onest и JetBrains Mono с `fonts.googleapis.com`/`fonts.gstatic.com` на этапе
> сборки образа. Build-хост должен иметь к ним доступ, иначе сборка упадёт
> (`request to fonts.googleapis.com ... failed`). Для air-gapped/CI без выхода в
> Google — перевести `frontend/styles/fonts.ts` на `next/font/local` (положить
> `.woff2` в репозиторий). Бэкенд-образ от внешней сети при сборке не зависит.

Поднимутся БД, бэкенд (миграции применяются на старте), фронт и nginx на `:80`.
Проверка: `curl -fsS http://<host>/readyz` → `{"ready":true,...}`.

> Отдельное имя проекта `t2r_prod` → собственные volume'ы, не пересекаются с dev
> `docker-compose.yml`. ClickHouse стартует пустым — подключите боевой источник в
> админке или перезалейте демо-данные (профиль `seed`).

## 4. TLS (Let's Encrypt, Cloudflare DNS-only)

`nginx.conf` уже слушает `:443` и читает сертификат из
`/etc/letsencrypt/live/<домен>/`. Выпустите его на ХОСТЕ **до** `compose up`
(порт 80 в этот момент свободен — standalone-челлендж):

```bash
sudo apt-get install -y certbot
sudo certbot certonly --standalone -d analyticaloslik.com -d www.analyticaloslik.com \
  --non-interactive --agree-tos -m you@example.com
```

Сертификаты лягут в `/etc/letsencrypt/` (этот путь монтируется в nginx-контейнер
read-only). После этого `compose up` поднимет nginx с рабочим HTTPS. Cloudflare —
**DNS-only (серое облако)**: клиенты ходят прямо на origin:443, поэтому домен в
сертификате должен совпадать с реальным (A-запись → IP сервера).

Продление: `sudo certbot renew` (standalone требует свободный :80 — на время
продления остановите nginx: `docker compose -f docker/docker-compose.prod.yml stop nginx`).
Для хакатона (сертификат на 90 дней) продление не понадобится.

## 5. Чек-лист безопасности

- [x] `T2R_ENV=prod` → Secure-cookie, JSON-логи, HSTS, security-заголовки.
- [x] Клиентская часть под UUID-ключом, админка — логин/пароль (rate-limit 5/мин).
- [x] Ввод ключа доступа rate-limit (10/мин), перебор логируется.
- [x] SQL-guard (только SELECT/WITH, whitelist таблиц, запрет IO-функций/DDL).
- [x] Креды источников шифруются (Fernet) в Postgres.
- [ ] Порты БД наружу НЕ проброшены (в prod-compose только nginx `:80/:443`).
- [ ] Смените дефолтные пароли БД и хеш админа.
- [ ] Поставьте TLS и ограничьте `T2R_CORS_ORIGINS` своим доменом.
