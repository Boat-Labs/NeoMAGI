from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from scripts.devcoord import coord as coord_module
from tests.devcoord_helpers import (
    CoordRecord,
    CoordService,
    FakeClock,
    MemoryCoordStore,
    init_git_repo_with_review,
    make_paths,
    run_cli,
)


def test_init_creates_milestone_and_agent_records(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)

    exit_code = run_cli(
        [
            "init",
            "--milestone",
            "M7",
            "--run-date",
            "2026-03-01",
        ],
        store=store,
        paths=paths,
    )

    assert exit_code == 0
    issues = store.list_records("m7")
    milestone_issues = [
        issue for issue in issues if issue.metadata.get("coord_kind") == "milestone"
    ]
    agent_issues = [issue for issue in issues if issue.metadata.get("coord_kind") == "agent"]

    assert len(milestone_issues) == 1
    assert milestone_issues[0].metadata["milestone"] == "m7"
    assert milestone_issues[0].metadata["run_date"] == "2026-03-01"
    assert {issue.metadata["role"] for issue in agent_issues} == {"pm", "backend", "tester"}


def test_apply_payload_file_executes_open_gate(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    init_payload_path = tmp_path / "init.json"
    init_payload_path.write_text(
        json.dumps(
            {
                "milestone": "M7",
                "run_date": "2026-03-01",
                "roles": ["pm", "backend", "tester"],
            }
        ),
        "utf-8",
    )

    run_cli(
        [
            "apply",
            "init",
            "--payload-file",
            str(init_payload_path),
        ],
        store=store,
        paths=paths,
    )

    payload_path = tmp_path / "open_gate.json"
    payload_path.write_text(
        json.dumps(
            {
                "milestone": "M7",
                "phase": "1",
                "gate_id": "G-M7-P1",
                "allowed_role": "backend",
                "target_commit": "abc1234",
                "task": "open backend phase 1 gate",
            }
        ),
        "utf-8",
    )

    exit_code = run_cli(
        [
            "apply",
            "open-gate",
            "--payload-file",
            str(payload_path),
        ],
        store=store,
        paths=paths,
        now_fn=FakeClock("2026-03-01T10:01:00Z"),
    )

    assert exit_code == 0
    issues = store.list_records("m7")
    assert any(issue.metadata.get("coord_kind") == "gate" for issue in issues)
    assert any(issue.metadata.get("event") == "GATE_OPEN_SENT" for issue in issues)


def test_apply_payload_stdin_executes_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    monkeypatch.setattr(
        coord_module.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "milestone": "M7",
                    "run_date": "2026-03-01",
                    "roles": ["pm", "backend", "tester"],
                }
            )
        ),
    )

    exit_code = run_cli(
        [
            "apply",
            "init",
            "--payload-stdin",
        ],
        store=store,
        paths=paths,
    )

    assert exit_code == 0
    issues = store.list_records("m7")
    assert any(issue.metadata.get("coord_kind") == "milestone" for issue in issues)


def test_open_gate_canonicalizes_target_commit_when_git_can_resolve_it(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    short_commit = init_git_repo_with_review(paths, "dev_docs/reviews/m7_phase1_2026-03-01.md")
    full_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=paths.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock("2026-03-01T10:00:00Z"),
    )

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit=short_commit,
        task="open gate with short git ref",
    )
    service.render("M7")

    audit = service.audit("M7")
    assert audit["open_gates"] == [
        {
            "gate": "G-M7-P1",
            "phase": "1",
            "status": "pending",
            "allowed_role": "backend",
            "target_commit": full_commit,
        }
    ]
    assert audit["pending_ack_messages"] == [
        {
            "command": "GATE_OPEN",
            "role": "backend",
            "gate": "G-M7-P1",
            "phase": "1",
            "target_commit": full_commit,
        }
    ]


