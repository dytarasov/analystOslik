from t2r.agents.tools.schema_renderer import render_schema


def test_renders_tables_with_columns():
    tables = [
        {"id": "t1", "database": "db", "table_name": "orders", "title": "Заказы", "description": "Факты заказов"},
        {"id": "t2", "database": "db", "table_name": "users", "title": "Пользователи", "description": ""},
    ]
    cols = {
        "t1": [{"name": "id", "data_type": "Int64", "semantic_role": "id", "description": "PK"}],
        "t2": [
            {"name": "id", "data_type": "Int64", "semantic_role": "id", "description": "PK"},
            {"name": "email", "data_type": "String", "semantic_role": "dimension", "description": "Email"},
        ],
    }
    out = render_schema(tables, cols)
    assert "db.orders" in out
    assert "db.users" in out
    assert "email" in out


def test_truncates_to_max_tables():
    tables = [
        {"id": f"t{i}", "database": "db", "table_name": f"t{i}", "title": "", "description": ""}
        for i in range(50)
    ]
    out = render_schema(tables, {}, max_tables=5)
    assert "db.t4" in out
    assert "db.t9" not in out
