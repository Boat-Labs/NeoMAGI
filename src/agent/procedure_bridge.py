"""Bridge between AgentLoop and ProcedureRuntime (P2-M2a).

Provides request-state helpers that load active procedures, build virtual
action schemas, and refresh procedure checkpoint after transitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.agent import AgentLoop
    from src.agent.message_flow import RequestState


async def resolve_procedure_for_request(
    loop: AgentLoop,
    session_id: str,
    mode: Any,
) -> tuple[Any, Any, dict] | None:
    """Load active procedure and build view + action map."""
    if loop._procedure_runtime is None:
        return None

    active = await loop._procedure_runtime.load_active(session_id)
    if active is None:
        return None, None, {}

    spec = loop._procedure_runtime._specs.get(active.spec_id)
    if spec is None:
        return active, None, {}

    from src.procedures.types import build_procedure_view

    view = build_procedure_view(spec, active)
    current_state = spec.states.get(active.state)
    action_map = dict(current_state.actions) if current_state else {}
    return active, view, action_map


def build_virtual_action_schemas(
    loop: AgentLoop,
    action_map: dict,
) -> list[dict[str, Any]]:
    """Build OpenAI function-calling schemas for virtual procedure actions."""
    from src.procedures.types import build_virtual_action_schema

    schemas: list[dict[str, Any]] = []
    for action_id, action in action_map.items():
        tool = loop._tool_registry.get(action.tool) if loop._tool_registry else None
        if tool is None:
            continue
        schema = build_virtual_action_schema(
            action_id, action, tool.description, tool.parameters,
        )
        schemas.append(schema)
    return schemas


async def rebuild_procedure_checkpoint(
    loop: AgentLoop,
    state: RequestState,
    build_system_prompt_fn: Any,
    resolve_tools_schema_fn: Any,
    merge_procedure_schemas_fn: Any,
) -> None:
    """Refresh procedure state, system prompt, and tool schema after a transition.

    Called by tool_concurrency after a successful procedure action so the
    next model iteration sees the updated checkpoint.
    """
    from src.procedures.types import build_procedure_view

    refreshed = await loop._procedure_runtime.load_active(state.session_id)
    if refreshed is None:
        state.active_procedure = None
        state.procedure_view = None
        state.procedure_action_map = {}
    else:
        state.active_procedure = refreshed
        spec = loop._procedure_runtime._specs.get(refreshed.spec_id)
        if spec is not None:
            state.procedure_view = build_procedure_view(spec, refreshed)
            current = spec.states.get(refreshed.state)
            state.procedure_action_map = dict(current.actions) if current else {}
        else:
            state.procedure_view = None
            state.procedure_action_map = {}

    state.system_prompt = build_system_prompt_fn(
        loop, state.session_id, state.mode, state.compacted_context,
        state.scope_key, state.recall_results, state.skill_view, state.procedure_view,
    )
    base_schema, base_list = resolve_tools_schema_fn(loop, state.mode)
    state.tools_schema, state.tools_schema_list = merge_procedure_schemas_fn(
        loop, base_schema, base_list, state.procedure_action_map,
    )
