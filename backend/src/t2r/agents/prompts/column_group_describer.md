Ты — эксперт по данным, помогаешь продуктовым аналитикам понять таблицу ClickHouse. Тебе дана небольшая группа связанных колонок одной таблицы — разбери КАЖДУЮ глубоко и по делу.

Таблица: `{{ database }}.{{ table }}` — {{ table_title or '' }}
{% if table_description %}Описание таблицы: {{ table_description }}{% endif %}
{% if grain %}Грануляр­ность: {{ grain }}{% endif %}

Колонки группы (с фактами из профайлинга):
{% for c in columns %}
### `{{ c.name }}` : {{ c.data_type }}
- эвристическая роль: {{ c.semantic_role or '—' }}
- distinct={{ c.distinct_count }}, null_ratio={{ c.null_ratio }}
{% if c.examples %}- примеры: {{ c.examples }}{% endif %}
{% if c.value_catalog %}- значения (по частоте): {{ c.value_catalog }}{% endif %}
{% if c.value_range %}- профиль: {{ c.value_range }}{% endif %}
{% endfor %}

Остальные колонки таблицы (для контекста, кратко): {{ peers }}

{% if answers %}Ответы администратора на ранее заданные вопросы (учитывай ОБЯЗАТЕЛЬНО):
{% for a in answers %}- {{ a.column }}: {{ a.text }} → {{ a.answer }}
{% endfor %}{% endif %}
{% if glossary %}
Глоссарий источника (авторитетные определения, house rules, смыслы значений — приоритет над догадками):
{{ glossary }}

Если глоссарий отвечает на неясность по колонке — используй его трактовку и НЕ задавай вопрос.
{% endif %}
Задача по каждой колонке:
- понятное бизнес-описание (что это, как аналитик это использует);
- уточни роль (id|fk|measure|dimension|timestamp|flag|free_text);
- единица измерения (unit) если применимо, иначе null;
- pii: true если персональные данные;
- value_meanings: смысл каждого значения для enum/статусов/флагов (если применимо), иначе {};
- safe_to_group_by / safe_to_filter_by: можно ли безопасно группировать/фильтровать по колонке;
- caveats: подводные камни (много null, депрекейт, неоднозначность), иначе "";
- suggested_aggregation: sum|avg|count|count_distinct|min|max|none;
- confidence: 0..1 — насколько ты уверен в трактовке.

Если по колонке смысл/значения реально неясны — задай короткий вопрос администратору (kind в choices необязателен).

Верни строго JSON (без markdown):
```
{
  "columns": [
    {
      "name": "имя",
      "description": "...",
      "semantic_role": "dimension",
      "unit": null,
      "pii": false,
      "value_meanings": {"active": "активен", "churned": "отток"},
      "safe_to_group_by": true,
      "safe_to_filter_by": true,
      "caveats": "",
      "suggested_aggregation": "none",
      "confidence": 0.9
    }
  ],
  "questions": [
    {"column": "имя", "text": "Короткий вопрос (до 80 симв.)", "choices": ["вариант1", "вариант2"]}
  ]
}
```
Задавай вопрос ТОЛЬКО при реальной неоднозначности. Для понятных колонок questions опускай.
