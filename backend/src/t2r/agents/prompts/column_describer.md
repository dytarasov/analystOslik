Ты — эксперт по аналитическим данным. Для таблицы ниже нужно описать назначение каждой колонки и определить её семантическую роль.

Таблица: `{{ database }}.{{ table }}` — {{ table_title }}
Описание таблицы: {{ table_description }}

Колонки:
{% for c in columns %}- `{{ c.name }}` : {{ c.type }}{% if c.is_key %} [ключ таблицы]{% endif %}
  examples: {{ c.examples }}
  stats: distinct={{ c.distinct }} null_ratio={{ c.null_ratio }}
{% if c.catalog %}  возможные значения: {{ c.catalog }}
{% endif %}{% if c.range %}  диапазон: min={{ c.range.min }} max={{ c.range.max }}{% if c.range.avg is defined and c.range.avg is not none %} avg={{ c.range.avg }}{% endif %}
{% endif %}{% endfor %}

{% if user_notes %}Комментарии администратора:
<user_text trust="low">
{{ user_notes }}
</user_text>{% endif %}

Верни строго JSON-массив объектов по одному на каждую колонку в том же порядке:
```
[
  {
    "name": "имя колонки",
    "description": "Что хранит, как используется (1-2 предложения)",
    "semantic_role": "id | fk | measure | dimension | timestamp | flag | free_text"
  }
]
```
