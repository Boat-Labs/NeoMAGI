from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog

from src.agent.events import AgentEvent, TextChunk, ToolCallInfo
from src.agent.model_client import ModelClient
from src.agent.prompt_builder import PromptBuilder
from src.session.manager import SessionManager
from src.tools.registry import ToolRegistry

logger = structlog.get_logger()

MAX_TOOL_ITERATIONS = 10


def _safe_parse_args(raw: str | None) -> tuple[dict, str | None]:
    """Parse JSON tool call arguments. Returns (dict, error_message | None).

    Enforces dict type to match protocol.ToolCallData.arguments.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return {}, f"JSON parse error: {e}"
    if not isinstance(parsed, dict):
        return {}, f"Expected dict, got {type(parsed).__name__}"
    return parsed, None


class AgentLoop:
    """Core agent loop with tool calling support.

    Flow: user msg → build prompt → LLM → (tool_calls → execute → LLM)* → text response
    """

    def __init__(
        self,
        model_client: ModelClient,
        session_manager: SessionManager,
        workspace_dir: Path,
        model: str = "gpt-4o-mini",
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._model_client = model_client
        self._session_manager = session_manager
        self._prompt_builder = PromptBuilder(workspace_dir, tool_registry=tool_registry)
        self._tool_registry = tool_registry
        self._model = model

    async def handle_message(
        self, session_id: str, content: str
    ) -> AsyncIterator[AgentEvent]:
        """Handle an incoming user message and yield agent events.

        Yields TextChunk for text content and ToolCallInfo for tool calls.
        Implements a tool call loop: LLM may call tools multiple times before
        producing a final text response.
        """
        # 1. Append user message
        await self._session_manager.append_message(session_id, "user", content)

        # 2. Build system prompt
        system_prompt = self._prompt_builder.build(session_id)

        # 3. Get tools schema
        tools_schema = None
        if self._tool_registry and self._tool_registry.list_tools():
            tools_schema = self._tool_registry.get_tools_schema()

        # 4. Tool call loop
        for iteration in range(MAX_TOOL_ITERATIONS):
            history = self._session_manager.get_history(session_id)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                *history,
            ]

            # Use non-streaming to detect tool calls vs text
            response = await self._model_client.chat_completion(
                messages, self._model, tools=tools_schema
            )

            # If model wants to call tools
            if response.tool_calls:
                # Store assistant message with tool_calls
                tool_calls_data = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in response.tool_calls
                ]
                await self._session_manager.append_message(
                    session_id,
                    "assistant",
                    response.content or "",
                    tool_calls=tool_calls_data,
                )

                # Execute each tool call
                for tc in response.tool_calls:
                    parsed_args, parse_err = _safe_parse_args(tc.function.arguments)
                    if parse_err:
                        logger.warning(
                            "tool_call_args_parse_failed",
                            tool_name=tc.function.name,
                            error=parse_err,
                            raw_args=str(tc.function.arguments)[:200],
                        )
                    yield ToolCallInfo(
                        tool_name=tc.function.name,
                        arguments=parsed_args,
                        call_id=tc.id,
                    )

                    result = await self._execute_tool(
                        tc.function.name, tc.function.arguments
                    )

                    # Store tool result
                    await self._session_manager.append_message(
                        session_id,
                        "tool",
                        json.dumps(result),
                        tool_call_id=tc.id,
                    )

                logger.info(
                    "tool_call_iteration",
                    iteration=iteration + 1,
                    tools_called=len(response.tool_calls),
                    session_id=session_id,
                )
                continue

            # No tool calls — this is the final text response
            text = response.content or ""
            if text:
                yield TextChunk(content=text)
            await self._session_manager.append_message(session_id, "assistant", text)
            logger.info("response_complete", session_id=session_id, chars=len(text))
            return

        # Safety: if we hit max iterations, yield what we have
        logger.warning("max_tool_iterations", max=MAX_TOOL_ITERATIONS, session_id=session_id)
        yield TextChunk(
            content="I've reached the maximum number of tool calls. Please try again."
        )

    async def _execute_tool(self, tool_name: str, arguments_json: str) -> dict:
        """Execute a tool by name. Returns result dict or error dict."""
        if not self._tool_registry:
            return {"error_code": "NO_REGISTRY", "message": "Tool registry not available"}

        tool = self._tool_registry.get(tool_name)
        if not tool:
            logger.warning("unknown_tool", tool_name=tool_name)
            return {"error_code": "UNKNOWN_TOOL", "message": f"Unknown tool: {tool_name}"}

        try:
            arguments = json.loads(arguments_json)
        except (json.JSONDecodeError, TypeError) as e:
            return {"error_code": "INVALID_ARGS", "message": f"Invalid JSON arguments: {e}"}
        if not isinstance(arguments, dict):
            return {
                "error_code": "INVALID_ARGS",
                "message": f"Expected dict arguments, got {type(arguments).__name__}",
            }

        try:
            result = await tool.execute(arguments)
            logger.info("tool_executed", tool_name=tool_name)
            return result
        except Exception:
            logger.exception("tool_execution_failed", tool_name=tool_name)
            return {"error_code": "EXECUTION_ERROR", "message": f"Tool {tool_name} failed"}
