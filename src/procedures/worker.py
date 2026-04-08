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
    ) -> None:
        self._model_client = model_client
        self._tool_registry = tool_registry
        self._role_spec = role_spec
        self._model = model

    async def execute(self, packet: HandoffPacket) -> WorkerResult:
        """Execute a delegated task within bounded iterations."""
        allowed_tools = self._build_allowed_tools()
        tools_schema = self._build_tools_schema(allowed_tools)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt(packet)},
            {"role": "user", "content": packet.task_brief},
        ]

        iterations_used = 0
        collected_evidence: list[str] = list(packet.evidence)
        collected_questions: list[str] = list(packet.open_questions)

        for iteration in range(self._role_spec.max_iterations):
            iterations_used = iteration + 1
            try:
                response = await self._model_client.chat_completion(
                    messages,
                    self._model,
                    tools=tools_schema or None,
                )
            except Exception as exc:
                logger.warning(
                    "worker_model_timeout",
                    handoff_id=packet.handoff_id,
                    iteration=iterations_used,
                    error=str(exc),
                )
                return WorkerResult(
                    ok=False,
                    error_code="WORKER_MODEL_TIMEOUT",
                    error_detail=str(exc),
                    iterations_used=iterations_used,
                    evidence=tuple(collected_evidence),
                    open_questions=tuple(collected_questions),
                )

            if not response.tool_calls:
                # Final answer — extract from content
                content = response.content or ""
                result_dict = _try_parse_json(content)
                return WorkerResult(
                    ok=True,
                    result=result_dict,
                    iterations_used=iterations_used,
                    evidence=tuple(result_dict.get("evidence", collected_evidence)),
                    open_questions=tuple(
                        result_dict.get("open_questions", collected_questions)
                    ),
                )

            # Process tool calls
            messages.append(_assistant_message(response))

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args_str = tc["arguments"]
                tool = allowed_tools.get(tool_name)

                if tool is None:
                    logger.info(
                        "worker_tool_rejected",
                        tool_name=tool_name,
                        reason="not_in_allowed_set",
                        handoff_id=packet.handoff_id,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps({
                            "error": f"Tool '{tool_name}' not available for worker",
                        }),
                    })
                    continue

                try:
                    args = json.loads(tool_args_str) if tool_args_str else {}
                except json.JSONDecodeError:
                    args = {}

                try:
                    result = await tool.execute(args)
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                except Exception as exc:
                    logger.warning(
                        "worker_tool_failed",
                        tool_name=tool_name,
                        handoff_id=packet.handoff_id,
                        error=str(exc),
                    )
                    result_str = json.dumps({"error": str(exc)})
                    collected_evidence.append(f"tool_failure:{tool_name}:{exc}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_str,
                })

        # Iteration limit reached
        return WorkerResult(
            ok=False,
            error_code="WORKER_ITERATION_LIMIT",
            error_detail=f"Reached max iterations ({self._role_spec.max_iterations})",
            iterations_used=iterations_used,
            evidence=tuple(collected_evidence),
            open_questions=tuple(collected_questions),
        )

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    def _build_allowed_tools(self) -> dict[str, BaseTool]:
        """Filter registry to tools allowed for this role."""
        result: dict[str, BaseTool] = {}
        for group in self._role_spec.allowed_tool_groups:
            for mode in ("chat_safe", "coding"):
                from src.tools.base import ToolMode

                for tool in self._tool_registry.list_tools(ToolMode(mode)):
                    if tool.group == group and not tool.is_procedure_only:
                        result[tool.name] = tool
        return result

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


def _assistant_message(response: Any) -> dict[str, Any]:
    """Build an assistant message dict from a ChatCompletionMessage."""
    msg: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.get("id", ""),
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in response.tool_calls
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
