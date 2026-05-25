"""Unit tests for the profiling-enrichment refactor (packages П1–П4).

Covers the pure, dependency-free pieces: ClickHouse type classification,
deterministic relation-candidate generation, cardinality/type/singular
helpers, and the enriched schema renderer.
"""
from __future__ import annotations

from t2r.agents.admin_profiling.pipeline import (
    _cardinality,
    _compatible_types,
    _deterministic_relation_candidates,
    _render_md_note,
    _singular,
)
from t2r.agents.tools.schema_renderer import render_schema
from t2r.infra.clickhouse.profiler import is_numeric_type, is_temporal_type


# ── ClickHouse type classification ─────────────────────────────────────────
def test_is_numeric_type_unwraps_modifiers():
    assert is_numeric_type("UInt64")
    assert is_numeric_type("Nullable(Int32)")
    assert is_numeric_type("LowCardinality(Nullable(Float64))")
    assert is_numeric_type("Decimal(10, 2)")
    assert not is_numeric_type("String")
    assert not is_numeric_type("Date")


def test_is_temporal_type():
    assert is_temporal_type("Date")
    assert is_temporal_type("DateTime")
    assert is_temporal_type("Nullable(DateTime64(3))")
    assert not is_temporal_type("UInt32")
    assert not is_temporal_type("String")


# ── helpers ─────────────────────────────────────────────────────────────────
def test_singular():
    assert _singular("teachers") == "teacher"
    assert _singular("schools") == "school"
    assert _singular("companies") == "company"
    assert _singular("event") == "event"


def test_compatible_types():
    assert _compatible_types("UInt64", "Int64")  # integer family is joinable
    assert _compatible_types("Nullable(UInt32)", "UInt32")
    assert _compatible_types("Decimal(10,2)", "Decimal(18,4)")
    assert not _compatible_types("String", "UInt64")
    assert _compatible_types("String", "String")


def test_cardinality():
    assert _cardinality({"distinct": 100, "total": 100}) == "1:1"
    assert _cardinality({"distinct": 5, "total": 1000}) == "N:1"
    assert _cardinality({"distinct": None, "total": 100}) is None
    assert _cardinality({}) is None


# ── deterministic relation candidates ───────────────────────────────────────
def _teachers_dim():
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "database": "dict",
        "name": "teachers",
        "columns": [
            {"name": "teacher_id", "type": "UInt64", "role": "id", "primary": True, "sorting": True},
            {"name": "full_name", "type": "String", "role": "dimension", "primary": False, "sorting": False},
        ],
    }


def test_candidate_points_fk_to_dimension_key():
    columns = [
        {"name": "teacher_id", "type": "UInt64", "position": 1},
        {"name": "ptn_date", "type": "Date", "position": 2},
    ]
    col_keys = {
        "teacher_id": {"primary": False, "sorting": True, "partition": False},
        "ptn_date": {"primary": False, "sorting": False, "partition": True},
    }
    cands = _deterministic_relation_candidates(
        "facttable-0000-0000-0000-000000000000",
        "cdm", "teachers_events_daily", columns, col_keys, [_teachers_dim()],
    )
    # Exactly one candidate: fact.teacher_id → dict.teachers.teacher_id
    matches = [
        c for c in cands
        if c["from_col"] == "teacher_id" and c["to_col"] == "teacher_id"
    ]
    assert len(matches) == 1
    m = matches[0]
    assert m["from_table_id"] == "facttable-0000-0000-0000-000000000000"
    assert m["to_table_id"] == "11111111-1111-1111-1111-111111111111"
    assert m["to_db"] == "dict" and m["to_tbl"] == "teachers"
    # `ptn_date` has no name/type match in the dimension → no candidate.
    assert not any(c["from_col"] == "ptn_date" for c in cands)


def test_no_candidates_without_described():
    cands = _deterministic_relation_candidates(
        "t", "db", "tbl",
        [{"name": "x_id", "type": "UInt64", "position": 1}],
        {"x_id": {}},
        [],
    )
    assert cands == []


# ── enriched schema rendering ────────────────────────────────────────────────
def test_render_schema_surfaces_keys_values_ranges():
    tables = [
        {
            "id": "t1",
            "database": "cdm",
            "table_name": "events",
            "title": "События",
            "description": "Факты активности",
            "total_rows": 1_607_364,
            "partition_key": "toYYYYMM(ptn_date)",
            "sorting_key": "teacher_id, ptn_date",
            "grain": "один учитель за один день",
        }
    ]
    cols = {
        "t1": [
            {
                "name": "status",
                "data_type": "String",
                "semantic_role": "dimension",
                "description": "Статус",
                "value_catalog": [
                    {"value": "active", "count": 100},
                    {"value": "churned", "count": 5},
                ],
            },
            {
                "name": "teacher_id",
                "data_type": "UInt64",
                "semantic_role": "fk",
                "description": "Учитель",
                "is_in_sorting_key": True,
                "value_range": {"min": 1, "max": 999, "avg": 500},
            },
        ]
    }
    out = render_schema(tables, cols)
    assert "PARTITION BY (toYYYYMM(ptn_date))" in out
    assert "Грануляр" in out  # grain line rendered
    assert "значения: active, churned" in out
    assert "диапазон: 1 … 999" in out
    assert "[ORDER]" in out  # teacher_id sorting-key marker


def test_render_md_note_includes_physical_and_catalog():
    table_json = {
        "title": "События",
        "domain": "operations",
        "description": "desc",
        "grain": "один учитель в день",
    }
    col_desc = [{"name": "status", "semantic_role": "dimension", "description": "Статус"}]
    stats = {"status": {"distinct": 2, "null_ratio": 0.0}}
    meta = {"engine": "MergeTree", "total_rows": 1000, "sorting_key": "teacher_id"}
    catalogs = {"status": [{"value": "active", "count": 10}, {"value": "churned", "count": 2}]}
    out = _render_md_note(
        "cdm", "events", table_json, col_desc, stats,
        meta=meta, catalogs=catalogs, col_keys={"status": {}},
    )
    assert "MergeTree" in out
    assert "ORDER BY (teacher_id)" in out
    assert "значения: active, churned" in out
    assert "Грануляр" in out
