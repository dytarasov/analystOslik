"""Unit tests for the pure pass-1 heuristics (role classification, value-shape)."""
from __future__ import annotations

from t2r.agents.admin_profiling.pass1 import detect_pattern, heuristic_role


def test_role_timestamp():
    assert heuristic_role("ptn_date", "Date", {}, {}, False) == "timestamp"
    assert heuristic_role("ts", "Nullable(DateTime)", {}, {}, False) == "timestamp"


def test_role_id_vs_fk():
    # unique key on its own table → id
    assert heuristic_role(
        "teacher_id", "UInt64", {"distinct": 100, "total": 100},
        {"primary": True, "sorting": True}, False,
    ) == "id"
    # repeating *_id, not a unique key → fk
    assert heuristic_role(
        "school_id", "UInt64", {"distinct": 50, "total": 1000}, {}, False
    ) == "fk"


def test_role_flag():
    assert heuristic_role(
        "is_active", "UInt8", {"distinct": 2, "total": 10000}, {}, False
    ) == "flag"
    assert heuristic_role("active", "Bool", {"distinct": 2, "total": 5}, {}, False) == "flag"


def test_role_dimension_and_measure():
    assert heuristic_role(
        "status", "String", {"distinct": 4, "total": 10000}, {}, True
    ) == "dimension"
    assert heuristic_role(
        "amount", "Float64", {"distinct": 9000, "total": 10000}, {}, False
    ) == "measure"


def test_role_free_text():
    assert heuristic_role(
        "comment", "String", {"distinct": 9000, "total": 10000}, {}, False
    ) == "free_text"


def test_detect_pattern():
    assert detect_pattern(["a@b.com", "c@d.io"]) == "email"
    assert detect_pattern(["11111111-1111-1111-1111-111111111111"]) == "uuid"
    assert detect_pattern(["http://x", "https://y"]) == "url"
    assert detect_pattern(['{"a":1}', "[1,2]"]) == "json"
    assert detect_pattern(["123", "456"]) == "numeric_string"
    assert detect_pattern(["hello", "world"]) is None
    assert detect_pattern([]) is None
    assert detect_pattern(None) is None
