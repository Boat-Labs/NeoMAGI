from __future__ import annotations

from pathlib import Path

import pytest

from tests.devcoord_helpers import (
    DEFAULT_GATE_ID,
    DEFAULT_MILESTONE,
    DEFAULT_PHASE,
    DEFAULT_TARGET_COMMIT,
    CoordService,
    FakeClock,
    MemoryCoordStore,
    SQLiteCoordStore,
    ack_default_gate_open,
    build_parser,
    gate_review_default,
    init_default_control_plane,
    init_git_repo_with_review,
    make_paths,
    make_sqlite_paths,
    make_sqlite_service,
    open_default_gate,
    phase_complete_default,
    run_cli,
)


def _init_open_ack_sqlite(service: CoordService) -> None:
    init_default_control_plane(service)
    open_default_gate(service, task="open gate")
    ack_default_gate_open(service, phase=DEFAULT_PHASE, task="ACK")


def _init_open_ack_cli(store, paths) -> None:
    run_cli(
        ["init", "--milestone", DEFAULT_MILESTONE, "--run-date", "2026-03-01"],
        store=store,
        paths=paths,
    )
    run_cli(
        [
            "gate",
            "open",
            "--milestone",
            DEFAULT_MILESTONE,
            "--phase",
            DEFAULT_PHASE,
            "--gate",
            DEFAULT_GATE_ID,
            "--allowed-role",
            "backend",
            "--target-commit",
            DEFAULT_TARGET_COMMIT,
            "--task",
            "open",
        ],
        store=store,
        paths=paths,
        now_fn=lambda: "2026-03-01T10:01:00Z",
    )
    run_cli(
        [
            "command",
            "ack",
            "--milestone",
            DEFAULT_MILESTONE,
            "--role",
            "backend",
            "--cmd",
            "GATE_OPEN",
            "--gate",
            DEFAULT_GATE_ID,
            "--commit",
            DEFAULT_TARGET_COMMIT,
            "--phase",
            DEFAULT_PHASE,
            "--task",
            "ack",
        ],
        store=store,
        paths=paths,
        now_fn=lambda: "2026-03-01T10:02:00Z",
    )


