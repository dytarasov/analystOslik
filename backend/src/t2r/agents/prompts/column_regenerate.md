Ты — эксперт по аналитическим данным. Пересмотри описание одной колонки с учётом контекста таблицы и комментариев администратора.

Таблица: `{{ database }}.{{ table }}` — {{ table_title }}
Описание таблицы: {{ table_description }}

Колонка: `{{ column.name }}` ({{ column.data_type }})
Текущее описание: {{ column.description or '—' }}
Текущая семантическая роль: {{ column.semantic_role or '—' }}
Статистика: distinct={{ column.distinct_count }}, null_ratio={{ column.null_ratio }}, examples={{ column.examples }}

{% if guidance %}Комментарии администратора (главный сигнал — учитывай ОБЯЗАТЕЛЬНО):
<user_text trust="low">
{{ guidance }}
</user_text>{% endif %}

Верни строго один JSON-объект:
```
{
  "description": "Уточнённое описание (1-3 предложения).",
  "semantic_role": "id | fk | measure | dimension | timestamp | flag | free_text"
}
```
