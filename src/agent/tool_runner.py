from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

from src.agent.guardrail import GuardCheckResult, check_pre_tool_guard
from src.tools.context import ToolContext

if TYPE_CHECKING:
    from src.agent.agent import AgentLoop

logger = structlog.get_logger()


async def execute_tool(
    loop: AgentLoop,
    tool_name: str,
    arguments_json: str,
    *,
    scope_key: str,
    session_id: str,
    guard_state: GuardCheckResult,
) -> dict:
    """Execute a tool by name. Returns result dict or error dict."""
    if not loop._tool_registry:
        return {"error_code": "NO_REGISTRY", "message": "Tool registry not available"}
    tool = loop._tool_registry.get(tool_name)
    if not tool:
        logger.warning("unknown_tool", tool_name=tool_name)
        return {"error_code": "UNKNOWN_TOOL", "message": f"Unknown tool: {tool_name}"}
    blocked = check_pre_tool_guard(guard_state, tool_name, tool.risk_level)
    if blocked is not None:
        return {
            "ok": False,
            "error_code": blocked.error_code,
            "tool_name": tool_name,
            "message": blocked.detail,
        }
    arguments, error = _parse_tool_arguments(arguments_json)
    if error is not None:
        return error
    context = ToolContext(scope_key=scope_key, session_id=session_id)
    try:
        result = await tool.execute(arguments, context)
        logger.info("tool_executed", tool_name=tool_name)
        return result
    except Exception:
        logger.exception("tool_execution_failed", tool_name=tool_name)
        return {"error_code": "EXECUTION_ERROR", "message": f"Tool {tool_name} failed"}


def _parse_tool_arguments(arguments_json: str) -> tuple[dict, dict | None]:
    try:
        arguments = json.loads(arguments_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return {}, {"error_code": "INVALID_ARGS", "message": f"Invalid JSON arguments: {exc}"}
    if not isinstance(arguments, dict):
        return {}, {
            "error_code": "INVALID_ARGS",
            "message": f"Expected dict arguments, got {type(arguments).__name__}",
        }
    return arguments, None
