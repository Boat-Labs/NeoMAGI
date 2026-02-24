from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog

from src.agent.compaction import CompactionEngine, CompactionResult
from src.agent.events import AgentEvent, TextChunk, ToolCallInfo, ToolDenied
from src.agent.guardrail import (
    GuardCheckResult,
    check_pre_llm_guard,
    check_pre_tool_guard,
    load_contract,
    maybe_refresh_contract,
)
from src.agent.model_client import ContentDelta, ModelClient, ToolCallsComplete
from src.agent.prompt_builder import PromptBuilder
from src.agent.token_budget import BudgetTracker
from src.config.settings import CompactionSettings, MemorySettings, SessionSettings
from src.memory.contracts import ResolvedFlushCandidate
from src.memory.writer import MemoryWriter
from src.session.manager import MessageWithSeq, SessionManager
from src.session.scope_resolver import SessionIdentity, resolve_scope_key
from src.tools.context import ToolContext
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


def _messages_with_seq_to_openai(messages: list[MessageWithSeq]) -> list[dict[str, Any]]:
    """Convert MessageWithSeq list to OpenAI chat format dicts."""
    result: list[dict[str, Any]] = []
    for m in messages:
        msg_dict: dict[str, Any] = {"role": m.role, "content": m.content or ""}
        if m.tool_calls is not None:
            msg_dict["tool_calls"] = m.tool_calls
        if m.tool_call_id is not None:
            msg_dict["tool_call_id"] = m.tool_call_id
        result.append(msg_dict)
    return result


