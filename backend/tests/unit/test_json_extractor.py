import pytest

from t2r.infra.llm.json_extractor import extract_json


def test_pure_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_pure_array():
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_fenced_object():
    raw = "Here is the result:\n```json\n{\"x\": 5}\n```\nthanks"
    assert extract_json(raw) == {"x": 5}


def test_fenced_array_without_lang():
    raw = "before\n```\n[1, 2]\n```\nafter"
    assert extract_json(raw) == [1, 2]


def test_inline_object_with_prefix():
    raw = "ответ: {\"a\": 1, \"b\": [2, 3]} конец"
    assert extract_json(raw) == {"a": 1, "b": [2, 3]}


def test_raises_when_no_json():
    with pytest.raises(ValueError):
        extract_json("totally not json")
