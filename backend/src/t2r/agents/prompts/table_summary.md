Ты — эксперт по аналитическим хранилищам. По структурному снимку таблицы ClickHouse дай ей бизнес-описание для продуктовых аналитиков.

Таблица: `{{ database }}.{{ table }}`
{% if meta %}Физика: {% if meta.total_rows is not none %}rows≈{{ meta.total_rows }}{% endif %}{% if meta.sorting_key %}, ORDER BY ({{ meta.sorting_key }}){% endif %}{% if meta.partition_key %}, PARTITION BY ({{ meta.partition_key }}){% endif %}
{% endif %}
Колонки (имя : тип · роль · distinct/null{% raw %}{% endraw %}):
{% for c in columns %}- `{{ c.name }}` : {{ c.data_type }} · {{ c.semantic_role or '—' }} · distinct={{ c.distinct_count }} null={{ c.null_ratio }}{% if c.catalog_sample %} · значения: {{ c.catalog_sample }}{% endif %}
{% endfor %}

Верни строго JSON (без markdown):
```
{
  "title": "Краткое название (рус, 2-6 слов)",
  "description": "Что хранит, зачем, какие сущности, нюансы (3-6 предложений)",
  "grain": "Что описывает одна строка (напр. 'один учитель за день'). Опусти если неясно.",
  "domain": "sales | marketing | users | finance | operations | inventory | catalog | support | other",
  "tags": ["до 5 тегов на русском"]
}
```
