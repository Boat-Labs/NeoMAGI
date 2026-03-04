"""Tests for ComponentHealthTracker integration with OpenAICompatModelClient.

Covers:
- Non-streaming calls: success/failure recording
- Streaming calls: defer_health=True defers success, failure always immediate
- Streaming iteration: success on completion, failure on mid-stream error
- Streaming creation failure: failure recorded even with defer_health=True
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import APIConnectionError, APIStatusError

from src.agent.model_client import ContentDelta, OpenAICompatModelClient
from src.infra.errors import LLMError
from src.infra.health import ComponentHealthTracker


def _make_response(content: str = "hello"):
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    resp.choices = [choice]
    return resp


def _make_stream_chunks(tokens: list[str]):
    """Create an async generator yielding chunks with text content."""

    async def _gen():
        for t in tokens:
            chunk = MagicMock()
            chunk.choices = [SimpleNamespace(delta=SimpleNamespace(content=t, tool_calls=None))]
            yield chunk

    return _gen()


def _make_failing_stream(tokens_before_fail: list[str], error: Exception):
    """Create an async generator that yields some tokens then raises."""

    async def _gen():
        for t in tokens_before_fail:
            chunk = MagicMock()
            chunk.choices = [SimpleNamespace(delta=SimpleNamespace(content=t, tool_calls=None))]
            yield chunk
        raise error

    return _gen()


@pytest.fixture()
def tracker():
    return ComponentHealthTracker()


@pytest.fixture()
def client(tracker):
    c = OpenAICompatModelClient(api_key="test-key", max_retries=0, health_tracker=tracker)
    c._client = MagicMock()
    return c


class TestNonStreamingHealthTracking:
    @pytest.mark.asyncio
    async def test_chat_success_records_success(self, client, tracker):
        client._client.chat.completions.create = AsyncMock(return_value=_make_response("hi"))
        await client.chat([{"role": "user", "content": "test"}], "m")
        assert tracker.provider_consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_chat_success_resets_failure_count(self, client, tracker):
        tracker.provider_consecutive_failures = 3
        client._client.chat.completions.create = AsyncMock(return_value=_make_response("hi"))
        await client.chat([{"role": "user", "content": "test"}], "m")
        assert tracker.provider_consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_chat_api_error_records_failure(self, client, tracker):
        resp = MagicMock()
        resp.status_code = 500
        resp.headers = {}
        client._client.chat.completions.create = AsyncMock(
            side_effect=APIStatusError(
                message="Internal Server Error", response=resp, body=None
            )
        )
        with pytest.raises(LLMError):
            await client.chat([{"role": "user", "content": "test"}], "m")
        assert tracker.provider_consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_chat_retryable_exhausted_records_failure(self, client, tracker):
        client._client.chat.completions.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )
        with pytest.raises(LLMError):
            await client.chat([{"role": "user", "content": "test"}], "m")
        assert tracker.provider_consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_chat_completion_success_records(self, client, tracker):
        client._client.chat.completions.create = AsyncMock(return_value=_make_response("hi"))
        await client.chat_completion([{"role": "user", "content": "test"}], "m")
        assert tracker.provider_consecutive_failures == 0


class TestStreamingHealthTracking:
    """Streaming calls use defer_health=True: success deferred to iteration."""

    @pytest.mark.asyncio
    async def test_stream_creation_does_not_record_success(self, client, tracker):
        """Stream creation success should NOT reset failure count (deferred)."""
        tracker.provider_consecutive_failures = 3
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_stream_chunks(["hello"])
        )
        # Consume the stream
        tokens = []
        async for t in client.chat_stream(
            [{"role": "user", "content": "test"}], "m"
        ):
            tokens.append(t)
        # After full iteration, success IS recorded
        assert tracker.provider_consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_stream_iteration_complete_records_success(self, client, tracker):
        tracker.provider_consecutive_failures = 4
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_stream_chunks(["a", "b", "c"])
        )
        tokens = []
        async for t in client.chat_stream(
            [{"role": "user", "content": "test"}], "m"
        ):
            tokens.append(t)
        assert tokens == ["a", "b", "c"]
        assert tracker.provider_consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_stream_midstream_failure_records_failure(self, client, tracker):
        """Mid-stream error should record failure."""
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_failing_stream(["ok"], RuntimeError("stream died"))
        )
        with pytest.raises(RuntimeError, match="stream died"):
            async for _ in client.chat_stream(
                [{"role": "user", "content": "test"}], "m"
            ):
                pass
        assert tracker.provider_consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_stream_creation_failure_records_failure(self, client, tracker):
        """Stream creation failure (retries exhausted) records failure immediately."""
        client._client.chat.completions.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )
        with pytest.raises(LLMError):
            async for _ in client.chat_stream(
                [{"role": "user", "content": "test"}], "m"
            ):
                pass
        assert tracker.provider_consecutive_failures == 1


class TestStreamWithToolsHealthTracking:
    @pytest.mark.asyncio
    async def test_stream_with_tools_complete_records_success(self, client, tracker):
        tracker.provider_consecutive_failures = 2
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_stream_chunks(["hello"])
        )
        events = []
        async for e in client.chat_stream_with_tools(
            [{"role": "user", "content": "test"}], "m", tools=[{"type": "function"}]
        ):
            events.append(e)
        assert any(isinstance(e, ContentDelta) for e in events)
        assert tracker.provider_consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_stream_with_tools_midstream_failure(self, client, tracker):
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_failing_stream(["a"], RuntimeError("boom"))
        )
        with pytest.raises(RuntimeError, match="boom"):
            async for _ in client.chat_stream_with_tools(
                [{"role": "user", "content": "test"}], "m", tools=[{"type": "function"}]
            ):
                pass
        assert tracker.provider_consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_stream_with_tools_creation_failure(self, client, tracker):
        client._client.chat.completions.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )
        with pytest.raises(LLMError):
            async for _ in client.chat_stream_with_tools(
                [{"role": "user", "content": "test"}], "m", tools=[{"type": "function"}]
            ):
                pass
        assert tracker.provider_consecutive_failures == 1


class TestNoTrackerDoesNotCrash:
    """Client without health_tracker should work normally."""

    @pytest.mark.asyncio
    async def test_chat_without_tracker(self):
        c = OpenAICompatModelClient(api_key="test-key", max_retries=0)
        c._client = MagicMock()
        c._client.chat.completions.create = AsyncMock(return_value=_make_response("ok"))
        result = await c.chat([{"role": "user", "content": "test"}], "m")
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_stream_without_tracker(self):
        c = OpenAICompatModelClient(api_key="test-key", max_retries=0)
        c._client = MagicMock()
        c._client.chat.completions.create = AsyncMock(
            return_value=_make_stream_chunks(["a", "b"])
        )
        tokens = []
        async for t in c.chat_stream([{"role": "user", "content": "test"}], "m"):
            tokens.append(t)
        assert tokens == ["a", "b"]
