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
from src.infra.health import ComponentHealthTracker

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
        health_tracker: ComponentHealthTracker | None = None,
        provider_name: str = "",
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._health_tracker = health_tracker
        self._provider_name = provider_name

    async def _retry_call(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        context: str = "",
        defer_health: bool = False,
    ) -> T:
        """Execute an async call with exponential backoff retry.

        Retries on: APIConnectionError, APITimeoutError, RateLimitError.
        Non-retryable API errors are wrapped in LLMError.

        When defer_health=True (streaming calls), success recording is deferred
        to the caller (stream iteration completion). Failure is always recorded
        immediately since no iteration phase will follow a creation failure.
        """
        for attempt in range(self._max_retries + 1):
            try:
                result = await coro_factory()
                if not defer_health and self._health_tracker:
                    self._health_tracker.record_provider_success(self._provider_name)
                return result
            except _RETRYABLE as e:
                self._handle_retryable(attempt, e, context)
                await asyncio.sleep(
                    self._base_delay * (2**attempt) + random.uniform(0, 0.5)
                )
            except APIStatusError as e:
                self._record_failure()
                raise LLMError(f"LLM API error: {e.status_code} {e.message}") from e
        # Unreachable, but satisfies type checker
        raise LLMError("Retry loop exhausted")  # pragma: no cover

    def _handle_retryable(self, attempt: int, error: Exception, context: str) -> None:
        """Handle a retryable error: raise on last attempt, log otherwise."""
        if attempt == self._max_retries:
            self._record_failure()
            raise LLMError(
                f"LLM call failed after {self._max_retries + 1} attempts: {error}"
            ) from error
        delay = self._base_delay * (2**attempt) + random.uniform(0, 0.5)
        logger.warning(
            "llm_retry",
            attempt=attempt + 1,
            max_retries=self._max_retries,
            delay=round(delay, 2),
            error=str(error),
            context=context,
        )

    def _record_failure(self) -> None:
        if self._health_tracker:
            self._health_tracker.record_provider_failure(self._provider_name)

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
            defer_health=True,
        )
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
            if self._health_tracker:
                self._health_tracker.record_provider_success(self._provider_name)
        except Exception:
            if self._health_tracker:
                self._health_tracker.record_provider_failure(self._provider_name)
            raise

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
        """Stream LLM response, yielding content deltas and accumulated tool calls."""
        stream = await self._open_stream(messages, model, tools=tools, temperature=temperature)
        accumulator = _ToolCallAccumulator()
        try:
            async for delta in _iter_deltas(stream):
                if delta.content:
                    yield ContentDelta(text=delta.content)
                accumulator.ingest_delta(delta)
            if self._health_tracker:
                self._health_tracker.record_provider_success(self._provider_name)
        except Exception:
            self._record_failure()
            raise
        if accumulator.has_calls():
            yield ToolCallsComplete(tool_calls=accumulator.collect())

    async def _open_stream(self, messages, model, *, tools=None, temperature=None):
        """Create a streaming completion with retry and health deferral."""
        logger.debug(
            "chat_stream_with_tools_request", model=model,
            message_count=len(messages), tool_count=len(tools) if tools else 0,
        )
        return await self._retry_call(
            lambda: self._client.chat.completions.create(
                model=model, messages=messages,
                tools=tools if tools else NOT_GIVEN, stream=True,
                **({"temperature": temperature} if temperature is not None else {}),
            ),
            context="chat_stream_with_tools", defer_health=True,
        )


async def _iter_deltas(stream):
    """Yield choice deltas from a streaming response, skipping empty chunks."""
    async for chunk in stream:
        if chunk.choices:
            yield chunk.choices[0].delta


class _ToolCallAccumulator:
    """Accumulate streaming tool-call deltas into complete calls.

    Prefer index (OpenAI format), fallback to id (Gemini may emit index=None).
    """

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, str]] = {}
        self._order: list[str] = []
        self._fallback_seq = 0
        self._last_key: str | None = None

    def ingest_delta(self, delta: Any) -> None:
        """Ingest all tool_calls from a choice delta (no-op if none)."""
        if not delta.tool_calls:
            return
        for tc in delta.tool_calls:
            self._ingest_one(tc)

    def _ingest_one(self, tc_delta: Any) -> None:
        key = self._resolve_key(tc_delta)
        if key not in self._calls:
            self._calls[key] = {"id": "", "name": "", "arguments": ""}
            self._order.append(key)
        entry = self._calls[key]
        self._last_key = key

        if tc_delta.id:
            entry["id"] = tc_delta.id
        if tc_delta.function:
            if tc_delta.function.name:
                entry["name"] = tc_delta.function.name
            if tc_delta.function.arguments:
                entry["arguments"] += tc_delta.function.arguments

    def has_calls(self) -> bool:
        return bool(self._calls)

    def collect(self) -> list[dict[str, str]]:
        return [self._calls[k] for k in self._order]

    def _resolve_key(self, tc_delta: Any) -> str:
        if tc_delta.index is not None:
            return f"idx:{tc_delta.index}"
        if tc_delta.id:
            return f"id:{tc_delta.id}"
        if tc_delta.function and tc_delta.function.name:
            key = f"fallback:{self._fallback_seq}"
            self._fallback_seq += 1
            return key
        if self._last_key is not None:
            return self._last_key
        key = f"fallback:{self._fallback_seq}"
        self._fallback_seq += 1
        return key
