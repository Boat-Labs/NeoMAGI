from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.agent.compaction import CompactionResult, split_turns

if TYPE_CHECKING:
    from src.agent.agent import AgentLoop


async def try_compact(
    loop: AgentLoop,
    *,
    session_id: str,
    system_prompt: str,
    tools_schema_list: list[dict],
    budget_status: Any,
    last_compaction_seq: int | None,
    compacted_context: str | None,
    current_user_seq: int,
    lock_token: str,
    scope_key: str,
) -> CompactionResult | None:
    """Execute compaction with full error handling."""
    try:
        result = await _run_compaction(
            loop,
            session_id=session_id,
            system_prompt=system_prompt,
            tools_schema_list=tools_schema_list,
            budget_status=budget_status,
            last_compaction_seq=last_compaction_seq,
            compacted_context=compacted_context,
            current_user_seq=current_user_seq,
        )
        if result.status == "noop":
            _agent_logger().info(
                "compaction_noop",
                session_id=session_id,
                last_compaction_seq=last_compaction_seq,
            )
            return result
        await loop._session_manager.store_compaction_result(
            session_id,
            result,
            lock_token=lock_token,
        )
        await _persist_flush_candidates(loop, result, session_id, scope_key)
        _agent_logger().info(
            "compaction_complete",
            session_id=session_id,
            status=result.status,
            new_compaction_seq=result.new_compaction_seq,
            flush_candidates=len(result.memory_flush_candidates),
        )
        return result
    except Exception:
        return await _recover_from_compaction_failure(
            loop,
            session_id=session_id,
            current_user_seq=current_user_seq,
            lock_token=lock_token,
        )


def emergency_trim(
    loop: AgentLoop,
    *,
    session_id: str,
    current_user_seq: int,
    min_preserved_turns_override: int | None = None,
) -> CompactionResult | None:
    """Force watermark forward to reduce context at turn boundaries."""
    min_preserved = _min_preserved_turns(loop, min_preserved_turns_override)
    all_messages = loop._session_manager.get_history_with_seq(session_id)
    if not all_messages:
        return None
    completed_turns = [
        turn for turn in split_turns(all_messages) if turn.start_seq < current_user_seq
    ]
    if len(completed_turns) <= min_preserved:
        return None
    trim_turns = completed_turns[:-min_preserved]
    if not trim_turns:
        return None
    new_seq = min(trim_turns[-1].end_seq, current_user_seq - 1)
    trimmed_count = sum(1 for message in all_messages if message.seq <= new_seq)
    preserved_turns_count = len(completed_turns) - len(trim_turns)
    return CompactionResult(
        status="failed",
        compacted_context=None,
        compaction_metadata=_emergency_trim_metadata(preserved_turns_count, trimmed_count),
        new_compaction_seq=new_seq,
    )


async def _run_compaction(
    loop: AgentLoop,
    *,
    session_id: str,
    system_prompt: str,
    tools_schema_list: list[dict],
    budget_status: Any,
    last_compaction_seq: int | None,
    compacted_context: str | None,
    current_user_seq: int,
) -> CompactionResult:
    all_messages = loop._session_manager.get_history_with_seq(session_id)
    return await loop._compaction_engine.compact(
        messages=all_messages,
        system_prompt=system_prompt,
        tools_schema=tools_schema_list,
        budget_status=budget_status,
        last_compaction_seq=last_compaction_seq,
        previous_compacted_context=compacted_context,
        current_user_seq=current_user_seq,
        model=loop._model,
        session_id=session_id,
    )


async def _recover_from_compaction_failure(
    loop: AgentLoop,
    *,
    session_id: str,
    current_user_seq: int,
    lock_token: str,
) -> CompactionResult | None:
    _agent_logger().exception("compaction_failed", session_id=session_id)
    result = loop._emergency_trim(session_id=session_id, current_user_seq=current_user_seq)
    if result is None:
        return None
    try:
        await loop._session_manager.store_compaction_result(
            session_id,
            result,
            lock_token=lock_token,
        )
    except Exception:
        _agent_logger().exception("emergency_trim_store_failed", session_id=session_id)
        return None
    _agent_logger().warning(
        "emergency_trim_applied",
        session_id=session_id,
        new_compaction_seq=result.new_compaction_seq,
    )
    return result


async def _persist_flush_candidates(
    loop: AgentLoop,
    result: CompactionResult,
    session_id: str,
    scope_key: str,
) -> None:
    if result.memory_flush_candidates and loop._memory_writer:
        await loop._persist_flush_candidates(
            result.memory_flush_candidates,
            session_id,
            scope_key=scope_key,
        )


def _min_preserved_turns(loop: AgentLoop, override: int | None) -> int:
    default_preserved = loop._settings.min_preserved_turns if loop._settings else 8
    return override or default_preserved


def _emergency_trim_metadata(preserved_turns_count: int, trimmed_count: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "failed",
        "emergency_trim": True,
        "triggered_at": datetime.now(UTC).isoformat(),
        "preserved_count": preserved_turns_count,
        "summarized_count": 0,
        "trimmed_count": trimmed_count,
        "flush_skipped": True,
        "anchor_validation_passed": True,
        "anchor_retry_used": False,
        "compacted_context_tokens": 0,
        "rolling_summary_input_tokens": 0,
    }


def _agent_logger():
    from src.agent import agent as agent_module

    return agent_module.logger
