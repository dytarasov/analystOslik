from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from t2r.infra.clickhouse.factory import CHClientFactory
from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.db.repos.sql_recipe_repo_pg import SqlRecipeRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader


class ClientAgentDeps:
    """Everything the ReAct client agent needs to answer a question.

    Same infrastructure the old hardcoded pipeline used — we only replaced the
    brain (steps + classifier) with a tool-calling loop.
    """

    def __init__(
        self,
        *,
        ch_factory: CHClientFactory,
        semantic_repo: SemanticRepoPg,
        notes_repo: NotesRepoPg,
        sql_recipe_repo: SqlRecipeRepoPg,
        graph_repo: GraphRepoNeo4j,
        session: AsyncSession,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        prompts: PromptLoader,
        export_dir: str,
        ch_max_execution_time: int,
        ch_default_limit: int,
        run_budget_seconds: int = 150,
        answer_timeout_seconds: int = 300,
    ) -> None:
        self.ch_factory = ch_factory
        self.semantic_repo = semantic_repo
        self.notes_repo = notes_repo
        self.sql_recipe_repo = sql_recipe_repo
        self.graph_repo = graph_repo
        self.session = session
        self.llm = llm
        self.embeddings = embeddings
        self.prompts = prompts
        self.export_dir = export_dir
        self.ch_max_execution_time = ch_max_execution_time
        self.ch_default_limit = ch_default_limit
        # Wall-clock ceiling for the whole ReAct run, and how long a confirm_plan/
        # ask_user prompt may wait for the user before the run self-finishes.
        self.run_budget_seconds = run_budget_seconds
        self.answer_timeout_seconds = answer_timeout_seconds
