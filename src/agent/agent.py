from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from src.agent.model_client import ModelClient
from src.agent.prompt_builder import PromptBuilder
from src.session.manager import SessionManager

logger = logging.getLogger(__name__)


class AgentLoop:
    """Core agent loop: receive message -> build prompt -> call LLM -> stream response."""

    def __init__(
        self,
        model_client: ModelClient,
        session_manager: SessionManager,
        workspace_dir: Path,
        model: str = "gpt-4o-mini",
    ) -> None:
        self._model_client = model_client
        self._session_manager = session_manager
        self._prompt_builder = PromptBuilder(workspace_dir)
        self._model = model

    async def handle_message(self, session_id: str, content: str) -> AsyncIterator[str]:
        """Handle an incoming user message and yield response chunks.

        Flow:
        1. Append user message to session
        2. Build system prompt
        3. Get conversation history
        4. Stream LLM response
        5. Collect and append full assistant response to session
        """
        # 1. Append user message
        self._session_manager.append_message(session_id, "user", content)

        # 2. Build system prompt
        system_prompt = self._prompt_builder.build(session_id)

        # 3. Get history (includes the just-appended user message)
        history = self._session_manager.get_history(session_id)

        # 4. Compose messages: system + history
        messages = [{"role": "system", "content": system_prompt}, *history]

        # 5. Stream response and collect full content
        full_response: list[str] = []
        async for chunk in self._model_client.chat_stream(messages, self._model):
            full_response.append(chunk)
            yield chunk

        # 6. Append complete assistant response to session
        assistant_content = "".join(full_response)
        self._session_manager.append_message(session_id, "assistant", assistant_content)
        logger.info(
            "Completed response for session %s: %d chars",
            session_id,
            len(assistant_content),
        )
