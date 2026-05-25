from __future__ import annotations

import pytest

from t2r.infra.security.sql_guard import SqlGuardError, validate_and_rewrite


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