class TestMemoryCoordStoreContract:
    def test_create_returns_record_with_correct_fields(self) -> None:
        store = MemoryCoordStore()
        rec = store.create_record(
            title="test event",
            record_type="task",
            description="desc",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        assert rec.record_id
        assert rec.title == "test event"
        assert rec.record_type == "task"
        assert rec.status == "open"
        assert rec.has_label("coord")
        assert rec.metadata_str("coord_kind") == "event"

    def test_create_with_non_open_status(self) -> None:
        store = MemoryCoordStore()
        rec = store.create_record(
            title="closed item",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "milestone"},
            status="closed",
        )
        assert rec.status == "closed"

    def test_list_records_filters_by_milestone(self) -> None:
        store = MemoryCoordStore()
        store.create_record(
            title="m1 event",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        store.create_record(
            title="m2 event",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m2", "coord_kind": "event"},
        )
        assert len(store.list_records("m1")) == 1
        assert len(store.list_records("m2")) == 1
        assert store.list_records("m1")[0].title == "m1 event"

    def test_list_records_filters_by_kind(self) -> None:
        store = MemoryCoordStore()
        store.create_record(
            title="milestone rec",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "milestone"},
        )
        store.create_record(
            title="event rec",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        store.create_record(
            title="gate rec",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "gate"},
        )

        assert len(store.list_records("m1")) == 3
        assert len(store.list_records("m1", kind="milestone")) == 1
        assert len(store.list_records("m1", kind="event")) == 1
        assert len(store.list_records("m1", kind="gate")) == 1
        assert len(store.list_records("m1", kind="agent")) == 0

    def test_list_records_excludes_non_coord_labels(self) -> None:
        store = MemoryCoordStore()
        store.create_record(
            title="no coord label",
            record_type="task",
            description="",
            labels=["other"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        assert store.list_records("m1") == []

    def test_update_record_merges_fields(self) -> None:
        store = MemoryCoordStore()
        rec = store.create_record(
            title="original",
            record_type="task",
            description="desc",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        updated = store.update_record(rec.record_id, title="changed", status="closed")
        assert updated.title == "changed"
        assert updated.status == "closed"
        assert updated.description == "desc"
        assert updated.metadata_str("coord_kind") == "event"

    def test_update_record_replaces_metadata(self) -> None:
        store = MemoryCoordStore()
        rec = store.create_record(
            title="t",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "gate", "phase": "1"},
        )
        new_meta = {"milestone": "m1", "coord_kind": "gate", "phase": "1", "status": "closed"}
        updated = store.update_record(rec.record_id, metadata=new_meta)
        assert updated.metadata == new_meta

    def test_create_record_returns_same_as_list(self) -> None:
        store = MemoryCoordStore()
        created = store.create_record(
            title="roundtrip",
            record_type="task",
            description="d",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        listed = store.list_records("m1", kind="event")
        assert len(listed) == 1
        assert listed[0].record_id == created.record_id
        assert listed[0].title == created.title


class TestCoordRecordFromMapping:
    def test_string_labels_split(self) -> None:
        rec = CoordRecord.from_mapping(
            {"id": "1", "labels": "coord, extra", "metadata": {}}
        )
        assert rec.has_label("coord")
        assert rec.has_label("extra")

    def test_string_metadata_parsed(self) -> None:
        rec = CoordRecord.from_mapping(
            {
                "id": "1",
                "labels": [],
                "metadata": json.dumps({"coord_kind": "event"}),
            }
        )
        assert rec.metadata_str("coord_kind") == "event"

    def test_invalid_json_metadata_falls_back(self) -> None:
        rec = CoordRecord.from_mapping(
            {"id": "1", "labels": [], "metadata": "not-json"}
        )
        assert rec.metadata == {}

    def test_metadata_int_and_bool(self) -> None:
        rec = CoordRecord.from_mapping(
            {
                "id": "1",
                "labels": [],
                "metadata": {"count": 5, "flag": True, "str_int": "3"},
            }
        )
        assert rec.metadata_int("count") == 5
        assert rec.metadata_bool("flag") is True
        assert rec.metadata_int("str_int") == 3
        assert rec.metadata_int("missing", 99) == 99
        assert rec.metadata_bool("missing") is False
