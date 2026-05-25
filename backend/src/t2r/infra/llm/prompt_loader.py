from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Template

PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "agents" / "prompts"
)


class PromptLoader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or PROMPTS_DIR
        self._cache: dict[str, str] = {}

    def load(self, prompt_name: str) -> str:
        if prompt_name not in self._cache:
            path = self.root / f"{prompt_name}.md"
            self._cache[prompt_name] = path.read_text(encoding="utf-8")
        return self._cache[prompt_name]

    def render(self, prompt_name: str, /, **vars_: Any) -> str:
        return Template(self.load(prompt_name)).render(**vars_)
