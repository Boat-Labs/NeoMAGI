"""Tests for builder work memory lifecycle (P2-M1c em2.2.2).

bd CLI calls are mocked to avoid real side effects.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.builder.types import BuilderTaskRecord
from src.builder.work_memory import (
    _run_bd_command,
    create_builder_task,
    link_artifact_to_bead,
    update_task_progress,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_bd_create(bead_id: str = "NeoMAGI-test") -> AsyncMock:
    """Return a mock that simulates successful bd create."""

    async def _side_effect(*args: str) -> tuple[bool, str]:
        if args and args[0] == "create":
            return True, json.dumps({"id": bead_id})
        # comments add
        if args and args[0] == "comments":
            return True, json.dumps({"id": 1})
        return True, "{}"

    mock = AsyncMock(side_effect=_side_effect)
    return mock


def _mock_bd_fail() -> AsyncMock:
    """Return a mock that simulates bd CLI unavailable."""

    async def _side_effect(*args: str) -> tuple[bool, str]:
        return False, "bd not found"

    return AsyncMock(side_effect=_side_effect)


# ---------------------------------------------------------------------------
# create_builder_task
# ---------------------------------------------------------------------------


class TestCreateBuilderTask:
    """create_builder_task should create artifact + best-effort bead."""

    @pytest.fixture()
    def base_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "artifacts"

    async def test_creates_artifact_file(self, base_dir: Path) -> None:
        with patch(
            "src.builder.work_memory._run_bd_command",
            new=_mock_bd_create(),
        ):
            record = await create_builder_task(
                task_brief="test task",
                scope="test",
                base_dir=base_dir,
            )
        assert record.artifact_id
        artifact_path = base_dir / "builder_runs" / f"{record.artifact_id}.md"
        assert artifact_path.exists()

    async def test_links_bead_id(self, base_dir: Path) -> None:
        with patch(
            "src.builder.work_memory._run_bd_command",
            new=_mock_bd_create("NeoMAGI-linked"),
        ):
            record = await create_builder_task(
                task_brief="linked task",
                scope="test",
                base_dir=base_dir,
            )
        assert record.bead_id == "NeoMAGI-linked"

    async def test_degrades_without_bd(self, base_dir: Path) -> None:
        with patch(
            "src.builder.work_memory._run_bd_command",
            new=_mock_bd_fail(),
        ):
            record = await create_builder_task(
                task_brief="no bd task",
                scope="test",
                base_dir=base_dir,
            )
        assert record.bead_id is None
        # Artifact should still be written
        artifact_path = base_dir / "builder_runs" / f"{record.artifact_id}.md"
        assert artifact_path.exists()

    async def test_passes_optional_fields(self, base_dir: Path) -> None:
        with patch(
            "src.builder.work_memory._run_bd_command",
            new=_mock_bd_create(),
        ):
            record = await create_builder_task(
                task_brief="full task",
                scope="builder",
                base_dir=base_dir,
                decision_snapshots=("snap1",),
                todo_items=("todo1", "todo2"),
                blockers=("blocker1",),
                artifact_refs=("ref1",),
            )
        assert record.decision_snapshots == ("snap1",)
        assert record.todo_items == ("todo1", "todo2")
        assert record.blockers == ("blocker1",)
        assert record.artifact_refs == ("ref1",)


# ---------------------------------------------------------------------------
# update_task_progress
# ---------------------------------------------------------------------------


class TestUpdateTaskProgress:
    """update_task_progress should re-render artifact + optional bead comment."""

    @pytest.fixture()
    def base_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "artifacts"
        d.mkdir()
        return d

    @pytest.fixture()
    def base_record(self) -> BuilderTaskRecord:
        return BuilderTaskRecord(
            artifact_id="update-test-id",
            bead_id="NeoMAGI-upd",
            task_brief="update task",
            scope="test",
        )

    async def test_updates_artifact_file(
        self, base_dir: Path, base_record: BuilderTaskRecord
    ) -> None:
        with patch(
            "src.builder.work_memory._run_bd_command",
            new=_mock_bd_create(),
        ):
            updated = await update_task_progress(
                base_record,
                base_dir,
                validation_summary="tests pass",
            )
        assert updated.validation_summary == "tests pass"
        # Original unchanged (frozen)
        assert base_record.validation_summary is None
        # File written
        artifact_path = base_dir / "builder_runs" / f"{updated.artifact_id}.md"
        assert artifact_path.exists()
        content = artifact_path.read_text()
        assert "tests pass" in content

    async def test_updates_multiple_fields(
        self, base_dir: Path, base_record: BuilderTaskRecord
    ) -> None:
        with patch(
            "src.builder.work_memory._run_bd_command",
            new=_mock_bd_create(),
        ):
            updated = await update_task_progress(
                base_record,
                base_dir,
                todo_items=("new todo",),
                blockers=("new blocker",),
                next_recommended_action="review",
            )
        assert updated.todo_items == ("new todo",)
        assert updated.blockers == ("new blocker",)
        assert updated.next_recommended_action == "review"

    async def test_no_update_no_bead_comment(
        self, base_dir: Path, base_record: BuilderTaskRecord
    ) -> None:
        mock = _mock_bd_create()
        with patch("src.builder.work_memory._run_bd_command", new=mock):
            await update_task_progress(base_record, base_dir)
        # No bd comments call since no fields updated
        for call in mock.call_args_list:
            args = call[0]
            assert args[0] != "comments"

    async def test_degrades_without_bead_id(self, base_dir: Path) -> None:
        record_no_bead = BuilderTaskRecord(
            artifact_id="no-bead-upd",
            task_brief="no bead",
            scope="test",
        )
        mock = _mock_bd_create()
        with patch("src.builder.work_memory._run_bd_command", new=mock):
            updated = await update_task_progress(
                record_no_bead,
                base_dir,
                validation_summary="ok",
            )
        assert updated.validation_summary == "ok"
        # No bd calls at all
        mock.assert_not_called()


# ---------------------------------------------------------------------------
# link_artifact_to_bead
# ---------------------------------------------------------------------------


class TestLinkArtifactToBead:
    """link_artifact_to_bead should add artifact ref as bead comment."""

    async def test_successful_link(self) -> None:
        mock = _mock_bd_create()
        with patch("src.builder.work_memory._run_bd_command", new=mock):
            await link_artifact_to_bead(
                "NeoMAGI-link",
                Path("workspace/artifacts/builder_runs/abc.md"),
            )
        mock.assert_called_once_with(
            "comments", "add", "NeoMAGI-link",
            "artifact: workspace/artifacts/builder_runs/abc.md",
        )

    async def test_failure_does_not_raise(self) -> None:
        mock = _mock_bd_fail()
        with patch("src.builder.work_memory._run_bd_command", new=mock):
            # Should not raise
            await link_artifact_to_bead(
                "NeoMAGI-fail",
                Path("workspace/artifacts/builder_runs/abc.md"),
            )


# ---------------------------------------------------------------------------
# _run_bd_command (internal, tested for coverage)
# ---------------------------------------------------------------------------


class TestRunBdCommand:
    """_run_bd_command should handle subprocess edge cases."""

    async def test_nonexistent_command(self) -> None:
        """If bd binary doesn't exist, should return failure gracefully."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("bd not found"),
        ):
            ok, output = await _run_bd_command("show", "x", "--json")
        assert not ok
        assert "not found" in output
