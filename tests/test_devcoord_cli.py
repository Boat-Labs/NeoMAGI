from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from tests.devcoord_helpers import (
    CoordError,
    CoordPaths,
    FakeClock,
    MemoryCoordStore,
    _normalize_argv,
    build_parser,
    init_git_repo_with_review,
    make_paths,
    run_cli,
)


class TestArgvNormalization:
    def test_open_gate_rewrites_to_gate_open(self) -> None:
        assert _normalize_argv(["open-gate", "--milestone", "M7"]) == [
            "gate",
            "open",
            "--milestone",
            "M7",
        ]

    def test_ack_rewrites_to_command_ack(self) -> None:
        assert _normalize_argv(["ack", "--milestone", "M7"]) == [
            "command",
            "ack",
            "--milestone",
            "M7",
        ]

    def test_ping_rewrites_to_command_send_ping(self) -> None:
        assert _normalize_argv(["ping", "--milestone", "M7", "--role", "backend"]) == [
            "command",
            "send",
            "--name",
            "PING",
            "--milestone",
            "M7",
            "--role",
            "backend",
        ]

    def test_render_rewrites_to_projection_render(self) -> None:
        assert _normalize_argv(["render", "--milestone", "M7"]) == [
            "projection",
            "render",
            "--milestone",
            "M7",
        ]

    def test_audit_rewrites_to_projection_audit(self) -> None:
        assert _normalize_argv(["audit", "--milestone", "M7"]) == [
            "projection",
            "audit",
            "--milestone",
            "M7",
        ]

    def test_milestone_close_rewrites(self) -> None:
        assert _normalize_argv(["milestone-close", "--milestone", "M7"]) == [
            "milestone",
            "close",
            "--milestone",
            "M7",
        ]

    def test_gate_review_rewrites(self) -> None:
        assert _normalize_argv(["gate-review", "--milestone", "M7"]) == [
            "gate",
            "review",
            "--milestone",
            "M7",
        ]

    def test_gate_close_rewrites(self) -> None:
        assert _normalize_argv(["gate-close", "--milestone", "M7"]) == [
            "gate",
            "close",
            "--milestone",
            "M7",
        ]

    def test_heartbeat_rewrites(self) -> None:
        assert _normalize_argv(["heartbeat", "--milestone", "M7"]) == [
            "event",
            "heartbeat",
            "--milestone",
            "M7",
        ]

    def test_retired_backend_flag_raises(self) -> None:
        with pytest.raises(CoordError, match="--backend has been retired"):
            _normalize_argv(["--backend", "sqlite", "open-gate", "--milestone", "M7"])

    def test_retired_backend_equals_syntax_raises(self) -> None:
        with pytest.raises(CoordError, match="--backend has been retired"):
            _normalize_argv(["--backend=sqlite", "open-gate", "--milestone", "M7"])

    def test_retired_beads_dir_flag_raises(self) -> None:
        with pytest.raises(CoordError, match="--beads-dir has been retired"):
            _normalize_argv(["--beads-dir", "/tmp/x", "init", "--milestone", "M7"])

    def test_retired_bd_bin_flag_raises(self) -> None:
        with pytest.raises(CoordError, match="--bd-bin has been retired"):
            _normalize_argv(["--bd-bin", "bd", "init", "--milestone", "M7"])

    def test_retired_dolt_bin_flag_raises(self) -> None:
        with pytest.raises(CoordError, match="--dolt-bin has been retired"):
            _normalize_argv(["--dolt-bin", "dolt", "init", "--milestone", "M7"])

    def test_does_not_rewrite_option_values(self) -> None:
        result = _normalize_argv([
            "ping",
            "--task",
            "follow up open-gate",
            "--milestone",
            "M7",
            "--role",
            "b",
            "--phase",
            "1",
            "--gate",
            "G",
        ])
        assert result[:4] == ["command", "send", "--name", "PING"]
        idx = result.index("--task")
        assert result[idx + 1] == "follow up open-gate"

    def test_does_not_rewrite_ping_in_option_value(self) -> None:
        result = _normalize_argv([
            "heartbeat",
            "--task",
            "waiting for ping ack",
            "--milestone",
            "M7",
            "--role",
            "b",
            "--phase",
            "1",
            "--status",
            "working",
        ])
        assert result[:2] == ["event", "heartbeat"]
        idx = result.index("--task")
        assert result[idx + 1] == "waiting for ping ack"

    def test_init_not_rewritten(self) -> None:
        assert _normalize_argv(["init", "--milestone", "M7"]) == ["init", "--milestone", "M7"]

    def test_apply_not_rewritten(self) -> None:
        assert _normalize_argv(["apply", "open-gate", "--payload-file", "f"]) == [
            "apply",
            "open-gate",
            "--payload-file",
            "f",
        ]

    def test_grouped_command_not_rewritten(self) -> None:
        assert _normalize_argv(["gate", "open", "--milestone", "M7"]) == [
            "gate",
            "open",
            "--milestone",
            "M7",
        ]

    def test_empty_argv(self) -> None:
        assert _normalize_argv([]) == []

    def test_help_flag_passthrough(self) -> None:
        assert _normalize_argv(["--help"]) == ["--help"]


