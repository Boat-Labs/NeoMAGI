from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, TypeVar

import structlog
from openai import (
    NOT_GIVEN,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessage

from src.infra.errors import LLMError

logger = structlog.get_logger()

T = TypeVar("T")

_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)


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
    Includes exponential backoff retry for transient errors.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._max_retries = max_retries
        self._base_delay = base_delay

    async def _retry_call(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        context: str = "",
    ) -> T:
        """Execute an async call with exponential backoff retry.

        Retries on: APIConnectionError, APITimeoutError, RateLimitError.
        Non-retryable API errors are wrapped in LLMError.
        """
        for attempt in range(self._max_retries + 1):
            try:
                return await coro_factory()
            except _RETRYABLE as e:
                if attempt == self._max_retries:
                    raise LLMError(
                        f"LLM call failed after {self._max_retries + 1} attempts: {e}"
                    ) from e
                delay = self._base_delay * (2**attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "llm_retry",
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                    delay=round(delay, 2),
                    error=str(e),
                    context=context,
                )
                await asyncio.sleep(delay)
            except APIStatusError as e:
                raise LLMError(
                    f"LLM API error: {e.status_code} {e.message}"
                ) from e
        # Unreachable, but satisfies type checker
        raise LLMError("Retry loop exhausted")  # pragma: no cover

    async def chat(self, messages: list[dict[str, Any]], model: str) -> str:
        """Send messages and return the complete response content."""
        logger.debug("chat_request", model=model, message_count=len(messages))
        response = await self._retry_call(
            lambda: self._client.chat.completions.create(model=model, messages=messages),
            context="chat",
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
        stream = await self._retry_call(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools else NOT_GIVEN,
                stream=True,
            ),
            context="chat_stream",
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
        response = await self._retry_call(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools else NOT_GIVEN,
            ),
            context="chat_completion",
        )
        message = response.choices[0].message
        logger.debug(
            "chat_completion_response",
            has_content=bool(message.content),
            tool_calls=len(message.tool_calls) if message.tool_calls else 0,
        )
        return message
