"""Tests for src/backend/cli.py — CLI entry point smoke tests."""

from __future__ import annotations

import subprocess
import sys

import pytest

from src.backend.cli import _build_parser


class TestCliParser:
    def test_help_output(self) -> None:
        """--help should produce output without error."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_doctor_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"
        assert args.deep is False

    def test_doctor_deep_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["doctor", "--deep"])
        assert args.command == "doctor"
        assert args.deep is True

    def test_no_command_returns_none(self) -> None:
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None


class TestCliModule:
    def test_module_help(self) -> None:
        """python -m src.backend.cli --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "doctor" in result.stdout

    def test_doctor_help(self) -> None:
        """python -m src.backend.cli doctor --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "doctor", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "--deep" in result.stdout