class AgentLoop:
    """Core agent loop with tool calling support.

    Flow: user msg → build prompt → budget check → [compact] → LLM →
          (tool_calls → execute → LLM)* → text response

    Compaction integration (Phase 3, ADR 0031/0032):
    - Budget check before each LLM call
    - Compact when budget_status == "compact_needed"
    - CompactionEngine owns flush generation (ADR 0032)
    - Watermark rebuild via get_effective_history (ADR 0031)
    - Reentry protection: max_compactions_per_request
    """

    def __init__(
        self,
        model_client: ModelClient,
        session_manager: SessionManager,
        workspace_dir: Path,
        model: str = "gpt-4o-mini",
        tool_registry: ToolRegistry | None = None,
        compaction_settings: CompactionSettings | None = None,
        session_settings: SessionSettings | None = None,
        memory_settings: MemorySettings | None = None,
    ) -> None:
        self._model_client = model_client
        self._session_manager = session_manager
        self._workspace_dir = workspace_dir
        self._memory_settings = memory_settings
        self._prompt_builder = PromptBuilder(
            workspace_dir,
            tool_registry=tool_registry,
            memory_settings=memory_settings,
        )
        self._tool_registry = tool_registry
        self._model = model
        self._settings = compaction_settings
        self._session_settings = session_settings or SessionSettings()
        self._budget_tracker: BudgetTracker | None = None
        self._compaction_engine: CompactionEngine | None = None
        self._memory_writer: MemoryWriter | None = None
        self._contract = load_contract(workspace_dir)
        if memory_settings is not None:
            self._memory_writer = MemoryWriter(workspace_dir, memory_settings)
        if compaction_settings is not None:
            self._budget_tracker = BudgetTracker(compaction_settings, model)
            self._compaction_engine = CompactionEngine(
                model_client,
                self._budget_tracker.counter,
                compaction_settings,
                workspace_dir=workspace_dir,
            )

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
        user_msg = await self._session_manager.append_message(
            session_id, "user", content, lock_token=lock_token
        )
        current_user_seq = user_msg.seq

        # Resolve scope_key ONCE as local variable (concurrency-safe, ADR 0034)
        identity = SessionIdentity(session_id=session_id, channel_type="dm")
        scope_key = resolve_scope_key(
            identity, dm_scope=self._session_settings.dm_scope
        )

        # Resolve effective mode (fail-closed to chat_safe on error)
        mode = await self._session_manager.get_mode(session_id)

        # 3. Get tools schema (mode-filtered)
        tools_schema = None
        tools_schema_list: list[dict] = []
        if self._tool_registry and self._tool_registry.list_tools(mode):
            tools_schema = self._tool_registry.get_tools_schema(mode)
            tools_schema_list = tools_schema or []

        # Compaction state tracking for this request
        compaction_count = 0
        max_compactions = (
            self._settings.max_compactions_per_request if self._settings else 2
        )

        # 2. Load compaction state and build initial prompt
        compaction_state = await self._session_manager.get_compaction_state(session_id)
        last_compaction_seq = (
            compaction_state.last_compaction_seq if compaction_state else None
        )
        compacted_context = (
            compaction_state.compacted_context if compaction_state else None
        )

        system_prompt = self._prompt_builder.build(
            session_id, mode, compacted_context=compacted_context,
            scope_key=scope_key,
        )

        # Lazily refresh guardrail contract (hash-based, ADR 0035)
        self._contract = maybe_refresh_contract(
            self._contract, self._workspace_dir
        )

        # 4. Streaming tool call loop
        for iteration in range(MAX_TOOL_ITERATIONS):
            # Build effective history via watermark (ADR 0031)
            effective_msgs = self._session_manager.get_effective_history(
                session_id, last_compaction_seq
            )
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                *_messages_with_seq_to_openai(effective_msgs),
            ]

            # Budget check + compaction trigger
            if self._budget_tracker is not None:
                total_tokens = self._budget_tracker.counter.count_messages(messages)
                if tools_schema_list:
                    total_tokens += self._budget_tracker.counter.count_tools_schema(
                        tools_schema_list
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

                # Trigger compaction if needed and allowed
                if (
                    budget_status.status == "compact_needed"
                    and self._compaction_engine is not None
                    and compaction_count < max_compactions
                    and lock_token is not None
                    and current_user_seq is not None
                ):
                    compact_result = await self._try_compact(
                        session_id=session_id,
                        system_prompt=system_prompt,
                        tools_schema_list=tools_schema_list,
                        budget_status=budget_status,
                        last_compaction_seq=last_compaction_seq,
                        compacted_context=compacted_context,
                        current_user_seq=current_user_seq,
                        lock_token=lock_token,
                    )
                    compaction_count += 1

                    if compact_result and compact_result.status != "noop":
                        # Update state with new watermark
                        last_compaction_seq = compact_result.new_compaction_seq
                        compacted_context = compact_result.compacted_context

                        # Rebuild system prompt with new compacted context
                        system_prompt = self._prompt_builder.build(
                            session_id, mode, compacted_context=compacted_context,
                            scope_key=scope_key,
                        )

                        # Rebuild effective history with new watermark
                        effective_msgs = self._session_manager.get_effective_history(
                            session_id, last_compaction_seq
                        )
                        messages = [
                            {"role": "system", "content": system_prompt},
                            *_messages_with_seq_to_openai(effective_msgs),
                        ]

                        # F2: Post-compaction budget recheck
                        rebuilt_tokens = (
                            self._budget_tracker.counter.count_messages(messages)
                        )
                        if tools_schema_list:
                            rebuilt_tokens += (
                                self._budget_tracker.counter.count_tools_schema(
                                    tools_schema_list
                                )
                            )
                        post_status = self._budget_tracker.check(rebuilt_tokens)

                        if post_status.status == "compact_needed":
                            # Overflow: try emergency trim with reduced preserved turns
                            original_turns = (
                                self._settings.min_preserved_turns
                            )
                            reduced_turns = max(original_turns // 2, 1)

                            logger.warning(
                                "post_compaction_still_over_budget",
                                original_preserved=original_turns,
                                reduced_preserved=reduced_turns,
                                tokens=rebuilt_tokens,
                            )
                            emergency_result = self._emergency_trim(
                                session_id=session_id,
                                current_user_seq=current_user_seq,
                                min_preserved_turns_override=reduced_turns,
                            )
                            if emergency_result is None:
                                # Cannot trim further → fail-open
                                logger.error(
                                    "emergency_trim_returned_none",
                                    session_id=session_id,
                                )
                                yield TextChunk(
                                    content="抱歉，当前会话内容过长，无法进一步压缩。"
                                    "请开始新会话继续对话。"
                                )
                                return

                            # Emergency trim succeeded → store + rebuild + recheck
                            try:
                                await self._session_manager.store_compaction_result(
                                    session_id,
                                    emergency_result,
                                    lock_token=lock_token,
                                )
                                last_compaction_seq = (
                                    emergency_result.new_compaction_seq
                                )
                                compacted_context = (
                                    emergency_result.compacted_context
                                )
                                system_prompt = self._prompt_builder.build(
                                    session_id,
                                    mode,
                                    compacted_context=compacted_context,
                                    scope_key=scope_key,
                                )
                                effective_msgs = (
                                    self._session_manager.get_effective_history(
                                        session_id, last_compaction_seq
                                    )
                                )
                                messages = [
                                    {"role": "system", "content": system_prompt},
                                    *_messages_with_seq_to_openai(effective_msgs),
                                ]
                                final_tokens = (
                                    self._budget_tracker.counter.count_messages(
                                        messages
                                    )
                                )
                                if tools_schema_list:
                                    final_tokens += (
                                        self._budget_tracker.counter
                                        .count_tools_schema(tools_schema_list)
                                    )
                                final_status = self._budget_tracker.check(
                                    final_tokens
                                )
                                if final_status.status == "compact_needed":
                                    logger.error(
                                        "emergency_trim_still_over_budget",
                                        tokens=final_tokens,
                                        session_id=session_id,
                                    )
                                    yield TextChunk(
                                        content="抱歉，当前会话内容过长，"
                                        "无法进一步压缩。"
                                        "请开始新会话继续对话。"
                                    )
                                    return
                            except Exception:
                                logger.exception(
                                    "overflow_emergency_store_failed",
                                    session_id=session_id,
                                )
                                yield TextChunk(
                                    content="抱歉，会话压缩过程中遇到错误。"
                                    "请开始新会话继续对话。"
                                )
                                return

            # Pre-LLM guard check (ADR 0035): detection only, no blocking
            execution_context = "\n".join(
                m.get("content", "") for m in messages if m.get("content")
            )
            guard_state = check_pre_llm_guard(self._contract, execution_context)

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
                        result = await self._execute_tool(
                            tc["name"],
                            tc["arguments"],
                            scope_key=scope_key,
                            session_id=session_id,
                            guard_state=guard_state,
                        )

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

    async def _try_compact(
        self,
        *,
        session_id: str,
        system_prompt: str,
        tools_schema_list: list[dict],
        budget_status: Any,
        last_compaction_seq: int | None,
        compacted_context: str | None,
        current_user_seq: int,
        lock_token: str,
    ) -> CompactionResult | None:
        """Execute compaction with full error handling.

        Returns CompactionResult on success/degraded, None on total failure.
        Emergency trim is applied when compaction fails entirely.
        """
        try:
            all_messages = self._session_manager.get_history_with_seq(session_id)
            result = await self._compaction_engine.compact(
                messages=all_messages,
                system_prompt=system_prompt,
                tools_schema=tools_schema_list,
                budget_status=budget_status,
                last_compaction_seq=last_compaction_seq,
                previous_compacted_context=compacted_context,
                current_user_seq=current_user_seq,
                model=self._model,
                session_id=session_id,
            )

            if result.status == "noop":
                logger.info(
                    "compaction_noop",
                    session_id=session_id,
                    last_compaction_seq=last_compaction_seq,
                )
                return result

            # Persist compaction result (success/degraded/failed)
            await self._session_manager.store_compaction_result(
                session_id, result, lock_token=lock_token
            )

            # Flush candidate persist (Phase 1, M3)
            if result.memory_flush_candidates and self._memory_writer:
                await self._persist_flush_candidates(
                    result.memory_flush_candidates, session_id
                )

            logger.info(
                "compaction_complete",
                session_id=session_id,
                status=result.status,
                new_compaction_seq=result.new_compaction_seq,
                flush_candidates=len(result.memory_flush_candidates),
            )
            return result

        except Exception:
            logger.exception("compaction_failed", session_id=session_id)

            # Emergency trim: force watermark to reduce context
            emergency_result = self._emergency_trim(
                session_id=session_id,
                current_user_seq=current_user_seq,
            )
            if emergency_result is not None:
                try:
                    await self._session_manager.store_compaction_result(
                        session_id, emergency_result, lock_token=lock_token
                    )
                    logger.warning(
                        "emergency_trim_applied",
                        session_id=session_id,
                        new_compaction_seq=emergency_result.new_compaction_seq,
                    )
                    return emergency_result
                except Exception:
                    logger.exception("emergency_trim_store_failed", session_id=session_id)

            return None

    def _emergency_trim(
        self,
        *,
        session_id: str,
        current_user_seq: int,
        min_preserved_turns_override: int | None = None,
    ) -> CompactionResult | None:
        """Emergency trim: force watermark forward to reduce context.

        Preserves only the most recent messages around current_user_seq.
        No summary is generated — this is a last resort.

        min_preserved_turns_override: per-call override, does NOT modify self._settings.
        """
        default_preserved = self._settings.min_preserved_turns if self._settings else 8
        min_preserved = min_preserved_turns_override or default_preserved
        all_msgs = self._session_manager.get_history_with_seq(session_id)
        if not all_msgs:
            return None

        # Find a watermark that leaves roughly min_preserved turns worth of messages
        # Each turn is approximately 2 messages (user + assistant)
        keep_count = min_preserved * 2
        if len(all_msgs) <= keep_count:
            return None

        trim_point = all_msgs[-(keep_count + 1)]
        new_seq = min(trim_point.seq, current_user_seq - 1)

        from datetime import UTC, datetime

        return CompactionResult(
            status="failed",
            compacted_context=None,
            compaction_metadata={
                "schema_version": 1,
                "status": "failed",
                "emergency_trim": True,
                "triggered_at": datetime.now(UTC).isoformat(),
                "preserved_count": min_preserved,
                "summarized_count": 0,
                "trimmed_count": len(all_msgs) - keep_count,
                "flush_skipped": True,
                "anchor_validation_passed": True,
                "anchor_retry_used": False,
                "compacted_context_tokens": 0,
                "rolling_summary_input_tokens": 0,
            },
            new_compaction_seq=new_seq,
        )

    async def _persist_flush_candidates(
        self,
        candidates: list[Any],
        session_id: str,
    ) -> None:
        """Persist flush candidates to daily notes via MemoryWriter.

        Boundary mapping: agent-layer MemoryFlushCandidate → memory-side
        ResolvedFlushCandidate. scope_key resolved per candidate using
        candidate.source_session_id (NOT current session_id).

        Failure is logged but does not block the main flow.
        """
        try:
            resolved = [
                ResolvedFlushCandidate(
                    candidate_text=c.candidate_text,
                    scope_key=resolve_scope_key(
                        SessionIdentity(session_id=c.source_session_id),
                        dm_scope=self._session_settings.dm_scope,
                    ),
                    source_session_id=c.source_session_id,
                    confidence=c.confidence,
                    constraint_tags=tuple(c.constraint_tags),
                )
                for c in candidates
            ]
            min_confidence = (
                self._memory_settings.flush_min_confidence
                if self._memory_settings
                else 0.5
            )
            written = await self._memory_writer.process_flush_candidates(
                resolved, min_confidence=min_confidence
            )
            logger.info(
                "memory_flush_persisted",
                count=written,
                total=len(candidates),
                session_id=session_id,
            )
        except Exception:
            logger.exception("memory_flush_persist_failed", session_id=session_id)

    async def _execute_tool(
        self,
        tool_name: str,
        arguments_json: str,
        *,
        scope_key: str,
        session_id: str,
        guard_state: GuardCheckResult,
    ) -> dict:
        """Execute a tool by name. Returns result dict or error dict.

        scope_key, session_id, guard_state are explicit parameters (NOT from
        self) for concurrency safety (ADR 0034/0035).
        """
        if not self._tool_registry:
            return {"error_code": "NO_REGISTRY", "message": "Tool registry not available"}

        tool = self._tool_registry.get(tool_name)
        if not tool:
            logger.warning("unknown_tool", tool_name=tool_name)
            return {"error_code": "UNKNOWN_TOOL", "message": f"Unknown tool: {tool_name}"}

        # Pre-tool guard check (ADR 0035)
        guard_block = check_pre_tool_guard(
            guard_state, tool_name, tool.risk_level
        )
        if guard_block is not None:
            return {
                "ok": False,
                "error_code": guard_block.error_code,
                "tool_name": tool_name,
                "message": guard_block.detail,
            }

        try:
            arguments = json.loads(arguments_json)
        except (json.JSONDecodeError, TypeError) as e:
            return {"error_code": "INVALID_ARGS", "message": f"Invalid JSON arguments: {e}"}
        if not isinstance(arguments, dict):
            return {
                "error_code": "INVALID_ARGS",
                "message": f"Expected dict arguments, got {type(arguments).__name__}",
            }

        context = ToolContext(scope_key=scope_key, session_id=session_id)

        try:
            result = await tool.execute(arguments, context)
            logger.info("tool_executed", tool_name=tool_name)
            return result
        except Exception:
            logger.exception("tool_execution_failed", tool_name=tool_name)
            return {"error_code": "EXECUTION_ERROR", "message": f"Tool {tool_name} failed"}
