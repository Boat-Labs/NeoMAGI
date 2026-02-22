"""Compaction engine: rolling summary + anchor preservation + flush generation.

Memory flush is generated exclusively by this module (ADR 0032).
AgentLoop only orchestrates — it MUST NOT call MemoryFlushGenerator directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

from src.agent.memory_flush import MemoryFlushCandidate, MemoryFlushGenerator
from src.agent.token_budget import BudgetStatus, TokenCounter
from src.session.manager import MessageWithSeq

if TYPE_CHECKING:
    from src.agent.model_client import ModelClient
    from src.config.settings import CompactionSettings

logger = structlog.get_logger()

# Prompt template for rolling summary generation (ADR 0028)
_SUMMARY_PROMPT = """\
You are a conversation compactor. Produce a structured JSON summary of the conversation below.

Previous summary (if any):
{previous_summary}

Conversation to compress:
{conversation}

Output a JSON object with exactly these keys:
- "facts": list of confirmed facts
- "decisions": list of decisions made
- "open_todos": list of unfinished items
- "user_prefs": list of user preference declarations
- "timeline": list of key events with timestamps or order

Rules:
- Be concise. Each item should be one sentence.
- Preserve information critical for task continuity.
- Do NOT include casual greetings or acknowledgments.
- Output ONLY the JSON object, no markdown fencing.
- Total output must be within {max_output_tokens} tokens.
"""


@dataclass
class Turn:
    """A conversation turn: user message + all subsequent assistant/tool messages."""

    start_seq: int
    end_seq: int
    messages: list[MessageWithSeq]


@dataclass
class CompactionResult:
    """Result of a compaction operation."""

    status: Literal["success", "degraded", "failed", "noop"]
    compacted_context: str | None = None
    compaction_metadata: dict = field(default_factory=dict)
    new_compaction_seq: int = 0
    memory_flush_candidates: list[MemoryFlushCandidate] = field(default_factory=list)
    preserved_messages: list[MessageWithSeq] = field(default_factory=list)


def split_turns(messages: list[MessageWithSeq]) -> list[Turn]:
    """Split messages into turns by user-message boundaries.

    A turn starts at each 'user' role message and includes all subsequent
    assistant/tool messages until the next user message.
    """
    if not messages:
        return []

    turns: list[Turn] = []
    current_msgs: list[MessageWithSeq] = []

    for msg in messages:
        if msg.role == "user" and current_msgs:
            # End previous turn, start new one
            turns.append(Turn(
                start_seq=current_msgs[0].seq,
                end_seq=current_msgs[-1].seq,
                messages=current_msgs,
            ))
            current_msgs = []
        current_msgs.append(msg)

    # Last turn
    if current_msgs:
        turns.append(Turn(
            start_seq=current_msgs[0].seq,
            end_seq=current_msgs[-1].seq,
            messages=current_msgs,
        ))

    return turns


class CompactionEngine:
    """Core compaction logic: rolling summary + anchor preservation + flush generation.

    Memory flush is generated exclusively by this module (ADR 0032).
    AgentLoop only orchestrates.
    """

    def __init__(
        self,
        model_client: ModelClient,
        token_counter: TokenCounter,
        settings: CompactionSettings,
        workspace_dir: Path | None = None,
    ) -> None:
        self._model_client = model_client
        self._counter = token_counter
        self._settings = settings
        self._workspace_dir = workspace_dir
        self._flush_generator = MemoryFlushGenerator(settings)

    async def compact(
        self,
        messages: list[MessageWithSeq],
        system_prompt: str,
        tools_schema: list[dict],
        budget_status: BudgetStatus,
        last_compaction_seq: int | None,
        previous_compacted_context: str | None,
        current_user_seq: int,
        model: str,
        session_id: str = "",
    ) -> CompactionResult:
        """Execute compaction pipeline.

        Steps:
        1. Split messages into turns
        2. Exclude current unfinished turn (seq >= current_user_seq)
        3. Identify compressible range (after last_compaction_seq, before preserved zone)
        4. If no compressible range -> return noop
        5. Generate memory flush candidates from compressible turns
        6. Build rolling summary via LLM
        7. Anchor visibility validation (ADR 0030)
        8. Return CompactionResult
        """
        all_turns = split_turns(messages)

        if not all_turns:
            return CompactionResult(
                status="noop",
                new_compaction_seq=last_compaction_seq or 0,
                compaction_metadata=self._make_metadata("noop"),
            )

        # Step 2: Exclude current unfinished turn
        completed_turns = [t for t in all_turns if t.start_seq < current_user_seq]
        if not completed_turns:
            return CompactionResult(
                status="noop",
                new_compaction_seq=last_compaction_seq or 0,
                compaction_metadata=self._make_metadata("noop"),
            )

        # Step 3: Identify preserved and compressible zones
        min_preserved = self._settings.min_preserved_turns
        if len(completed_turns) <= min_preserved:
            return CompactionResult(
                status="noop",
                new_compaction_seq=last_compaction_seq or 0,
                compaction_metadata=self._make_metadata("noop"),
                preserved_messages=[
                    m for t in completed_turns for m in t.messages
                ],
            )

        preserved_turns = completed_turns[-min_preserved:]
        compressible_turns = completed_turns[:-min_preserved]

        # Filter out already-compacted turns
        if last_compaction_seq is not None:
            compressible_turns = [
                t for t in compressible_turns if t.end_seq > last_compaction_seq
            ]

        if not compressible_turns:
            return CompactionResult(
                status="noop",
                new_compaction_seq=last_compaction_seq or 0,
                compaction_metadata=self._make_metadata("noop"),
                preserved_messages=[
                    m for t in preserved_turns for m in t.messages
                ],
            )

        # New watermark: end_seq of last compressible turn
        new_compaction_seq = compressible_turns[-1].end_seq
        # Invariant: must not exceed current_user_seq - 1
        new_compaction_seq = min(new_compaction_seq, current_user_seq - 1)

        # Step 5: Memory flush (with timeout protection)
        flush_candidates: list[MemoryFlushCandidate] = []
        flush_skipped = False
        try:
            flush_candidates = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    self._flush_generator.generate,
                    compressible_turns,
                    session_id,
                ),
                timeout=self._settings.flush_timeout_s,
            )
        except (TimeoutError, Exception):
            logger.warning("flush_timeout_or_error", session_id=session_id)
            flush_skipped = True

        # Step 6: Rolling summary via LLM
        conversation_text = self._turns_to_text(compressible_turns)
        input_tokens = self._counter.count_text(conversation_text)
        max_summary_tokens = int(input_tokens * 0.3)

        # Input too small for meaningful summary → degraded (trim-only, watermark advances)
        if max_summary_tokens < 100:
            logger.info(
                "input_too_small_for_summary",
                input_tokens=input_tokens,
                max_summary_tokens=max_summary_tokens,
            )
            return CompactionResult(
                status="degraded",
                compacted_context=previous_compacted_context,
                compaction_metadata=self._make_metadata(
                    "degraded",
                    preserved_count=len(preserved_turns),
                    summarized_count=len(compressible_turns),
                    flush_skipped=flush_skipped,
                ),
                new_compaction_seq=new_compaction_seq,
                memory_flush_candidates=flush_candidates,
                preserved_messages=[m for t in preserved_turns for m in t.messages],
            )

        summary_text: str | None = None
        anchor_retry_used = False
        status: Literal["success", "degraded", "failed", "noop"] = "success"

        try:
            summary_text = await asyncio.wait_for(
                self._generate_summary(
                    previous_compacted_context,
                    conversation_text,
                    max_summary_tokens,
                    model,
                ),
                timeout=self._settings.compact_timeout_s,
            )
        except (TimeoutError, Exception) as e:
            logger.warning("compaction_llm_failed", error=str(e), session_id=session_id)
            status = "degraded"

        # Step 7: Anchor visibility validation (ADR 0030)
        # effective_history_text = preserved turns text (approximation of what the
        # model will see alongside system_prompt + compacted_context)
        preserved_text = self._turns_to_text(preserved_turns)
        anchor_passed = True
        if summary_text and status == "success":
            anchor_passed = self._validate_anchors(
                system_prompt, summary_text, preserved_text
            )
            if not anchor_passed and self._settings.anchor_retry_enabled:
                anchor_retry_used = True
                logger.info("anchor_retry", session_id=session_id)
                try:
                    summary_text = await asyncio.wait_for(
                        self._generate_summary(
                            previous_compacted_context,
                            conversation_text,
                            max_summary_tokens,
                            model,
                        ),
                        timeout=self._settings.compact_timeout_s,
                    )
                    anchor_passed = self._validate_anchors(
                        system_prompt, summary_text, preserved_text
                    )
                except (TimeoutError, Exception):
                    anchor_passed = False

                if not anchor_passed:
                    status = "degraded"
                    logger.warning(
                        "anchor_validation_failed_after_retry", session_id=session_id
                    )

        # Build metadata
        metadata = self._make_metadata(
            status,
            preserved_count=len(preserved_turns),
            summarized_count=len(compressible_turns),
            flush_skipped=flush_skipped,
            anchor_validation_passed=anchor_passed,
            anchor_retry_used=anchor_retry_used,
            compacted_context_tokens=(
                self._counter.count_text(summary_text) if summary_text else 0
            ),
            rolling_summary_input_tokens=input_tokens,
        )

        return CompactionResult(
            status=status,
            compacted_context=summary_text,
            compaction_metadata=metadata,
            new_compaction_seq=new_compaction_seq,
            memory_flush_candidates=flush_candidates,
            preserved_messages=[m for t in preserved_turns for m in t.messages],
        )

    async def _generate_summary(
        self,
        previous_context: str | None,
        conversation_text: str,
        max_output_tokens: int,
        model: str,
    ) -> str:
        """Generate rolling summary via LLM call."""
        prompt = _SUMMARY_PROMPT.format(
            previous_summary=previous_context or "(none)",
            conversation=conversation_text,
            max_output_tokens=max_output_tokens,
        )

        messages = [
            {"role": "system", "content": "You are a precise conversation summarizer."},
            {"role": "user", "content": prompt},
        ]

        response = await self._model_client.chat(
            messages, model, temperature=self._settings.summary_temperature
        )
        return response.strip()

    # Anchor files: first non-empty line used as probe (ADR 0030)
    _ANCHOR_FILES = ("AGENTS.md", "SOUL.md", "USER.md")

    def _extract_anchor_phrases(self) -> list[str]:
        """Extract first non-empty line from each workspace anchor file."""
        if self._workspace_dir is None:
            return []
        anchors: list[str] = []
        for filename in self._ANCHOR_FILES:
            filepath = self._workspace_dir / filename
            if not filepath.exists():
                continue
            try:
                text = filepath.read_text(encoding="utf-8")
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped:
                        anchors.append(stripped)
                        break
            except OSError:
                logger.warning("anchor_file_read_error", file=filename)
        return anchors

    def _validate_anchors(
        self,
        system_prompt: str,
        compacted_context: str | None,
        effective_history_text: str = "",
    ) -> bool:
        """Validate anchor visibility in final model context (ADR 0030).

        Checks that first non-empty line from AGENTS/SOUL/USER files
        is present in the final context sent to the model.
        """
        if not system_prompt:
            return False

        final_context = system_prompt + (compacted_context or "") + effective_history_text

        anchor_phrases = self._extract_anchor_phrases()
        if not anchor_phrases:
            # No anchors to validate → pass (don't block compaction)
            return True

        for phrase in anchor_phrases:
            if phrase not in final_context:
                logger.warning("anchor_missing", phrase=phrase[:80])
                return False

        return True

    def _turns_to_text(self, turns: list[Turn]) -> str:
        """Convert turns to plain text for summary input."""
        lines: list[str] = []
        for turn in turns:
            for msg in turn.messages:
                content = msg.content or ""
                if content:
                    lines.append(f"[{msg.role}]: {content}")
        return "\n".join(lines)

    def _make_metadata(
        self,
        status: str,
        preserved_count: int = 0,
        summarized_count: int = 0,
        trimmed_count: int = 0,
        flush_skipped: bool = False,
        anchor_validation_passed: bool = True,
        anchor_retry_used: bool = False,
        compacted_context_tokens: int = 0,
        rolling_summary_input_tokens: int = 0,
    ) -> dict:
        return {
            "schema_version": 1,
            "status": status,
            "preserved_count": preserved_count,
            "summarized_count": summarized_count,
            "trimmed_count": trimmed_count,
            "flush_skipped": flush_skipped,
            "anchor_validation_passed": anchor_validation_passed,
            "anchor_retry_used": anchor_retry_used,
            "triggered_at": datetime.now(UTC).isoformat(),
            "compacted_context_tokens": compacted_context_tokens,
            "rolling_summary_input_tokens": rolling_summary_input_tokens,
        }
