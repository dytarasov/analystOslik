from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from t2r.agents.orchestrator.registry import RunRegistry
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
from t2r.services.edit_service import EditService
from t2r.services.profiling_service import ProfilingService
from t2r.services.selection_service import SelectionService
from t2r.services.semantic_service import SemanticService
from t2r.services.session_service import SessionService
from t2r.services.source_service import SourceService
from t2r.services.table_chat_service import TableChatService
from t2r.services.task_service import TaskService
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
    def profiling_repo(self, session: AsyncSession) -> ProfilingRepoPg:
        return ProfilingRepoPg(session)

    @provide
    def graph_repo(self, driver: AsyncDriver) -> GraphRepoNeo4j:
        return GraphRepoNeo4j(driver)

    @provide
    def selection_repo(self, session: AsyncSession) -> SelectionRepoPg:
        return SelectionRepoPg(session)

    @provide
    def selection_service(
        self,
        sm: async_sessionmaker[AsyncSession],
        cipher: FernetCipher,
    ) -> SelectionService:
        return SelectionService(sessionmaker=sm, cipher=cipher)

    @provide
    def source_service(self, repo: SourceRepoPg) -> SourceService:
        return SourceService(repo)

    @provide
    def semantic_service(self, repo: SemanticRepoPg) -> SemanticService:
        return SemanticService(repo)

    @provide
    def profiling_service(
        self,
        sm: async_sessionmaker[AsyncSession],
        cipher: FernetCipher,
        driver: AsyncDriver,
        llm: LLMClient,
        emb: EmbeddingsClient,
        prompts: PromptLoader,
        registry: RunRegistry,
    ) -> ProfilingService:
        return ProfilingService(
            sessionmaker=sm,
            cipher=cipher,
            neo4j_driver=driver,
            llm=llm,
            embeddings=emb,
            prompts=prompts,
            registry=registry,
        )

    @provide
    def edit_service(
        self,
        sm: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        prompts: PromptLoader,
        registry: RunRegistry,
    ) -> EditService:
        return EditService(
            sessionmaker=sm,
            llm=llm,
            prompts=prompts,
            registry=registry,
        )

    @provide
    def table_chat_service(
        self,
        sm: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        prompts: PromptLoader,
        registry: RunRegistry,
    ) -> TableChatService:
        return TableChatService(
            sessionmaker=sm,
            llm=llm,
            prompts=prompts,
            registry=registry,
        )

    @provide
    def session_service(self, session: AsyncSession) -> SessionService:
        return SessionService(session)

    @provide
    def task_service(
        self,
        sm: async_sessionmaker[AsyncSession],
        cipher: FernetCipher,
        driver: AsyncDriver,
        llm: LLMClient,
        emb: EmbeddingsClient,
        prompts: PromptLoader,
        registry: RunRegistry,
        settings: Settings,
    ) -> TaskService:
        return TaskService(
            sessionmaker=sm,
            cipher=cipher,
            neo4j_driver=driver,
            llm=llm,
            embeddings=emb,
            prompts=prompts,
            registry=registry,
            settings=settings,
        )