class TestSQLitePragmaSettings:
    def test_journal_mode_is_wal(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        conn = store._connect()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        store.close()

    def test_busy_timeout_at_least_5000(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        conn = store._connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout >= 5000
        store.close()


class TestCommandSendSurface:
    @pytest.mark.parametrize("cmd_name", ["STOP", "WAIT", "RESUME", "PING"])
    def test_command_send_creates_pending_message_and_event(
        self, tmp_path: Path, cmd_name: str
    ) -> None:
        _, store, service = make_sqlite_service(
            tmp_path,
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
        )
        _init_open_ack_sqlite(service)
        service.ping(
            DEFAULT_MILESTONE,
            role="backend",
            phase=DEFAULT_PHASE,
            gate_id=DEFAULT_GATE_ID,
            task=f"send {cmd_name}",
            command_name=cmd_name,
        )
        messages = store.list_records(DEFAULT_MILESTONE.lower(), kind="message")
        sent_msgs = [
            m
            for m in messages
            if m.metadata.get("command") == cmd_name and not m.metadata_bool("effective")
        ]
        assert len(sent_msgs) >= 1
        events = store.list_records(DEFAULT_MILESTONE.lower(), kind="event")
        sent_events = [e for e in events if e.metadata.get("event") == f"{cmd_name}_SENT"]
        assert len(sent_events) >= 1
        store.close()

    @pytest.mark.parametrize("cmd_name", ["STOP", "WAIT", "RESUME"])
    def test_command_send_can_be_acked(self, tmp_path: Path, cmd_name: str) -> None:
        _, store, service = make_sqlite_service(
            tmp_path,
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
        )
        _init_open_ack_sqlite(service)
        service.ping(
            DEFAULT_MILESTONE,
            role="backend",
            phase=DEFAULT_PHASE,
            gate_id=DEFAULT_GATE_ID,
            task=f"send {cmd_name}",
            command_name=cmd_name,
        )
        service.ack(
            DEFAULT_MILESTONE,
            role="backend",
            command=cmd_name,
            gate_id=DEFAULT_GATE_ID,
            commit=DEFAULT_TARGET_COMMIT,
            phase=DEFAULT_PHASE,
            task=f"ACK {cmd_name}",
        )
        messages = store.list_records(DEFAULT_MILESTONE.lower(), kind="message")
        acked = [
            m
            for m in messages
            if m.metadata.get("command") == cmd_name and m.metadata_bool("effective")
        ]
        assert len(acked) == 1
        store.close()

    @pytest.mark.parametrize("cmd_name", ["STOP", "WAIT", "RESUME", "PING"])
    def test_command_send_cli_surface(self, tmp_path: Path, cmd_name: str) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        _init_open_ack_cli(store, paths)
        exit_code = run_cli(
            [
                "command",
                "send",
                "--name",
                cmd_name,
                "--milestone",
                DEFAULT_MILESTONE,
                "--role",
                "backend",
                "--phase",
                DEFAULT_PHASE,
                "--gate",
                DEFAULT_GATE_ID,
                "--task",
                f"send {cmd_name}",
            ],
            store=store,
            paths=paths,
            now_fn=lambda: "2026-03-01T10:03:00Z",
        )
        assert exit_code == 0
        records = store.list_records(DEFAULT_MILESTONE.lower())
        sent_msgs = [
            r
            for r in records
            if r.metadata.get("coord_kind") == "message" and r.metadata.get("command") == cmd_name
        ]
        assert len(sent_msgs) >= 1


class TestTransactionAtomicity:
    def test_ack_rolls_back_on_failure(self, tmp_path: Path) -> None:
        _, store, service = make_sqlite_service(
            tmp_path,
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
        )
        init_default_control_plane(service)
        open_default_gate(service, task="open gate")

        original_create = store.create_record

        def failing_create(**kwargs):
            meta = kwargs.get("metadata", {})
            if meta.get("coord_kind") == "event" and meta.get("event") == "ACK":
                raise RuntimeError("injected fault: ACK event write failure")
            return original_create(**kwargs)

        store.create_record = failing_create  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="injected fault"):
            service.ack(
                DEFAULT_MILESTONE,
                role="backend",
                command="GATE_OPEN",
                gate_id=DEFAULT_GATE_ID,
                commit=DEFAULT_TARGET_COMMIT,
                phase=DEFAULT_PHASE,
                task="ACK",
            )

        store.create_record = original_create  # type: ignore[assignment]

        messages = store.list_records(DEFAULT_MILESTONE.lower(), kind="message")
        gate_open_msgs = [m for m in messages if m.metadata.get("command") == "GATE_OPEN"]
        assert len(gate_open_msgs) == 1
        assert gate_open_msgs[0].metadata_bool("effective") is False

        gates = store.list_records(DEFAULT_MILESTONE.lower(), kind="gate")
        gate = [g for g in gates if g.metadata.get("gate_id") == DEFAULT_GATE_ID][0]
        assert gate.metadata_str("gate_state") == "pending"
        store.close()

    def test_gate_close_rolls_back_on_failure(self, tmp_path: Path) -> None:
        paths, store, service = make_sqlite_service(
            tmp_path,
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
        )
        _init_open_ack_sqlite(service)
        phase_complete_default(service, commit=DEFAULT_TARGET_COMMIT, task="done")

        report_relpath = "dev_docs/reports/m7_p1_review.md"
        report_commit = init_git_repo_with_review(paths, report_relpath)
        gate_review_default(service, report_commit, report_relpath, task="review")
        service.render(DEFAULT_MILESTONE)

        original_create = store.create_record

        def failing_create(**kwargs):
            if kwargs.get("metadata", {}).get("event") == "GATE_CLOSE":
                raise RuntimeError("injected fault: GATE_CLOSE event write failure")
            return original_create(**kwargs)

        store.create_record = failing_create  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="injected fault"):
            service.gate_close(
                DEFAULT_MILESTONE,
                phase=DEFAULT_PHASE,
                gate_id=DEFAULT_GATE_ID,
                result="PASS",
                report_commit=report_commit,
                report_path=report_relpath,
                task="close gate",
            )

        store.create_record = original_create  # type: ignore[assignment]

        gates = store.list_records(DEFAULT_MILESTONE.lower(), kind="gate")
        gate = [g for g in gates if g.metadata.get("gate_id") == DEFAULT_GATE_ID][0]
        assert gate.metadata_str("gate_state") != "closed"

        phases = store.list_records(DEFAULT_MILESTONE.lower(), kind="phase")
        phase = [p for p in phases if p.metadata.get("phase") == DEFAULT_PHASE][0]
        assert phase.metadata_str("phase_state") != "closed"
        store.close()


class TestProjectionRebuildable:
    def test_tampered_projection_is_corrected_by_rerender(self, tmp_path: Path) -> None:
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

        log_dir = paths.workspace_root / "dev_docs" / "logs" / "phase1" / "m7_2026-03-01"
        heartbeat_path = log_dir / "heartbeat_events.jsonl"
        gate_state_path = log_dir / "gate_state.md"

        original_heartbeat = heartbeat_path.read_text("utf-8")
        original_gate_state = gate_state_path.read_text("utf-8")

        heartbeat_path.write_text("TAMPERED CONTENT\n", "utf-8")
        gate_state_path.write_text("TAMPERED GATE STATE\n", "utf-8")

        service.render("M7")

        restored_heartbeat = heartbeat_path.read_text("utf-8")
        restored_gate_state = gate_state_path.read_text("utf-8")

        assert restored_heartbeat == original_heartbeat
        assert restored_gate_state == original_gate_state
        assert "TAMPERED" not in restored_heartbeat
        assert "TAMPERED" not in restored_gate_state
        store.close()


class TestRuntimeDocsAlignment:
    GROUPED_COMMANDS = [
        ["init", "--help"],
        ["gate", "open", "--help"],
        ["gate", "review", "--help"],
        ["gate", "close", "--help"],
        ["command", "ack", "--help"],
        ["command", "send", "--help"],
        ["event", "heartbeat", "--help"],
        ["event", "phase-complete", "--help"],
        ["event", "recovery-check", "--help"],
        ["event", "state-sync-ok", "--help"],
        ["event", "stale-detected", "--help"],
        ["event", "log-pending", "--help"],
        ["event", "unconfirmed-instruction", "--help"],
        ["projection", "render", "--help"],
        ["projection", "audit", "--help"],
        ["milestone", "close", "--help"],
        ["apply", "--help"],
    ]

    @pytest.mark.parametrize("argv", GROUPED_COMMANDS, ids=[" ".join(c) for c in GROUPED_COMMANDS])
    def test_grouped_command_help_succeeds(self, argv: list[str]) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(argv)
        assert exc_info.value.code == 0
