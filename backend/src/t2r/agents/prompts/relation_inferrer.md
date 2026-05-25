Ты — эксперт по моделям данных. Тебе дан набор таблиц с их колонками. Нужно предположить какие колонки ссылаются на какие (inferred foreign keys и семантические связи).

Текущая таблица: `{{ database }}.{{ table }}` — {{ table_title }}

Её колонки:
{% for c in columns %}- `{{ c.name }}` : {{ c.type }} ({{ c.semantic_role }}) — {{ c.description }}
{% endfor %}

Другие таблицы источника (контекст):
{% for t in other_tables %}
- `{{ t.database }}.{{ t.name }}` — {{ t.title }}
  колонки: {% for col in t.columns %}{{ col.name }}({{ col.role }}){% if not loop.last %}, {% endif %}{% endfor %}
{% endfor %}

Верни строго JSON-массив. Связь только если уверенность ≥ 0.5. Если не уверен — не возвращай.
```
[
  {
    "from_column": "имя колонки в текущей таблице",
    "to_database": "имя БД целевой таблицы",
    "to_table": "имя целевой таблицы",
    "to_column": "имя колонки в целевой таблице",
    "confidence": 0.0..1.0,
    "reasoning": "1-2 предложения почему"
  }
]
```
