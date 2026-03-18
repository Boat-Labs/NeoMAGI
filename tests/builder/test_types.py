"""Tests for BuilderTaskRecord type (P2-M1c em2.2.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.builder.types import BuilderTaskRecord


class TestBuilderTaskRecordConstruction:
    """BuilderTaskRecord should construct with required + optional fields."""

    def test_minimal_construction(self) -> None:
        record = BuilderTaskRecord(
            artifact_id="abc-123",
            task_brief="implement feature X",
            scope="builder",
        )
        assert record.artifact_id == "abc-123"
        assert record.task_brief == "implement feature X"
        assert record.scope == "builder"
        assert record.bead_id is None
        assert record.decision_snapshots == ()
        assert record.todo_items == ()
        assert record.blockers == ()
        assert record.artifact_refs == ()
        assert record.validation_summary is None
        assert record.promote_candidates == ()
        assert record.next_recommended_action is None

    def test_full_construction(self) -> None:
        record = BuilderTaskRecord(
            artifact_id="def-456",
            bead_id="NeoMAGI-xyz",
            task_brief="build memory module",
            scope="memory",
            decision_snapshots=("chose PostgreSQL",),
            todo_items=("write tests", "add migrations"),
            blockers=("pending review",),
            artifact_refs=("workspace/artifacts/builder_runs/def-456.md",),
            validation_summary="all tests pass",
            promote_candidates=("skill:memory-search",),
            next_recommended_action="submit for review",
        )
        assert record.bead_id == "NeoMAGI-xyz"
        assert len(record.decision_snapshots) == 1
        assert len(record.todo_items) == 2
        assert len(record.blockers) == 1
        assert record.validation_summary == "all tests pass"
        assert len(record.promote_candidates) == 1
        assert record.next_recommended_action == "submit for review"


class TestBuilderTaskRecordFrozen:
    """BuilderTaskRecord must be immutable (frozen=True)."""

    def test_cannot_mutate_field(self) -> None:
        record = BuilderTaskRecord(
            artifact_id="abc-123",
            task_brief="task",
            scope="test",
        )
        with pytest.raises(ValidationError):
            record.task_brief = "changed"  # type: ignore[misc]

    def test_cannot_mutate_optional_field(self) -> None:
        record = BuilderTaskRecord(
            artifact_id="abc-123",
            task_brief="task",
            scope="test",
        )
        with pytest.raises(ValidationError):
            record.bead_id = "new-id"  # type: ignore[misc]


class TestBuilderTaskRecordValidation:
    """BuilderTaskRecord field validation."""

    def test_missing_required_artifact_id(self) -> None:
        with pytest.raises(ValidationError):
            BuilderTaskRecord(task_brief="task", scope="test")  # type: ignore[call-arg]

    def test_missing_required_task_brief(self) -> None:
        with pytest.raises(ValidationError):
            BuilderTaskRecord(artifact_id="id", scope="test")  # type: ignore[call-arg]

    def test_missing_required_scope(self) -> None:
        with pytest.raises(ValidationError):
            BuilderTaskRecord(artifact_id="id", task_brief="task")  # type: ignore[call-arg]

    def test_model_copy_produces_new_record(self) -> None:
        original = BuilderTaskRecord(
            artifact_id="abc-123",
            task_brief="original",
            scope="test",
        )
        updated = original.model_copy(update={"task_brief": "updated"})
        assert updated.task_brief == "updated"
        assert original.task_brief == "original"
        assert updated.artifact_id == original.artifact_id
