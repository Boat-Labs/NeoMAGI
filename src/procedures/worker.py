"""WorkerExecutor — lightweight multi-turn executor for delegated tasks (P2-M2b D1).

No session persistence, no memory access, no compaction.
All conversation exists only in memory for the duration of one delegation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from src.procedures.handoff import HandoffPacket, WorkerResult
from src.procedures.roles import RoleSpec
from src.tools.base import BaseTool

if TYPE_CHECKING:
    from src.agent.model_client import ModelClient
    from src.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Worker system prompt template
# ---------------------------------------------------------------------------

_WORKER_SYSTEM = """\
You are a worker agent executing a delegated task.
You have access to a limited set of tools. Complete the task and return a structured result.

## Task Brief
{task_brief}

## Constraints
{constraints}

## Current State
{current_state}

## Evidence
{evidence}

## Open Questions
{open_questions}

Respond with a JSON object: {{"result": {{...}}, "evidence": [...], "open_questions": [...]}}
When you have the final answer, respond with text (no tool calls).
"""


# ---------------------------------------------------------------------------
# WorkerExecutor
# ---------------------------------------------------------------------------


class WorkerExecutor:
    """Lightweight executor for delegated subtasks.

    Runs bounded model call iterations with filtered tool access.
    All conversation is ephemeral — nothing is persisted.
    """

    def __init__(
        self,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        role_spec: RoleSpec,
        model: str = "gpt-4o-mini",
        scope_key: str = "main",
        session_id: str = "main",
    ) -> None:
        self._model_client = model_client
        self._tool_registry = tool_registry
        self._role_spec = role_spec
        self._model = model
        self._scope_key = scope_key
        self._session_id = session_id

    async def execute(self, packet: HandoffPacket) -> WorkerResult:
        """Execute a delegated task within bounded iterations."""
        allowed_tools = self._build_allowed_tools()
        tools_schema = self._build_tools_schema(allowed_tools)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt(packet)},
            {"role": "user", "content": packet.task_brief},
        ]

        collected_evidence: list[str] = list(packet.evidence)
        collected_questions: list[str] = list(packet.open_questions)

        for iteration in range(self._role_spec.max_iterations):
            iterations_used = iteration + 1

            response, err = await self._call_model(
                messages, tools_schema, packet.handoff_id, iterations_used,
                collected_evidence, collected_questions,
            )
            if err is not None:
                return err

            if not response.tool_calls:
                return self._extract_final_result(
                    response, iterations_used, collected_evidence, collected_questions,
                )

            normalized_calls = _normalize_tool_calls(response.tool_calls)
            messages.append(_assistant_message_from_calls(response.content, normalized_calls))
            await self._process_tool_calls(
                normalized_calls, allowed_tools, messages,
                packet.handoff_id, collected_evidence,
            )

        return WorkerResult(
            ok=False,
            error_code="WORKER_ITERATION_LIMIT",
            error_detail=f"Reached max iterations ({self._role_spec.max_iterations})",
            iterations_used=self._role_spec.max_iterations,
            evidence=tuple(collected_evidence),
            open_questions=tuple(collected_questions),
        )

    # -----------------------------------------------------------------------
    # Execute helpers
    # -----------------------------------------------------------------------

    async def _call_model(
        self,
        messages: list[dict[str, Any]],
        tools_schema: list[dict],
        handoff_id: str,
        iterations_used: int,
        collected_evidence: list[str],
        collected_questions: list[str],
    ) -> tuple[Any, WorkerResult | None]:
        """Call the model, returning (response, None) or (None, error_result)."""
        try:
            response = await self._model_client.chat_completion(
                messages,
                self._model,
                tools=tools_schema or None,
            )
        except Exception as exc:
            logger.warning(
                "worker_model_timeout",
                handoff_id=handoff_id,
                iteration=iterations_used,
                error=str(exc),
            )
            return None, WorkerResult(
                ok=False,
                error_code="WORKER_MODEL_TIMEOUT",
                error_detail=str(exc),
                iterations_used=iterations_used,
                evidence=tuple(collected_evidence),
                open_questions=tuple(collected_questions),
            )
        return response, None

    def _extract_final_result(
        self,
        response: Any,
        iterations_used: int,
        collected_evidence: list[str],
        collected_questions: list[str],
    ) -> WorkerResult:
        """Parse the model's final text response into a WorkerResult."""
        content = response.content or ""
        result_dict = _try_parse_json(content)
        inner_result = result_dict.get("result", result_dict)
        if not isinstance(inner_result, dict):
            inner_result = result_dict
        return WorkerResult(
            ok=True,
            result=inner_result,
            iterations_used=iterations_used,
            evidence=tuple(result_dict.get("evidence", collected_evidence)),
            open_questions=tuple(result_dict.get("open_questions", collected_questions)),
        )

    async def _process_tool_calls(
        self,
        normalized_calls: list[dict[str, str]],
        allowed_tools: dict[str, BaseTool],
        messages: list[dict[str, Any]],
        handoff_id: str,
        collected_evidence: list[str],
    ) -> None:
        """Execute each tool call and append results to messages."""
        for tc in normalized_calls:
            tool_name = tc["name"]
            tool = allowed_tools.get(tool_name)

            if tool is None:
                messages.append(_rejected_tool_message(tc, tool_name))
                logger.info(
                    "worker_tool_rejected",
                    tool_name=tool_name,
                    reason="not_in_allowed_set",
                    handoff_id=handoff_id,
                )
                continue

            result_str = await self._run_tool(
                tool, tc["arguments"], handoff_id, tool_name, collected_evidence,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result_str,
            })

    async def _run_tool(
        self,
        tool: BaseTool,
        tool_args_str: str,
        handoff_id: str,
        tool_name: str,
        collected_evidence: list[str],
    ) -> str:
        """Execute a single tool, returning JSON result string."""
        try:
            args = json.loads(tool_args_str) if tool_args_str else {}
        except json.JSONDecodeError:
            args = {}

        try:
            from src.tools.context import ToolContext as _TC

            worker_ctx = _TC(scope_key=self._scope_key, session_id=self._session_id)
            result = await tool.execute(args, worker_ctx)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.warning(
                "worker_tool_failed",
                tool_name=tool_name,
                handoff_id=handoff_id,
                error=str(exc),
            )
            collected_evidence.append(f"tool_failure:{tool_name}:{exc}")
            return json.dumps({"error": str(exc)})

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    def _build_allowed_tools(self) -> dict[str, BaseTool]:
        """Filter registry to tools allowed for this role.

        Triple filter:
        1. Only groups in role_spec.allowed_tool_groups
        2. Exclude is_procedure_only tools (D7)
        3. Exclude RiskLevel.high tools — workers bypass the normal
           check_pre_tool_guard path, so high-risk tools must be excluded
           at schema level to prevent unguarded writes
        """
        candidates = _collect_candidate_tools(self._tool_registry)
        return {
            tool.name: tool
            for tool in candidates
            if tool.group in self._role_spec.allowed_tool_groups
            and _is_worker_eligible(tool)
        }

    def _build_tools_schema(self, allowed_tools: dict[str, BaseTool]) -> list[dict]:
        """Build OpenAI function calling schema for allowed tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in allowed_tools.values()
        ]

    def _build_system_prompt(self, packet: HandoffPacket) -> str:
        return _WORKER_SYSTEM.format(
            task_brief=packet.task_brief,
            constraints="\n".join(f"- {c}" for c in packet.constraints) or "None",
            current_state=json.dumps(packet.current_state, ensure_ascii=False, default=str),
            evidence="\n".join(f"- {e}" for e in packet.evidence) or "None",
            open_questions="\n".join(f"- {q}" for q in packet.open_questions) or "None",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_candidate_tools(registry: ToolRegistry) -> list[BaseTool]:
    """Gather all tools from chat_safe and coding modes, deduplicated."""
    from src.tools.base import ToolMode

    seen: dict[str, BaseTool] = {}
    for mode in (ToolMode.chat_safe, ToolMode.coding):
        for tool in registry.list_tools(mode):
            seen[tool.name] = tool
    return list(seen.values())


def _is_worker_eligible(tool: BaseTool) -> bool:
    """Check whether a tool passes the worker filter (excludes procedure-only and high-risk)."""
    from src.tools.base import RiskLevel

    return not tool.is_procedure_only and tool.risk_level != RiskLevel.high


def _rejected_tool_message(tc: dict[str, str], tool_name: str) -> dict[str, Any]:
    """Build a tool-role message for a rejected (unavailable) tool call."""
    return {
        "role": "tool",
        "tool_call_id": tc.get("id", ""),
        "content": json.dumps({"error": f"Tool '{tool_name}' not available for worker"}),
    }


def _normalize_tool_calls(raw_calls: Any) -> list[dict[str, str]]:
    """Normalize OpenAI SDK tool call objects or dicts to plain dicts.

    ``chat_completion()`` returns ``ChatCompletionMessageToolCall`` Pydantic
    objects whereas the streaming accumulator yields plain dicts.  This
    helper accepts both and always returns ``[{"id", "name", "arguments"}]``.
    """
    result: list[dict[str, str]] = []
    for tc in raw_calls:
        if isinstance(tc, dict):
            result.append(tc)
        else:
            # Pydantic SDK object: attributes id, function.name, function.arguments
            fn = getattr(tc, "function", None)
            result.append({
                "id": getattr(tc, "id", "") or "",
                "name": getattr(fn, "name", "") if fn else "",
                "arguments": getattr(fn, "arguments", "") if fn else "",
            })
    return result


def _assistant_message_from_calls(
    content: str | None, normalized_calls: list[dict[str, str]],
) -> dict[str, Any]:
    """Build an assistant message dict from content + normalized tool calls."""
    msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if normalized_calls:
        msg["tool_calls"] = [
            {
                "id": tc.get("id", ""),
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in normalized_calls
        ]
    return msg


def _try_parse_json(text: str) -> dict[str, Any]:
    """Try to parse text as JSON, returning empty dict on failure."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if len(lines) > 2 else lines)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"raw": text}
    except (json.JSONDecodeError, ValueError):
        return {"raw": text}
