"""Tool concurrency: execution grouping and bounded parallel dispatch.

Provides the group builder, single-tool runner, and parallel executor
for same-turn read-only tool call batching (P2-M1 Post Works P2).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from src.agent.events import ToolDenied
from src.agent.guardrail import GuardCheckResult

logger = structlog.get_logger(__name__)
_parallel_logger = structlog.get_logger("tool_concurrency")

# Maximum number of tool calls executed concurrently within a single parallel group.
_MAX_PARALLEL_TOOLS = 3

# OI-M2-04 hotfix: per-request limit for the same non-read-only tool.
# Allows 3 executions; the 4th is rejected.
WRITE_TOOL_REQUEST_LIMIT = 3

# Error codes from guardrail.py that indicate a guard denial (not a tool failure).
_GUARD_DENY_CODES = frozenset({
    "GUARD_CONTRACT_UNAVAILABLE",
    "GUARD_ANCHOR_MISSING",
    "MODE_DENIED",
})

# Procedure error codes that represent guard denials, not tool failures.
_PROCEDURE_DENY_CODES = frozenset({
    "PROCEDURE_ACTION_DENIED",
    "PROCEDURE_CONFLICT",
})


@dataclass
class _ExecutionGroup:
    """A batch of tool calls sharing the same execution strategy."""

    tool_calls: list[dict[str, str]]
    parallel: bool  # True only when ALL tools are read_only + concurrency_safe
    start_index: int  # original index of the first tool call in the turn


@dataclass
class _ToolOutcome:
    """Result of a single tool execution, for deferred yield / append."""

    tool_call: dict[str, str]
    result: dict[str, Any]
    denied_event: ToolDenied | None = None
    failure_signal: str | None = None


# ---------------------------------------------------------------------------
# Group builder
# ---------------------------------------------------------------------------


def _build_execution_groups(
    tool_calls: list[dict[str, str]],
    registry: Any,
    procedure_action_ids: frozenset[str] | None = None,
) -> list[_ExecutionGroup]:
    """Split tool calls into execution groups.

    Consecutive tools that are both read_only AND concurrency_safe form a
    parallel group.  Any other tool acts as a barrier that flushes the
    current parallel group and executes alone.

    Procedure virtual actions are always barriers (P2-M2a D4).
    """
    groups: list[_ExecutionGroup] = []
    pending: list[dict[str, str]] = []
    pending_start = 0

    for i, tc in enumerate(tool_calls):
        if _is_parallel_eligible(tc["name"], registry, procedure_action_ids):
            if not pending:
                pending_start = i
            pending.append(tc)
        else:
            if pending:
                groups.append(
                    _ExecutionGroup(tool_calls=pending, parallel=True, start_index=pending_start)
                )
                pending = []
            groups.append(_ExecutionGroup(tool_calls=[tc], parallel=False, start_index=i))

    if pending:
        groups.append(
            _ExecutionGroup(tool_calls=pending, parallel=True, start_index=pending_start)
        )
    return groups


def _is_parallel_eligible(
    tool_name: str,
    registry: Any,
    procedure_action_ids: frozenset[str] | None = None,
) -> bool:
    """Return True only if the tool declares both read_only and concurrency_safe.

    Procedure virtual actions are ALWAYS barriers (never parallel).
    """
    if procedure_action_ids and tool_name in procedure_action_ids:
        return False
    if registry is None:
        return False
    tool = registry.get(tool_name)
    if tool is None:
        return False
    return tool.is_read_only and tool.is_concurrency_safe


# ---------------------------------------------------------------------------
# Single-tool runner
# ---------------------------------------------------------------------------


def _mode_denial(
    registry: Any,
    mode: Any,
    session_id: str,
    tool_call: dict[str, str],
) -> tuple[ToolDenied, dict[str, Any]] | None:
    """Check if a tool is denied in the current mode."""
    tool = registry.get(tool_call["name"]) if registry else None
    if tool is None or registry.check_mode(tool_call["name"], mode):
        return None
    logger.warning(
        "tool_denied_by_mode",
        tool_name=tool_call["name"],
        mode=mode.value,
        session_id=session_id,
    )
    message = f"Tool '{tool_call['name']}' is not available in '{mode.value}' mode."
    next_action = "当前为 chat_safe 模式，代码工具不可用。请切换到 coding 模式后再试。"
    denied = ToolDenied(
        tool_name=tool_call["name"],
        call_id=tool_call["id"],
        mode=mode.value,
        error_code="MODE_DENIED",
        message=message,
        next_action=next_action,
    )
    result = {
        "ok": False,
        "error_code": "MODE_DENIED",
        "tool_name": tool_call["name"],
        "mode": mode.value,
        "message": message,
        "next_action": next_action,
    }
    return denied, result


def _check_write_limit(loop, state, tool_call) -> _ToolOutcome | None:
    """Return a breaker outcome if the tool hit the per-request write limit."""
    tool_name = tool_call["name"]
    tool_obj = loop._tool_registry.get(tool_name) if loop._tool_registry else None
    if tool_obj is None or tool_obj.is_read_only:
        return None
    count = state.write_tool_counts.get(tool_name, 0)
    if count >= WRITE_TOOL_REQUEST_LIMIT:
        logger.info(
            "write_tool_request_limit", tool_name=tool_name,
            count=count, limit=WRITE_TOOL_REQUEST_LIMIT,
            session_id=state.session_id,
        )
        return _ToolOutcome(
            tool_call=tool_call,
            result={
                "ok": False, "error_code": "WRITE_TOOL_REQUEST_LIMIT",
                "detail": (
                    f"Tool '{tool_name}' already executed "
                    f"{WRITE_TOOL_REQUEST_LIMIT} times in this request. "
                    "Stop and respond to the user in text."
                ),
            },
            failure_signal=f"tool_failure:{tool_name}",
        )
    state.write_tool_counts[tool_name] = count + 1
    return None


def _classify_failure(result, tool_call) -> str | None:
    """Return a failure signal string, or None if the result is OK."""
    if isinstance(result, dict) and not result.get("ok", True):
        code = result.get("error_code", "")
        name = tool_call["name"]
        return f"guard_denied:{name}" if code in _GUARD_DENY_CODES else f"tool_failure:{name}"
    return None


async def _run_single_tool(
    loop: Any, state: Any, tool_call: dict[str, str],
    guard_state: GuardCheckResult,
) -> _ToolOutcome:
    """Execute one tool call and return an outcome without side effects."""
    if state.procedure_action_map and tool_call["name"] in state.procedure_action_map:
        return await _run_procedure_action(loop, state, tool_call, guard_state)

    breaker = _check_write_limit(loop, state, tool_call)
    if breaker is not None:
        return breaker

    denial = _mode_denial(loop._tool_registry, state.mode, state.session_id, tool_call)
    if denial is not None:
        denied_event, result = denial
        return _ToolOutcome(
            tool_call=tool_call, result=result, denied_event=denied_event,
            failure_signal=f"guard_denied:{tool_call['name']}",
        )
    result = await loop._execute_tool(
        tool_call["name"], tool_call["arguments"],
        scope_key=state.scope_key, session_id=state.session_id,
        guard_state=guard_state,
        principal_id=getattr(state, "principal_id", None),
    )
    return _ToolOutcome(
        tool_call=tool_call, result=result,
        failure_signal=_classify_failure(result, tool_call),
    )


async def _run_procedure_action(
    loop: Any,
    state: Any,
    tool_call: dict[str, str],
    guard_state: GuardCheckResult,
) -> _ToolOutcome:
    """Route a virtual action tool call to ProcedureRuntime.apply_action()."""
    from src.tools.context import ToolContext

    action_id = tool_call["name"]
    active = state.active_procedure
    if active is None or loop._procedure_runtime is None:
        return _ToolOutcome(tool_call=tool_call, result={
            "ok": False, "error_code": "PROCEDURE_UNKNOWN",
            "message": "No active procedure for this session",
        })

    # D8: build ProcedureActionDeps and inject into ToolContext
    procedure_deps = _build_procedure_deps(loop, state)
    tool_context = ToolContext(
        scope_key=state.scope_key,
        session_id=state.session_id,
        principal_id=getattr(state, "principal_id", None),
        actor=_resolve_actor(),
        procedure_deps=procedure_deps,
    )
    result = await loop._procedure_runtime.apply_action(
        instance_id=active.instance_id,
        action_id=action_id,
        args_json=tool_call["arguments"],
        expected_revision=active.revision,
        tool_context=tool_context,
        guard_state=guard_state,
        mode=state.mode,
    )

    if isinstance(result, dict) and result.get("ok", False):
        await _refresh_procedure_state(loop, state)
        # D9: check for publish flush signal
        await _handle_publish_flush(loop, state, result)

    failure_signal: str | None = None
    if isinstance(result, dict) and not result.get("ok", True):
        error_code = result.get("error_code", "")
        if error_code in _PROCEDURE_DENY_CODES:
            failure_signal = f"guard_denied:{action_id}"
        else:
            failure_signal = f"tool_failure:{action_id}"
    return _ToolOutcome(tool_call=tool_call, result=result, failure_signal=failure_signal)


def _build_procedure_deps(loop: Any, state: Any) -> Any:
    """Build ProcedureActionDeps from AgentLoop state (D8)."""
    from src.procedures.deps import ProcedureActionDeps

    spec = None
    if loop._procedure_runtime and state.active_procedure:
        spec = loop._procedure_runtime._specs.get(state.active_procedure.spec_id)
    return ProcedureActionDeps(
        active_procedure=state.active_procedure,
        spec=spec,
        model_client=loop._model_client,
        model=loop._model,
    )


def _resolve_actor() -> Any:
    """Resolve the current actor role. V1: always primary (P1-2r3)."""
    from src.procedures.roles import AgentRole

    return AgentRole.primary


async def _handle_publish_flush(loop: Any, state: Any, result: dict) -> None:
    """D9: extract publish flush texts and persist via existing pipeline."""
    flush_texts = result.get("_publish_flush_texts")
    if not flush_texts:
        return

    from src.agent.memory_flush import MemoryFlushCandidate

    candidates = [
        MemoryFlushCandidate(
            source_session_id=state.session_id,
            candidate_text=text,
            constraint_tags=["published_result"],
            confidence=1.0,
        )
        for text in flush_texts
        if text.strip()
    ]
    if candidates and hasattr(loop, "_persist_flush_candidates"):
        await loop._persist_flush_candidates(
            candidates,
            state.session_id,
            scope_key=state.scope_key,
        )


async def _refresh_procedure_state(loop: Any, state: Any) -> None:
    """Refresh procedure checkpoint, system prompt, and tool schema.

    Called after a successful procedure action to ensure the next model
    iteration sees the updated state, allowed actions, and prompt view.
    """
    from src.agent.message_flow import _rebuild_procedure_checkpoint

    await _rebuild_procedure_checkpoint(loop, state)


# ---------------------------------------------------------------------------
# Parallel / serial executor + observability
# ---------------------------------------------------------------------------


async def _execute_group(
    loop: Any,
    state: Any,
    group: _ExecutionGroup,
    guard_state: GuardCheckResult,
    *,
    session_id: str,
    iteration: int,
) -> list[_ToolOutcome]:
    """Execute all tool calls in a group, returning ordered outcomes."""
    if group.parallel and len(group.tool_calls) > 1:
        return await _execute_parallel(
            loop, state, group, guard_state,
            session_id=session_id, iteration=iteration,
        )
    # Serial: single barrier tool OR single-element parallel group.
    if not group.parallel:
        tc = group.tool_calls[0]
        _parallel_logger.info(
            "serial_barrier_tool",
            session_id=session_id,
            iteration=iteration,
            tool_index=group.start_index,
            tool_name=tc["name"],
            reason="not_parallel_eligible",
        )
    outcomes: list[_ToolOutcome] = []
    for tc in group.tool_calls:
        outcomes.append(await _run_single_tool(loop, state, tc, guard_state))
    return outcomes


async def _execute_parallel(
    loop: Any,
    state: Any,
    group: _ExecutionGroup,
    guard_state: GuardCheckResult,
    *,
    session_id: str,
    iteration: int,
) -> list[_ToolOutcome]:
    """Execute tool calls concurrently in bounded batches."""
    tool_calls = group.tool_calls
    all_outcomes: list[_ToolOutcome] = []
    tool_names = [tc["name"] for tc in tool_calls]
    effective_concurrency = min(len(tool_calls), _MAX_PARALLEL_TOOLS)

    _parallel_logger.info(
        "tool_parallel_group_started",
        session_id=session_id,
        iteration=iteration,
        group_index=group.start_index,
        group_size=len(tool_calls),
        max_concurrency=effective_concurrency,
        tool_names=tool_names,
    )

    for batch_start in range(0, len(tool_calls), _MAX_PARALLEL_TOOLS):
        batch = tool_calls[batch_start : batch_start + _MAX_PARALLEL_TOOLS]
        results: list[_ToolOutcome] = [None] * len(batch)  # type: ignore[list-item]
        async with asyncio.TaskGroup() as tg:
            for idx, tc in enumerate(batch):
                tg.create_task(
                    _run_and_store(loop, state, tc, guard_state, results, idx)
                )
        all_outcomes.extend(results)

    _parallel_logger.info(
        "tool_parallel_group_finished",
        session_id=session_id,
        iteration=iteration,
        group_index=group.start_index,
        group_size=len(tool_calls),
        max_concurrency=effective_concurrency,
        tool_names=tool_names,
    )
    return all_outcomes


async def _run_and_store(
    loop: Any,
    state: Any,
    tool_call: dict[str, str],
    guard_state: GuardCheckResult,
    results: list[_ToolOutcome],
    index: int,
) -> None:
    """Run a single tool and place the outcome at *index* in *results*."""
    results[index] = await _run_single_tool(loop, state, tool_call, guard_state)
