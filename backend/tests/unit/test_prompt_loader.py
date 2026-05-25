from pathlib import Path

from t2r.infra.llm.prompt_loader import PromptLoader


def test_load_and_render(tmp_path: Path):
    (tmp_path / "hello.md").write_text("Hi {{ name }}!", encoding="utf-8")
    loader = PromptLoader(root=tmp_path)
    assert loader.render("hello", name="мир") == "Hi мир!"


def test_real_prompts_present_and_renderable():
    loader = PromptLoader()
    text = loader.render(
        "table_describer",
        database="db",
        table="t",
        ddl="CREATE TABLE t (id Int64)",
        columns=[{"name": "id", "type": "Int64", "comment": ""}],
        stats={"id": {"total": 10, "distinct": 10, "null_ratio": 0}},
        sample_preview="| id |",
        usage={"available": False, "top_queries": []},
        user_notes=None,
    )
    assert "db" in text and "t" in text and "id" in text
