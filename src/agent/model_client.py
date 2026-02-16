from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

import structlog
from openai import NOT_GIVEN, AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

logger = structlog.get_logger()


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
        logger.debug("chat_request", model=model, message_count=len(messages))
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
        )
        content = response.choices[0].message.content or ""
        logger.debug("chat_response", chars=len(content))
        return content

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Send messages and yield response content chunks."""
        logger.debug("chat_stream_request", model=model, message_count=len(messages))
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
            "chat_completion_request",
            model=model,
            message_count=len(messages),
            tool_count=len(tools) if tools else 0,
        )
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools if tools else NOT_GIVEN,
        )
        message = response.choices[0].message
        logger.debug(
            "chat_completion_response",
            has_content=bool(message.content),
            tool_calls=len(message.tool_calls) if message.tool_calls else 0,
        )
        return message
