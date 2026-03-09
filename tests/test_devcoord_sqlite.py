from __future__ import annotations

from pathlib import Path

import pytest

from scripts.devcoord import coord as coord_module
from tests.devcoord_helpers import (
    SQLITE_SCHEMA_VERSION,
    CoordError,
    CoordService,
    FakeClock,
    SQLiteCoordStore,
    _resolve_paths,
    init_git_repo_with_review,
    make_sqlite_paths,
)


class TestSQLiteSchemaBootstrap:
    def test_init_creates_db_and_tables(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        assert paths.control_db.exists()
        conn = store._connect()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"milestones", "phases", "gates", "roles", "messages", "events"} <= tables
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SQLITE_SCHEMA_VERSION
        store.close()

    def test_init_idempotent(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        store.init_store()
        store.close()

    def test_schema_version_mismatch_fails_closed(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        conn = store._connect()
        conn.execute(f"PRAGMA user_version={SQLITE_SCHEMA_VERSION + 1}")
        conn.commit()
        store.close()
        store2 = SQLiteCoordStore(paths.control_db)
        with pytest.raises(CoordError, match="incompatible schema version"):
            store2.init_store()
        store2.close()

    def test_empty_store_returns_no_records(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        assert store.list_records("m7") == []
        store.close()


class TestSQLiteFullLifecycle:
    def test_init_open_ack_heartbeat_phase_complete(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)

        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))

        records = store.list_records("m7")
        kinds = sorted(rec.metadata_str("coord_kind") for rec in records)
        assert kinds.count("milestone") == 1
        assert kinds.count("agent") == 3

        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )

        gates = store.list_records("m7", kind="gate")
        assert len(gates) == 1
        assert gates[0].metadata_str("gate_state") == "pending"

        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )

        gates = store.list_records("m7", kind="gate")
        assert gates[0].metadata_str("gate_state") == "open"

        service.heartbeat(
            "M7",
            role="backend",
            phase="1",
            status="working",
            task="coding",
            eta_min=30,
            gate_id="G-M7-P1",
            target_commit="abc1234",
        )

        service.phase_complete(
            "M7",
            role="backend",
            phase="1",
            gate_id="G-M7-P1",
            commit="abc1234",
            task="phase 1 done",
        )

        events = store.list_records("m7", kind="event")
        event_types = [e.metadata_str("event") for e in events]
        assert "GATE_OPEN_SENT" in event_types
        assert "ACK" in event_types
        assert "GATE_EFFECTIVE" in event_types
        assert "HEARTBEAT" in event_types
        assert "PHASE_COMPLETE" in event_types

        store.close()

    def test_render_and_audit_with_sqlite(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )

        service.render("M7")

        log_dir = paths.log_dir("m7", "2026-03-01")
        assert (log_dir / "heartbeat_events.jsonl").exists()
        assert (log_dir / "gate_state.md").exists()
        assert (log_dir / "watchdog_status.md").exists()
        assert paths.progress_file.exists()

        heartbeat_lines = [
            line
            for line in (log_dir / "heartbeat_events.jsonl").read_text("utf-8").splitlines()
            if line.strip()
        ]
        assert len(heartbeat_lines) > 0

        audit = service.audit("M7")
        assert audit["reconciled"] is True
        assert audit["received_events"] == len(heartbeat_lines)

        store.close()

    def test_gate_close_and_milestone_close_with_sqlite(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )
        service.phase_complete(
            "M7",
            role="backend",
            phase="1",
            gate_id="G-M7-P1",
            commit="abc1234",
            task="done",
        )

        report_relpath = "dev_docs/reports/m7_p1_review.md"
        report_commit = init_git_repo_with_review(paths, report_relpath)

        service.gate_review(
            "M7",
            role="tester",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="review",
        )
        service.render("M7")
        service.gate_close(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="close gate",
        )
        service.render("M7")

        audit = service.audit("M7")
        assert audit["reconciled"] is True
        assert audit["open_gates"] == []

        service.close_milestone("M7")

        milestones = store.list_records("m7", kind="milestone")
        assert milestones[0].status == "closed"

        store.close()

    def test_ping_and_ack_with_sqlite(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )
        service.ping(
            "M7",
            role="backend",
            phase="1",
            gate_id="G-M7-P1",
            task="checking in",
        )
        service.ack(
            "M7",
            role="backend",
            command="PING",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK PING",
        )

        messages = store.list_records("m7", kind="message")
        ping_msgs = [m for m in messages if m.metadata_str("command") == "PING"]
        assert len(ping_msgs) == 1
        assert ping_msgs[0].metadata_bool("effective") is True

        store.close()

    def test_recovery_check_and_state_sync_ok(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )
        service.recovery_check(
            "M7",
            role="backend",
            last_seen_gate="G-M7-P1",
            task="backend recovery",
        )
        service.state_sync_ok(
            "M7",
            role="backend",
            gate_id="G-M7-P1",
            target_commit="abc1234",
            task="sync ok",
        )

        events = store.list_records("m7", kind="event")
        event_types = [e.metadata_str("event") for e in events]
        assert "RECOVERY_CHECK" in event_types
        assert "STATE_SYNC_OK" in event_types

        store.close()


