from __future__ import annotations

import abc

from t2r.agents.orchestrator.context import RunContext
from t2r.agents.orchestrator.run import AgentRun


class Step(abc.ABC):
    """Base class for pipeline steps."""

    name: str = ""
    step_id: str = ""

    def __init__(self, *, step_id: str | None = None, name: str | None = None) -> None:
        self.step_id = step_id or self.__class__.__name__
        self.name = name or self.__class__.__name__

    @abc.abstractmethod
    async def execute(self, run: AgentRun, ctx: RunContext) -> None: ...
