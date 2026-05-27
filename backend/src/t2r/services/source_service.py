from __future__ import annotations

from uuid import UUID

from t2r.domain.models.source import (
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    TestConnectionResult,
)
from t2r.errors import NotFoundError, UpstreamError
from t2r.infra.clickhouse.client import make_ch_client
from t2r.infra.clickhouse.permissions import probe_readonly
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
from t2r.infra.graph.repo import GraphRepoNeo4j
from t2r.logging import get_logger

logger = get_logger("source_service")


class SourceService:
    def __init__(self, repo: SourceRepoPg, graph: GraphRepoNeo4j) -> None:
        self.repo = repo
        self.graph = graph

    async def list(self) -> list[DataSource]:
        return await self.repo.list()

    async def get(self, source_id: UUID) -> DataSource:
        src = await self.repo.get(source_id)
        if not src:
            raise NotFoundError("Источник не найден")
        return src

    async def create(self, payload: DataSourceCreate) -> DataSource:
        return await self.repo.create(
            name=payload.name,
            kind=payload.kind,
            host=payload.host,
            port=payload.port,
            database=payload.database,
            username=payload.username,
            password=payload.password,
            secure=payload.secure,
            extra_settings=payload.extra_settings,
        )

    async def update(self, source_id: UUID, payload: DataSourceUpdate) -> DataSource:
        if not await self.repo.get(source_id):
            raise NotFoundError("Источник не найден")
        updated = await self.repo.update(
            source_id,
            name=payload.name,
            host=payload.host,
            port=payload.port,
            database=payload.database,
            username=payload.username,
            password=payload.password,
            secure=payload.secure,
            extra_settings=payload.extra_settings,
            glossary_md=payload.glossary_md,
        )
        if not updated:
            raise NotFoundError("Источник не найден")
        return updated

    async def delete(self, source_id: UUID) -> None:
        await self.repo.delete(source_id)
        # PG cascades; Neo4j has no FK, so drop its nodes explicitly (best-effort).
        try:
            await self.graph.delete_source(str(source_id))
        except Exception:  # noqa: BLE001
            logger.exception("graph cleanup on source delete failed", source_id=str(source_id))

    async def test_connection(self, source_id: UUID) -> TestConnectionResult:
        src = await self.get(source_id)
        password = await self.repo.get_password(source_id)
        if password is None:
            raise NotFoundError("Пароль источника не найден")
        try:
            client = await make_ch_client(
                host=src.host,
                port=src.port,
                username=src.username,
                password=password,
                database=src.database,
                secure=src.secure,
            )
        except Exception as exc:  # noqa: BLE001
            await self.repo.update_test_status(
                source_id, ok=False, readonly=False, error=str(exc)
            )
            return TestConnectionResult(ok=False, error=str(exc))
        try:
            if not await client.ping():
                raise UpstreamError("ClickHouse не отвечает")
            version = await client.server_version()
            readonly = await probe_readonly(client)
        finally:
            await client.close()
        await self.repo.update_test_status(source_id, ok=True, readonly=readonly, error=None)
        return TestConnectionResult(ok=True, version=version, readonly=readonly)

    async def test_credentials(self, payload: DataSourceCreate) -> TestConnectionResult:
        try:
            client = await make_ch_client(
                host=payload.host,
                port=payload.port,
                username=payload.username,
                password=payload.password,
                database=payload.database,
                secure=payload.secure,
            )
        except Exception as exc:  # noqa: BLE001
            return TestConnectionResult(ok=False, error=str(exc))
        try:
            if not await client.ping():
                return TestConnectionResult(ok=False, error="ping failed")
            version = await client.server_version()
            readonly = await probe_readonly(client)
        finally:
            await client.close()
        return TestConnectionResult(ok=True, version=version, readonly=readonly)
