from __future__ import annotations

import pytest

from t2r.infra.security.sql_guard import SqlGuardError, validate_and_rewrite

# Column-level guard fixtures: `secret` is excluded in db.orders.
_WL = {"db.orders", "db.users"}
_ENABLED = {"db.orders": ["id", "user_id", "amount"]}
_DISABLED = {"db.orders": {"secret"}}


def _guard(sql: str):
    return validate_and_rewrite(
        sql,
        whitelist_qnames=_WL,
        enabled_columns=_ENABLED,
        disabled_columns=_DISABLED,
    )


def test_disabled_column_qualified_is_rejected():
    with pytest.raises(SqlGuardError):
        _guard("SELECT o.secret FROM db.orders o")


def test_disabled_column_unqualified_single_table_is_rejected():
    with pytest.raises(SqlGuardError):
        _guard("SELECT secret FROM db.orders")


def test_disabled_column_db_qualified_is_rejected():
    with pytest.raises(SqlGuardError):
        _guard("SELECT orders.secret FROM db.orders")


def test_disabled_column_in_where_is_rejected():
    with pytest.raises(SqlGuardError):
        _guard("SELECT id FROM db.orders WHERE secret = 1")


def test_disabled_column_via_alias_in_join_is_rejected():
    with pytest.raises(SqlGuardError):
        _guard(
            "SELECT o.secret, u.id FROM db.orders o JOIN db.users u ON o.user_id = u.id"
        )


def test_enabled_column_passes():
    res = _guard("SELECT o.amount FROM db.orders o")
    assert "amount" in res.rewritten


def test_bare_star_expands_to_enabled_columns():
    res = _guard("SELECT * FROM db.orders")
    # Expanded to the enabled set; the disabled column never appears.
    assert "id" in res.rewritten and "amount" in res.rewritten
    assert "secret" not in res.rewritten
    assert "*" not in res.rewritten


def test_qualified_star_expands_with_alias():
    res = _guard("SELECT o.* FROM db.orders o")
    assert "o.id" in res.rewritten and "o.amount" in res.rewritten
    assert "secret" not in res.rewritten


def test_star_left_alone_when_no_disabled_columns():
    # db.users has no disabled columns → `*` is preserved.
    res = _guard("SELECT * FROM db.users")
    assert "*" in res.rewritten


def test_multi_table_bare_star_with_disabled_is_rejected():
    with pytest.raises(SqlGuardError):
        _guard(
            "SELECT * FROM db.orders o JOIN db.users u ON o.user_id = u.id"
        )


def test_count_star_is_not_treated_as_projection_star():
    res = _guard("SELECT count(*) FROM db.orders")
    assert res.has_aggregate is True


def test_no_column_maps_means_no_column_enforcement():
    # Without a disabled map the guard does no column work (back-compat path).
    res = validate_and_rewrite("SELECT secret FROM db.orders", whitelist_qnames=_WL)
    assert "secret" in res.rewritten


def test_select_passes_and_gets_limit_and_settings():
    res = validate_and_rewrite(
        "SELECT id, name FROM analytics.orders WHERE status='paid'",
        whitelist_qnames={"analytics.orders"},
        default_limit=500,
        max_execution_time=15,
    )
    assert "LIMIT 500" in res.rewritten
    # Settings are returned as a separate dict (applied via driver, not
    # spliced into SQL — see GuardResult.settings).
    assert "SETTINGS" not in res.rewritten.upper()
    assert res.settings == {
        "max_execution_time": 15,
        "max_result_rows": 500,
        "result_overflow_mode": "break",
    }
    assert res.referenced_tables == ["analytics.orders"]
    assert res.has_aggregate is False


def test_aggregate_caps_with_break_not_throw():
    """A high-cardinality GROUP BY must be truncated, not error out.

    `result_overflow_mode='break'` makes ClickHouse return a capped result when
    the aggregate produces more than max_result_rows groups, instead of the
    default 'throw' that would surface as an execution error on a valid query.
    """
    res = validate_and_rewrite(
        "SELECT teacher_id, count() FROM analytics.orders GROUP BY teacher_id",
        whitelist_qnames={"analytics.orders"},
        default_limit=10000,
    )
    assert res.has_aggregate is True
    assert res.settings is not None
    assert res.settings["result_overflow_mode"] == "break"
    assert res.settings["max_result_rows"] == 10000


def test_select_with_aggregate_does_not_get_limit():
    res = validate_and_rewrite(
        "SELECT count() FROM analytics.orders",
        whitelist_qnames={"analytics.orders"},
    )
    assert res.has_aggregate is True
    assert "LIMIT" not in res.rewritten.upper()
    # Settings still attached for safety.
    assert res.settings and "max_execution_time" in res.settings


def test_existing_limit_is_preserved():
    res = validate_and_rewrite(
        "SELECT id FROM analytics.orders LIMIT 7",
        whitelist_qnames={"analytics.orders"},
        default_limit=999,
    )
    assert "LIMIT 7" in res.rewritten
    assert "LIMIT 999" not in res.rewritten


def test_rejects_insert():
    with pytest.raises(SqlGuardError):
        validate_and_rewrite("INSERT INTO foo VALUES (1)")


def test_rejects_update():
    with pytest.raises(SqlGuardError):
        validate_and_rewrite("UPDATE foo SET a=1")


def test_rejects_delete():
    with pytest.raises(SqlGuardError):
        validate_and_rewrite("DELETE FROM foo WHERE id=1")


def test_rejects_create_drop():
    with pytest.raises(SqlGuardError):
        validate_and_rewrite("CREATE TABLE x (a Int8) ENGINE=Memory")
    with pytest.raises(SqlGuardError):
        validate_and_rewrite("DROP TABLE x")


def test_rejects_two_statements():
    with pytest.raises(SqlGuardError):
        validate_and_rewrite("SELECT 1; SELECT 2")


def test_rejects_forbidden_function_url():
    with pytest.raises(SqlGuardError):
        validate_and_rewrite(
            "SELECT * FROM url('http://evil', 'CSV', 'a UInt8')",
            whitelist_qnames=set(),
        )


def test_rejects_forbidden_function_s3():
    with pytest.raises(SqlGuardError):
        validate_and_rewrite(
            "SELECT * FROM s3('http://evil/a.parquet', 'Parquet', 'a UInt8')",
            whitelist_qnames=set(),
        )


def test_whitelist_blocks_unknown_table():
    with pytest.raises(SqlGuardError):
        validate_and_rewrite(
            "SELECT * FROM secret.passwords",
            whitelist_qnames={"analytics.orders"},
        )


def test_whitelist_allows_system_numbers():
    res = validate_and_rewrite(
        "SELECT number FROM system.numbers LIMIT 3",
        whitelist_qnames={"analytics.orders"},
    )
    assert "system.numbers" in res.rewritten


def test_show_tables_passes():
    res = validate_and_rewrite("SHOW TABLES FROM analytics")
    assert "SHOW" in res.rewritten.upper()


def test_with_cte_passes():
    res = validate_and_rewrite(
        "WITH t AS (SELECT id FROM analytics.orders) SELECT count() FROM t",
        whitelist_qnames={"analytics.orders"},
    )
    assert res.has_aggregate is True
