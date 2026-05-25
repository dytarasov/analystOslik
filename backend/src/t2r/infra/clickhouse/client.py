from __future__ import annotations

from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient


class CHClient:
    """Thin wrapper around clickhouse-connect AsyncClient."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    @property
    def raw(self) -> AsyncClient:
        return self._client

    async def ping(self) -> bool:
        try:
            await self._client.query("SELECT 1")
            return True
        except Exception:
            return False

    async def server_version(self) -> str:
        result = await self._client.query("SELECT version()")
        rows = result.result_rows
        return str(rows[0][0]) if rows else ""

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ):
        return await self._client.query(sql, parameters=parameters, settings=settings)

    async def close(self) -> None:
        await self._client.close()


async def make_ch_client(
    host: str,
    port: int,
    username: str,
    password: str,
    database: str,
    secure: bool = False,
    settings: dict[str, Any] | None = None,
) -> CHClient:
    client = await clickhouse_connect.get_async_client(
        host=host,
        port=port,
        username=username,
        password=password,
        database=database,
        secure=secure,
        settings=settings or {},
    )
    return CHClient(client)
