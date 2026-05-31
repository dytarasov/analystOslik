from __future__ import annotations

import httpx
from openai import AsyncOpenAI


class EmbeddingsClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dim: int,
        request_timeout: float = 30.0,
        max_input_chars: int = 16000,
    ) -> None:
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(request_timeout, connect=min(10.0, request_timeout)),
        )
        self._model = model
        self._dim = dim
        # bge-m3 has an 8192-token context; an over-long input makes the endpoint
        # return no embedding data and the client raise (a wide table's note can be
        # 25k+ chars → ~9k tokens). Cap the embedded text to a char budget that
        # stays safely under the limit for realistic Russian+schema text (~3 chars
        # /token here). The stored note keeps its FULL body — only the vector's
        # input is truncated, so retrieval still finds the table on its lead content.
        self._max_chars = max_input_chars

    @property
    def dim(self) -> int:
        return self._dim

    def _cap(self, text: str) -> str:
        t = text or ""
        return t if len(t) <= self._max_chars else t[: self._max_chars]

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(
            model=self._model, input=self._cap(text)
        )
        return list(resp.data[0].embedding)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.embeddings.create(
            model=self._model, input=[self._cap(t) for t in texts]
        )
        return [list(d.embedding) for d in resp.data]
