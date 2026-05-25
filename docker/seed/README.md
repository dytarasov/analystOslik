# text-to-report demo dataset

Отдельный модуль, который наливает в `clickhouse` контейнер из `docker-compose.yml` полноценный аналитический датасет: 23 таблицы e-commerce + SaaS, ~500k строк, фиксированный random seed, прогретый `system.query_log`. На нём можно гонять и admin-pipeline (профилирование), и client-pipeline (text → SQL → отчёт).

## Что внутри

- `schema.sql` — DDL для базы `demo`: 23 таблицы, MergeTree-движки, явные комментарии у ключевых таблиц.
- `seed.py` — генератор данных + 22 аналитических запроса для прогрева `system.query_log`.
- `Dockerfile` — Python 3.12 + `clickhouse-connect`.

## Таблицы и связи

Размерности: `customers`, `employees`, `brands`, `categories` (само-связь `parent_id`), `products`, `suppliers`, `warehouses`, `subscription_plans`, `currency_rates`, `product_suppliers` (m2m), `inventory`.  
Факты: `orders`, `order_items`, `payments`, `shipments`, `refunds`, `reviews`, `web_events`, `marketing_campaigns`, `campaign_clicks`, `subscriptions`, `support_tickets`, `ticket_messages`.

В ClickHouse нет внешних ключей — все связи **inferred** (по именам колонок и общим значениям). Это нужно специально, чтобы было что инферить агенту из ddl/sample/usage_log.

| Источник | Колонка-FK | Цель |
| --- | --- | --- |
| `orders.customer_id` | → | `customers.id` |
| `orders.employee_id` | → | `employees.id` |
| `order_items.order_id` | → | `orders.id` |
| `order_items.product_id` | → | `products.id` |
| `products.category_id` | → | `categories.id` |
| `products.brand_id` | → | `brands.id` |
| `categories.parent_id` | → | `categories.id` (self) |
| `employees.manager_id` | → | `employees.id` (self) |
| `payments.order_id` | → | `orders.id` |
| `shipments.order_id` | → | `orders.id` |
| `shipments.warehouse_id` | → | `warehouses.id` |
| `refunds.order_id` | → | `orders.id` |
| `refunds.processed_by` | → | `employees.id` |
| `reviews.product_id` | → | `products.id` |
| `reviews.customer_id` | → | `customers.id` |
| `web_events.customer_id` | → | `customers.id` |
| `marketing_campaigns.owner_id` | → | `employees.id` |
| `campaign_clicks.campaign_id` | → | `marketing_campaigns.id` |
| `campaign_clicks.customer_id` | → | `customers.id` |
| `subscriptions.customer_id` | → | `customers.id` |
| `subscriptions.plan_id` | → | `subscription_plans.id` |
| `support_tickets.customer_id` | → | `customers.id` |
| `support_tickets.assignee_id` | → | `employees.id` |
| `ticket_messages.ticket_id` | → | `support_tickets.id` |
| `inventory.warehouse_id` | → | `warehouses.id` |
| `inventory.product_id` | → | `products.id` |
| `product_suppliers.product_id` | → | `products.id` |
| `product_suppliers.supplier_id` | → | `suppliers.id` |

## Запуск

Через docker compose profile `seed` (рекомендуется):

```bash
docker compose -f docker/docker-compose.yml --profile seed run --rm ch-seeder
```

Это поднимет (если ещё не запущен) сервис `clickhouse` и зальёт данные. Контейнер `ch-seeder` завершится после загрузки.

Локально (без docker, в твоём venv):

```bash
cd docker/seed
pip install -r requirements.txt
CH_HOST=localhost CH_PORT=8123 CH_USER=demo CH_PASS=demo python seed.py
```

## Параметры

| Переменная | Дефолт | Описание |
| --- | --- | --- |
| `CH_HOST` | `clickhouse` | хост ClickHouse |
| `CH_PORT` | `8123` | HTTP-порт |
| `CH_USER` | `demo` | пользователь |
| `CH_PASS` | `demo` | пароль |
| `CH_DATABASE` | `default` | стартовая БД (схема создаст `demo`) |
| `SEED_PROFILE` | `full` | `full` (~500k строк) или `small` (~12k для smoke) |
| `SEED_RESET` | `1` | `1` — `DROP TABLE`-ит все таблицы перед загрузкой |

Random seed захардкожен (`42`) — повторные запуски дают побайтово одинаковые данные.

## Использование в text-to-report

1. Подними стек: `docker compose -f docker/docker-compose.yml up -d`.
2. Накатывай данные: `docker compose -f docker/docker-compose.yml --profile seed run --rm ch-seeder`.
3. В админке (`http://localhost:3000/admin`) добавь источник:
   - host: `clickhouse`, port `8123`, database `demo`, user `demo`, password `demo`
4. Запусти профилирование. Агент пройдёт по 23 таблицам, сгенерирует описания, выведет inferred-связи, заполнит граф.
5. Зайди на `/` и спрашивай естественным языком: «сколько заказов в марте 2025», «топ-10 продуктов по выручке», «какой канал маркетинга даёт больше всего конверсий».
