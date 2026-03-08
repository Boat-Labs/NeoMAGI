from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog

from src.agent.compaction import CompactionEngine, CompactionResult
from src.agent.compaction_flow import emergency_trim as _emergency_trim_impl
from src.agent.compaction_flow import try_compact as _try_compact_impl
from src.agent.events import AgentEvent
from src.agent.guardrail import GuardCheckResult, load_contract
from src.agent.message_flow import handle_message as _handle_message_impl
from src.agent.model_client import ModelClient
from src.agent.prompt_builder import PromptBuilder
from src.agent.token_budget import BudgetTracker
from src.agent.tool_history import _messages_with_seq_to_openai as _messages_with_seq_to_openai
from src.agent.tool_history import _safe_parse_args as _safe_parse_args
from src.agent.tool_history import _sanitize_tool_call_history as _sanitize_tool_call_history
from src.agent.tool_runner import execute_tool as _execute_tool_impl
from src.config.settings import CompactionSettings, MemorySettings, SessionSettings
from src.memory.contracts import ResolvedFlushCandidate
from src.memory.evolution import EvolutionEngine
from src.memory.searcher import MemorySearcher
from src.memory.writer import MemoryWriter
from src.session.manager import SessionManager
from src.session.scope_resolver import SessionIdentity, resolve_scope_key
from src.tools.registry import ToolRegistry

logger = structlog.get_logger()

MAX_TOOL_ITERATIONS = 10

__all__ = [
    "AgentLoop",
    "MAX_TOOL_ITERATIONS",
    "_messages_with_seq_to_openai",
    "_safe_parse_args",
    "_sanitize_tool_call_history",
    "resolve_scope_key",
]


class AgentLoop:
    """Core agent loop with tool calling support."""

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
        memory_searcher: MemorySearcher | None = None,
        evolution_engine: EvolutionEngine | None = None,
    ) -> None:
        self._model_client = model_client
        self._session_manager = session_manager
        self._workspace_dir = workspace_dir
        self._memory_settings = memory_settings
        self._memory_searcher = memory_searcher
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
        self._evolution_engine = evolution_engine
        self._bootstrap_done = False
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
        self,
        session_id: str,
        content: str,
        *,
        lock_token: str | None = None,
        identity: SessionIdentity | None = None,
        dm_scope: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        async for event in _handle_message_impl(
            self,
            session_id,
            content,
            lock_token=lock_token,
            identity=identity,
            dm_scope=dm_scope,
            max_tool_iterations=MAX_TOOL_ITERATIONS,
        ):
            yield event

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
        scope_key: str,
    ) -> CompactionResult | None:
        return await _try_compact_impl(
            self,
            session_id=session_id,
            system_prompt=system_prompt,
            tools_schema_list=tools_schema_list,
            budget_status=budget_status,
            last_compaction_seq=last_compaction_seq,
            compacted_context=compacted_context,
            current_user_seq=current_user_seq,
            lock_token=lock_token,
            scope_key=scope_key,
        )

    def _emergency_trim(
        self,
        *,
        session_id: str,
        current_user_seq: int,
        min_preserved_turns_override: int | None = None,
    ) -> CompactionResult | None:
        return _emergency_trim_impl(
            self,
            session_id=session_id,
            current_user_seq=current_user_seq,
            min_preserved_turns_override=min_preserved_turns_override,
        )

    async def _fetch_memory_recall(
        self,
        session_id: str,
        *,
        scope_key: str,
    ) -> list:
        """Fetch memory recall results for prompt injection."""
        if not self._memory_searcher:
            return []
        try:
            all_messages = self._session_manager.get_history_with_seq(session_id)
            recent_user = [
                message.content
                for message in all_messages
                if message.role == "user" and message.content
            ][-3:]
            query = PromptBuilder.extract_recall_query(recent_user)
            if not query:
                return []
            max_results = (
                self._memory_settings.memory_recall_max_results if self._memory_settings else 5
            )
            min_score = (
                self._memory_settings.memory_recall_min_score if self._memory_settings else 1.0
            )
            results = await self._memory_searcher.search(
                query,
                scope_key=scope_key,
                limit=max_results,
                min_score=min_score,
            )
            if results:
                logger.info(
                    "memory_recall_fetched",
                    session_id=session_id,
                    query=query[:50],
                    results=len(results),
                )
            return results
        except Exception:
            logger.exception("memory_recall_fetch_failed", session_id=session_id)
            return []

    async def _persist_flush_candidates(
        self,
        candidates: list[Any],
        session_id: str,
        *,
        scope_key: str,
    ) -> None:
        """Persist flush candidates to daily notes via MemoryWriter."""
        try:
            resolved = [
                ResolvedFlushCandidate(
                    candidate_text=candidate.candidate_text,
                    scope_key=scope_key,
                    source_session_id=candidate.source_session_id,
                    confidence=candidate.confidence,
                    constraint_tags=tuple(candidate.constraint_tags),
                )
                for candidate in candidates
            ]
            min_confidence = (
                self._memory_settings.flush_min_confidence if self._memory_settings else 0.5
            )
            written = await self._memory_writer.process_flush_candidates(
                resolved,
                min_confidence=min_confidence,
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
        return await _execute_tool_impl(
            self,
            tool_name,
            arguments_json,
            scope_key=scope_key,
            session_id=session_id,
            guard_state=guard_state,
        )
