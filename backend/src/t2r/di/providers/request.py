from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.infra.db.repos.notes_repo_pg import NotesRepoPg
from t2r.infra.db.repos.profiling_repo_pg import ProfilingRepoPg
from t2r.infra.db.repos.selection_repo_pg import SelectionRepoPg
from t2r.infra.db.repos.semantic_repo_pg import SemanticRepoPg
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.infra.security.cipher import FernetCipher
from t2r.services.glossary_service import GlossaryService
from t2r.services.semantic_service import SemanticService
from t2r.services.session_service import SessionService
from t2r.services.source_service import SourceService


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
    ) -> GlossaryService:
        return GlossaryService(
            source_repo=source_repo,
            semantic_repo=semantic_repo,
            notes_repo=notes_repo,
            graph_repo=graph_repo,
            embeddings=emb,
            llm=llm,
            prompts=prompts,
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
