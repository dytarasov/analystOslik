from __future__ import annotations

from uuid import UUID

from t2r.errors import NotFoundError
from t2r.infra.clickhouse.client import CHClient, make_ch_client
from t2r.infra.db.repos.source_repo_pg import SourceRepoPg


class CHClientFactory:
    """Creates a CHClient bound to a source from the repository."""

    def __init__(self, repo: SourceRepoPg) -> None:
        self.repo = repo

    async def for_source(self, source_id: UUID) -> CHClient:
        src = await self.repo.get(source_id)
        if not src:
            raise NotFoundError("Источник не найден")
        password = await self.repo.get_password(source_id)
        if password is None:
            raise NotFoundError("Пароль источника не найден")
        return await make_ch_client(
            host=src.host,
            port=src.port,
            username=src.username,
            password=password,
            database=src.database,
            secure=src.secure,
        )
