"""Unit tests for pass-2 column grouping."""
from __future__ import annotations

from t2r.agents.admin_profiling.pass2 import group_columns


def _names(groups):
    return [[c["name"] for c in g] for g in groups]


def test_groups_by_prefix_and_caps_size():
    cols = [
        {"name": "id"},
        {"name": "lesson_id"},
        {"name": "lesson_date"},
        {"name": "lesson_count"},
        {"name": "lesson_paid"},
        {"name": "school_id"},
    ]
    groups = _names(group_columns(cols, max_size=3))
    assert ["id"] in groups
    assert ["school_id"] in groups
    # lesson_* (4 cols) chunked into 3 + 1
    assert ["lesson_id", "lesson_date", "lesson_count"] in groups
    assert ["lesson_paid"] in groups
    # no group exceeds the cap
    assert all(len(g) <= 3 for g in groups)


def test_every_column_covered_exactly_once():
    cols = [{"name": n} for n in ("a", "b_x", "b_y", "c", "d_1", "d_2", "d_3", "d_4")]
    groups = group_columns(cols, max_size=3)
    flat = [c["name"] for g in groups for c in g]
    assert sorted(flat) == sorted(c["name"] for c in cols)
    assert len(flat) == len(set(flat))  # no duplication


def test_empty():
    assert group_columns([]) == []
