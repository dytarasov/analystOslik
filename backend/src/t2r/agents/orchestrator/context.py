from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunContext:
    """Shared mutable context passed between steps."""

    data: dict[str, Any] = field(default_factory=dict)
    awaited_answer: Any | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def update(self, **kw: Any) -> None:
        self.data.update(kw)
