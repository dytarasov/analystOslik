Ты — эксперт по аналитическим хранилищам данных. Тебе дана таблица ClickHouse. Это аналитическое хранилище — большинство таблиц это либо фактовые (события/метрики), либо измерения (справочники).

Твоя задача: дать таблице короткое название, человекочитаемое описание, грануляр­ность, домен и теги. Опиши максимально полезно для аналитика — что хранит, зачем нужна, какие сущности описывает, нюансы значений.

База: `{{ database }}`
Таблица: `{{ table }}`

DDL:
```sql
{{ ddl }}
```

{% if meta %}Физические свойства: engine={{ meta.engine or '?' }}{% if meta.total_rows is not none %}, rows≈{{ meta.total_rows }}{% endif %}{% if meta.sorting_key %}, ORDER BY ({{ meta.sorting_key }}){% endif %}{% if meta.partition_key %}, PARTITION BY ({{ meta.partition_key }}){% endif %}{% if meta.primary_key %}, PRIMARY KEY ({{ meta.primary_key }}){% endif %}
{% endif %}
Колонки (имя : тип):
{% for c in columns %}- `{{ c.name }}` : {{ c.type }}{% if c.comment %}  — {{ c.comment }}{% endif %}
{% endfor %}

Статистика колонок (count / distinct / null%):
{% for name, s in stats.items() %}- `{{ name }}` — total={{ s.total }}, distinct={{ s.distinct }}, null_ratio={{ s.null_ratio }}
{% endfor %}

Sample (первые 5 строк):
{{ sample_preview }}

{% if usage.available %}Топ-3 запросов из system.query_log (по частоте):
{% for q in usage.top_queries[:3] %}- ({{ q.count }}×, ~{{ q.avg_ms|default('?') }}ms) `{{ q.query|truncate(200) }}`
{% endfor %}{% endif %}

{% if user_notes %}Комментарии администратора (учитывай ОБЯЗАТЕЛЬНО):
<user_text trust="low">
{{ user_notes }}
</user_text>{% endif %}

Верни строго один JSON-объект (без пояснений и без markdown):
```
{
  "title": "Краткое название таблицы (русский, 2-6 слов)",
  "description": "Подробное описание: что хранит, зачем нужна, какие сущности описывает, особенности (3-6 предложений)",
  "grain": "Грануляр­ность: что описывает одна строка (напр. 'один учитель за один день', 'одна школа'). Опусти если неясно.",
  "domain": "Бизнес-домен одним словом: sales | marketing | users | finance | operations | inventory | catalog | support | other",
  "tags": ["до 5 тегов на русском"]
}
```
