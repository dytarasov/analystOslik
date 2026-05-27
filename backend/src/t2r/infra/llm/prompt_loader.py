from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Template

PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "agents" / "prompts"
)

# Shared identity prepended to every rendered prompt so the model always knows
# who it is, regardless of which agent/step is calling. Deliberately neutral
# about output shape — it must not coax extra prose into JSON-only prompts, so
# it explicitly defers to "the format the concrete task requires".
IDENTITY_PREAMBLE = (
    "Ты — «Аналитический Ослик»: дружелюбный, спокойный и аккуратный "
    "ИИ-ассистент для аналитики данных. Это твоя постоянная роль во всех "
    "задачах ниже. Работай точно и по делу, общайся на русском языке и строго "
    "следуй формату ответа, которого требует конкретная задача."
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
        body = Template(self.load(prompt_name)).render(**vars_)
        # The donkey identity also rides along as ``{{ oslik_identity }}`` for
        # templates that want to place it inline; otherwise it is prepended.
        return f"{IDENTITY_PREAMBLE}\n\n---\n\n{body}"
