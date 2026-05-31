from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import AsyncOpenAI


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    """One assistant message in a tool-calling loop: free text and/or tool calls."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    """Parse tool-call arguments JSON, tolerating sloppy output from weak models."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        from t2r.infra.llm.json_extractor import extract_json

        try:
            value = extract_json(raw)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


class LLMClient:
    """Thin wrapper around AsyncOpenAI for OpenAI-compatible endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        openrouter_provider: str | None = None,
        openrouter_pin: bool = False,
        request_timeout: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        # Explicit timeout + bounded retries: without these the SDK defaults to a
        # 600s timeout and 2 retries, so a stalled upstream could hang a single
        # call for minutes and freeze the agent's "running" spinner. Cap connect
        # separately so an unreachable endpoint fails fast.
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(request_timeout, connect=min(10.0, request_timeout)),
            max_retries=max_retries,
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        # OpenRouter routing. Default: no provider block → OpenRouter picks and
        # falls back across providers, so a single provider throwing 429s/errors
        # can't block us. Pinning is opt-in (openrouter_pin) and even then keeps
        # ``allow_fallbacks: True`` — a *preference*, not a hard pin, so a sick
        # preferred provider still degrades to others instead of failing. Merged
        # into the request body via the OpenAI SDK's ``extra_body``.
        self._extra_body: dict[str, Any] | None = None
        if openrouter_pin and openrouter_provider:
            self._extra_body = {
                "provider": {
                    "order": [openrouter_provider],
                    "allow_fallbacks": True,
                }
            }

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        response_format: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature if temperature is None else temperature,
            max_tokens=self._max_tokens if max_tokens is None else max_tokens,
            response_format=response_format,
            extra_body=self._extra_body,
        )
        choice = resp.choices[0]
        return choice.message.content or ""

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AssistantTurn:
        """One step of a tool-calling loop using native OpenAI function calling.

        Returns the assistant message (free text and/or parsed tool calls). The
        caller is responsible for executing the tools and appending the results
        back as ``role: "tool"`` messages before calling again.
        """
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=self._temperature if temperature is None else temperature,
            max_tokens=self._max_tokens if max_tokens is None else max_tokens,
            extra_body=self._extra_body,
        )
        msg = resp.choices[0].message
        calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=_parse_tool_arguments(tc.function.arguments),
            )
            for tc in (msg.tool_calls or [])
        ]
        return AssistantTurn(content=msg.content, tool_calls=calls, raw=msg)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature if temperature is None else temperature,
            max_tokens=self._max_tokens if max_tokens is None else max_tokens,
            stream=True,
            extra_body=self._extra_body,
        )
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content  # type: ignore[union-attr]
            except (AttributeError, IndexError):
                delta = None
            if delta:
                yield delta
