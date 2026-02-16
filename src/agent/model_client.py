from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class ModelClient(ABC):
    """Abstract base class for LLM model clients."""

    @abstractmethod
    async def chat(self, messages: list[dict[str, str]], model: str) -> str:
        """Send messages and return the complete response content."""
        ...

    @abstractmethod
    def chat_stream(
        self, messages: list[dict[str, str]], model: str
    ) -> AsyncIterator[str]:
        """Send messages and yield response content chunks as they arrive."""
        ...


class OpenAICompatModelClient(ModelClient):
    """Model client using the OpenAI SDK.

    Works with OpenAI, Gemini, and Ollama via OpenAI-compatible endpoints.
    """

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(self, messages: list[dict[str, str]], model: str) -> str:
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
        self, messages: list[dict[str, str]], model: str
    ) -> AsyncIterator[str]:
        """Send messages and yield response content chunks."""
        logger.debug("chat_stream request: model=%s, messages=%d", model, len(messages))
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
