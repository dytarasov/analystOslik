Ты — эксперт по аналитическим данным. Тебе дано прошлое описание таблицы и feedback от администратора. Нужно переписать описание с учётом feedback.

Таблица: `{{ database }}.{{ table }}`

Прошлые поля:
- title: {{ title }}
- description: {{ description }}
- domain: {{ domain }}
- tags: {{ tags }}

DDL:
```sql
{{ ddl }}
```

Sample (первые 3 строки):
{{ sample_preview }}

<user_text trust="low" name="admin_feedback">
{{ user_notes }}
</user_text>

Верни строго JSON-объект:
```
{
  "title": "...",
  "description": "...",
  "domain": "...",
  "tags": ["..."]
}
```