class TestSQLitePathResolution:
    def test_control_db_path_from_control_root(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        assert paths.control_db == paths.control_root / "control.db"

    def test_lock_file_in_control_root(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        assert paths.lock_file == paths.control_root / "coord.lock"


class TestSQLitePathResolutionFromCLI:
    def test_resolve_paths_returns_devcoord_control_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_root = tmp_path / "workspace"
        (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
        monkeypatch.setattr(
            coord_module, "_resolve_git_common_dir", lambda cwd: workspace_root / ".git"
        )
        paths = _resolve_paths()
        assert paths.control_root == workspace_root / ".devcoord"
        assert paths.control_db == workspace_root / ".devcoord" / "control.db"

    def test_legacy_beads_without_control_db_raises_split_brain_guard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_root = tmp_path / "workspace"
        (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
        beads_marker = workspace_root / ".beads" / "metadata.json"
        beads_marker.parent.mkdir(parents=True, exist_ok=True)
        beads_marker.write_text("{}", "utf-8")
        monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
        monkeypatch.setattr(
            coord_module, "_resolve_git_common_dir", lambda cwd: workspace_root / ".git"
        )
        with pytest.raises(CoordError, match="Legacy beads control plane detected"):
            _resolve_paths()

    def test_legacy_coord_beads_without_control_db_raises_split_brain_guard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_root = tmp_path / "workspace"
        (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
        legacy_marker = workspace_root / ".coord" / "beads" / ".beads" / "metadata.json"
        legacy_marker.parent.mkdir(parents=True, exist_ok=True)
        legacy_marker.write_text("{}", "utf-8")
        monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
        monkeypatch.setattr(
            coord_module, "_resolve_git_common_dir", lambda cwd: workspace_root / ".git"
        )
        with pytest.raises(CoordError, match="Legacy beads control plane detected"):
            _resolve_paths()

    def test_legacy_beads_with_control_db_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_root = tmp_path / "workspace"
        (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
        beads_marker = workspace_root / ".beads" / "metadata.json"
        beads_marker.parent.mkdir(parents=True, exist_ok=True)
        beads_marker.write_text("{}", "utf-8")
        control_root = workspace_root / ".devcoord"
        control_root.mkdir(parents=True, exist_ok=True)
        (control_root / "control.db").write_text("", "utf-8")
        monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
        monkeypatch.setattr(
            coord_module, "_resolve_git_common_dir", lambda cwd: workspace_root / ".git"
        )
        paths = _resolve_paths()
        assert paths.control_root == control_root


class TestSQLiteStaleDetectedAndLogPending:
    def test_stale_detected_event(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.stale_detected(
            "M7",
            role="backend",
            phase="1",
            task="timeout check",
            gate_id="G-M7-P1",
            ping_count=3,
        )
        events = store.list_records("m7", kind="event")
        stale_events = [e for e in events if e.metadata_str("event") == "STALE_DETECTED"]
        assert len(stale_events) == 1
        assert stale_events[0].metadata.get("ping_count") == 3
        store.close()

    def test_log_pending_event(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock("2026-03-01T10:00:00Z", "2026-03-01T10:01:00Z")
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.log_pending(
            "M7",
            phase="1",
            task="deferred log",
        )
        events = store.list_records("m7", kind="event")
        log_events = [e for e in events if e.metadata_str("event") == "LOG_PENDING"]
        assert len(log_events) == 1
        store.close()


class TestSQLiteUnconfirmedInstruction:
    def test_unconfirmed_instruction(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.unconfirmed_instruction(
            "M7",
            role="backend",
            command="GATE_OPEN",
            phase="1",
            gate_id="G-M7-P1",
            task="unconfirmed",
            ping_count=5,
        )
        events = store.list_records("m7", kind="event")
        uc_events = [e for e in events if e.metadata_str("event") == "UNCONFIRMED_INSTRUCTION"]
        assert len(uc_events) == 1
        store.close()


class TestSQLiteFreshStartBootstrap:
    def test_fresh_start_from_empty_db(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
            "2026-03-01T10:08:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)

        service.init_control_plane(
            "P2-M1B", run_date="2026-03-01", roles=("pm", "backend", "tester")
        )
        service.open_gate(
            "P2-M1B",
            phase="1",
            gate_id="G0",
            allowed_role="backend",
            target_commit="deadbeef",
            task="open G0",
        )
        service.ack(
            "P2-M1B",
            role="backend",
            command="GATE_OPEN",
            gate_id="G0",
            commit="deadbeef",
            phase="1",
            task="ACK G0",
        )
        service.heartbeat(
            "P2-M1B",
            role="backend",
            phase="1",
            status="working",
            task="implementing",
            eta_min=60,
            gate_id="G0",
        )
        service.phase_complete(
            "P2-M1B",
            role="backend",
            phase="1",
            gate_id="G0",
            commit="deadbeef",
            task="P1 done",
        )

        report_relpath = "dev_docs/reports/p2m1b_review.md"
        report_commit = init_git_repo_with_review(paths, report_relpath)

        service.gate_review(
            "P2-M1B",
            role="tester",
            phase="1",
            gate_id="G0",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="review G0",
        )
        service.render("P2-M1B")
        service.gate_close(
            "P2-M1B",
            phase="1",
            gate_id="G0",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="close G0",
        )
        service.render("P2-M1B")

        audit = service.audit("P2-M1B")
        assert audit["reconciled"] is True
        assert audit["open_gates"] == []
        assert audit["pending_ack_messages"] == []

        service.close_milestone("P2-M1B")

        milestones = store.list_records("p2-m1b", kind="milestone")
        assert milestones[0].status == "closed"

        store.close()


class TestSQLiteWriteConflictSmoke:
    def test_busy_timeout_with_concurrent_write(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store1 = SQLiteCoordStore(paths.control_db)
        store1.init_store()
        store2 = SQLiteCoordStore(paths.control_db)
        store2.init_store()

        clock1 = FakeClock("2026-03-01T10:00:00Z")
        service1 = CoordService(paths=paths, store=store1, now_fn=clock1)
        service1.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend"))

        records = store2.list_records("m7")
        assert len(records) > 0

        store1.close()
        store2.close()


class TestSQLiteCloseMilestoneContract:
    def test_close_milestone_preserves_event_status_and_closes_records(
        self, tmp_path: Path
    ) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )
        service.phase_complete(
            "M7",
            role="backend",
            phase="1",
            gate_id="G-M7-P1",
            commit="abc1234",
            task="done",
        )

        report_relpath = "dev_docs/reports/m7_p1_review.md"
        report_commit = init_git_repo_with_review(paths, report_relpath)

        service.gate_review(
            "M7",
            role="tester",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="review",
        )
        service.render("M7")
        service.gate_close(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="close gate",
        )
        service.render("M7")

        events_before = store.list_records("m7", kind="event")
        event_statuses_before = {
            e.metadata_int("event_seq"): e.metadata_str("status") for e in events_before
        }

        service.close_milestone("M7")

        events_after = store.list_records("m7", kind="event")
        for ev in events_after:
            seq = ev.metadata_int("event_seq")
            assert ev.metadata_str("status") == event_statuses_before[seq]

        milestones = store.list_records("m7", kind="milestone")
        assert all(m.status == "closed" for m in milestones)
        phases = store.list_records("m7", kind="phase")
        assert all(p.status == "closed" for p in phases)
        gates = store.list_records("m7", kind="gate")
        assert all(g.status == "closed" for g in gates)
        roles = store.list_records("m7", kind="agent")
        assert all(r.status == "closed" for r in roles)

        events_after = store.list_records("m7", kind="event")
        assert len(events_after) > 0
        assert all(e.status == "closed" for e in events_after)

        messages = store.list_records("m7", kind="message")
        assert len(messages) > 0
        assert all(m.status == "closed" for m in messages)

        all_records = store.list_records("m7")
        assert all(rec.status == "closed" for rec in all_records)

        audit = service.audit("M7")
        assert audit["reconciled"] is True

        store.close()

    def test_render_after_close_preserves_event_projection(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )
        service.heartbeat(
            "M7",
            role="backend",
            phase="1",
            status="working",
            task="coding",
            eta_min=30,
            gate_id="G-M7-P1",
            target_commit="abc1234",
        )
        service.phase_complete(
            "M7",
            role="backend",
            phase="1",
            gate_id="G-M7-P1",
            commit="abc1234",
            task="done",
        )

        report_relpath = "dev_docs/reports/m7_p1_review.md"
        report_commit = init_git_repo_with_review(paths, report_relpath)

        service.gate_review(
            "M7",
            role="tester",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="review",
        )
        service.render("M7")
        service.gate_close(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="close gate",
        )
        service.render("M7")

        log_dir = paths.log_dir("m7", "2026-03-01")
        projection_before = (log_dir / "heartbeat_events.jsonl").read_text("utf-8")

        service.close_milestone("M7")

        service.render("M7")
        projection_after = (log_dir / "heartbeat_events.jsonl").read_text("utf-8")

        assert projection_before == projection_after

        store.close()
