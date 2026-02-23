#!/usr/bin/env python3
"""Block destructive Bash commands in Claude Code PreToolUse hook."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    message: str


RULES: tuple[Rule, ...] = (
    Rule(
        re.compile(r"\brm\b[^\n;|&]*\s-(?:[^\s]*r[^\s]*|[^\s]*R[^\s]*)", re.IGNORECASE),
        "Blocked recursive delete (rm -r / rm -rf).",
    ),
    Rule(
        re.compile(r"\bfind\b[^\n;|&]*\s-delete\b", re.IGNORECASE),
        "Blocked bulk delete via find -delete.",
    ),
    Rule(
        re.compile(r"\bgit\b[^\n;|&]*\breset\b[^\n;|&]*--hard\b", re.IGNORECASE),
        "Blocked git reset --hard (discards local changes).",
    ),
    Rule(
        re.compile(r"\bgit\b[^\n;|&]*\bclean\b[^\n;|&]*\s-[^\s]*f[^\s]*", re.IGNORECASE),
        "Blocked git clean -f (deletes untracked files).",
    ),
    Rule(
        re.compile(r"\bgit\b[^\n;|&]*\bcheckout\b[^\n;|&]*\s--\s+", re.IGNORECASE),
        "Blocked git checkout -- <path> (discards file changes).",
    ),
    Rule(
        re.compile(r"\bgit\b[^\n;|&]*\bpush\b[^\n;|&]*--force(?:-with-lease)?\b", re.IGNORECASE),
        "Blocked git push --force/--force-with-lease (history rewrite risk).",
    ),
    Rule(
        re.compile(r"\bmkfs(?:\.[a-z0-9_+-]+)?\b", re.IGNORECASE),
        "Blocked mkfs (high-risk disk destructive operation).",
    ),
    Rule(
        re.compile(r"\bdd\b[^\n;|&]*\bof=/dev/", re.IGNORECASE),
        "Blocked dd writing to /dev/* (high-risk disk destructive operation).",
    ),
)


def load_payload() -> dict:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"Hook JSON parse error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        sys.exit(0)
    return data


def main() -> None:
    payload = load_payload()
    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)

    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command.strip():
        sys.exit(0)

    violations = [rule.message for rule in RULES if rule.pattern.search(command)]
    if not violations:
        sys.exit(0)

    print("Blocked potentially destructive command:", file=sys.stderr)
    for msg in dict.fromkeys(violations):
        print(f"- {msg}", file=sys.stderr)
    print("If required, run it manually in terminal after human confirmation.", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
