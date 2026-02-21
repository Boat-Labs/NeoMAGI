from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog

from src.agent.events import AgentEvent, TextChunk, ToolCallInfo, ToolDenied
from src.agent.model_client import ContentDelta, ModelClient, ToolCallsComplete
from src.agent.prompt_builder import PromptBuilder
from src.agent.token_budget import BudgetTracker
from src.config.settings import CompactionSettings
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
        compaction_settings: CompactionSettings | None = None,
    ) -> None:
        self._model_client = model_client
        self._session_manager = session_manager
        self._prompt_builder = PromptBuilder(workspace_dir, tool_registry=tool_registry)
        self._tool_registry = tool_registry
        self._model = model
        self._budget_tracker: BudgetTracker | None = None
        if compaction_settings is not None:
            self._budget_tracker = BudgetTracker(compaction_settings, model)

    async def handle_message(
        self, session_id: str, content: str, *, lock_token: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Handle an incoming user message and yield agent events.

        Yields TextChunk for text content and ToolCallInfo for tool calls.
        Implements a tool call loop: LLM may call tools multiple times before
        producing a final text response.

        When lock_token is provided, all append_message calls include atomic
        fencing to reject stale writes after lock takeover.
        """
        # 1. Append user message
        await self._session_manager.append_message(
            session_id, "user", content, lock_token=lock_token
        )

        # Resolve effective mode (fail-closed to chat_safe on error)
        mode = await self._session_manager.get_mode(session_id)

        # 2. Build system prompt
        system_prompt = self._prompt_builder.build(session_id, mode)

        # 3. Get tools schema (mode-filtered)
        tools_schema = None
        if self._tool_registry and self._tool_registry.list_tools(mode):
            tools_schema = self._tool_registry.get_tools_schema(mode)

        # 4. Streaming tool call loop
        for iteration in range(MAX_TOOL_ITERATIONS):
            history = self._session_manager.get_history(session_id)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                *history,
            ]

            # Budget check (observe only, does not trigger compaction in Phase 1)
            if self._budget_tracker is not None:
                total_tokens = self._budget_tracker.counter.count_messages(messages)
                if tools_schema:
                    total_tokens += self._budget_tracker.counter.count_tools_schema(
                        tools_schema
                    )
                budget_status = self._budget_tracker.check(total_tokens)
                logger.info(
                    "budget_check",
                    session_id=session_id,
                    model=self._model,
                    iteration=iteration,
                    current_tokens=budget_status.current_tokens,
                    status=budget_status.status,
                    usable_budget=budget_status.usable_budget,
                    warn_threshold=budget_status.warn_threshold,
                    compact_threshold=budget_status.compact_threshold,
                    tokenizer_mode=budget_status.tokenizer_mode,
                )

            # Stream the LLM response — content tokens arrive immediately,
            # tool calls are accumulated and yielded at the end of the stream.
            collected_text = ""
            tool_calls_result: list[dict[str, str]] | None = None

            async for event in self._model_client.chat_stream_with_tools(
                messages, self._model, tools=tools_schema
            ):
                if isinstance(event, ContentDelta):
                    yield TextChunk(content=event.text)
                    collected_text += event.text
                elif isinstance(event, ToolCallsComplete):
                    tool_calls_result = event.tool_calls

            # Branch: tool calls detected
            if tool_calls_result:
                tool_calls_data = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    }
                    for tc in tool_calls_result
                ]
                await self._session_manager.append_message(
                    session_id,
                    "assistant",
                    collected_text,  # may be empty when model only returns tool_calls
                    tool_calls=tool_calls_data,
                    lock_token=lock_token,
                )

                for tc in tool_calls_result:
                    parsed_args, parse_err = _safe_parse_args(tc["arguments"])
                    if parse_err:
                        logger.warning(
                            "tool_call_args_parse_failed",
                            tool_name=tc["name"],
                            error=parse_err,
                            raw_args=tc["arguments"][:200],
                        )
                    yield ToolCallInfo(
                        tool_name=tc["name"],
                        arguments=parsed_args,
                        call_id=tc["id"],
                    )

                    # Execution gate: mode check (only for registered tools)
                    if (
                        self._tool_registry
                        and self._tool_registry.get(tc["name"]) is not None
                        and not self._tool_registry.check_mode(tc["name"], mode)
                    ):
                        logger.warning(
                            "tool_denied_by_mode",
                            tool_name=tc["name"],
                            mode=mode.value,
                            session_id=session_id,
                        )
                        denial_msg = (
                            f"Tool '{tc['name']}' is not available in "
                            f"'{mode.value}' mode."
                        )
                        denial_next = (
                            "当前为 chat_safe 模式，代码工具不可用。"
                            "未来版本将支持 coding 模式。"
                        )
                        yield ToolDenied(
                            tool_name=tc["name"],
                            call_id=tc["id"],
                            mode=mode.value,
                            error_code="MODE_DENIED",
                            message=denial_msg,
                            next_action=denial_next,
                        )
                        result = {
                            "ok": False,
                            "error_code": "MODE_DENIED",
                            "tool_name": tc["name"],
                            "mode": mode.value,
                            "message": denial_msg,
                            "next_action": denial_next,
                        }
                    else:
                        result = await self._execute_tool(tc["name"], tc["arguments"])

                    await self._session_manager.append_message(
                        session_id,
                        "tool",
                        json.dumps(result),
                        tool_call_id=tc["id"],
                        lock_token=lock_token,
                    )

                logger.info(
                    "tool_call_iteration",
                    iteration=iteration + 1,
                    tools_called=len(tool_calls_result),
                    session_id=session_id,
                )
                continue

            # Branch: no tool calls — this is the final text response
            await self._session_manager.append_message(
                session_id, "assistant", collected_text, lock_token=lock_token
            )
            logger.info(
                "response_complete", session_id=session_id, chars=len(collected_text)
            )
            return

        # Safety: max iterations
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