class TestHelpSurface:
    def test_top_level_parser_only_has_grouped_commands(self) -> None:
        parser = build_parser()
        subparsers_action = None
        for action in parser._subparsers._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparsers_action = action
                break
        assert subparsers_action is not None
        assert set(subparsers_action.choices.keys()) == {
            "init",
            "gate",
            "command",
            "event",
            "projection",
            "milestone",
            "apply",
        }

    def test_flat_alias_help_resolves_to_grouped(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            run_cli(["open-gate", "--help"])
        out = capsys.readouterr().out
        assert "--milestone" in out
        assert "--phase" in out
        assert "--allowed-role" in out


class TestGroupedCLISmoke:
    def _init_m7(self, store: MemoryCoordStore, paths: CoordPaths) -> None:
        run_cli(["init", "--milestone", "M7", "--run-date", "2026-03-01"], store=store, paths=paths)

    def _open_gate(self, store: MemoryCoordStore, paths: CoordPaths, clock: FakeClock) -> None:
        run_cli(
            [
                "gate",
                "open",
                "--milestone",
                "M7",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--allowed-role",
                "backend",
                "--target-commit",
                "abc1234",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )

    def _ack_gate_open(self, store: MemoryCoordStore, paths: CoordPaths, clock: FakeClock) -> None:
        run_cli(
            [
                "command",
                "ack",
                "--milestone",
                "M7",
                "--role",
                "backend",
                "--cmd",
                "GATE_OPEN",
                "--gate",
                "G-M7-P1",
                "--commit",
                "abc1234",
                "--phase",
                "1",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )

    def _review_gate(
        self,
        store: MemoryCoordStore,
        paths: CoordPaths,
        clock: FakeClock,
        report_commit: str,
        report_path: str,
        *,
        command: str = "gate",
        role: str = "pm",
    ) -> None:
        argv = [command, "review"] if command == "gate" else [command]
        run_cli(
            [
                *argv,
                "--milestone",
                "M7",
                "--role",
                role,
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--result",
                "PASS",
                "--report-commit",
                report_commit,
                "--report-path",
                report_path,
                "--task",
                "review",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )

    def _close_gate(
        self,
        store: MemoryCoordStore,
        paths: CoordPaths,
        clock: FakeClock,
        report_commit: str,
        report_path: str,
        *,
        command: str = "gate",
    ) -> int:
        argv = [command, "close"] if command == "gate" else [command]
        return run_cli(
            [
                *argv,
                "--milestone",
                "M7",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--result",
                "PASS",
                "--report-commit",
                report_commit,
                "--report-path",
                report_path,
                "--task",
                "close",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )

    def test_gate_open(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        self._init_m7(store, paths)
        exit_code = run_cli(
            [
                "gate",
                "open",
                "--milestone",
                "M7",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--allowed-role",
                "backend",
                "--target-commit",
                "abc1234",
            ],
            store=store,
            paths=paths,
            now_fn=FakeClock("2026-03-01T10:01:00Z"),
        )
        assert exit_code == 0
        assert any(r.metadata.get("coord_kind") == "gate" for r in store.list_records("m7"))

    def test_command_ack(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        self._init_m7(store, paths)
        self._open_gate(store, paths, FakeClock("2026-03-01T10:01:00Z"))
        exit_code = run_cli(
            [
                "command",
                "ack",
                "--milestone",
                "M7",
                "--role",
                "backend",
                "--cmd",
                "GATE_OPEN",
                "--gate",
                "G-M7-P1",
                "--commit",
                "abc1234",
                "--phase",
                "1",
            ],
            store=store,
            paths=paths,
            now_fn=FakeClock("2026-03-01T10:02:00Z"),
        )
        assert exit_code == 0

    def test_command_send_ping(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        self._init_m7(store, paths)
        clock = FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:10:00Z",
        )
        self._open_gate(store, paths, clock)
        self._ack_gate_open(store, paths, clock)
        exit_code = run_cli(
            [
                "command",
                "send",
                "--name",
                "PING",
                "--milestone",
                "M7",
                "--role",
                "backend",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--task",
                "checking in",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )
        assert exit_code == 0
        messages = store.list_records("m7", kind="message")
        ping_msgs = [m for m in messages if m.metadata_str("command") == "PING"]
        assert len(ping_msgs) == 1

    def test_event_heartbeat(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        self._init_m7(store, paths)
        clock = FakeClock("2026-03-01T10:01:00Z", "2026-03-01T10:02:00Z", "2026-03-01T10:10:00Z")
        self._open_gate(store, paths, clock)
        self._ack_gate_open(store, paths, clock)
        exit_code = run_cli(
            [
                "event",
                "heartbeat",
                "--milestone",
                "M7",
                "--role",
                "backend",
                "--phase",
                "1",
                "--status",
                "working",
                "--task",
                "coding",
                "--gate",
                "G-M7-P1",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )
        assert exit_code == 0
        events = store.list_records("m7", kind="event")
        assert any(e.metadata_str("event") == "HEARTBEAT" for e in events)

    def test_projection_render_and_audit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        self._init_m7(store, paths)
        run_cli(
            [
                "gate",
                "open",
                "--milestone",
                "M7",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--allowed-role",
                "backend",
                "--target-commit",
                "abc1234",
            ],
            store=store,
            paths=paths,
            now_fn=FakeClock("2026-03-01T10:01:00Z"),
        )
        exit_code = run_cli(
            ["projection", "render", "--milestone", "M7"],
            store=store,
            paths=paths,
        )
        assert exit_code == 0
        log_dir = paths.log_dir("m7", "2026-03-01")
        assert (log_dir / "heartbeat_events.jsonl").exists()

        exit_code = run_cli(
            ["projection", "audit", "--milestone", "M7"],
            store=store,
            paths=paths,
        )
        assert exit_code == 0
        audit = json.loads(capsys.readouterr().out)
        assert audit["reconciled"] is True

    def test_milestone_close(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
        report_commit = init_git_repo_with_review(paths, report_path)
        clock = FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
        )
        self._init_m7(store, paths)
        self._open_gate(store, paths, clock)
        self._review_gate(store, paths, clock, report_commit, report_path)
        run_cli(["projection", "render", "--milestone", "M7"], store=store, paths=paths)
        self._close_gate(store, paths, clock, report_commit, report_path)
        run_cli(["projection", "render", "--milestone", "M7"], store=store, paths=paths)
        exit_code = run_cli(
            ["milestone", "close", "--milestone", "M7"],
            store=store,
            paths=paths,
        )
        assert exit_code == 0
        assert all(r.status == "closed" for r in store.list_records("m7"))


class TestFlatAliasCompatibility:
    def _init_and_open(
        self, store: MemoryCoordStore, paths: CoordPaths, clock: FakeClock
    ) -> None:
        run_cli(["init", "--milestone", "M7", "--run-date", "2026-03-01"], store=store, paths=paths)
        run_cli(
            [
                "open-gate",
                "--milestone",
                "M7",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--allowed-role",
                "backend",
                "--target-commit",
                "abc1234",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )

    def _ack_open(self, store: MemoryCoordStore, paths: CoordPaths, clock: FakeClock) -> None:
        run_cli(
            [
                "ack",
                "--milestone",
                "M7",
                "--role",
                "backend",
                "--cmd",
                "GATE_OPEN",
                "--gate",
                "G-M7-P1",
                "--commit",
                "abc1234",
                "--phase",
                "1",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )

    def _review_alias(
        self,
        store: MemoryCoordStore,
        paths: CoordPaths,
        clock: FakeClock,
        report_commit: str,
        report_path: str,
    ) -> None:
        run_cli(
            [
                "gate-review",
                "--milestone",
                "M7",
                "--role",
                "pm",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--result",
                "PASS",
                "--report-commit",
                report_commit,
                "--report-path",
                report_path,
                "--task",
                "review",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )

    def _close_gate(
        self,
        store: MemoryCoordStore,
        paths: CoordPaths,
        clock: FakeClock,
        report_commit: str,
        report_path: str,
        *,
        command: str = "gate-close",
    ) -> int:
        return run_cli(
            [
                command,
                "--milestone",
                "M7",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--result",
                "PASS",
                "--report-commit",
                report_commit,
                "--report-path",
                report_path,
                "--task",
                "close",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )

    def test_open_gate_alias(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        run_cli(["init", "--milestone", "M7", "--run-date", "2026-03-01"], store=store, paths=paths)
        exit_code = run_cli(
            [
                "open-gate",
                "--milestone",
                "M7",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--allowed-role",
                "backend",
                "--target-commit",
                "abc1234",
            ],
            store=store,
            paths=paths,
            now_fn=FakeClock("2026-03-01T10:01:00Z"),
        )
        assert exit_code == 0

    def test_ack_alias(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        clock = FakeClock("2026-03-01T10:01:00Z", "2026-03-01T10:02:00Z")
        self._init_and_open(store, paths, clock)
        exit_code = run_cli(
            [
                "ack",
                "--milestone",
                "M7",
                "--role",
                "backend",
                "--cmd",
                "GATE_OPEN",
                "--gate",
                "G-M7-P1",
                "--commit",
                "abc1234",
                "--phase",
                "1",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )
        assert exit_code == 0

    def test_ping_alias(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        clock = FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:10:00Z",
        )
        self._init_and_open(store, paths, clock)
        self._ack_open(store, paths, clock)
        exit_code = run_cli(
            [
                "ping",
                "--milestone",
                "M7",
                "--role",
                "backend",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--task",
                "checking in",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )
        assert exit_code == 0

    def test_render_alias(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        clock = FakeClock("2026-03-01T10:01:00Z")
        self._init_and_open(store, paths, clock)
        exit_code = run_cli(["render", "--milestone", "M7"], store=store, paths=paths)
        assert exit_code == 0

    def test_audit_alias(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        clock = FakeClock("2026-03-01T10:01:00Z")
        self._init_and_open(store, paths, clock)
        run_cli(["render", "--milestone", "M7"], store=store, paths=paths)
        exit_code = run_cli(["audit", "--milestone", "M7"], store=store, paths=paths)
        assert exit_code == 0
        audit = json.loads(capsys.readouterr().out)
        assert "reconciled" in audit

    def test_gate_review_alias(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
        report_commit = init_git_repo_with_review(paths, report_path)
        clock = FakeClock("2026-03-01T10:01:00Z", "2026-03-01T10:02:00Z")
        self._init_and_open(store, paths, clock)
        exit_code = run_cli(
            [
                "gate-review",
                "--milestone",
                "M7",
                "--role",
                "tester",
                "--phase",
                "1",
                "--gate",
                "G-M7-P1",
                "--result",
                "PASS",
                "--report-commit",
                report_commit,
                "--report-path",
                report_path,
                "--task",
                "review",
            ],
            store=store,
            paths=paths,
            now_fn=clock,
        )
        assert exit_code == 0

    def test_gate_close_alias(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
        report_commit = init_git_repo_with_review(paths, report_path)
        clock = FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
        )
        self._init_and_open(store, paths, clock)
        self._review_alias(store, paths, clock, report_commit, report_path)
        run_cli(["render", "--milestone", "M7"], store=store, paths=paths)
        exit_code = self._close_gate(
            store,
            paths,
            clock,
            report_commit,
            report_path,
            command="gate-close",
        )
        assert exit_code == 0

    def test_milestone_close_alias(self, tmp_path: Path) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
        report_commit = init_git_repo_with_review(paths, report_path)
        clock = FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
        )
        self._init_and_open(store, paths, clock)
        self._review_alias(store, paths, clock, report_commit, report_path)
        run_cli(["render", "--milestone", "M7"], store=store, paths=paths)
        self._close_gate(store, paths, clock, report_commit, report_path, command="gate-close")
        run_cli(["render", "--milestone", "M7"], store=store, paths=paths)
        exit_code = run_cli(["milestone-close", "--milestone", "M7"], store=store, paths=paths)
        assert exit_code == 0


class TestApplyStability:
    def test_apply_audit_json_unchanged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = MemoryCoordStore()
        paths = make_paths(tmp_path)
        init_payload = tmp_path / "init.json"
        init_payload.write_text(
            json.dumps({"milestone": "M7", "run_date": "2026-03-01", "roles": "pm,backend,tester"}),
            "utf-8",
        )
        run_cli(["apply", "init", "--payload-file", str(init_payload)], store=store, paths=paths)
        open_gate_payload = tmp_path / "open_gate.json"
        open_gate_payload.write_text(
            json.dumps(
                {
                    "milestone": "M7",
                    "phase": "1",
                    "gate_id": "G-M7-P1",
                    "allowed_role": "backend",
                    "target_commit": "abc1234",
                    "task": "open",
                }
            ),
            "utf-8",
        )
        run_cli(
            ["apply", "open-gate", "--payload-file", str(open_gate_payload)],
            store=store,
            paths=paths,
            now_fn=FakeClock("2026-03-01T10:01:00Z"),
        )
        render_payload = tmp_path / "render.json"
        render_payload.write_text(json.dumps({"milestone": "M7"}), "utf-8")
        run_cli(
            ["apply", "render", "--payload-file", str(render_payload)],
            store=store,
            paths=paths,
        )
        audit_payload = tmp_path / "audit.json"
        audit_payload.write_text(json.dumps({"milestone": "M7"}), "utf-8")
        exit_code = run_cli(
            ["apply", "audit", "--payload-file", str(audit_payload)],
            store=store,
            paths=paths,
        )
        assert exit_code == 0
        audit = json.loads(capsys.readouterr().out)
        assert audit["reconciled"] is True
        assert isinstance(audit["received_events"], int)
        assert isinstance(audit["open_gates"], list)
