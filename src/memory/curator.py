"""Memory curator: consolidate daily notes into MEMORY.md.

Reviews recent daily notes, identifies lasting patterns, and updates
MEMORY.md with curated knowledge. LLM-assisted for pattern recognition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.agent.model_client import ModelClient
    from src.config.settings import MemorySettings
    from src.memory.indexer import MemoryIndexer

logger = structlog.get_logger()

CURATION_SYSTEM_PROMPT = """You are a memory curator for a personal AI assistant.

Your task: review recent daily notes and the current MEMORY.md, then propose updates.

Rules:
1. Only add HIGH-CONFIDENCE patterns confirmed across multiple entries.
2. Remove outdated or contradicted information.
3. Keep MEMORY.md concise — prefer fewer, higher-quality entries.
4. Use markdown ## headers to organize sections.
5. Return ONLY the updated MEMORY.md content, nothing else.
6. If no changes are needed, return the current content unchanged.
"""


@dataclass
class CurationProposal:
    """Proposed changes to MEMORY.md."""

    new_content: str
    additions: list[str] = field(default_factory=list)
    removals: list[str] = field(default_factory=list)


@dataclass
class CurationResult:
    """Result of a curation pass."""

    status: str  # "updated" | "no_changes" | "error"
    additions_count: int = 0
    removals_count: int = 0
    new_content: str | None = None


class MemoryCurator:
    """Review daily notes and update MEMORY.md with lasting knowledge.

    Workflow:
    1. Read recent daily notes (past N days)
    2. Read current MEMORY.md content
    3. LLM proposes updates (additions + removals)
    4. Apply updates to MEMORY.md
    5. Reindex MEMORY.md via MemoryIndexer
    """

    def __init__(
        self,
        model_client: ModelClient,
        settings: MemorySettings,
        indexer: MemoryIndexer | None = None,
    ) -> None:
        self._model_client = model_client
        self._settings = settings
        self._indexer = indexer

    async def curate(
        self,
        workspace_path: Path,
        *,
        scope_key: str = "main",
        lookback_days: int | None = None,
    ) -> CurationResult:
        """Execute curation pass. Returns summary of changes."""
        days = lookback_days or self._settings.curation_lookback_days

        # 1. Read recent daily notes
        daily_content = self._read_recent_daily_notes(workspace_path, days=days)
        if not daily_content:
            logger.info("curation_skipped_no_notes", workspace=str(workspace_path))
            return CurationResult(status="no_changes")

        # 2. Read current MEMORY.md
        memory_md_path = workspace_path / "MEMORY.md"
        current_curated = ""
        if memory_md_path.is_file():
            current_curated = memory_md_path.read_text(encoding="utf-8").strip()

        # 3. Generate proposal via LLM
        proposal = await self.propose_updates(daily_content, current_curated)

        # 4. Check if content changed
        if proposal.new_content.strip() == current_curated.strip():
            logger.info("curation_no_changes")
            return CurationResult(status="no_changes")

        # 5. Enforce size limit
        max_chars = self._settings.curated_max_tokens * 4
        new_content = proposal.new_content
        if len(new_content) > max_chars:
            new_content = new_content[:max_chars]
            logger.warning("curation_truncated", max_chars=max_chars)

        # 6. Write updated MEMORY.md
        memory_md_path.write_text(new_content + "\n", encoding="utf-8")

        # 7. Reindex if indexer available
        if self._indexer:
            try:
                await self._indexer.index_curated_memory(
                    memory_md_path, scope_key=scope_key
                )
            except Exception:
                logger.exception("curation_reindex_failed")

        result = CurationResult(
            status="updated",
            additions_count=len(proposal.additions),
            removals_count=len(proposal.removals),
            new_content=new_content,
        )
        logger.info(
            "curation_complete",
            additions=result.additions_count,
            removals=result.removals_count,
        )
        return result

    async def propose_updates(
        self,
        daily_content: str,
        current_curated: str,
    ) -> CurationProposal:
        """Generate update proposal via LLM.

        Uses low temperature for factual accuracy.
        """
        user_prompt = (
            f"## Current MEMORY.md\n\n{current_curated or '(empty)'}\n\n"
            f"## Recent Daily Notes\n\n{daily_content}\n\n"
            "Based on the daily notes above, produce the updated MEMORY.md content. "
            "Add confirmed patterns and remove outdated information."
        )

        messages = [
            {"role": "system", "content": CURATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        response_text = ""
        async for event in self._model_client.chat_stream_with_tools(
            messages,
            model="gpt-4o-mini",
            temperature=self._settings.curation_temperature,
        ):
            from src.agent.model_client import ContentDelta

            if isinstance(event, ContentDelta):
                response_text += event.text

        return CurationProposal(new_content=response_text.strip())

    @staticmethod
    def _read_recent_daily_notes(
        workspace_path: Path,
        *,
        days: int = 7,
    ) -> str:
        """Read daily notes from the past N days."""
        memory_dir = workspace_path / "memory"
        if not memory_dir.is_dir():
            return ""

        today = date.today()
        parts: list[str] = []

        for offset in range(days):
            target_date = today - timedelta(days=offset)
            filepath = memory_dir / f"{target_date.isoformat()}.md"
            if not filepath.is_file():
                continue
            try:
                content = filepath.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"=== {target_date.isoformat()} ===\n{content}")
            except OSError:
                logger.exception("curator_daily_note_read_error", path=str(filepath))

        return "\n\n".join(parts)
