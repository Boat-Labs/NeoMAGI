"""Anthropic (Claude) model client for NeoMAGI.

Handles message/tool/streaming format conversion between NeoMAGI's
OpenAI-style internal format and the Anthropic Messages API.
"""

from __future__ import annotations

import asyncio
import json as _json
import random
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, TypeVar

import structlog

from src.agent.model_client import (
    ContentDelta,
    ModelClient,
    ModelMessage,
    StreamEvent,
    ToolCallsComplete,
)
from src.infra.errors import LLMError
from src.infra.health import ComponentHealthTracker

logger = structlog.get_logger()
T = TypeVar("T")

# ---------------------------------------------------------------------------
# Anthropic SDK imports (optional at import time)
# ---------------------------------------------------------------------------

_ANTHROPIC_RETRYABLE: tuple[type[Exception], ...] = ()
try:
    from anthropic import (
        APIConnectionError as AnthropicConnectionError,
    )
    from anthropic import (
        APIStatusError as AnthropicStatusError,
    )
    from anthropic import (
        APITimeoutError as AnthropicTimeoutError,
    )
    from anthropic import (
        AsyncAnthropic,
    )
    from anthropic import (
        RateLimitError as AnthropicRateLimitError,
    )

    _ANTHROPIC_RETRYABLE = (
        AnthropicConnectionError,
        AnthropicTimeoutError,
        AnthropicRateLimitError,
    )
except ImportError:  # pragma: no cover
    AsyncAnthropic = None  # type: ignore[assignment,misc]
    AnthropicStatusError = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Message format conversion helpers (small, testable functions)
# ---------------------------------------------------------------------------


def _extract_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract system messages into a single prompt, return remaining messages."""
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_parts.append(msg.get("content", "") or "")
        else:
            rest.append(msg)
    return ("\n\n".join(system_parts) if system_parts else None), rest


def _extract_tool_call_fields(tc: dict[str, Any]) -> tuple[str, str, str]:
    """Extract (id, name, arguments_json) from either flat or nested tool call dict.

    Supports two formats:
    - Flat: {"id", "name", "arguments"}
    - Nested (OpenAI persisted): {"id", "type": "function", "function": {"name", "arguments"}}
    """
    tc_id = tc.get("id", "")
    fn = tc.get("function")
    if isinstance(fn, dict):
        return tc_id, fn.get("name", ""), fn.get("arguments", "{}")
    return tc_id, tc.get("name", ""), tc.get("arguments", "{}")


def _parse_arguments(raw: str) -> dict[str, Any]:
    """Parse a JSON arguments string into a dict, returning {} on failure."""
    if not isinstance(raw, str):
        return raw if isinstance(raw, dict) else {}
    try:
        return _json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _convert_assistant_msg(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert an assistant message (possibly with tool_calls) to Anthropic format."""
    content_blocks: list[dict[str, Any]] = []
    text_content = msg.get("content")
    if text_content:
        content_blocks.append({"type": "text", "text": text_content})

    for tc in msg.get("tool_calls") or []:
        tc_id, name, args_raw = _extract_tool_call_fields(tc)
        content_blocks.append({
            "type": "tool_use",
            "id": tc_id,
            "name": name,
            "input": _parse_arguments(args_raw),
        })

    return {
        "role": "assistant",
        "content": content_blocks if content_blocks else (text_content or ""),
    }


def _append_tool_result(
    converted: list[dict[str, Any]], msg: dict[str, Any],
) -> None:
    """Append a tool_result block, merging into last user message if possible."""
    block = {
        "type": "tool_result",
        "tool_use_id": msg.get("tool_call_id", ""),
        "content": msg.get("content", ""),
    }
    if (
        converted
        and converted[-1]["role"] == "user"
        and isinstance(converted[-1]["content"], list)
    ):
        converted[-1]["content"].append(block)
    else:
        converted.append({"role": "user", "content": [block]})


