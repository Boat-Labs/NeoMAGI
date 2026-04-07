from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.agent.events import TextChunk, ToolCallInfo
from src.agent.guardrail import GuardCheckResult, check_pre_llm_guard, maybe_refresh_contract
from src.agent.model_client import ContentDelta, ToolCallsComplete
from src.agent.tool_concurrency import _build_execution_groups, _execute_group
from src.agent.tool_history import (
    _messages_with_seq_to_openai,
    _safe_parse_args,
    _sanitize_tool_call_history,
)
from src.session.scope_resolver import SessionIdentity

if TYPE_CHECKING:
    from src.agent.agent import AgentLoop


@dataclass
class RequestState:
    session_id: str
    lock_token: str | None
    mode: Any
    scope_key: str
    current_user_seq: int | None
    tools_schema: list[dict[str, Any]] | None
    tools_schema_list: list[dict[str, Any]]
    compaction_count: int
    max_compactions: int
    last_compaction_seq: int | None
    compacted_context: str | None
    recall_results: list[Any]
    system_prompt: str
    # ── skill runtime fields (P2-M1b-P3) ──
    task_frame: Any = None  # TaskFrame | None
    resolved_skills: tuple = ()  # tuple[SkillSpec, ...]
    skill_view: Any = None  # ResolvedSkillView | None
    teaching_intent: bool = False
    accumulated_failure_signals: list = None  # type: ignore[assignment]
    # ── procedure runtime fields (P2-M2a) ──
    active_procedure: Any = None  # ActiveProcedure | None
    procedure_view: Any = None  # ProcedureView | None
    procedure_action_map: dict = None  # type: ignore[assignment]  # action_id -> ActionSpec

    def __post_init__(self) -> None:
        if self.accumulated_failure_signals is None:
            self.accumulated_failure_signals = []
        if self.procedure_action_map is None:
            self.procedure_action_map = {}


@dataclass
class IterationPrep:
    messages: list[dict[str, Any]]
    guard_state: GuardCheckResult
    stop_text: str | None = None


@dataclass
class StreamEventState:
    text: str | None = None
    tool_calls: list[dict[str, str]] | None = None


async def handle_message(
    loop: AgentLoop,
    session_id: str,
    content: str,
    *,
    lock_token: str | None = None,
    identity: SessionIdentity | None = None,
    dm_scope: str | None = None,
    max_tool_iterations: int,
) -> Any:
    state = await _initialize_request_state(
        loop,
        session_id,
        content,
        lock_token=lock_token,
        identity=identity,
        dm_scope=dm_scope,
    )
    async for event in _run_iteration_loop(loop, state, max_tool_iterations=max_tool_iterations):
        yield event


async def _initialize_request_state(
    loop: AgentLoop,
    session_id: str,
    content: str,
    *,
    lock_token: str | None,
    identity: SessionIdentity | None,
    dm_scope: str | None,
) -> RequestState:
    await _ensure_bootstrap(loop)
    user_msg = await loop._session_manager.append_message(
        session_id,
        "user",
        content,
        lock_token=lock_token,
    )
    scope_key = _resolve_scope_key(loop, session_id, identity, dm_scope)
    mode = await loop._session_manager.get_mode(session_id)
    tools_schema, tools_schema_list = _resolve_tools_schema(loop, mode)
    last_compaction_seq, compacted_context = await _load_compaction_state(loop, session_id)
    recall_results = await loop._fetch_memory_recall(session_id, scope_key=scope_key)
    loop._contract = maybe_refresh_contract(loop._contract, loop._workspace_dir)

    skill_result = await _resolve_skills_for_request(loop, content, mode, identity)
    procedure_result = await _resolve_procedure_for_request(loop, session_id, mode)

    return _assemble_request_state(
        loop, session_id, lock_token, mode, scope_key, user_msg.seq,
        tools_schema, tools_schema_list,
        last_compaction_seq, compacted_context, recall_results,
        skill_result, procedure_result,
    )


