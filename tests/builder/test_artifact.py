"""Tests for artifact generation, rendering, and persistence (P2-M1c em2.2.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.builder.artifact import (
    generate_artifact_id,
    render_artifact_markdown,
    write_artifact,
)
from src.builder.types import BuilderTaskRecord


class TestGenerateArtifactId:
    """generate_artifact_id should produce valid unique IDs."""

    def test_returns_string(self) -> None:
        aid = generate_artifact_id()
        assert isinstance(aid, str)
        assert len(aid) > 0

    def test_unique_ids(self) -> None:
        ids = {generate_artifact_id() for _ in range(100)}
        assert len(ids) == 100

    def test_uuid_format(self) -> None:
        """ID should be a valid UUID-4 string (V1 fallback)."""
        import uuid

        aid = generate_artifact_id()
        parsed = uuid.UUID(aid)
        assert parsed.version == 4


class TestRenderArtifactMarkdown:
    """render_artifact_markdown should produce well-formed markdown."""

    def test_minimal_record(self) -> None:
        record = BuilderTaskRecord(
            artifact_id="test-id-123",
            task_brief="test task",
            scope="test",
        )
        md = render_artifact_markdown(record)
        assert "# Builder Task: test task" in md
        assert "`test-id-123`" in md
        assert "**scope**: test" in md
        # Optional sections should not appear
        assert "## Decision Snapshots" not in md
        assert "## TODO Items" not in md
        assert "## Blockers" not in md

    def test_full_record(self) -> None:
        record = BuilderTaskRecord(
            artifact_id="full-id",
            bead_id="NeoMAGI-abc",
            task_brief="full task",
            scope="builder",
            decision_snapshots=("decision A", "decision B"),
            todo_items=("item 1", "item 2"),
            blockers=("blocker X",),
            artifact_refs=("ref/path.md",),
            validation_summary="all green",
            promote_candidates=("skill:foo",),
            next_recommended_action="deploy",
        )
        md = render_artifact_markdown(record)
        assert "# Builder Task: full task" in md
        assert "`NeoMAGI-abc`" in md
        assert "## Decision Snapshots" in md
        assert "- decision A" in md
        assert "- decision B" in md
        assert "## TODO Items" in md
        assert "- [ ] item 1" in md
        assert "- [ ] item 2" in md
        assert "## Blockers" in md
        assert "- blocker X" in md
        assert "## Artifact References" in md
        assert "- ref/path.md" in md
        assert "## Validation Summary" in md
        assert "all green" in md
        assert "## Promote Candidates" in md
        assert "- skill:foo" in md
        assert "## Next Recommended Action" in md
        assert "deploy" in md

    def test_bead_id_omitted_when_none(self) -> None:
        record = BuilderTaskRecord(
            artifact_id="no-bead",
            task_brief="task",
            scope="test",
        )
        md = render_artifact_markdown(record)
        assert "bead_id" not in md


class TestWriteArtifact:
    """write_artifact should persist markdown to the correct path."""

    @pytest.fixture()
    def artifact_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "artifacts"

    async def test_creates_file(self, artifact_dir: Path) -> None:
        record = BuilderTaskRecord(
            artifact_id="write-test-id",
            task_brief="write test",
            scope="test",
        )
        path = await write_artifact(record, artifact_dir)
        assert path.exists()
        assert path.name == "write-test-id.md"
        assert path.parent.name == "builder_runs"

    async def test_file_content_matches_render(self, artifact_dir: Path) -> None:
        record = BuilderTaskRecord(
            artifact_id="content-test",
            task_brief="content check",
            scope="test",
            todo_items=("verify content",),
        )
        path = await write_artifact(record, artifact_dir)
        content = path.read_text(encoding="utf-8")
        expected = render_artifact_markdown(record)
        assert content == expected

    async def test_creates_parent_directories(self, tmp_path: Path) -> None:
        deep_dir = tmp_path / "a" / "b" / "c"
        record = BuilderTaskRecord(
            artifact_id="deep-id",
            task_brief="deep",
            scope="test",
        )
        path = await write_artifact(record, deep_dir)
        assert path.exists()

    async def test_overwrites_existing(self, artifact_dir: Path) -> None:
        record_v1 = BuilderTaskRecord(
            artifact_id="overwrite-id",
            task_brief="version 1",
            scope="test",
        )
        record_v2 = BuilderTaskRecord(
            artifact_id="overwrite-id",
            task_brief="version 2",
            scope="test",
        )
        await write_artifact(record_v1, artifact_dir)
        path = await write_artifact(record_v2, artifact_dir)
        content = path.read_text(encoding="utf-8")
        assert "version 2" in content
        assert "version 1" not in content