def convert_messages_for_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style messages to Anthropic Messages API format."""
    system_prompt, non_system = _extract_system(messages)
    converted: list[dict[str, Any]] = []

    for msg in non_system:
        role = msg.get("role", "")
        if role == "user":
            converted.append({"role": "user", "content": msg.get("content", "")})
        elif role == "assistant":
            converted.append(_convert_assistant_msg(msg))
        elif role == "tool":
            _append_tool_result(converted, msg)

    return system_prompt, converted


def convert_tools_for_anthropic(tools: list[dict] | None) -> list[dict] | None:
    """Convert OpenAI-style tool definitions to Anthropic format."""
    if not tools:
        return None
    return [
        {
            "name": (fn := tool.get("function", tool)).get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {}),
        }
        for tool in tools
    ]


def anthropic_response_to_model_message(response: Any) -> ModelMessage:
    """Convert Anthropic Messages response to provider-neutral ModelMessage."""
    content_parts: list[str] = []
    tool_calls: list[dict[str, str]] = []

    for block in response.content:
        if block.type == "text":
            content_parts.append(block.text)
        elif block.type == "tool_use":
            args = block.input
            if not isinstance(args, str):
                args = _json.dumps(args)
            tool_calls.append({"id": block.id, "name": block.name, "arguments": args})

    return ModelMessage(content="".join(content_parts) or None, tool_calls=tool_calls)


# ---------------------------------------------------------------------------
# Shared kwargs builder (reduces duplication across methods)
# ---------------------------------------------------------------------------


def _build_create_kwargs(
    model: str,
    max_tokens: int,
    converted: list[dict[str, Any]],
    *,
    system_prompt: str | None,
    tools: list[dict] | None,
    temperature: float | None,
    stream: bool = False,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": converted,
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    if temperature is not None:
        kwargs["temperature"] = temperature
    anthropic_tools = convert_tools_for_anthropic(tools)
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
    if stream:
        kwargs["stream"] = True
    return kwargs


# ---------------------------------------------------------------------------
# Stream event processing
# ---------------------------------------------------------------------------


async def _iter_stream_events(
    stream: Any,
) -> AsyncIterator[ContentDelta | ToolCallsComplete]:
    """Iterate Anthropic stream events, yielding ContentDelta and ToolCallsComplete."""
    tool_calls: list[dict[str, Any]] = []
    current_tool: dict[str, Any] | None = None

    async for event in stream:
        if event.type == "content_block_start":
            block = event.content_block
            if block.type == "tool_use":
                current_tool = {"id": block.id, "name": block.name, "arguments": ""}
        elif event.type == "content_block_delta":
            delta = event.delta
            if delta.type == "text_delta":
                yield ContentDelta(text=delta.text)
            elif delta.type == "input_json_delta" and current_tool is not None:
                current_tool["arguments"] += delta.partial_json
        elif event.type == "content_block_stop" and current_tool is not None:
            tool_calls.append(current_tool)
            current_tool = None

    if tool_calls:
        yield ToolCallsComplete(tool_calls=tool_calls)


# ---------------------------------------------------------------------------
# AnthropicModelClient
# ---------------------------------------------------------------------------


class AnthropicModelClient(ModelClient):
    """Model client using the Anthropic SDK for Claude models."""

    def __init__(
        self,
        api_key: str,
        *,
        max_tokens: int = 8192,
        max_retries: int = 3,
        base_delay: float = 1.0,
        health_tracker: ComponentHealthTracker | None = None,
        provider_name: str = "claude",
    ) -> None:
        if AsyncAnthropic is None:
            raise ImportError("anthropic package not installed")
        self._client = AsyncAnthropic(api_key=api_key)
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._health_tracker = health_tracker
        self._provider_name = provider_name

    # -- retry helper --

    async def _retry_call(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        context: str = "",
        defer_health: bool = False,
    ) -> T:
        for attempt in range(self._max_retries + 1):
            try:
                result = await coro_factory()
                if not defer_health and self._health_tracker:
                    self._health_tracker.record_provider_success(self._provider_name)
                return result
            except _ANTHROPIC_RETRYABLE as e:
                self._on_retryable(attempt, e, context)
                await asyncio.sleep(self._base_delay * (2**attempt) + random.uniform(0, 0.5))
            except Exception as e:
                self._on_fatal(e)
        raise LLMError("Retry loop exhausted")  # pragma: no cover

    def _on_retryable(self, attempt: int, error: Exception, context: str) -> None:
        if attempt == self._max_retries:
            if self._health_tracker:
                self._health_tracker.record_provider_failure(self._provider_name)
            raise LLMError(
                f"Anthropic call failed after {self._max_retries + 1} attempts: {error}"
            ) from error
        logger.warning(
            "anthropic_retry", attempt=attempt + 1, max_retries=self._max_retries,
            error=str(error), context=context,
        )

    def _on_fatal(self, error: Exception) -> None:
        if self._health_tracker:
            self._health_tracker.record_provider_failure(self._provider_name)
        if AnthropicStatusError and isinstance(error, AnthropicStatusError):
            raise LLMError(f"Anthropic API error: {error.status_code} {error.message}") from error
        raise

    # -- health helper --

    def _record_stream_ok(self) -> None:
        if self._health_tracker:
            self._health_tracker.record_provider_success(self._provider_name)

    def _record_stream_fail(self) -> None:
        if self._health_tracker:
            self._health_tracker.record_provider_failure(self._provider_name)

    # -- ModelClient interface --

    async def chat(
        self, messages: list[dict[str, Any]], model: str, temperature: float | None = None,
    ) -> str:
        system_prompt, converted = convert_messages_for_anthropic(messages)
        kwargs = _build_create_kwargs(
            model, self._max_tokens, converted,
            system_prompt=system_prompt, tools=None, temperature=temperature,
        )
        response = await self._retry_call(
            lambda: self._client.messages.create(**kwargs), context="chat",
        )
        return "".join(b.text for b in response.content if b.type == "text")

    async def chat_stream(
        self, messages: list[dict[str, Any]], model: str, *,
        tools: list[dict] | None = None, temperature: float | None = None,
    ) -> AsyncIterator[str]:
        system_prompt, converted = convert_messages_for_anthropic(messages)
        kwargs = _build_create_kwargs(
            model, self._max_tokens, converted,
            system_prompt=system_prompt, tools=tools, temperature=temperature, stream=True,
        )
        stream = await self._retry_call(
            lambda: self._client.messages.create(**kwargs),
            context="chat_stream", defer_health=True,
        )
        try:
            async for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield event.delta.text
            self._record_stream_ok()
        except Exception:
            self._record_stream_fail()
            raise

    async def chat_completion(
        self, messages: list[dict[str, Any]], model: str, *,
        tools: list[dict] | None = None, temperature: float | None = None,
    ) -> ModelMessage:
        system_prompt, converted = convert_messages_for_anthropic(messages)
        kwargs = _build_create_kwargs(
            model, self._max_tokens, converted,
            system_prompt=system_prompt, tools=tools, temperature=temperature,
        )
        response = await self._retry_call(
            lambda: self._client.messages.create(**kwargs), context="chat_completion",
        )
        return anthropic_response_to_model_message(response)

    async def chat_stream_with_tools(
        self, messages: list[dict[str, Any]], model: str, *,
        tools: list[dict] | None = None, temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        system_prompt, converted = convert_messages_for_anthropic(messages)
        kwargs = _build_create_kwargs(
            model, self._max_tokens, converted,
            system_prompt=system_prompt, tools=tools, temperature=temperature, stream=True,
        )
        stream = await self._retry_call(
            lambda: self._client.messages.create(**kwargs),
            context="chat_stream_with_tools", defer_health=True,
        )
        try:
            async for event in _iter_stream_events(stream):
                yield event
            self._record_stream_ok()
        except Exception:
            self._record_stream_fail()
            raise