def _assemble_request_state(
    loop: AgentLoop,
    session_id: str,
    lock_token: str | None,
    mode: Any,
    scope_key: str,
    current_user_seq: int | None,
    tools_schema: list[dict[str, Any]] | None,
    tools_schema_list: list[dict[str, Any]],
    last_compaction_seq: int | None,
    compacted_context: str | None,
    recall_results: list[Any],
    skill_result: tuple[Any, tuple, Any, bool],
    procedure_result: tuple[Any, Any, dict] | None = None,
) -> RequestState:
    """Build the final RequestState from pre-resolved components."""
    task_frame, resolved_skills, skill_view, teaching_intent = skill_result
    active_procedure, procedure_view, procedure_action_map = (
        procedure_result if procedure_result else (None, None, {})
    )
    tools_schema, tools_schema_list = _merge_procedure_schemas(
        loop, tools_schema, tools_schema_list, procedure_action_map,
    )
    system_prompt = _build_system_prompt(
        loop, session_id, mode, compacted_context, scope_key, recall_results,
        skill_view, procedure_view,
    )
    max_compactions = loop._settings.max_compactions_per_request if loop._settings else 2
    return RequestState(
        session_id=session_id, lock_token=lock_token, mode=mode,
        scope_key=scope_key, current_user_seq=current_user_seq,
        tools_schema=tools_schema, tools_schema_list=tools_schema_list,
        compaction_count=0, max_compactions=max_compactions,
        last_compaction_seq=last_compaction_seq, compacted_context=compacted_context,
        recall_results=recall_results, system_prompt=system_prompt,
        task_frame=task_frame, resolved_skills=resolved_skills,
        skill_view=skill_view, teaching_intent=teaching_intent,
        active_procedure=active_procedure, procedure_view=procedure_view,
        procedure_action_map=procedure_action_map,
    )


