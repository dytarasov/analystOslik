from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from t2r.agents.orchestrator.registry import RunRegistry
from t2r.infra.db.engine import make_engine, make_sessionmaker
from t2r.infra.graph.driver import make_neo4j_driver
from t2r.infra.llm.embeddings import EmbeddingsClient
from t2r.infra.llm.openai_client import LLMClient
from t2r.infra.llm.prompt_loader import PromptLoader
from t2r.infra.security.cipher import FernetCipher
from t2r.infra.security.jwt import JwtCodec
from t2r.services.auth_service import AuthService
from t2r.settings import Settings, get_settings


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def settings(self) -> Settings:
        return get_settings()

    @provide
    async def engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = make_engine(settings.pg_dsn)
        try:
            yield engine
        finally:
            await engine.dispose()

    @provide
    def sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return make_sessionmaker(engine)

    @provide
    async def neo4j_driver(self, settings: Settings) -> AsyncIterator[AsyncDriver]:
        driver = make_neo4j_driver(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            yield driver
        finally:
            await driver.close()

    @provide
    def cipher(self, settings: Settings) -> FernetCipher:
        return FernetCipher(settings.encryption_key)

    @provide
    def jwt_codec(self, settings: Settings) -> JwtCodec:
        return JwtCodec(settings.jwt_secret, settings.jwt_ttl_seconds)

    @provide
    def auth_service(self, settings: Settings, jwt: JwtCodec) -> AuthService:
        return AuthService(settings, jwt)

    @provide
    def llm_client(self, settings: Settings) -> LLMClient:
        return LLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    @provide
    def embeddings_client(self, settings: Settings) -> EmbeddingsClient:
        return EmbeddingsClient(
            base_url=settings.emb_base_url,
            api_key=settings.emb_api_key,
            model=settings.emb_model,
            dim=settings.emb_dim,
        )

    @provide
    def prompt_loader(self) -> PromptLoader:
        return PromptLoader()

    @provide
    def run_registry(self) -> RunRegistry:
        return RunRegistry()
