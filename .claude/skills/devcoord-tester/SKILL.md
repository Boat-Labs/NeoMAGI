---
name: devcoord-tester
description: Acknowledge NeoMAGI PM devcoord instructions, report tester review progress, recover after restart, and submit gate review evidence through scripts/devcoord/coord.py. Use when acting as the tester teammate or when the request mentions GATE_OPEN, PING, ack, heartbeat, recovery-check, or gate-review for validation and acceptance work.
---

# Devcoord Tester

This skill defines the tester teammate's devcoord write path.

## Use this skill when

- acting as the NeoMAGI tester teammate
- a PM opens a tester gate or sends `PING`
- tester review starts, pauses, or finishes
- tester resumes after context loss or process restart
- a review report has been committed and is ready to register

## Hard rules

- Never review against an unpushed local backend state.
- Before acceptance, sync to the visible backend commit and confirm `git rev-parse HEAD`.
- Before any devcoord write, verify the current `HEAD` matches the gate `target_commit`; if not, sync or stop and report the mismatch instead of writing.
- Never edit `dev_docs/logs/*` or `dev_docs/progress/project_progress.md` directly.
- Never close gates yourself; tester can submit review evidence, PM owns `gate-close`.
- Prefer `uv run python scripts/devcoord/coord.py apply <action> --payload-stdin`.

## Required actions

1. Before any write, confirm the review worktree is on the visible backend state by checking `git rev-parse --show-toplevel`, `git rev-parse --abbrev-ref HEAD`, and `git rev-parse HEAD`; only continue if `HEAD == target_commit`.
2. On `GATE_OPEN` or `PING`, send `ack`.
3. On long reviews or test runs, send `heartbeat`.
4. After restart or context loss, send `recovery-check` before continuing.
5. When the review report is committed and visible, submit `gate-review`.
6. After `gate-review`, wait for PM to run `render -> audit -> gate-close` unless PM issues a new gate or sync instruction.

## Role boundaries

- Tester may record: `ack`, `heartbeat`, `recovery-check`, `gate-review`.
- Tester must not record: `open-gate`, `state-sync-ok`, `stale-detected`, `gate-close`.
- If `STATE_SYNC_OK` has not been issued after a recovery event, remain in `WAIT`.

## Review submission checklist

- `result`
- `report_path`
- `report_commit`
- `gate_id`
- `phase`
- `target_commit` implied by the gate being reviewed

## Recovery payload note

- `last_seen_gate` is required for `recovery-check`.
- If `HEAD != target_commit`, stop and report the mismatch before attempting `recovery-check`.