def _merge_procedure_schemas(
    loop: AgentLoop,
    tools_schema: list[dict[str, Any]] | None,
    tools_schema_list: list[dict[str, Any]],
    procedure_action_map: dict,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Merge procedure virtual action schemas into tools schema."""
    if not procedure_action_map or tools_schema is None:
        return tools_schema, tools_schema_list
    virtual_schemas = _build_virtual_action_schemas(loop, procedure_action_map)
    merged = tools_schema + virtual_schemas
    return merged, merged


async def _resolve_skills_for_request(
    loop: AgentLoop,
    content: str,
    mode: Any,
    identity: SessionIdentity | None,
) -> tuple[Any, tuple, Any, bool]:
    """Extract task frame and resolve skills for the current request."""
    if loop._skill_resolver is None:
        return None, (), None, False

    from src.skills.task_frame import extract_task_frame

    tool_names = (
        tuple(t.name for t in loop._tool_registry.list_tools(mode))
        if loop._tool_registry
        else ()
    )
    channel = identity.channel_type if identity else None
    task_frame = extract_task_frame(
        content,
        mode=mode.value if hasattr(mode, "value") else str(mode),
        channel=channel,
        available_tools=tool_names,
    )
    candidates = await loop._skill_resolver.resolve(task_frame)
    skill_view = None
    if candidates and loop._skill_projector is not None:
        skill_view = loop._skill_projector.project(candidates, task_frame)
    resolved_skills = tuple(spec for spec, _ in candidates) if candidates else ()
    teaching_intent = _detect_teaching_intent(content)
    return task_frame, resolved_skills, skill_view, teaching_intent


async def _resolve_procedure_for_request(
    loop: AgentLoop, session_id: str, mode: Any,
) -> tuple[Any, Any, dict] | None:
    from src.agent.procedure_bridge import resolve_procedure_for_request
    return await resolve_procedure_for_request(loop, session_id, mode)


def _build_virtual_action_schemas(loop: AgentLoop, action_map: dict) -> list[dict[str, Any]]:
    from src.agent.procedure_bridge import build_virtual_action_schemas
    return build_virtual_action_schemas(loop, action_map)


async def _rebuild_procedure_checkpoint(loop: AgentLoop, state: RequestState) -> None:
    from src.agent.procedure_bridge import rebuild_procedure_checkpoint
    await rebuild_procedure_checkpoint(
        loop, state, _build_system_prompt, _resolve_tools_schema, _merge_procedure_schemas,
    )


async def _run_iteration_loop(
    loop: AgentLoop,
    state: RequestState,
    *,
    max_tool_iterations: int,
) -> Any:
    for iteration in range(max_tool_iterations):
        prep = await _prepare_iteration(loop, state, iteration)
        if prep.stop_text is not None:
            yield TextChunk(content=prep.stop_text)
            return
        collected_text = ""
        tool_calls_result: list[dict[str, str]] | None = None
        async for event in loop._model_client.chat_stream_with_tools(
            prep.messages,
            loop._model,
            tools=state.tools_schema,
        ):
            stream_state = _parse_stream_event(event)
            if stream_state.text is not None:
                yield TextChunk(content=stream_state.text)
                collected_text += stream_state.text
            tool_calls_result = _merge_tool_calls_result(
                tool_calls_result,
                stream_state.tool_calls,
            )
        if tool_calls_result:
            async for event in _handle_tool_calls(
                loop,
                state,
                iteration,
                collected_text,
                tool_calls_result,
                prep.guard_state,
            ):
                yield event
            continue
        await _complete_assistant_response(loop, state, collected_text)
        return
    _agent_logger().warning(
        "max_tool_iterations",
        max=max_tool_iterations,
        session_id=state.session_id,
    )
    await _finalize_task_terminal(
        loop, state, "max_iterations",
        success=False,
        failure_signals=tuple(state.accumulated_failure_signals),
    )
    yield TextChunk(content="I've reached the maximum number of tool calls. Please try again.")


async def _prepare_iteration(
    loop: AgentLoop,
    state: RequestState,
    iteration: int,
) -> IterationPrep:
    messages = _iteration_messages(loop, state)
    budget_status = _budget_status(loop, state, messages, iteration)
    if budget_status is not None and _should_compact(loop, state, budget_status):
        stop_text = await _apply_compaction(loop, state, budget_status)
        if stop_text is not None:
            return IterationPrep(
                messages=[],
                guard_state=check_pre_llm_guard(loop._contract, ""),
                stop_text=stop_text,
            )
        messages = _iteration_messages(loop, state)
    guard_state = check_pre_llm_guard(loop._contract, _execution_context(messages))
    return IterationPrep(messages=messages, guard_state=guard_state)


def _iteration_messages(loop: AgentLoop, state: RequestState) -> list[dict[str, Any]]:
    effective_messages = loop._session_manager.get_effective_history(
        state.session_id,
        state.last_compaction_seq,
    )
    history = _sanitize_tool_call_history(_messages_with_seq_to_openai(effective_messages))
    return [{"role": "system", "content": state.system_prompt}, *history]


def _budget_status(
    loop: AgentLoop,
    state: RequestState,
    messages: list[dict[str, Any]],
    iteration: int,
) -> Any | None:
    if loop._budget_tracker is None:
        return None
    total_tokens = loop._budget_tracker.counter.count_messages(messages)
    if state.tools_schema_list:
        total_tokens += loop._budget_tracker.counter.count_tools_schema(state.tools_schema_list)
    budget_status = loop._budget_tracker.check(total_tokens)
    _agent_logger().info(
        "budget_check",
        session_id=state.session_id,
        model=loop._model,
        iteration=iteration,
        current_tokens=budget_status.current_tokens,
        status=budget_status.status,
        usable_budget=budget_status.usable_budget,
        warn_threshold=budget_status.warn_threshold,
        compact_threshold=budget_status.compact_threshold,
        tokenizer_mode=budget_status.tokenizer_mode,
    )
    return budget_status


def _should_compact(loop: AgentLoop, state: RequestState, budget_status: Any) -> bool:
    return (
        budget_status.status == "compact_needed"
        and loop._compaction_engine is not None
        and state.compaction_count < state.max_compactions
        and state.lock_token is not None
        and state.current_user_seq is not None
    )


async def _apply_compaction(loop: AgentLoop, state: RequestState, budget_status: Any) -> str | None:
    result = await loop._try_compact(
        session_id=state.session_id,
        system_prompt=state.system_prompt,
        tools_schema_list=state.tools_schema_list,
        budget_status=budget_status,
        last_compaction_seq=state.last_compaction_seq,
        compacted_context=state.compacted_context,
        current_user_seq=state.current_user_seq,
        lock_token=state.lock_token,
        scope_key=state.scope_key,
    )
    state.compaction_count += 1
    if result is None or result.status == "noop":
        return None
    _apply_compaction_result(loop, state, result)
    return await _ensure_compaction_within_budget(loop, state)


def _apply_compaction_result(loop: AgentLoop, state: RequestState, result: Any) -> None:
    state.last_compaction_seq = result.new_compaction_seq
    state.compacted_context = result.compacted_context
    state.system_prompt = _build_system_prompt(
        loop,
        state.session_id,
        state.mode,
        state.compacted_context,
        state.scope_key,
        state.recall_results,
        state.skill_view,
        state.procedure_view,
    )


async def _ensure_compaction_within_budget(loop: AgentLoop, state: RequestState) -> str | None:
    messages = _iteration_messages(loop, state)
    if _final_budget_status(loop, state, messages).status != "compact_needed":
        return None
    original_turns = loop._settings.min_preserved_turns
    reduced_turns = max(original_turns // 2, 1)
    _agent_logger().warning(
        "post_compaction_still_over_budget",
        original_preserved=original_turns,
        reduced_preserved=reduced_turns,
        tokens=loop._budget_tracker.counter.count_messages(messages),
    )
    result = loop._emergency_trim(
        session_id=state.session_id,
        current_user_seq=state.current_user_seq,
        min_preserved_turns_override=reduced_turns,
    )
    if result is None:
        _agent_logger().error("emergency_trim_returned_none", session_id=state.session_id)
        return _over_budget_text()
    return await _store_emergency_result(loop, state, result)


def _final_budget_status(
    loop: AgentLoop,
    state: RequestState,
    messages: list[dict[str, Any]],
) -> Any:
    total_tokens = loop._budget_tracker.counter.count_messages(messages)
    if state.tools_schema_list:
        total_tokens += loop._budget_tracker.counter.count_tools_schema(state.tools_schema_list)
    return loop._budget_tracker.check(total_tokens)


async def _store_emergency_result(loop: AgentLoop, state: RequestState, result: Any) -> str | None:
    try:
        await loop._session_manager.store_compaction_result(
            state.session_id,
            result,
            lock_token=state.lock_token,
        )
    except Exception:
        _agent_logger().exception("overflow_emergency_store_failed", session_id=state.session_id)
        return "抱歉，会话压缩过程中遇到错误。请开始新会话继续对话。"
    _apply_compaction_result(loop, state, result)
    final_status = _final_budget_status(loop, state, _iteration_messages(loop, state))
    if final_status.status == "compact_needed":
        _agent_logger().error(
            "emergency_trim_still_over_budget",
            tokens=final_status.current_tokens,
            session_id=state.session_id,
        )
        return _over_budget_text()
    return None


async def _handle_tool_calls(
    loop: AgentLoop,
    state: RequestState,
    iteration: int,
    collected_text: str,
    tool_calls_result: list[dict[str, str]],
    guard_state: GuardCheckResult,
) -> Any:
    await loop._session_manager.append_message(
        state.session_id, "assistant", collected_text,
        tool_calls=_tool_calls_payload(tool_calls_result),
        lock_token=state.lock_token,
    )
    action_ids = frozenset(state.procedure_action_map) if state.procedure_action_map else None
    groups = _build_execution_groups(tool_calls_result, loop._tool_registry, action_ids)
    for group in groups:
        for tc in group.tool_calls:
            parsed_args = _log_parse_result(tc)
            yield ToolCallInfo(tool_name=tc["name"], arguments=parsed_args, call_id=tc["id"])
        outcomes = await _execute_group(
            loop, state, group, guard_state,
            session_id=state.session_id, iteration=iteration,
        )
        for outcome in outcomes:
            if outcome.denied_event is not None:
                yield outcome.denied_event
            if outcome.failure_signal is not None:
                state.accumulated_failure_signals.append(outcome.failure_signal)
            await loop._session_manager.append_message(
                state.session_id, "tool", json.dumps(outcome.result),
                tool_call_id=outcome.tool_call["id"], lock_token=state.lock_token,
            )
    _agent_logger().info(
        "tool_call_iteration", iteration=iteration + 1,
        tools_called=len(tool_calls_result), session_id=state.session_id,
    )


async def _ensure_bootstrap(loop: AgentLoop) -> None:
    if loop._bootstrap_done or loop._evolution_engine is None:
        return
    try:
        await loop._evolution_engine.ensure_bootstrap(loop._workspace_dir)
    except Exception:
        _agent_logger().exception("soul_bootstrap_failed")
    loop._bootstrap_done = True


def _resolve_scope_key(
    loop: AgentLoop,
    session_id: str,
    identity: SessionIdentity | None,
    dm_scope: str | None,
) -> str:
    from src.agent import agent as agent_module

    effective_identity = identity or SessionIdentity(session_id=session_id, channel_type="dm")
    effective_dm_scope = dm_scope if dm_scope is not None else loop._session_settings.dm_scope
    return agent_module.resolve_scope_key(effective_identity, dm_scope=effective_dm_scope)


def _resolve_tools_schema(
    loop: AgentLoop,
    mode: Any,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    if not loop._tool_registry or not loop._tool_registry.list_tools(mode):
        return None, []
    tools_schema = loop._tool_registry.get_tools_schema(mode)
    return tools_schema, tools_schema or []


async def _load_compaction_state(loop: AgentLoop, session_id: str) -> tuple[int | None, str | None]:
    state = await loop._session_manager.get_compaction_state(session_id)
    if state is None:
        return None, None
    return state.last_compaction_seq, state.compacted_context


def _build_system_prompt(
    loop: AgentLoop,
    session_id: str,
    mode: Any,
    compacted_context: str | None,
    scope_key: str,
    recall_results: list[Any],
    skill_view: Any = None,
    procedure_view: Any = None,
) -> str:
    return loop._prompt_builder.build(
        session_id,
        mode,
        compacted_context=compacted_context,
        scope_key=scope_key,
        recall_results=recall_results,
        skill_view=skill_view,
        procedure_view=procedure_view,
    )


def _execution_context(messages: list[dict[str, Any]]) -> str:
    return "\n".join(message.get("content", "") for message in messages if message.get("content"))


async def _complete_assistant_response(
    loop: AgentLoop,
    state: RequestState,
    collected_text: str,
) -> None:
    await loop._session_manager.append_message(
        state.session_id,
        "assistant",
        collected_text,
        lock_token=state.lock_token,
    )
    _agent_logger().info(
        "response_complete",
        session_id=state.session_id,
        chars=len(collected_text),
    )
    terminal = "assistant_response"
    failure_signals = tuple(state.accumulated_failure_signals)
    # If all iterations had denials/failures, reflect that in terminal state
    if failure_signals and any(s.startswith("guard_denied:") for s in failure_signals):
        terminal = "guard_denied"
    elif failure_signals and any(s.startswith("tool_failure:") for s in failure_signals):
        terminal = "tool_failure"
    success = not failure_signals
    await _finalize_task_terminal(
        loop, state, terminal, success=success, failure_signals=failure_signals,
    )


def _over_budget_text() -> str:
    return "抱歉，当前会话内容过长，无法进一步压缩。请开始新会话继续对话。"


def _tool_calls_payload(tool_calls_result: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "id": tool_call["id"],
            "type": "function",
            "function": {
                "name": tool_call["name"],
                "arguments": tool_call["arguments"],
            },
        }
        for tool_call in tool_calls_result
    ]


def _log_parse_result(tool_call: dict[str, str]) -> dict:
    parsed_args, parse_error = _safe_parse_args(tool_call["arguments"])
    if parse_error:
        _agent_logger().warning(
            "tool_call_args_parse_failed",
            tool_name=tool_call["name"],
            error=parse_error,
            raw_args=tool_call["arguments"][:200],
        )
    return parsed_args


def _parse_stream_event(event: Any) -> StreamEventState:
    if isinstance(event, ContentDelta):
        return StreamEventState(text=event.text)
    if isinstance(event, ToolCallsComplete):
        return StreamEventState(tool_calls=event.tool_calls)
    return StreamEventState()


def _merge_tool_calls_result(
    current: list[dict[str, str]] | None,
    incoming: list[dict[str, str]] | None,
) -> list[dict[str, str]] | None:
    return incoming if incoming is not None else current


def _detect_teaching_intent(content: str) -> bool:
    """Lightweight rule: detect explicit user teaching intent."""
    teaching_signals = [
        "记住这个方法",
        "以后这类任务",
        "按这个做",
        "remember this",
        "from now on",
        "always do",
    ]
    lower = content.lower()
    return any(signal in lower for signal in teaching_signals)


async def _finalize_task_terminal(
    loop: AgentLoop,
    state: RequestState,
    terminal_state: str,
    *,
    success: bool = True,
    failure_signals: tuple[str, ...] = (),
) -> None:
    """Unified post-run-learning entry point.

    Called at task terminal states (assistant_response, max_iterations).
    Records outcome evidence for resolved skills, and logs teaching intent
    when detected (V1: log only, no automatic skill creation).
    """
    if loop._skill_learner is None:
        return
    from src.skills.types import TaskOutcome

    # Teaching intent means the user is teaching a NEW skill, not confirming
    # existing resolved skills.  Do NOT set user_confirmed for existing skills
    # based on teaching_intent — that would pollute evidence for unrelated skills.
    if state.resolved_skills:
        outcome = TaskOutcome(
            success=success,
            terminal_state=terminal_state,
            user_confirmed=False,
            failure_signals=failure_signals,
        )
        try:
            await loop._skill_learner.record_outcome(list(state.resolved_skills), outcome)
        except Exception:
            _agent_logger().exception("post_run_learning_failed", session_id=state.session_id)

    # Teaching intent → propose new skill via governance
    if state.teaching_intent:
        await _propose_taught_skill(loop, state)


async def _propose_taught_skill(loop: AgentLoop, state: RequestState) -> None:
    """Propose a new skill from teaching intent (V1).

    Extracts meaningful capability, activation_tags, and delta from the
    user message + task frame so the proposed skill can actually be resolved
    and projected in future turns.
    """
    from src.skills.types import SkillEvidence, SkillSpec

    _agent_logger().info(
        "teaching_intent_detected",
        session_id=state.session_id,
        has_resolved_skills=bool(state.resolved_skills),
    )
    if loop._skill_learner is None:
        return
    try:
        summary, capability, tags, delta = _extract_skill_draft_from_context(state)
        spec_draft = SkillSpec(
            id=f"user-taught-{uuid4().hex[:12]}",
            capability=capability,
            version=1,
            summary=summary,
            activation=f"Activate for {capability} tasks",
            activation_tags=tags,
            delta=delta,
        )
        evidence_draft = SkillEvidence(source="human-taught")
        await loop._skill_learner.propose_new_skill(
            spec_draft, evidence_draft, proposed_by="user",
        )
    except Exception:
        _agent_logger().exception(
            "teaching_skill_proposal_failed", session_id=state.session_id,
        )


def _extract_skill_draft_from_context(
    state: RequestState,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    """Extract (summary, capability, activation_tags, delta) from request state.

    V1 heuristic: derive capability from task_type, tags from task_type +
    content keywords, delta from the user's instruction text (stripped of
    the teaching signal prefix).
    """
    frame = state.task_frame
    task_type = frame.task_type.value if frame else "unknown"
    outcome = (frame.target_outcome or "") if frame else ""

    # Capability: use task_type as base, fall back to "general"
    capability = task_type if task_type != "unknown" else "general"

    # Summary: first 100 chars of target_outcome, or generic
    summary = outcome[:100].strip() if outcome else f"User-taught {capability} skill"

    # Tags: task_type + significant content words (top 5 by length, > 3 chars)
    tags_set: set[str] = {task_type}
    words = [w.lower() for w in outcome.split() if len(w) > 3]
    for word in sorted(set(words), key=len, reverse=True)[:5]:
        tags_set.add(word)
    tags = tuple(sorted(tags_set))

    # Delta: the user's instruction as a single reusable experience line
    delta_text = outcome.strip()
    delta = (delta_text,) if delta_text else ()

    return summary, capability, tags, delta


def _agent_logger():
    from src.agent import agent as agent_module

    return agent_module.logger
