from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.profiling_repo_pg import ProfilingRepoPg
from t2r.infra.db.repos.selection_repo_pg import SelectionRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.db.repos.sql_recipe_repo_pg import SqlRecipeRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.infra.security.cipher import FernetCipher
from t2r.services.glossary_service import GlossaryService
from t2r.services.semantic_service import SemanticService
from t2r.services.session_service import SessionService
from t2r.services.source_service import SourceService
from t2r.services.sql_notes_service import SqlNotesService
from t2r.settings import Settings


class RequestProvider(Provider):
    scope = Scope.REQUEST

    @provide
    async def pg_session(
        self, sm: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[AsyncSession]:
        async with sm() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @provide
    def source_repo(self, session: AsyncSession, cipher: FernetCipher) -> SourceRepoPg:
        return SourceRepoPg(session, cipher)

    @provide
    def semantic_repo(self, session: AsyncSession) -> SemanticRepoPg:
        return SemanticRepoPg(session)

    @provide
    def notes_repo(self, session: AsyncSession) -> NotesRepoPg:
        return NotesRepoPg(session)

    @provide
    def sql_recipe_repo(self, session: AsyncSession) -> SqlRecipeRepoPg:
        return SqlRecipeRepoPg(session)

    @provide
    def profiling_repo(self, session: AsyncSession) -> ProfilingRepoPg:
        return ProfilingRepoPg(session)

    @provide
    def graph_repo(self, driver: AsyncDriver) -> GraphRepoNeo4j:
        return GraphRepoNeo4j(driver)

    @provide
    def selection_repo(self, session: AsyncSession) -> SelectionRepoPg:
        return SelectionRepoPg(session)

    @provide
    def source_service(
        self, repo: SourceRepoPg, graph_repo: GraphRepoNeo4j
    ) -> SourceService:
        return SourceService(repo, graph_repo)

    @provide
    def glossary_service(
        self,
        source_repo: SourceRepoPg,
        semantic_repo: SemanticRepoPg,
        notes_repo: NotesRepoPg,
        graph_repo: GraphRepoNeo4j,
        emb: EmbeddingsClient,
        llm: LLMClient,
        prompts: PromptLoader,
        settings: Settings,
    ) -> GlossaryService:
        return GlossaryService(
            source_repo=source_repo,
            semantic_repo=semantic_repo,
            notes_repo=notes_repo,
            graph_repo=graph_repo,
            embeddings=emb,
            llm=llm,
            prompts=prompts,
            ingest_max_tokens=settings.llm_ingest_max_tokens,
        )

    @provide
    def sql_notes_service(
        self,
        source_repo: SourceRepoPg,
        sql_recipe_repo: SqlRecipeRepoPg,
        emb: EmbeddingsClient,
        llm: LLMClient,
        prompts: PromptLoader,
        settings: Settings,
    ) -> SqlNotesService:
        return SqlNotesService(
            source_repo=source_repo,
            sql_recipe_repo=sql_recipe_repo,
            embeddings=emb,
            llm=llm,
            prompts=prompts,
            ingest_max_tokens=settings.llm_ingest_max_tokens,
        )

    @provide
    def semantic_service(
        self,
        repo: SemanticRepoPg,
        graph_repo: GraphRepoNeo4j,
        notes_repo: NotesRepoPg,
        emb: EmbeddingsClient,
    ) -> SemanticService:
        return SemanticService(repo, graph_repo, notes_repo, emb)

    @provide
    def session_service(self, session: AsyncSession) -> SessionService:
        return SessionService(session)
