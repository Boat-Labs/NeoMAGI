from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
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


@dataclass
class ContentDelta:
    """A chunk of streamed text content."""

    text: str


@dataclass
class ToolCallsComplete:
    """Accumulated tool calls from a completed stream."""

    tool_calls: list[dict[str, str]] = field(default_factory=list)


StreamEvent = ContentDelta | ToolCallsComplete


class ModelClient(ABC):
    """Abstract base class for LLM model clients."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float | None = None,
    ) -> str:
        """Send messages and return the complete response content."""
        ...

    @abstractmethod
    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
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
        temperature: float | None = None,
    ) -> ChatCompletionMessage:
        """Non-streaming call. Returns full message (may contain content or tool_calls)."""
        ...

    @abstractmethod
    def chat_stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream response with tool call support.

        Content tokens yield immediately as ContentDelta.
        Tool call deltas are accumulated; a single ToolCallsComplete is yielded
        after the stream ends if any tool calls were detected.
        """
        ...


def _first_choice(response, *, context: str = ""):
    """Extract first choice from response, raising LLMError if empty."""
    if not response.choices:
        raise LLMError(f"Empty choices from provider ({context})")
    return response.choices[0]


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

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float | None = None,
    ) -> str:
        """Send messages and return the complete response content."""
        logger.debug("chat_request", model=model, message_count=len(messages))
        response = await self._retry_call(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=messages,
                **({"temperature": temperature} if temperature is not None else {}),
            ),
            context="chat",
        )
        content = _first_choice(response, context="chat").message.content or ""
        logger.debug("chat_response", chars=len(content))
        return content

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Send messages and yield response content chunks."""
        logger.debug("chat_stream_request", model=model, message_count=len(messages))
        stream = await self._retry_call(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools else NOT_GIVEN,
                stream=True,
                **({"temperature": temperature} if temperature is not None else {}),
            ),
            context="chat_stream",
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
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
                **({"temperature": temperature} if temperature is not None else {}),
            ),
            context="chat_completion",
        )
        message = _first_choice(response, context="chat_completion").message
        logger.debug(
            "chat_completion_response",
            has_content=bool(message.content),
            tool_calls=len(message.tool_calls) if message.tool_calls else 0,
        )
        return message

    async def chat_stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream LLM response, yielding content deltas and accumulated tool calls.

        Content tokens are yielded immediately as ContentDelta.
        Tool call deltas are accumulated silently; after the stream ends,
        a single ToolCallsComplete is yielded if any tool calls were detected.

        OpenAI streaming tool_calls format:
        - First chunk per tool: {index, id, function: {name, arguments: ""}}
        - Subsequent chunks: {index, function: {arguments: "partial..."}}
        - Arguments are partial JSON strings that must be concatenated.
        """
        logger.debug(
            "chat_stream_with_tools_request",
            model=model,
            message_count=len(messages),
            tool_count=len(tools) if tools else 0,
        )
        stream = await self._retry_call(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools else NOT_GIVEN,
                stream=True,
                **({"temperature": temperature} if temperature is not None else {}),
            ),
            context="chat_stream_with_tools",
        )

        # Accumulate tool_calls delta fragments, keyed by index
        pending_tool_calls: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Content path: yield immediately
            if delta.content:
                yield ContentDelta(text=delta.content)

            # Tool calls path: accumulate delta fragments
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                    entry = pending_tool_calls[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

        # After stream ends: yield accumulated tool calls if any
        if pending_tool_calls:
            yield ToolCallsComplete(
                tool_calls=[pending_tool_calls[i] for i in sorted(pending_tool_calls)]
            )
