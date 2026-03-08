from __future__ import annotations

import json
from typing import Any

import structlog

from src.session.manager import MessageWithSeq

logger = structlog.get_logger()


def _safe_parse_args(raw: str | None) -> tuple[dict, str | None]:
    """Parse JSON tool call arguments. Returns (dict, error_message | None)."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {}, f"JSON parse error: {exc}"
    if not isinstance(parsed, dict):
        return {}, f"Expected dict, got {type(parsed).__name__}"
    return parsed, None


def _messages_with_seq_to_openai(messages: list[MessageWithSeq]) -> list[dict[str, Any]]:
    """Convert MessageWithSeq list to OpenAI chat format dicts."""
    result: list[dict[str, Any]] = []
    for message in messages:
        payload: dict[str, Any] = {"role": message.role, "content": message.content or ""}
        if message.tool_calls is not None:
            payload["tool_calls"] = message.tool_calls
        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id
        result.append(payload)
    return result


def _sanitize_tool_call_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop malformed tool-call fragments before sending history to the LLM."""
    sanitized: list[dict[str, Any]] = []
    pending_ids: set[str] | None = None
    chain_start_idx: int | None = None
    pending_assistant: dict[str, Any] | None = None
    index = 0
    while index < len(messages):
        message = messages[index]
        if pending_ids is None:
            handled = _append_clean_history_message(
                message,
                sanitized,
                pending_state=(pending_ids, chain_start_idx, pending_assistant),
            )
            index += 1
            if handled is None:
                continue
            pending_ids, chain_start_idx, pending_assistant = handled
            continue
        handled = _append_pending_tool_message(
            message,
            sanitized,
            pending_ids,
            chain_start_idx,
            pending_assistant,
        )
        if handled is None:
            pending_ids, chain_start_idx, pending_assistant = _rollback_incomplete_chain(
                sanitized,
                chain_start_idx,
                pending_assistant,
                "dropping_incomplete_tool_call_chain",
            )
            continue
        pending_ids, chain_start_idx, pending_assistant = handled
        index += 1
    if pending_ids:
        _rollback_incomplete_chain(
            sanitized,
            chain_start_idx,
            pending_assistant,
            "dropping_trailing_incomplete_tool_call_chain",
        )
    return sanitized


def _append_clean_history_message(
    message: dict[str, Any],
    sanitized: list[dict[str, Any]],
    *,
    pending_state: tuple[set[str] | None, int | None, dict[str, Any] | None],
) -> tuple[set[str] | None, int | None, dict[str, Any] | None] | None:
    role = message.get("role")
    if role == "tool":
        logger.warning("dropping_orphan_tool_message", tool_call_id=message.get("tool_call_id"))
        return None
    pending_ids, _, _ = pending_state
    tool_calls = message.get("tool_calls")
    if role == "assistant" and tool_calls:
        call_ids = _tool_call_ids(tool_calls)
        if not call_ids:
            _append_assistant_fallback(message, sanitized)
            logger.warning("dropping_malformed_tool_calls_payload")
            return None
        start_idx = len(sanitized)
        sanitized.append(message)
        return set(call_ids), start_idx, message
    sanitized.append(message)
    return pending_ids, None, None


def _append_pending_tool_message(
    message: dict[str, Any],
    sanitized: list[dict[str, Any]],
    pending_ids: set[str],
    chain_start_idx: int | None,
    pending_assistant: dict[str, Any] | None,
) -> tuple[set[str] | None, int | None, dict[str, Any] | None] | None:
    if message.get("role") != "tool":
        return None
    tool_call_id = message.get("tool_call_id")
    if isinstance(tool_call_id, str) and tool_call_id in pending_ids:
        sanitized.append(message)
        pending_ids.remove(tool_call_id)
        if pending_ids:
            return pending_ids, chain_start_idx, pending_assistant
        return None, None, None
    logger.warning("dropping_unexpected_tool_message", tool_call_id=tool_call_id)
    return pending_ids, chain_start_idx, pending_assistant


def _rollback_incomplete_chain(
    sanitized: list[dict[str, Any]],
    chain_start_idx: int | None,
    pending_assistant: dict[str, Any] | None,
    reason: str,
) -> tuple[None, None, None]:
    if chain_start_idx is None:
        return None, None, None
    fallback_text = (
        pending_assistant.get("content", "") if isinstance(pending_assistant, dict) else ""
    )
    del sanitized[chain_start_idx:]
    if isinstance(fallback_text, str) and fallback_text.strip():
        sanitized.append({"role": "assistant", "content": fallback_text})
    logger.warning(reason)
    return None, None, None


def _tool_call_ids(tool_calls: Any) -> set[str]:
    return {
        tool_call.get("id")
        for tool_call in tool_calls
        if isinstance(tool_call, dict) and isinstance(tool_call.get("id"), str)
    }


def _append_assistant_fallback(message: dict[str, Any], sanitized: list[dict[str, Any]]) -> None:
    content = message.get("content", "")
    if isinstance(content, str) and content.strip():
        sanitized.append({"role": "assistant", "content": content})
