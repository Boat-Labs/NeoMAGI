from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from openai import NOT_GIVEN, AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

logger = logging.getLogger(__name__)


class ModelClient(ABC):
    """Abstract base class for LLM model clients."""

    @abstractmethod
    async def chat(self, messages: list[dict[str, Any]], model: str) -> str:
        """Send messages and return the complete response content."""
        ...

    @abstractmethod
    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Send messages and yield response content chunks as they arrive."""
        ...

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
    ) -> ChatCompletionMessage:
        """Non-streaming call. Returns full message (may contain content or tool_calls)."""
        ...


class OpenAICompatModelClient(ModelClient):
    """Model client using the OpenAI SDK.

    Works with OpenAI, Gemini, and Ollama via OpenAI-compatible endpoints.
    """

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(self, messages: list[dict[str, Any]], model: str) -> str:
        """Send messages and return the complete response content."""
        logger.debug("chat request: model=%s, messages=%d", model, len(messages))
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
        )
        content = response.choices[0].message.content or ""
        logger.debug("chat response: %d chars", len(content))
        return content

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Send messages and yield response content chunks."""
        logger.debug("chat_stream request: model=%s, messages=%d", model, len(messages))
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools if tools else NOT_GIVEN,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
    ) -> ChatCompletionMessage:
        """Non-streaming call returning full message with potential tool_calls."""
        logger.debug(
            "chat_completion request: model=%s, messages=%d, tools=%d",
            model,
            len(messages),
            len(tools) if tools else 0,
        )
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools if tools else NOT_GIVEN,
        )
        message = response.choices[0].message
        logger.debug(
            "chat_completion response: content=%s, tool_calls=%d",
            bool(message.content),
            len(message.tool_calls) if message.tool_calls else 0,
        )
        return message
