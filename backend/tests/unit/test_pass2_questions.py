"""Unit tests for the pass-2 question guard (_sanitize_questions).

The guard is the deterministic backstop behind the prompt discipline: it drops
clarifying questions that are either about a non-enabled column (the reported
"asks about excluded fields") or about a fact the profiler already supplies
(type/format/examples), which the describer must infer rather than ask.
"""
from __future__ import annotations

from t2r.agents.admin_profiling.pass2 import _sanitize_questions


def test_keeps_genuine_business_question_on_enabled_column():
    out = _sanitize_questions(
        [{"column": "status", "text": "Что значат коды 0 и 1 в этой колонке?"}],
        {"status", "amount"},
    )
    assert len(out) == 1
    assert out[0]["column"] == "status"


def test_drops_question_about_disabled_or_unknown_column():
    out = _sanitize_questions(
        [
            {"column": "ghost", "text": "Что это значит по бизнесу?"},
            {"column": None, "text": "Общий вопрос без колонки"},
        ],
        {"status", "amount"},
    )
    assert out == []


def test_drops_derivable_fact_questions():
    qs = [
        {"column": "payments", "text": "В каком формате хранятся данные в строке?"},
        {"column": "blob", "text": "Каков реальный тип колонки: Array или String?"},
        {"column": "ids", "text": "Списки ID через запятую или JSON?"},
        {"column": "raw", "text": "Какова внутренняя структура этой строки?"},
    ]
    # every column is "enabled" here — they must still be dropped as derivable
    enabled = {q["column"] for q in qs}
    assert _sanitize_questions(qs, enabled) == []


def test_handles_non_list_and_non_dict_input():
    assert _sanitize_questions(None, {"a"}) == []
    assert _sanitize_questions("nope", {"a"}) == []
    assert _sanitize_questions([42, "x", None], {"a"}) == []


def test_kakogo_tipa_only_drops_data_type_questions():
    # "какого типа" is ordinary Russian ("what kind of") — a genuine business
    # question must survive; only the data/storage-type variant is derivable.
    kept = _sanitize_questions(
        [{"column": "payment", "text": "Какого типа этот платёж — рекуррентный или разовый?"}],
        {"payment"},
    )
    assert len(kept) == 1
    dropped = _sanitize_questions(
        [{"column": "blob", "text": "Какого типа данные хранятся в колонке?"}],
        {"blob"},
    )
    assert dropped == []
